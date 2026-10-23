"""AI-5. The summary a reviewer reads first, and the template that stands in for it.

The model is given what was *decided*, never what to decide: the area scores, the final
status of every check, and the findings carrying the most money. It writes prose around
numbers that are already settled, and step 19 then checks every number it wrote.

A failure here is the least costly in the harness, so it never fails the review. With no
provider, no answer or an unusable one, a template summary is assembled from the same
figures — plainer, and safe for exactly that reason: every number in it was copied from a
figure by code rather than composed around one.
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
from review.models.result import ReportPayload, Score
from review.models.verdicts import CheckOutcome, Status

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 4_000
"""Prose plus reasoning tokens. Below this a reasoning model returns nothing at all rather
than something short, and the template would stand in for no good reason."""

TOP_FINDINGS: Final = 8
MAX_LISTED: Final = 6

SUMMARY_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "next_steps"],
    "properties": {
        "summary": {
            "type": "string",
            "description": "Three to five sentences, what a reviewer must act on first",
        },
        "next_steps": {
            "type": "array",
            "maxItems": MAX_LISTED,
            "items": {"type": "string"},
            "description": "Specific things a person must obtain or confirm",
        },
    },
}


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
) -> tuple[ReportPayload, LLMResponse | None]:
    """The written report: the model's where one answered, and code's otherwise.

    The headline is always code's. It carries the score and the counts, which are the
    numbers most likely to be quoted onward, and those are not left to prose.

    ``notes`` are review-level observations code established — an uncorroborated entry type,
    for instance. They are appended to the summary either way, so a caveat a reviewer must
    see does not depend on a model choosing to repeat it. ``steps`` are the same thing for the
    next steps: an instruction that follows from how the review ran rather than from what it
    found, which a model reading only the outcomes has no way to know it must give.
    """
    headline = headline_for(score, outcomes)
    fallback = template(score, outcomes, headline, notes, steps)
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
        logger.warning("the summary was not valid JSON, using the template: %s", exc)
        return fallback, response
    if not isinstance(written, dict):  # pragma: no cover - the schema forbids it
        return fallback, response

    prose = str(written.get("summary", "")).strip()
    if not prose:
        return fallback, response

    return (
        ReportPayload(
            headline=headline,
            summary=_with_notes(prose, notes),
            next_steps=(*steps, *_lines(written.get("next_steps")))[:MAX_LISTED],
        ),
        response,
    )


def headline_for(score: Score, outcomes: dict[str, CheckOutcome]) -> str:
    """What a reader sees first: the score, and what is still unsettled beneath it."""
    failed = sum(1 for outcome in outcomes.values() if outcome.final_status is Status.FAIL)
    waiting = sum(1 for outcome in outcomes.values() if outcome.needs_review)
    return (
        f"{score.total} out of 100, {score.band}"
        f"{' (provisional)' if score.provisional else ''}. "
        f"{len(outcomes)} checks ran; {failed} failed and {waiting} need a person."
    )


def template(
    score: Score,
    outcomes: dict[str, CheckOutcome],
    headline: str,
    notes: tuple[str, ...] = (),
    steps: tuple[str, ...] = (),
) -> ReportPayload:
    """A summary assembled from the outcomes themselves, for when no model answers."""
    failed = sorted(
        (o for o in outcomes.values() if o.final_status is Status.FAIL),
        key=lambda outcome: outcome.check_id,
    )
    waiting = sorted(
        (o for o in outcomes.values() if o.needs_review and o.final_status is not Status.FAIL),
        key=lambda outcome: outcome.check_id,
    )
    weakest = min(score.areas, key=lambda area: area.score, default=None)

    sentences = [
        f"{len(outcomes)} checks ran on this entry: {len(failed)} failed and "
        f"{len(waiting) + len(failed)} need a person before the score is final."
    ]
    if weakest is not None:
        sentences.append(
            f"The weakest area is {weakest.area.replace('_', ' ')} at {weakest.score} "
            f"out of 100{f': {weakest.reason}' if weakest.reason else ''}."
        )
    if score.provisional:
        sentences.append(
            "The score is provisional: an unvalidated check holds half credit and may fall "
            "once a reviewer settles it."
        )

    return ReportPayload(
        headline=headline,
        summary=_with_notes(" ".join(sentences), notes),
        next_steps=(
            *steps,
            *(f"Review {outcome.check_id}, which failed." for outcome in failed),
            *(
                (f"Settle the {len(waiting)} check(s) waiting on a person above.",)
                if waiting
                else ()
            ),
        )[:MAX_LISTED],
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


__all__ = ["MAX_OUTPUT_TOKENS", "SUMMARY_SCHEMA", "headline_for", "summarise", "template"]
