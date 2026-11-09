"""AI-5. The next steps a reviewer reads, and the executive summary that is always code's.

The executive summary is never composed by a model: ``plain_summary`` builds it from the
score alone, one line per area that did not fully pass and one naming the ones that did. It
can never carry a cell reference, a figure citation or a tool's own schema name, because it
never reads a tool's output in the first place — only the status an area finally reached.

What a model can still add is the next steps: the specific things a person must obtain or
confirm, which benefits from reading the findings rather than just their statuses. A failure
here is the least costly in the harness, so it never fails the review — with no provider, no
answer or an unusable one, the next steps fall back to the failed checks themselves, named.
"""

import json
import logging
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from review.agents.judging import call_model
from review.context.prompts import PromptRegistry
from review.models.context import JETypeContext
from review.models.evidence import ExceptionCluster, Figure
from review.models.recalculation import EntryRecalculation
from review.models.result import ReportPayload, Score, ScoringMethod
from review.models.verdicts import CheckOutcome, Status, stricter

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 1_000
"""Next steps plus reasoning tokens. Below this a reasoning model returns nothing at all
rather than something short, and the fallback steps would stand in for no good reason."""

TOP_FINDINGS: Final = 8
MAX_LISTED: Final = 6

_FULL_SHARE: Final = 100.0
"""A whole share, on the scale the parameters are scored on."""

SUMMARY_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["next_steps"],
    "properties": {
        "next_steps": {
            "type": "array",
            "maxItems": MAX_LISTED,
            "items": {"type": "string"},
            "description": "Specific things a person must obtain or confirm",
        },
    },
}

AREA_LABELS: Final[dict[str, str]] = {
    "evidence_completeness": "Evidence Completeness",
    "evidence_appropriateness": "Evidence Appropriateness",
    "calculation_accuracy": "Calculation Accuracy",
    "pii": "PII Assessment",
    "trend": "Trend Analysis",
    "period": "Period",
}
"""The six parameters, named exactly as the report's own parameter cards name them.

Word for word, deliberately. The summary and the cards are read one after the other by the
same person, and a paragraph calling something "PII review" above a card headed "PII
Assessment" makes a reader stop and work out whether those are the same thing. An area from
an older context version falls back to its key, titled — those are read-only history, and
never worth a line here."""

_STATUS_WORDING: Final[dict[Status, str]] = {
    Status.FAIL: "exceptions were found and not resolved",
    Status.PARTIAL: "could not be fully confirmed",
    Status.MISSING_EVIDENCE: "the evidence this needs is missing",
    Status.UNABLE_TO_VALIDATE: "could not be established either way",
}
"""How each outcome reads in a sentence a business reader finishes in one pass.

No counts and no "checks". A check is the engine's unit of work, not the reader's: the
report is laid out as six parameters, the reader argues about parameters, and "1 of 5
checks found exceptions" asks them to hold a denominator that appears nowhere else on the
page. What each parameter came to is the finding; the detail sits on its own card.

Each phrase is also written to carry a word the report's own reader marks as a problem
("missing", "could not", "exceptions"). That marking can only over-mark, never under-mark,
so a phrase worded around those words would quietly read as though nothing were wrong."""


async def summarise(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    je_type: JETypeContext,
    score: Score,
    outcomes: dict[str, CheckOutcome],
    pack: dict[str, tuple[Figure, ...]],
    notes: tuple[str, ...] = (),
    clusters: dict[str, tuple[ExceptionCluster, ...]] | None = None,
    steps: tuple[str, ...] = (),
    recalculation: EntryRecalculation | None = None,
) -> tuple[ReportPayload, LLMResponse | None]:
    """The written report: the executive summary is always code's, the next steps are the
    model's where one answered.

    The headline is always code's too. It carries the score and the counts, which are the
    numbers most likely to be quoted onward, and those are not left to prose.

    ``notes`` are review-level observations code established — an uncorroborated entry type,
    for instance. They are appended to the summary either way, so a caveat a reviewer must
    see does not depend on a model choosing to repeat it. ``steps`` are the same thing for the
    next steps: an instruction that follows from how the review ran rather than from what it
    found, which a model reading only the outcomes has no way to know it must give.
    """
    headline = headline_for(score, outcomes, recalculation)
    summary = _with_notes(plain_summary(score, outcomes, recalculation), notes)
    fallback_steps = (*steps, *_fallback_next_steps(outcomes))[:MAX_LISTED]
    fallback = ReportPayload(headline=headline, summary=summary, next_steps=fallback_steps)
    if provider is None:
        return fallback, None

    response = await call_model(
        provider,
        prompts.get(AICall.SUMMARY).text,
        _payload(je_type, score, outcomes, pack, notes, clusters),
        SUMMARY_SCHEMA,
        "summary",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response is None:
        return fallback, None

    try:
        written = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("the next steps were not valid JSON, using the fallback: %s", exc)
        return fallback, response
    if not isinstance(written, dict):  # pragma: no cover - the schema forbids it
        return fallback, response

    next_steps = (*steps, *_lines(written.get("next_steps")))[:MAX_LISTED]
    return (
        ReportPayload(headline=headline, summary=summary, next_steps=next_steps or fallback_steps),
        response,
    )


def headline_for(
    score: Score,
    outcomes: dict[str, CheckOutcome],
    recalculation: EntryRecalculation | None = None,
) -> str:
    """What a reader sees first: the score, and what is still unsettled beneath it.

    Counted over the parameters rather than over the checks beneath them, in the words the
    report uses everywhere else. This line is copied out of the report into mail and
    tickets, so it has to stand on its own in front of a reader who has never seen the
    engine - and "18 checks ran" is a denominator that appears nowhere they can look.
    """
    failed = 0
    waiting = 0
    for area in score.areas:
        checks = [outcome for outcome in outcomes.values() if outcome.area == area.area]
        if not checks:
            continue
        badge = _area_status(
            checks, method=area.method, score=area.score, recalculation=recalculation
        )
        if badge is Status.FAIL:
            failed += 1
        if any(outcome.needs_review for outcome in checks):
            waiting += 1

    tested = f"{len(score.areas)} parameter{'' if len(score.areas) == 1 else 's'} tested"
    needs = "needs" if waiting == 1 else "need"
    return (
        f"{score.total} out of 100, {score.band}"
        f"{' (provisional)' if score.provisional else ''}. "
        f"{tested}; {failed} failed and {waiting} {needs} a person."
    )


def template(
    score: Score,
    outcomes: dict[str, CheckOutcome],
    headline: str,
    notes: tuple[str, ...] = (),
    steps: tuple[str, ...] = (),
    recalculation: EntryRecalculation | None = None,
) -> ReportPayload:
    """The report for when no model answers. The same summary a model-written one gets,
    since neither composes the executive summary from a tool's own output."""
    return ReportPayload(
        headline=headline,
        summary=_with_notes(plain_summary(score, outcomes, recalculation), notes),
        next_steps=(*steps, *_fallback_next_steps(outcomes))[:MAX_LISTED],
    )


def _area_status(
    checks: list[CheckOutcome],
    *,
    method: ScoringMethod | None = None,
    score: float | None = None,
    recalculation: EntryRecalculation | None = None,
) -> Status:
    """A parameter is no better than its worst check - unless its checks are not what it is
    scored on.

    Mirrors ``api.pydanticmodels.review_models.area_status`` exactly, kept as its own copy
    rather than imported: the API layer already depends on this package, and a dependency
    back the other way would be a layering mistake, not a shortcut. The two must agree,
    because they are read side by side - this one writes the sentence in the summary and
    that one badges the card the sentence is about, and a paragraph reporting exceptions
    above a card badged Pass is a report arguing with itself.

    So the same two exceptions apply here. An entry-tolerance parameter is badged on whether
    the entry rebuilt to what was posted, and a baseline-share one on how much of the
    required evidence is present - in both cases the number does not come from the checks,
    so neither does the word.
    """
    if (
        method is ScoringMethod.ENTRY_TOLERANCE
        and recalculation is not None
        and recalculation.within_tolerance is not None
    ):
        return Status.PASS if recalculation.within_tolerance else Status.FAIL

    if method is ScoringMethod.BASELINE_SHARE and score is not None:
        if score >= _FULL_SHARE:
            return Status.PASS
        return Status.FAIL if score <= 0 else Status.PARTIAL

    worst = checks[0].final_status
    for check in checks[1:]:
        worst = stricter(worst, check.final_status)
    return worst


def _joined(items: list[str]) -> str:
    """``"A"``, ``"A and B"``, or ``"A, B and C"`` — never an Oxford comma before the last."""
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"


def plain_summary(
    score: Score,
    outcomes: dict[str, CheckOutcome],
    recalculation: EntryRecalculation | None = None,
) -> str:
    """The executive summary, in plain English: one line naming each parameter that did not
    fully pass, then one naming the ones that did.

    It says the same things, about the same six parameters, in the same words the cards
    below it use — because it is read as the opening of that section rather than as a
    separate account of the same review. What it does not say is how the engine arrived
    there: no check counts, no check ids, nothing a reader would have to learn the shape of
    the engine to follow.

    Built from the score alone — each parameter's name and the outcome it reached — and
    never from a check's conclusion or a tool's figure. That is what makes it safe: there is
    no cell reference, figure citation or schema name anywhere in this function for a line to
    accidentally carry forward. Every one of those stays exactly where it belongs, in the
    sections below, next to the finding it explains.

    ``recalculation`` is read only to badge the parameter scored on it, exactly as the card
    is badged. Optional, so a caller without one still writes every other line identically.
    """
    sentences: list[str] = []
    passed: list[str] = []

    for area in score.areas:
        label = AREA_LABELS.get(area.area, area.area.replace("_", " ").capitalize())
        checks = [outcome for outcome in outcomes.values() if outcome.area == area.area]
        if not checks:
            continue

        status = _area_status(
            checks, method=area.method, score=area.score, recalculation=recalculation
        )
        if status is Status.PASS:
            passed.append(label)
            continue

        sentences.append(f"{label}: {_STATUS_WORDING[status]}.")

    if passed:
        sentences.append(f"{_joined(passed)} passed.")

    return " ".join(sentences)


def _fallback_next_steps(outcomes: dict[str, CheckOutcome]) -> tuple[str, ...]:
    """What the fallback insists on: the failed checks, named, and the waiting count."""
    failed = sorted(
        (o for o in outcomes.values() if o.final_status is Status.FAIL),
        key=lambda outcome: outcome.check_id,
    )
    waiting = sorted(
        (o for o in outcomes.values() if o.needs_review and o.final_status is not Status.FAIL),
        key=lambda outcome: outcome.check_id,
    )
    return (
        *(f"Review {outcome.check_id}, which failed." for outcome in failed),
        *((f"Settle the {len(waiting)} check(s) waiting on a person above.",) if waiting else ()),
    )


def _with_notes(prose: str, notes: tuple[str, ...]) -> str:
    """The written summary, with the caveats code established appended to it.

    These had a list of their own on the report until the report stopped carrying one. They
    are not findings a model chose to write and they are not instructions, so the summary is
    where they belong: it is the first thing read, and step 19 traces any number in them
    from there exactly as it did before.
    """
    return " ".join([prose, *notes[:MAX_LISTED]])


def _lines(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    lines = (str(item).strip() for item in raw)
    return tuple(line for line in lines if line)[:MAX_LISTED]


def _payload(
    je_type: JETypeContext,
    score: Score,
    outcomes: dict[str, CheckOutcome],
    pack: dict[str, tuple[Figure, ...]],
    notes: tuple[str, ...] = (),
    clusters: dict[str, tuple[ExceptionCluster, ...]] | None = None,
) -> dict[str, Any]:
    """What AI-5 is given: the decided results, and the findings that move the most money.

    It sees the weights here, unlike the judging calls, because by this point the score is
    already fixed — explaining a weighting it cannot change is the whole job of the step.
    """
    ranked = sorted(
        (
            (figure, check_id)
            for check_id, figures in pack.items()
            for figure in figures
            if figure.exception_count
        ),
        key=lambda pair: (pair[0].financial_impact or 0, pair[0].exception_count),
        reverse=True,
    )[:TOP_FINDINGS]

    return {
        "journal_entry_type": je_type.name,
        "review_notes": list(notes),
        "score": {
            "total": score.total,
            "band": score.band,
            "provisional": score.provisional,
            "areas": [
                {
                    "area": area.area,
                    "score": area.score,
                    "weight": area.weight,
                    "scored_on": area.method.value,
                    "why": area.reason,
                }
                for area in score.areas
            ],
        },
        "checks": [
            {
                "check_id": outcome.check_id,
                "area": outcome.area,
                "status": outcome.final_status.value,
                "needs_a_person": outcome.needs_review,
                "reasons": [flag.value for flag in outcome.review_reasons],
                "conclusion": outcome.verdict.conclusion if outcome.verdict else "",
            }
            for outcome in sorted(outcomes.values(), key=lambda item: item.check_id)
        ],
        "largest_findings": [
            {
                "check_id": check_id,
                "figure": figure.figure_id,
                "summary": figure.summary,
                "exceptions": figure.exception_count,
                "financial_impact": (
                    str(figure.financial_impact) if figure.financial_impact is not None else None
                ),
            }
            for figure, check_id in ranked
        ],
        "exceptions_grouped_by_cause": [
            {
                "check_id": check_id,
                "cause": item.cause,
                "affected": item.affected,
                "financial_impact": (
                    str(item.financial_impact) if item.financial_impact is not None else None
                ),
            }
            for check_id, found in sorted((clusters or {}).items())
            for item in found
        ],
        "personal_data_flagged": any(
            figure.tool == "pii_scan" and figure.exception_count
            for figures in pack.values()
            for figure in figures
        ),
    }


__all__ = [
    "AREA_LABELS",
    "MAX_OUTPUT_TOKENS",
    "SUMMARY_SCHEMA",
    "headline_for",
    "plain_summary",
    "summarise",
    "template",
]
