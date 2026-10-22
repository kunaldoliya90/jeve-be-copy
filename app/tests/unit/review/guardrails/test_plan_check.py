"""Step 12: nothing runs until the plan has been checked against this workbook.

Every test here is about a step being *dropped*. That is the whole function: a tool called
with an argument it does not take, or on a table the map never bound, produces a number
nobody asked for, and a number nobody asked for is worse than a gap.
"""

from review.guardrails.plan_check import plan_check
from review.models.plan import ExecutionPlan, PlanSource, PlanStep
from review.models.sheet_map import SheetMap
from review.models.workbook import TableRef


def _sheet_map(*tables: str) -> SheetMap:
    return SheetMap(
        tables={
            name: TableRef(
                sheet=name,
                header_row=1,
                first_row=2,
                last_row=10,
                columns={"total_accrual": "A", "employee_ref": "B"},
            )
            for name in tables
        },
        je_type="vacation-accrual-france",
    )


def _plan(*steps: PlanStep, source: PlanSource = PlanSource.RECIPE) -> ExecutionPlan:
    return ExecutionPlan(steps=steps, source=source)


def _step(check_id: str, tool: str, **args: object) -> PlanStep:
    return PlanStep(check_id=check_id, tool=tool, args=args)


def test_a_valid_step_passes_through_unchanged() -> None:
    plan = _plan(_step("accrual_reperforms", "recalc_accrual", table="calculation"))

    checked = plan_check(plan, _sheet_map("calculation"))

    assert checked.plan.steps == plan.steps
    assert checked.dropped == ()


def test_the_plan_source_survives_the_check() -> None:
    """A reader has to know whether a step came from a recipe or from a model."""
    plan = _plan(
        _step("accrual_reperforms", "recalc_accrual", table="calculation"),
        source=PlanSource.AI,
    )

    assert plan_check(plan, _sheet_map("calculation")).plan.source is PlanSource.AI


def test_a_step_naming_a_tool_that_does_not_exist_is_dropped() -> None:
    plan = _plan(_step("some_check", "no_such_tool", table="calculation"))

    checked = plan_check(plan, _sheet_map("calculation"))

    assert checked.plan.steps == ()
    assert checked.dropped == (("some_check", "no tool named 'no_such_tool'"),)


def test_a_step_passing_an_argument_the_tool_does_not_take_is_dropped() -> None:
    """A tool that silently ignores an argument measures something other than what was asked.

    This is the case that caught two misleading recipes in the context file: both named a
    master table that the tool never consulted.
    """
    plan = _plan(_step("accrual_reperforms", "recalc_accrual", table="calculation", master="x"))

    checked = plan_check(plan, _sheet_map("calculation"))

    assert checked.plan.steps == ()
    assert checked.dropped[0][1] == "recalc_accrual does not take master"


def test_a_step_on_a_table_this_workbook_does_not_have_is_dropped() -> None:
    plan = _plan(_step("entry_balances", "balance", table="je_posting"))

    checked = plan_check(plan, _sheet_map("calculation"))

    assert checked.plan.steps == ()
    assert checked.dropped[0][1] == "this workbook has no je_posting table"


def test_only_the_first_step_for_a_check_is_kept() -> None:
    """Two figures competing to be decisive is a status nobody chose."""
    plan = _plan(
        _step("accrual_reperforms", "recalc_accrual", table="calculation"),
        _step("accrual_reperforms", "negative_scan", table="calculation"),
    )

    checked = plan_check(plan, _sheet_map("calculation"))

    assert [step.tool for step in checked.plan.steps] == ["recalc_accrual"]
    assert checked.dropped[0][1] == "a step for this check was already planned"


def test_a_dropped_step_does_not_take_the_valid_ones_with_it() -> None:
    plan = _plan(
        _step("accrual_reperforms", "recalc_accrual", table="calculation"),
        _step("entry_balances", "balance", table="je_posting"),
        _step("negative_accruals_explained", "negative_scan", table="calculation"),
    )

    checked = plan_check(plan, _sheet_map("calculation"))

    assert [step.check_id for step in checked.plan.steps] == [
        "accrual_reperforms",
        "negative_accruals_explained",
    ]
    assert checked.dropped_checks == ("entry_balances",)


def test_a_step_with_no_table_argument_is_left_alone() -> None:
    """``formula_health`` and ``evidence_register`` read the whole workbook."""
    plan = _plan(_step("manual_updates_supported", "formula_health"))

    assert plan_check(plan, _sheet_map()).plan.steps != ()


def test_an_optional_table_that_is_absent_does_not_drop_the_step() -> None:
    """The tool degrades and says so in its figure, which tells a reviewer more.

    ``pii_scan`` names tables it may not find; dropping the step would turn a partial
    answer into no answer at all.
    """
    plan = _plan(
        _step(
            "personal_data_located",
            "pii_scan",
            expected_on=["calculation", "employee_master"],
            forbidden_on=["je_posting"],
        )
    )

    assert plan_check(plan, _sheet_map("calculation")).dropped == ()


def test_an_empty_plan_checks_clean() -> None:
    checked = plan_check(_plan(), _sheet_map("calculation"))

    assert checked.plan.steps == ()
    assert checked.dropped_checks == ()
