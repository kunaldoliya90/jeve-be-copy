"""The category split work paper: an independent, bottom-up re-add of the working.

What this is, and what it is not. Every other artefact in ``review/`` answers a question the
business asked — *does the accrual re-perform*, *is each row on the right side*. This one
answers the question a reviewer asks next, which is **how** the number is arrived at: what
the entry's value is composed of, how it divides between the classes the workbook itself
sorts rows into, whether the totals the file states above its own data agree with a re-add of
the rows beneath them, and what one row's arithmetic looks like step by step.

Three rules shape every model below.

**Nothing here is named in code.** Not a row title, not a column title, not a category. The
row titles are the headers the preparer wrote; the column letters come from the verified
sheet map; the categories are whatever distinct values the classification column turns out to
hold. A workbook that spells its classes ``Permanent``/``Temp`` instead of ``FTE``/``Non-FTE``
produces a correct work paper with no edit here, which is the entire design intent — the same
intent as ``context/aliases.py`` one level up.

**Nothing here decides anything.** The split is a presentation of measurements. A status on
``ExceptionTest`` is the status step 16 already reached for that check, carried across rather
than recomputed: a work paper that formed its own second opinion on a check is how a report
comes to disagree with the check it reports on.

**A row is a row number.** ``RowWalkthrough`` traces one row's arithmetic, and the one thing
it must never carry is who that row is. Identifying columns are excluded twice over — by the
meaning the map bound them to, and by being near-unique down the table — so a column nobody
has taught the alias table about still fails closed.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from review.models.verdicts import Status


class SplitBasis(BaseModel):
    """Where the work paper was read from, and under what tolerance. The audit trail.

    A reviewer reopening this in March needs to know which block of which tab was re-added
    before any figure on it means anything, and ``formulas_available`` is why a ``.xlsb``
    work paper carries no rules section rather than an empty one.
    """

    model_config = ConfigDict(frozen=True)

    table: str = Field(..., min_length=1, description="The name the JE type binds this table as")
    sheet: str = Field(..., min_length=1)
    header_row: int = Field(..., ge=1)
    first_row: int = Field(..., ge=1)
    last_row: int = Field(..., ge=1)
    rows_read: int = Field(
        default=0,
        ge=0,
        description=(
            "Rows that carried a classification and were summed. Below the block's own row "
            "count whenever it reaches into the blank and subtotal rows beneath the data."
        ),
    )
    rows_total: int = Field(
        default=0,
        ge=0,
        description=(
            "Data rows in the block. Above ``rows_read`` whenever the block reaches past "
            "the last record - the rows below it are the preparer's own subtotals and "
            "spacers, and nothing here treats them as records that failed to classify."
        ),
    )
    classification_header: str = Field(
        default="", description="The header text of the column the split was taken on"
    )
    classification_column: str = Field(default="", description="Its column letter")
    classification_meaning: str | None = Field(
        default=None,
        description=(
            "The meaning the sheet map bound it to, when it bound one. None when the column "
            "was chosen by shape instead - the fallback, and worth a reader knowing about."
        ),
    )
    tolerance: Decimal = Field(
        default=Decimal(0), ge=0, description="What the business agreed counts as agreeing"
    )
    formulas_available: bool = Field(
        default=True, description="False for .xlsb, which carries no formulas to read rules from"
    )


class CategoryRef(BaseModel):
    """One class the workbook sorts its rows into, as the file spells it."""

    model_config = ConfigDict(frozen=True)

    key: str = Field(..., min_length=1, description="Case-folded, for joining amounts to it")
    label: str = Field(
        ..., min_length=1, description="The first spelling seen in the file, shown to a reader"
    )
    row_count: int = Field(default=0, ge=0, description="Rows carrying this class")


class ComponentSplit(BaseModel):
    """One measured column of the working, re-added and divided by class.

    ``header`` is the row title and it is the preparer's own words. ``amounts`` is keyed by
    ``CategoryRef.key`` and holds only classes that actually occur, so a reader never sees a
    column of zeros for a class this file does not use.
    """

    model_config = ConfigDict(frozen=True)

    header: str = Field(default="", description="The column's own header text, as written")
    column: str = Field(..., min_length=1, description="Column letter it was read from")
    meaning: str | None = Field(
        default=None, description="The meaning the map bound it to, when it bound one"
    )
    number_format: str | None = Field(
        default=None,
        description=(
            "Excel's own format string for the column, so money, days and percentages are "
            "rendered the way the file renders them rather than the way code guesses."
        ),
    )
    amounts: dict[str, Decimal] = Field(
        default_factory=dict, description="Category key -> the sum of this column's rows"
    )
    total: Decimal = Field(
        default=Decimal(0),
        description=(
            "The classes added together, so the split always visibly adds up to the total "
            "beside it. Rows carrying no class at all are outside both: the block a "
            "profiler claims reaches past the last record into the preparer's own subtotal "
            "rows, and summing those would double the column. ``SplitBasis.rows_read`` "
            "against ``rows_total`` is where a reader sees how many were left out."
        ),
    )
    rows_populated: int = Field(
        default=0, ge=0, description="Classified rows holding a number in this column"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shares(self) -> dict[str, float]:
        """Each class's share of this component. Zero total means no shares, not an error."""
        if not self.total:
            return {}
        return {key: float(amount / self.total) for key, amount in self.amounts.items()}


class SplitTieOut(BaseModel):
    """A total the file states above its own data, against a re-add of the rows below it.

    Found by shape, not by address: a numeric cell sitting in a component's column *above*
    the header row is a control total, and that is what makes this work on a workbook whose
    totals are not in row 6. ``stated_is_formula`` is the finding behind it — a control total
    that was typed rather than calculated agrees with nothing by accident.
    """

    model_config = ConfigDict(frozen=True)

    label: str = Field(default="", description="The component's header, which is what it totals")
    cell: str = Field(..., min_length=1, description='Where the stated total sits, e.g. "AU7"')
    stated: Decimal | None = Field(default=None, description="What the file says")
    recomputed: Decimal = Field(default=Decimal(0), description="What the rows add up to")
    variance: Decimal | None = Field(
        default=None, description="Stated less recomputed. None when the file states nothing"
    )
    within_tolerance: bool | None = Field(
        default=None, description="None when there is nothing to compare, which is not a pass"
    )
    stated_is_formula: bool | None = Field(
        default=None,
        description=(
            "Whether the stated total calculates itself. None on a format that carries no "
            "formulas - not the same as a hardcoded total, and never reported as one."
        ),
    )


class DerivedRule(BaseModel):
    """One live formula off the working's first data row, with its letters resolved.

    ``in_headers`` is the plain-English reading, and it is a substitution rather than a
    translation: every reference is replaced by the header of the column it points at, so the
    sentence a reviewer reads cannot say anything the file does not. Inventing prose for an
    arbitrary formula is the one thing that would put a wrong explanation under a right
    number.
    """

    model_config = ConfigDict(frozen=True)

    column: str = Field(..., min_length=1)
    row: int = Field(
        ...,
        ge=1,
        description=(
            "The row it was read off. The working's first data row wherever that row "
            "calculates; the first one below it that does, where the preparer typed over "
            "the formula. Carried because a reviewer opening the file needs the row that "
            "actually holds what they are reading."
        ),
    )
    header: str = Field(default="", description="What the formula produces, in the file's words")
    formula: str = Field(..., min_length=1, description="As written in the cell")
    in_headers: str = Field(
        default="", description="The same formula with each reference replaced by its header"
    )
    reads: tuple[str, ...] = Field(
        default=(), description="Headers it reads from, in the order they appear"
    )


class WalkStep(BaseModel):
    """One line of a single row's arithmetic: what the file holds, and how it got there."""

    model_config = ConfigDict(frozen=True)

    header: str = Field(default="")
    column: str = Field(..., min_length=1)
    cell: str = Field(..., min_length=1)
    value: Decimal | None = Field(default=None, description="None when the cell holds no number")
    number_format: str | None = Field(default=None)
    formula: str | None = Field(default=None, description="None when the cell is a typed constant")
    in_headers: str | None = Field(default=None, description="That formula, references resolved")


class RowWalkthrough(BaseModel):
    """One row of the working, traced step by step. Addressed by row number and nothing else.

    No name, no employee reference, no identifier of any kind: ``why`` says why this row was
    picked and ``row`` says where to find it, which is everything a reviewer needs to open the
    file and follow the same arithmetic. The amounts are here because a trace without them
    traces nothing - but the person they belong to is not, and must not become so.
    """

    model_config = ConfigDict(frozen=True)

    row: int = Field(..., ge=1)
    why: str = Field(default="", description="Why this row is on the work paper, written by code")
    category: str = Field(default="", description="The class this row carries, as spelled")
    context: tuple[tuple[str, str], ...] = Field(
        default=(),
        description=(
            "Header and value for the low-cardinality text columns - a cost centre, a "
            "currency. Never a column unique enough down the table to identify the row."
        ),
    )
    steps: tuple[WalkStep, ...] = Field(default=())


class ExceptionTest(BaseModel):
    """One check that ran, as the work paper reports it.

    Every field is carried from the check's own figures and from the status step 16 reached.
    Nothing here is recomputed: the point of the section is to show what the tests found, and
    a work paper that re-ran them would eventually print a different answer from the report
    two sections above it.
    """

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(..., min_length=1)
    question: str = Field(default="", description="What was asked, in the business's words")
    area: str = Field(default="")
    tool: str = Field(default="", description="What established it")
    rows_total: int = Field(default=0, ge=0, description="Rows in the block it ran over")
    rows_checked: int = Field(
        default=0, ge=0, description="Rows every input resolved on; below total is a gap"
    )
    flagged: int = Field(default=0, ge=0, description="Exceptions it raised")
    severity: str = Field(
        default="",
        description=(
            'One of "high", "medium" or "info", read off the weight the business gave the '
            "area. An unscored observation is always info - it moves no score by design."
        ),
    )
    status: Status = Field(
        default=Status.UNABLE_TO_VALIDATE, description="The status step 16 reached. Not re-derived"
    )
    financial_impact: Decimal | None = Field(default=None)
    first_example: str | None = Field(default=None, description="Cell reference, never a name")
    conclusion: str = Field(default="", description="The decisive figure's own line")


class SplitSummary(BaseModel):
    """The whole work paper: the basis it was read on, the split, the rules and the tests.

    Written once with the review and served with it. It holds no cell the report does not
    already stand on, and it is the one artefact that shows a reviewer the arithmetic rather
    than the conclusion.
    """

    model_config = ConfigDict(frozen=True)

    basis: SplitBasis
    categories: tuple[CategoryRef, ...] = Field(default=())
    components: tuple[ComponentSplit, ...] = Field(default=())
    tie_outs: tuple[SplitTieOut, ...] = Field(default=())
    rules: tuple[DerivedRule, ...] = Field(default=())
    walkthroughs: tuple[RowWalkthrough, ...] = Field(default=())
    tests: tuple[ExceptionTest, ...] = Field(default=())
    notes: tuple[str, ...] = Field(
        default=(),
        description=(
            "What the work paper could not do, named rather than left as an empty section. "
            "A missing rules section with no note reads as a workbook with no formulas."
        ),
    )
