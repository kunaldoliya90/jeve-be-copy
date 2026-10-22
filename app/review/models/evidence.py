"""Tool results and the evidence they form: figures, row coverage, pictures and PII.

A ``Figure`` is the only way a number reaches a verdict. Every number a model is allowed to
quote appears in ``numbers``, and ``answer_check`` rejects any other.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from review.models.workbook import CellRef


class EvidenceStatus(StrEnum):
    """How well an expected evidence item is actually supported."""

    AVAILABLE = "available"
    UNCORROBORATED = "uncorroborated"
    """Present, but nothing in it ties to a workbook figure. Half credit."""
    UNVERIFIED = "unverified"
    """Something is there and a person must confirm it. No credit."""
    MISSING = "missing"


class PIIKind(StrEnum):
    """Personal data the scanner looks for, by pattern, in every text cell."""

    EMAIL = "email"
    IBAN = "iban"
    PHONE = "phone"
    SSN = "ssn"
    NINO = "nino"
    PAN = "pan"
    NAME = "name"


class RowCoverage(BaseModel):
    """How much of a table a figure actually covered.

    Gate rule 1: a figure may support a pass only when it is ``complete``. An agreeing total
    over partly-bound rows is the failure this exists to catch, because a neighbouring column
    can hit the same total while every row beneath it is wrong.
    """

    model_config = ConfigDict(frozen=True)

    rows_total: int = Field(default=0, ge=0, description="Data rows in the table")
    rows_bound: int = Field(default=0, ge=0, description="Rows every required input resolved on")
    rows_tied: int = Field(default=0, ge=0, description="Bound rows agreeing within tolerance")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        """``rows_tied == rows_bound == rows_total`` and at least one row. Nothing less."""
        return self.rows_total > 0 and self.rows_tied == self.rows_bound == self.rows_total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tie_rate(self) -> float:
        """The gradient. It guides a mapping search; it never decides a pass."""
        return self.rows_tied / self.rows_total if self.rows_total else 0.0


class Figure(BaseModel):
    """One tool result, addressed by id and quotable only through ``numbers``."""

    model_config = ConfigDict(frozen=True)

    figure_id: str = Field(
        ..., pattern=r"^F-\d{2,3}$", description='Assigned in plan order: "F-07"'
    )
    check_id: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = Field(
        default=None, description="None means the tool could not compute it, which is not a fail"
    )
    decisive: bool = Field(
        default=False, description="True when this figure alone sets the check's status"
    )
    exception_count: int = Field(default=0, ge=0)
    financial_impact: Decimal | None = Field(
        default=None,
        description="Absolute value the exceptions move. Ranks findings; None when not monetary",
    )
    coverage: RowCoverage | None = Field(
        default=None, description="Set by row-level tools; required for a pass on those checks"
    )
    samples: tuple[dict[str, Any], ...] = Field(
        default=(),
        description="Worst-first exemplars with cell references. Never employee identifiers",
    )
    summary: str = Field(default="", description="One line, written by Python")
    numbers: tuple[Decimal, ...] = Field(
        default=(), description="Every number a verdict citing this figure may quote"
    )
    error: str | None = Field(
        default=None, description="Error class when the tool raised; it never raises upward"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def supports_pass(self) -> bool:
        """Whether this figure may back a ``pass``.

        A row-level figure must have tied every row; a figure without coverage needs only to
        be ``ok``. A figure that could not compute supports nothing either way.
        """
        if self.ok is not True:
            return False
        return self.coverage.complete if self.coverage is not None else True


class EvidenceItem(BaseModel):
    """One expected evidence layer and whether the workbook actually satisfies it.

    Matching fails closed: a layer wrongly reported missing carries a remediation and a
    reviewer who can point at the tab, while one wrongly reported available is evidence
    nobody checked.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str = Field(..., min_length=1)
    expected: str = Field(default="", description="What was expected, in the business's words")
    required: bool = Field(default=False, description="Recommended items are reported, not scored")
    status: EvidenceStatus = Field(default=EvidenceStatus.MISSING)
    location: str | None = Field(default=None, description="Sheet or picture that satisfied it")
    remediation: str | None = Field(default=None, description="What to go and get, when missing")
    note: str = Field(
        default="",
        description=(
            "Why it reads as it does, written by code. A register whose every row says "
            "only 'missing' tells a reviewer nothing about where to look next."
        ),
    )
    needs_review: bool = Field(default=False)


class PictureReading(BaseModel):
    """What AI-R read off one pasted screenshot. Code checks the amounts against the workbook."""

    model_config = ConfigDict(frozen=True)

    picture_id: str = Field(..., min_length=1)
    legible: bool = Field(default=False)
    kind: str | None = Field(default=None)
    period: str | None = Field(default=None)
    entity: str | None = Field(default=None)
    currency: str | None = Field(default=None)
    amounts: tuple[tuple[str, Decimal], ...] = Field(
        default=(), description="Label and value pairs, as read"
    )
    approval_seen: bool = Field(default=False)


class PIIFinding(BaseModel):
    """Personal data found in the workbook, located but never reproduced."""

    model_config = ConfigDict(frozen=True)

    kind: PIIKind
    count: int = Field(default=0, ge=0)
    cells: tuple[CellRef, ...] = Field(default=(), description="Capped; locations only, no values")
    expected_here: bool = Field(
        default=False,
        description="True where the JE type expects this data, e.g. employee rows on a working",
    )


class ExceptionCluster(BaseModel):
    """Step 13b. One cause, and the exceptions it accounts for.

    Narrative only. A cluster never carries a status and never changes a count: the figures
    settled those before anything here ran, and this exists so a person can read 888
    exceptions as four causes instead of as a list.
    """

    model_config = ConfigDict(frozen=True)

    cause: str = Field(..., min_length=1, description="One line, descriptive not diagnostic")
    affected: int = Field(default=0, ge=0, description="Rows this cause accounts for")
    financial_impact: Decimal | None = Field(default=None)
    figure_ids: tuple[str, ...] = Field(default=(), description="The figures it was drawn from")
    example_rows: tuple[int, ...] = Field(default=(), description="Row numbers, never names")


class MethodologyReading(BaseModel):
    """AI-M's output: the basis the workbook's own methodology tab states.

    Never an input to arithmetic. ``methodology_drift`` diffs it against what the business
    approved, so the worst a misreading can do is raise a finding a reviewer resolves — it
    can never move a computed number.
    """

    model_config = ConfigDict(frozen=True)

    salary_per_day_denominator: str | None = Field(
        default=None, description="As the tab states it. None when the tab does not say"
    )
    eligible_leave_types: tuple[str, ...] = Field(default=())
    balance_floored_at_zero: bool | None = Field(
        default=None, description="None is 'not stated', which is not the same as false"
    )
    unread: tuple[str, ...] = Field(
        default=(), description="Parameters the tab does not state, named rather than guessed"
    )
    source_sha256: str = Field(default="", description="Digest of the lines that were read")
