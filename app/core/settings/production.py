"""Production configuration (FLAVOUR=prod)."""

from typing import ClassVar, Literal

from core.settings.base import BaseConfig
from core.settings.consts import Flavour


class ProductionConfig(BaseConfig):
    """Debug cannot be enabled (``APP_DEBUG=true`` fails at startup); API docs are off."""

    FLAVOUR: ClassVar[Flavour] = Flavour.PROD

    DEBUG: Literal[False] = False
