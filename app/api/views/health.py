"""Health check endpoint."""

from api.consts import ApiTag
from api.pydanticmodels.health_models import HealthResponse
from core.apiview import APIView


class HealthView(APIView):
    """Reports that the service is up and serving requests."""

    @classmethod
    @APIView.configure(response_model=HealthResponse, tags=[ApiTag.HEALTH], summary="Health check")
    async def get(cls) -> HealthResponse:
        return HealthResponse()
