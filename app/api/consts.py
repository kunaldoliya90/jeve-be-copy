"""Constants and enums for the API layer."""

from enum import StrEnum
from typing import Final


class ApiPath(StrEnum):
    """Route paths, relative to ``API_PREFIX``."""

    HEALTH = "/health/"


class ApiTag(StrEnum):
    HEALTH = "health"


class HealthStatus(StrEnum):
    OK = "ok"


class ErrorCode(StrEnum):
    """Machine-readable codes carried by ``BaseApplicationError``."""

    LLM_CONFIGURATION_ERROR = "llm_configuration_error"
    LLM_PROVIDER_ERROR = "llm_provider_error"


class LLMProviderName(StrEnum):
    """LLM vendors the service can call."""

    OPENAI = "openai"


class OpenAIModel(StrEnum):
    """OpenAI models deployed on the Azure AI Foundry resource; the value is the deployment name."""

    GPT_5_5 = "gpt-5.5"
    GPT_4O = "gpt-4o"  # no reasoning, cheaper: extraction, rewriting, simple classification


class ReasoningEffort(StrEnum):
    """How long a reasoning model thinks before answering; more effort, more output tokens."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


TOKENS_PER_MILLION: Final = 1_000_000
