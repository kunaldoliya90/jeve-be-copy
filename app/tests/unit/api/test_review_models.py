"""The projections the HTTP contract makes: an area's badge, and one metered model call."""

from decimal import Decimal

from api.pydanticmodels.review_models import AICallCostView, area_status
from review.models.result import AICallRecord
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
