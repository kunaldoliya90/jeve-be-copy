"""Base class of every LLM provider: one call path that meters tokens and cost and logs them."""

import logging
import time
from abc import ABC, abstractmethod
from typing import ClassVar

from api.consts import LLMProviderName
from api.exceptions.llm_exceptions import LLMProviderError
from api.pydanticmodels.llm_models import LLMCompletion, LLMPricing, LLMRequest, LLMResponse
from api.utils import CostMeteringUtils

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Calls one model of one vendor.

    Callers use ``generate``; a subclass implements only ``_complete``, the vendor API call.
    Each call's tokens and cost are logged and returned in its ``LLMResponse``.
    """

    PROVIDER: ClassVar[LLMProviderName]

    def __init__(self, model: str, pricing: LLMPricing) -> None:
        self.model = model
        self.pricing = pricing

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Send ``request`` to the model; return the answer with its token usage and cost."""
        started = time.perf_counter()
        try:
            completion = await self._complete(request)
        except LLMProviderError as exc:
            logger.warning(
                "llm call failed provider=%s model=%s duration_ms=%.0f error=%s",
                self.PROVIDER,
                self.model,
                self._elapsed_ms(started),
                exc.message,
            )
            raise
        elapsed = self._elapsed_ms(started)
        response = self._meter(completion, elapsed)
        self._log_metrics(response, elapsed)
        return response

    @abstractmethod
    async def _complete(self, request: LLMRequest) -> LLMCompletion:
        """Call the vendor API.

        Report usage in ``TokenUsage``'s convention and raise ``LLMProviderError`` for any
        vendor failure.
        """

    def _meter(self, completion: LLMCompletion, duration_ms: float) -> LLMResponse:
        cost = CostMeteringUtils.calculate_cost(completion.usage, self.pricing)
        return LLMResponse(
            text=completion.text,
            usage=completion.usage,
            response_id=completion.response_id,
            incomplete_reason=completion.incomplete_reason,
            provider=self.PROVIDER,
            model=self.model,
            cost=cost,
            latency_ms=round(duration_ms),
        )

    def _log_metrics(self, response: LLMResponse, duration_ms: float) -> None:
        usage = response.usage
        logger.info(
            "llm call provider=%s model=%s response_id=%s duration_ms=%.0f input_tokens=%d "
            "cached_input_tokens=%d output_tokens=%d reasoning_tokens=%d total_tokens=%d "
            "cost_usd=%.6f",
            response.provider,
            response.model,
            response.response_id,
            duration_ms,
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.output_tokens,
            usage.reasoning_tokens,
            usage.total_tokens,
            response.cost.total_usd,
        )
        if response.incomplete_reason is not None:
            logger.warning(
                "llm response incomplete provider=%s model=%s response_id=%s reason=%s",
                response.provider,
                response.model,
                response.response_id,
                response.incomplete_reason,
            )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000
