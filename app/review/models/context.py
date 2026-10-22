"""Business knowledge, as the harness loads it: JE type files and the global rules.

These are the only models built from files a business owner edits rather than from code, so
they validate strictly and fail at startup rather than mid-review. Every file's SHA goes
into the review's audit trail and into the reuse key, which is what stops a stored result
being served across a rule change.
"""

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.consts import RiskRating
from review.models.result import ScoringMethod
from review.models.verdicts import Status


class DecidedBy(StrEnum):
    """Who sets a check's status."""

    FIGURES = "figures"
    """The decisive tool result alone. AI-3 writes the conclusion text and nothing else."""
    JUDGEMENT = "judgement"
    """AI-3, from the figures and the business rules. AI-4 may only make it stricter."""


class EvidenceKind(StrEnum):
    """What satisfies an expected evidence item."""

    SHEET = "sheet"
    PICTURE = "picture"
    SHEET_OR_FACT = "sheet_or_fact"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    """``extra="forbid"`` so a typo in a hand-edited YAML file is a startup error rather
    than a silently ignored rule."""


class AreaSpec(_Frozen):
    """One scored review area. Weights across a JE type must total 100."""

    weight: int = Field(..., ge=0, le=100)
    method: ScoringMethod


class CheckRecipe(_Frozen):
    """The fixed tool call for a check.

    Figures-decided checks always use their recipe; AI-2's step for them is discarded, so a
    model cannot choose how its own arithmetic is done.
    """

    tool: str = Field(..., min_length=1)
    args: dict[str, object] = Field(default_factory=dict)


class CheckSpec(_Frozen):
    """One check: the question, the area it feeds, and who decides it."""

    id: str = Field(..., min_length=1)
    area: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    decided_by: DecidedBy
    recipe: CheckRecipe | None = Field(default=None)
    risk: str = Field(default="", description="The business risk this check addresses")

    @model_validator(mode="after")
    def _figures_checks_need_a_recipe(self) -> "CheckSpec":
        if self.decided_by is DecidedBy.FIGURES and self.recipe is None:
            raise ValueError(f"check {self.id!r} is decided by figures but has no recipe")
        return self


class EvidenceSpec(_Frozen):
    """One expected evidence layer."""

    id: str = Field(..., min_length=1)
    required: bool = Field(default=False)
    kind: EvidenceKind = Field(default=EvidenceKind.SHEET)
    expected: str = Field(
        default="",
        description="What this layer is, in the business's words. Read by a person, never matched",
    )
    corroborate: tuple[str, ...] = Field(
        default=(), description="Fact names a picture's amounts must match to earn full credit"
    )
    remediation: str = Field(default="", description="What to go and get when it is missing")


class TableSpec(_Frozen):
    """A table the JE type needs. Says *what* to find, never *where* — that is AI-1's job."""

    required: bool = Field(default=False)
    meanings: tuple[str, ...] = Field(default=())


class VariantSpec(_Frozen):
    """One shape a JE type can take, and what disqualifies it.

    ``rejects`` is the load-bearing half. Without it a near-miss binds to the wrong
    arithmetic and returns a plausible number, which is worse than an unsupported workbook
    because nothing looks wrong.
    """

    id: str = Field(..., min_length=1)
    requires_all: tuple[str, ...] = Field(..., min_length=1)
    rejects: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def _requires_and_rejects_are_disjoint(self) -> "VariantSpec":
        both = set(self.requires_all) & set(self.rejects)
        if both:
            raise ValueError(f"variant {self.id!r} both requires and rejects {sorted(both)}")
        return self


class DiscriminatorSpec(_Frozen):
    """How a workbook is recognised as this JE type, before any model call."""

    precedence: int = Field(..., ge=0, description="Lower is evaluated first")
    variants: tuple[VariantSpec, ...] = Field(..., min_length=1)
    corroborating: tuple[str, ...] = Field(
        default=(), description="Signals that must be cited from outside the posting table"
    )
    never: tuple[str, ...] = Field(
        default=(), description='Signals that may never be used, e.g. "tab_name_match"'
    )

    @model_validator(mode="after")
    def _variant_ids_are_unique(self) -> "DiscriminatorSpec":
        ids = [variant.id for variant in self.variants]
        if len(set(ids)) != len(ids):
            raise ValueError("variant ids must be unique within a JE type")
        return self


class MethodologySpec(_Frozen):
    """Parameters the arithmetic needs, stated by the business rather than inferred.

    ``methodology_drift`` diffs the workbook's own methodology tab against these, so a
    preparer changing the basis shows up as an exception instead of as a new normal.
    """

    salary_per_day_denominator: str = Field(
        ..., min_length=1, description='Named basis, e.g. "approved". Never an assumed 30'
    )
    eligible_leave_types: tuple[str, ...] = Field(default=())
    balance_floored_at_zero: bool = Field(default=True)
    notes: tuple[str, ...] = Field(default=())


class AccountsSpec(_Frozen):
    """Account numbers by role, and the tab they are validated against."""

    model_config = ConfigDict(frozen=True, extra="allow")

    source: str = Field(..., min_length=1, description="Sheet role holding the master mapping")


class PopulationSpec(_Frozen):
    """Which staffing types are in scope, and on which side of the expense split."""

    include_fte: tuple[str, ...] = Field(default=())
    include_non_fte: tuple[str, ...] = Field(default=())
    exclude: tuple[str, ...] = Field(default=())


class JETypeContext(_Frozen):
    """One JE type, loaded from ``context/je_types/<id>.yaml``."""

    id: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    name: str = Field(..., min_length=1)
    process: str = Field(default="")
    sub_process: str = Field(default="")
    aliases: tuple[str, ...] = Field(default=())
    objective: str = Field(..., min_length=1)

    areas: dict[str, AreaSpec] = Field(..., min_length=1)
    tables: dict[str, TableSpec] = Field(default_factory=dict)
    facts: tuple[str, ...] = Field(default=())
    evidence: tuple[EvidenceSpec, ...] = Field(default=())
    checks: tuple[CheckSpec, ...] = Field(..., min_length=1)

    discriminators: DiscriminatorSpec | None = Field(default=None)
    methodology: MethodologySpec | None = Field(default=None)
    accounts: AccountsSpec | None = Field(default=None)
    population: PopulationSpec | None = Field(default=None)
    known_patterns: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def _weights_total_one_hundred(self) -> "JETypeContext":
        total = sum(area.weight for area in self.areas.values())
        if total != 100:
            raise ValueError(f"area weights for {self.id!r} total {total}, not 100")
        return self

    @model_validator(mode="after")
    def _checks_are_unique_and_scored(self) -> "JETypeContext":
        ids = [check.id for check in self.checks]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate check ids in {self.id!r}")
        unknown = {c.area for c in self.checks} - set(self.areas) - {OBSERVATION_AREA}
        if unknown:
            raise ValueError(f"checks in {self.id!r} name unscored areas: {sorted(unknown)}")
        covered = {check.area for check in self.checks}
        missing = set(self.areas) - covered
        if missing:
            raise ValueError(f"areas with no check in {self.id!r}: {sorted(missing)}")
        return self

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> "JETypeContext":
        ids = [item.id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate evidence ids in {self.id!r}")
        return self

    @property
    def required_evidence(self) -> tuple[EvidenceSpec, ...]:
        """Only these are scored. Recommended items are reported and never counted."""
        return tuple(item for item in self.evidence if item.required)

    def check(self, check_id: str) -> CheckSpec | None:
        return next((check for check in self.checks if check.id == check_id), None)

    def checks_for(self, area: str) -> tuple[CheckSpec, ...]:
        return tuple(check for check in self.checks if check.area == area)


OBSERVATION_AREA = "observation"
"""Checks reported to the reviewer but never scored. Not an area, so it carries no weight."""


class Band(_Frozen):
    """A band label, the lowest score that earns it, and the risk rating it carries."""

    min: float = Field(..., ge=0, le=100)
    label: str = Field(..., min_length=1)
    risk: RiskRating = Field(..., description="The entry's risk rating for a score in this band")


class PIIRules(_Frozen):
    """What counts as personal data, and what is expected rather than exceptional."""

    patterns: tuple[str, ...] = Field(default=())
    exclude_facts: tuple[str, ...] = Field(
        default=(), description="Sign-off names are expected and are not findings"
    )
    masked_value_regex: str = Field(default=r"^(\*{2,}|X{2,}\d{2,4})$")


class TrendRules(_Frozen):
    """Unresolved until the business sets a threshold, and never a pass before then."""

    lookback_months: int = Field(default=3, ge=1)
    max_change_pct: float | None = Field(default=None)


class ClusteringRules(_Frozen):
    """When row-level exceptions are grouped by cause before a person reads them."""

    min_exceptions: int = Field(
        default=25, ge=1, description="Below this, exceptions are shown as they are"
    )
    max_clusters: int = Field(default=6, ge=1, le=20)


class BusinessRules(_Frozen):
    """``context/business_rules.yaml``: the numbers the business has agreed, kept out of code."""

    version: int = Field(..., ge=1)
    credits: dict[Status, int] = Field(..., min_length=1)
    needs_review_cap: int = Field(default=50, ge=0, le=100)
    bands: tuple[Band, ...] = Field(..., min_length=1)
    tolerance: Decimal = Field(default=Decimal("0.005"), ge=0)
    trend: TrendRules = Field(default_factory=TrendRules)
    pii: PIIRules = Field(default_factory=PIIRules)
    clustering: ClusteringRules = Field(default_factory=ClusteringRules)
    formula_defects_can_fail_check: bool = Field(default=False)

    @model_validator(mode="after")
    def _every_status_has_a_credit(self) -> "BusinessRules":
        missing = set(Status) - set(self.credits)
        if missing:
            raise ValueError(f"no credit defined for {sorted(status for status in missing)}")
        return self

    @model_validator(mode="after")
    def _bands_descend_and_reach_zero(self) -> "BusinessRules":
        mins = [band.min for band in self.bands]
        if mins != sorted(mins, reverse=True):
            raise ValueError("bands must be listed from highest minimum to lowest")
        if mins[-1] != 0:
            raise ValueError("the lowest band must start at 0, or a score could match none")
        return self

    def _band_for(self, score: float) -> Band:
        """The first band the score reaches. The list is validated to always match one."""
        return next(band for band in self.bands if score >= band.min)

    def band_for(self, score: float) -> str:
        return self._band_for(score).label

    def risk_for(self, score: float) -> RiskRating:
        """The risk rating a score earns.

        Read off the same band as the label, never computed beside it: a second set of
        thresholds for the rating is a second set of thresholds to forget to update.
        """
        return self._band_for(score).risk

    def credit_for(self, status: Status, *, needs_review: bool) -> int:
        """Credit for a status, capped when the check is waiting for a person."""
        credit = self.credits[status]
        return min(credit, self.needs_review_cap) if needs_review else credit


class ContextVersions(_Frozen):
    """What every review records about the knowledge it was decided by.

    These hashes key the reuse index and the layout cache, so changing a rule or a prompt
    produces a new key and no stored result is ever served across the change.
    """

    je_type: str = Field(..., min_length=1, description='e.g. "vacation-accrual-france@1#a1b2"')
    rules: str = Field(..., min_length=1)
    prompts: dict[str, str] = Field(default_factory=dict)
    harness: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)

    def as_key_material(self) -> dict[str, str]:
        """Flattened for ``reuse_key``; prompt hashes are namespaced so they cannot clash."""
        material = {
            "je_type": self.je_type,
            "rules": self.rules,
            "harness": self.harness,
            "model": self.model,
        }
        material.update({f"prompt.{call}": sha for call, sha in self.prompts.items()})
        return material
