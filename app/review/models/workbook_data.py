"""The parsed workbook, held in memory for the length of one review and never persisted.

Kept apart from ``workbook.py`` on purpose. A ``WorkbookProfile`` is measurements and may be
written to the audit trail; ``WorkbookData`` holds the cells themselves, including employee
rows, and goes no further than the tools that read it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from review.consts import WorkbookFormat

CellValue = str | Decimal | date | datetime | bool | None


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell as both passes saw it.

    ``formula`` is ``None`` on a value-only read (``.xlsb``), which is not the same as a cell
    that genuinely holds no formula — ``WorkbookData.formulas_available`` says which.
    """

    value: CellValue = None
    formula: str | None = None
    number_format: str | None = None

    @property
    def is_blank(self) -> bool:
        """Empty of both value and formula.

        A cell carrying a formula is never blank, even with no cached value: a workbook
        written by a tool rather than by Excel has formulas and no cached values at all, and
        treating those cells as empty would hide every table on the sheet.
        """
        if self.formula is not None:
            return False
        return self.value is None or (isinstance(self.value, str) and not self.value.strip())


@dataclass(frozen=True, slots=True)
class Sheet:
    """One worksheet. ``cells`` is sparse: absent keys are empty cells."""

    name: str
    hidden: bool = False
    max_row: int = 0
    max_column: int = 0
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)

    def cell(self, row: int, column: int) -> Cell:
        """The cell at a 1-based position, or a blank one. Never raises on an empty cell."""
        return self.cells.get((row, column), Cell())

    def row_values(self, row: int) -> list[CellValue]:
        """One row left to right, padded to ``max_column`` so positions line up."""
        return [self.cell(row, col).value for col in range(1, self.max_column + 1)]


@dataclass(frozen=True, slots=True)
class PictureData:
    """One screenshot pasted onto a sheet, with the bytes themselves.

    In memory for the length of the review and never written anywhere. A screenshot of a
    payroll screen is the most sensitive thing in one of these files, and the only thing
    that leaves the server is what AI-R read off it — amounts and labels, checked by code.
    """

    picture_id: str
    sheet: str
    anchor_cell: str
    sha256: str
    content_type: str
    data: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class WorkbookData:
    """Step 4's output: every sheet read once, with formulas where the format carries them."""

    sheets: tuple[Sheet, ...] = ()
    workbook_format: WorkbookFormat = WorkbookFormat.XLSX
    formulas_available: bool = True
    """False for ``.xlsb``. Lineage, tie-out and typed-over detection degrade rather than
    silently report nothing, and the review records that they did."""
    pictures: tuple[PictureData, ...] = ()
    """Screenshots pasted onto the sheets. Empty for ``.xlsb``, which carries no drawings
    this reader can reach."""

    def sheet(self, name: str) -> Sheet | None:
        return next((sheet for sheet in self.sheets if sheet.name == name), None)

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(sheet.name for sheet in self.sheets)
