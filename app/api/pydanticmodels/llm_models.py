"""LLM call models: the request, the answer and its token and cost metering."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from api.consts import LLMProviderName, ReasoningEffort


class LLMPricing(BaseModel):
    """Price of one LLM model, in USD per million tokens."""

    model_config = ConfigDict(frozen=True)

    input_usd_per_million: Decimal = Field(..., ge=0, description="Uncached input tokens")
    cached_input_usd_per_million: Decimal = Field(..., ge=0, description="Cached input tokens")
    output_usd_per_million: Decimal = Field(..., ge=0, description="Output tokens")


class LLMRequest(BaseModel):
    """One prompt for an LLM provider."""

    input: str = Field(..., min_length=1, description="User content the model answers")
    instructions: str | None = Field(
        default=None, description="System instructions: role, rules, output format"
    )
    reasoning_effort: ReasoningEffort | None = Field(
        default=None, description="Reasoning models only (gpt-5.5); None uses the model default"
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
