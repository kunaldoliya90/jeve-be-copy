"""One record's difference traced to one input, and the cases where it must not be.

The sample is the shape both real workbooks carry: a daily rate of 100,000 typed into a
column that holds a rate of a few hundred, on a formula of
``cp_provision + rtt_amount / 12``.
"""

from decimal import Decimal

import pytest

from review.models.context import (
    DerivedTermSpec,
    RecalcInputSpec,
    RecalculationSpec,
    WalkthroughSpec,
    WalkthroughStepSpec,
)
from review.models.recalculation import CitedValue, RecalcCitation, RecalcUnit
from review.recalc.row_diagnosis import diagnose_row

# Named escapes rather than the characters themselves: these are the operators a reader
# sees on the report, and a source file that carries them literally trips the linter's
# ambiguous-character rule on every line that quotes one.
TIMES = "×"  # noqa: RUF001 - the operator the report prints, not the letter x
DIVIDE = "÷"


def _spec(**overrides: object) -> RecalculationSpec:
    fields: dict[str, object] = {
        "table": "calculation",
        "formula": "cp_provision + rtt_amount / 12",
        "stated": "total_accrual",
        "row_tolerance": Decimal("0.01"),
        "inputs": (
            RecalcInputSpec(id="cp_provision", meaning="cp_provision", unit=RecalcUnit.CURRENCY),
            RecalcInputSpec(
                id="rtt_entitlement_days", meaning="rtt_entitlement_days", unit=RecalcUnit.DAYS
            ),
            RecalcInputSpec(id="months_to_accrue", meaning="months", unit=RecalcUnit.MONTHS),
            RecalcInputSpec(id="rtt_days_taken", meaning="rtt_days_taken", unit=RecalcUnit.DAYS),
            RecalcInputSpec(
                id="daily_rate", meaning="daily_rate", unit=RecalcUnit.CURRENCY_PER_DAY
            ),
        ),
        "derive": (
            DerivedTermSpec(
                id="earned_rtt_days",
                expression="coalesce(rtt_entitlement_days, 0) / 12 * months_to_accrue",
                label="RTT days earned",
                unit=RecalcUnit.DAYS,
            ),
            DerivedTermSpec(
                id="unused_rtt_days",
                # The file's own convention, and the reason only one input reconciles this
                # record: the formula steps rather than slopes through the days taken.
                expression=(
                    "if(coalesce(rtt_days_taken, 0) == 0, 0, "
                    "max(earned_rtt_days - rtt_days_taken, 0))"
                ),
                label="Unused RTT days",
                unit=RecalcUnit.DAYS,
            ),
            DerivedTermSpec(
                id="rtt_amount",
                expression="unused_rtt_days * daily_rate",
                label="Rebuilt RTT accrual",
                unit=RecalcUnit.CURRENCY,
            ),
        ),
        "walkthrough": WalkthroughSpec(
            total="Total = Holiday amount (CP) + 1/12 of the value of unused RTT days",
            footnote="Only for FTE and Non-FTE employees.",
            steps=(
                WalkthroughStepSpec(
                    id="days_earned",
                    line=f"Days earned = yearly RTT days {DIVIDE} 12 {TIMES} months worked.",
                    label="Days earned",
                    shows=f"{{rtt_entitlement_days}} {DIVIDE} 12 {TIMES} {{months_to_accrue}}",
                    expr="coalesce(rtt_entitlement_days, 0) / 12 * months_to_accrue",
                ),
                WalkthroughStepSpec(
                    id="value",
                    line=f"Value = days left {TIMES} daily salary.",
                    label="Value",
                    shows=f"{{unused_rtt_days}} {TIMES} {{daily_rate}}",
                    expr="unused_rtt_days * daily_rate",
                ),
            ),
        ),
    }
    fields.update(overrides)
    return RecalculationSpec(**fields)


def _citation(**overrides: object) -> RecalcCitation:
    fields: dict[str, object] = {
        "row": 9,
        "sheet": "GPAY",
        "kind": "stated_differs",
        "recomputed": Decimal("59631.51"),
        "stated": Decimal("12556.50"),
        "stated_cell": "AH9",
        "difference": Decimal("47075.01"),
        "inputs": (
            CitedValue(
                id="cp_provision", label="Total Provision\nCP", cell="M9", value=Decimal("12409.29")
            ),
            CitedValue(
                id="rtt_entitlement_days", label="Acquis\nRTT", cell="Q9", value=Decimal(10)
            ),
            CitedValue(
                id="months_to_accrue", label="Months to Accrue", cell="Z9", value=Decimal(8)
            ),
            CitedValue(id="rtt_days_taken", label="Pris\nRTT", cell="R9", value=Decimal(1)),
            CitedValue(
                id="daily_rate",
                label="Daily Salary (RTT Rate)",
                cell="W9",
                value=Decimal("100000"),
            ),
        ),
    }
    fields.update(overrides)
    return RecalcCitation(**fields)


def test_it_names_the_cell_that_carries_the_difference() -> None:
    found = diagnose_row(_spec(), _citation())

    assert found is not None
    assert found.fault is not None
    assert found.fault.input_id == "daily_rate"
    assert found.fault.cell == "W9"
    assert found.where == "GPAY sheet, row 9, column W (daily salary)."


def test_the_expected_value_is_solved_from_the_record_itself() -> None:
    """311.74 is what the row's own stated amount implies, not a number from anywhere else."""
    found = diagnose_row(_spec(), _citation())

    assert found is not None
    assert found.fault is not None
    assert found.fault.expected == Decimal("311.74")
    assert found.problem == "The daily salary is 100,000. It should be about 311.74."


def test_it_asks_for_the_right_value_rather_than_announcing_one() -> None:
    """The record agreeing at some value is evidence about where to look, not a verdict."""
    found = diagnose_row(_spec(), _citation())

    assert found is not None
    assert found.next_step == "Ask the preparer for the right daily salary and fix cell W9."


def test_the_impact_claims_the_whole_difference_only_when_it_is_the_whole_difference() -> None:
    whole = diagnose_row(_spec(), _citation(), entry_difference=Decimal("47075.00"))
    part = diagnose_row(_spec(), _citation(), entry_difference=Decimal("91000.00"))

    assert whole is not None
    assert "the full difference in the total" in whole.impact
    assert part is not None
    assert "full difference" not in part.impact


def test_the_example_runs_the_business_steps_both_ways() -> None:
    found = diagnose_row(_spec(), _citation())

    assert found is not None
    steps = {step.label: step for step in found.steps}
    assert steps["Days earned"].correct == f"10 {DIVIDE} 12 {TIMES} 8 = 6.67"
    # Unaffected by the rate, so the number is printed once rather than twice.
    assert steps["Days earned"].differs is False
    assert steps["Days earned"].actual == "6.67"
    assert steps["Value"].correct == f"5.67 {TIMES} 311.74 = 1,766.53"
    assert steps["Value"].actual == f"5.67 {TIMES} 100,000 = 566,666.67"
    assert steps["Value"].differs is True


def test_a_record_that_was_never_recomputed_is_not_diagnosed() -> None:
    """A blank input is a gap in the evidence, not an input with a wrong value in it."""
    assert diagnose_row(_spec(), _citation(kind="input_missing", recomputed=None)) is None


def test_nothing_is_reported_where_the_value_is_only_slightly_out() -> None:
    """A rate out by a twentieth is rounding or a stale figure. It identifies no cell."""
    close = _citation(stated=Decimal("59000.00"))

    assert diagnose_row(_spec(), close) is None


@pytest.mark.parametrize("missing", ["daily_rate", "cp_provision"])
def test_nothing_is_reported_where_an_input_was_not_read(missing: str) -> None:
    citation = _citation(
        inputs=tuple(
            value.model_copy(update={"value": None}) if value.id == missing else value
            for value in _citation().inputs
        )
    )

    assert diagnose_row(_spec(), citation) is None


def test_nothing_is_reported_where_two_inputs_each_reconcile_the_record() -> None:
    """One explanation, or none. A confident sentence naming the wrong cell is worse than
    no sentence at all, and on a formula that slopes smoothly through two of its inputs
    there is no way to tell which of them is the one that is wrong."""
    smooth = _spec(
        derive=(
            DerivedTermSpec(
                id="earned_rtt_days",
                expression="coalesce(rtt_entitlement_days, 0) / 12 * months_to_accrue",
                unit=RecalcUnit.DAYS,
            ),
            DerivedTermSpec(
                id="unused_rtt_days",
                expression="max(earned_rtt_days - rtt_days_taken, 0)",
                unit=RecalcUnit.DAYS,
            ),
            DerivedTermSpec(
                id="rtt_amount", expression="unused_rtt_days * daily_rate", unit=RecalcUnit.CURRENCY
            ),
        )
    )

    assert diagnose_row(smooth, _citation()) is None


def test_a_formula_with_no_walkthrough_still_names_the_cell() -> None:
    """The step table is the business's to author; the finding does not wait on it."""
    found = diagnose_row(_spec(walkthrough=None), _citation())

    assert found is not None
    assert found.steps == ()
    assert found.fault is not None
