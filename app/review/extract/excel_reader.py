"""Step 4. Read the workbook once into memory, values and formulas both.

``.xlsx`` and ``.xlsm`` are read twice by openpyxl — once for cached values, once for
formulas — because a review needs both: the value to check arithmetic, the formula to prove
a line is linked rather than typed. ``.xlsb`` has no formula surface in pyxlsb, so it is read
once and ``formulas_available`` is False; the checks that need formulas degrade to
``unable_to_validate`` rather than quietly reporting nothing.

Numbers are converted to ``Decimal`` from the cell's own text, never through ``float``:
a binary float cannot represent 0.1, and these entries are reconciled to the cent.
"""

import logging
import struct
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from api.exceptions.review_exceptions import WorkbookUnreadableError
from review.consts import BLANK_RUN_LIMIT, WorkbookFormat
from review.extract.pictures import extract_pictures
from review.models.workbook_data import Cell, CellValue, Sheet, WorkbookData

logger = logging.getLogger(__name__)


def read_workbook(source: Path | bytes, workbook_format: WorkbookFormat) -> WorkbookData:
    """Parse the workbook, or raise ``WorkbookUnreadableError``.

    The gatekeeper has already established that this is a safe package, so anything that
    fails here is a defect in the workbook itself.
    """
    content = source.read_bytes() if isinstance(source, Path) else source
    if workbook_format is WorkbookFormat.XLSB:
        return _read_xlsb(content)
    return _read_ooxml(content, workbook_format)


def _read_ooxml(content: bytes, workbook_format: WorkbookFormat) -> WorkbookData:
    try:
        values = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
        formulas = openpyxl.load_workbook(BytesIO(content), data_only=False, read_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise WorkbookUnreadableError(
            f"The workbook could not be opened: {exc}", {"format": workbook_format}
        ) from exc

    try:
        sheets = tuple(
            _read_ooxml_sheet(values[name], formulas[name]) for name in values.sheetnames
        )
    finally:
        values.close()
        formulas.close()

    return WorkbookData(
        sheets=sheets,
        workbook_format=workbook_format,
        formulas_available=True,
        # Read from the package rather than through openpyxl: a read-only load does not
        # expose drawings, and a full load costs more than the rest of step 4 together.
        pictures=extract_pictures(content),
    )


def _read_ooxml_sheet(value_sheet: Worksheet, formula_sheet: Worksheet) -> Sheet:
    """Merge the two passes into one sparse cell map, skipping cells empty in both.

    Positions come from enumeration rather than ``cell.row`` / ``cell.column``: a read-only
    worksheet yields ``EmptyCell`` for gaps, and those carry a value but no coordinates.
    """
    cells: dict[tuple[int, int], Cell] = {}
    max_row = max_column = 0

    formula_rows = formula_sheet.iter_rows()
    for row_index, value_row in enumerate(value_sheet.iter_rows(), start=1):
        formulas = [cell.value for cell in next(formula_rows, ())]
        for column_index, cell in enumerate(value_row, start=1):
            value = _coerce(cell.value)
            raw = formulas[column_index - 1] if column_index <= len(formulas) else None
            formula = raw if isinstance(raw, str) and raw.startswith("=") else None
            if value is None and formula is None:
                continue
            cells[(row_index, column_index)] = Cell(
                value=value,
                formula=formula,
                number_format=getattr(cell, "number_format", None),
            )
            max_row = max(max_row, row_index)
            max_column = max(max_column, column_index)

    return Sheet(
        name=value_sheet.title,
        hidden=value_sheet.sheet_state != "visible",
        max_row=max_row,
        max_column=max_column,
        cells=cells,
    )


def _read_xlsb(content: bytes) -> WorkbookData:
    """Values only. pyxlsb exposes no formulas, and the result records that it did not."""
    try:
        from pyxlsb import open_workbook
    except ImportError as exc:  # pragma: no cover - pinned in base.txt
        raise WorkbookUnreadableError("pyxlsb is required to read .xlsb workbooks") from exc

    sheets: list[Sheet] = []
    try:
        with open_workbook(BytesIO(content)) as book:
            for name in book.sheets:
                with book.get_sheet(name) as sheet:
                    sheets.append(_read_xlsb_sheet(name, sheet))
    # pyxlsb defines no exception type of its own: a malformed record surfaces as whatever
    # the binary parser hit. These are the ones a corrupt or truncated file produces.
    except (struct.error, ValueError, KeyError, IndexError, EOFError, OSError) as exc:
        raise WorkbookUnreadableError(f"The .xlsb workbook could not be read: {exc}") from exc

    logger.info("xlsb read without formulas sheets=%d", len(sheets))
    return WorkbookData(
        sheets=tuple(sheets),
        workbook_format=WorkbookFormat.XLSB,
        formulas_available=False,
    )


def _read_xlsb_sheet(name: str, sheet: Any) -> Sheet:
    cells: dict[tuple[int, int], Cell] = {}
    max_row = max_column = 0
    blank_run = 0

    for row in sheet.rows():
        populated = False
        for item in row:
            value = _coerce(item.v)
            if value is None:
                continue
            populated = True
            # pyxlsb indexes from zero; everything else here is 1-based.
            position = (item.r + 1, item.c + 1)
            cells[position] = Cell(value=value)
            max_row = max(max_row, position[0])
            max_column = max(max_column, position[1])

        blank_run = 0 if populated else blank_run + 1
        if blank_run > BLANK_RUN_LIMIT:
            # A stray cell near row 1,048,576 would otherwise hold the read open for minutes.
            break

    return Sheet(name=name, max_row=max_row, max_column=max_column, cells=cells)


def _coerce(value: object) -> CellValue:
    """Normalise one raw cell value, keeping money exact.

    ``bool`` is checked before ``int`` because in Python ``True`` is an ``int``, and a flag
    column summed as 1s and 0s would be a quietly wrong total.
    """
    if value is None or isinstance(value, bool | Decimal | date | datetime):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # repr() of a float is its shortest round-tripping text, which is the value the
        # preparer saw; Decimal(float) would carry the binary artefact instead.
        try:
            return Decimal(repr(value))
        except InvalidOperation:  # pragma: no cover - nan and inf only
            return None
    if isinstance(value, str):
        return value
    return str(value)
