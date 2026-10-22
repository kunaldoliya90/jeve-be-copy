"""TokenMeteringUtils and CostMeteringUtils: token arithmetic and USD cost of LLM usage."""

from decimal import Decimal

from api.pydanticmodels.llm_models import LLMCost, LLMPricing, TokenUsage
from api.utils import CostMeteringUtils, TokenMeteringUtils

PRICING = LLMPricing(
    input_usd_per_million=Decimal("5.00"),
    cached_input_usd_per_million=Decimal("0.50"),
    output_usd_per_million=Decimal("30.00"),
)


def test_uncached_input_tokens_excludes_cached_tokens() -> None:
    usage = TokenUsage(input_tokens=1_000, cached_input_tokens=400)

    assert TokenMeteringUtils.uncached_input_tokens(usage) == 600


def test_sum_usage_adds_every_count() -> None:
    first = TokenUsage(
        input_tokens=100, cached_input_tokens=10, output_tokens=50, reasoning_tokens=5
    )
    second = TokenUsage(
        input_tokens=200, cached_input_tokens=20, output_tokens=70, reasoning_tokens=0
    )

    total = TokenMeteringUtils.sum_usage([first, second])

    assert total == TokenUsage(
        input_tokens=300, cached_input_tokens=30, output_tokens=120, reasoning_tokens=5
    )
    assert total.total_tokens == 420


def test_sum_usage_of_no_calls_is_zero() -> None:
    assert TokenMeteringUtils.sum_usage([]) == TokenUsage()


def test_calculate_cost_bills_cached_input_at_cached_rate() -> None:
    usage = TokenUsage(input_tokens=1_000_000, cached_input_tokens=400_000)

    cost = CostMeteringUtils.calculate_cost(usage, PRICING)

    assert cost.input_usd == Decimal("3.00")
    assert cost.cached_input_usd == Decimal("0.20")
    assert cost.output_usd == 0


def test_calculate_cost_bills_reasoning_tokens_as_output() -> None:
    usage = TokenUsage(output_tokens=2_000, reasoning_tokens=1_500)

    cost = CostMeteringUtils.calculate_cost(usage, PRICING)

    assert cost.output_usd == Decimal("0.06")


def test_calculate_cost_is_exact_for_single_tokens() -> None:
    usage = TokenUsage(input_tokens=2, cached_input_tokens=1, output_tokens=1)

    cost = CostMeteringUtils.calculate_cost(usage, PRICING)

    assert cost.total_usd == Decimal("0.0000355")


def test_sum_costs_adds_every_part() -> None:
    first = LLMCost(
        input_usd=Decimal("1"), cached_input_usd=Decimal("0.1"), output_usd=Decimal("2")
    )
    second = LLMCost(input_usd=Decimal("3"), cached_input_usd=Decimal("0"), output_usd=Decimal("4"))

    total = CostMeteringUtils.sum_costs([first, second])

    assert total == LLMCost(
        input_usd=Decimal("4"), cached_input_usd=Decimal("0.1"), output_usd=Decimal("6")
    )


def test_sum_costs_of_no_calls_is_zero() -> None:
    assert CostMeteringUtils.sum_costs([]).total_usd == 0
