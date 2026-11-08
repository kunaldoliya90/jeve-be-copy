"""The HTTP contract for reviews: what the browser sends, and what it gets back.

Kept deliberately separate from the harness's own models in ``review/models/``. Those change
as the pipeline grows; this is what a frontend codes against, so it changes only when the
contract genuinely does. Field names are ``snake_case`` end to end — one spelling everywhere
is one fewer thing to get wrong between two codebases.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.consts import ErrorCode, JETypeSource, SSEEvent
from review.models.context import OBSERVATION_AREA, JETypeContext
from review.models.evidence import EvidenceItem
from review.models.recalculation import EntryRecalculation
from review.models.result import AICallRecord, ReviewResult, ScoringMethod
from review.models.split_summary import SplitSummary
from review.models.verdicts import CheckOutcome, ReviewFlag, Status, stricter

ReviewState = Literal["queued", "running", "completed", "needs_review", "failed", "rejected"]
"""What a review is doing. ``needs_review`` is a *completed* review whose score is
provisional because something is waiting for a person — not an error."""


class ErrorResponse(BaseModel):
    """Every 4xx and 5xx body. One shape, so the client has one error path."""

    error_code: ErrorCode
    message: str
    review_id: str | None = Field(default=None)
    details: dict[str, str] = Field(default_factory=dict)


class ReviewAccepted(BaseModel):
    """202 from ``POST /reviews``. The work runs in the background."""

    review_id: str
    status: ReviewState = Field(default="queued")
    events_url: str = Field(..., description="Subscribe here for progress; it closes at the end")
    reused_from: str | None = Field(
        default=None, description="Set when an identical file under identical rules was found"
    )


class ProgressEvent(BaseModel):
    """One Server-Sent Event.

    Says *where the system looked*, never what a cell held: these cross the network to a
    browser, and the workbook's contents do not.
    """

    event: SSEEvent
    review_id: str
    step: str = Field(default="", description='Step number as text, e.g. "13a"')
    name: str = Field(default="")
    ts: datetime
    progress_pct: int = Field(
        default=0,
        ge=0,
        le=100,
        description=(
            "How far the review has got, as of this event. Carried here as well as on the "
            "review detail so a client watching the stream does not have to poll the whole "
            "detail to move a progress bar, and cannot show a percentage that disagrees with "
            "the step it is displaying beside it."
        ),
    )
    detail: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class FigureSampleView(BaseModel):
    """One worked example behind a check's conclusion: a row the check actually looked at.

    Reads whichever of these a tool's sample dict carries - a row-level re-performance has
    all five, a tool comparing something else (an account, a posting date) has only some -
    so a field absent from the tool's own sample is ``None`` here rather than forcing a
    shape onto every tool. Never an employee identifier, same as the figure it comes from.
    """

    row: int | None = Field(default=None, description="Row number, never a name")
    cell: str | None = Field(default=None, description='Cell ref, e.g. "GPAY!AH9"')
    expected: Decimal | None = Field(
        default=None, description="What re-performing the arithmetic gives"
    )
    actual: Decimal | None = Field(default=None, description="What the workbook states")
    difference: Decimal | None = Field(default=None, description="expected minus actual")


class CheckView(BaseModel):
    """One check as the report shows it."""

    check_id: str
    area: str
    question: str = Field(default="")
    status: Status
    decided_by: str = Field(default="", description='"figures" or "judgement"')
    needs_review: bool = Field(default=False)
    review_reasons: list[ReviewFlag] = Field(default_factory=list)
    conclusion: str = Field(default="")
    exception_count: int = Field(default=0, ge=0)
    financial_impact: Decimal | None = Field(default=None)
    cells: list[str] = Field(default_factory=list, description='Cell refs, e.g. "GPAY!AL35"')
    samples: list[FigureSampleView] = Field(
        default_factory=list,
        description="Worst-first, the rows behind the conclusion - same ones cells is made from",
    )


class AreaView(BaseModel):
    """One scored review area, as a tested parameter."""

    area: str
    weight: int
    method: str
    score: float
    status: Status = Field(
        default=Status.UNABLE_TO_VALIDATE,
        description=(
            "The strictest status among the area's checks. An area is no better than its "
            "worst check, whatever the weighted arithmetic makes of it."
        ),
    )
    reason: str = Field(default="")
    checks: list[CheckView] = Field(default_factory=list)


class EvidenceView(BaseModel):
    """One row of the evidence register: what was expected, and what the workbook offered."""

    item: str
    expected: str = Field(default="", description="What the layer is, in the business's words")
    required: bool = Field(
        default=False, description="Required layers are scored; recommended ones are reported"
    )
    status: str
    location: str | None = Field(default=None)
    remediation: str | None = Field(default=None)
    note: str = Field(default="", description="Why it reads as it does, written by code")

    @classmethod
    def of(cls, item: EvidenceItem) -> "EvidenceView":
        return cls(
            item=item.item_id,
            expected=item.expected,
            required=item.required,
            status=item.status.value,
            location=item.location,
            remediation=item.remediation,
            note=item.note,
        )


class SheetView(BaseModel):
    """One tab, as the profiler measured it. Shape only — no cell ever leaves the server."""

    name: str
    hidden: bool = Field(default=False)
    row_count: int = Field(default=0)
    column_count: int = Field(default=0)
    formula_count: int | None = Field(
        default=None, description="None when the format carries no formulas, which is not zero"
    )
    tables: int = Field(default=0)
    headers: list[str] = Field(default_factory=list, description="First few column labels")


class WorkbookView(BaseModel):
    """What the file turned out to be. Shape only, never values."""

    file_name: str
    size_bytes: int
    workbook_format: str
    sheet_count: int = Field(default=0)
    table_count: int = Field(default=0)
    row_count: int = Field(default=0, description="Rows on the calculation table")
    formulas_available: bool = Field(default=True)
    layout_fingerprint: str = Field(default="")
    defect_summary: list[str] = Field(default_factory=list)
    sheets: list[SheetView] = Field(default_factory=list)
    lineage: list[str] = Field(
        default_factory=list, description='Which sheet feeds which, e.g. "JE Upload!F <- ADP GL"'
    )


class AICallCostView(BaseModel):
    """One model call, metered and charged to the numbered step that made it."""

    call: str = Field(..., description='"AI-1" ... "AI-5", "AI-R", "AI-M", "AI-X"')
    step: str = Field(default="", description='The step it was made from, e.g. "13a"')
    name: str = Field(default="", description="What that step was doing, in words")
    model: str = Field(default="", description="Model or deployment that served it")
    input_tokens: int = Field(default=0, ge=0, description="Cached input included")
    cached_tokens: int = Field(default=0, ge=0, description="A subset of the input tokens")
    output_tokens: int = Field(default=0, ge=0, description="Reasoning included")
    reasoning_tokens: int = Field(default=0, ge=0, description="A subset of the output tokens")
    total_tokens: int = Field(
        default=0,
        ge=0,
        description=(
            "Input plus output. Carried rather than left to the reader to add up, because "
            "cached and reasoning tokens are subsets and summing all four double-counts."
        ),
    )
    cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    latency_ms: int = Field(default=0, ge=0)
    schema_valid: bool = Field(default=True, description="False when the answer stopped early")

    @classmethod
    def of(cls, record: AICallRecord) -> "AICallCostView":
        return cls(
            call=record.call,
            step=record.step,
            name=record.name,
            model=record.model,
            input_tokens=record.input_tokens,
            cached_tokens=record.cached_tokens,
            output_tokens=record.output_tokens,
            reasoning_tokens=record.reasoning_tokens,
            total_tokens=record.input_tokens + record.output_tokens,
            cost_usd=record.cost_usd,
            latency_ms=record.latency_ms,
            schema_valid=record.schema_valid,
        )


class CostView(BaseModel):
    """What the review spent. Zero on a fully deterministic run, and that is the point."""

    ai_calls: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_usd: Decimal = Field(default=Decimal(0), ge=0)
    duration_ms: int = Field(default=0, ge=0)
    calls: list[AICallCostView] = Field(
        default_factory=list,
        description=(
            "Every model call in the order it was made, each charged to its step. A single "
            "total tells a reader what a review cost; this tells them what to change."
        ),
    )


class ReviewSummary(BaseModel):
    """One row of ``GET /reviews``."""

    review_id: str
    file_name: str
    document_number: str | None = Field(default=None)
    je_type: str
    status: ReviewState
    score: float | None = Field(default=None)
    band: str | None = Field(default=None)
    provisional: bool = Field(default=False)
    created_at: datetime


class ReviewDetail(BaseModel):
    """``GET /reviews/{id}``: the whole result, or how far a running one has got."""

    model_config = ConfigDict(ser_json_timedelta="float")

    review_id: str
    status: ReviewState
    file_name: str = Field(default="")
    created_at: datetime
    reused_from: str | None = Field(
        default=None,
        description=(
            "Set when this upload matched a file already reviewed under identical rules. "
            "Everything below is that review's result, served under this upload's id."
        ),
    )

    # --- which entry this is. All of it established by the system ---
    document_number: str | None = Field(
        default=None, description="The name the workbook was uploaded under, less its suffix"
    )
    risk_rating: str | None = Field(
        default=None,
        description=(
            "From the band this review's score reached, so it is absent until the review "
            "has been scored. The same fact as `band`, in one word - not a second opinion "
            "on it"
        ),
    )
    je_type: str = Field(default="unknown")
    je_type_name: str = Field(default="", description="The business name of the recognised type")
    je_type_variant: str | None = Field(default=None)
    je_type_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="What step 9 left the identification at, after checking it against the file",
    )
    je_type_corroborated: bool = Field(
        default=False,
        description=(
            "True when a second, independent reading agreed the type, or when none was "
            "available to disagree. False means the two readings differed and a person is "
            "looking - the score is unaffected either way."
        ),
    )
    je_type_source: JETypeSource = Field(
        default=JETypeSource.UNKNOWN,
        description=(
            "Where the type came from. 'shape': the workbook's own columns settled it. "
            "'declared': they did not, and the checks ran against what the uploader named "
            "- every result beneath it is unconfirmed and the review stays with a person. "
            "'unknown': neither, and no check ran."
        ),
    )
    process: str = Field(default="", description="From the JE type file, e.g. Record to Report")
    sub_process: str = Field(default="")
    period: str | None = Field(
        default=None, description="Fiscal period read off the posting table by code"
    )

    step: str = Field(default="", description="Where a running review has got to")
    progress_pct: int = Field(default=0, ge=0, le=100)

    score: float | None = Field(default=None)
    band: str | None = Field(default=None)
    provisional: bool = Field(
        default=False, description="True when a check is still waiting for a person"
    )
    headline: str = Field(default="")
    summary: str = Field(default="")

    workbook: WorkbookView | None = Field(default=None)
    areas: list[AreaView] = Field(default_factory=list)
    observations: list[CheckView] = Field(
        default_factory=list,
        description=(
            "Checks reported to the reviewer and deliberately not scored, such as PII and "
            "trend for this entry type. Shown as parameters, badged as carrying no weight."
        ),
    )
    evidence: list[EvidenceView] = Field(default_factory=list)
    split_summary: SplitSummary | None = Field(
        default=None,
        description=(
            "The category split work paper: the entry's value re-added from the rows of the "
            "working and divided by the classes the workbook itself sorts them into, with "
            "the control totals it states above its own data, the live formulas behind each "
            "column, a row traced step by step, and what each test found. None when the "
            "workbook bound no table whose rows could be re-added. "
            "Carried as the harness model rather than re-declared as a view. Every field on "
            "it is already a reader's field - headers the preparer wrote, column letters, "
            "amounts, row numbers - and a parallel set of nine identical classes here would "
            "be the mapping layer that hides drift rather than prevents it."
        ),
    )
    recalculation: EntryRecalculation | None = Field(
        default=None,
        description=(
            "The entry recalculation work paper: the posted amount regenerated record by "
            "record from the formula the business approved, set against what was actually "
            "booked, with a citation for every record that did not reconcile. None where "
            "the entry type states no formula, where the working could not be bound, and "
            "on a record stored before the work paper existed. "
            "Carried as the harness model for the same reason as `split_summary`: every "
            "field on it is already a reader's field, and a parallel set of view classes "
            "here would be a mapping layer that hides drift rather than preventing it."
        ),
    )
    needs_person: list[CheckView] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    untraced_numbers: list[str] = Field(
        default_factory=list,
        description=(
            "Numbers in the written summary that trace to no figure and no score. Listed "
            "rather than removed: a reader must know which figure to distrust."
        ),
    )
    cost: CostView = Field(default_factory=CostView)

    not_yet_run: list[str] = Field(
        default_factory=list,
        description=(
            "Phases this build does not perform, named rather than silently skipped. A "
            "reader must never mistake an absent judgement for a passed one."
        ),
    )

    @classmethod
    def from_result(
        cls, result: ReviewResult, *, je_spec: JETypeContext | None = None, **extra: Any
    ) -> "ReviewDetail":
        """Project a stored result onto the wire shape.

        Thinner than the live view by necessity: the figures a check cited are working
        memory and are not kept, so a re-read carries each check's status without its
        conclusion. ``je_spec`` restores the questions and the decision basis, which are
        versioned context rather than run state and so can be looked up again safely.
        """
        score = result.score
        report = result.report
        entry = result.entry
        return cls(
            review_id=result.review_id,
            status=result.status,
            file_name=result.audit.file_name,
            created_at=result.created_at,
            document_number=entry.document_number if entry else None,
            risk_rating=entry.risk_rating if entry else None,
            je_type=result.je_type,
            je_type_name=entry.je_type_name if entry else "",
            je_type_variant=entry.je_type_variant if entry else None,
            je_type_confidence=result.audit.je_type_confidence,
            je_type_corroborated=result.audit.je_type_corroborated,
            je_type_source=_source_of(result),
            process=entry.process if entry else "",
            sub_process=entry.sub_process if entry else "",
            period=entry.period if entry else None,
            step="20",
            progress_pct=100,
            score=score.total if score else None,
            band=score.band if score else None,
            provisional=score.provisional if score else False,
            headline=report.headline if report else "",
            summary=report.summary if report else "",
            areas=[
                AreaView(
                    area=area.area,
                    weight=area.weight,
                    method=area.method.value,
                    score=area.score,
                    status=area_status(
                        [outcome for outcome in result.checks if outcome.area == area.area],
                        method=area.method,
                        recalculation=result.recalculation,
                    ),
                    reason=plain_area_reason(area.area, area.reason, result.recalculation),
                    checks=[
                        stored_check(outcome, je_spec)
                        for outcome in result.checks
                        if outcome.area == area.area
                    ],
                )
                for area in (score.areas if score else ())
            ],
            observations=[
                stored_check(outcome, je_spec)
                for outcome in result.checks
                if outcome.area == OBSERVATION_AREA
            ],
            evidence=[EvidenceView.of(item) for item in result.evidence_register],
            # Persisted with the record, so a re-read carries the whole work paper rather
            # than the thinner projection the checks come back as. It holds no figure: it is
            # its own arithmetic over the rows, plus the statuses step 16 reached.
            split_summary=result.split_summary,
            # Stored with the record, like the split. The figures behind the check are
            # working memory and are gone; this paper is the arithmetic itself, so a
            # re-read shows a reviewer exactly what the run showed them.
            recalculation=result.recalculation,
            needs_person=[stored_check(outcome, je_spec) for outcome in result.needs_person],
            cost=CostView(
                ai_calls=len(result.audit.ai_calls),
                total_tokens=result.audit.total_tokens,
                total_usd=result.audit.total_cost_usd,
                duration_ms=result.audit.duration_ms,
                calls=[AICallCostView.of(record) for record in result.audit.ai_calls],
            ),
            next_steps=list(report.next_steps) if report else [],
            untraced_numbers=list(report.untraced_numbers) if report else [],
            **extra,
        )


def _source_of(result: ReviewResult) -> JETypeSource:
    """Where a stored review's entry type came from.

    Records written before the field existed carry nothing, and for those the answer is
    not a guess: the declared fallback did not exist yet, so a record with a type is a
    record whose type the shape settled.
    """
    source = JETypeSource(result.audit.je_type_source)
    if source is JETypeSource.UNKNOWN and result.je_type != "unknown":
        return JETypeSource.SHAPE
    return source


def area_status(
    outcomes: list[CheckOutcome],
    *,
    method: ScoringMethod | str | None = None,
    recalculation: EntryRecalculation | None = None,
) -> Status:
    """An area is no better than its worst check - unless its checks are not what it is
    scored on.

    The default is the strictest check, and it is separate from the weighted score on
    purpose: an exception-rate area with three bad rows in forty thousand scores in the
    nineties and still contains a failure, and a card badged from the score alone would
    read as a pass.

    An area scored ``entry_tolerance`` is the exception, because its number does not come
    from its checks either. It is badged from the same work paper that scores it, on the
    one question that paper answers: did the entry rebuild to what was posted, inside the
    stated tolerance. Inside it passes, outside it fails. Badging that area off its
    strictest check instead is what put a red "Fail" on a card reading 100/100 - two true
    answers to two different questions, printed as though they were one.

    Deliberately the tolerance answer rather than the paper's own ``status``, which is
    ``partial`` wherever a record could not be read. The badge now says what the score
    says, and the records that could not be rebuilt are named in the card's own sentence
    instead of turning a full score amber.

    Where the work paper could not answer - ``within_tolerance`` is ``None``, so there was
    nothing to compare - the checks decide the badge, exactly as they decide the score.
    """
    if (
        method == ScoringMethod.ENTRY_TOLERANCE
        and recalculation is not None
        and recalculation.within_tolerance is not None
    ):
        return Status.PASS if recalculation.within_tolerance else Status.FAIL

    if not outcomes:
        return Status.UNABLE_TO_VALIDATE
    worst = outcomes[0].final_status
    for outcome in outcomes[1:]:
        worst = stricter(worst, outcome.final_status)
    return worst


CALCULATION_AREA = "calculation_accuracy"

_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def _count(value: int) -> str:
    """Small counts read as words in a sentence; larger ones stay as figures."""
    return _WORDS[value] if 0 <= value < len(_WORDS) else f"{value:,}"


def plain_area_reason(area: str, reason: str, recalculation: EntryRecalculation | None) -> str:
    """An area's reason in the words a business reader uses, where the facts allow one.

    The engine writes each area's reason from the decisive figure, in the vocabulary the
    figure was produced in - "2,273 employee rows carry only 1 distinct reference(s), so
    the column is redacted or constant". Every word of that is true and a reviewer chasing
    the finding needs it, but the card it lands on is read in two seconds by somebody
    deciding whether to look further.

    So for the one area where the work paper states the finding as a figure anyone can
    read - the entry rebuilt against what was posted - the reason is written from the work
    paper instead. Derived on the way out rather than stored, so a review recorded before
    this existed reads the same way as one recorded after it.

    Everything else keeps the engine's sentence: a plain wording invented for an area
    whose facts this function does not hold would be a guess, and a guess on an audit
    report is worse than a sentence the reader has to work at.

    Note for anyone changing the wording: the frontend writes the same sentence from the
    same fields (``lib/parameters.ts``) for reviews served by an older build. The two are
    meant to agree.
    """
    if area != CALCULATION_AREA or recalculation is None:
        return reason
    return _calculation_reason(recalculation) or reason


def _difference(value: Decimal) -> str:
    """A difference in whole units, unless whole units would round it away.

    A rebuild that is four hundredths out is not "0 lower than the booked amount" - that
    sentence reads as a tool that cannot subtract. Dust keeps the decimals that make it
    legible as dust.
    """
    figure = abs(value)
    return f"{figure:,.0f}" if figure >= 1 else f"{figure:,.2f}"


def _calculation_reason(paper: EntryRecalculation) -> str:
    """The rebuild against what was posted, in two short sentences.

    Only ever the total difference and how many records carry it. Where several records
    are out, the count is the finding and the per-record figures are not repeated here -
    they are the citations on the work paper, which is where a reviewer goes next.

    A difference inside the stated tolerance says so on the same line as the figure. It is
    the same answer the area is now scored on, and a card reading "47,075 higher" beside a
    score of 100 with nothing joining the two is a card that looks wrong.
    """
    if paper.difference is None:
        return ""

    sentences: list[str] = []
    figure = _difference(paper.difference)
    inside = paper.within_tolerance is True

    if Decimal(figure.replace(",", "")) == 0:
        # Only where the figure itself is nothing. A 47,075 that the tolerance allowed is
        # a tie, but "matches" is not what it says, and this card is read on its own.
        sentences.append("Our total matches the booked amount.")
    else:
        direction = "lower" if paper.difference < 0 else "higher"
        tolerance = ", inside the tolerance allowed" if inside else ""
        sentences.append(f"Our total is {figure} {direction} than the booked amount{tolerance}.")

        out = paper.rows_recomputed - paper.rows_agreeing
        if out == 1:
            sentences.append("The cause is one employee row.")
        elif out > 1:
            sentences.append(f"The cause is {_count(out)} employee rows.")

    # A rebuild that could not read every record is not a complete account of the
    # difference, and the card must not read as though it were.
    if paper.rows_incomplete > 0:
        rows = "row" if paper.rows_incomplete == 1 else "rows"
        sentences.append(
            f"{_count(paper.rows_incomplete).capitalize()} {rows} could not be rebuilt."
        )

    return " ".join(sentences)


def stored_check(outcome: CheckOutcome, je_spec: JETypeContext | None) -> CheckView:
    """One check as a re-read of the stored record can report it.

    The conclusion and the cells it cited came from figures, which are working memory and
    are not kept. They come back empty rather than reconstructed: a plausible sentence
    nobody wrote at the time is worse than a blank.
    """
    spec = je_spec.check(outcome.check_id) if je_spec else None
    return CheckView(
        check_id=outcome.check_id,
        area=outcome.area,
        question=spec.question if spec else "",
        status=outcome.final_status,
        decided_by=spec.decided_by.value if spec else "",
        needs_review=outcome.needs_review,
        review_reasons=list(outcome.review_reasons),
    )


class CheckSpecView(BaseModel):
    """One check a JE type will run, before any review has run it."""

    check_id: str
    area: str
    question: str
    decided_by: str = Field(..., description='"figures" needs no model; "judgement" does')
    risk: str = Field(default="")
    tool: str | None = Field(default=None)


class AreaSpecView(BaseModel):
    area: str
    weight: int
    method: str
    checks: list[CheckSpecView] = Field(default_factory=list)


class JETypeView(BaseModel):
    """What a journal entry of this type is checked on. The review plan, before the review."""

    id: str
    name: str
    version: int
    process: str = Field(default="", description="e.g. Record to Report")
    sub_process: str = Field(default="", description="e.g. Payroll")
    objective: str
    aliases: list[str] = Field(default_factory=list)
    areas: list[AreaSpecView] = Field(default_factory=list)
    observations: list[CheckSpecView] = Field(
        default_factory=list, description="Reported to the reviewer, never scored"
    )
    evidence: list[EvidenceView] = Field(default_factory=list)
    check_count: int = Field(default=0)
    figures_decided: int = Field(
        default=0, description="Checks settled by arithmetic alone, with no model involved"
    )
