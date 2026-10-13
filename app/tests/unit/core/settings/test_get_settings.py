"""FLAVOUR-based selection of the configuration class."""

import pytest

from core.settings import get_settings
from core.settings.base import BaseConfig
from core.settings.development import DevelopmentConfig
from core.settings.production import ProductionConfig
from core.settings.staging import StagingConfig


@pytest.mark.parametrize(
    ("flavour", "expected_class"),
    [
        ("dev", DevelopmentConfig),
        ("stag", StagingConfig),
        ("prod", ProductionConfig),
        (" PROD ", ProductionConfig),
    ],
)
def test_get_settings_flavour_selects_config_class(
    monkeypatch: pytest.MonkeyPatch, flavour: str, expected_class: type[BaseConfig]
) -> None:
    monkeypatch.setenv("FLAVOUR", flavour)

    assert type(get_settings()) is expected_class


def test_get_settings_without_flavour_defaults_to_development() -> None:
    assert type(get_settings()) is DevelopmentConfig


@pytest.mark.parametrize("flavour", ["", "development", "staging", "production", "local"])
def test_get_settings_unknown_flavour_raises(monkeypatch: pytest.MonkeyPatch, flavour: str) -> None:
    monkeypatch.setenv("FLAVOUR", flavour)

    with pytest.raises(ValueError, match="Flavour"):
        get_settings()


def test_get_settings_returns_independent_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLAVOUR", "prod")
    production = get_settings()
    monkeypatch.setenv("FLAVOUR", "dev")
    development = get_settings()

    assert production.DEBUG is False
    assert development.DEBUG is True
