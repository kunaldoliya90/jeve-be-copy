"""API routes. Every view is registered here; paths are relative to ``API_PREFIX``."""

from fastapi import APIRouter

from api.consts import ApiPath
from api.views.health import HealthView
from api.views.je_types import JETypeDetailView, JETypesView
from api.views.reviews import ReviewEventsView, ReviewsView, ReviewView

router = APIRouter()

HealthView.register_route(router, ApiPath.HEALTH)
ReviewsView.register_route(router, ApiPath.REVIEWS)
ReviewView.register_route(router, ApiPath.REVIEW)
ReviewEventsView.register_route(router, ApiPath.REVIEW_EVENTS)
JETypesView.register_route(router, ApiPath.JE_TYPES)
JETypeDetailView.register_route(router, ApiPath.JE_TYPE)
