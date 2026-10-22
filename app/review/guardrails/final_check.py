"""Step 19. Every number in the written summary must trace to something that produced it.

The last thing between a report's prose and a reader. A summary is the part of a review most
likely to be quoted onward and least likely to be checked, so each figure in it is matched
against the score, the area scores, the cited figures and the counts the harness itself
produced.

An untraced number is **listed, not removed**. Deleting it would leave a sentence that reads
as though it were verified; naming it tells the reader exactly which figure to distrust.
"""

import logging
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Final

from review.models.evidence import Figure, RowCoverage
from review.models.result import ReportPayload, Score
from review.models.verdicts import CheckOutcome

logger = logging.getLogger(__name__)

_NUMBER: Final = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")
r"""Grouped first, then plain, and neither may end on punctuation.

Both halves of that matter, and a live run proved it. A pattern whose integer part is
``[\d,]*`` matches the ``230,`` in "AH230, and" — reporting a figure that was never written,
and reporting the real ``230`` beside it as a second one. A decimal part without required
digits swallows the full stop that ends a sentence, with the same result."""

_TOLERANCE: Final = Decimal("0.01")

_IGNORE_BELOW: Final = Decimal(10)
"""Single digits in prose ("3 of 6 areas") are sentence structure, not citations. Flagging
them would fill the list with noise, and a list full of noise is a list nobody reads."""


def final_check(
    report: ReportPayload,
    score: Score,
    evidence_pack: dict[str, tuple[Figure, ...]],
    outcomes: dict[str, CheckOutcome] | None = None,
) -> ReportPayload:
    """Return the report with any untraced numbers named on it."""
    allowed = _traceable(score, evidence_pack, outcomes or {})
    prose = " ".join([report.summary, *report.gaps, *report.next_steps])

    untraced: list[str] = []
    for match in _NUMBER.findall(prose):
        try:
            value = Decimal(match.replace(",", ""))
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            continue
        if abs(value) < _IGNORE_BELOW:
            continue
        if not any(abs(value - candidate) <= _TOLERANCE for candidate in allowed):
            untraced.append(match)

    if untraced:
        logger.warning(
            "summary quotes %d number(s) that trace nowhere: %s",
            len(untraced),
            ", ".join(dict.fromkeys(untraced)),
        )

    return report.model_copy(update={"untraced_numbers": tuple(dict.fromkeys(untraced))})


def _traceable(
    score: Score,
    evidence_pack: dict[str, tuple[Figure, ...]],
    outcomes: dict[str, CheckOutcome],
) -> list[Decimal]:
    """Everything the summary is entitled to quote.

    Four sources and no others: the score, the area scores and their weights, the figures
    the checks produced, and counts over the outcomes themselves. The last of those is what
    lets a report say how many checks ran and how many failed — facts code established,
    which belong in a summary as much as any amount does.
    """
    allowed: list[Decimal] = [Decimal(str(score.total))]
    allowed.extend(Decimal(str(area.score)) for area in score.areas)
    allowed.extend(Decimal(area.weight) for area in score.areas)

    for figures in evidence_pack.values():
        for figure in figures:
            allowed.extend(figure.numbers)
            allowed.append(Decimal(figure.exception_count))
            if figure.financial_impact is not None:
                allowed.append(figure.financial_impact)
            if figure.coverage is not None:
                allowed.extend(_coverage_counts(figure.coverage))

    allowed.extend(_counts(outcomes))
    return allowed


def _coverage_counts(coverage: RowCoverage) -> list[Decimal]:
    """Row counts a report may quote, including the two it has to subtract to get.

    "1,059 rows had no comparable figure" and "37 rows differ" are the differences between
    the three counts rather than any one of them. They are as established as the counts
    themselves — a live run flagged both as untraced, which is exactly the false positive
    that teaches a reader to ignore the list.
    """
    total, bound, tied = coverage.rows_total, coverage.rows_bound, coverage.rows_tied
    return [
        Decimal(total),
        Decimal(bound),
        Decimal(tied),
        Decimal(total - bound),
        Decimal(bound - tied),
        Decimal(total - tied),
    ]


def _counts(outcomes: dict[str, CheckOutcome]) -> list[Decimal]:
    """How many checks ran, how many are waiting on a person, and how many reached each
    status. A summary opens on these, and they are no less established than an amount."""
    if not outcomes:
        return []
    by_status = Counter(outcome.final_status for outcome in outcomes.values())
    waiting = sum(1 for outcome in outcomes.values() if outcome.needs_review)
    return [
        Decimal(len(outcomes)),
        Decimal(waiting),
        Decimal(len(outcomes) - waiting),
        *(Decimal(count) for count in by_status.values()),
    ]
