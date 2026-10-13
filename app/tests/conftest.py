"""Shared fixtures. Every test starts from an isolated configuration environment."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from core.settings.consts import ENV_PREFIX, FLAVOUR_ENV_VAR


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove FLAVOUR and ``APP_*`` variables and hide any developer ``.env`` file."""
    for name in list(os.environ):
        if name.upper() == FLAVOUR_ENV_VAR or name.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # DevelopmentConfig reads ./.env; tests decide what is there


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
