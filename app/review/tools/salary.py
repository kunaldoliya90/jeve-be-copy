"""The salary leg: the daily rate, and the accrual derived from it.

``recalc_salary_per_day`` is the second re-performance in the harness and the one that
reaches furthest back: it rebuilds the RTT accrual from the leave balance and the daily rate
rather than accepting the amount the workbook states. ``balance x rate`` is the arithmetic
the workbooks themselves perform, read from their own cell formulas.
"""

from decimal import Decimal
from typing import Any, Final

from review.models.evidence import Figure, RowCoverage
from review.tools.base import BaseTool, ToolContext, worst_first
from review.tools.reperformance import data_rows

_ROUNDING_HEADROOM: Final = Decimal("0.01")
"""A per-row product is rounded to the currency's scale before it is stored, so a cent of
difference is the rounding and not an exception. Wider than the entry-level tolerance for
exactly that reason."""


class RecalcSalaryPerDayTool(BaseTool):
    """Does the leave balance times the daily rate give the accrual the workbook states?

    This is the formula the workbooks compute, taken from their own cell formulas: the
    remaining days multiplied by a daily salary rate. Re-performing it is what establishes
    the amount rather than agreeing with it, so the figure carries its row coverage and a
    partial run can never support a pass.
    """

    name = "recalc_salary_per_day"
    signature = {  # noqa: RUF012
        "table": {"type": "string", "description": "Table holding the employee-level working"},
    }

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "calculation", **_: Any
    ) -> list[Figure]:
        bound = ctx.table(table)
        args = {"table": table}

        needed = ("rtt_balance_days", "daily_salary_rate", "rtt_accrual")
        missing = [meaning for meaning in needed if not bound.has(meaning)]
        if missing:
            return [
                self.unable(
                    check_id,
                    "the daily rate, the balance in days or the accrual amount is not bound "
                    f"({', '.join(missing)}), so the amount cannot be rebuilt from its inputs",
                    args,
                )
            ]

        diffs: list[dict[str, Any]] = []
        rows = data_rows(bound)
        checked = tied = 0

        for row in rows:
            days = bound.number(row, "rtt_balance_days")
            rate = bound.number(row, "daily_salary_rate")
            stated = bound.number(row, "rtt_accrual")
            # A missing input stays missing. Reading it as a zero would turn a data gap
            # into a confident re-performance of nothing.
            if days is None or rate is None or stated is None:
                continue
            checked += 1

            expected = days * rate
            difference = expected - stated
            if abs(difference) <= _ROUNDING_HEADROOM:
                tied += 1
            else:
                diffs.append(
                    {
                        "row": row,
                        "cell": bound.cell_ref(row, "rtt_accrual").cell,
                        "days": days,
                        "rate": rate,
                        "expected": expected,
                        "actual": stated,
                        "difference": difference,
                    }
                )

        impact = sum((abs(item["difference"]) for item in diffs), Decimal(0))
        coverage = RowCoverage(rows_total=len(rows), rows_bound=checked, rows_tied=tied)

        return [
            self.figure(
                check_id,
                ok=checked > 0 and not diffs,
                summary=_recalc_summary(tied, checked, len(rows), diffs, impact),
                exception_count=len(diffs),
                financial_impact=impact or None,
                coverage=coverage,
                samples=worst_first(diffs),
                numbers=[impact, *(item["difference"] for item in diffs)],
                args=args,
            )
        ]


def _recalc_summary(
    tied: int, checked: int, rows: int, diffs: list[dict[str, Any]], impact: Decimal
) -> str:
    line = (
        f"Balance in days times the daily rate: {tied:,} of {checked:,} rows "
        "with all three inputs agree"
    )
    if diffs:
        line += f", {len(diffs):,} differ by {impact} in total"
    if checked < rows:
        line += f"; {rows - checked:,} row(s) did not carry all three"
    return line


class SalaryValidateTool(BaseTool):
    """Is the daily rate usable wherever it is being used?

    Without a salary master in the workbook the rate cannot be checked against its source,
    so this checks what the file itself can prove: that no employee carries an accrual
    derived from a rate that is absent, zero or negative. An accrual standing on a rate of
    nothing is not a small error — it is an amount with no basis at all.
    """

    name = "salary_validate"
    signature = {  # noqa: RUF012
        "table": {"type": "string"},
        "master": {"type": "string", "description": "Salary source, where the file carries one"},
    }

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "calculation",
        master: str = "employee_master",
        **_: Any,
    ) -> list[Figure]:
        bound = ctx.table(table)
        args = {"table": table, "master": master}

        if not bound.has("daily_salary_rate"):
            return [self.unable(check_id, "no daily salary rate column is bound", args)]

        rows = data_rows(bound)
        unusable: list[dict[str, Any]] = []
        with_rate = 0

        for row in rows:
            # Only the RTT amount is derived from a daily rate. An employee with CP leave
            # alone has no use for one, and flagging them would report six hundred false
            # exceptions on a file where nothing is wrong.
            derived = bound.number(row, "rtt_accrual")
            if derived is None or derived == 0:
                continue

            rate = bound.number(row, "daily_salary_rate")
            if rate is not None and rate > 0:
                with_rate += 1
                continue
            unusable.append(
                {
                    "row": row,
                    "cell": bound.cell_ref(row, "daily_salary_rate").cell,
                    "rate": rate,
                    "difference": abs(derived),
                }
            )

        impact = sum((item["difference"] for item in unusable), Decimal(0))
        checked = with_rate + len(unusable)

        # A salary source would let the rate be checked against something external. These
        # workbooks carry none, so the figure says what it did establish and what it did not.
        source = ctx.optional_table(master)
        has_source = source is not None and source.has("daily_salary_rate")

        return [
            self.figure(
                check_id,
                ok=checked > 0 and not unusable,
                summary=(
                    f"{with_rate:,} of {checked:,} employees with an RTT amount carry a "
                    f"usable daily rate"
                    + (
                        f"; {len(unusable):,} derive {impact} from a rate that is absent or zero"
                        if unusable
                        else ""
                    )
                    + (
                        ""
                        if has_source
                        else ". The workbook carries no independent salary source, so the "
                        "rate itself is not checked against one"
                    )
                ),
                exception_count=len(unusable),
                financial_impact=impact or None,
                coverage=RowCoverage(rows_total=len(rows), rows_bound=checked, rows_tied=with_rate),
                samples=worst_first(unusable),
                numbers=[impact, *(item["difference"] for item in unusable)],
                args=args,
            )
        ]
