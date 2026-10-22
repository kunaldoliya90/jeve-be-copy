"""AI-3 Judge and AI-4 Challenge: the two calls that conclude, and the rules around them.

Both are single-shot, strictly schema'd, and given only a projection of the case file. What
they return is a *proposal*: nothing either says reaches a report before the guardrails have
checked it against the figures.

The whole layer is optional. With no API key configured, ``judge`` and ``challenge`` return
nothing and every judgement check reports as unproven — a review that is honestly incomplete
rather than one that quietly invented conclusions.
"""

import json
import logging
from typing import Any, Final

from api.consts import AICall, OpenAIModel, ReasoningEffort
from api.exceptions.llm_exceptions import LLMProviderError
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMImage, LLMRequest, LLMResponse
from review.agents.projections import challenge_input, judge_input
from review.agents.schemas import CHALLENGES_SCHEMA, VERDICTS_SCHEMA
from review.context.prompts import PromptRegistry
from review.models.context import BusinessRules, CheckSpec, JETypeContext
from review.models.evidence import ExceptionCluster, Figure
from review.models.verdicts import Challenge, Status, Verdict, stricter

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS: Final = 6_000
"""Covers reasoning tokens as well as visible ones on a reasoning model, so a low cap plus
real thinking would return an empty answer rather than a short one."""

JUDGING_EFFORT: Final = ReasoningEffort.LOW
"""Weighing supplied figures against stated rules. The arithmetic is already done."""


class JudgingResult:
    """What one judging pass produced, and what it cost."""

    __slots__ = ("calls", "challenges", "verdicts")

    def __init__(
        self,
        verdicts: dict[str, Verdict] | None = None,
        challenges: dict[str, Challenge] | None = None,
        calls: tuple[LLMResponse, ...] = (),
    ) -> None:
        self.verdicts = verdicts or {}
        self.challenges = challenges or {}
        self.calls = calls

    @property
    def ran(self) -> bool:
        return bool(self.calls)


async def judge(
    provider: BaseLLMProvider,
    prompts: PromptRegistry,
    je_type: JETypeContext,
    rules: BusinessRules,
    checks: list[CheckSpec],
    pack: dict[str, tuple[Figure, ...]],
    facts: dict[str, Any],
    clusters: dict[str, tuple[ExceptionCluster, ...]] | None = None,
) -> tuple[dict[str, Verdict], LLMResponse | None]:
    """AI-3. One conclusion per check, from the figures and the agreed rules."""
    if not checks:
        return {}, None

    prompt = prompts.get(AICall.JUDGE)
    payload = judge_input(je_type, rules, checks, pack, facts, clusters)
    response = await call_model(provider, prompt.text, payload, VERDICTS_SCHEMA, "verdicts")
    if response is None:
        return {}, None

    verdicts: dict[str, Verdict] = {}
    for raw in _items(response.text, "verdicts"):
        try:
            verdict = Verdict.model_validate(raw)
        except ValueError as exc:
            logger.warning("discarding a malformed verdict: %s", exc)
            continue
        verdicts[verdict.check_id] = verdict

    logger.info("AI-3 returned %d verdict(s) for %d check(s)", len(verdicts), len(checks))
    return verdicts, response


async def challenge(
    provider: BaseLLMProvider,
    prompts: PromptRegistry,
    je_type: JETypeContext,
    rules: BusinessRules,
    checks: list[CheckSpec],
    pack: dict[str, tuple[Figure, ...]],
    verdicts: dict[str, Verdict],
) -> tuple[dict[str, Challenge], LLMResponse | None]:
    """AI-4. A second reading of the same evidence, which may only tighten a conclusion."""
    if not verdicts:
        return {}, None

    prompt = prompts.get(AICall.CHALLENGE)
    payload = challenge_input(je_type, rules, checks, pack, verdicts)
    response = await call_model(provider, prompt.text, payload, CHALLENGES_SCHEMA, "challenges")
    if response is None:
        return {}, None

    challenges: dict[str, Challenge] = {}
    for raw in _items(response.text, "challenges"):
        try:
            proposed = Challenge.model_validate(raw)
        except ValueError as exc:
            logger.warning("discarding a malformed challenge: %s", exc)
            continue
        challenges[proposed.check_id] = _clamp(proposed, verdicts.get(proposed.check_id))

    logger.info("AI-4 returned %d challenge(s)", len(challenges))
    return challenges, response


def _clamp(proposed: Challenge, verdict: Verdict | None) -> Challenge:
    """A challenge may tighten a conclusion and never loosen one.

    Enforced here rather than asked for in the prompt: a looser status is discarded and
    logged, so the worst a drifting second opinion can do is agree. Without this, the step
    that exists to catch an over-generous pass could grant one.
    """
    if verdict is None or proposed.status_should_be is None:
        return proposed

    tightest = stricter(verdict.status, proposed.status_should_be)
    if tightest is proposed.status_should_be:
        return proposed

    logger.warning(
        "challenge on %s proposed %s, looser than the verdict %s; discarded",
        proposed.check_id,
        proposed.status_should_be.value,
        verdict.status.value,
    )
    return proposed.model_copy(update={"status_should_be": None, "agrees": True})


async def call_model(
    provider: BaseLLMProvider,
    instructions: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    name: str,
    *,
    max_output_tokens: int = MAX_OUTPUT_TOKENS,
    effort: ReasoningEffort = JUDGING_EFFORT,
    image: LLMImage | None = None,
) -> LLMResponse | None:
    """One schema-bound request, shared by every call in the harness.

    A provider failure is never fatal: the checks it would have settled report as unproven,
    which is an honest outcome rather than a missing one.
    """
    request = LLMRequest(
        instructions=instructions,
        input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        reasoning_effort=effort,
        response_schema=schema,
        schema_name=name,
        max_output_tokens=max_output_tokens,
        image=image,
    )
    try:
        response = await provider.generate(request)
    except LLMProviderError as exc:
        logger.warning("%s call failed, continuing without it: %s", name, exc)
        return None

    if response.incomplete_reason:
        # A reasoning model that exhausts its budget returns nothing usable. Treating a
        # truncated answer as a conclusion is how a half-formed judgement reaches a report.
        logger.warning("%s answer was incomplete (%s)", name, response.incomplete_reason)
        return None
    return response


def _items(text: str, key: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("%s answer was not valid JSON: %s", key, exc)
        return []
    items = parsed.get(key) if isinstance(parsed, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def create_provider() -> BaseLLMProvider | None:
    """The judging provider, or ``None`` when no key is configured.

    A missing key is a configuration state, not an error: the harness runs its arithmetic
    either way, and says plainly that nothing was judged.
    """
    from api.helpers.llmproviders.openai_llm_provider import OpenAILLMProvider
    from review.context.loader import ContextError

    try:
        client = OpenAILLMProvider.create_client()
    except Exception as exc:  # noqa: BLE001 - any configuration failure is the same answer
        logger.info("no judging provider configured: %s", exc)
        return None
    if isinstance(client, ContextError):  # pragma: no cover - defensive
        return None
    return OpenAILLMProvider(client, OpenAIModel.GPT_5_5)


__all__ = [
    "MAX_OUTPUT_TOKENS",
    "JudgingResult",
    "Status",
    "call_model",
    "challenge",
    "create_provider",
    "judge",
]
