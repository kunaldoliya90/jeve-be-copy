"""Step 17. The score, as pure arithmetic over the final statuses.

Same statuses, same score, every time — so two people reading the same review cannot reach
different numbers, and a result stored in February still computes to itself in March.

Four methods, because one does not fit every area. ``weakest_check`` is right where a single
failure should fail the area; at employee level it is wrong, because three bad rows in forty
thousand would score the area zero. ``exception_rate`` and ``impact_share`` exist for that.
"""

import logging
from decimal import Decimal

from review.models.context import BusinessRules, JETypeContext
from review.models.evidence import Figure
from review.models.result import AreaScore, Score, ScoringMethod
from review.models.verdicts import CheckOutcome

logger = logging.getLogger(__name__)

_FULL = 100.0


def score_review(
    je_type: JETypeContext,
    rules: BusinessRules,
    outcomes: dict[str, CheckOutcome],
    evidence_pack: dict[str, tuple[Figure, ...]],
) -> Score:
    """The weighted score, its band, and whether anything is still waiting for a person."""
    areas: list[AreaScore] = []

    for area, spec in je_type.areas.items():
        checks = [outcome for outcome in outcomes.values() if outcome.area == area]
        figures = tuple(
            figure for outcome in checks for figure in evidence_pack.get(outcome.check_id, ())
        )
        value = _score_area(spec.method, rules, checks, figures)
        areas.append(
            AreaScore(
                area=area,
                weight=spec.weight,
                method=spec.method,
                score=round(value, 1),
                reason=_reason(checks, figures),
            )
        )

    total = sum(area.score * area.weight for area in areas) / 100
    provisional = any(outcome.needs_review for outcome in outcomes.values())

    logger.info("scored total=%.1f provisional=%s areas=%d", total, provisional, len(areas))
    return Score(
        total=round(total, 1),
        band=rules.band_for(total),
        risk_rating=rules.risk_for(total),
        areas=tuple(areas),
        provisional=provisional,
    )


def _score_area(
    method: ScoringMethod,
    rules: BusinessRules,
    checks: list[CheckOutcome],
    figures: tuple[Figure, ...],
) -> float:
    if not checks:
        # A weighted area with no check contributes a score derived from nothing. The
        # context loader refuses this at startup, so reaching it means a plan went wrong.
        return 0.0

    credits = [rules.credit_for(c.final_status, needs_review=c.needs_review) for c in checks]

    match method:
        case ScoringMethod.WEAKEST_CHECK:
            return float(min(credits))
        case ScoringMethod.BASELINE_SHARE:
            return sum(credits) / len(credits)
        case ScoringMethod.EXCEPTION_RATE:
            return _by_exception_rate(figures, fallback=min(credits))
        case ScoringMethod.IMPACT_SHARE:
            return _by_impact_share(figures, fallback=min(credits))


def _by_exception_rate(figures: tuple[Figure, ...], *, fallback: float) -> float:
    """Scored on the share of rows excepted, not on the worst row.

    Three bad rows in forty thousand is a 99.99% clean population; scoring it zero would
    push a reviewer toward ignoring the area entirely.
    """
    coverages = [(figure, figure.coverage) for figure in figures if figure.coverage is not None]
    if not coverages:
        return fallback

    rows = sum(coverage.rows_total for _, coverage in coverages if coverage is not None)
    exceptions = sum(figure.exception_count for figure, _ in coverages)
    if rows == 0:
        return fallback
    return max(0.0, _FULL * (1 - min(1.0, exceptions / rows)))


def _by_impact_share(figures: tuple[Figure, ...], *, fallback: float) -> float:
    """Scored on the share of the entry's value the exceptions move.

    A one-cent difference and a half-million-euro one are not the same finding, and counting
    them alike is how a score stops meaning anything.
    """
    impacted = [figure for figure in figures if figure.financial_impact is not None]
    if not impacted:
        return fallback

    affected = sum((figure.financial_impact or Decimal(0) for figure in impacted), Decimal(0))
    base = sum(
        (max(abs(number) for number in figure.numbers) if figure.numbers else Decimal(0))
        for figure in figures
    )
    if base <= 0:
        return fallback if affected else _FULL
    share = min(Decimal(1), affected / base)
    return float(Decimal(_FULL) * (Decimal(1) - share))


def _reason(checks: list[CheckOutcome], figures: tuple[Figure, ...]) -> str:
    """One sentence a reviewer can read without translating it first: what drove this area's
    score, in the decisive figure's own words.

    Nothing else is prepended. The check count, how many are waiting on a person and which
    one was weakest used to open this sentence as a semicolon-separated tally — accurate, but
    not something a business reader parses, and it is already on the card either way: the
    tile's own badges and its check list say all of that. This is the one sentence worth
    reading in full, so it is the only one here.
    """
    if not checks:
        return "No check ran for this area."
    decisive = next((figure for figure in figures if figure.decisive and figure.summary), None)
    return _sentence(decisive.summary) if decisive else ""


def _sentence(text: str) -> str:
    """Capitalised, and ended with a full stop if it did not already have one.

    A tool's own summary is written to stand alone mid-sentence ("4 of 6 expected evidence
    layers are present"), not to open a card by itself, so this is the only polish applied —
    never a reword, since the words themselves are the tool's to own.
    """
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else f"{text}."
