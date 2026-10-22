"""Development configuration (FLAVOUR=dev): local runs, docker-compose and tests."""

from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from core.settings.base import BaseConfig
from core.settings.consts import DOTENV_ENCODING, DOTENV_FILE, Flavour, LogLevel, OpenAISettings


class DevelopmentConfig(BaseConfig):
    """Debug on, API docs on. Also reads a git-ignored ``.env`` from the working directory."""

    model_config = SettingsConfigDict(env_file=DOTENV_FILE, env_file_encoding=DOTENV_ENCODING)

    FLAVOUR: ClassVar[Flavour] = Flavour.DEV

    DEBUG: bool = True
    LOG_LEVEL: LogLevel = LogLevel.DEBUG
    DOCS_URL: str | None = "/docs"
    REDOC_URL: str | None = "/redoc"
    OPENAPI_URL: str | None = "/openapi.json"

    OPENAI_SETTINGS: OpenAISettings = {
        "BASE_URL": "https://openai-compliance-tool.services.ai.azure.com/openai/v1",
        "API_KEY": None,
        "TIMEOUT_SECONDS": 300,
    }
