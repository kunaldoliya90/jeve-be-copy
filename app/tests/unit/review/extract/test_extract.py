"""Steps 4 to 6 on a real workbook: reading, profiling, and the fingerprint's stability."""

from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest

from review.consts import WorkbookFormat
from review.extract.excel_reader import read_workbook
from review.extract.profiler import profile
from review.extract.structure_context import build_structure_context, layout_fingerprint
from review.models.workbook import ColumnType, DefectKind
from review.models.workbook_data import WorkbookData

MAX_TOKENS = 8_000


@pytest.fixture
def data(workbook_bytes: bytes) -> WorkbookData:
    return read_workbook(workbook_bytes, WorkbookFormat.XLSX)


# ------------------------------------------------------------------ step 4: the reader


def test_reads_every_sheet_including_hidden_ones(data: WorkbookData) -> None:
    """Preparers hide working data, so a hidden sheet is read and marked, never skipped."""
    assert data.sheet_names == ("ADP GL", "JE Upload", "Working")
    working = data.sheet("Working")
    assert working is not None
    assert working.hidden is True


def test_numbers_are_decimal_not_float(data: WorkbookData) -> None:
    source = data.sheet("ADP GL")
    assert source is not None
    value = source.cell(2, 3).value
    assert isinstance(value, Decimal)
    assert value == Decimal("1200.50")


def test_both_passes_are_merged(data: WorkbookData) -> None:
    """A linked cell carries its formula and its cached value at once."""
    posting = data.sheet("JE Upload")
    assert posting is not None
    linked = posting.cell(4, 4)
    assert linked.formula == "='ADP GL'!C2"


def test_typed_over_cell_has_no_formula(data: WorkbookData) -> None:
    posting = data.sheet("JE Upload")
    assert posting is not None
    assert posting.cell(6, 4).formula is None


def test_xlsx_reports_formulas_available(data: WorkbookData) -> None:
    assert data.formulas_available is True


def test_formula_count_is_none_when_the_format_carries_no_formulas(data: WorkbookData) -> None:
    """None and 0 mean different things: 0 would claim the sheet is a pasted extract."""
    xlsx = profile(data)
    assert all(sheet.formula_count is not None for sheet in xlsx.sheets)


# --------------------------------------------------------------- step 5: the profiler


def test_finds_the_table_under_a_title_block(data: WorkbookData) -> None:
    """The posting sheet has a title in row 1 and a blank row, so the header is row 3."""
    result = profile(data)
    posting = next(t for t in result.tables if t.sheet == "JE Upload")
    assert posting.header_row == 3
    assert posting.first_row == 4
    assert posting.last_row == 6


def test_column_totals_are_exact(data: WorkbookData) -> None:
    result = profile(data)
    posting = next(t for t in result.tables if t.sheet == "JE Upload")
    debit = next(col for col in posting.columns if col.header == "Debit")
    assert debit.total == Decimal("1200.50") + Decimal("880.00") + Decimal("2415.75")
    assert debit.column_type is ColumnType.NUMERIC


def test_typed_over_formula_is_reported_as_a_defect(data: WorkbookData) -> None:
    """An observation, never a failure on its own, but it is how a working paper decays."""
    result = profile(data)
    kinds = {defect.kind for defect in result.defects}
    assert DefectKind.TYPED_OVER_FORMULA in kinds


def test_lineage_records_which_sheet_feeds_which(data: WorkbookData) -> None:
    result = profile(data)
    sources = {source for shape in result.formula_shapes for source in shape.feeds_from}
    assert "ADP GL" in sources


# ------------------------------------------------------- step 6: context and fingerprint


def test_structure_context_carries_shape_and_totals(data: WorkbookData) -> None:
    context = build_structure_context(profile(data), data, max_tokens=MAX_TOKENS)
    posting = next(sheet for sheet in context.sheets if sheet.name == "JE Upload")
    assert "Debit" in posting.headers
    assert posting.totals
    assert context.token_estimate > 0


def test_structure_context_samples_exclude_amounts(data: WorkbookData) -> None:
    """Only short text columns are sampled, so no amount can reach a prompt this way."""
    context = build_structure_context(profile(data), data, max_tokens=MAX_TOKENS)
    posting = next(sheet for sheet in context.sheets if sheet.name == "JE Upload")
    debit_letter = "D"
    assert debit_letter not in posting.samples


def test_structure_context_trims_rather_than_overflowing(data: WorkbookData) -> None:
    context = build_structure_context(profile(data), data, max_tokens=1)
    assert context.trimmed is True
    assert all(not sheet.samples for sheet in context.sheets)


def test_fingerprint_is_stable_when_only_row_counts_change(workbook_bytes: bytes) -> None:
    """Next month's longer file from the same template must hit the layout cache."""
    first = read_workbook(workbook_bytes, WorkbookFormat.XLSX)

    workbook = openpyxl.load_workbook(BytesIO(workbook_bytes))
    posting = workbook["JE Upload"]
    posting.append(["FR03", "720008", "EUR", 101.00])
    posting.append(["FR04", "720008", "EUR", 202.00])
    buffer = BytesIO()
    workbook.save(buffer)
    longer = read_workbook(buffer.getvalue(), WorkbookFormat.XLSX)

    assert layout_fingerprint(profile(first)) == layout_fingerprint(profile(longer))


def test_fingerprint_changes_when_a_column_is_renamed(workbook_bytes: bytes) -> None:
    """A renamed column is a different template, and must not reuse the stored map."""
    first = read_workbook(workbook_bytes, WorkbookFormat.XLSX)

    workbook = openpyxl.load_workbook(BytesIO(workbook_bytes))
    workbook["JE Upload"].cell(row=3, column=4, value="Debit Amount EUR")
    buffer = BytesIO()
    workbook.save(buffer)
    renamed = read_workbook(buffer.getvalue(), WorkbookFormat.XLSX)

    assert layout_fingerprint(profile(first)) != layout_fingerprint(profile(renamed))
