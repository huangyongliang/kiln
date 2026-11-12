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


def test_jenkins_and_git_are_off_until_configured() -> None:
    settings = Settings(_env_file=None)
    assert settings.jenkins_url == ""
    assert settings.git_provider == "off"
    assert settings.git_base_url == ""
    assert settings.executor_configured is False
    assert settings.git_configured is False


def test_jenkins_requires_url_user_and_token_together() -> None:
    with pytest.raises(ValidationError):
        Settings(jenkins_url="http://127.0.0.1:9", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(jenkins_url="http://user:secret@127.0.0.1:9", _env_file=None)


def test_git_provider_requires_base_url_and_token() -> None:
    with pytest.raises(ValidationError, match="GIT_BASE_URL"):
        Settings(git_provider="gitlab", _env_file=None)
    with pytest.raises(ValidationError, match="GIT_BASE_URL"):
        Settings(git_provider="gitea", _env_file=None)
    with pytest.raises(ValidationError, match="GIT_TOKEN"):
        Settings(git_provider="gitlab", git_base_url="http://127.0.0.1:9", _env_file=None)


def test_git_clone_base_falls_back_and_rejects_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(git_clone_base_url="http://user:secret@127.0.0.1:9", _env_file=None)
    empty = Settings(_env_file=None)
    assert empty.git_clone_base() == ""
    configured = Settings(
        git_provider="gitea",
        git_base_url="http://127.0.0.1:9",
        git_token="local-only",
        git_clone_base_url="http://git.internal:3000",
        _env_file=None,
    )
    assert configured.git_clone_base() == "http://git.internal:3000"
    fallback = Settings(
        git_provider="gitlab",
        git_base_url="http://127.0.0.1:9",
        git_token="local-only",
        _env_file=None,
    )
    assert fallback.git_clone_base() == "http://127.0.0.1:9"


def test_blank_git_provider_is_off() -> None:
    settings = Settings(git_provider="disabled", _env_file=None)
    assert settings.git_provider == "off"


def test_artifact_storage_defaults_to_a_local_directory() -> None:
    settings = Settings(_env_file=None)
    assert settings.kiln_artifact_root == "var/kiln-artifacts"
    assert settings.kiln_artifact_max_bytes == 64 * 1024 * 1024


def test_artifact_root_rejects_blank_and_urls() -> None:
    with pytest.raises(ValidationError, match="KILN_ARTIFACT_ROOT"):
        Settings(kiln_artifact_root="  ", _env_file=None)
    with pytest.raises(ValidationError, match="本地目录"):
        Settings(kiln_artifact_root="http://127.0.0.1:9/repo", _env_file=None)


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
