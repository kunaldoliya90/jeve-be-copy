"""The check plan: which tool runs on which columns, and where each step came from."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PlanSource(StrEnum):
    """Where a plan came from. Recorded because it changes what a failure means."""

    AI = "ai"
    CACHE = "cache"
    RECIPE = "recipe"
    """The JE type's own fixed recipe. Figures-decided checks always use this."""


class PlanStep(BaseModel):
    """One tool call. ``args`` name meanings from the sheet map, never raw ranges."""

    model_config = ConfigDict(frozen=True)

    check_id: str = Field(..., min_length=1)
    tool: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = Field(default="", description="One line; shown to a reviewer, not acted on")


class ExecutionPlan(BaseModel):
    """Every step step 13 will run, after ``plan_check`` has validated them."""

    model_config = ConfigDict(frozen=True)

    steps: tuple[PlanStep, ...] = Field(default=())
    source: PlanSource = Field(default=PlanSource.RECIPE)
