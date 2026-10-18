"""Constants, enums and dict-setting keys used by the settings classes."""

from enum import StrEnum
from typing import Final, TypedDict

from pydantic import SecretStr


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


class OpenAISettings(TypedDict):
    """Keys of each flavour's ``OPENAI_SETTINGS``.

    ``API_KEY`` is a secret: leave it ``None`` in code and set the whole dict in the
    environment as JSON, ``APP_OPENAI_SETTINGS='{"BASE_URL": ..., "API_KEY": ..., ...}'``.
    """

    BASE_URL: str
    API_KEY: SecretStr | None
    TIMEOUT_SECONDS: float


FLAVOUR_ENV_VAR: Final = "FLAVOUR"
DEFAULT_FLAVOUR: Final = Flavour.DEV
ENV_PREFIX: Final = "APP_"
DOTENV_FILE: Final = ".env"
DOTENV_ENCODING: Final = "utf-8"
API_PREFIX_PATTERN: Final = r"^/[\w\-/]*[\w\-]$"
LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
