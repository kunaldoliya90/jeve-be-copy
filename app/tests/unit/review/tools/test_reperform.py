"""The declarative row-level re-performance.

Every behaviour here was a decision in one of the three Python tools this replaced, and each
is pinned deliberately: a refactor that quietly changed when a check reports *unable to
validate* rather than *fail* would change what reviewers are asked to look at.
"""

from decimal import Decimal

import pytest

from review.models.context import ReperformanceSpec, RowIdentitySpec
from review.models.evidence import Figure
from review.models.workbook_data import CellValue
from review.tools.base import ToolContext, run_tool
from review.tools.reperform import TOOL_NAME, ReperformTool
from tests.unit.review.tools.conftest import make_context

CALCULATION = (
    "employee_ref",
    "fte_accrual",
    "non_fte_accrual",
    "total_accrual",
    "cp_earned",
    "cp_taken",
    "cp_balance",
)
Records = list[list[CellValue]]

SPLIT = RowIdentitySpec(
    id="fte_split_sums",
    label="FTE plus non-FTE against the stated total",
    expression="fte_accrual + non_fte_accrual",
    against="total_accrual",
)
MOVEMENT = RowIdentitySpec(
    id="cp_movement",
    label="CP earned minus taken against the stated balance",
    expression="max(cp_earned - cp_taken, 0)",
    against="cp_balance",
    min_tie_rate=0.5,
)


def context(
    records: Records, *identities: RowIdentitySpec, columns: tuple[str, ...] = CALCULATION
) -> ToolContext:
    ctx = make_context({"calculation": (columns, records)})
    ctx.je_type_args["reperformance"] = ReperformanceSpec(
        identities=identities or (SPLIT, MOVEMENT)
    )
    return ctx


def run(ctx: ToolContext, *identities: str) -> list[Figure]:
    return run_tool(
        ReperformTool(), ctx, "a_check", table="calculation", identities=list(identities)
    )


# --------------------------------------------------------------------------- it holds


def test_an_identity_that_holds_on_every_record_passes() -> None:
    records: Records = [
        ["E1", Decimal(100), Decimal(0), Decimal(100), None, None, None],
        ["E2", Decimal(150), Decimal(50), Decimal(200), None, None, None],
    ]

    figures = run(context(records), "fte_split_sums")

    assert len(figures) == 1
    figure = figures[0]
    assert figure.ok is True
    assert figure.supports_pass is True
    assert figure.summary == ("FTE plus non-FTE against the stated total: 2 of 2 rows agree")
    assert figure.coverage is not None
    assert (figure.coverage.rows_total, figure.coverage.rows_bound, figure.coverage.rows_tied) == (
        2,
        2,
        2,
    )


def test_the_label_the_business_wrote_is_what_the_figure_says() -> None:
    """The summary reaches the report, so the wording is the business's, not the code's."""
    records: Records = [["E1", Decimal(1), Decimal(0), Decimal(1), None, None, None]]

    figures = run(context(records), "fte_split_sums")

    assert figures[0].summary.startswith(SPLIT.label)


# ------------------------------------------------------------------- it does not hold


def test_a_record_that_differs_is_reported_with_its_cell_and_priced() -> None:
    records: Records = [
        ["E1", Decimal(100), Decimal(0), Decimal(100), None, None, None],
        ["E2", Decimal(150), Decimal(50), Decimal(175), None, None, None],
    ]

    figure = run(context(records), "fte_split_sums")[0]

    assert figure.ok is False
    assert figure.exception_count == 1
    assert figure.financial_impact == Decimal(25)
    assert "1 differ by 25 in total" in figure.summary
    assert figure.samples[0]["row"] == 3
    assert figure.samples[0]["cell"] == "D3"
    assert figure.samples[0]["difference"] == Decimal(25)


def test_the_worst_record_is_the_first_sample() -> None:
    records: Records = [
        ["E1", Decimal(100), Decimal(0), Decimal(90), None, None, None],
        ["E2", Decimal(100), Decimal(0), Decimal(40), None, None, None],
    ]

    figure = run(context(records), "fte_split_sums")[0]

    assert [sample["difference"] for sample in figure.samples] == [Decimal(60), Decimal(10)]


def test_a_missing_input_is_unbound_and_never_a_difference() -> None:
    """The workbook claimed nothing there, so there is nothing to disagree with."""
    records: Records = [
        ["E1", Decimal(100), Decimal(0), Decimal(100), None, None, None],
        ["E2", None, Decimal(50), Decimal(200), None, None, None],
    ]

    figure = run(context(records), "fte_split_sums")[0]

    assert figure.coverage is not None
    assert figure.coverage.rows_bound == 1
    assert figure.coverage.complete is False
    assert figure.supports_pass is False, "an incomplete figure can never back a pass"
    assert figure.exception_count == 1, "the unbound row is reported"
    assert "1 row(s) had no comparable figure" in figure.summary


def test_the_rows_beneath_the_data_are_not_records() -> None:
    records: Records = [
        ["E1", Decimal(100), Decimal(0), Decimal(100), None, None, None],
        [None, Decimal(100), Decimal(0), Decimal(100), None, None, None],
    ]

    figure = run(context(records), "fte_split_sums")[0]

    assert figure.coverage is not None
    assert figure.coverage.rows_total == 1


# --------------------------------------------------------------- the declared conventions


def test_the_declared_tolerance_is_what_decides_a_cent() -> None:
    """A product rounded before it was stored needs a cent of room; a sum does not."""
    records: Records = [
        ["E1", Decimal("100.00"), Decimal("0.004"), Decimal("100.00"), None, None, None]
    ]
    generous = RowIdentitySpec(
        id="with_room",
        label="With a cent of room",
        expression="fte_accrual + non_fte_accrual",
        against="total_accrual",
        tolerance=Decimal("0.01"),
    )
    strict = RowIdentitySpec(
        id="no_room",
        label="With no room at all",
        expression="fte_accrual + non_fte_accrual",
        against="total_accrual",
        tolerance=Decimal(0),
    )

    ctx = context(records, generous, strict)

    assert run(ctx, "with_room")[0].ok is True
    assert run(ctx, "no_room")[0].ok is False


def test_an_identity_that_fails_nearly_everywhere_is_the_wrong_identity() -> None:
    """2,000 confident false findings are worse than an honest "cannot be re-performed"."""
    records: Records = [
        ["E1", None, None, None, Decimal(10), Decimal(1), Decimal(5)],
        ["E2", None, None, None, Decimal(10), Decimal(1), Decimal(5)],
        ["E3", None, None, None, Decimal(10), Decimal(1), Decimal(9)],
    ]

    figure = run(context(records), "cp_movement")[0]

    assert figure.ok is None, "never a failure: the convention is unknown, not broken"
    assert figure.supports_pass is False
    assert figure.error == "cp_movement_convention_unknown"
    assert "derives the figure some other way" in figure.summary


def test_coalesce_lets_a_two_leg_total_tie_when_either_leg_is_blank() -> None:
    """``cp_provision_builds_up``'s own shape: a blank leg is a real zero, not a gap, because a
    new joiner has no prior-period leg and the business's own stated total simply omits it."""
    identity = RowIdentitySpec(
        id="builds_up",
        label="Prior plus current against the stated total",
        expression="coalesce(prior, 0) + coalesce(current, 0)",
        against="stated",
    )
    records: Records = [
        ["E1", Decimal(10), Decimal(5), Decimal(15), None, None, None],
        ["E2", None, Decimal(7), Decimal(7), None, None, None],
        ["E3", Decimal(3), None, Decimal(3), None, None, None],
    ]
    ctx = context(
        records, identity, columns=("employee_ref", "prior", "current", "stated", "a", "b", "c")
    )

    figure = run(ctx, "builds_up")[0]

    assert figure.ok is True
    assert figure.coverage is not None
    assert figure.coverage.complete is True


def test_above_the_declared_tie_rate_the_exceptions_are_reported_as_exceptions() -> None:
    records: Records = [
        ["E1", None, None, None, Decimal(10), Decimal(1), Decimal(9)],
        ["E2", None, None, None, Decimal(10), Decimal(1), Decimal(9)],
        ["E3", None, None, None, Decimal(10), Decimal(1), Decimal(5)],
    ]

    figure = run(context(records), "cp_movement")[0]

    assert figure.ok is False
    assert figure.exception_count == 1
    assert figure.error is None


def test_the_floor_the_business_states_is_applied() -> None:
    """``max(earned - taken, 0)``: rows that took more than they earned must not go negative."""
    records: Records = [["E1", None, None, None, Decimal(1), Decimal(5), Decimal(0)]]

    figure = run(context(records), "cp_movement")[0]

    assert figure.ok is True, "earned less taken is negative, floored to the stated zero"


# --------------------------------------------------------------------------- the refusals


def test_a_column_this_workbook_does_not_carry_is_unable_to_validate() -> None:
    records: Records = [["E1", Decimal(1), Decimal(0), Decimal(1)]]
    columns = ("employee_ref", "fte_accrual", "non_fte_accrual", "total_accrual")

    figure = run(context(records, columns=columns), "cp_movement")[0]

    assert figure.ok is None
    assert figure.error == "cp_movement_columns_missing"
    assert "cp_earned, cp_taken, cp_balance" in figure.summary
    assert figure.coverage is None


def test_each_identity_is_reported_separately_under_one_check() -> None:
    """The leave check re-performs two legs; a row missing one still has the other verified."""
    records: Records = [
        ["E1", Decimal(1), Decimal(0), Decimal(1), Decimal(10), Decimal(1), Decimal(9)]
    ]

    figures = run(context(records), "fte_split_sums", "cp_movement")

    assert len(figures) == 2
    assert [figure.args["identity"] for figure in figures] == ["fte_split_sums", "cp_movement"]
    assert all(figure.ok is True for figure in figures)


def test_an_identity_the_entry_type_does_not_declare_is_reported_not_ignored() -> None:
    records: Records = [["E1", Decimal(1), Decimal(0), Decimal(1), None, None, None]]

    figure = run(context(records), "no_such_identity")[0]

    assert figure.ok is None
    assert "no identity named 'no_such_identity'" in (figure.error or "")


def test_an_entry_type_with_no_identities_cannot_re_perform_anything() -> None:
    records: Records = [["E1", Decimal(1), Decimal(0), Decimal(1), None, None, None]]
    ctx = make_context({"calculation": (CALCULATION, records)})

    figure = run_tool(ReperformTool(), ctx, "a_check", table="calculation", identities=["x"])[0]

    assert figure.ok is None
    assert "states no record-level identities" in figure.summary


def test_a_check_that_names_no_identity_says_so() -> None:
    records: Records = [["E1", Decimal(1), Decimal(0), Decimal(1), None, None, None]]

    figure = run_tool(ReperformTool(), context(records), "a_check", table="calculation")[0]

    assert figure.ok is None
    assert "no identity was named" in figure.summary


def test_the_identities_come_from_the_entry_type_and_the_tool_is_named_once() -> None:
    """A plan argument is a table or an id; the arithmetic itself is never in the plan."""
    assert ReperformTool().name == TOOL_NAME
    assert set(ReperformTool().signature) == {"table", "identities"}


@pytest.mark.parametrize("identities", ["fte_split_sums", ["fte_split_sums"]])
def test_one_identity_may_be_named_as_a_string_or_a_list(identities: str | list[str]) -> None:
    """A cached plan from an older build may carry either, and both mean the same check."""
    records: Records = [["E1", Decimal(1), Decimal(0), Decimal(1), None, None, None]]

    figures = run_tool(
        ReperformTool(), context(records), "a_check", table="calculation", identities=identities
    )

    assert len(figures) == 1
    assert figures[0].ok is True
