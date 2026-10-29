"""What is scored, rendered and stored: the score, the report and the audit trail."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from api.consts import RiskRating
from review.models.evidence import EvidenceItem, PIIFinding
from review.models.split_summary import SplitSummary
from review.models.verdicts import CheckOutcome, Status


class ScoringMethod(StrEnum):
    """How an area turns its checks into a score.

    ``WEAKEST_CHECK`` is right where one failure should fail the area, and wrong at employee
    level: three bad rows in forty thousand would score the area zero. The last two exist
    for that case.
    """

    WEAKEST_CHECK = "weakest_check"
    BASELINE_SHARE = "baseline_share"
    EXCEPTION_RATE = "exception_rate"
    """Scored on the share of rows excepted, not on the worst row."""
    IMPACT_SHARE = "impact_share"
    """Scored on the share of the entry's value the exceptions move."""


class AreaScore(BaseModel):
    """One review area's contribution, with the reason a reader can act on."""

    model_config = ConfigDict(frozen=True)

    area: str = Field(..., min_length=1)
    weight: int = Field(..., ge=0, le=100)
    method: ScoringMethod
    score: float = Field(..., ge=0.0, le=100.0)
    reason: str = Field(default="", description="Usually the weakest check's conclusion")


class Score(BaseModel):
    """Step 17. Pure Python over the final statuses: same statuses, same score, always."""

    model_config = ConfigDict(frozen=True)

    total: float = Field(..., ge=0.0, le=100.0)
    band: str = Field(..., min_length=1)
    risk_rating: RiskRating | None = Field(
        default=None,
        description=(
            "The band's risk rating: the same fact as the band, in one word. Scoring always "
            "sets it. It is optional only because a record written before the field existed "
            "cannot be given one - records are write-once, so there is nothing to migrate "
            "and a required field here would make every review stored before this change "
            "unreadable rather than merely incomplete."
        ),
    )
    areas: tuple[AreaScore, ...] = Field(default=())
    provisional: bool = Field(
        default=False, description="True when any check is still waiting for a person"
    )


class ReportPayload(BaseModel):
    """What the UI renders. Prose is AI-5's; every number in it is traced at step 19."""

    model_config = ConfigDict(frozen=True)

    headline: str = Field(default="")
    summary: str = Field(default="")
    next_steps: tuple[str, ...] = Field(default=())
    untraced_numbers: tuple[str, ...] = Field(
        default=(), description="Listed rather than silently removed"
    )


class EntryIdentity(BaseModel):
    """Which journal entry this review is of. Every field here the system established.

    Nothing on this model is the uploader's word any more. ``document_number`` and
    ``risk_rating`` were once typed on the upload form, which in practice meant they were
    blank on nearly every review; the number now comes off the file name the workbook was
    uploaded under, and the rating off the band the score reached.

    One consequence worth stating: the rating is the score, banded. It corroborates
    nothing, and a reader who reads it as a second opinion on the score has been misled.
    """

    model_config = ConfigDict(frozen=True)

    document_number: str | None = Field(
        default=None, description="The file name the workbook was uploaded under, less its suffix"
    )
    risk_rating: RiskRating | None = Field(
        default=None, description="From the band the score reached. None until step 17 has run"
    )
    je_type: str = Field(default="unknown", description="Recognised type id")
    je_type_name: str = Field(default="", description="Its business name")
    je_type_variant: str | None = Field(default=None, description="Which model of it")
    process: str = Field(default="", description="From the JE type file, e.g. Record to Report")
    sub_process: str = Field(default="")
    period: str | None = Field(
        default=None, description="Fiscal period read off the posting table by code"
    )


class AICallRecord(BaseModel):
    """One model call, metered. Feeds both the audit trail and the cost alert."""

    model_config = ConfigDict(frozen=True)

    call: str = Field(..., min_length=1, description='"AI-1" ... "AI-5", "AI-R", "AI-M", "AI-X"')
    step: str = Field(
        default="",
        description=(
            'The numbered step that made the call, e.g. "13a". Recorded at the call site '
            "rather than read off the run, because the conditional readers fire between two "
            "steps and would otherwise be charged to whichever one happened to be current."
        ),
    )
    name: str = Field(default="", description="What that step was doing, in words")
    model: str = Field(default="", description="Model or deployment that served the call")
    input_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    latency_ms: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    schema_valid: bool = Field(default=True)


class ToolRunRecord(BaseModel):
    """One tool execution, for diagnosing a review months later."""

    model_config = ConfigDict(frozen=True)

    figure_id: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    duration_ms: int = Field(default=0, ge=0)
    ok: bool = Field(default=True)


class AuditTrail(BaseModel):
    """Everything needed to explain, re-verify or reproduce a review.

    ``versions`` is what makes a result replayable: the JE type, rules and prompt hashes and
    the model id all key the reuse cache, so a result is never served across a rule change.
    """

    model_config = ConfigDict(frozen=True)

    versions: dict[str, str] = Field(default_factory=dict)
    file_sha256: str = Field(..., min_length=64, max_length=64)
    file_name: str = Field(default="")
    size_bytes: int = Field(default=0, ge=0)
    layout_fingerprint: str = Field(default="")
    reuse_hit: bool = Field(default=False)
    layout_hit: bool = Field(default=False)
    ai_calls: tuple[AICallRecord, ...] = Field(default=())
    tool_runs: tuple[ToolRunRecord, ...] = Field(default=())
    flags: tuple[str, ...] = Field(default=())
    total_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    total_tokens: int = Field(default=0, ge=0)
    je_type_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="What step 9 left the identification at"
    )
    je_type_corroborated: bool = Field(
        default=False,
        description="True when a second reading agreed the type, or when none was available "
        "to disagree. False means the two readings differed and a person is looking.",
    )
    je_type_source: str = Field(
        default="unknown",
        description="Where the type came from: 'shape' when the workbook's own columns "
        "settled it, 'declared' when they did not and the uploader's choice was used "
        "instead, 'unknown' when neither and no check ran. A JETypeSource value.",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="How long the review took. Kept with the record because a re-read "
        "otherwise reports every stored review as having taken no time at all.",
    )


class ReviewResult(BaseModel):
    """Step 20. Written once and never updated."""

    model_config = ConfigDict(frozen=True)

    review_id: str = Field(..., min_length=1)
    status: Literal["completed", "needs_review", "failed"]
    je_type: str = Field(default="unknown")
    entry: EntryIdentity | None = Field(
        default=None, description="None on a review that never got as far as recognising a type"
    )
    score: Score | None = Field(default=None, description="None only when the review failed")
    checks: tuple[CheckOutcome, ...] = Field(default=())
    pii_flagged: bool = Field(default=False)
    pii_findings: tuple[PIIFinding, ...] = Field(default=())
    evidence_register: tuple[EvidenceItem, ...] = Field(default=())
    split_summary: SplitSummary | None = Field(
        default=None,
        description=(
            "The category split work paper: the working re-added from its own rows. None "
            "when the workbook bound no table whose rows could be re-added, and None on "
            "every record written before the work paper existed - records are write-once, "
            "so a required field here would make those unreadable rather than incomplete."
        ),
    )
    report: ReportPayload | None = Field(default=None)
    audit: AuditTrail
    created_at: datetime
    reused_from: str | None = Field(
        default=None, description="Set when served from the reuse index at zero AI cost"
    )

    @property
    def needs_person(self) -> tuple[CheckOutcome, ...]:
        """Checks a reviewer has to look at, in the order the report lists them."""
        return tuple(check for check in self.checks if check.needs_review)

    @property
    def failed_checks(self) -> tuple[CheckOutcome, ...]:
        return tuple(check for check in self.checks if check.final_status is Status.FAIL)
