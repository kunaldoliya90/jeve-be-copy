"""LLM metering: token usage and its USD cost, for any provider."""

from collections.abc import Iterable
from decimal import Decimal

from api.consts import TOKENS_PER_MILLION
from api.pydanticmodels.llm_models import LLMCost, LLMPricing, TokenUsage


class TokenMeteringUtils:
    """Measures token usage of LLM calls."""

    @staticmethod
    def uncached_input_tokens(usage: TokenUsage) -> int:
        """Input tokens not served from the prompt cache, billed at the full input price."""
        return usage.input_tokens - usage.cached_input_tokens

    @staticmethod
    def sum_usage(usages: Iterable[TokenUsage]) -> TokenUsage:
        """Total usage of several calls, e.g. every call an agent made for one request."""
        items = list(usages)
        return TokenUsage(
            input_tokens=sum(usage.input_tokens for usage in items),
            cached_input_tokens=sum(usage.cached_input_tokens for usage in items),
            output_tokens=sum(usage.output_tokens for usage in items),
            reasoning_tokens=sum(usage.reasoning_tokens for usage in items),
        )


class CostMeteringUtils:
    """Prices token usage of LLM calls."""

    @staticmethod
    def calculate_cost(usage: TokenUsage, pricing: LLMPricing) -> LLMCost:
        """Cost of ``usage`` at ``pricing``.

        Cached input tokens are billed at the cached rate, the rest of the input at the input
        rate, and every output token (reasoning included) at the output rate.
        """
        return LLMCost(
            input_usd=CostMeteringUtils._price(
                TokenMeteringUtils.uncached_input_tokens(usage), pricing.input_usd_per_million
            ),
            cached_input_usd=CostMeteringUtils._price(
                usage.cached_input_tokens, pricing.cached_input_usd_per_million
            ),
            output_usd=CostMeteringUtils._price(
                usage.output_tokens, pricing.output_usd_per_million
            ),
        )

    @staticmethod
    def sum_costs(costs: Iterable[LLMCost]) -> LLMCost:
        """Total cost of several calls, possibly made with different models."""
        items = list(costs)
        return LLMCost(
            input_usd=sum((cost.input_usd for cost in items), Decimal(0)),
            cached_input_usd=sum((cost.cached_input_usd for cost in items), Decimal(0)),
            output_usd=sum((cost.output_usd for cost in items), Decimal(0)),
        )

    @staticmethod
    def _price(tokens: int, usd_per_million: Decimal) -> Decimal:
        return tokens * usd_per_million / TOKENS_PER_MILLION
