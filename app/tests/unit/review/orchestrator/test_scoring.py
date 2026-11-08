"""Step 17: the reason line, and how an area turns its checks into a score.

The reason is one sentence in the decisive figure's own words - no semicolon-separated
tally, no check id, because the card already shows the check count and which ones are
waiting on a person.
"""

from decimal import Decimal

from api.consts import RiskRating
from review.models.context import Band, BusinessRules
from review.models.evidence import Figure
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
