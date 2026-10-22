"""Shared fixtures. Every test starts from an isolated configuration environment."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import applifespan
from app import app
from core.settings.consts import ENV_PREFIX, FLAVOUR_ENV_VAR
from review.orchestrator.orchestrator import ReviewOrchestrator
from review.storage.reviews import ReviewStore
from tests.doubles import StubLLMProvider


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remove FLAVOUR and ``APP_*`` variables and hide any developer ``.env`` file."""
    for name in list(os.environ):
        if name.upper() == FLAVOUR_ENV_VAR or name.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # DevelopmentConfig reads ./.env; tests decide what is there


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches a model unless it asks for one.

    ``config`` is built when ``core.settings`` is imported, which is before any fixture
    runs, so clearing the environment above does not unconfigure a developer's key. Without
    this the integration suite would call a real endpoint — billed, slow, and answering
    differently on every run.
    """
    monkeypatch.setattr(applifespan, "create_provider", lambda: None)


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch) -> StubLLMProvider:
    """Give the app a model that answers from a schema instead of from the network.

    Request it in a test that needs the judging or reporting path to run; the autouse
    fixture above leaves the app with no provider at all.
    """
    stub = StubLLMProvider()
    monkeypatch.setattr(applifespan, "create_provider", lambda: stub)
    return stub


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def stub_client(stub_model: StubLLMProvider) -> Iterator[TestClient]:
    """A client whose app judges and reports through the stub.

    Declared as its own fixture rather than asking a test for both: the provider has to be
    replaced before the lifespan builds the orchestrator, and fixture order in a test
    signature is a poor thing to depend on for that.
    """
    with TestClient(app) as test_client:
        yield test_client


def store_of(test_client: TestClient) -> ReviewStore:
    """The running app's review store.

    ``TestClient.app`` is typed as a bare ASGI callable, so the cast is here once rather
    than as an ignore comment at every call site.
    """
    store = cast(FastAPI, test_client.app).state.store
    assert isinstance(store, ReviewStore)
    return store


def orchestrator_of(test_client: TestClient) -> ReviewOrchestrator:
    orchestrator = cast(FastAPI, test_client.app).state.orchestrator
    assert isinstance(orchestrator, ReviewOrchestrator)
    return orchestrator
