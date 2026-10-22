"""Step 11. The plan: which tool runs on which columns, for every check.

Almost all of it is deterministic, and that is the design rather than a shortcut. A JE type
states a fixed recipe per check, and a figures-decided check **always** uses it: a model that
could choose how its own arithmetic is done could choose the arithmetic that agrees with it.

AI-2 exists for the remainder — a check the context file declares but gives no recipe for.
It proposes one step from the tool catalogue, and ``plan_check`` validates it like any other
before step 13 runs it. For a JE type whose checks all carry recipes, AI-2 does not run, and
the plan says it came from the recipes.
"""

import json
import logging
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from review.agents.judging import call_model
from review.context.prompts import PromptRegistry
from review.models.context import JETypeContext
from review.models.plan import ExecutionPlan, PlanSource, PlanStep
from review.models.sheet_map import SheetMap
from review.tools import registry

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 3_000

PLAN_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check_id", "tool", "args", "why"],
                "properties": {
                    "check_id": {"type": "string"},
                    "tool": {"type": "string"},
                    # Name/value pairs rather than a free-form object: strict mode forbids
                    # `additionalProperties: true`, and an argument list is better named
                    # anyway — the plan check validates each name against the tool.
                    "args": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "value"],
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                            },
                        },
                    },
                    "why": {"type": "string"},
                },
            },
        }
    },
}


def plan_from_recipes(je_type: JETypeContext) -> ExecutionPlan:
    """Every check that states its own recipe, in the order the context file lists them.

    Order is kept because figure ids are assigned in plan order: ``F-07`` has to mean the
    same thing to the reviewer reading the report as to the engineer reading the trace.
    """
    return ExecutionPlan(
        source=PlanSource.RECIPE,
        steps=tuple(
            PlanStep(
                check_id=check.id,
                tool=check.recipe.tool,
                args=dict(check.recipe.args),
                why="the recipe this journal entry type states for the check",
            )
            for check in je_type.checks
            if check.recipe is not None
        ),
    )


def unplanned(je_type: JETypeContext, plan: ExecutionPlan) -> tuple[str, ...]:
    """Checks with no step. These are what AI-2 is asked about, and nothing else."""
    planned = {step.check_id for step in plan.steps}
    return tuple(check.id for check in je_type.checks if check.id not in planned)


async def propose_steps(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    je_type: JETypeContext,
    sheet_map: SheetMap,
    check_ids: tuple[str, ...],
) -> tuple[tuple[PlanStep, ...], LLMResponse | None]:
    """AI-2, for the checks the context file left unplanned.

    Returns no steps when there is nothing to plan or no provider to ask, which are both
    ordinary states: the checks then report as unproven rather than as passed.
    """
    if provider is None or not check_ids:
        return (), None

    questions = {check.id: check for check in je_type.checks}
    payload = {
        "tools_you_may_use": registry.catalogue(),
        "columns_bound_in_this_workbook": _bound(sheet_map),
        "checks_with_no_recipe": [
            {
                "check_id": check_id,
                "question": questions[check_id].question,
                "risk": questions[check_id].risk,
            }
            for check_id in check_ids
            if check_id in questions
        ],
    }

    response = await call_model(
        provider,
        prompts.get(AICall.PLAN).text,
        payload,
        PLAN_SCHEMA,
        "plan",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response is None:
        return (), None

    try:
        answer = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("AI-2 answer was not valid JSON: %s", exc)
        return (), response

    allowed = set(check_ids)
    steps: list[PlanStep] = []
    for raw in answer.get("steps", []) if isinstance(answer, dict) else []:
        if not isinstance(raw, dict):
            continue
        check_id = str(raw.get("check_id", ""))
        if check_id not in allowed:
            # A step for a check that already has a recipe is exactly what this step is not
            # allowed to produce, so it is dropped here rather than argued with at step 12.
            logger.warning("AI-2 proposed a step for %r, which it was not asked about", check_id)
            continue
        try:
            steps.append(
                PlanStep(
                    check_id=check_id,
                    tool=str(raw.get("tool", "")),
                    args=_args(raw.get("args")),
                    why=str(raw.get("why", "")),
                )
            )
        except ValueError as exc:
            logger.warning("discarding a malformed plan step: %s", exc)

    logger.info("AI-2 proposed %d step(s) for %d unplanned check(s)", len(steps), len(check_ids))
    return tuple(steps), response


def _args(raw: object) -> dict[str, Any]:
    """The proposed arguments as a mapping, from the name/value pairs the schema allows."""
    if not isinstance(raw, list):
        return {}
    pairs: dict[str, Any] = {}
    for item in raw:
        if isinstance(item, dict) and str(item.get("name", "")):
            pairs[str(item["name"])] = item.get("value")
    return pairs


def merge(plan: ExecutionPlan, proposed: tuple[PlanStep, ...]) -> ExecutionPlan:
    """Add proposed steps after the recipe steps, keeping one step per check."""
    if not proposed:
        return plan
    seen = {step.check_id for step in plan.steps}
    extra = tuple(step for step in proposed if step.check_id not in seen)
    if not extra:
        return plan
    return ExecutionPlan(steps=(*plan.steps, *extra), source=PlanSource.AI)


def _bound(sheet_map: SheetMap) -> dict[str, list[str]]:
    """Table name -> the meanings bound on it. What a step's args may name, and no more."""
    return {name: sorted(ref.columns) for name, ref in sheet_map.tables.items()}


__all__ = [
    "PLAN_SCHEMA",
    "merge",
    "plan_from_recipes",
    "propose_steps",
    "unplanned",
]
