"""Settings: ``from core.settings import config``.

``FLAVOUR`` (dev | stag | prod, default dev) selects the configuration class at startup.
An unknown value stops the service instead of silently falling back to development.
"""

import os
from typing import Final

from core.settings.consts import DEFAULT_FLAVOUR, FLAVOUR_ENV_VAR, Flavour
from core.settings.development import DevelopmentConfig
from core.settings.production import ProductionConfig
from core.settings.staging import StagingConfig

# A setting only the flavours define (e.g. OPENAI_SETTINGS) type-checks only if all of them do.
type AppConfig = DevelopmentConfig | StagingConfig | ProductionConfig

SETTINGS_BY_FLAVOUR: Final[dict[Flavour, type[AppConfig]]] = {
    Flavour.DEV: DevelopmentConfig,
    Flavour.STAG: StagingConfig,
    Flavour.PROD: ProductionConfig,
}


def get_settings() -> AppConfig:
    """Build the configuration for the current ``FLAVOUR``."""
    flavour = Flavour(os.getenv(FLAVOUR_ENV_VAR, DEFAULT_FLAVOUR).strip().lower())
    return SETTINGS_BY_FLAVOUR[flavour]()


config = get_settings()
