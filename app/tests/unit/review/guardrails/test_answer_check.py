"""Step 16's guardrail: who gets to set a check's status once a model has spoken.

The rule the rest of this file exists to protect: on a check the entry type declares
``decided_by: figures``, the figure decides. A model may say it disagrees - and saying so
sends the check to a person - but it may not write the status, and neither may the second
opinion asked to read the same figures again.
"""

from decimal import Decimal

from review.guardrails.answer_check import check_answer
from review.models.context import CheckRecipe, CheckSpec, DecidedBy
from review.models.evidence import Figure, RowCoverage
from review.models.verdicts import Challenge, Finding, ReviewFlag, Status, Verdict


def _check(decided_by: DecidedBy = DecidedBy.FIGURES) -> CheckSpec:
    return CheckSpec(
        id="evidence_baseline",
        area="evidence_completeness",
        question="Are the required evidence items present?",
        decided_by=decided_by,
        recipe=CheckRecipe(tool="evidence_register"),
    )


def _register(present: int = 3, required: int = 4) -> tuple[Figure, ...]:
    """The evidence register with one baseline layer missing: partial, by coverage."""
    return (
        Figure(
            figure_id="F-01",
            check_id="evidence_baseline",
            tool="evidence_register",
            ok=True,
            decisive=True,
            coverage=RowCoverage(rows_total=required, rows_bound=required, rows_tied=present),
            summary=f"{present} of {required} baseline evidence layers are present",
        ),
    )


def _verdict(status: Status) -> Verdict:
    return Verdict(
        check_id="evidence_baseline",
        status=status,
        conclusion="The approval is not in the workbook.",
        findings=(
            Finding(
                kind="exception",
                severity="medium",
                text="No approval sheet was found.",
                evidence_ids=("F-01",),
            ),
        ),
    )


def _challenge(status: Status) -> Challenge:
    return Challenge(
        check_id="evidence_baseline",
        agrees=False,
        status_should_be=status,
        reason="Reading the same register, the approval is simply absent.",
    )


def test_the_figure_overrides_a_model_that_read_it_differently() -> None:
    problems = check_answer(
        _check(), _register(), _verdict(Status.MISSING_EVIDENCE), _challenge(Status.PARTIAL)
    )

    assert problems.status is Status.PARTIAL
    assert ReviewFlag.STATUS_OVERRIDDEN in problems.flags


def test_a_challenge_cannot_put_back_the_status_the_figure_overrode() -> None:
    """The defect this guards: three of four baseline layers reported as "no evidence".

    The figures-wins rule set the check to partial, and the second opinion - reading the
    same register - handed the model's own answer straight back, because the stricter of
    the two wins and ``missing_evidence`` is stricter than ``partial``.
    """
    problems = check_answer(
        _check(),
        _register(),
        _verdict(Status.MISSING_EVIDENCE),
        _challenge(Status.MISSING_EVIDENCE),
    )

    assert problems.status is Status.PARTIAL, "the figure settled this, not the challenge"


def test_a_disagreement_still_sends_a_figures_decided_check_to_a_person() -> None:
    """Nothing is discarded. The challenge is a reason to look, which is what the flag is."""
    problems = check_answer(
        _check(), _register(), _verdict(Status.PARTIAL), _challenge(Status.FAIL)
    )

    assert problems.status is Status.PARTIAL
    assert ReviewFlag.CHALLENGE_DISAGREED in problems.flags
    assert not problems.clean


def test_a_challenge_still_tightens_a_check_the_figures_did_not_settle() -> None:
    """A judgement-decided check is the model's to answer, so the second opinion counts."""
    problems = check_answer(
        _check(DecidedBy.JUDGEMENT), _register(), _verdict(Status.PARTIAL), _challenge(Status.FAIL)
    )

    assert problems.status is Status.FAIL
    assert ReviewFlag.CHALLENGE_DISAGREED in problems.flags


def test_a_challenge_never_loosens_a_status() -> None:
    problems = check_answer(
        _check(DecidedBy.JUDGEMENT), _register(), _verdict(Status.FAIL), _challenge(Status.PASS)
    )

    assert problems.status is Status.FAIL


# --------------------------------------------------------------- what a conclusion may quote


def _period_figure() -> tuple[Figure, ...]:
    """The period tool's figure: one curated number, and the counts it put in its samples."""
    return (
        Figure(
            figure_id="F-01",
            check_id="posting_period_agrees",
            tool="period_agreement",
            ok=True,
            decisive=True,
            summary="1 distinct fiscal period(s) on the entry: 07; 1 fiscal year(s)",
            numbers=(Decimal(1),),
            samples=({"period": "07", "fiscal_year": "2026", "lines": 428},),
        ),
    )


def _judged(conclusion: str) -> Verdict:
    return Verdict(
        check_id="posting_period_agrees",
        status=Status.PASS,
        conclusion=conclusion,
        findings=(),
    )


def test_a_conclusion_may_quote_a_number_from_the_samples_it_was_shown() -> None:
    """The live defect: "428 lines" came out of the tool's own exemplar and was flagged.

    The judging payload sends those exemplars, so the model is quoting the evidence it was
    handed. Calling that untraced sent a passing check to a person and - through the
    waiting-for-a-person cap - halved the parameter's score to 50 on a card reading
    "Period is correct".
    """
    problems = check_answer(
        _check(DecidedBy.JUDGEMENT),
        _period_figure(),
        _judged("The entry carries 1 fiscal period, with period 07 on 428 lines."),
        Challenge(check_id="posting_period_agrees", agrees=True),
    )

    assert ReviewFlag.UNTRACED_NUMBER not in problems.flags
    assert problems.clean
    assert problems.status is Status.PASS


def test_a_number_no_figure_produced_is_still_untraced() -> None:
    """The guard still guards: 9,999 is in no figure, no sample and no count."""
    problems = check_answer(
        _check(DecidedBy.JUDGEMENT),
        _period_figure(),
        _judged("The entry carries 1 fiscal period, with period 07 on 9,999 lines."),
        Challenge(check_id="posting_period_agrees", agrees=True),
    )

    assert ReviewFlag.UNTRACED_NUMBER in problems.flags
