"""What the workbook is, as Python reads it: cell references, the profile and the context.

``WorkbookProfile`` is everything step 5 measured; ``StructureContext`` is the much smaller
projection of it that step 6 builds and AI-1 is allowed to see. The split is the point: the
profile may hold values, the context may not.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ColumnType(StrEnum):
    """What a column's data rows mostly hold, decided by share, not by header."""

    NUMERIC = "numeric"
    DATE = "date"
    TEXT = "text"
    EMPTY = "empty"


class DefectKind(StrEnum):
    """Workbook problems worth reporting. None of them can fail a check on its own."""

    REF_ERROR = "ref_error"
    NA_ERROR = "na_error"
    VALUE_ERROR = "value_error"
    EXTERNAL_LINK = "external_link"
    TYPED_OVER_FORMULA = "typed_over_formula"


class CellRef(BaseModel):
    """One cell, addressed the way a reviewer would quote it."""

    model_config = ConfigDict(frozen=True)

    sheet: str = Field(..., min_length=1, description='Sheet name, e.g. "JE Upload"')
    cell: str = Field(..., min_length=2, description='A1 reference, e.g. "F12"')


class TableRef(BaseModel):
    """Where a table sits and which column carries which meaning.

    ``columns`` maps a meaning the JE type asked for onto the column letter that holds it,
    so tool arguments name meanings and never raw ranges.
    """

    model_config = ConfigDict(frozen=True)

    sheet: str = Field(..., min_length=1)
    header_row: int = Field(..., ge=1, description="1-based row holding the headers")
    first_row: int = Field(..., ge=1, description="1-based first data row")
    last_row: int = Field(..., ge=1, description="1-based last data row")
    columns: dict[str, str] = Field(
        default_factory=dict, description='Meaning -> column letter, e.g. {"debit": "F"}'
    )


class PictureRef(BaseModel):
    """A screenshot pasted onto a sheet, extracted with the cell it is anchored to."""

    model_config = ConfigDict(frozen=True)

    picture_id: str = Field(..., description='Assigned in reading order: "P1", "P2", ...')
    sheet: str = Field(..., min_length=1)
    anchor_cell: str = Field(..., description="Top-left cell the drawing is anchored to")
    sha256: str = Field(..., min_length=64, max_length=64, description="Keys the picture cache")


class ColumnProfile(BaseModel):
    """One column of one table, measured rather than interpreted."""

    model_config = ConfigDict(frozen=True)

    letter: str = Field(..., min_length=1)
    header: str = Field(default="", description="Header text as written, before normalising")
    column_type: ColumnType = Field(default=ColumnType.EMPTY)
    numeric_ratio: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Share of data rows holding a number"
    )
    total: Decimal | None = Field(
        default=None, description="Exact sum of the numeric cells, or None if not numeric"
    )
    distinct_count: int | None = Field(default=None, ge=0)


class TableProfile(BaseModel):
    """A contiguous block of rows under a header row."""

    model_config = ConfigDict(frozen=True)

    sheet: str = Field(..., min_length=1)
    header_row: int = Field(..., ge=1)
    first_row: int = Field(..., ge=1)
    last_row: int = Field(..., ge=1)
    columns: tuple[ColumnProfile, ...] = Field(default=())

    @property
    def row_count(self) -> int:
        return max(0, self.last_row - self.first_row + 1)


class FormulaShape(BaseModel):
    """A run of identical R1C1 formulas down a column, and the sheets it reads from.

    Row counts are deliberately excluded from the shape so that next month's longer file
    fingerprints the same as this month's.
    """

    model_config = ConfigDict(frozen=True)

    sheet: str = Field(..., min_length=1)
    column: str = Field(..., min_length=1)
    r1c1: str = Field(..., min_length=1, description="The formula in R1C1 form")
    first_row: int = Field(..., ge=1)
    last_row: int = Field(..., ge=1)
    feeds_from: tuple[str, ...] = Field(
        default=(), description="Sheet names this formula reads, for lineage"
    )


class Defect(BaseModel):
    """One workbook problem, reported as an observation."""

    model_config = ConfigDict(frozen=True)

    kind: DefectKind
    cell: CellRef
    detail: str = Field(default="")


class SheetProfile(BaseModel):
    """One sheet, measured. Hidden sheets are read and marked, never skipped."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    hidden: bool = Field(default=False, description="Preparers hide working data")
    row_count: int = Field(default=0, ge=0)
    column_count: int = Field(default=0, ge=0)
    formula_count: int | None = Field(
        default=None,
        description="None when the formula pass was skipped, which is not the same as zero",
    )
    picture_ids: tuple[str, ...] = Field(default=())


class WorkbookProfile(BaseModel):
    """Step 5. Everything Python measured about the workbook, with no AI involved."""

    model_config = ConfigDict(frozen=True)

    sheets: tuple[SheetProfile, ...] = Field(default=())
    tables: tuple[TableProfile, ...] = Field(default=())
    formula_shapes: tuple[FormulaShape, ...] = Field(default=())
    defects: tuple[Defect, ...] = Field(default=())
    pictures: tuple[PictureRef, ...] = Field(default=())


class SheetSummary(BaseModel):
    """One sheet as AI-1 is allowed to see it: shape and labels, never employee values."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    hidden: bool = Field(default=False)
    row_count: int = Field(default=0, ge=0)
    headers: tuple[str, ...] = Field(default=())
    column_types: tuple[ColumnType, ...] = Field(default=())
    totals: dict[str, Decimal] = Field(
        default_factory=dict, description="Column letter -> exact total"
    )
    samples: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        description="Short category columns only: codes, currencies, periods. Never amounts",
    )


class StructureContext(BaseModel):
    """Step 6. The only view of the workbook AI-1 ever receives.

    Capped at ``STRUCTURE_CONTEXT_MAX_TOKENS``; when it overflows, samples are dropped first
    and then tables beyond the first few per sheet are summarised to one line.
    """

    model_config = ConfigDict(frozen=True)

    sheets: tuple[SheetSummary, ...] = Field(default=())
    lineage: tuple[str, ...] = Field(
        default=(), description='Readable feeds-from edges, e.g. "JE Upload!F <- ADP GL!D"'
    )
    defects_summary: tuple[str, ...] = Field(default=())
    layout_fingerprint: str = Field(
        ..., min_length=64, max_length=64, description="Keys the layout cache; see step 7"
    )
    token_estimate: int = Field(default=0, ge=0)
    trimmed: bool = Field(
        default=False, description="True when the cap forced samples or tables to be dropped"
    )
