"""Base class of every domain exception."""

from typing import Any

from api.consts import ErrorCode


class BaseApplicationError(Exception):
    """A domain error: a readable message, a machine-readable code and optional details."""

    def __init__(
        self, message: str, error_code: ErrorCode, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}
