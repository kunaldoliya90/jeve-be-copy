"""FastAPI application instance, served with ``uvicorn app:app`` from the ``app/`` directory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
# No credentials cross-origin: the API has no auth or cookies, so none are needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=config.API_PREFIX)
