"""OpenAI models through the Responses API, served by Azure AI Foundry's OpenAI v1 endpoint."""

import logging
from typing import ClassVar, cast

from openai import APIError, AsyncOpenAI, omit
from openai.types.responses import Response
from openai.types.shared_params import Reasoning

from api.consts import LLMProviderName, OpenAIModel
from api.exceptions.llm_exceptions import LLMConfigurationError, LLMProviderError
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.helpers.llmproviders.llm_pricing import OPENAI_PRICING
from api.pydanticmodels.llm_models import LLMCompletion, LLMRequest, TokenUsage
from core.settings import config

logger = logging.getLogger(__name__)


class OpenAILLMProvider(BaseLLMProvider):
    """Calls one OpenAI model deployment, e.g. ``OpenAIModel.GPT_5_5`` for reasoning work.

    ``client`` holds the connection pool: build it once with ``create_client()``, share it
    between providers and close it when the application stops.
    """

    PROVIDER: ClassVar[LLMProviderName] = LLMProviderName.OPENAI

    def __init__(self, client: AsyncOpenAI, model: OpenAIModel) -> None:
        super().__init__(model=model, pricing=OPENAI_PRICING[model])
        self._client = client

    @staticmethod
    def create_client() -> AsyncOpenAI:
        """Build the client from ``OPENAI_SETTINGS``; close it with ``await client.close()``."""
        settings = config.OPENAI_SETTINGS
        api_key = settings["API_KEY"]
        if not api_key:
            raise LLMConfigurationError("OPENAI_SETTINGS API_KEY is not set (APP_OPENAI_SETTINGS)")
        return AsyncOpenAI(
            base_url=settings["BASE_URL"],
            api_key=api_key.get_secret_value(),
            timeout=settings["TIMEOUT_SECONDS"],
        )

    async def _complete(self, request: LLMRequest) -> LLMCompletion:
        # ReasoningEffort values are the SDK's effort literals.
        reasoning = (
            cast(Reasoning, {"effort": request.reasoning_effort})
            if request.reasoning_effort is not None
            else omit
        )
        try:
            response = await self._client.responses.create(
                model=self.model,
                input=request.input,
                instructions=request.instructions or omit,
                reasoning=reasoning,
                max_output_tokens=request.max_output_tokens or omit,
            )
        except APIError as exc:
            raise LLMProviderError(f"OpenAI {self.model} call failed: {exc.message}") from exc
        return LLMCompletion(
            text=response.output_text,
            usage=self._usage(response),
            response_id=response.id,
            incomplete_reason=(
                response.incomplete_details.reason if response.incomplete_details else None
            ),
        )

    @staticmethod
    def _usage(response: Response) -> TokenUsage:
        if response.usage is None:
            logger.warning("openai response has no usage response_id=%s", response.id)
            return TokenUsage()
        return TokenUsage(
            input_tokens=response.usage.input_tokens,
            cached_input_tokens=response.usage.input_tokens_details.cached_tokens,
            output_tokens=response.usage.output_tokens,
            reasoning_tokens=response.usage.output_tokens_details.reasoning_tokens,
        )
