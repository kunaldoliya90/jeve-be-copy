"""Constants and enums for the API layer."""

from enum import StrEnum
from typing import Final


class ApiPath(StrEnum):
    """Route paths, relative to ``API_PREFIX``."""

    HEALTH = "/health/"
    REVIEWS = "/reviews/"
    REVIEW = "/reviews/{review_id}/"
    REVIEW_EVENTS = "/reviews/{review_id}/events/"
    JE_TYPES = "/je-types/"
    JE_TYPE = "/je-types/{je_type_id}/"


class ApiTag(StrEnum):
    HEALTH = "health"
    REVIEWS = "reviews"
    JE_TYPES = "je-types"


class SSEEvent(StrEnum):
    """Progress events streamed while a review runs.

    Every event carries ``review_id``, ``step`` and ``ts``. They say where the system looked,
    never what a cell held.
    """

    REVIEW_STARTED = "review_started"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    CACHE_HIT = "cache_hit"
    AI_CALL = "ai_call"
    CHECK_FINISHED = "check_finished"
    REVIEW_COMPLETED = "review_completed"
    REVIEW_FAILED = "review_failed"


class AICall(StrEnum):
    """The model calls one review may make. Only JUDGE, CHALLENGE and SUMMARY always run."""

    UNDERSTAND = "AI-1"
    PLAN = "AI-2"
    PICTURE_READER = "AI-R"
    METHODOLOGY = "AI-M"
    CLUSTERING = "AI-X"
    JUDGE = "AI-3"
    CHALLENGE = "AI-4"
    SUMMARY = "AI-5"


class JETypeSource(StrEnum):
    """Where the entry type the checks ran against came from.

    The distinction is the whole audit point. ``SHAPE`` means the workbook's own columns
    settled it; ``DECLARED`` means they did not and the harness fell back to what the
    uploader named, so every result beneath it is unconfirmed; ``UNKNOWN`` means neither,
    and no check ran at all.
    """

    SHAPE = "shape"
    DECLARED = "declared"
    UNKNOWN = "unknown"


class RiskRating(StrEnum):
    """How risky the review found this entry, read off the band its score reached.

    A conclusion, not an input. It was once typed on the upload form, which meant it was
    almost always blank and, when it was not, was a number the system had never checked
    against anything. It is now derived from the score, so it exists on every review and
    says something the review actually established.

    It is therefore not independent of the compliance score — it is that score, banded. A
    reader must not treat the two as corroborating one another.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class HealthStatus(StrEnum):
    OK = "ok"


class ErrorCode(StrEnum):
    """Machine-readable codes carried by ``BaseApplicationError``."""

    LLM_CONFIGURATION_ERROR = "llm_configuration_error"
    LLM_PROVIDER_ERROR = "llm_provider_error"

    NOT_EXCEL = "not_excel"
    TOO_LARGE = "too_large"
    CORRUPT_WORKBOOK = "corrupt_workbook"
    MACRO_BLOCKED = "macro_blocked"
    AT_CAPACITY = "at_capacity"
    AI_UNAVAILABLE = "ai_unavailable"
    REVIEW_NOT_FOUND = "review_not_found"
    REVIEW_RUNNING = "review_running"
    REVIEW_TIMEOUT = "review_timeout"
    NO_TABLES = "no_tables"
    INTERNAL = "internal"


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
