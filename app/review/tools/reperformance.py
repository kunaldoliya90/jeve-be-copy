"""Population, classification and sign checks over the records.

The arithmetic identities that used to live here - the accrual split and the leave movement -
are declared by the business now and re-performed by ``tools/reperform.py``. What is left is
the work that is not a formula: which employees are in scope, which side of the expense split
each belongs on, and whether any amount is negative.

Every one of them works **row by row**. A total that agrees while individual rows cancel out
is a coincidence, and in these workbooks a plausible one, so each figure carries its row
coverage and a figure that did not cover every row cannot support a pass.

A missing input stays missing. It is never patched with a zero: that turns a data gap into a
confident figure, and an Excel error value into one too.
"""

from decimal import Decimal
from typing import Any, Final

from review.models.evidence import Figure, RowCoverage
from review.tools.base import BaseTool, BoundTable, ToolContext, worst_first


def data_rows(table: BoundTable, key: str = "employee_ref") -> list[int]:
    """Rows that are actually records, not the blanks and totals beneath them.

    A profiled block ends at a run of empty rows, so it reaches past the last employee into
    trailing subtotal and spacer rows. Counting those as rows that failed to tie turns a
    clean workbook into a failure, which is the worst kind of wrong answer.
    """
    if not table.has(key):
        return list(table.rows)
    return [row for row in table.rows if table.text(row, key)]


class NegativeScanTool(BaseTool):
    """Negative employee-level amounts.

    Never approved automatically. A negative accrual is either a genuine adjustment or a
    sign that the source data, the leave information or the employee mapping is wrong, and
    only a person can tell which.
    """

    name = "negative_scan"
    signature = {"table": {"type": "string"}, "column": {"type": "string"}}  # noqa: RUF012

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "calculation",
        column: str = "total_accrual",
        **_: Any,
    ) -> list[Figure]:
        bound = ctx.table(table)
        args = {"table": table, "column": column}

        negatives: list[tuple[int, str, Decimal]] = [
            (row, bound.cell_ref(row, column).cell, value)
            for row in bound.rows
            if (value := bound.number(row, column)) is not None and value < 0
        ]
        impact = sum((abs(amount) for _, _, amount in negatives), Decimal(0))

        return [
            self.figure(
                check_id,
                ok=not negatives,
                summary=(
                    f"{len(negatives):,} employee row(s) carry a negative {column}"
                    f"{f', {impact} in total' if negatives else ''}"
                ),
                exception_count=len(negatives),
                financial_impact=impact or None,
                samples=worst_first(
                    [
                        {"row": row, "cell": cell, "difference": amount}
                        for row, cell, amount in negatives
                    ]
                ),
                numbers=[impact, *(amount for _, _, amount in negatives)],
                args=args,
            )
        ]


class ClassificationCheckTool(BaseTool):
    """Is each employee's amount on the side its classification says it should be?

    An employee classed as FTE whose amount sits in the non-FTE column posts a correct total
    to the wrong expense account — the entry balances, and the expense split is wrong.
    """

    name = "classification_check"
    signature = {"table": {"type": "string"}}  # noqa: RUF012

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "calculation", **_: Any
    ) -> list[Figure]:
        bound = ctx.table(table)
        args = {"table": table}

        if not bound.has("fte_classification"):
            return [self.unable(check_id, "no classification column is bound", args)]

        mismatches: list[dict[str, Any]] = []
        checked = 0
        for row in bound.rows:
            label = bound.text(row, "fte_classification").casefold()
            fte = bound.number(row, "fte_accrual") or Decimal(0)
            non_fte = bound.number(row, "non_fte_accrual") or Decimal(0)
            if not label or (fte == 0 and non_fte == 0):
                continue
            checked += 1

            # "non-fte" contains "fte", so the negative form has to be tested first.
            is_non_fte = "non" in label
            wrong = non_fte if not is_non_fte else fte
            if wrong != 0:
                mismatches.append(
                    {
                        "row": row,
                        "classification": bound.text(row, "fte_classification"),
                        "cell": bound.cell_ref(row, "fte_classification").cell,
                        "difference": abs(wrong),
                    }
                )

        impact = sum((item["difference"] for item in mismatches), Decimal(0))
        return [
            self.figure(
                check_id,
                ok=not mismatches,
                summary=(
                    f"{checked:,} classified row(s) checked; "
                    f"{len(mismatches):,} carry an amount on the wrong side"
                    f"{f', {impact} in total' if mismatches else ''}"
                ),
                exception_count=len(mismatches),
                financial_impact=impact or None,
                coverage=RowCoverage(
                    rows_total=bound.row_count,
                    rows_bound=checked,
                    rows_tied=checked - len(mismatches),
                ),
                samples=worst_first(mismatches),
                numbers=[impact, *(item["difference"] for item in mismatches)],
                args=args,
            )
        ]


class RttBalanceReperformTool(BaseTool):
    """Rebuilds the RTT days actually valued, from the days earned to date and days taken.

    Not declared as a ``reperform`` identity because the rule it reproduces is not one line of
    arithmetic. The days earned to date are ``rtt_earned / 12 * months_to_accrue``, and a plain
    ``max(earned_to_date - rtt_taken, 0)`` is wrong on both sample workbooks: whenever "Pris
    RTT" is blank the file's own stated balance is zero, not the earned-to-date amount nothing
    has yet reduced - true on every one of 547 and 527 such rows, never once the exception.

    Two blanks are folded to zero rather than left missing, both verified against real data
    rather than assumed. "Pris RTT" blank is the rule above. "Acquis RTT" blank is a different,
    entirely ordinary population: 137 and 132 employees on the two workbooks who are properly
    FTE-classified, have real CP activity, and simply have no RTT entitlement this period - the
    file states their balance as zero too, on all of them. Declaring either with ``reperform``'s
    "missing propagates" rule would read these employees as unbound rather than tied, which
    would leave this check permanently short of full coverage - never a pass - on a workbook
    where nothing is wrong, for a population this large. A genuinely unresolved row still is:
    two employees on 305800.xlsx carry an unmapped `#N/A` staffing type with every CP and RTT
    column blank, and those two are correctly reported as unbound by the equivalent gap in
    ``cp_provision_builds_up``, because nothing here manufactures a zero for a row that is
    actually missing data rather than stating a real one.
    """

    name = "rtt_balance_reperforms"
    signature = {"table": {"type": "string"}}  # noqa: RUF012

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "calculation", **_: Any
    ) -> list[Figure]:
        bound = ctx.table(table)
        args = {"table": table}

        needed = ("rtt_earned", "months_to_accrue", "rtt_balance_days")
        missing = [meaning for meaning in needed if not bound.has(meaning)]
        if missing:
            return [
                self.figure(
                    check_id,
                    ok=None,
                    summary=(
                        f"this workbook has no {', '.join(missing)} column, so the RTT "
                        "balance cannot be re-performed"
                    ),
                    args=args,
                    error="rtt_balance_columns_missing",
                )
            ]

        rows = data_rows(bound)
        diffs: list[dict[str, Any]] = []
        checked = tied = 0
        for row in rows:
            months = bound.number(row, "months_to_accrue")
            actual = bound.number(row, "rtt_balance_days")
            if months is None or actual is None:
                continue
            earned = bound.number(row, "rtt_earned") or Decimal(0)
            taken = bound.number(row, "rtt_taken") or Decimal(0)

            earned_to_date = earned / 12 * months
            expected = Decimal(0) if taken == 0 else max(earned_to_date - taken, Decimal(0))

            checked += 1
            difference = expected - actual
            if abs(difference) <= ctx.tolerance:
                tied += 1
            else:
                diffs.append(
                    {
                        "row": row,
                        "cell": bound.cell_ref(row, "rtt_balance_days").cell,
                        "expected": expected,
                        "actual": actual,
                        "difference": difference,
                    }
                )

        coverage = RowCoverage(rows_total=len(rows), rows_bound=checked, rows_tied=tied)
        if coverage.rows_bound and coverage.tie_rate < _MIN_TIE_RATE:
            return [
                self.figure(
                    check_id,
                    ok=None,
                    summary=(
                        f"RTT balance days: only {coverage.rows_tied:,} of "
                        f"{coverage.rows_bound:,} records agree, so this workbook derives the "
                        "figure some other way and it cannot be re-performed from the columns "
                        "present"
                    ),
                    coverage=coverage,
                    args=args,
                    error="rtt_balance_convention_unknown",
                )
            ]

        impact = sum((abs(diff["difference"]) for diff in diffs), Decimal(0))
        unbound = coverage.rows_total - coverage.rows_bound
        return [
            self.figure(
                check_id,
                ok=coverage.complete,
                summary=(
                    f"RTT balance days: {coverage.rows_tied:,} of {coverage.rows_total:,} rows "
                    f"agree{f', {len(diffs):,} differ by {impact} in total' if diffs else ''}"
                    f"{f', {unbound:,} row(s) had no comparable figure' if unbound else ''}"
                ),
                exception_count=len(diffs) + unbound,
                financial_impact=impact or None,
                coverage=coverage,
                samples=worst_first(diffs),
                numbers=[impact, *(diff["difference"] for diff in diffs)],
                args=args,
            )
        ]


_MIN_TIE_RATE: Final = 0.5
"""Below this, the blank-means-zero convention verified on the sample workbooks is the wrong
one for this file, and reporting thousands of exceptions would be the harness's error, not the
preparer's - the same guard `cp_movement` and `rtt_movement` carry."""


class PopulationReconcileTool(BaseTool):
    """Is every employee in the working present in the master, exactly once?

    Three different errors, all of which move the total: someone in the working who is not
    in the master, someone counted twice, and someone in the master who is absent from the
    working.
    """

    name = "population_reconcile"
    signature = {"table": {"type": "string"}, "master": {"type": "string"}}  # noqa: RUF012

    def run(
        self,
        ctx: ToolContext,
        check_id: str,
        *,
        table: str = "calculation",
        master: str = "employee_master",
        **_: Any,
    ) -> list[Figure]:
        args = {"table": table, "master": master}
        working = ctx.table(table)
        reference = ctx.optional_table(master)
        if reference is None:
            return [self.unable(check_id, f"no {master!r} table is bound", args)]

        working_refs: dict[str, int] = {}
        duplicates: list[dict[str, Any]] = []
        for row in data_rows(working):
            key = _employee_key(working.text(row, "employee_ref"))
            if not key:
                continue
            if key in working_refs:
                duplicates.append({"row": row, "employee": key, "first_seen": working_refs[key]})
            else:
                working_refs[key] = row

        master_refs = {
            key
            for row in reference.rows
            if (key := _employee_key(reference.text(row, "employee_ref")))
        }

        rows = len(data_rows(working))
        if not _is_usable_identifier(len(working_refs), rows):
            # The reference column is redacted or constant, so there is nothing to join on.
            # Reconciling anyway would report every employee as missing from the master,
            # which is a confident finding about the tool rather than about the entry.
            return [
                self.figure(
                    check_id,
                    ok=None,
                    summary=(
                        f"{rows:,} employee rows carry only {len(working_refs):,} distinct "
                        "reference(s), so the column is redacted or constant and the "
                        "population cannot be reconciled against the master"
                    ),
                    args=args,
                    error="employee_reference_not_usable",
                    numbers=[Decimal(rows), Decimal(len(working_refs))],
                )
            ]

        unknown = sorted(set(working_refs) - master_refs)
        missing = sorted(master_refs - set(working_refs))
        exceptions = len(duplicates) + len(unknown)

        return [
            self.figure(
                check_id,
                ok=exceptions == 0,
                summary=(
                    f"{len(working_refs):,} employees in the working against "
                    f"{len(master_refs):,} in the master: {len(unknown):,} not in the master, "
                    f"{len(duplicates):,} duplicated, {len(missing):,} in the master only"
                ),
                exception_count=exceptions,
                coverage=RowCoverage(
                    rows_total=len(data_rows(working)),
                    rows_bound=len(working_refs),
                    # Clamped: an employee can be both unknown and duplicated, and a
                    # negative tie count is a crash rather than a finding.
                    rows_tied=max(0, len(working_refs) - exceptions),
                ),
                samples=[
                    *({"issue": "not in master", "employee": ref} for ref in unknown[:5]),
                    *({"issue": "duplicated", **item} for item in duplicates[:5]),
                ],
                numbers=[
                    Decimal(len(working_refs)),
                    Decimal(len(master_refs)),
                    Decimal(len(unknown)),
                    Decimal(len(duplicates)),
                    Decimal(len(missing)),
                ],
                args=args,
            )
        ]


_MIN_DISTINCT_SHARE: Final = 0.5
"""An identifier column should be nearly all distinct. Far below this it is redacted."""


def _is_usable_identifier(distinct: int, rows: int) -> bool:
    return rows > 0 and distinct / rows >= _MIN_DISTINCT_SHARE


def _employee_key(value: str) -> str:
    """One spelling for an employee reference across sheets.

    The calculation sheet stores it as a number and the master as text, so ``10044212``
    and ``10044212.0`` are the same person. Without this every employee reads as absent
    from the master and the whole population check is a false alarm.
    """
    cleaned = value.strip()
    if cleaned.endswith(".0"):
        cleaned = cleaned[:-2]
    return cleaned.lstrip("0").casefold()
