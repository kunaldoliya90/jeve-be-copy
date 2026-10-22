"""The case file: one object every phase reads and extends, and every AI input projects from.

Nothing here is sent to a model whole. Each call receives a projection carrying only the
slice it needs, which is what keeps the workbook itself out of every prompt.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from review.models.evidence import EvidenceItem, Figure, PictureReading
from review.models.plan import ExecutionPlan
from review.models.sheet_map import SheetMap
from review.models.verdicts import CheckOutcome
from review.models.workbook import StructureContext, WorkbookProfile


class ReviewContext(BaseModel):
    """Everything gathered for one review, assembled at step 10 and filled at step 13.

    Mutable by design: the orchestrator extends it phase by phase. The models it holds are
    each frozen, so a phase replaces a field rather than editing one in place.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    review_id: str = Field(..., min_length=1)
    reuse_key: str = Field(..., min_length=1)

    profile: WorkbookProfile | None = Field(default=None)
    structure: StructureContext | None = Field(default=None)
    sheet_map: SheetMap | None = Field(default=None)
    facts: dict[str, Any] = Field(
        default_factory=dict, description="Values read from the fact cells by code, not by AI"
    )

    je_type: str = Field(default="unknown")
    je_type_variant: str | None = Field(
        default=None, description="Which accrual model the discriminator settled on"
    )

    plan: ExecutionPlan | None = Field(default=None)
    pictures_read: dict[str, PictureReading] = Field(default_factory=dict)
    evidence_pack: dict[str, tuple[Figure, ...]] = Field(
        default_factory=dict, description="Check id -> its figures"
    )
    evidence_register: tuple[EvidenceItem, ...] = Field(default=())
    outcomes: dict[str, CheckOutcome] = Field(default_factory=dict)

    versions: dict[str, str] = Field(
        default_factory=dict,
        description="JE type, rules and prompt hashes plus the model id. Keys the caches",
    )
    pre_flagged: dict[str, tuple[str, ...]] = Field(
        default_factory=dict,
        description="Check id -> flags raised before judging, e.g. map_unverified",
    )

    def figures_for(self, check_id: str) -> tuple[Figure, ...]:
        """Figures a verdict on this check is allowed to cite."""
        return self.evidence_pack.get(check_id, ())

    def decisive_figure(self, check_id: str) -> Figure | None:
        """The figure that sets a figures-decided check's status, if the plan produced one."""
        return next((f for f in self.figures_for(check_id) if f.decisive), None)
