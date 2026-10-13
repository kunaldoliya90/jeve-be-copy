"""Constants and enums for the API layer."""

from enum import StrEnum


class ApiPath(StrEnum):
    """Route paths, relative to ``API_PREFIX``."""

    HEALTH = "/health/"


class ApiTag(StrEnum):
    HEALTH = "health"


class HealthStatus(StrEnum):
    OK = "ok"
