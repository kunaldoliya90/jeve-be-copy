"""The entry recalculation work paper: the booked value regenerated from first principles.

What this answers, and what it refuses to. Every other re-performance in ``review/`` asks
whether one column agrees with another; this one asks the question an auditor asks of the
entry itself - **can the amount that was posted be produced again from its own inputs?** The
recomputed figure is built row by row from the inputs the business declared, in Decimal, by
code alone. The booked figure is read from somewhere else entirely, so the two are
independent by construction and their difference means something.

Four rules shape every model below.

**The formula is the business's, not the code's.** It is declared in the JE type file as an
expression over named inputs, parsed at startup, and evaluated per row. Nothing here assumes
a generic accrual formula, a thirty-day month or a currency, and a JE type that states a
different formula produces a correct work paper with no edit.

**A missing input is never a zero.** A row an input does not resolve on is counted as
incomplete and reported, never recomputed as though the input were nought. That is the one
substitution that turns a data gap into a confident wrong number.

**A discrepancy is a citation.** Not a count and not a percentage: the row, the cell the
workbook states its own figure in, the cells every input was read from, what the arithmetic
produced instead, and a sentence written by code saying why the two differ. A reviewer must
be able to open the file at that cell and see the same thing.

**No row is named.** Citations carry a row number and cell references. Never an employee
reference, never a name - the same rule as the category split work paper beside it.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from review.models.verdicts import Status


class RecalcUnit(StrEnum):
    """What a declared input is measured in. Stated by the business, never inferred."""

    CURRENCY = "currency"
    DAYS = "days"
    MONTHS = "months"
    CURRENCY_PER_DAY = "currency_per_day"
    RATE = "rate"
    """A multiplier or a percentage, as the file holds it."""
    COUNT = "count"


class RoundingMode(StrEnum):
    """How the business rounds. Named because the direction is a decision, not a detail.

    Half-up is what a spreadsheet does and what a preparer expects; half-even is what a
    Decimal does by default, and the two differ by a cent on every amount ending in five.
    Declaring it is what stops a work paper disagreeing with the workbook over rounding.
    """

    HALF_UP = "half_up"
    HALF_EVEN = "half_even"
    DOWN = "down"


class DiscrepancyKind(StrEnum):
    """Why one row is on the work paper.

    The three are acted on differently, which is why they are never collapsed into
    "exception": an amount that differs is a finding, an input that is missing is a data
    gap, and a row the file states nothing for is neither.
    """

    STATED_DIFFERS = "stated_differs"
    """The arithmetic produced a different amount from the one the workbook states."""
    INPUT_MISSING = "input_missing"
    """An input the formula needs did not resolve, so the row could not be recomputed."""
    STATED_MISSING = "stated_missing"
    """The row was recomputed and the workbook states no amount to compare it against."""


class BookedMethod(StrEnum):
    """Where the booked amount comes from. Always a different reading from the recomputation.

    Deliberately two. Summing a column of the *working* was the obvious third, and it is the
    one that must not exist: a profiled block runs past the last record into the preparer's
    own subtotal rows, so it would double-count - and it would take its figure from the same
    table the recomputation walks, which is not an independent reading of anything.
    """

    POSTING_DEBITS = "posting_debits"
    """The debit side of the posted entry, summed from its own lines."""
    STATED_FACT = "stated_fact"
    """A control total the entry states about itself, read at step 13."""


class CitedValue(BaseModel):
    """One number a citation rests on, with where it was read from.

    ``label`` is the preparer's own header text wherever the workbook has one, so a reader
    sees the column as the file names it rather than as the harness names it.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1, description="The input id the formula refers to it by")
    label: str = Field(default="", description="The column header, as the preparer wrote it")
    cell: str = Field(default="", description='Where it was read, e.g. "AB9"')
    value: Decimal | None = Field(default=None, description="None when the cell holds no number")
    unit: RecalcUnit = Field(default=RecalcUnit.CURRENCY)


class RecalcCitation(BaseModel):
    """One row that did not reconcile, in the form a reviewer can act on.

    Everything needed to reopen the workbook and reach the same conclusion: the row, the
    cell the file states its figure in, every input cell behind the recomputation, and the
    reason the two differ - written by code, from the measurement, never by a model.
    """

    model_config = ConfigDict(frozen=True)

    row: int = Field(..., ge=1)
    sheet: str = Field(default="")
    kind: DiscrepancyKind
    recomputed: Decimal | None = Field(
        default=None, description="What the formula produced. None when an input was missing"
    )
    stated: Decimal | None = Field(default=None, description="What the workbook states")
    stated_cell: str = Field(default="", description="Where it states it")
    difference: Decimal | None = Field(
        default=None, description="Recomputed less stated. None when either side is absent"
    )
    difference_pct: float | None = Field(
        default=None, description="Against the stated amount. None when that is zero or absent"
    )
    why: str = Field(
        default="",
        description="Why this row is here, in a sentence. Written by code from the figures",
    )
    inputs: tuple[CitedValue, ...] = Field(
        default=(), description="Every input the row was recomputed from, with its cell"
    )
    missing: tuple[str, ...] = Field(
        default=(), description="Ids of the inputs that did not resolve on this row"
    )


class RecalcInputReading(BaseModel):
    """One declared input, and what this particular workbook carries for it.

    The declaration is the business's (id, unit, source, whether the formula needs it); the
    rest is measured. ``bound`` false on a required input is the whole reason a review comes
    back inconclusive rather than wrong, so it is carried per input rather than summarised.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1)
    meaning: str = Field(default="", description="The column meaning the sheet map binds it to")
    unit: RecalcUnit = Field(default=RecalcUnit.CURRENCY)
    source: str = Field(
        default="", description="The table it is read from, as the JE type names it"
    )
    description: str = Field(default="", description="What it is, in the business's words")
    required: bool = Field(default=True, description="Whether the formula cannot run without it")
    bound: bool = Field(default=False, description="Whether this workbook has such a column")
    column: str = Field(default="", description="Its column letter, when bound")
    header: str = Field(default="", description="Its header text, as the preparer wrote it")
    rows_present: int = Field(default=0, ge=0, description="Records holding a number for it")
    rows_missing: int = Field(default=0, ge=0, description="Records that do not")


class RecalcComponent(BaseModel):
    """One term of the formula, totalled over the records, so the figure comes apart.

    A reader told only the recomputed total has to take it on trust. Each component is the
    same arithmetic stopped one step early.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(..., min_length=1)
    label: str = Field(default="", description="The column header or the declared description")
    expression: str = Field(default="", description="How it is derived, as the JE type states it")
    in_headers: str = Field(
        default="", description="That expression with each input replaced by its header text"
    )
    unit: RecalcUnit = Field(default=RecalcUnit.CURRENCY)
    total: Decimal = Field(default=Decimal(0))
    rows_populated: int = Field(default=0, ge=0)
    number_format: str | None = Field(
        default=None, description="Excel's own format for the column, where there is one"
    )


class RecalcCorroboration(BaseModel):
    """A derived term checked against a column the workbook computes for the same quantity.

    This is the strongest evidence the work paper carries: the formula was re-performed from
    inputs and the file's own answer agrees, row by row. It corroborates and never decides -
    the entry's status comes from the entry-level comparison alone.
    """

    model_config = ConfigDict(frozen=True)

    term: str = Field(..., min_length=1, description="The derived term that was re-performed")
    against: str = Field(..., min_length=1, description="The meaning it was compared with")
    label: str = Field(default="", description="That column's header, as written")
    rows_compared: int = Field(default=0, ge=0)
    rows_agreeing: int = Field(default=0, ge=0)
    impact: Decimal = Field(default=Decimal(0), description="Absolute difference, summed")
    first_example: str = Field(default="", description="Cell reference of the worst row, if any")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def agrees(self) -> bool:
        """Every row compared, and every one of them tied. Nothing less corroborates."""
        return self.rows_compared > 0 and self.rows_agreeing == self.rows_compared


class BookedAmount(BaseModel):
    """The amount that was actually posted, read independently of the recomputation."""

    model_config = ConfigDict(frozen=True)

    method: BookedMethod
    total: Decimal | None = Field(default=None, description="None when it could not be read")
    description: str = Field(default="", description="Where it was read from, in a sentence")
    table: str = Field(default="")
    sheet: str = Field(default="")
    lines: int = Field(default=0, ge=0, description="Lines or rows it was summed over")
    accounts: tuple[str, ...] = Field(
        default=(), description="Accounts the debit side reached, as the file spells them"
    )


class RecalcBasis(BaseModel):
    """What was recalculated, under which rules. The audit trail of the work paper.

    Everything a reviewer needs before any figure on it means anything: the block of the tab
    that was walked, the formula as the business states it and as the preparer's own headers
    read it, and every supporting convention - sign, currency, rounding, day count and the
    tolerance the comparison was made under.
    """

    model_config = ConfigDict(frozen=True)

    table: str = Field(..., min_length=1, description="The name the JE type binds this table as")
    sheet: str = Field(default="")
    header_row: int = Field(default=0, ge=0)
    first_row: int = Field(default=0, ge=0)
    last_row: int = Field(default=0, ge=0)
    record_key: str = Field(
        default="",
        description=(
            "The meaning that tells a record from a spacer or a subtotal row beneath the "
            "data. Without it the preparer's own total row would be counted as a record."
        ),
    )
    stated_column: str = Field(
        default="",
        description=(
            "The column letter holding the workbook's own amount per record, which the "
            "recomputation is compared against row by row. Empty where the file states "
            "none, and then there is nothing to tie a record against - only the entry."
        ),
    )
    stated_header: str = Field(default="", description="That column's header, as written")
    formula: str = Field(..., min_length=1, description="As the JE type declares it")
    formula_in_headers: str = Field(
        default="",
        description=(
            "The same formula with each input replaced by that column's header text. A "
            "substitution, never a translation: it cannot say anything the file does not."
        ),
    )
    sign: str = Field(default="", description="Which side of the entry a positive amount is")
    currency: str = Field(default="", description="As the workbook states it, where it does")
    rounding: str = Field(default="", description='e.g. "2 decimal places, half up"')
    day_count: str = Field(
        default="",
        description="The day-count convention the business approved, named rather than assumed",
    )
    tolerance_pct: Decimal = Field(
        default=Decimal(0), ge=0, description="What the business agreed counts as agreeing"
    )
    tolerance_amount: Decimal = Field(
        default=Decimal(0), ge=0, description="That percentage of the booked amount, in currency"
    )
    row_tolerance: Decimal = Field(
        default=Decimal(0), ge=0, description="Per-row headroom, for the currency's own rounding"
    )
    worked_example: str = Field(
        default="", description="The business's own worked example of this formula"
    )
    provisional_basis: bool = Field(
        default=False,
        description=(
            "True until the business has signed the formula and the tolerance off. The "
            "arithmetic is the same either way; what changes is whether a pass may be "
            "relied on, and a reader is owed that distinction on the face of the paper."
        ),
    )


class EntryRecalculation(BaseModel):
    """The whole work paper: recomputed against booked, and every row that did not reconcile.

    Written with the record and served with it. It decides nothing on its own - the check's
    status comes from the figures this produces, through the same routing rules as every
    other check - but it is the one artefact that shows a reviewer the entry being rebuilt.
    """

    model_config = ConfigDict(frozen=True)

    basis: RecalcBasis
    status: Status = Field(
        default=Status.UNABLE_TO_VALIDATE,
        description=(
            "Pass within tolerance on complete coverage; fail outside it; partial where the "
            "arithmetic agreed but not every record could be recomputed; unable to validate "
            "where a required input or the booked amount is missing. Never a pass by default."
        ),
    )
    explanation: str = Field(
        default="", description="The outcome in a sentence, written by code from the figures"
    )

    recomputed_total: Decimal = Field(default=Decimal(0), description="The formula, summed")
    booked: BookedAmount
    difference: Decimal | None = Field(
        default=None, description="Recomputed less booked. None when the booked side is absent"
    )
    difference_pct: float | None = Field(default=None, description="Against the booked amount")
    within_tolerance: bool | None = Field(
        default=None, description="None when there was nothing to compare, which is not a pass"
    )

    rows_total: int = Field(default=0, ge=0, description="Rows in the block that are records")
    rows_recomputed: int = Field(default=0, ge=0, description="Records every input resolved on")
    rows_incomplete: int = Field(default=0, ge=0, description="Records an input was missing on")
    rows_agreeing: int = Field(
        default=0, ge=0, description="Recomputed records agreeing with the amount the file states"
    )
    row_impact: Decimal = Field(
        default=Decimal(0), description="Absolute row-level difference, summed over the records"
    )

    inputs: tuple[RecalcInputReading, ...] = Field(default=())
    components: tuple[RecalcComponent, ...] = Field(default=())
    corroborations: tuple[RecalcCorroboration, ...] = Field(default=())
    citations: tuple[RecalcCitation, ...] = Field(
        default=(),
        description="Worst first, capped for reading. ``citation_count`` is the full number",
    )
    citation_count: int = Field(default=0, ge=0, description="Rows that did not reconcile, in full")
    notes: tuple[str, ...] = Field(
        default=(),
        description=(
            "What the recalculation could not do, named rather than left as a blank section. "
            "An input the workbook computes elsewhere is a limit of the evidence, not a pass."
        ),
    )
    duration_ms: int = Field(default=0, ge=0, description="How long the pass over the rows took")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        """Every record recomputed. A partial recalculation can never support a pass."""
        return self.rows_total > 0 and self.rows_recomputed == self.rows_total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ties(self) -> bool:
        """The entry regenerated to within tolerance, over every record."""
        return self.within_tolerance is True and self.complete
