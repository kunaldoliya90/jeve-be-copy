"""Constants and enums used by the settings classes."""

from enum import StrEnum
from typing import Final


class Flavour(StrEnum):
    """Deployment environment, selected with the ``FLAVOUR`` environment variable."""

    DEV = "dev"
    STAG = "stag"
    PROD = "prod"


class LogLevel(StrEnum):
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


FLAVOUR_ENV_VAR: Final = "FLAVOUR"
DEFAULT_FLAVOUR: Final = Flavour.DEV
ENV_PREFIX: Final = "APP_"
DOTENV_FILE: Final = ".env"
DOTENV_ENCODING: Final = "utf-8"
API_PREFIX_PATTERN: Final = r"^/[\w\-/]*[\w\-]$"
LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
