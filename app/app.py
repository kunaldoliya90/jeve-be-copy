"""FastAPI application instance, served with ``uvicorn app:app`` from the ``app/`` directory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.views import router
from applifespan import app_lifespan
from core.settings import config
from core.settings.consts import (
    CORS_ALLOW_CREDENTIALS,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    CORS_PREFLIGHT_MAX_AGE_S,
)


def _under_api_prefix(path: str | None) -> str | None:
    return f"{config.API_PREFIX}{path}" if path else None


app = FastAPI(
    title=config.APP_NAME,
    debug=config.DEBUG,
    lifespan=app_lifespan,
    docs_url=_under_api_prefix(config.DOCS_URL),
    redoc_url=_under_api_prefix(config.REDOC_URL),
    openapi_url=_under_api_prefix(config.OPENAPI_URL),
)


# A browser sends the upload and reads the progress stream from another origin, so the
# allowed ones have to be named here; with none configured the middleware is left off
# rather than added with an empty list, which would answer every preflight with a 400.
if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.CORS_ORIGINS),
        allow_credentials=CORS_ALLOW_CREDENTIALS,
        allow_methods=list(CORS_ALLOW_METHODS),
        allow_headers=list(CORS_ALLOW_HEADERS),
        max_age=CORS_PREFLIGHT_MAX_AGE_S,
    )

app.include_router(router, prefix=config.API_PREFIX)
