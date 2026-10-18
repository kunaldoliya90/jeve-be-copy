"""Staging configuration (FLAVOUR=stag)."""

from typing import ClassVar

from core.settings.base import BaseConfig
from core.settings.consts import Flavour, OpenAISettings


class StagingConfig(BaseConfig):
    """Production-like, but API docs stay available."""

    FLAVOUR: ClassVar[Flavour] = Flavour.STAG

    DOCS_URL: str | None = "/docs"
    REDOC_URL: str | None = "/redoc"
    OPENAPI_URL: str | None = "/openapi.json"

    OPENAI_SETTINGS: OpenAISettings = {
        "BASE_URL": "https://openai-compliance-tool.services.ai.azure.com/openai/v1",
        "API_KEY": None,
        "TIMEOUT_SECONDS": 300,
    }
