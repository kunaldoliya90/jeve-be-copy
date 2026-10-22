"""Base configuration shared by every flavour."""

from typing import Any, ClassVar

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.settings.consts import API_PREFIX_PATTERN, ENV_PREFIX, LOG_FORMAT, Flavour, LogLevel


class BaseConfig(BaseSettings):
    """Base global configuration.

    Values can be overridden with ``APP_``-prefixed environment variables (``APP_DEBUG``,
    ``APP_LOG_LEVEL``...). Flavour classes override only the defaults that differ; the
    defaults here are the safe, production-like choice. Instances are immutable.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore", frozen=True)

    FLAVOUR: ClassVar[Flavour]

    DEBUG: bool = False
    APP_NAME: str = "jeve-be"
    API_PREFIX: str = Field(default="/api/jeve", pattern=API_PREFIX_PATTERN)
    LOG_LEVEL: LogLevel = LogLevel.INFO

    # API documentation, served under API_PREFIX. None disables it.
    DOCS_URL: str | None = None
    REDOC_URL: str | None = None
    OPENAPI_URL: str | None = None

    # --- review harness ---------------------------------------------------------------
    MAX_UPLOAD_MB: int = Field(default=25, gt=0, description="Rejected above this, unparsed")
    MAX_ACTIVE_REVIEWS: int = Field(
        default=4, gt=0, description="Per worker. Each pins a thread and a parsed workbook"
    )
    TOOL_WORKERS: int = Field(default=8, gt=0, description="Thread pool for step 13")
    REVIEW_DEADLINE_S: float = Field(default=600.0, gt=0)
    STRUCTURE_CONTEXT_MAX_TOKENS: int = Field(
        default=8_000, gt=0, description="Cap on what AI-1 may see of the workbook"
    )
    FIGURE_SAMPLES: int = Field(
        default=10, gt=0, description="Exemplars per figure; exceptions are kept in full"
    )

    @property
    def MAX_UPLOAD_BYTES(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def LOGGING_CONFIG(self) -> dict[str, Any]:
        """``logging.config.dictConfig`` settings, applied at application startup."""
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"standard": {"format": LOG_FORMAT}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"handlers": ["console"], "level": self.LOG_LEVEL},
        }
