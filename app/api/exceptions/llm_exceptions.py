"""Errors raised by LLM providers."""

from api.consts import ErrorCode
from api.exceptions.base import BaseApplicationError


class LLMConfigurationError(BaseApplicationError):
    """An LLM provider cannot be used because a setting is missing, such as its API key."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.LLM_CONFIGURATION_ERROR)


class LLMProviderError(BaseApplicationError):
    """The vendor API call failed: connection, timeout, rate limit or an error response."""

    def __init__(self, message: str) -> None:
        super().__init__(message, ErrorCode.LLM_PROVIDER_ERROR)
