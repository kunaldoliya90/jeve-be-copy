"""Step 13b. AI-X: thousands of row-level exceptions, read as a handful of causes.

Conditional, and off the answer path entirely. It runs only where a check's exceptions pass
the threshold the business set, and what it produces is narrative: a cause, the rows it
accounts for, and the figures it was drawn from. No status, no count and no amount is
decided here — all three were settled by the tools before this ran.

Why a model at all: grouping 888 exceptions into "137 rows where earned leave is a hardcoded
zero" and "2,136 rows that compute correctly" is a reading task over text that code would
have to be told the shape of in advance. Why it is safe: it sees only tool output, it cannot
change what it was given, and ``reconciles`` says plainly when its groups do not add up.
"""

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from api.consts import AICall
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from review.agents.judging import call_model
from review.agents.projections import figure_payload
from review.context.prompts import PromptRegistry
from review.models.context import ClusteringRules
from review.models.evidence import ExceptionCluster, Figure

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 4_000
MAX_EXAMPLE_ROWS: Final = 6

CLUSTERS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clusters"],
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "cause",
                    "affected",
                    "financial_impact",
                    "figure_ids",
                    "example_rows",
                ],
                "properties": {
                    "cause": {"type": "string", "description": "One line, descriptive"},
                    "affected": {"type": "integer", "minimum": 0},
                    "financial_impact": {"type": ["string", "null"]},
                    "figure_ids": {"type": "array", "items": {"type": "string"}},
                    "example_rows": {"type": "array", "items": {"type": "integer"}},
                },
            },
        }
    },
}


def needs_clustering(figures: tuple[Figure, ...], rules: ClusteringRules) -> bool:
    """Whether this check has enough exceptions to be worth grouping."""
    return sum(figure.exception_count for figure in figures) >= rules.min_exceptions


async def cluster(
    provider: BaseLLMProvider | None,
    prompts: PromptRegistry,
    pack: dict[str, tuple[Figure, ...]],
    rules: ClusteringRules,
) -> tuple[dict[str, tuple[ExceptionCluster, ...]], LLMResponse | None]:
    """Group the exceptions of every check that has too many to read as a list.

    One call for the whole review rather than one per check: the same cause usually shows up
    under several checks, and a reader wants it described once.
    """
    crowded = {
        check_id: figures for check_id, figures in pack.items() if needs_clustering(figures, rules)
    }
    if provider is None or not crowded:
        return {}, None

    response = await call_model(
        provider,
        prompts.get(AICall.CLUSTERING).text,
        _payload(crowded, rules),
        CLUSTERS_SCHEMA,
        "clusters",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    if response is None:
        return {}, None

    try:
        answer = json.loads(response.text)
    except json.JSONDecodeError as exc:
        logger.warning("AI-X answer was not valid JSON: %s", exc)
        return {}, response

    clusters = _read(answer, crowded, rules)
    logger.info(
        "AI-X grouped %d check(s) of exceptions into %d cause(s)",
        len(crowded),
        sum(len(found) for found in clusters.values()),
    )
    return clusters, response


def _read(
    answer: object,
    crowded: dict[str, tuple[Figure, ...]],
    rules: ClusteringRules,
) -> dict[str, tuple[ExceptionCluster, ...]]:
    """Turn the answer into clusters, attached to the checks their figures belong to.

    A cluster citing a figure id that was never sent is dropped: it is the same unverified
    citation that ``answer_check`` catches on a verdict, and it means just as little here.
    """
    if not isinstance(answer, dict):  # pragma: no cover - the schema forbids it
        return {}

    owner = {
        figure.figure_id: check_id for check_id, figures in crowded.items() for figure in figures
    }
    found: dict[str, list[ExceptionCluster]] = {}

    for raw in answer.get("clusters", []):
        if not isinstance(raw, dict):
            continue
        ids = tuple(str(value) for value in raw.get("figure_ids", []) if str(value) in owner)
        if not ids:
            logger.warning("dropping a cluster that cites no figure it was given")
            continue

        built = ExceptionCluster(
            cause=str(raw.get("cause", "")).strip() or "unexplained",
            affected=max(0, int(raw.get("affected", 0) or 0)),
            financial_impact=_amount(raw.get("financial_impact")),
            figure_ids=ids,
            example_rows=tuple(int(row) for row in raw.get("example_rows", [])[:MAX_EXAMPLE_ROWS]),
        )
        for check_id in dict.fromkeys(owner[figure_id] for figure_id in ids):
            found.setdefault(check_id, []).append(built)

    return {check_id: tuple(clusters[: rules.max_clusters]) for check_id, clusters in found.items()}


def reconciles(clusters: tuple[ExceptionCluster, ...], figures: tuple[Figure, ...]) -> bool:
    """Whether the groups account for every exception they were given.

    Checked rather than assumed, and reported rather than corrected. Groups that do not add
    up are still useful to read; a reader just needs to know they are not the whole picture.
    """
    exceptions = sum(figure.exception_count for figure in figures)
    return sum(item.affected for item in clusters) == exceptions


def _payload(crowded: dict[str, tuple[Figure, ...]], rules: ClusteringRules) -> dict[str, Any]:
    """What AI-X sees: the figures it is grouping, and nothing else about the review."""
    return {
        "at_most_this_many_causes": rules.max_clusters,
        "checks": [
            {
                "check_id": check_id,
                "total_exceptions": sum(figure.exception_count for figure in figures),
                "figures": [figure_payload(figure) for figure in figures],
            }
            for check_id, figures in sorted(crowded.items())
        ],
    }


def _amount(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return abs(Decimal(str(raw)))
    except (InvalidOperation, ValueError):
        return None


__all__ = ["CLUSTERS_SCHEMA", "cluster", "needs_clustering", "reconciles"]
