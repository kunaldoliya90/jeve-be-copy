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
from review.models.result import AICallRecord, ReviewResult
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
                        [outcome for outcome in result.checks if outcome.area == area.area]
                    ),
                    reason=area.reason,
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


def area_status(outcomes: list[CheckOutcome]) -> Status:
    """An area is no better than its worst check.

    Separate from the weighted score on purpose: an exception-rate area with three bad rows
    in forty thousand scores in the nineties and still contains a failure, and a card badged
    from the score alone would read as a pass.
    """
    if not outcomes:
        return Status.UNABLE_TO_VALIDATE
    worst = outcomes[0].final_status
    for outcome in outcomes[1:]:
        worst = stricter(worst, outcome.final_status)
    return worst


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
