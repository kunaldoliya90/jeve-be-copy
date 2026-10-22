"""Judgements and what code does to them: AI-3's verdict, AI-4's challenge, the outcome.

``Status`` carries an explicit strictness order because two rules depend on it: AI-4 may
only move a status to something equal or stricter, and when the two disagree the stricter
one wins. Leaving that to enum declaration order would make it an accident.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    """A check's outcome. ``UNABLE_TO_VALIDATE`` is never a quiet pass."""

    PASS = "pass"  # noqa: S105  # a review status, not a credential
    PARTIAL = "partial"
    UNABLE_TO_VALIDATE = "unable_to_validate"
    MISSING_EVIDENCE = "missing_evidence"
    FAIL = "fail"


_STRICTNESS: dict[Status, int] = {
    Status.PASS: 0,
    Status.PARTIAL: 1,
    Status.UNABLE_TO_VALIDATE: 2,
    Status.MISSING_EVIDENCE: 3,
    Status.FAIL: 4,
}


def strictness(status: Status) -> int:
    """Rank used to compare two statuses. Higher is stricter."""
    return _STRICTNESS[status]


def stricter(first: Status, second: Status) -> Status:
    """The stricter of two statuses, used when the judge and the challenge disagree."""
    return first if strictness(first) >= strictness(second) else second


class ReviewFlag(StrEnum):
    """Why ``answer_check`` or the routing rules sent a check to a person."""

    UNVERIFIED_CITATION = "unverified_citation"
    UNTRACED_NUMBER = "untraced_number"
    UNSUPPORTED_FAIL = "unsupported_fail"
    UNSUPPORTED_PASS = "unsupported_pass"  # noqa: S105
    PASS_CONTRADICTS_FIGURES = "pass_contradicts_figures"  # noqa: S105
    STATUS_OVERRIDDEN = "status_overridden"
    MISSING_VERDICT = "missing_verdict"
    CHALLENGE_MISSING = "challenge_missing"
    CHALLENGE_DISAGREED = "challenge_disagreed"
    """The second opinion read the same figures differently. The stricter side wins."""
    MAP_UNVERIFIED = "map_unverified"
    INCOMPLETE_ROW_COVERAGE = "incomplete_row_coverage"
    """A re-performance figure did not cover every row. Gate rule 1."""
    NEGATIVE_ACCRUAL = "negative_accrual"
    """A negative employee-level amount. Never approved automatically."""


class Finding(BaseModel):
    """One exception or observation, pinned to the figures that evidence it."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["exception", "observation"]
    severity: Literal["high", "medium", "low"]
    text: str = Field(..., min_length=1)
    evidence_ids: tuple[str, ...] = Field(
        default=(), description="Figure ids only. Anything else is an unverified citation"
    )


class Verdict(BaseModel):
    """AI-3's answer for one check. On a figures-decided check the status is not its to set."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(..., min_length=1)
    status: Status
    conclusion: str = Field(default="", max_length=400)
    findings: tuple[Finding, ...] = Field(default=(), max_length=8)


class Challenge(BaseModel):
    """AI-4's second opinion. It can lower a status and never raise one."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(..., min_length=1)
    agrees: bool = Field(default=True)
    status_should_be: Status | None = Field(
        default=None, description="Discarded and logged when looser than the judge's status"
    )
    reason: str = Field(default="")
    evidence_ids: tuple[str, ...] = Field(default=())


class CheckOutcome(BaseModel):
    """What a check finally came to, after the guardrails and the routing rules."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(..., min_length=1)
    area: str = Field(..., min_length=1)
    final_status: Status
    needs_review: bool = Field(default=False)
    review_reasons: tuple[ReviewFlag, ...] = Field(default=())
    verdict: Verdict | None = Field(default=None)
    challenge: Challenge | None = Field(default=None)
