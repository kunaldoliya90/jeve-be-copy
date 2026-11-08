"""The projections the HTTP contract makes: an area's badge, and one metered model call."""

from decimal import Decimal

from api.pydanticmodels.review_models import (
    CALCULATION_AREA,
    AICallCostView,
    area_status,
    plain_area_reason,
)
from review.models.recalculation import (
    BookedAmount,
    BookedMethod,
    EntryRecalculation,
    RecalcBasis,
)
from review.models.result import AICallRecord, ScoringMethod
from review.models.verdicts import CheckOutcome, Status


def _outcome(check_id: str, status: Status) -> CheckOutcome:
    return CheckOutcome(check_id=check_id, area="population", final_status=status)


def test_area_status_with_no_checks_is_unable_to_validate() -> None:
    """An area nothing ran against is unproven, which is never a quiet pass."""
    assert area_status([]) is Status.UNABLE_TO_VALIDATE


def test_area_status_takes_the_strictest_check() -> None:
    worst = area_status(
        [
            _outcome("a", Status.PASS),
            _outcome("b", Status.FAIL),
            _outcome("c", Status.PARTIAL),
        ]
    )
    assert worst is Status.FAIL


def test_area_status_is_pass_only_when_every_check_passed() -> None:
    assert area_status([_outcome("a", Status.PASS), _outcome("b", Status.PASS)]) is Status.PASS


def test_area_status_prefers_missing_evidence_over_unable_to_validate() -> None:
    """Nothing was supplied beats nothing could be concluded: the first is actionable."""
    worst = area_status(
        [_outcome("a", Status.UNABLE_TO_VALIDATE), _outcome("b", Status.MISSING_EVIDENCE)]
    )
    assert worst is Status.MISSING_EVIDENCE


def test_a_call_view_carries_the_step_that_made_it() -> None:
    view = AICallCostView.of(
        AICallRecord(
            call="AI-M",
            step="13a",
            name="Reading the methodology",
            model="gpt-4o",
            input_tokens=1_200,
            cached_tokens=800,
            output_tokens=300,
            reasoning_tokens=120,
            cost_usd=Decimal("0.0042"),
            latency_ms=1_850,
        )
    )

    assert (view.call, view.step, view.name) == ("AI-M", "13a", "Reading the methodology")
    assert view.model == "gpt-4o"
    assert view.cost_usd == Decimal("0.0042")
    assert view.latency_ms == 1_850


def test_a_call_view_totals_input_and_output_without_double_counting() -> None:
    """Cached and reasoning tokens are subsets. Adding all four would bill them twice."""
    view = AICallCostView.of(
        AICallRecord(
            call="AI-3",
            input_tokens=1_000,
            cached_tokens=900,
            output_tokens=400,
            reasoning_tokens=350,
        )
    )

    assert view.total_tokens == 1_400


def _paper(**overrides: object) -> EntryRecalculation:
    """The work paper as the vacation-accrual sample produces it."""
    fields: dict[str, object] = {
        "basis": RecalcBasis(table="employee_working", formula="days x rate"),
        "status": Status.PARTIAL,
        "recomputed_total": Decimal("27952887.64"),
        "booked": BookedAmount(method=BookedMethod.POSTING_DEBITS, total=Decimal("27905812.64")),
        "difference": Decimal("47075.00"),
        "within_tolerance": False,
        "rows_total": 2273,
        "rows_recomputed": 2273,
        "rows_agreeing": 2272,
        "rows_incomplete": 0,
    }
    fields.update(overrides)
    return EntryRecalculation(**fields)


def test_calculation_reason_states_the_difference_and_the_one_row_behind_it() -> None:
    """The sentence the card is read on: one figure, and what carries it."""
    assert plain_area_reason(CALCULATION_AREA, "weakest is ...", _paper()) == (
        "Our total is 47,075 higher than the booked amount. The cause is one employee row."
    )


def test_calculation_reason_counts_the_rows_rather_than_listing_them() -> None:
    """Several records out is a count. The per-record figures are the work paper's."""
    reason = plain_area_reason(CALCULATION_AREA, "weakest is ...", _paper(rows_agreeing=2270))
    assert reason == (
        "Our total is 47,075 higher than the booked amount. The cause is three employee rows."
    )


def test_calculation_reason_says_which_way_the_difference_runs() -> None:
    reason = plain_area_reason(CALCULATION_AREA, "...", _paper(difference=Decimal("-47075.00")))
    assert "47,075 lower than the booked amount" in reason


def test_calculation_reason_does_not_hide_a_partial_rebuild() -> None:
    """A difference read off 2,271 of 2,273 records is not the whole account of it."""
    reason = plain_area_reason(
        CALCULATION_AREA, "...", _paper(rows_recomputed=2271, rows_agreeing=2270, rows_incomplete=2)
    )
    assert reason.endswith("Two rows could not be rebuilt.")


def test_calculation_reason_is_plain_when_the_entry_ties() -> None:
    tied = _paper(
        difference=Decimal("0.00"), within_tolerance=True, rows_agreeing=2273, rows_incomplete=0
    )
    assert plain_area_reason(CALCULATION_AREA, "...", tied) == (
        "Our total matches the booked amount."
    )


def test_every_other_area_keeps_the_words_the_engine_wrote() -> None:
    """A plain wording invented for facts this function does not hold would be a guess."""
    assert plain_area_reason("period", "1 distinct fiscal period(s): 07", _paper()) == (
        "1 distinct fiscal period(s): 07"
    )
    assert plain_area_reason(CALCULATION_AREA, "weakest is ...", None) == "weakest is ..."


def test_calculation_reason_says_a_difference_is_inside_tolerance() -> None:
    """The card has to join the figure to the score beside it: 47,075 against a 100."""
    reason = plain_area_reason(CALCULATION_AREA, "...", _paper(within_tolerance=True))
    assert reason == (
        "Our total is 47,075 higher than the booked amount, inside the tolerance allowed. "
        "The cause is one employee row."
    )


def test_calculation_reason_never_writes_a_difference_of_zero() -> None:
    """Four hundredths out is not "0 lower": that reads as a tool that cannot subtract."""
    dust = _paper(difference=Decimal("-0.04"), within_tolerance=False)
    assert "0.04 lower than the booked amount" in plain_area_reason(CALCULATION_AREA, "...", dust)


def test_an_entry_tolerance_area_is_badged_from_the_work_paper() -> None:
    """The card's number comes from the rebuild, so its badge must too.

    Badging this area off its strictest check is what put a red Fail on a card reading
    100/100: two true answers to two different questions, printed as one. A record that
    could not be rebuilt is said in the card's sentence, not in its badge.
    """
    partial = _paper(
        status=Status.PARTIAL, within_tolerance=True, rows_recomputed=2271, rows_incomplete=2
    )
    badge = area_status(
        [_outcome("a", Status.FAIL), _outcome("b", Status.PASS)],
        method=ScoringMethod.ENTRY_TOLERANCE,
        recalculation=partial,
    )
    assert badge is Status.PASS


def test_an_entry_tolerance_area_fails_outside_tolerance() -> None:
    """The one case where this badge is a finding: the entry does not rebuild."""
    out = _paper(status=Status.FAIL, within_tolerance=False)
    badge = area_status(
        [_outcome("a", Status.PASS)], method=ScoringMethod.ENTRY_TOLERANCE, recalculation=out
    )
    assert badge is Status.FAIL


def test_an_entry_tolerance_area_passes_on_a_complete_rebuild() -> None:
    # The engine's own `_status`: within tolerance over every record, nothing cited.
    tied = _paper(status=Status.PASS, within_tolerance=True, rows_agreeing=2273, rows_incomplete=0)
    badge = area_status(
        [_outcome("a", Status.FAIL)], method=ScoringMethod.ENTRY_TOLERANCE, recalculation=tied
    )
    assert badge is Status.PASS


def test_an_entry_tolerance_area_falls_back_to_its_checks_without_an_answer() -> None:
    """``within_tolerance`` is None where there was nothing to compare."""
    nothing = _paper(within_tolerance=None, difference=None)
    badge = area_status(
        [_outcome("a", Status.FAIL)], method=ScoringMethod.ENTRY_TOLERANCE, recalculation=nothing
    )
    assert badge is Status.FAIL


def test_every_other_area_is_still_badged_by_its_worst_check() -> None:
    badge = area_status(
        [_outcome("a", Status.PASS), _outcome("b", Status.FAIL)],
        method=ScoringMethod.WEAKEST_CHECK,
        recalculation=_paper(within_tolerance=True),
    )
    assert badge is Status.FAIL
