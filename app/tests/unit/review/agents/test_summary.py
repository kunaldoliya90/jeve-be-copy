"""AI-5's next steps, and the executive summary that is always code's.

The template carries the whole load whenever no key is configured, which is every
deterministic run, so it is tested as a first-class path rather than a fallback. The
executive summary itself never varies with whether a model answered - ``plain_summary`` is
what both ``template`` and ``summarise`` build it from - so it gets its own tests, independent
of either caller.
"""

import re
from decimal import Decimal

from api.consts import RiskRating
from review.agents.summary import SUMMARY_SCHEMA, headline_for, plain_summary, template
from review.models.result import AreaScore, Score, ScoringMethod
from review.models.verdicts import CheckOutcome, ReviewFlag, Status


def _score(*, total: float = 62.5, provisional: bool = True, baseline: float = 50.0) -> Score:
    return Score(
        total=total,
        band="needs work",
        risk_rating=RiskRating.MEDIUM,
        provisional=provisional,
        areas=(
            AreaScore(
                area="calculation_accuracy",
                weight=60,
                method=ScoringMethod.WEAKEST_CHECK,
                score=100.0,
                reason="every row reperformed",
            ),
            AreaScore(
                area="evidence_completeness",
                weight=40,
                method=ScoringMethod.BASELINE_SHARE,
                score=baseline,
                reason="2 of 6 layers present",
            ),
        ),
    )


def _outcomes() -> dict[str, CheckOutcome]:
    return {
        "accrual_reperforms": CheckOutcome(
            check_id="accrual_reperforms", area="calculation_accuracy", final_status=Status.PASS
        ),
        "evidence_documented": CheckOutcome(
            check_id="evidence_documented",
            area="evidence_completeness",
            final_status=Status.UNABLE_TO_VALIDATE,
            needs_review=True,
            review_reasons=(ReviewFlag.MISSING_VERDICT,),
        ),
        "evidence_approved": CheckOutcome(
            check_id="evidence_approved",
            area="evidence_completeness",
            final_status=Status.FAIL,
            needs_review=True,
        ),
    }


def test_headline_for_reports_the_score_and_what_is_unsettled() -> None:
    """Counted over the parameters the report shows, in the words it shows them in.

    This line is copied into mail and tickets, so a denominator a reader cannot find
    anywhere on the report - "3 checks ran" - is a denominator they cannot check.
    """
    headline = headline_for(_score(), _outcomes())

    assert "62.5 out of 100" in headline
    assert "(provisional)" in headline
    assert "2 parameters tested; 0 failed and 1 needs a person." in headline


def test_the_headline_never_says_check() -> None:
    assert "check" not in headline_for(_score(), _outcomes()).lower()


def test_headline_for_omits_provisional_when_the_score_is_final() -> None:
    assert "provisional" not in headline_for(_score(provisional=False), _outcomes())


# ------------------------------------------------------------------------ plain_summary


def test_plain_summary_names_the_parameter_that_did_not_pass() -> None:
    """Named as the card names it, and said in one plain clause."""
    prose = plain_summary(_score(), _outcomes())

    assert "Evidence Completeness: could not be fully confirmed." in prose


def test_plain_summary_never_says_check() -> None:
    """The reader's unit is the parameter. A check is the engine's, and it is not theirs
    to learn in order to read their own report."""
    prose = plain_summary(_score(), _outcomes())

    assert "check" not in prose.lower()


def test_plain_summary_carries_no_count_a_reader_cannot_find() -> None:
    """ "2 of 2" was counting checks, and nothing on the report showed a reader those two."""
    assert not re.search(r"\d+ of \d+", plain_summary(_score(), _outcomes()))


def test_plain_summary_names_the_parameters_that_passed() -> None:
    assert "Calculation Accuracy passed." in plain_summary(_score(), _outcomes())


def test_plain_summary_names_every_parameter_passed_when_nothing_failed() -> None:
    outcomes = {
        "accrual_reperforms": CheckOutcome(
            check_id="accrual_reperforms", area="calculation_accuracy", final_status=Status.PASS
        ),
        "evidence_documented": CheckOutcome(
            check_id="evidence_documented", area="evidence_completeness", final_status=Status.PASS
        ),
    }

    prose = plain_summary(_score(baseline=100.0), outcomes)

    assert prose == "Calculation Accuracy and Evidence Completeness passed."


def test_plain_summary_words_each_status_differently() -> None:
    def _area(status: Status) -> dict[str, CheckOutcome]:
        return {
            "the_check": CheckOutcome(
                check_id="the_check", area="calculation_accuracy", final_status=status
            )
        }

    score = Score(
        total=50.0,
        band="needs work",
        risk_rating=RiskRating.MEDIUM,
        provisional=True,
        areas=(
            AreaScore(
                area="calculation_accuracy",
                weight=100,
                method=ScoringMethod.WEAKEST_CHECK,
                score=0.0,
            ),
        ),
    )

    assert "exceptions were found and not resolved" in plain_summary(score, _area(Status.FAIL))
    assert "could not be fully confirmed" in plain_summary(score, _area(Status.PARTIAL))
    assert "could not be established" in plain_summary(score, _area(Status.UNABLE_TO_VALIDATE))
    assert "the evidence this needs is missing" in plain_summary(
        score, _area(Status.MISSING_EVIDENCE)
    )


def test_plain_summary_carries_no_cell_reference_citation_or_schema_name() -> None:
    """The one guarantee the whole function exists for."""
    prose = plain_summary(_score(), _outcomes())

    assert not re.search(r"\([A-Z]{1,3}-\d{1,3}\)", prose), "no figure citation, e.g. (F-01)"
    assert "!" not in prose, "no cell reference, e.g. GPAY!E1"
    assert "_" not in prose, "no snake_case schema or tool identifier"


def test_plain_summary_is_empty_for_a_score_with_no_areas() -> None:
    bare = Score(total=100.0, band="clean", risk_rating=RiskRating.LOW, provisional=False, areas=())

    assert plain_summary(bare, {}) == ""


def test_plain_summary_falls_back_to_a_titled_key_for_an_unmapped_area() -> None:
    score = Score(
        total=50.0,
        band="needs work",
        risk_rating=RiskRating.MEDIUM,
        provisional=True,
        areas=(
            AreaScore(
                area="je_reconciliation",
                weight=100,
                method=ScoringMethod.WEAKEST_CHECK,
                score=0.0,
            ),
        ),
    )
    outcomes = {"x": CheckOutcome(check_id="x", area="je_reconciliation", final_status=Status.FAIL)}

    assert "Je reconciliation:" in plain_summary(score, outcomes)


# ----------------------------------------------------------------------------- template


def test_template_reuses_plain_summary() -> None:
    report = template(_score(), _outcomes(), "head")

    assert report.headline == "head"
    assert report.summary.startswith(plain_summary(_score(), _outcomes()))


def test_template_lists_failures_and_the_waiting_count_as_next_steps() -> None:
    report = template(_score(), _outcomes(), "head")

    assert report.next_steps == (
        "Review evidence_approved, which failed.",
        "Settle the 1 check(s) waiting on a person above.",
    )


def test_template_appends_the_caveats_code_established_to_the_summary() -> None:
    """They had a list of their own until the report stopped carrying one.

    A caveat a reviewer must see cannot depend on a model repeating it, so code writes it
    either way - and with no list left to write it into, it belongs in the prose read first.
    """
    report = template(_score(), _outcomes(), "head", notes=("The entry type is uncorroborated.",))

    assert report.summary.endswith("The entry type is uncorroborated.")


def test_template_puts_the_steps_code_insists_on_first() -> None:
    """A caveat that follows from how the review ran, not from what it found."""
    report = template(_score(), _outcomes(), "head", steps=("Confirm the entry type.",))

    assert report.next_steps[0] == "Confirm the entry type."


def test_template_survives_a_score_with_no_areas() -> None:
    bare = Score(total=100.0, band="clean", risk_rating=RiskRating.LOW, provisional=False, areas=())

    report = template(bare, {}, "head")

    assert report.summary == ""
    assert report.next_steps == ()


def test_summary_schema_is_strict_so_the_model_cannot_add_fields() -> None:
    assert SUMMARY_SCHEMA["additionalProperties"] is False
    assert set(SUMMARY_SCHEMA["required"]) == {"next_steps"}


def test_the_template_carries_no_financial_figures_of_its_own() -> None:
    """It summarises statuses. Amounts belong to the figures, and next steps cite those."""
    report = template(_score(), _outcomes(), "head")

    assert str(Decimal("27905812.64")) not in report.summary
