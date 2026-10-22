"""Errors raised by the review harness. Views map these onto HTTP responses."""

from api.consts import ErrorCode
from api.exceptions.base import BaseApplicationError


class ReviewError(BaseApplicationError):
    """Base for every review failure, so a view can catch the family."""


class UploadRejectedError(ReviewError):
    """The gatekeeper refused the file before any parsing cost was paid."""

    def __init__(self, message: str, error_code: ErrorCode, details: dict[str, str] | None = None):
        super().__init__(message=message, error_code=error_code, details=details)


class WorkbookUnreadableError(ReviewError):
    """The file passed the gatekeeper but could not be parsed into a profile."""

    def __init__(self, message: str, details: dict[str, str] | None = None):
        super().__init__(message=message, error_code=ErrorCode.CORRUPT_WORKBOOK, details=details)


class ReviewCapacityError(ReviewError):
    """Too many reviews already running on this worker."""

    def __init__(self, message: str, details: dict[str, str] | None = None):
        super().__init__(message=message, error_code=ErrorCode.AT_CAPACITY, details=details)


class ReviewNotFoundError(ReviewError):
    """No review with that id, or its result has aged out of the store."""

    def __init__(self, message: str, details: dict[str, str] | None = None):
        super().__init__(message=message, error_code=ErrorCode.REVIEW_NOT_FOUND, details=details)


class ReviewDeadlineError(ReviewError):
    """The whole-review deadline expired. Nothing is cached from a timed-out review."""

    def __init__(self, message: str, details: dict[str, str] | None = None):
        super().__init__(message=message, error_code=ErrorCode.REVIEW_TIMEOUT, details=details)
