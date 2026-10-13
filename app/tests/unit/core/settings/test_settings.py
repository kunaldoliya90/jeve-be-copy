"""Settings classes: per-flavour defaults and validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.settings.base import BaseConfig
from core.settings.development import DevelopmentConfig
from core.settings.production import ProductionConfig
from core.settings.staging import StagingConfig


def test_development_config_defaults_enable_debug_and_docs() -> None:
    config = DevelopmentConfig()

    assert config.FLAVOUR == "dev"
    assert config.DEBUG is True
    assert config.LOG_LEVEL == "DEBUG"
    assert config.DOCS_URL == "/docs"


def test_staging_config_defaults_disable_debug_keep_docs() -> None:
    config = StagingConfig()

    assert config.FLAVOUR == "stag"
    assert config.DEBUG is False
    assert config.LOG_LEVEL == "INFO"
    assert config.DOCS_URL == "/docs"


def test_production_config_defaults_disable_debug_and_docs() -> None:
    config = ProductionConfig()

    assert config.FLAVOUR == "prod"
    assert config.DEBUG is False
    assert config.LOG_LEVEL == "INFO"
    assert config.DOCS_URL is None
    assert config.REDOC_URL is None
    assert config.OPENAPI_URL is None


@pytest.mark.parametrize("config_class", [DevelopmentConfig, StagingConfig, ProductionConfig])
def test_config_reads_app_prefixed_env_vars(
    monkeypatch: pytest.MonkeyPatch, config_class: type[BaseConfig]
) -> None:
    monkeypatch.setenv("APP_APP_NAME", "jeve-under-test")
    monkeypatch.setenv("APP_LOG_LEVEL", "warning")
    monkeypatch.setenv("APP_API_PREFIX", "/api/other")
    monkeypatch.setenv("APP_NAME", "unprefixed-is-ignored")

    config = config_class()

    assert config.APP_NAME == "jeve-under-test"
    assert config.LOG_LEVEL == "WARNING"
    assert config.API_PREFIX == "/api/other"


def test_production_config_debug_true_is_rejected() -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        ProductionConfig(DEBUG=True)


@pytest.mark.parametrize("prefix", ["", "/", "api/jeve", "/api/jeve/", "/api jeve"])
def test_config_invalid_api_prefix_is_rejected(prefix: str) -> None:
    with pytest.raises(ValidationError, match="API_PREFIX"):
        DevelopmentConfig(API_PREFIX=prefix)


def test_config_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        DevelopmentConfig(LOG_LEVEL="VERBOSE")


def test_config_is_immutable() -> None:
    config = DevelopmentConfig()

    with pytest.raises(ValidationError, match="frozen"):
        config.DEBUG = False  # type: ignore[misc]


def test_config_dotenv_file_is_read_by_development_only(tmp_path: Path) -> None:
    # The isolated_environment fixture made tmp_path the working directory.
    (tmp_path / ".env").write_text("APP_APP_NAME=from-dotenv\n", encoding="utf-8")

    assert DevelopmentConfig().APP_NAME == "from-dotenv"
    assert StagingConfig().APP_NAME == "jeve-be"
    assert ProductionConfig().APP_NAME == "jeve-be"


def test_config_logging_config_uses_log_level() -> None:
    logging_config = StagingConfig(LOG_LEVEL="ERROR").LOGGING_CONFIG

    assert logging_config["root"]["level"] == "ERROR"


def test_config_flavour_overrides_leave_base_defaults_unchanged() -> None:
    DevelopmentConfig()

    assert BaseConfig.model_fields["DEBUG"].default is False
    assert BaseConfig.model_fields["DOCS_URL"].default is None
