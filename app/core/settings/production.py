"""Production configuration (FLAVOUR=prod)."""

from typing import ClassVar, Literal

from core.settings.base import BaseConfig
from core.settings.consts import CorsOrigins, Flavour, OpenAISettings


class ProductionConfig(BaseConfig):
    """Debug cannot be enabled (``APP_DEBUG=true`` fails at startup); API docs are off."""

    FLAVOUR: ClassVar[Flavour] = Flavour.PROD

    DEBUG: Literal[False] = False

    CORS_ORIGINS: CorsOrigins = ("https://jeve-fe-copy-rouge.vercel.app",)

    OPENAI_SETTINGS: OpenAISettings = {
        "BASE_URL": "https://openai-compliance-tool.services.ai.azure.com/openai/v1",
        "API_KEY": None,
        "TIMEOUT_SECONDS": 300,
    }
