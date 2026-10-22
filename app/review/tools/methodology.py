"""Does the workbook's own methodology tab still state the approved basis?

The methodology tab is where a preparer writes down the rules the entry was built under.
When those quietly change, every other check still passes — the arithmetic is consistent
with the new basis — and nobody decided anything. This compares what the tab states against
what the business approved, so drift surfaces as an exception rather than as a new normal.

Two comparisons, from two sources. The period stamp is read from the cells by code. The
basis stated in prose is read by AI-M, and this tool only ever *diffs* that reading against
what the business approved — nothing computes from it, so a misreading costs a reviewer a
minute and can never move a number. Where no reading is available, the prose half reports as
unread rather than as agreement.
"""

import re
from decimal import Decimal
from typing import Any, Final

from review.models.evidence import Figure
from review.models.sheet_map import SheetRole
from review.models.workbook_data import Sheet
from review.tools.base import BaseTool, ToolContext

_PERIOD = re.compile(r"\bFY\d{2}P\d{2}\b", re.IGNORECASE)
"""The period stamp these entries use, e.g. FY26P07."""

_SCAN_COLUMNS: Final = 40
_SCAN_ROWS: Final = 200
"""The methodology tab is a page of prose and a small table, never a data sheet."""


class MethodologyDriftTool(BaseTool):
    """Compare the methodology tab's stated period against the entry it accompanies.

    A methodology stamped for one period attached to an entry posted in another is the
    cheapest symptom of a workbook rolled forward without being re-based — and it is the one
    error class the arithmetic cannot catch, because every figure agrees with every other.
    """

    name = "methodology_drift"
    signature = {  # noqa: RUF012
        "table": {"type": "string", "description": "The posted entry to compare against"},
    }

    def _basis_drift(self, ctx: ToolContext) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        """Differences between the basis the tab states and the basis that was approved.

        Returns the differences and the parameters nobody could read. An unread parameter is
        never counted as agreeing: silence is not a match, and treating it as one would hide
        exactly the drift this tool exists to find.
        """
        approved, stated = ctx.methodology, ctx.methodology_read
        if approved is None:
            return [], ("the approved methodology",)
        if stated is None:
            return [], ("the methodology stated on the tab",)

        drift: list[dict[str, Any]] = []
        unread = list(stated.unread)

        if stated.salary_per_day_denominator is None:
            unread.append("salary per day denominator")
        elif not _same(stated.salary_per_day_denominator, approved.salary_per_day_denominator):
            drift.append(
                {
                    "issue": "salary per day denominator",
                    "stated": stated.salary_per_day_denominator,
                    "expected": approved.salary_per_day_denominator,
                }
            )

        if stated.balance_floored_at_zero is None:
            unread.append("whether a negative balance is floored at zero")
        elif stated.balance_floored_at_zero != approved.balance_floored_at_zero:
            drift.append(
                {
                    "issue": "negative balances floored at zero",
                    "stated": str(stated.balance_floored_at_zero),
                    "expected": str(approved.balance_floored_at_zero),
                }
            )

        if approved.eligible_leave_types and stated.eligible_leave_types:
            drift.extend(
                _leave_type_drift(approved.eligible_leave_types, stated.eligible_leave_types)
            )
        elif approved.eligible_leave_types:
            unread.append("eligible leave types")

        return drift, tuple(dict.fromkeys(unread))

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "je_posting", **_: Any
    ) -> list[Figure]:
        args = {"table": table}
        drift, unread = self._basis_drift(ctx)
        sheet = _methodology_sheet(ctx)
        if sheet is None:
            return [
                self.unable(
                    check_id,
                    "the workbook carries no methodology tab, so there is no stated basis "
                    "to compare against",
                    args,
                )
            ]

        stated = _periods_on(sheet)
        posting = ctx.optional_table(table)
        posted = (
            {
                posting.text(row, "fiscal_period")
                for row in posting.rows
                if posting.has("fiscal_period") and posting.text(row, "fiscal_period")
            }
            if posting is not None
            else set()
        )

        if not stated:
            return [
                self.figure(
                    check_id,
                    ok=False if drift else None,
                    summary=(
                        f"The methodology tab states no period stamp, so it cannot be "
                        f"matched to the entry"
                        f"{f' (posted in period {", ".join(sorted(posted))})' if posted else ''}"
                        f"{_basis_line(drift, unread)}"
                    ),
                    exception_count=len(drift),
                    samples=drift,
                    args=args,
                    error=None if drift else "no_period_on_methodology",
                )
            ]

        # The tab names a period as FY26P07; the entry carries the period alone, as 07.
        periods_on_entry = {value.lstrip("0") or "0" for value in posted}
        matched = {
            stamp
            for stamp in stated
            if stamp[-2:].lstrip("0") in periods_on_entry or not periods_on_entry
        }
        agrees = bool(matched) and len(stated) == 1

        return [
            self.figure(
                check_id,
                ok=agrees and not drift,
                summary=(
                    f"The methodology tab states {', '.join(sorted(stated))}"
                    + (
                        f" and the entry posts in period {', '.join(sorted(posted))}"
                        if posted
                        else ""
                    )
                    + (
                        "; they agree"
                        if agrees
                        else "; they do not agree, so the stated basis may not be the one "
                        "this entry was built under"
                    )
                    + _basis_line(drift, unread)
                ),
                exception_count=(0 if agrees else max(1, len(stated) - 1)) + len(drift),
                samples=[*drift, *({"stated": stamp} for stamp in sorted(stated))],
                numbers=[Decimal(len(stated))],
                args=args,
            )
        ]


def _methodology_sheet(ctx: ToolContext) -> Sheet | None:
    """The tab the sheet map calls the methodology, found by role rather than by name."""
    for name, role in ctx.sheet_map.roles.items():
        if role is SheetRole.METHODOLOGY:
            return ctx.data.sheet(name)
    return None


def _periods_on(sheet: Sheet) -> set[str]:
    """Period stamps written anywhere on the tab, upper-cased so FY26p07 is one value."""
    found: set[str] = set()
    for (row, col), cell in sheet.cells.items():
        if row > _SCAN_ROWS or col > _SCAN_COLUMNS:
            continue
        if isinstance(cell.value, str):
            found.update(match.upper() for match in _PERIOD.findall(cell.value))
    return found


def _leave_type_drift(approved: tuple[str, ...], stated: tuple[str, ...]) -> list[dict[str, Any]]:
    """Leave types on the tab that nobody approved, and approved ones the tab omits."""
    expected = ", ".join(sorted(approved))
    extra = {_norm(item) for item in stated} - {_norm(item) for item in approved}
    missing = {_norm(item) for item in approved} - {_norm(item) for item in stated}
    return [
        {"issue": f"leave types {label}", "stated": ", ".join(sorted(values)), "expected": expected}
        for label, values in (("not approved", extra), ("approved but not stated", missing))
        if values
    ]


def _basis_line(drift: list[dict[str, Any]], unread: tuple[str, ...]) -> str:
    """One clause on the stated basis, appended to whatever the period comparison said."""
    if drift:
        differences = "; ".join(
            f"{item['issue']} is stated as {item['stated']}, approved is {item['expected']}"
            for item in drift
        )
        return f". The stated basis differs from the approved one: {differences}"
    if unread:
        return f". Not read from the tab, so not confirmed: {', '.join(unread)}"
    return ". The stated basis matches the approved one"


def _norm(value: str) -> str:
    return value.strip().casefold()


def _same(left: str, right: str) -> bool:
    return _norm(left) == _norm(right)


__all__: list[str] = ["MethodologyDriftTool"]
