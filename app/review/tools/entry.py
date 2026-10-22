"""Tools about the posted entry itself: accounts, period, reversal, evidence and PII."""

from collections import Counter
from decimal import Decimal
from typing import Any

from review.models.evidence import Figure, PictureReading
from review.tools.base import BaseTool, ToolContext, worst_first


class AccountMappingTool(BaseTool):
    """Are the accounts on the entry the ones the master says to use?

    The right total in the wrong account still balances. It misstates the expense split, and
    nothing else in the review would notice.
    """

    name = "account_mapping"
    signature = {"table": {"type": "string"}, "master": {"type": "string"}}  # noqa: RUF012

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "je_posting",
        master: str = "entity_master",
        **_: Any,
    ) -> list[Figure]:
        args = {"table": table, "master": master}
        posting = ctx.table(table)
        reference = ctx.optional_table(master)

        expected: set[str] = set()
        if reference is not None:
            for row in reference.rows:
                for meaning in (
                    "liability_account",
                    "fte_expense_account",
                    "non_fte_expense_account",
                ):
                    if reference.has(meaning):
                        value = reference.text(row, meaning)
                        if value:
                            expected.add(_account(value))

        used = Counter(
            _account(posting.text(row, "account"))
            for row in posting.rows
            if posting.text(row, "account")
        )
        if not expected:
            return [
                self.figure(
                    check_id,
                    ok=None,
                    summary=(
                        f"{len(used)} distinct account(s) used: {', '.join(sorted(used))}. "
                        "The entity master states none, so they cannot be checked against it."
                    ),
                    args=args,
                    error="no_expected_accounts",
                    numbers=[Decimal(len(used))],
                )
            ]

        unexpected = sorted(set(used) - expected)
        return [
            self.figure(
                check_id,
                ok=not unexpected,
                summary=(
                    f"{len(used)} account(s) used against {len(expected)} the master allows"
                    f"{f'; not in the master: {", ".join(unexpected)}' if unexpected else ''}"
                ),
                exception_count=sum(used[account] for account in unexpected),
                samples=[{"account": account, "lines": used[account]} for account in unexpected],
                numbers=[Decimal(len(used)), Decimal(len(expected))],
                args=args,
            )
        ]


class ReversalCheckTool(BaseTool):
    """Is the reversal configured so the accrual actually unwinds?

    An accrual that never reverses double-counts the liability in the following period, and
    a reversal dated on or before its posting is not a reversal at all.
    """

    name = "reversal_check"
    signature = {"table": {"type": "string"}}  # noqa: RUF012

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "je_posting", **_: Any
    ) -> list[Figure]:
        args = {"table": table}
        posting = ctx.table(table)
        if not (posting.has("posting_date") and posting.has("reversal_date")):
            return [self.unable(check_id, "posting or reversal date is not bound", args)]

        missing: list[dict[str, Any]] = []
        not_later: list[dict[str, Any]] = []
        checked = 0

        for row in posting.rows:
            posted = posting.text(row, "posting_date")
            reversed_on = posting.text(row, "reversal_date")
            if not posted:
                continue
            checked += 1
            if not reversed_on:
                missing.append({"row": row, "cell": posting.cell_ref(row, "reversal_date").cell})
            elif reversed_on <= posted:
                not_later.append({"row": row, "posting": posted, "reversal": reversed_on})

        exceptions = len(missing) + len(not_later)
        return [
            self.figure(
                check_id,
                ok=exceptions == 0 and checked > 0,
                summary=(
                    f"{checked:,} line(s) carry a posting date; {len(missing):,} have no "
                    f"reversal date and {len(not_later):,} reverse on or before posting"
                ),
                exception_count=exceptions,
                samples=[*missing[:5], *not_later[:5]],
                numbers=[Decimal(checked), Decimal(len(missing)), Decimal(len(not_later))],
                args=args,
            )
        ]


class PeriodAgreementTool(BaseTool):
    """Do the period labels on the entry agree with one another?

    Leave, salary or status data from the wrong period misstates the obligation, and the
    cheapest symptom is two different period labels in one file.
    """

    name = "period_agreement"
    signature = {"table": {"type": "string"}}  # noqa: RUF012

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "je_posting", **_: Any
    ) -> list[Figure]:
        args = {"table": table}
        posting = ctx.table(table)
        if not posting.has("fiscal_period"):
            return [self.unable(check_id, "no fiscal period column is bound", args)]

        periods = Counter(
            posting.text(row, "fiscal_period")
            for row in posting.rows
            if posting.text(row, "fiscal_period")
        )
        years = Counter(
            posting.text(row, "fiscal_year")
            for row in posting.rows
            if posting.has("fiscal_year") and posting.text(row, "fiscal_year")
        )
        distinct = len(periods)

        # The year rides along on each sample so a reader is never shown a bare period
        # number: "08" is not a period anyone can act on, and two entries a year apart
        # carry the same one.
        year = years.most_common(1)[0][0] if len(years) == 1 else ""

        return [
            self.figure(
                check_id,
                ok=distinct == 1 and len(years) <= 1,
                summary=(
                    f"{distinct} distinct fiscal period(s) on the entry"
                    f"{f': {", ".join(sorted(periods))}' if distinct <= 4 else ''}"
                    f"{f'; {len(years)} fiscal year(s)' if years else ''}"
                ),
                exception_count=max(0, distinct - 1),
                samples=[
                    {"period": value, "fiscal_year": year, "lines": count}
                    for value, count in periods.most_common(5)
                ],
                numbers=[Decimal(distinct)],
                args=args,
            )
        ]


class EvidenceRegisterTool(BaseTool):
    """Is each expected evidence layer actually present in the workbook?

    A layer is satisfied by a *sheet*, never by a column found somewhere. Matching fails
    closed on purpose: a layer wrongly reported missing carries a remediation and a reviewer
    who can point at the tab, while one wrongly reported available is evidence nobody checked.
    """

    name = "evidence_register"
    signature = {"items": {"type": "array", "items": {"type": "string"}}}  # noqa: RUF012

    #: Evidence item -> the sheet roles or name fragments that can satisfy it.
    SATISFIED_BY = {  # noqa: RUF012
        "methodology": ("methodology",),
        "accrual_calculation": ("calculation",),
        "employee_master": ("master_data",),
        "approval": ("approval",),
        "vacation_provision_file": ("supporting",),
        "provider_screenshot": ("picture",),
    }

    def run(self, ctx: ToolContext, check_id: str, **_: Any) -> list[Figure]:
        found: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for item, satisfiers in self.SATISFIED_BY.items():
            if "picture" in satisfiers:
                present, note = _picture_evidence(ctx)
                location = _picture_location(ctx)
            else:
                location = _satisfying_sheet(ctx, satisfiers)
                present, note = location is not None, ""
            entry: dict[str, Any] = {"item": item, "present": present}
            if location is not None:
                entry["location"] = location
            if note:
                entry["issue"] = note
            (found if present else missing).append(entry)

        return [
            self.figure(
                check_id,
                ok=not missing,
                summary=_evidence_summary(len(found), len(self.SATISFIED_BY), missing),
                exception_count=len(missing),
                # Every layer, not only the gaps, because the register renders from these
                # and a reviewer needs the satisfied rows to see what was actually relied
                # on. Missing first so the sample cap can never be what hides a gap.
                samples=[*missing, *found],
                numbers=[Decimal(len(found)), Decimal(len(self.SATISFIED_BY))],
                args={},
            )
        ]


class FormulaHealthTool(BaseTool):
    """Numbers typed over a calculated column, and broken references.

    Observations, never failures on their own — but the single most common way a working
    paper stops being a working paper, so they are counted and located.
    """

    name = "formula_health"
    signature = {}  # noqa: RUF012

    def run(self, ctx: ToolContext, check_id: str, **_: Any) -> list[Figure]:
        counts: Counter[str] = Counter()
        samples: list[dict[str, Any]] = []
        for defect in ctx.defects:
            counts[defect.kind.value] += 1
            if len(samples) < 10:
                samples.append(
                    {"kind": defect.kind.value, "cell": f"{defect.cell.sheet}!{defect.cell.cell}"}
                )

        total = sum(counts.values())
        return [
            self.figure(
                check_id,
                ok=total == 0,
                summary=(
                    "No formula defects found"
                    if total == 0
                    else "; ".join(f"{kind}: {count:,}" for kind, count in counts.most_common())
                ),
                exception_count=total,
                samples=samples,
                numbers=[Decimal(total), *(Decimal(count) for count in counts.values())],
                args={},
            )
        ]


class PIIScanTool(BaseTool):
    """Where personal data sits, and whether it is expected on that tab.

    Employee data on the calculation sheet and the employee master is expected for this entry
    type and is not a finding. The same data reaching the SAP-facing posting tab is a leak,
    which is what this looks for.
    """

    name = "pii_scan"
    signature = {  # noqa: RUF012
        "expected_on": {"type": "array", "items": {"type": "string"}},
        "forbidden_on": {"type": "array", "items": {"type": "string"}},
    }

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        expected_on: list[str] | None = None,
        forbidden_on: list[str] | None = None,
        **_: Any,
    ) -> list[Figure]:
        forbidden = list(forbidden_on or ["je_posting"])
        args = {"expected_on": list(expected_on or []), "forbidden_on": forbidden}

        leaks: list[dict[str, Any]] = []
        for table_name in forbidden:
            table = ctx.optional_table(table_name)
            if table is None:
                continue
            for meaning in ("employee_ref", "employee_name"):
                if table.has(meaning):
                    leaks.append({"table": table_name, "column": meaning})

        return [
            self.figure(
                check_id,
                ok=not leaks,
                summary=(
                    "No personal data on the tabs where it does not belong"
                    if not leaks
                    else f"{len(leaks)} personal-data column(s) found on: "
                    + ", ".join(sorted({entry["table"] for entry in leaks}))
                ),
                exception_count=len(leaks),
                samples=leaks,
                numbers=[Decimal(len(leaks))],
                args=args,
            )
        ]


def _satisfying_sheet(ctx: ToolContext, satisfiers: tuple[str, ...]) -> str | None:
    """The tab that satisfies a layer, by its bound role first and its name second.

    The name is a fallback, not a signal of equal standing: a tab called "Approval" that the
    binder gave no role to is still worth pointing a reviewer at, but the role is what the
    workbook was actually read as.
    """
    for sheet, role in ctx.sheet_map.roles.items():
        if role.value in satisfiers:
            return sheet
    return next(
        (
            sheet
            for sheet in ctx.sheet_map.roles
            if any(satisfier in sheet.casefold() for satisfier in satisfiers)
        ),
        None,
    )


def _picture_location(ctx: ToolContext) -> str | None:
    """Where a screenshot sits, named only when one was actually read and legible."""
    legible = [reading for reading in ctx.picture_readings.values() if reading.legible]
    if not legible:
        return None
    return f"{len(legible)} screenshot(s) read"


def _picture_evidence(ctx: ToolContext) -> tuple[bool, str]:
    """Whether a pasted screenshot stands as evidence, and what is wrong with it if not.

    Four outcomes, and the distinctions matter to whoever reads the report: no picture at
    all; a picture nobody read; a picture read and illegible; and a picture whose amounts
    disagree with the entry. Only the last is a reconciliation finding, and only the first
    three are an evidence gap — collapsing them into "missing" would send a reviewer looking
    for a file that is already in the workbook.
    """
    if not ctx.data.pictures:
        return False, "no screenshot is pasted into the workbook"
    if not ctx.picture_readings:
        return False, "a screenshot is present but was not read"

    legible = [reading for reading in ctx.picture_readings.values() if reading.legible]
    if not legible:
        return False, "the screenshots present could not be read"

    mismatched = _screenshot_mismatches(ctx, legible)
    if mismatched:
        return False, "; ".join(mismatched)
    return True, ""


def _screenshot_mismatches(ctx: ToolContext, readings: list[PictureReading]) -> list[str]:
    """Facts a screenshot states that the workbook does not agree with.

    Only the facts code read from the workbook are compared, and only where a screenshot
    actually carries that amount. A screenshot that shows something else entirely is not a
    mismatch; it is a different screenshot.
    """
    problems: list[str] = []
    for name, value in ctx.facts.items():
        if not isinstance(value, Decimal):
            continue
        stated = [
            amount
            for reading in readings
            for label, amount in reading.amounts
            if name.replace("_", " ") in label.casefold()
        ]
        if stated and not any(ctx.is_zero(amount - value) for amount in stated):
            problems.append(
                f"a screenshot states {name.replace('_', ' ')} as "
                f"{', '.join(str(amount) for amount in stated)}, the workbook has {value}"
            )
    return problems


def _evidence_summary(found: int, expected: int, missing: list[dict[str, Any]]) -> str:
    line = f"{found} of {expected} expected evidence layers are present in the workbook"
    if missing:
        line += "; missing: " + ", ".join(str(entry["item"]) for entry in missing)
    return line


def _account(value: str) -> str:
    """Account codes arrive as text on one sheet and as a number on another."""
    cleaned = value.strip()
    return cleaned[:-2] if cleaned.endswith(".0") else cleaned


__all__ = [
    "AccountMappingTool",
    "EvidenceRegisterTool",
    "FormulaHealthTool",
    "PIIScanTool",
    "PeriodAgreementTool",
    "ReversalCheckTool",
    "worst_first",
]
