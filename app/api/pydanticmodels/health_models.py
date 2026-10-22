"""Health check models."""

from typing import Literal

from pydantic import BaseModel, Field

from api.consts import HealthStatus


class HealthResponse(BaseModel):
    """Response of the health check endpoint."""

    status: Literal[HealthStatus.OK] = Field(
        default=HealthStatus.OK, description="Service health status"
    )
