"""Step 5. Measure the workbook without understanding it.

Nothing here interprets a header or decides what a sheet is for — that is AI-1's job at step
8, and it works from what this produces. A profile is arithmetic and counting only, which is
what makes it reproducible and safe to put in an audit trail.
"""

import logging
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal

from review.consts import (
    BLANK_RUN_LIMIT,
    HEADER_TEXT_SHARE,
    MIN_HEADER_COLUMNS,
    MIN_TYPED_OVER_FORMULAS,
)
from review.models.workbook import (
    CellRef,
    ColumnProfile,
    ColumnType,
    Defect,
    DefectKind,
    FormulaShape,
    PictureRef,
    SheetProfile,
    TableProfile,
    WorkbookProfile,
)
from review.models.workbook_data import Cell, CellValue, Sheet, WorkbookData
from review.utils.sheet_utils import column_letter

logger = logging.getLogger(__name__)

_ERROR_VALUES: dict[str, DefectKind] = {
    "#REF!": DefectKind.REF_ERROR,
    "#N/A": DefectKind.NA_ERROR,
    "#VALUE!": DefectKind.VALUE_ERROR,
}

_EXTERNAL_LINK = re.compile(r"\[\d+\]|'\[[^\]]+\]")
"""An external workbook reference, in either of the two forms Excel writes."""


def profile(data: WorkbookData) -> WorkbookProfile:
    """Everything step 6 and the map check need, measured."""
    sheets: list[SheetProfile] = []
    tables: list[TableProfile] = []
    shapes: list[FormulaShape] = []
    defects: list[Defect] = []

    for sheet in data.sheets:
        found = _find_tables(sheet)
        tables.extend(found)
        if data.formulas_available:
            shapes.extend(_formula_shapes(sheet))
        defects.extend(_defects(sheet))
        sheets.append(
            SheetProfile(
                name=sheet.name,
                hidden=sheet.hidden,
                row_count=sheet.max_row,
                column_count=sheet.max_column,
                formula_count=_formula_count(sheet, data),
                picture_ids=_picture_ids(sheet.name, data),
            )
        )

    logger.info("profiled sheets=%d tables=%d defects=%d", len(sheets), len(tables), len(defects))
    return WorkbookProfile(
        sheets=tuple(sheets),
        tables=tuple(tables),
        formula_shapes=tuple(shapes),
        defects=tuple(defects),
        # References only. The bytes stay on WorkbookData, which is never persisted.
        pictures=tuple(
            PictureRef(
                picture_id=picture.picture_id,
                sheet=picture.sheet,
                anchor_cell=picture.anchor_cell,
                sha256=picture.sha256,
            )
            for picture in data.pictures
        ),
    )


def _picture_ids(sheet_name: str, data: WorkbookData) -> tuple[str, ...]:
    """The screenshots anchored to one tab, so a reader can see where evidence sits."""
    return tuple(picture.picture_id for picture in data.pictures if picture.sheet == sheet_name)


# ----------------------------------------------------------------------------- tables


def _find_tables(sheet: Sheet) -> list[TableProfile]:
    """Contiguous blocks under a header row.

    Row populations are counted once from the sparse cell map, so the scan skips title rows
    and blank rows without materialising them — a 38,000-row sheet pays for its cells, not
    for its rows squared.
    """
    populated = _row_populations(sheet)
    candidates = [row for row in _candidate_rows(populated) if _is_header_row(sheet, row)]

    tables: list[TableProfile] = []
    claimed: list[range] = []
    # Widest first, earliest to break a tie. A preamble of batch options qualifies as a
    # header on its own terms and sits above the real line header, so taking the first match
    # would claim the whole sheet for six columns of metadata and never reach the twenty-six
    # columns of postings below it.
    for header in sorted(candidates, key=lambda row: (-populated[row], row)):
        if any(header in block for block in claimed):
            continue
        last = _last_data_row(sheet, header + 1)
        if last <= header:
            continue
        block = range(header, last + 1)
        if any(_overlaps(block, other) for other in claimed):
            continue
        claimed.append(block)
        tables.append(_table_profile(sheet, header, last))

    return sorted(tables, key=lambda table: table.header_row)


def _candidate_rows(populated: dict[int, int]) -> list[int]:
    """Rows that could begin a table: wide enough, and wider or narrower than the row above.

    A header sits at a discontinuity. Rows in the middle of a block have the same population
    as the row before them, so skipping those turns a per-row test over a 133,000-row sheet
    into a handful of checks.
    """
    return [
        row
        for row in sorted(populated)
        if populated[row] >= MIN_HEADER_COLUMNS and populated.get(row - 1, 0) != populated[row]
    ]


def _overlaps(first: range, second: range) -> bool:
    return first.start <= second.stop - 1 and second.start <= first.stop - 1


def _row_populations(sheet: Sheet) -> dict[int, int]:
    counts: dict[int, int] = {}
    for (row, _), cell in sheet.cells.items():
        if not cell.is_blank:
            counts[row] = counts.get(row, 0) + 1
    return counts


def _is_header_row(sheet: Sheet, row: int) -> bool:
    """A row of labels sitting on top of rows of data.

    Two signals, and the second carries the weight. Text share alone would reject a real
    posting row, which is mostly *text* — company code, account, currency, with a single
    numeric amount — so the test is that the header differs from the row beneath it in at
    least one column's type, "Debit" over a number, rather than that the data is numeric.

    Label uniqueness is deliberately not a signal. Real working papers repeat labels across
    related columns: one file names three of its forty-two GPAY columns alike, and its JE
    Create header reuses eight labels across fourteen columns. A uniqueness rule tight enough
    to matter rejected one or the other of them whatever its threshold.
    """
    header = _row_cells(sheet, row)
    labels = [cell.value for cell in header if not cell.is_blank]
    if len(labels) < MIN_HEADER_COLUMNS or _text_share(labels) < HEADER_TEXT_SHARE:
        return False

    below = _row_cells(sheet, row + 1)
    if len([cell for cell in below if not cell.is_blank]) < MIN_HEADER_COLUMNS:
        return False
    return any(
        isinstance(head.value, str) and not head.is_blank and _is_data_cell(cell)
        for head, cell in zip(header, below, strict=False)
    )


def _row_cells(sheet: Sheet, row: int) -> list[Cell]:
    return [sheet.cell(row, col) for col in range(1, sheet.max_column + 1)]


def _is_data_cell(cell: Cell) -> bool:
    """A cell that is not another label: a number, a date, or anything calculated."""
    if cell.is_blank:
        return False
    return cell.formula is not None or not isinstance(cell.value, str)


def _last_data_row(sheet: Sheet, first: int) -> int:
    """Walk down until the data runs out. A long blank run ends the block."""
    last = first - 1
    blank_run = 0
    for row in range(first, sheet.max_row + 1):
        if any(not cell.is_blank for cell in _row_cells(sheet, row)):
            last = row
            blank_run = 0
        else:
            blank_run += 1
            if blank_run > BLANK_RUN_LIMIT:
                break
    return last


def _table_profile(sheet: Sheet, header_row: int, last_row: int) -> TableProfile:
    first_row = header_row + 1
    columns: list[ColumnProfile] = []
    for index in range(1, sheet.max_column + 1):
        header = sheet.cell(header_row, index).value
        values = [sheet.cell(row, index).value for row in range(first_row, last_row + 1)]
        populated = [value for value in values if not _blank(value)]
        if not populated and _blank(header):
            continue
        columns.append(
            ColumnProfile(
                letter=column_letter(index),
                header=str(header).strip() if header is not None else "",
                column_type=_column_type(populated),
                numeric_ratio=_numeric_share(values),
                total=_exact_total(populated),
                distinct_count=len({str(value) for value in populated}),
            )
        )
    return TableProfile(
        sheet=sheet.name,
        header_row=header_row,
        first_row=first_row,
        last_row=last_row,
        columns=tuple(columns),
    )


# ----------------------------------------------------------------------- column measures


def _column_type(values: list[CellValue]) -> ColumnType:
    if not values:
        return ColumnType.EMPTY
    counts = Counter(_kind(value) for value in values)
    kind, _ = counts.most_common(1)[0]
    return kind


def _kind(value: CellValue) -> ColumnType:
    if isinstance(value, Decimal):
        return ColumnType.NUMERIC
    if isinstance(value, date | datetime):
        return ColumnType.DATE
    return ColumnType.TEXT


def _numeric_share(values: list[CellValue]) -> float:
    """Over every data row, blanks included: a column that is half empty is not 90% numeric."""
    if not values:
        return 0.0
    numeric = sum(1 for value in values if isinstance(value, Decimal))
    return numeric / len(values)


def _exact_total(values: list[CellValue]) -> Decimal | None:
    numbers = [value for value in values if isinstance(value, Decimal)]
    return sum(numbers, Decimal(0)) if numbers else None


# ---------------------------------------------------------------------- formulas, defects


def _formula_shapes(sheet: Sheet) -> list[FormulaShape]:
    """Runs of the same formula down a column, grouped so row counts leave the fingerprint."""
    shapes: list[FormulaShape] = []
    by_column: dict[int, list[tuple[int, str]]] = {}
    for (row, col), cell in sheet.cells.items():
        if cell.formula:
            by_column.setdefault(col, []).append((row, _to_r1c1(cell.formula, row, col)))

    for col, entries in sorted(by_column.items()):
        entries.sort()
        run_start, run_formula, previous = entries[0][0], entries[0][1], entries[0][0]
        for row, formula in [*entries[1:], (-1, "")]:
            if formula == run_formula and row == previous + 1:
                previous = row
                continue
            shapes.append(
                FormulaShape(
                    sheet=sheet.name,
                    column=column_letter(col),
                    r1c1=run_formula,
                    first_row=run_start,
                    last_row=previous,
                    feeds_from=_referenced_sheets(run_formula),
                )
            )
            run_start, run_formula, previous = row, formula, row
    return shapes


def _to_r1c1(formula: str, row: int, col: int) -> str:
    """Relative form, so the same formula copied down a column is one shape rather than many.

    A deliberately small translation: it replaces the row number of an A1 reference with its
    offset, which is enough to collapse a filled-down column.
    """

    def replace(match: re.Match[str]) -> str:
        letters, digits = match.group(1), match.group(2)
        offset = int(digits) - row
        return f"{letters}[{offset:+d}]"

    return re.sub(r"\$?([A-Z]{1,3})\$?(\d{1,7})", replace, formula)


def _referenced_sheets(formula: str) -> tuple[str, ...]:
    """Sheet names a formula reads, for the lineage line the structure context carries."""
    names = re.findall(r"'([^']+)'!|([A-Za-z_][\w.]*)!", formula)
    return tuple(dict.fromkeys(quoted or bare for quoted, bare in names if quoted or bare))


def _defects(sheet: Sheet) -> list[Defect]:
    """Error values, external links and numbers typed over a column of formulas."""
    defects: list[Defect] = []
    for (row, col), cell in sheet.cells.items():
        if isinstance(cell.value, str) and (kind := _ERROR_VALUES.get(cell.value.strip())):
            defects.append(_defect(kind, sheet.name, row, col))
        if cell.formula and _EXTERNAL_LINK.search(cell.formula):
            defects.append(_defect(DefectKind.EXTERNAL_LINK, sheet.name, row, col))
    defects.extend(_typed_over(sheet))
    return defects


def _typed_over(sheet: Sheet) -> list[Defect]:
    """A constant sitting in a column whose other rows are formulas.

    The preparer overwrote a calculation. It is an observation, never a failure on its own,
    but it is the single most common way a working paper stops being a working paper.
    """
    defects: list[Defect] = []
    by_column: dict[int, list[tuple[int, Cell]]] = {}
    for (row, col), cell in sheet.cells.items():
        by_column.setdefault(col, []).append((row, cell))

    for col, entries in by_column.items():
        formulas = [cell for _, cell in entries if cell.formula]
        constants = [
            (row, cell)
            for row, cell in entries
            if not cell.formula and isinstance(cell.value, Decimal)
        ]
        # The column must still read as a calculated one: more formulas than overwrites, and
        # enough of them to be a pattern rather than a pair of unrelated cells.
        if len(formulas) < MIN_TYPED_OVER_FORMULAS or len(formulas) <= len(constants):
            continue
        defects.extend(
            _defect(DefectKind.TYPED_OVER_FORMULA, sheet.name, row, col) for row, _ in constants
        )
    return defects


def _defect(kind: DefectKind, sheet: str, row: int, col: int) -> Defect:
    return Defect(kind=kind, cell=CellRef(sheet=sheet, cell=f"{column_letter(col)}{row}"))


def _formula_count(sheet: Sheet, data: WorkbookData) -> int | None:
    """Formula cells on this sheet, or ``None`` when the format carries no formulas.

    ``None`` is not zero: zero says a sheet is a pasted extract rather than a live working
    paper, and claiming that about a ``.xlsb`` nobody could read formulas from would be a
    finding we invented.
    """
    if not data.formulas_available:
        return None
    return sum(1 for cell in sheet.cells.values() if cell.formula)


# --------------------------------------------------------------------------- small helpers


def _blank(value: CellValue) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text_share(values: list[CellValue]) -> float:
    if not values:
        return 0.0
    return sum(1 for value in values if isinstance(value, str)) / len(values)
