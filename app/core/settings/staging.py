"""Staging configuration (FLAVOUR=stag)."""

from typing import ClassVar

from core.settings.base import BaseConfig
from core.settings.consts import Flavour


class StagingConfig(BaseConfig):
    """Production-like, but API docs stay available."""

    FLAVOUR: ClassVar[Flavour] = Flavour.STAG

    DOCS_URL: str | None = "/docs"
    REDOC_URL: str | None = "/redoc"
    OPENAPI_URL: str | None = "/openapi.json"
