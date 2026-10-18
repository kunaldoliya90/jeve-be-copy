"""BaseLLMProvider: metering, totals and metric logs around a provider's API call."""

import logging
from decimal import Decimal

import pytest

from api.consts import LLMProviderName
from api.exceptions.llm_exceptions import LLMProviderError
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMCompletion, LLMPricing, LLMRequest, TokenUsage

pytestmark = pytest.mark.anyio

LOGGER = "api.helpers.llmproviders.base_llm_provider"
PRICING = LLMPricing(
    input_usd_per_million=Decimal("2"),
    cached_input_usd_per_million=Decimal("1"),
    output_usd_per_million=Decimal("10"),
)
USAGE = TokenUsage(input_tokens=1_000, cached_input_tokens=200, output_tokens=300)
REQUEST = LLMRequest(input="What is the capital of France?")


class FakeLLMProvider(BaseLLMProvider):
    """Returns a fixed completion, or raises a fixed error, instead of calling a vendor."""

    PROVIDER = LLMProviderName.OPENAI

    def __init__(
        self, completion: LLMCompletion | None = None, error: LLMProviderError | None = None
    ) -> None:
        super().__init__(model="fake-model", pricing=PRICING)
        self._completion = completion or LLMCompletion(text="Paris", usage=USAGE)
        self._error = error

    async def _complete(self, request: LLMRequest) -> LLMCompletion:
        if self._error is not None:
            raise self._error
        return self._completion


async def test_generate_returns_answer_with_usage_and_cost() -> None:
    response = await FakeLLMProvider().generate(REQUEST)

    assert response.text == "Paris"
    assert response.provider == LLMProviderName.OPENAI
    assert response.model == "fake-model"
    assert response.usage == USAGE
    assert response.cost.total_usd == Decimal("0.0048")  # 800*2 + 200*1 + 300*10, per million


async def test_generate_logs_tokens_and_cost(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)

    await FakeLLMProvider().generate(REQUEST)

    message = caplog.records[-1].getMessage()
    assert "provider=openai model=fake-model" in message
    assert "input_tokens=1000 cached_input_tokens=200 output_tokens=300" in message
    assert "total_tokens=1300 cost_usd=0.004800" in message


async def test_generate_incomplete_answer_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    completion = LLMCompletion(text="", usage=USAGE, incomplete_reason="max_output_tokens")
    caplog.set_level(logging.WARNING, logger=LOGGER)

    response = await FakeLLMProvider(completion=completion).generate(REQUEST)

    assert response.incomplete_reason == "max_output_tokens"
    assert "reason=max_output_tokens" in caplog.records[-1].getMessage()


async def test_generate_provider_error_is_logged_and_reraised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = FakeLLMProvider(error=LLMProviderError("rate limited"))
    caplog.set_level(logging.WARNING, logger=LOGGER)

    with pytest.raises(LLMProviderError, match="rate limited"):
        await provider.generate(REQUEST)

    assert "llm call failed provider=openai model=fake-model" in caplog.records[-1].getMessage()
