"""Step 13c, second half: the formulas the workbook itself applies, examined.

The rebuild beside this one answers *is the number right*. This answers a question a right
number cannot settle: **is the file arriving at it the way the business says it should?**

A column can be wrong and still tie. `=AF9` in a column the business says is
`fte_accrual + non_fte_accrual` produces the correct total on every record whose non-FTE
share is zero - which, on the sample workbooks, is nearly all of them. Nothing else in the
harness would see that: the figure agrees, the entry reconciles, every check passes, and the
column is still not the approved arithmetic. Closing that gap is what this module is for.

**It reads formulas; it never evaluates or interprets them.** There is no Excel evaluator
here and no model anywhere near it. Every finding is a structural fact about cells:

* the column holds no formula at all, so the figure is stated rather than computed;
* its cells do not share one formula, so part of the column was edited;
* a constant sits where the column otherwise calculates;
* the references resolve to different columns from the ones the approved arithmetic reads.

The last is the gap-closer, and it is deliberately a comparison of **which columns are
read**, not of the expression's shape. `=AF9+AG9` and `=SUM(AF9:AG9)` are the same
arithmetic written two ways and neither is a finding; `=AF9` is a different arithmetic and
is. Judging the shape of an arbitrary formula would mean inventing an opinion about what the
preparer meant, which is the one thing a work paper must not do.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from review.models.context import RecalculationSpec, ReperformanceSpec
from review.models.recalculation import FormulaFault, FormulaFinding
from review.models.workbook import Defect, DefectKind, FormulaShape, TableRef
from review.models.workbook_data import Sheet
from review.recalc.formula import compile_formula, substitute
from review.tools.base import ToolContext
from review.utils.sheet_utils import column_index

logger = logging.getLogger(__name__)

MAX_FINDINGS_PER_COLUMN: Final = 3
"""Deviating runs reported per column. A column somebody edited in forty places is one
finding about that column, not forty - the count carries the scale."""

_LETTERS: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_REFERENCE: Final = re.compile(
    r"""
    (?:'(?P<quoted>[^']+)'|(?P<bare>[A-Za-z_][\w.]*))!   # an explicit sheet, if any
    \$?(?P<other>[A-Z]{1,3})\$?\d{1,7}
  | \$?(?P<column>[A-Z]{1,3})\$?(?P<row>\d{1,7})          # or a reference on this sheet
    """,
    re.VERBOSE,
)
"""Every A1 reference in a formula, with the sheet it names where it names one.

Deliberately not a formula parser. All this has to know is which columns a cell reads, and
a reference is the only part of Excel's syntax that says so.
"""


@dataclass(frozen=True, slots=True)
class AuditedColumn:
    """One column whose formulas are examined, and the arithmetic it is held to."""

    table: str
    ref: TableRef
    meaning: str
    """The column's own meaning - what the figure in it is."""
    expects: frozenset[str]
    """Meanings the approved arithmetic reads. Empty where the business declares none."""
    expression: str = ""
    """That arithmetic as the business wrote it, for the finding to quote."""
    record_key: str = ""
    """The meaning that separates a record from the rows beneath the data, where there is one.

    Load-bearing. A profiled block runs past the last record into the preparer's own totals,
    and on one sample workbook the single formula in the whole column is the ``SUBTOTAL`` on
    that row. Read as the column's arithmetic it reported, confidently, that the column was
    computed from the wrong inputs - a false finding about a workbook that is fine.
    """


def audit_formulas(
    ctx: ToolContext,
    shapes: Sequence[FormulaShape],
    reperformance: ReperformanceSpec | None,
    recalculation: RecalculationSpec | None,
) -> tuple[tuple[FormulaFinding, ...], int]:
    """Examine every column the business declares arithmetic for. Returns findings and count.

    Never raises: a column that cannot be examined produces a finding saying so, because
    "nothing was established" and "nothing is wrong" must never render the same way.
    """
    columns = _columns(ctx, reperformance, recalculation)
    if not columns:
        return (), 0

    if not ctx.data.formulas_available:
        # One finding, not one per column: the fact is about the file, not about the columns.
        first = columns[0]
        return (
            (
                FormulaFinding(
                    fault=FormulaFault.UNREADABLE,
                    sheet=first.ref.sheet,
                    why=(
                        "This workbook format stores values without the formulas behind "
                        "them, so nothing could be established about the arithmetic the "
                        "file applies. The figures were still rebuilt and compared."
                    ),
                ),
            ),
            len(columns),
        )

    findings: list[FormulaFinding] = []
    for column in columns:
        findings.extend(_audit(ctx, shapes, column))
    return tuple(findings), len(columns)


def _columns(
    ctx: ToolContext,
    reperformance: ReperformanceSpec | None,
    recalculation: RecalculationSpec | None,
) -> list[AuditedColumn]:
    """Which columns are examined, and what each is held to.

    Only columns the business has said something about. A column nobody declared arithmetic
    for is examined for consistency alone, and a column nobody mentioned at all is not
    examined: inventing an expectation is how a work paper comes to report a finding about a
    column the business never asked anybody to compute.
    """
    columns: list[AuditedColumn] = []
    seen: set[tuple[str, str]] = set()

    def add(
        table: str,
        meaning: str,
        expects: Iterable[str] = (),
        expression: str = "",
        record_key: str = "",
    ) -> None:
        ref = ctx.sheet_map.tables.get(table)
        if ref is None or meaning not in ref.columns:
            return
        key = (ref.sheet, ref.columns[meaning])
        if key in seen:
            return
        seen.add(key)
        columns.append(
            AuditedColumn(
                table=table,
                ref=ref,
                meaning=meaning,
                expects=frozenset(expects),
                expression=expression,
                record_key=record_key,
            )
        )

    if reperformance is not None:
        for identity in reperformance.identities:
            add(
                reperformance.table,
                identity.against,
                compile_formula(identity.expression).names,
                identity.expression,
                reperformance.record_key,
            )

    if recalculation is not None:
        if recalculation.stated:
            reads = {
                item.meaning
                for item in recalculation.inputs
                if item.meaning and item.id in compile_formula(recalculation.formula).names
            }
            add(
                recalculation.table,
                recalculation.stated,
                reads,
                recalculation.formula,
                recalculation.record_key,
            )
        # The booked side carries no declared expression - it is a posted line, not a
        # derivation - but a link that was edited or typed over is exactly how one line comes
        # to carry another line's amount.
        add(recalculation.booked.table, recalculation.booked.amount)

    return columns


def _audit(
    ctx: ToolContext, shapes: Sequence[FormulaShape], column: AuditedColumn
) -> list[FormulaFinding]:
    """Everything that can be established about one column's formulas."""
    sheet = ctx.data.sheet(column.ref.sheet)
    if sheet is None:  # pragma: no cover - the map proved the sheet exists
        return []

    letter = column.ref.columns[column.meaning]
    header = _header(sheet, column.ref, letter)
    index = column_index(letter)
    labels = _labels(sheet, column.ref)
    rows = _records(sheet, column)
    if not rows:
        return []

    calculated = [row for row in rows if _formula(sheet, row, index) is not None]
    stated = [
        row
        for row in rows
        if _formula(sheet, row, index) is None and not sheet.cell(row, index).is_blank
    ]
    found = _finding_factory(column, letter, header, len(calculated) + len(stated))

    if not calculated:
        return [
            found(
                FormulaFault.NOT_CALCULATED,
                cell=f"{letter}{rows[0]}",
                rows_affected=len(stated),
                why=(
                    f"Every value in this column is typed or pasted: {len(stated):,} of them. "
                    "The figure is stated in this workbook rather than calculated by it, so "
                    "the arithmetic behind it cannot be read here and has to be evidenced "
                    "another way."
                ),
            )
        ]

    findings: list[FormulaFinding] = []
    runs = _runs(shapes, column.ref.sheet, letter, rows[0], rows[-1])
    majority = max(runs, key=lambda run: run.last_row - run.first_row + 1) if runs else None

    if majority is not None and len(runs) > 1:
        deviants = [run for run in runs if run.r1c1 != majority.r1c1]
        for run in deviants[:MAX_FINDINGS_PER_COLUMN]:
            cell = f"{letter}{run.first_row}"
            formula = _formula(sheet, run.first_row, index) or ""
            findings.append(
                found(
                    FormulaFault.INCONSISTENT,
                    cell=cell,
                    formula=formula,
                    in_headers=_in_headers(formula, labels, run.first_row),
                    expected=_in_headers(
                        _formula(sheet, majority.first_row, index) or "",
                        labels,
                        majority.first_row,
                    ),
                    rows_affected=run.last_row - run.first_row + 1,
                    why=(
                        "This cell does not carry the same formula as the rest of its column. "
                        "A column filled down holds one formula; where it does not, part of "
                        "it was edited after the fact and that part proves nothing about the "
                        "rest."
                    ),
                )
            )
        if len(deviants) > MAX_FINDINGS_PER_COLUMN:
            logger.info(
                "column %s!%s has %d deviating formula runs; reporting %d",
                column.ref.sheet,
                letter,
                len(deviants),
                MAX_FINDINGS_PER_COLUMN,
            )

    typed = _typed_over(ctx.defects, column.ref.sheet, letter, set(rows))
    if typed:
        findings.append(
            found(
                FormulaFault.TYPED_OVER,
                cell=typed[0],
                rows_affected=len(typed),
                why=(
                    f"{len(typed):,} cell(s) in this calculated column hold a typed number "
                    "instead of the formula. A number typed over a calculation is the single "
                    "most common way a working paper stops being one, and it survives every "
                    "recalculation of the figures around it."
                ),
            )
        )

    if column.expects and majority is not None:
        reads = _meanings_read(
            _formula(sheet, majority.first_row, index) or "",
            column.ref,
            majority.first_row,
        )
        if reads is not None and column.meaning in reads:
            # A column that reads *itself* is a running total or a carry-forward, not a
            # different way of deriving the figure: a real derivation cannot reference its
            # own column without being circular. ``=D1+B2`` is the common shape - the row
            # above plus this row's movement - and comparing it with the approved arithmetic
            # would report every cumulative column in every workbook as wrongly computed.
            reads = None
        if reads is not None and reads != column.expects:
            formula = _formula(sheet, majority.first_row, index) or ""
            findings.append(
                found(
                    FormulaFault.DIFFERENT_INPUTS,
                    cell=f"{letter}{majority.first_row}",
                    formula=formula,
                    in_headers=_in_headers(formula, labels, majority.first_row),
                    expected=substitute(column.expression, _by_meaning(labels, column.ref)),
                    rows_affected=majority.last_row - majority.first_row + 1,
                    why=_different_inputs_why(reads, column.expects, labels, column.ref),
                )
            )

    return findings


def _finding_factory(
    column: AuditedColumn, letter: str, header: str, rows_checked: int
) -> Callable[..., FormulaFinding]:
    """A builder that carries the column's identity onto every finding about it."""

    def build(fault: FormulaFault, **fields: object) -> FormulaFinding:
        return FormulaFinding(
            fault=fault,
            sheet=column.ref.sheet,
            column=letter,
            header=header,
            rows_checked=rows_checked,
            **fields,
        )

    return build


def _different_inputs_why(
    reads: frozenset[str], expects: frozenset[str], labels: Mapping[str, str], ref: TableRef
) -> str:
    """Which columns the formula reads that the approved arithmetic does not, and vice versa."""
    by_meaning = _by_meaning(labels, ref)
    missing = sorted(by_meaning.get(name, name) for name in expects - reads)
    extra = sorted(by_meaning.get(name, name) for name in reads - expects)
    parts: list[str] = []
    if missing:
        parts.append(f"it does not read {_join(missing)}, which the approved arithmetic does")
    if extra:
        parts.append(f"it reads {_join(extra)}, which the approved arithmetic does not")
    return (
        "This column is not computed from the columns the business says it should be: "
        f"{'; and '.join(parts)}. "
        "A column built from the wrong inputs can still produce a figure that agrees - on "
        "the records where the missing input happens to be zero - so a tie here is not "
        "evidence that the arithmetic is right."
    )


# --------------------------------------------------------------------------- cell reading


def _formula(sheet: Sheet, row: int, column: int) -> str | None:
    return sheet.cell(row, column).formula


def _header(sheet: Sheet, ref: TableRef, letter: str) -> str:
    value = sheet.cell(ref.header_row, column_index(letter)).value
    return "" if value is None else str(value).strip()


def _labels(sheet: Sheet, ref: TableRef) -> dict[str, str]:
    """Column letter -> the header the preparer wrote, for every column of the table."""
    return {
        letter: header for letter in ref.columns.values() if (header := _header(sheet, ref, letter))
    }


def _by_meaning(labels: Mapping[str, str], ref: TableRef) -> dict[str, str]:
    """Meaning -> that column's header, so a sentence can name columns as the file does."""
    return {meaning: labels.get(letter, meaning) for meaning, letter in ref.columns.items()}


def _records(sheet: Sheet, column: AuditedColumn) -> list[int]:
    """The rows of the block that are records, by the key the entry type states.

    Without the key - the posted entry's own lines, for instance - every row of the block is
    a line and all of them count.
    """
    rows = range(column.ref.first_row, column.ref.last_row + 1)
    key = column.ref.columns.get(column.record_key) if column.record_key else None
    if key is None:
        return list(rows)
    at = column_index(key)
    return [row for row in rows if str(sheet.cell(row, at).value or "").strip()]


def _runs(
    shapes: Sequence[FormulaShape], sheet: str, letter: str, first: int, last: int
) -> list[FormulaShape]:
    """The profiler's own runs for this column, limited to the table's data rows.

    Reusing the profile rather than re-deriving the shapes keeps one definition of "the same
    formula": the R1C1 normalisation that makes a filled-down column one run.
    """
    return [
        shape
        for shape in shapes
        if shape.sheet == sheet
        and shape.column == letter
        and shape.last_row >= first
        and shape.first_row <= last
    ]


def _typed_over(defects: Iterable[Defect], sheet: str, letter: str, rows: set[int]) -> list[str]:
    """Cells the profiler already found holding a constant in a calculated column.

    Restricted to the records, like everything else here. The header band of a real working
    holds the preparer's own control total in the same column, and once the column below it
    calculates, that total is a constant among formulas - reported, it reads as a number
    typed over the arithmetic when it is the total *of* the arithmetic.
    """
    found: list[str] = []
    for defect in defects:
        if defect.kind is not DefectKind.TYPED_OVER_FORMULA or defect.cell.sheet != sheet:
            continue
        cell = defect.cell.cell
        digits = cell.lstrip(_LETTERS)
        if cell[: len(cell) - len(digits)] != letter or not digits.isdigit():
            continue
        if int(digits) in rows:
            found.append(cell)
    return found


def _meanings_read(formula: str, ref: TableRef, row: int) -> frozenset[str] | None:
    """The meanings a formula reads, or ``None`` when that cannot be established.

    ``None`` where the formula reaches another sheet or a column the map never bound: the
    reference is real but what it *means* is not known here, and guessing would turn an
    ordinary cross-sheet link into a finding.
    """
    by_letter = {letter: meaning for meaning, letter in ref.columns.items()}
    meanings: set[str] = set()
    for match in _REFERENCE.finditer(formula):
        if match.group("other") is not None:
            return None
        letter = match.group("column")
        if letter is None:  # pragma: no cover - one branch or the other always matches
            continue
        meaning = by_letter.get(letter)
        if meaning is None:
            return None
        meanings.add(meaning)
    return frozenset(meanings) if meanings else None


def _in_headers(formula: str, labels: Mapping[str, str], row: int) -> str:
    """The formula with each same-sheet reference replaced by that column's header.

    A substitution, never a translation - the same rule the split work paper keeps. A
    reference to another sheet is left exactly as written, because the header of a column on
    a tab this table does not cover is not something this function knows.
    """

    def swap(match: re.Match[str]) -> str:
        if match.group("other") is not None:
            return match.group(0)
        letter = match.group("column")
        header = labels.get(letter or "")
        if not header:
            return match.group(0)
        at = match.group("row")
        suffix = "" if at is None or int(at) == row else f" row {at}"
        return f"[{header}]{suffix}"

    return _REFERENCE.sub(swap, formula).strip()


def _join(names: Sequence[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


__all__ = ["AuditedColumn", "audit_formulas"]
