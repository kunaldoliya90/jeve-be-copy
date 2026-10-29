"""AI-5 and the template that stands in for it.

The template carries the whole load whenever no key is configured, which is every
deterministic run, so it is tested as a first-class path rather than a fallback.
"""

from decimal import Decimal

from api.consts import RiskRating
from review.agents.summary import SUMMARY_SCHEMA, headline_for, template
from review.models.result import AreaScore, Score, ScoringMethod
from review.models.verdicts import CheckOutcome, ReviewFlag, Status


def _score(*, total: float = 62.5, provisional: bool = True) -> Score:
    return Score(
        total=total,
        band="needs work",
        risk_rating=RiskRating.MEDIUM,
        provisional=provisional,
        areas=(
            AreaScore(
                area="accrual_reperformance",
                weight=25,
                method=ScoringMethod.WEAKEST_CHECK,
                score=100.0,
                reason="every row reperformed",
            ),
            AreaScore(
                area="population",
                weight=15,
                method=ScoringMethod.EXCEPTION_RATE,
                score=50.0,
                reason="the employee reference is redacted",
            ),
        ),
    )


def _outcomes() -> dict[str, CheckOutcome]:
    return {
        "accrual_reperforms": CheckOutcome(
            check_id="accrual_reperforms", area="accrual_reperformance", final_status=Status.PASS
        ),
        "population_complete": CheckOutcome(
            check_id="population_complete",
            area="population",
            final_status=Status.UNABLE_TO_VALIDATE,
            needs_review=True,
            review_reasons=(ReviewFlag.MISSING_VERDICT,),
        ),
        "je_balances": CheckOutcome(
            check_id="je_balances",
            area="je_reconciliation",
            final_status=Status.FAIL,
            needs_review=True,
        ),
    }


def test_headline_for_reports_the_score_and_what_is_unsettled() -> None:
    headline = headline_for(_score(), _outcomes())

    assert "62.5 out of 100" in headline
    assert "(provisional)" in headline
    assert "3 checks ran; 1 failed and 2 need a person." in headline


def test_headline_for_omits_provisional_when_the_score_is_final() -> None:
    assert "provisional" not in headline_for(_score(provisional=False), _outcomes())


def test_template_names_the_weakest_area_and_quotes_its_reason() -> None:
    report = template(_score(), _outcomes(), "head")

    assert report.headline == "head"
    assert "population at 50.0" in report.summary
    assert "the employee reference is redacted" in report.summary
    assert "provisional" in report.summary


def test_template_lists_failures_and_the_waiting_count_as_next_steps() -> None:
    report = template(_score(), _outcomes(), "head")

    assert report.next_steps == (
        "Review je_balances, which failed.",
        "Settle the 1 check(s) waiting on a person above.",
    )


def test_template_appends_the_caveats_code_established_to_the_summary() -> None:
    """They had a list of their own until the report stopped carrying one.

    A caveat a reviewer must see cannot depend on a model repeating it, so code writes it
    either way - and with no list left to write it into, it belongs in the prose read first.
    """
    report = template(_score(), _outcomes(), "head", notes=("The entry type is uncorroborated.",))

    assert report.summary.endswith("The entry type is uncorroborated.")
    assert report.summary.startswith("3 checks ran on this entry")


def test_template_puts_the_steps_code_insists_on_first() -> None:
    """A caveat that follows from how the review ran, not from what it found."""
    report = template(_score(), _outcomes(), "head", steps=("Confirm the entry type.",))

    assert report.next_steps[0] == "Confirm the entry type."


def test_template_never_quotes_a_number_it_was_not_given() -> None:
    """Every figure in the template is copied from the score, which is what makes it safe."""
    report = template(_score(total=0.0), {}, "head")

    assert report.summary.startswith("0 checks ran on this entry: 0 failed and 0 need a person")
    assert report.next_steps == ()


def test_template_survives_a_score_with_no_areas() -> None:
    bare = Score(total=100.0, band="clean", risk_rating=RiskRating.LOW, provisional=False, areas=())

    report = template(bare, {}, "head")

    assert "weakest area" not in report.summary


def test_summary_schema_is_strict_so_the_model_cannot_add_fields() -> None:
    assert SUMMARY_SCHEMA["additionalProperties"] is False
    assert set(SUMMARY_SCHEMA["required"]) == {"summary", "next_steps"}


def test_the_template_carries_no_financial_figures_of_its_own() -> None:
    """It summarises statuses. Amounts belong to the figures, and the model cites those."""
    report = template(_score(), _outcomes(), "head")

    assert str(Decimal("27905812.64")) not in report.summary
