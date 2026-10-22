"""Base class for class-based views, mirroring ``wifastapiutils.views.APIView``.

A view implements one ``@classmethod`` per HTTP method (``get``, ``post``...), configured
with ``@APIView.configure(...)``, and is registered with ``View.register_route(router, path)``.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from core.consts import ROUTE_OPTIONS_ATTR, HttpMethod


class APIView:
    """Groups the HTTP methods of one endpoint in a class."""

    @staticmethod
    def configure[F: Callable[..., Any]](**route_options: Any) -> Callable[[F], F]:
        """Attach FastAPI route options (``response_model``, ``status_code``, ``tags``...)."""

        def decorator(func: F) -> F:
            setattr(func, ROUTE_OPTIONS_ATTR, route_options)
            return func

        return decorator

    @classmethod
    def register_route(cls, router: APIRouter, path: str, **route_options: Any) -> None:
        """Add a route for every HTTP method the view implements.

        Options given here (e.g. ``dependencies``) are added to, and override, the ones from
        ``configure``.
        """
        for method in HttpMethod:
            endpoint = getattr(cls, method, None)
            if endpoint is None:
                continue
            options = {**getattr(endpoint, ROUTE_OPTIONS_ATTR, {}), **route_options}
            router.add_api_route(path, endpoint, methods=[method.upper()], **options)
