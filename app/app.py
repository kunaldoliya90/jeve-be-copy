"""FastAPI application instance, served with ``uvicorn app:app`` from the ``app/`` directory."""

from fastapi import FastAPI

from api.views import router
from applifespan import app_lifespan
from core.settings import config


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
app.include_router(router, prefix=config.API_PREFIX)
