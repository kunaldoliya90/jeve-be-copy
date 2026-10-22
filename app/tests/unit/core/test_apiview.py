"""APIView: configure() options and register_route() wiring."""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from core.apiview import APIView


class ItemResponse(BaseModel):
    name: str


def require_token() -> None:
    """Stand-in dependency used to check register_route options."""


class ItemView(APIView):
    @classmethod
    @APIView.configure(response_model=ItemResponse, status_code=201, tags=["items"])
    async def post(cls) -> ItemResponse:
        return ItemResponse(name="created")

    @classmethod
    async def get(cls, item_id: int) -> ItemResponse:
        return ItemResponse(name=f"item-{item_id}")


def _routes(router: APIRouter) -> dict[str, APIRoute]:
    return {
        method: route
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
    }


def test_register_route_adds_one_route_per_implemented_method() -> None:
    router = APIRouter()

    ItemView.register_route(router, "/items/{item_id}")

    assert set(_routes(router)) == {"GET", "POST"}


def test_register_route_applies_configure_options() -> None:
    router = APIRouter()

    ItemView.register_route(router, "/items/{item_id}")

    post_route = _routes(router)["POST"]
    assert post_route.status_code == 201
    assert post_route.tags == ["items"]


def test_register_route_options_are_added_to_every_method() -> None:
    router = APIRouter()

    ItemView.register_route(router, "/items/{item_id}", dependencies=[Depends(require_token)])

    assert all(route.dependencies for route in _routes(router).values())


def test_registered_view_serves_path_parameters() -> None:
    app = FastAPI()
    router = APIRouter()
    ItemView.register_route(router, "/items/{item_id}")
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/items/7")

    assert response.status_code == 200
    assert response.json() == {"name": "item-7"}
