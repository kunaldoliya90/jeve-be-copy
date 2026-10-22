"""Step 11: where each step in the plan came from, and what AI-2 is allowed to add.

The load-bearing property is the one the design states and this file pins down: a check
with a recipe is never planned by a model, so how its arithmetic is done is never chosen by
the same system that interprets the result.
"""

import pytest

from review.context.loader import ContextLoader
from review.context.prompts import PromptRegistry
from review.models.context import JETypeContext
from review.models.plan import ExecutionPlan, PlanSource, PlanStep
from review.models.sheet_map import SheetMap
from review.models.workbook import TableRef
from review.orchestrator.planner import (
    merge,
    plan_from_recipes,
    propose_steps,
    unplanned,
)
from tests.doubles import StubLLMProvider

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def france() -> JETypeContext:
    loader = ContextLoader()
    loader.load()
    return loader.je_type("vacation-accrual-france")


@pytest.fixture
def prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.load()
    return registry


def _sheet_map() -> SheetMap:
    return SheetMap(
        tables={
            "calculation": TableRef(
                sheet="GPAY",
                header_row=1,
                first_row=2,
                last_row=10,
                columns={"total_accrual": "A"},
            )
        },
        je_type="vacation-accrual-france",
    )


# --- the recipe half ----------------------------------------------------------------------


def test_the_plan_is_built_from_the_recipes_the_context_file_states(france: JETypeContext) -> None:
    plan = plan_from_recipes(france)

    assert plan.source is PlanSource.RECIPE
    assert len(plan.steps) == sum(1 for check in france.checks if check.recipe is not None)


def test_the_plan_keeps_the_order_the_context_file_lists(france: JETypeContext) -> None:
    """Figure ids are assigned in plan order, so the order is part of the contract."""
    plan = plan_from_recipes(france)

    expected = [check.id for check in france.checks if check.recipe is not None]
    assert [step.check_id for step in plan.steps] == expected


def test_every_check_of_this_type_is_planned_from_its_recipe(france: JETypeContext) -> None:
    """Nothing is left for a model to decide on this type, and that is the intent."""
    assert unplanned(france, plan_from_recipes(france)) == ()


# --- the AI-2 half ------------------------------------------------------------------------


async def test_no_provider_means_no_proposed_steps(
    france: JETypeContext, prompts: PromptRegistry
) -> None:
    steps, response = await propose_steps(None, prompts, france, _sheet_map(), ("some_check",))

    assert steps == ()
    assert response is None


async def test_nothing_to_plan_means_the_model_is_not_called(
    france: JETypeContext, prompts: PromptRegistry
) -> None:
    stub = StubLLMProvider()

    steps, response = await propose_steps(stub, prompts, france, _sheet_map(), ())

    assert steps == ()
    assert response is None
    assert stub.requests == [], "a call with nothing to ask about is a call not worth paying for"


async def test_a_proposed_step_for_a_check_it_was_not_asked_about_is_discarded(
    france: JETypeContext, prompts: PromptRegistry
) -> None:
    """The rule that keeps a model away from the arithmetic, enforced in code."""
    stub = StubLLMProvider(
        {
            "plan": {
                "steps": [
                    {
                        "check_id": "accrual_reperforms",
                        "tool": "balance",
                        "args": {},
                        "why": "I would rather do it this way",
                    }
                ]
            }
        }
    )

    steps, _ = await propose_steps(
        stub, prompts, france, _sheet_map(), ("manual_updates_supported",)
    )

    assert steps == (), "a recipe-backed check is never replanned"


async def test_a_step_for_a_check_it_was_asked_about_is_kept(
    france: JETypeContext, prompts: PromptRegistry
) -> None:
    stub = StubLLMProvider(
        {
            "plan": {
                "steps": [
                    {
                        "check_id": "manual_updates_supported",
                        "tool": "formula_health",
                        "args": {},
                        "why": "counts hardcoded cells where a formula is expected",
                    }
                ]
            }
        }
    )

    steps, response = await propose_steps(
        stub, prompts, france, _sheet_map(), ("manual_updates_supported",)
    )

    assert [step.tool for step in steps] == ["formula_health"]
    assert response is not None


async def test_the_model_is_told_the_bound_columns_and_the_tools_and_nothing_else(
    france: JETypeContext, prompts: PromptRegistry
) -> None:
    stub = StubLLMProvider({"plan": {"steps": []}})

    await propose_steps(stub, prompts, france, _sheet_map(), ("manual_updates_supported",))

    payload = stub.payload("plan")
    assert set(payload) == {
        "tools_you_may_use",
        "columns_bound_in_this_workbook",
        "checks_with_no_recipe",
    }
    assert payload["columns_bound_in_this_workbook"] == {"calculation": ["total_accrual"]}
    assert [check["check_id"] for check in payload["checks_with_no_recipe"]] == [
        "manual_updates_supported"
    ]


# --- merging --------------------------------------------------------------------------------


def test_merging_nothing_leaves_the_plan_and_its_source_alone() -> None:
    plan = ExecutionPlan(steps=(PlanStep(check_id="a", tool="balance"),))

    assert merge(plan, ()) is plan


def test_a_merged_plan_records_that_a_model_contributed() -> None:
    plan = ExecutionPlan(steps=(PlanStep(check_id="a", tool="balance"),))

    merged = merge(plan, (PlanStep(check_id="b", tool="formula_health"),))

    assert merged.source is PlanSource.AI
    assert [step.check_id for step in merged.steps] == ["a", "b"]


def test_a_proposed_step_cannot_displace_a_recipe_step() -> None:
    plan = ExecutionPlan(steps=(PlanStep(check_id="a", tool="recalc_accrual"),))

    merged = merge(plan, (PlanStep(check_id="a", tool="balance"),))

    assert [step.tool for step in merged.steps] == ["recalc_accrual"]
    assert merged.source is PlanSource.RECIPE, "nothing of the model's was taken"
