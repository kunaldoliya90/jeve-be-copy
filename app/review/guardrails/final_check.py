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

_NUMBER: Final = re.compile(
    r"(?<![A-Za-z$\d.,-])(?:-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
)
r"""A quoted figure: grouped first, then plain, never ending on punctuation, and never the
digits inside an identifier.

Every part of that was put there by a live run.

A pattern whose integer part is ``[\d,]*`` matches the ``230,`` in "AH230, and" - reporting a
figure that was never written. A decimal part without required digits swallows the full stop
that ends a sentence, with the same result.

The lookbehind is the third of them. ``AH230`` is a cell reference, ``F-12`` is a figure id
and ``FY26`` is a period: the digits in each are part of a name, not an amount anybody
claimed. Without it a report whose next step says "example cell AH230" lists 230 as a number
that traces to nothing - which tells a reviewer to distrust a figure that was never quoted,
and that is exactly the kind of noise that makes the list itself worth ignoring.

The lookbehind has to exclude digits, commas, dots and the hyphen as well as letters, not
just letters. The scanner does not give up on a blocked position; it restarts one character
later, so ``AH230`` blocked only on ``A-Z`` would still match the ``30``, and ``F-12`` would
still match the ``12``."""

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
    prose = " ".join([report.summary, *report.next_steps])

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
    """How many checks ran, how many are waiting on a person, how many reached each status,
    and how many sit in each area. A summary opens on the first three; an area's own reason
    is entitled to the last - "16 check(s); weakest is ..." is as established as a status
    count, and an area merging several checks together is exactly where that number stops
    being small enough that nobody would ever write it down."""
    if not outcomes:
        return []
    by_status = Counter(outcome.final_status for outcome in outcomes.values())
    by_area = Counter(outcome.area for outcome in outcomes.values())
    waiting = sum(1 for outcome in outcomes.values() if outcome.needs_review)
    return [
        Decimal(len(outcomes)),
        Decimal(waiting),
        Decimal(len(outcomes) - waiting),
        *(Decimal(count) for count in by_status.values()),
        *(Decimal(count) for count in by_area.values()),
    ]
