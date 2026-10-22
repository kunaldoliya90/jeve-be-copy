"""Test doubles for the model layer.

The harness must be testable without a key and without a network, and for a reason beyond
speed: a live model's answer changes between runs, so a test asserting on one asserts on
nothing. The stub below answers every call from the schema it was handed, which is what
makes the judging and reporting paths deterministic enough to assert on.
"""

import json
from typing import Any, ClassVar

from api.consts import LLMProviderName, OpenAIModel
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.helpers.llmproviders.llm_pricing import OPENAI_PRICING
from api.pydanticmodels.llm_models import LLMCompletion, LLMRequest, TokenUsage


class StubLLMProvider(BaseLLMProvider):
    """Answers each call with a canned, schema-shaped reply and records what it was asked.

    Keyed on ``schema_name`` rather than on the prompt: the schema is the contract the
    harness parses against, so a stub that satisfies it exercises exactly the code a real
    answer would.
    """

    PROVIDER: ClassVar[LLMProviderName] = LLMProviderName.OPENAI

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        super().__init__(OpenAIModel.GPT_5_5.value, OPENAI_PRICING[OpenAIModel.GPT_5_5])
        self.answers = answers or {}
        self.requests: list[LLMRequest] = []

    async def _complete(self, request: LLMRequest) -> LLMCompletion:
        self.requests.append(request)
        answer = self.answers.get(request.schema_name, {})
        return LLMCompletion(
            text=json.dumps(answer),
            usage=TokenUsage(input_tokens=1_000, cached_input_tokens=256, output_tokens=200),
            response_id=f"stub_{request.schema_name}",
        )

    def payload(self, schema_name: str) -> dict[str, Any]:
        """What the harness sent for one call, so a test can assert on the boundary."""
        for request in self.requests:
            if request.schema_name == schema_name:
                parsed = json.loads(request.input)
                assert isinstance(parsed, dict)
                return parsed
        raise AssertionError(f"no {schema_name} call was made")


__all__ = ["StubLLMProvider"]
