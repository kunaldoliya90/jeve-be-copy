"""Step 19: every number in the written summary traces, or it is named.

The behaviour that matters most here is the one that looks like a weakness: an untraced
number is reported rather than deleted. Deleting it would leave a sentence reading as though
it had been verified.
"""

from decimal import Decimal

from api.consts import RiskRating
from review.guardrails.final_check import final_check
from review.models.evidence import Figure, RowCoverage
from review.models.result import AreaScore, ReportPayload, Score, ScoringMethod
from review.models.verdicts import CheckOutcome, Status


def _score() -> Score:
    return Score(
        total=62.5,
        band="needs work",
        risk_rating=RiskRating.MEDIUM,
        areas=(
            AreaScore(
                area="accrual_reperformance",
                weight=25,
                method=ScoringMethod.WEAKEST_CHECK,
                score=100.0,
            ),
        ),
    )


def _pack() -> dict[str, tuple[Figure, ...]]:
    return {
        "accrual_reperforms": (
            Figure(
                figure_id="F-01",
                check_id="accrual_reperforms",
                tool="recalc_accrual",
                ok=True,
                decisive=True,
                exception_count=37,
                financial_impact=Decimal("12345.67"),
                coverage=RowCoverage(rows_total=2273, rows_bound=2273, rows_tied=2273),
                numbers=(Decimal("27905812.64"),),
                summary="2,273 of 2,273 rows reperformed",
            ),
        )
    }


def test_a_summary_quoting_only_cited_figures_is_left_alone() -> None:
    report = ReportPayload(
        headline="62.5 out of 100",
        summary="The accrual totals 27905812.64 across 2273 rows, with 37 exceptions.",
    )

    checked = final_check(report, _score(), _pack())

    assert checked.untraced_numbers == ()
    assert checked.summary == report.summary


def test_a_number_that_traces_nowhere_is_named_and_the_prose_is_kept() -> None:
    report = ReportPayload(summary="The accrual totals 27905999.00 across 2273 rows.")

    checked = final_check(report, _score(), _pack())

    assert checked.untraced_numbers == ("27905999.00",)
    assert "27905999.00" in checked.summary, "the sentence must not be silently edited"


def test_thousands_separators_are_matched_as_the_same_number() -> None:
    report = ReportPayload(summary="The accrual totals 27,905,812.64 over 2,273 rows.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ()


def test_the_next_steps_are_traced_as_well_as_the_summary() -> None:
    report = ReportPayload(
        summary="Reperformed in full, but a balance of 99999.99 could not be agreed.",
        next_steps=("Obtain the 88888 supporting schedule.",),
    )

    untraced = final_check(report, _score(), _pack()).untraced_numbers

    assert set(untraced) == {"99999.99", "88888"}


def test_small_counts_in_prose_are_not_treated_as_amounts() -> None:
    """ "3 of 6 areas" is a sentence, not a citation. Flagging it would train readers to
    ignore the list, which costs more than it catches."""
    report = ReportPayload(summary="3 of 6 areas scored full marks; 2 need a person.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ()


def test_the_score_itself_always_traces() -> None:
    report = ReportPayload(summary="The entry scored 62.5, and reperformance scored 100.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ()


def test_the_area_weight_traces_so_a_weighting_can_be_explained() -> None:
    report = ReportPayload(summary="Reperformance carries 25 of the 100 marks.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ()


def test_the_same_untraced_number_is_listed_once() -> None:
    report = ReportPayload(summary="54321 and again 54321.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ("54321",)


def test_a_figure_ending_a_sentence_keeps_its_own_spelling() -> None:
    """The full stop is punctuation, not a decimal point."""
    report = ReportPayload(summary="The variance was 54321. A reviewer must confirm it.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ("54321",)


def test_an_empty_report_is_unchanged() -> None:
    assert final_check(ReportPayload(), _score(), {}).untraced_numbers == ()


# --------------------------------------------------------- counts over the outcomes


def _outcomes(total: int) -> dict[str, CheckOutcome]:
    """``total`` checks, of which the first failed and the second needs a person."""
    statuses = [Status.FAIL, Status.UNABLE_TO_VALIDATE] + [Status.PASS] * (total - 2)
    return {
        f"check_{index}": CheckOutcome(
            check_id=f"check_{index}",
            area="je_reconciliation",
            final_status=status,
            needs_review=status is not Status.PASS,
        )
        for index, status in enumerate(statuses)
    }


def test_how_many_checks_ran_is_a_number_the_summary_may_quote() -> None:
    """It is a fact code established, not prose the model composed."""
    report = ReportPayload(summary="18 checks ran on this entry.")

    assert final_check(report, _score(), {}, _outcomes(18)).untraced_numbers == ()


def test_a_miscounted_total_is_still_caught() -> None:
    report = ReportPayload(summary="19 checks ran on this entry.")

    assert final_check(report, _score(), {}, _outcomes(18)).untraced_numbers == ("19",)


def test_the_number_settled_without_a_person_traces_as_well() -> None:
    outcomes = _outcomes(18)
    report = ReportPayload(summary="Of 18 checks, 16 were settled without a person.")

    assert final_check(report, _score(), {}, outcomes).untraced_numbers == ()


def test_counts_are_only_allowed_when_the_outcomes_are_supplied() -> None:
    """The traceable set is built from what it was given, never guessed at."""
    report = ReportPayload(summary="18 checks ran on this entry.")

    assert final_check(report, _score(), {}).untraced_numbers == ("18",)


# ----------------------------------------------- faults a live run found before a test did


def test_a_cell_reference_followed_by_a_comma_is_read_as_one_number() -> None:
    """A live run reported both "230," and "230" from the phrase "at AH230, and"."""
    report = ReportPayload(summary="Obtain support for the negative accrual at AH230, and ask why.")

    untraced = final_check(report, _score(), _pack()).untraced_numbers

    assert untraced == ("230",), "a comma is punctuation here, not a grouping separator"


def test_a_grouping_separator_still_reads_as_one_number() -> None:
    """The fix above must not break a properly grouped figure."""
    report = ReportPayload(summary="The accrual totals 27,905,812.64.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ()


def test_a_malformed_group_is_not_silently_joined() -> None:
    """``1,05`` is not a thousands group, so it reads as 1 and 5, both below the floor."""
    report = ReportPayload(summary="A count of 1,05 appears here.")

    assert final_check(report, _score(), _pack()).untraced_numbers == ()


def test_rows_that_could_not_be_compared_is_a_number_the_report_may_quote() -> None:
    """2,273 total less 1,214 compared is 1,059 — derived, and no less established."""
    figures = (
        Figure(
            figure_id="F-02",
            check_id="leave_balance_recalculates",
            tool="recalc_leave_balance",
            ok=False,
            decisive=True,
            coverage=RowCoverage(rows_total=2273, rows_bound=1214, rows_tied=1177),
            summary="1,177 of 1,214 compared rows agree",
        ),
    )
    report = ReportPayload(
        summary="1,059 rows had no comparable figure and 37 of those compared differ."
    )

    untraced = final_check(report, _score(), {"leave_balance_recalculates": figures})

    assert untraced.untraced_numbers == ()


def test_a_derived_count_that_is_wrong_is_still_caught() -> None:
    figures = (
        Figure(
            figure_id="F-02",
            check_id="leave_balance_recalculates",
            tool="recalc_leave_balance",
            ok=False,
            decisive=True,
            coverage=RowCoverage(rows_total=2273, rows_bound=1214, rows_tied=1177),
            summary="x",
        ),
    )
    report = ReportPayload(summary="1,060 rows had no comparable figure.")

    checked = final_check(report, _score(), {"leave_balance_recalculates": figures})

    assert checked.untraced_numbers == ("1,060",)
