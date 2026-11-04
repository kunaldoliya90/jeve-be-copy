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
from review.models.recalculation import BookedMethod, RecalcUnit, RoundingMode
from review.models.result import ScoringMethod
from review.models.verdicts import Status
from review.recalc.formula import FormulaError, compile_formula


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


class RoundingSpec(_Frozen):
    """The scale and direction the business rounds this entry's amounts to."""

    decimals: int = Field(default=2, ge=0, le=10)
    mode: RoundingMode = Field(default=RoundingMode.HALF_UP)

    def describe(self) -> str:
        return f"{self.decimals} decimal places, {self.mode.value.replace('_', ' ')}"


class CurrencySpec(_Frozen):
    """Where the entry's currency is read from, and what it is when the file is silent."""

    meaning: str | None = Field(
        default=None, description="Column meaning holding the currency, where the file carries one"
    )
    default: str = Field(default="", description="Used only when no column states it")


class RecalcInputSpec(_Frozen):
    """One input the approved formula reads, with its unit and where it comes from.

    Exactly one of ``meaning`` and ``value``: an input is either a column of this workbook or
    a constant the business states (a day-count denominator, a statutory rate). A constant
    with no owner is the thing this forbids - a 30 hardcoded in a formula is a methodology
    nobody approved.
    """

    id: str = Field(..., min_length=1, description="What the formula calls it")
    meaning: str | None = Field(default=None, description="The column meaning it is read from")
    value: Decimal | None = Field(default=None, description="A constant the business states")
    unit: RecalcUnit = Field(default=RecalcUnit.CURRENCY)
    source: str = Field(default="", description="The table it comes from; defaults to the working")
    required: bool = Field(
        default=True,
        description=(
            "Whether the formula cannot be evaluated without it. A required input this "
            "workbook does not carry makes the recalculation inconclusive, never a failure."
        ),
    )
    description: str = Field(default="", description="What it is, in the business's words")

    @model_validator(mode="after")
    def _a_column_or_a_constant(self) -> "RecalcInputSpec":
        if (self.meaning is None) == (self.value is None):
            raise ValueError(f"input {self.id!r} must state exactly one of a meaning or a value")
        return self


class DerivedTermSpec(_Frozen):
    """One intermediate the formula is built from, so the arithmetic reads in stages.

    ``agrees_with`` is the strongest evidence the work paper can carry: the term is
    re-performed from its inputs and compared, row by row, against a column the workbook
    computes for the same quantity. It corroborates; it never decides.
    """

    id: str = Field(..., min_length=1)
    expression: str = Field(..., min_length=1, description="Over inputs and earlier terms")
    unit: RecalcUnit = Field(default=RecalcUnit.CURRENCY)
    agrees_with: str | None = Field(
        default=None, description="A column meaning holding the workbook's own answer for it"
    )
    description: str = Field(default="", description="What it is, in the business's words")


class BookedSpec(_Frozen):
    """How the amount that was actually posted is read - never from the working itself.

    The point of the comparison is independence. ``posting_debits`` sums the debit side of
    the posted entry from its own lines, which is a different tab, a different table and a
    different reading from the one the recomputation walks.
    """

    method: BookedMethod = Field(default=BookedMethod.POSTING_DEBITS)
    table: str = Field(default="je_posting", description="The table the booked amount is read from")
    amount: str = Field(default="line_amount", description="The column meaning holding each line")
    account: str = Field(default="account", description="Used only to report which accounts it hit")
    fact: str = Field(
        default="", description="Fact name, for a control total the entry states about itself"
    )


class RecalcExplainedStepSpec(_Frozen):
    """One step of the formula, walked through in words, against the same fixed example.

    ``example`` is arithmetic over the one record ``RecalcExplainedSpec`` names, not a
    template filled in per review: the whole point is a reader can check it with a
    calculator, which a number that changes between reviews would not let them do.
    """

    label: str = Field(..., min_length=1, description="What this step establishes")
    detail: str = Field(default="", description="How it is worked out, in the business's words")
    example: str = Field(default="", description="That step, carried out on the fixed example")


class RecalcExplainedSpec(_Frozen):
    """The formula walked through in plain language, for a reader who has never seen it.

    The business's own words, carried verbatim - the same convention ``worked_example``
    already follows, and deliberately a fixed illustration rather than anything read from a
    particular review: a new review must never change what this section says, or a reader
    comparing two reports side by side would wonder why the explanation moved.
    """

    intro: str = Field(default="")
    context: str = Field(default="", description="What the two legs of the formula each mean")
    steps: tuple[RecalcExplainedStepSpec, ...] = Field(default=())
    summary: str = Field(default="", description="The whole thing, in one sentence")
    caveats: tuple[str, ...] = Field(default=(), description="What a reader should be told")


class RecalculationSpec(_Frozen):
    """The approved basis for regenerating this entry's value, owned by the business.

    Everything the design asks of a re-performance in one declaration: the formula, the
    inputs with their units and sources, the supporting conventions (sign, currency,
    rounding, day count), the tolerance the comparison is made under, and a worked example.
    Code evaluates it; no model ever sees it, and nothing in the harness assumes a formula
    for a JE type that does not state one.
    """

    table: str = Field(default="calculation", description="The working the records are read from")
    record_key: str = Field(
        default="employee_ref",
        description=(
            "The meaning that separates a record from the blank and subtotal rows beneath "
            "the data. A profiled block reaches past the last record, and on one sample "
            "file the row below it restates the whole entry."
        ),
    )
    formula: str = Field(..., min_length=1, description="Per record, over inputs and derived terms")
    stated: str | None = Field(
        default=None,
        description=(
            "The column meaning holding the workbook's own amount for each record, which is "
            "what row-level citations are raised against. None where the file states none."
        ),
    )
    inputs: tuple[RecalcInputSpec, ...] = Field(..., min_length=1)
    derive: tuple[DerivedTermSpec, ...] = Field(default=())
    booked: BookedSpec = Field(default_factory=BookedSpec)
    sign: str = Field(default="", description="Which side of the entry a positive amount is")
    currency: CurrencySpec = Field(default_factory=CurrencySpec)
    rounding: RoundingSpec = Field(default_factory=RoundingSpec)
    day_count: str = Field(
        default="",
        description=(
            "The day-count convention, named rather than assumed. Never an implied 30: a "
            "denominator nobody stated is a methodology nobody approved."
        ),
    )
    tolerance_pct: Decimal = Field(
        default=Decimal("0.1"),
        ge=0,
        description="Entry-level tolerance, as a percentage of the booked amount",
    )
    row_tolerance: Decimal = Field(
        default=Decimal("0.01"),
        ge=0,
        description="Per-row headroom for the currency's own rounding, in currency units",
    )
    provisional_basis: bool = Field(
        default=False,
        description=(
            "True until the business has signed the formula and the tolerance off. The work "
            "paper says so on its face: a pass under an unapproved basis is not a pass."
        ),
    )
    worked_example: str = Field(
        default="", description="The business's own worked example, shown beside the formula"
    )
    explained: RecalcExplainedSpec | None = Field(
        default=None, description="The formula walked through in plain language, step by step"
    )

    @property
    def names(self) -> frozenset[str]:
        """Everything the formula and the derived terms may refer to."""
        return frozenset({item.id for item in self.inputs} | {term.id for term in self.derive})

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "RecalculationSpec":
        ids = [item.id for item in self.inputs] + [term.id for term in self.derive]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(f"recalculation declares {duplicates} more than once")
        return self

    @model_validator(mode="after")
    def _every_expression_parses(self) -> "RecalculationSpec":
        """Parsed here so a malformed formula stops startup naming the field.

        A derived term may only read inputs and terms declared *before* it, which makes the
        evaluation order the declaration order and a cycle impossible to write.
        """
        known = {item.id for item in self.inputs}
        try:
            for term in self.derive:
                compile_formula(term.expression, frozenset(known))
                known.add(term.id)
            compile_formula(self.formula, frozenset(known))
        except FormulaError as exc:
            raise ValueError(str(exc)) from exc
        return self


class RowIdentitySpec(_Frozen):
    """One arithmetic identity the business says must hold on every record.

    ``expression`` is written over the **column meanings** the JE type's table declares -
    ``max(cp_earned - cp_taken, 0)`` - and ``against`` names the column holding the
    workbook's own answer for the same quantity. The row-level re-performances were Python
    until this existed, which meant the business could read its own arithmetic only by
    reading code; now a leave convention that differs by country is a YAML edit.

    The vocabulary is deliberately meanings rather than the declared input ids the entry
    recalculation uses. An identity is one line of arithmetic over columns a reviewer can
    point at, and giving each column a second name would buy nothing but a mapping to keep.
    """

    id: str = Field(..., min_length=1)
    expression: str = Field(..., min_length=1, description="Over the table's column meanings")
    against: str = Field(
        ..., min_length=1, description="The meaning holding the workbook's own answer"
    )
    label: str = Field(
        ...,
        min_length=1,
        description="What was compared, in the business's words. It is what the figure says",
    )
    tolerance: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-row headroom. None uses the business-wide tolerance. A product rounded to "
            "the currency's scale before it is stored needs a cent of room; a sum of two "
            "stored amounts does not."
        ),
    )
    min_tie_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Below this share of agreeing rows the identity is reported as unable to "
            "validate rather than as thousands of exceptions: an identity that fails on "
            "nearly every row is the wrong convention for this workbook, not a workbook "
            "where every row is wrong, and a confident false finding is the worse outcome."
        ),
    )


class ReperformanceSpec(_Frozen):
    """The record-level arithmetic for this entry type, as the business states it."""

    table: str = Field(default="calculation", description="The working the records are read from")
    record_key: str = Field(
        default="employee_ref",
        description="The meaning that separates a record from the rows beneath the data",
    )
    identities: tuple[RowIdentitySpec, ...] = Field(..., min_length=1)

    def identity(self, identity_id: str) -> RowIdentitySpec | None:
        return next((item for item in self.identities if item.id == identity_id), None)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "ReperformanceSpec":
        ids = [item.id for item in self.identities]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(f"reperformance declares {duplicates} more than once")
        return self


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
    reperformance: ReperformanceSpec | None = Field(
        default=None,
        description=(
            "The record-level identities the `reperform` tool runs. None for a JE type that "
            "states none, and then no row-level re-performance runs at all."
        ),
    )
    recalculation: RecalculationSpec | None = Field(
        default=None,
        description=(
            "The approved formula for regenerating this entry's value. None for a JE type "
            "whose formula the business has not stated, and the entry-level recalculation "
            "then reports as not run rather than inventing one."
        ),
    )
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
    def _identities_read_columns_this_type_declares(self) -> "JETypeContext":
        """Every identity must be expressed over meanings the working actually asks for.

        Checked here rather than on ``ReperformanceSpec`` because this is the only object
        that knows both the arithmetic and the tables, and a misspelled meaning would
        otherwise be a term that silently evaluates to nothing on every row - an identity
        reporting a clean workbook as unable to validate, which looks like a finding.
        """
        if self.reperformance is None:
            return self
        table = self.tables.get(self.reperformance.table)
        if table is None:
            raise ValueError(
                f"reperformance reads table {self.reperformance.table!r}, "
                f"which {self.id!r} does not declare"
            )
        meanings = frozenset(table.meanings)
        for identity in self.reperformance.identities:
            try:
                compile_formula(identity.expression, meanings)
            except FormulaError as exc:
                raise ValueError(f"identity {identity.id!r}: {exc}") from exc
            if identity.against not in meanings:
                raise ValueError(
                    f"identity {identity.id!r} compares against {identity.against!r}, which "
                    f"{self.reperformance.table!r} does not declare"
                )
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
