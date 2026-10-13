"""Constants and enums for core functionality."""

from enum import StrEnum
from typing import Final


class HttpMethod(StrEnum):
    """HTTP methods a view can implement, named as the view's classmethods."""

    GET = "get"
    POST = "post"
    PUT = "put"
    PATCH = "patch"
    DELETE = "delete"


ROUTE_OPTIONS_ATTR: Final = "__route_options__"

APP_IMPORT_PATH: Final = "app:app"
RUNSERVER_HOST: Final = "127.0.0.1"
RUNSERVER_PORT: Final = 8000
