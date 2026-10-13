"""Application startup and shutdown."""

import logging
import logging.config
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.settings import config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on startup; release resources on shutdown."""
    logging.config.dictConfig(config.LOGGING_CONFIG)
    logger.info("Starting %s (flavour=%s)", config.APP_NAME, config.FLAVOUR)
    yield
    logger.info("Stopped %s", config.APP_NAME)
