"""Step 17: the reason line, and how an area turns its checks into a score.

The reason is one sentence in the decisive figure's own words - no semicolon-separated
tally, no check id, because the card already shows the check count and which ones are
waiting on a person.
"""

from decimal import Decimal

from api.consts import RiskRating
from review.models.context import Band, BusinessRules
from review.models.evidence import Figure, RowCoverage
from review.models.recalculation import (
    BookedAmount,
    BookedMethod,
    EntryRecalculation,
    RecalcBasis,
)
from review.models.result import ScoringMethod
from review.models.verdicts import CheckOutcome, Status
from review.orchestrator.scoring import _reason, _score_area


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


_RULES = BusinessRules(
    version=1,
    credits=dict.fromkeys(Status, 0) | {Status.PASS: 100},
    bands=[Band(min=0, label="High risk", risk=RiskRating.HIGH)],
)

_FULL_CREDIT_RULES = BusinessRules(
    version=1,
    credits=dict.fromkeys(Status, 0) | {Status.PASS: 100},
    needs_review_cap=50,
    bands=[Band(min=0, label="High risk", risk=RiskRating.HIGH)],
)
"""The business's own cap, so a test can see it applied - and see it not applied."""


def _paper(**overrides: object) -> EntryRecalculation:
    fields: dict[str, object] = {
        "basis": RecalcBasis(table="employee_working", formula="days x rate"),
        "booked": BookedAmount(method=BookedMethod.POSTING_DEBITS, total=Decimal("100.00")),
        "difference": Decimal("1.00"),
        "within_tolerance": True,
        "rows_total": 10,
        "rows_recomputed": 10,
        "rows_agreeing": 9,
    }
    fields.update(overrides)
    return EntryRecalculation(**fields)


def test_entry_tolerance_passes_the_area_when_the_entry_ties() -> None:
    """The question the area is asked, answered over every record rather than one check."""
    assert (
        _score_area(
            ScoringMethod.ENTRY_TOLERANCE,
            _RULES,
            [_outcome("a", Status.FAIL)],
            (),
            _paper(),
        )
        == 100.0
    )


def test_entry_tolerance_falls_back_to_the_weakest_check_without_a_work_paper() -> None:
    """A missing answer is never read as a yes."""
    weakest = _score_area(
        ScoringMethod.ENTRY_TOLERANCE, _RULES, [_outcome("a", Status.FAIL)], (), None
    )
    assert weakest == 0.0


def test_entry_tolerance_falls_back_where_there_was_nothing_to_compare() -> None:
    """``within_tolerance`` is None when the booked side could not be read."""
    nothing = _paper(within_tolerance=None, difference=None)
    assert (
        _score_area(
            ScoringMethod.ENTRY_TOLERANCE,
            _RULES,
            [_outcome("a", Status.FAIL)],
            (),
            nothing,
        )
        == 0.0
    )


def _register(present: int, required: int) -> Figure:
    """The evidence register's figure: coverage counts the required layers only."""
    return Figure(
        figure_id="F-01",
        check_id="evidence_baseline",
        tool="evidence_register",
        ok=present > 0,
        decisive=True,
        coverage=RowCoverage(rows_total=required, rows_bound=required, rows_tied=present),
        summary=f"{present} of {required} baseline evidence layers are present",
    )


def test_baseline_share_scores_the_share_of_required_evidence_present() -> None:
    """Three of four baseline layers is three quarters of the support, not half credit."""
    assert (
        _score_area(
            ScoringMethod.BASELINE_SHARE,
            _RULES,
            [_outcome("evidence_baseline", Status.PARTIAL, needs_review=True)],
            (_register(3, 4),),
        )
        == 75.0
    )


def test_baseline_share_is_full_when_every_required_layer_is_there() -> None:
    assert (
        _score_area(
            ScoringMethod.BASELINE_SHARE,
            _RULES,
            [_outcome("evidence_baseline", Status.PASS)],
            (_register(4, 4),),
        )
        == 100.0
    )


def test_baseline_share_is_nothing_when_no_required_layer_is_there() -> None:
    assert (
        _score_area(
            ScoringMethod.BASELINE_SHARE,
            _RULES,
            [_outcome("evidence_baseline", Status.FAIL)],
            (_register(0, 4),),
        )
        == 0.0
    )


def test_baseline_share_separates_one_missing_layer_from_three() -> None:
    """The failure the old average hid: both used to score the same 50."""
    one = _score_area(
        ScoringMethod.BASELINE_SHARE,
        _RULES,
        [_outcome("evidence_baseline", Status.PARTIAL, needs_review=True)],
        (_register(3, 4),),
    )
    three = _score_area(
        ScoringMethod.BASELINE_SHARE,
        _RULES,
        [_outcome("evidence_baseline", Status.PARTIAL, needs_review=True)],
        (_register(1, 4),),
    )

    assert (one, three) == (75.0, 25.0)


def test_baseline_share_falls_back_to_the_checks_without_a_register() -> None:
    """A missing count is never read as nothing present."""
    assert (
        _score_area(
            ScoringMethod.BASELINE_SHARE,
            _RULES,
            [_outcome("evidence_baseline", Status.PASS)],
            (),
        )
        == 100.0
    )


def test_baseline_share_is_the_register_whatever_status_the_check_ended_on() -> None:
    """The register counts the layers; nothing downstream of it can re-count them.

    A model, a challenge or a routing rule may still leave this check in any status it
    likes - that is a reason to send it to a person, and it shows on the check row. What it
    must not do is change how many baseline layers the workbook actually holds.
    """
    collapsed = _score_area(
        ScoringMethod.BASELINE_SHARE,
        _RULES,
        [_outcome("evidence_baseline", Status.MISSING_EVIDENCE, needs_review=True)],
        (_register(3, 4),),
    )

    assert collapsed == 75.0


def _settled(
    check_id: str, status: Status = Status.PASS, *, needs_review: bool = False
) -> CheckOutcome:
    """A check the entry type decides by figures, and a figure decided."""
    return CheckOutcome(
        check_id=check_id,
        area="period",
        final_status=status,
        needs_review=needs_review,
        settled_by_figures=True,
    )


def test_a_model_disagreeing_cannot_halve_a_score_the_figure_settled() -> None:
    """The live defect: a card reading "Period is correct", badged Pass, scoring 50.

    The second opinion disagreed with a figures-decided check. It could not change the
    status - the figure wins - so the check stayed a pass, and then the
    waiting-for-a-person cap took half the credit anyway. The model wrote the score it was
    not allowed to write, one step further down.
    """
    full = _score_area(
        ScoringMethod.WEAKEST_CHECK,
        _FULL_CREDIT_RULES,
        [_settled("reversal_is_configured", needs_review=True)],
        (),
    )

    assert full == 100.0


def test_a_check_waiting_on_a_persons_answer_is_still_capped() -> None:
    """The cap is for a check whose answer a person owes, and that has not changed."""
    capped = _score_area(
        ScoringMethod.WEAKEST_CHECK,
        _FULL_CREDIT_RULES,
        [_outcome("posting_period_agrees", Status.PASS, needs_review=True)],
        (),
    )

    assert capped == 50.0
