"""LLM call models: the request, the answer and its token and cost metering."""

import base64
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from api.consts import LLMProviderName, ReasoningEffort


class LLMPricing(BaseModel):
    """Price of one LLM model, in USD per million tokens."""

    model_config = ConfigDict(frozen=True)

    input_usd_per_million: Decimal = Field(..., ge=0, description="Uncached input tokens")
    cached_input_usd_per_million: Decimal = Field(..., ge=0, description="Cached input tokens")
    output_usd_per_million: Decimal = Field(..., ge=0, description="Output tokens")


class LLMImage(BaseModel):
    """An image sent inline with a request.

    Inline rather than by URL on purpose: these are screenshots out of a client's workbook,
    and uploading one somewhere to be fetched back would put it on a third surface for no
    benefit.
    """

    model_config = ConfigDict(frozen=True)

    data: bytes = Field(..., description="The image bytes, base64-encoded when sent")
    content_type: str = Field(..., min_length=3, description='e.g. "image/png"')
    detail: str = Field(default="high", description='"high" for text in a screenshot')

    @property
    def data_url(self) -> str:
        """The image as a data URL, which is how the Responses API takes an inline image."""
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.content_type};base64,{encoded}"


class LLMRequest(BaseModel):
    """One prompt for an LLM provider."""

    input: str = Field(..., min_length=1, description="User content the model answers")
    instructions: str | None = Field(
        default=None, description="System instructions: role, rules, output format"
    )
    reasoning_effort: ReasoningEffort | None = Field(
        default=None, description="Reasoning models only (gpt-5.5); None uses the model default"
    )
    response_schema: dict[str, Any] | None = Field(
        default=None,
        description=(
            "JSON schema the answer must satisfy, enforced by the provider. Without it a "
            "model may return prose where a verdict was expected, and a parser that has to "
            "guess is a parser that eventually guesses wrong."
        ),
    )
    schema_name: str = Field(default="answer", description="Names the schema in the request")
    image: "LLMImage | None" = Field(
        default=None,
        description=(
            "One image to read alongside the text. Vision-capable models only; a model "
            "without vision returns an error rather than silently ignoring it."
        ),
    )
    max_output_tokens: int | None = Field(
        default=None, gt=0, description="Cap on output tokens, reasoning included"
    )


class TokenUsage(BaseModel):
    """Tokens consumed by one or more LLM calls.

    Counts follow OpenAI's convention, which every provider maps to: ``input_tokens``
    includes ``cached_input_tokens`` and ``output_tokens`` includes ``reasoning_tokens``.
    """

    input_tokens: int = Field(default=0, ge=0, description="Prompt tokens, cached included")
    cached_input_tokens: int = Field(default=0, ge=0, description="Prompt tokens read from cache")
    output_tokens: int = Field(default=0, ge=0, description="Generated tokens, reasoning included")
    reasoning_tokens: int = Field(default=0, ge=0, description="Generated tokens spent reasoning")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        """Input plus output tokens."""
        return self.input_tokens + self.output_tokens


class LLMCost(BaseModel):
    """USD cost of a ``TokenUsage``, split by how each kind of token is billed."""

    input_usd: Decimal = Field(default=Decimal(0), ge=0, description="Uncached input tokens")
    cached_input_usd: Decimal = Field(default=Decimal(0), ge=0, description="Cached input tokens")
    output_usd: Decimal = Field(default=Decimal(0), ge=0, description="Output tokens")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_usd(self) -> Decimal:
        """Cost of all tokens."""
        return self.input_usd + self.cached_input_usd + self.output_usd


class LLMCompletion(BaseModel):
    """What a provider's API returned, before the base provider meters it."""

    text: str = Field(..., description="The model's answer")
    usage: TokenUsage = Field(..., description="Tokens the call consumed")
    response_id: str | None = Field(default=None, description="Provider's id for the call")
    incomplete_reason: str | None = Field(
        default=None, description="Why the answer stopped early (max_output_tokens...)"
    )


class LLMResponse(LLMCompletion):
    """Result of ``BaseLLMProvider.generate``: the answer with its usage and cost."""

    provider: LLMProviderName = Field(..., description="Vendor that served the call")
    model: str = Field(..., description="Model or deployment that served the call")
    cost: LLMCost = Field(..., description="USD cost of the call")
    latency_ms: int = Field(
        default=0,
        ge=0,
        description=(
            "Wall clock the call took. Measured here rather than by the caller: a caller "
            "that times its own await also times whatever else it is doing concurrently."
        ),
    )
