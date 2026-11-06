"""Step 17's reason line: one sentence, in the decisive figure's own words.

No semicolon-separated tally, no check id - the card already shows the check count and
which ones are waiting on a person, so the only thing worth this sentence is the figure
that actually decided the score.
"""

from review.models.evidence import Figure
from review.models.verdicts import CheckOutcome, Status
from review.orchestrator.scoring import _reason


def _outcome(
    check_id: str, status: Status = Status.FAIL, *, needs_review: bool = False
) -> CheckOutcome:
    return CheckOutcome(
        check_id=check_id,
        area="calculation_accuracy",
        final_status=status,
        needs_review=needs_review,
    )


def _figure(check_id: str, summary: str, *, decisive: bool = True) -> Figure:
    return Figure(
        figure_id="F-01", check_id=check_id, tool="a_tool", decisive=decisive, summary=summary
    )


def test_reason_is_the_decisive_figures_own_sentence() -> None:
    checks = [_outcome("negative_accruals_explained")]
    figures = (_figure("negative_accruals_explained", "a negative accrual remains unexplained"),)

    assert _reason(checks, figures) == "A negative accrual remains unexplained."


def test_reason_carries_no_check_id_or_tally() -> None:
    """The one guarantee this function exists for."""
    checks = [
        _outcome("negative_accruals_explained", needs_review=True),
        _outcome("accrual_reperforms", Status.PASS),
    ]
    figures = (_figure("negative_accruals_explained", "a negative accrual remains unexplained"),)

    reason = _reason(checks, figures)

    assert "negative_accruals_explained" not in reason
    assert "check(s)" not in reason
    assert "waiting" not in reason
    assert "weakest" not in reason


def test_reason_does_not_double_a_full_stop_already_there() -> None:
    figures = (_figure("x", "The workbook states no formula for this column."),)

    assert _reason([_outcome("x")], figures) == "The workbook states no formula for this column."


def test_reason_is_empty_when_nothing_was_decisive() -> None:
    assert _reason([_outcome("x")], ()) == ""


def test_reason_ignores_a_figure_that_is_not_decisive() -> None:
    figures = (_figure("x", "a side observation", decisive=False),)

    assert _reason([_outcome("x")], figures) == ""


def test_reason_for_an_area_with_no_checks() -> None:
    assert _reason([], ()) == "No check ran for this area."
