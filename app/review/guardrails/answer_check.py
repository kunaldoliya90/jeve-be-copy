"""Step 16's guardrail: validate every model answer against the figures it claims to rest on.

No model output reaches a report unchecked. Each rule below catches a specific way a fluent
answer can be wrong, and every one of them flags the check for a person rather than silently
correcting it — a reviewer needs to know the system and the model disagreed.

Nothing here calls a model, by design. A guardrail that asked a model to check a model would
share its failure modes.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Final

from review.models.context import CheckSpec, DecidedBy
from review.models.evidence import Figure
from review.models.verdicts import Challenge, ReviewFlag, Status, Verdict, stricter

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")
"""Numbers as they appear in prose. Thousands separators are stripped before comparison."""

_TRACE_TOLERANCE: Final = Decimal("0.01")
"""A conclusion may round a figure when quoting it. It may not invent one."""

_IGNORE_BELOW: Final = Decimal(10)
"""Small integers in prose are counts and row numbers ("2 of 3 agree"), not amounts. Tracing
them would flag every well-written conclusion."""


class AnswerProblems:
    """What was wrong with one model answer, and the status it leaves the check in."""

    __slots__ = ("flags", "status")

    def __init__(self, status: Status, flags: list[ReviewFlag]) -> None:
        self.status = status
        self.flags = flags

    @property
    def clean(self) -> bool:
        return not self.flags


def check_answer(
    check: CheckSpec,
    figures: tuple[Figure, ...],
    verdict: Verdict | None,
    challenge: Challenge | None,
) -> AnswerProblems:
    """Validate a verdict and its challenge, returning the status the check actually earns."""
    flags: list[ReviewFlag] = []

    if verdict is None:
        return AnswerProblems(Status.UNABLE_TO_VALIDATE, [ReviewFlag.MISSING_VERDICT])

    known = {figure.figure_id for figure in figures}
    quotable = _quotable_numbers(figures)

    # --- every citation must name a figure that exists -------------------------------
    cited = {ref for finding in verdict.findings for ref in finding.evidence_ids}
    if cited - known:
        logger.info("check %s cites unknown figures %s", check.id, sorted(cited - known))
        flags.append(ReviewFlag.UNVERIFIED_CITATION)

    # --- every number must come from a figure ----------------------------------------
    prose = " ".join([verdict.conclusion, *(finding.text for finding in verdict.findings)])
    if _untraced(prose, quotable):
        flags.append(ReviewFlag.UNTRACED_NUMBER)

    # --- a failure must point at something --------------------------------------------
    if verdict.status is Status.FAIL and not any(
        finding.kind == "exception" for finding in verdict.findings
    ):
        flags.append(ReviewFlag.UNSUPPORTED_FAIL)

    status = verdict.status

    # --- a figures-decided check takes its status from the figure, not the model ------
    if check.decided_by is DecidedBy.FIGURES:
        decisive = next((figure for figure in figures if figure.decisive), None)
        settled = _status_from_figure(decisive)
        if settled is not None and settled is not status:
            logger.info(
                "check %s: model said %s, the figure says %s; the figure wins",
                check.id,
                status.value,
                settled.value,
            )
            flags.append(ReviewFlag.STATUS_OVERRIDDEN)
            status = settled

    # --- a pass needs a figure that actually passed, covering every row ---------------
    if status is Status.PASS:
        supporting = [figure for figure in figures if figure.supports_pass]
        if not supporting:
            flags.append(ReviewFlag.UNSUPPORTED_PASS)
            status = Status.PARTIAL
        elif any(
            figure.coverage is not None and not figure.coverage.complete for figure in figures
        ):
            flags.append(ReviewFlag.INCOMPLETE_ROW_COVERAGE)
            status = Status.PARTIAL
        elif any(figure.decisive and figure.ok is False for figure in figures):
            flags.append(ReviewFlag.PASS_CONTRADICTS_FIGURES)
            status = Status.PARTIAL

    # --- the second opinion ------------------------------------------------------------
    if challenge is None:
        flags.append(ReviewFlag.CHALLENGE_MISSING)
    elif not challenge.agrees and challenge.status_should_be is not None:
        # The stricter of the two, always. A disagreement is a reason to look, not to pick.
        tightened = stricter(status, challenge.status_should_be)
        if tightened is not status:
            logger.info(
                "check %s tightened from %s to %s by the challenge",
                check.id,
                status.value,
                tightened.value,
            )
            status = tightened
        flags.append(ReviewFlag.CHALLENGE_DISAGREED)

    return AnswerProblems(status, list(dict.fromkeys(flags)))


def _status_from_figure(decisive: Figure | None) -> Status | None:
    if decisive is None:
        return None
    if decisive.ok is None:
        return Status.UNABLE_TO_VALIDATE
    if not decisive.ok:
        return Status.FAIL
    return Status.PASS if decisive.supports_pass else Status.PARTIAL


def _quotable_numbers(figures: tuple[Figure, ...]) -> list[Decimal]:
    """Everything a conclusion is allowed to say, including the counts tools measured."""
    allowed: list[Decimal] = []
    for figure in figures:
        allowed.extend(figure.numbers)
        allowed.append(Decimal(figure.exception_count))
        if figure.financial_impact is not None:
            allowed.append(figure.financial_impact)
        if figure.coverage is not None:
            allowed.extend(
                Decimal(value)
                for value in (
                    figure.coverage.rows_total,
                    figure.coverage.rows_bound,
                    figure.coverage.rows_tied,
                )
            )
    return allowed


def _untraced(prose: str, allowed: list[Decimal]) -> bool:
    """Whether the prose quotes a number no figure produced."""
    for match in _NUMBER.findall(prose):
        try:
            value = Decimal(match.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            continue
        if abs(value) < _IGNORE_BELOW:
            continue
        if not any(abs(value - candidate) <= _TRACE_TOLERANCE for candidate in allowed):
            logger.info("conclusion quotes %s, which no figure produced", value)
            return True
    return False
