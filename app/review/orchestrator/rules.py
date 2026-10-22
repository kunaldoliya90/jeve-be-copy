"""Step 16. What the figures mean, and which checks go to a person.

Code decides this, never a model. The input is the evidence pack and the JE type's own
declaration of who settles each check; the output is a status and, where anything is
unproven, the reasons a reviewer needs to see.

The ordering below is the ordering in the design, and it is deliberate: an unproven check
must not be able to reach a pass by some later rule.
"""

import logging

from review.guardrails.answer_check import check_answer
from review.models.context import CheckSpec, DecidedBy
from review.models.evidence import Figure
from review.models.verdicts import Challenge, CheckOutcome, ReviewFlag, Status, Verdict

logger = logging.getLogger(__name__)


def outcome_for(
    check: CheckSpec,
    figures: tuple[Figure, ...],
    verdict: Verdict | None = None,
    challenge: Challenge | None = None,
) -> CheckOutcome:
    """Turn one check's figures, and any conclusion drawn on them, into its final outcome.

    Where a judging pass ran, the verdict is validated against the figures by
    ``answer_check`` and the status it earns is used. Where none ran, a judgement-decided
    check cannot be concluded at all and reports ``unable_to_validate`` — the honest result,
    since nobody has judged it, and half credit is what the rules give an unproven check.
    """
    reasons: list[ReviewFlag] = []

    if not figures:
        return CheckOutcome(
            check_id=check.id,
            area=check.area,
            final_status=Status.UNABLE_TO_VALIDATE,
            needs_review=True,
            review_reasons=(ReviewFlag.MISSING_VERDICT,),
        )

    decisive = next((figure for figure in figures if figure.decisive), figures[0])

    # --- a figure that could not be computed proves nothing, either way ---------------
    if decisive.ok is None:
        return CheckOutcome(
            check_id=check.id,
            area=check.area,
            final_status=Status.UNABLE_TO_VALIDATE,
            needs_review=True,
            review_reasons=(ReviewFlag.MAP_UNVERIFIED,),
        )

    # --- row coverage: an agreeing total over partly-bound rows is not a pass ---------
    incomplete = [
        figure for figure in figures if figure.coverage is not None and not figure.coverage.complete
    ]
    if incomplete and decisive.ok:
        reasons.append(ReviewFlag.INCOMPLETE_ROW_COVERAGE)

    # --- negative employee-level amounts always go to a person ------------------------
    if any(figure.tool == "negative_scan" and figure.exception_count for figure in figures):
        reasons.append(ReviewFlag.NEGATIVE_ACCRUAL)

    if verdict is not None:
        # A model concluded. What it said counts only as far as the figures bear it out.
        checked = check_answer(check, figures, verdict, challenge)
        status = checked.status
        reasons.extend(checked.flags)
    elif check.decided_by is DecidedBy.FIGURES:
        status = _status_from(decisive, bool(incomplete))
    else:
        # No model has run, so no judgement exists to record.
        status = Status.UNABLE_TO_VALIDATE
        reasons.append(ReviewFlag.MISSING_VERDICT)

    # Everything the figures could not settle is unproven, and unproven is never a pass.
    needs_review = bool(reasons) or status in {
        Status.UNABLE_TO_VALIDATE,
        Status.MISSING_EVIDENCE,
    }
    return CheckOutcome(
        check_id=check.id,
        area=check.area,
        final_status=status,
        needs_review=needs_review,
        review_reasons=tuple(dict.fromkeys(reasons)),
        verdict=verdict,
        challenge=challenge,
    )


def _status_from(decisive: Figure, incomplete: bool) -> Status:
    """A figures-decided check takes its status from the figure, and only from the figure."""
    if not decisive.ok:
        return Status.FAIL
    if incomplete or not decisive.supports_pass:
        # It agreed as far as it went, which is partial, not proven.
        return Status.PARTIAL
    return Status.PASS
