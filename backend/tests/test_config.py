import pytest
from pydantic import ValidationError

from kiln.config import LOCAL_API_TOKEN, Settings


def test_defaults_leave_the_model_off() -> None:
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "off"
    assert settings.llm_api_key_value() == ""
    assert settings.database_url.startswith("postgresql://")
    assert settings.api_token_value() == LOCAL_API_TOKEN


def test_generic_port_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9999")
    monkeypatch.setenv("HTTP_PORT", "9999")
    settings = Settings(_env_file=None)
    assert settings.kiln_http_port == 8000


def test_explicit_provider_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    settings = Settings(llm_provider="off", _env_file=None)
    assert settings.llm_provider == "off"


def test_none_alias_is_off() -> None:
    settings = Settings(llm_provider="none", _env_file=None)
    assert settings.llm_provider == "off"


def test_blank_api_token_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(kiln_api_token="  ", _env_file=None)


def test_openai_requires_base_url() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_provider="openai", llm_base_url="  ", _env_file=None)


def test_openai_accepts_a_configured_endpoint() -> None:
    settings = Settings(
        llm_provider="openai-compatible",
        llm_base_url="http://llm.example/v1",
        llm_api_key="local-only",
        llm_model="demo",
        _env_file=None,
    )
    assert settings.llm_provider == "openai"
    assert settings.llm_base_url == "http://llm.example/v1"
    assert settings.llm_api_key_value() == "local-only"
