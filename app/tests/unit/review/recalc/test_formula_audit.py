"""The workbook's own formulas, examined.

The gap this closes is the reason the module exists, so it is the first test here: a column
built from the wrong inputs produces a figure that *ties* on every record where the missing
input happens to be zero. Every other check in the harness passes such a workbook. This one
reports it, with the cell to open the file at.

The two false-positive guards below are as important as the faults. Both came from the real
workbooks: a preparer's own ``SUBTOTAL`` sitting under the data was being read as the
column's arithmetic, and a column that sums itself is a total rather than a different way of
deriving the figure. A confident false finding is worse than no finding at all.
"""

from collections.abc import Sequence
from decimal import Decimal

from api.consts import RiskRating
from review.models.context import (
    Band,
    BookedSpec,
    BusinessRules,
    RecalcInputSpec,
    RecalculationSpec,
    ReperformanceSpec,
    RowIdentitySpec,
)
from review.models.recalculation import FormulaFault, FormulaFinding
from review.models.sheet_map import SheetMap
from review.models.verdicts import Status
from review.models.workbook import CellRef, Defect, DefectKind, FormulaShape, TableRef
from review.models.workbook_data import Cell, CellValue, Sheet, WorkbookData
from review.recalc.formula_audit import audit_formulas
from review.tools.base import ToolContext
from review.utils.sheet_utils import column_letter

_RULES = BusinessRules(
    version=1,
    credits={
        Status.PASS: 100,
        Status.PARTIAL: 50,
        Status.UNABLE_TO_VALIDATE: 50,
        Status.MISSING_EVIDENCE: 0,
        Status.FAIL: 0,
    },
    bands=(Band(min=0, label="Risk", risk=RiskRating.HIGH),),
)

HEADERS = ("employee_ref", "fte_accrual", "non_fte_accrual", "total_accrual")
LABELS = ("Matricule", "FTE Vacation Accrual", "Non-FTE Vacation Accrual", "TOTAL ACCRUAL")

RECALC = RecalculationSpec(
    table="calculation",
    record_key="employee_ref",
    stated="total_accrual",
    formula="fte_amount + non_fte_amount",
    booked=BookedSpec(table="je_posting"),
    inputs=(
        RecalcInputSpec(id="fte_amount", meaning="fte_accrual"),
        RecalcInputSpec(id="non_fte_amount", meaning="non_fte_accrual"),
    ),
)
REPERFORMANCE = ReperformanceSpec(
    identities=(
        RowIdentitySpec(
            id="fte_split_sums",
            label="FTE plus non-FTE against the stated total",
            expression="fte_accrual + non_fte_accrual",
            against="total_accrual",
        ),
    )
)


Record = tuple[str, Decimal, Decimal, Decimal]


def workbook(
    rows: Sequence[Record],
    *,
    formulas: dict[str, str] | None = None,
    formulas_available: bool = True,
    last_row: int | None = None,
) -> tuple[ToolContext, tuple[FormulaShape, ...]]:
    """One calculation tab. ``formulas`` is keyed by cell, e.g. ``{"D2": "=B2+C2"}``."""
    cells: dict[tuple[int, int], Cell] = {}
    for column, label in enumerate(LABELS, start=1):
        cells[(1, column)] = Cell(value=label)
    for offset, record in enumerate(rows):
        values: tuple[CellValue, ...] = record
        for column, value in enumerate(values, start=1):
            cells[(2 + offset, column)] = Cell(value=value)

    for cell, formula in (formulas or {}).items():
        letter = cell.rstrip("0123456789")
        row = int(cell[len(letter) :])
        column = sum((ord(char) - 64) * 26**index for index, char in enumerate(reversed(letter)))
        existing = cells.get((row, column))
        cells[(row, column)] = Cell(value=existing.value if existing else None, formula=formula)

    sheet = Sheet(name="GPAY", max_row=1 + len(rows), max_column=len(LABELS), cells=cells)
    data = WorkbookData(sheets=(sheet,), formulas_available=formulas_available)
    ref = TableRef(
        sheet="GPAY",
        header_row=1,
        first_row=2,
        last_row=last_row or (1 + len(rows)),
        columns={meaning: column_letter(index) for index, meaning in enumerate(HEADERS, start=1)},
    )
    ctx = ToolContext(data=data, sheet_map=SheetMap(tables={"calculation": ref}), rules=_RULES)
    return ctx, _shapes(sheet)


def _shapes(sheet: Sheet) -> tuple[FormulaShape, ...]:
    """The profiler's own runs, built the profiler's way, so the audit sees what it sees."""
    from review.extract.profiler import profile

    return profile(WorkbookData(sheets=(sheet,))).formula_shapes


def audit(ctx: ToolContext, shapes: tuple[FormulaShape, ...]) -> list[FormulaFinding]:
    findings, _ = audit_formulas(ctx, shapes, REPERFORMANCE, RECALC)
    return list(findings)


TIES: list[Record] = [
    ("E1", Decimal(100), Decimal(0), Decimal(100)),
    ("E2", Decimal(150), Decimal(0), Decimal(150)),
    ("E3", Decimal(200), Decimal(0), Decimal(200)),
]


# ------------------------------------------------------------------- the gap this closes


def test_a_column_built_from_the_wrong_inputs_is_reported_even_though_it_ties() -> None:
    """The whole point. Non-FTE is zero on these records, so ``=B`` gives the right total.

    The figures agree, the entry reconciles, every other check passes - and the column is
    still not the approved arithmetic. Nothing else in the harness can see this.
    """
    ctx, shapes = workbook(TIES, formulas={"D2": "=B2", "D3": "=B3", "D4": "=B4"})

    findings = audit(ctx, shapes)

    wrong = next(f for f in findings if f.fault is FormulaFault.DIFFERENT_INPUTS)
    assert wrong.cell == "D2"
    assert wrong.column == "D"
    assert wrong.header == "TOTAL ACCRUAL"
    assert wrong.formula == "=B2"
    assert wrong.in_headers == "=[FTE Vacation Accrual]"
    # The approved arithmetic in the preparer's own column names, so the two lines a reader
    # compares are written in the same vocabulary.
    assert wrong.expected == "FTE Vacation Accrual + Non-FTE Vacation Accrual"
    assert "does not read Non-FTE Vacation Accrual" in wrong.why
    assert "a tie here is not evidence" in wrong.why


def test_a_column_reading_extra_columns_is_reported_too() -> None:
    ctx, shapes = workbook(TIES, formulas={"D2": "=B2+C2+A2", "D3": "=B3+C3+A3", "D4": "=B4+C4+A4"})

    findings = audit(ctx, shapes)

    wrong = next(f for f in findings if f.fault is FormulaFault.DIFFERENT_INPUTS)
    assert "it reads Matricule, which the approved arithmetic does not" in wrong.why


def test_the_approved_arithmetic_written_another_way_is_not_a_finding() -> None:
    """``=SUM(B2:C2)`` and ``=B2+C2`` are the same arithmetic. Only the inputs are compared."""
    ctx, shapes = workbook(
        TIES, formulas={"D2": "=SUM(B2:C2)", "D3": "=SUM(B3:C3)", "D4": "=SUM(B4:C4)"}
    )

    assert [f.fault for f in audit(ctx, shapes)] == []


# --------------------------------------------------------------------------- other faults


def test_a_column_nobody_calculated_is_reported_as_stated_not_computed() -> None:
    ctx, shapes = workbook(TIES)

    stated = next(f for f in audit(ctx, shapes) if f.fault is FormulaFault.NOT_CALCULATED)
    assert stated.column == "D"
    assert stated.cell == "D2"
    assert stated.rows_affected == 3
    assert "typed or pasted: 3 of them" in stated.why


def test_a_column_whose_cells_disagree_is_reported_with_the_odd_one() -> None:
    """The classic: a filled-down column where one row was edited afterwards."""
    ctx, shapes = workbook(TIES, formulas={"D2": "=B2+C2", "D3": "=B3+C3+1", "D4": "=B4+C4"})

    odd = next(f for f in audit(ctx, shapes) if f.fault is FormulaFault.INCONSISTENT)
    assert odd.cell == "D3"
    assert odd.formula == "=B3+C3+1"
    assert odd.rows_affected == 1
    assert "does not carry the same formula as the rest of its column" in odd.why


def test_a_number_typed_over_a_formula_is_reported_with_its_cell() -> None:
    ctx, shapes = workbook(TIES, formulas={"D2": "=B2+C2", "D4": "=B4+C4"})
    ctx.defects = (
        Defect(kind=DefectKind.TYPED_OVER_FORMULA, cell=CellRef(sheet="GPAY", cell="D3")),
    )

    typed = next(f for f in audit(ctx, shapes) if f.fault is FormulaFault.TYPED_OVER)
    assert typed.cell == "D3"
    assert typed.rows_affected == 1
    assert "survives every recalculation" in typed.why


def test_a_format_with_no_formulas_says_so_rather_than_reporting_nothing_wrong() -> None:
    ctx, shapes = workbook(TIES, formulas_available=False)

    findings = audit(ctx, shapes)

    assert [f.fault for f in findings] == [FormulaFault.UNREADABLE]
    assert "stores values without the formulas" in findings[0].why


# ------------------------------------------------------------- the false-positive guards


def test_the_preparers_own_total_row_is_not_the_columns_arithmetic() -> None:
    """Found on a real workbook: the only formula in the column was the grand total.

    The block runs past the last record, so ``=SUBTOTAL(9,D2:D4)`` on the row beneath it was
    being read as how the column is computed - and reported as the wrong inputs.
    """
    ctx, shapes = workbook(TIES, formulas={"D5": "=SUBTOTAL(9,D2:D4)"}, last_row=5)

    findings = audit(ctx, shapes)

    assert FormulaFault.DIFFERENT_INPUTS not in {f.fault for f in findings}
    assert FormulaFault.NOT_CALCULATED in {f.fault for f in findings}, (
        "the records themselves are still stated rather than calculated"
    )


def test_a_column_that_reads_itself_is_a_carry_forward_not_a_different_derivation() -> None:
    """``=D1+B2`` is the row above plus this row's movement: a running total, not a formula.

    A real derivation cannot reference its own column without being circular, so reading
    itself is the signal. Without this, every cumulative column in every workbook would be
    reported as computed from the wrong inputs.
    """
    ctx, shapes = workbook(TIES, formulas={"D2": "=D1+B2", "D3": "=D2+B3", "D4": "=D3+B4"})

    assert FormulaFault.DIFFERENT_INPUTS not in {f.fault for f in audit(ctx, shapes)}


def test_a_formula_reaching_another_sheet_is_not_judged() -> None:
    """A link is a link. What a column on another tab *means* is not known here."""
    ctx, shapes = workbook(
        TIES,
        formulas={"D2": "='JE Create'!O6", "D3": "='JE Create'!O7", "D4": "='JE Create'!O8"},
    )

    assert FormulaFault.DIFFERENT_INPUTS not in {f.fault for f in audit(ctx, shapes)}


def test_a_column_the_business_declares_nothing_about_is_not_examined() -> None:
    """Inventing an expectation is how a work paper reports a finding nobody asked for."""
    ctx, shapes = workbook(TIES, formulas={"B2": "=A2", "B3": "=A3", "B4": "=A4"})

    findings = audit(ctx, shapes)

    assert all(finding.column != "B" for finding in findings)


def test_nothing_is_examined_when_the_entry_type_declares_no_arithmetic() -> None:
    ctx, shapes = workbook(TIES)

    findings, audited = audit_formulas(ctx, shapes, None, None)

    assert findings == ()
    assert audited == 0, "zero audited is not the same as finding nothing wrong"
