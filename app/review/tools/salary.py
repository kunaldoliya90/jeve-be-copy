"""The salary leg: whether the daily rate is usable where it is being used.

The valuation identity - remaining days times the daily rate against the stated accrual - is
declared by the business now and re-performed by ``tools/reperform.py``. What is left here is
the question a formula cannot answer: whether the rate itself can be relied on at all.
"""

from decimal import Decimal
from typing import Any

from review.models.evidence import Figure, RowCoverage
from review.tools.base import BaseTool, ToolContext, worst_first
from review.tools.reperformance import data_rows


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
