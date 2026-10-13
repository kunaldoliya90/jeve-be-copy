"""API routes. Every view is registered here; paths are relative to ``API_PREFIX``."""

from fastapi import APIRouter

from api.consts import ApiPath
from api.views.health import HealthView

router = APIRouter()

HealthView.register_route(router, ApiPath.HEALTH)
