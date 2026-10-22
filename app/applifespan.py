"""Application startup and shutdown."""

import logging
import logging.config
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from api.consts import AICall
from core.settings import config
from review.agents.judging import create_provider
from review.context.loader import ContextLoader
from review.context.prompts import PromptRegistry
from review.orchestrator.orchestrator import ReviewOrchestrator
from review.storage.reviews import ReviewStore

logger = logging.getLogger(__name__)

STORAGE_ROOT = Path("var") / "reviews"


@asynccontextmanager
async def app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build everything the harness needs once, and fail loudly if anything is wrong.

    The context files are validated here rather than on first upload: a malformed rule
    should stop the service starting, not surface halfway through a client's review.
    """
    logging.config.dictConfig(config.LOGGING_CONFIG)
    logger.info("Starting %s (flavour=%s)", config.APP_NAME, config.FLAVOUR)

    context = ContextLoader()
    context.load()

    prompts = PromptRegistry()
    prompts.load(required=frozenset(AICall))

    store = ReviewStore(STORAGE_ROOT)
    store.initialise()

    app.state.context = context
    app.state.prompts = prompts
    app.state.store = store
    provider = create_provider()
    app.state.orchestrator = ReviewOrchestrator(
        store=store,
        context=context,
        prompts=prompts,
        provider=provider,
        max_active=config.MAX_ACTIVE_REVIEWS,
        deadline_s=config.REVIEW_DEADLINE_S,
        max_upload_bytes=config.MAX_UPLOAD_BYTES,
        tool_workers=config.TOOL_WORKERS,
        structure_context_max_tokens=config.STRUCTURE_CONTEXT_MAX_TOKENS,
        figure_samples=config.FIGURE_SAMPLES,
    )
    logger.info(
        "Review harness ready: %d JE type(s), judging %s",
        len(context.catalogue),
        "enabled" if provider else "disabled (no API key configured)",
    )

    yield

    logger.info("Stopped %s", config.APP_NAME)
