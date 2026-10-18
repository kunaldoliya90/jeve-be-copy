"""OpenAILLMProvider: Responses API calls, usage mapping, errors and client settings."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APIConnectionError, AsyncOpenAI, omit
from openai.types.responses import Response, ResponseUsage
from openai.types.responses.response import IncompleteDetails
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from api.consts import OpenAIModel, ReasoningEffort
from api.exceptions.llm_exceptions import LLMConfigurationError, LLMProviderError
from api.helpers.llmproviders import openai_llm_provider
from api.helpers.llmproviders.llm_pricing import OPENAI_PRICING
from api.helpers.llmproviders.openai_llm_provider import OpenAILLMProvider
from api.pydanticmodels.llm_models import LLMRequest, TokenUsage
from core.settings.development import DevelopmentConfig

pytestmark = pytest.mark.anyio

USAGE = ResponseUsage(
    input_tokens=1_000,
    input_tokens_details=InputTokensDetails(cached_tokens=400, cache_write_tokens=0),
    output_tokens=500,
    output_tokens_details=OutputTokensDetails(reasoning_tokens=300),
    total_tokens=1_500,
)


@pytest.fixture(autouse=True)
def default_settings(monkeypatch: pytest.MonkeyPatch, isolated_environment: None) -> None:
    """Use default settings, not whatever the developer's ``.env`` held at import time."""
    monkeypatch.setattr(openai_llm_provider, "config", DevelopmentConfig())


def _response(
    text: str = "Paris",
    usage: ResponseUsage | None = USAGE,
    incomplete_details: IncompleteDetails | None = None,
) -> Response:
    """A Responses API payload: a reasoning item, then the answer message."""
    return Response.model_validate(
        {
            "id": "resp_123",
            "object": "response",
            "created_at": 1_760_000_000,
            "model": "gpt-5.5",
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            ],
            "usage": usage,
            "incomplete_details": incomplete_details,
        }
    )


def _client(response: Response | None = None, error: Exception | None = None) -> MagicMock:
    client = MagicMock(spec=AsyncOpenAI)
    client.responses.create = AsyncMock(return_value=response or _response(), side_effect=error)
    return client


async def test_generate_sends_request_to_model_deployment() -> None:
    client = _client()
    provider = OpenAILLMProvider(client, OpenAIModel.GPT_5_5)
    request = LLMRequest(
        input="Is this journal entry balanced?",
        instructions="You are an auditor.",
        reasoning_effort=ReasoningEffort.HIGH,
        max_output_tokens=2_000,
    )

    await provider.generate(request)

    client.responses.create.assert_awaited_once_with(
        model="gpt-5.5",
        input="Is this journal entry balanced?",
        instructions="You are an auditor.",
        reasoning={"effort": "high"},
        max_output_tokens=2_000,
    )


async def test_generate_omits_unset_options() -> None:
    client = _client()

    await OpenAILLMProvider(client, OpenAIModel.GPT_4O).generate(LLMRequest(input="Hi"))

    client.responses.create.assert_awaited_once_with(
        model="gpt-4o", input="Hi", instructions=omit, reasoning=omit, max_output_tokens=omit
    )


async def test_generate_maps_openai_usage_and_prices_it_for_the_model() -> None:
    provider = OpenAILLMProvider(_client(), OpenAIModel.GPT_5_5)

    response = await provider.generate(LLMRequest(input="Hi"))

    assert response.text == "Paris"
    assert response.response_id == "resp_123"
    assert response.usage == TokenUsage(
        input_tokens=1_000, cached_input_tokens=400, output_tokens=500, reasoning_tokens=300
    )
    # gpt-5.5: 600 * $5 + 400 * $0.50 + 500 * $30, per million tokens.
    assert response.cost.total_usd == Decimal("0.0182")


async def test_generate_reports_incomplete_reason() -> None:
    response = _response(text="", incomplete_details=IncompleteDetails(reason="max_output_tokens"))
    provider = OpenAILLMProvider(_client(response), OpenAIModel.GPT_5_5)

    result = await provider.generate(LLMRequest(input="Hi"))

    assert result.incomplete_reason == "max_output_tokens"


async def test_generate_without_usage_reports_zero_tokens() -> None:
    provider = OpenAILLMProvider(_client(_response(usage=None)), OpenAIModel.GPT_4O)

    response = await provider.generate(LLMRequest(input="Hi"))

    assert response.usage == TokenUsage()
    assert response.cost.total_usd == 0


async def test_generate_api_error_raises_llm_provider_error() -> None:
    client = _client(error=APIConnectionError(request=MagicMock()))
    provider = OpenAILLMProvider(client, OpenAIModel.GPT_4O)

    with pytest.raises(LLMProviderError, match="OpenAI gpt-4o call failed"):
        await provider.generate(LLMRequest(input="Hi"))


def test_every_openai_model_has_a_price() -> None:
    assert set(OPENAI_PRICING) == set(OpenAIModel)


@pytest.mark.parametrize("api_key", [None, ""])
def test_create_client_without_api_key_raises(
    monkeypatch: pytest.MonkeyPatch, api_key: str | None
) -> None:
    settings = {**DevelopmentConfig().OPENAI_SETTINGS, "API_KEY": api_key}
    monkeypatch.setattr(openai_llm_provider, "config", DevelopmentConfig(OPENAI_SETTINGS=settings))

    with pytest.raises(LLMConfigurationError, match="API_KEY is not set"):
        OpenAILLMProvider.create_client()


async def test_create_client_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    openai_settings = {
        "API_KEY": "test-key",
        "BASE_URL": "https://example.test/openai/v1",
        "TIMEOUT_SECONDS": 12,
    }
    monkeypatch.setattr(
        openai_llm_provider, "config", DevelopmentConfig(OPENAI_SETTINGS=openai_settings)
    )

    client = OpenAILLMProvider.create_client()

    assert client.api_key == "test-key"
    assert str(client.base_url) == "https://example.test/openai/v1/"
    assert client.timeout == 12
    await client.close()
