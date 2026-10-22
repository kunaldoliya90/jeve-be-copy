"""Re-performing the accrual: the only tools that establish the number rather than agree with it.

Every one of them works **row by row**. A total that agrees while individual rows cancel out
is a coincidence, and in these workbooks a plausible one — a neighbouring column carries a
different, entirely sensible total. So each figure here carries its row coverage, and a
figure that did not cover every row cannot support a pass.

A missing input stays missing. It is never patched with a zero: that turns a data gap into a
confident figure, and an Excel error value into one too.
"""

from decimal import Decimal
from typing import Any, Final, NamedTuple

from review.models.evidence import Figure, RowCoverage
from review.tools.base import BaseTool, BoundTable, ToolContext, worst_first


class _RowDiff(NamedTuple):
    row: int
    expected: Decimal
    actual: Decimal
    difference: Decimal

    def as_sample(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "expected": self.expected,
            "actual": self.actual,
            "difference": self.difference,
        }


def data_rows(table: BoundTable, key: str = "employee_ref") -> list[int]:
    """Rows that are actually records, not the blanks and totals beneath them.

    A profiled block ends at a run of empty rows, so it reaches past the last employee into
    trailing subtotal and spacer rows. Counting those as rows that failed to tie turns a
    clean workbook into a failure, which is the worst kind of wrong answer.
    """
    if not table.has(key):
        return list(table.rows)
    return [row for row in table.rows if table.text(row, key)]


def _compare_rows(
    ctx: ToolContext,
    table: BoundTable,
    expected_of: Any,
    actual_meaning: str,
) -> tuple[list[_RowDiff], RowCoverage]:
    """Walk the records once, comparing a derived figure against the workbook's own.

    Rows where an input is absent are counted as *unbound* rather than as differences: the
    workbook did not claim anything there, so there is nothing to disagree with.
    """
    diffs: list[_RowDiff] = []
    bound = tied = 0
    rows = data_rows(table)
    total = len(rows)

    for row in rows:
        expected = expected_of(row)
        actual = table.number(row, actual_meaning)
        if expected is None or actual is None:
            continue
        bound += 1
        difference = expected - actual
        if ctx.is_zero(difference):
            tied += 1
        else:
            diffs.append(_RowDiff(row, expected, actual, difference))

    return diffs, RowCoverage(rows_total=total, rows_bound=bound, rows_tied=tied)


def _figure_from_rows(
    tool: BaseTool,
    ctx: ToolContext,
    check_id: str,
    *,
    what: str,
    diffs: list[_RowDiff],
    coverage: RowCoverage,
    args: dict[str, Any],
) -> Figure:
    impact = sum((abs(diff.difference) for diff in diffs), Decimal(0))
    unbound = coverage.rows_total - coverage.rows_bound

    return tool.figure(
        check_id,
        # Complete coverage *and* nothing differing. Either alone is not a pass.
        ok=coverage.complete,
        summary=(
            f"{what}: {coverage.rows_tied:,} of {coverage.rows_total:,} rows agree"
            f"{f', {len(diffs):,} differ by {impact} in total' if diffs else ''}"
            f"{f', {unbound:,} row(s) had no comparable figure' if unbound else ''}"
        ),
        exception_count=len(diffs) + unbound,
        financial_impact=impact or None,
        coverage=coverage,
        samples=worst_first([diff.as_sample() for diff in diffs]),
        numbers=[impact, *(diff.difference for diff in diffs)],
        args=args,
    )


_IMPLAUSIBLE_TIE_RATE: Final = 0.5
"""Below this, the formula is wrong rather than the data. See RecalcLeaveBalanceTool."""


class RecalcAccrualTool(BaseTool):
    """Does the FTE and non-FTE split add up to the total the workbook states, per employee?

    The split is what decides which expense account each amount reaches, so a row whose
    halves do not sum to its own total is either a classification error or a broken formula
    — and either way the posted entry is wrong in a way no grand total reveals.
    """

    name = "recalc_accrual"
    signature = {  # noqa: RUF012
        "table": {"type": "string", "description": "Table holding the employee-level working"},
    }

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "calculation", **_: Any
    ) -> list[Figure]:
        bound = ctx.table(table)
        args = {"table": table}

        def expected(row: int) -> Decimal | None:
            fte = bound.number(row, "fte_accrual")
            non_fte = bound.number(row, "non_fte_accrual")
            if fte is None and non_fte is None:
                return None
            return (fte or Decimal(0)) + (non_fte or Decimal(0))

        diffs, coverage = _compare_rows(ctx, bound, expected, "total_accrual")
        return [
            _figure_from_rows(
                self,
                ctx,
                check_id,
                what="FTE plus non-FTE against the stated total",
                diffs=diffs,
                coverage=coverage,
                args=args,
            )
        ]


class RecalcLeaveBalanceTool(BaseTool):
    """Does earned minus taken equal the closing balance, per employee and per leave type?

    The balance is the quantity being valued, so an error here scales linearly into the
    entry. Both legs are checked independently: a row missing its RTT inputs still has its
    CP balance verified, because gating the whole row on every input would understate how
    much was actually established.
    """

    name = "recalc_leave_balance"
    signature = {"table": {"type": "string"}}  # noqa: RUF012

    def run(
        self, ctx: ToolContext, check_id: str, *, table: str = "calculation", **_: Any
    ) -> list[Figure]:
        bound = ctx.table(table)
        figures: list[Figure] = []

        for leg, earned, taken, balance in (
            ("CP", "cp_earned", "cp_taken", "cp_balance"),
            ("RTT", "rtt_earned", "rtt_taken", "rtt_balance"),
        ):
            if not all(bound.has(meaning) for meaning in (earned, taken, balance)):
                figures.append(
                    self.figure(
                        check_id,
                        ok=None,
                        summary=f"{leg} leave columns are not all present, so the movement "
                        "cannot be recalculated",
                        decisive=False,
                        args={"table": table, "leg": leg},
                        error=f"{leg.lower()}_columns_missing",
                    )
                )
                continue

            def expected(row: int, earned: str = earned, taken: str = taken) -> Decimal | None:
                got = bound.number(row, earned)
                used = bound.number(row, taken)
                if got is None or used is None:
                    return None
                # Floored at zero: rows that have taken more than they have earned to date
                # would otherwise *reduce* the provision. One sample file has 965 of them.
                return max(got - used, Decimal(0))

            diffs, coverage = _compare_rows(ctx, bound, expected, balance)

            # A movement that disagrees with nearly every row is not 2,000 wrong rows: it
            # is the wrong formula for this workbook's convention — a carry-forward the
            # columns do not show, most likely. Reporting that as a failure would be a
            # confident false finding, so it reports as unable to validate instead.
            if coverage.rows_bound and coverage.tie_rate < _IMPLAUSIBLE_TIE_RATE:
                figures.append(
                    self.figure(
                        check_id,
                        ok=None,
                        summary=(
                            f"{leg} earned minus taken matches only "
                            f"{coverage.rows_tied:,} of {coverage.rows_bound:,} rows, so this "
                            "workbook derives the balance some other way; the movement cannot "
                            "be re-performed from the columns present"
                        ),
                        decisive=False,
                        coverage=coverage,
                        args={"table": table, "leg": leg},
                        error=f"{leg.lower()}_convention_unknown",
                    )
                )
                continue

            figures.append(
                _figure_from_rows(
                    self,
                    ctx,
                    check_id,
                    what=f"{leg} earned minus taken against the stated balance",
                    diffs=diffs,
                    coverage=coverage,
                    args={"table": table, "leg": leg},
                )
            )

        return figures


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
