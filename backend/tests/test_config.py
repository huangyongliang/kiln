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


def test_oidc_is_unset_until_every_required_value_is_present() -> None:
    settings = Settings(_env_file=None)
    assert settings.oidc_configured is False
    assert settings.oidc_scopes() == ""
    assert settings.kiln_session_ttl_seconds == 12 * 60 * 60
    assert settings.kiln_session_cookie_secure is False
    with pytest.raises(ValidationError, match="KILN_OIDC_ISSUER_URL"):
        Settings(kiln_oidc_issuer_url="http://127.0.0.1:9", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(kiln_oidc_issuer_url="http://user:secret@127.0.0.1:9", _env_file=None)
    configured = Settings(
        kiln_oidc_issuer_url="http://127.0.0.1:9",
        kiln_oidc_client_id="kiln-test-client",
        kiln_oidc_client_secret="local-only",
        kiln_oidc_redirect_uri="http://127.0.0.1:9/api/v1/auth/oidc/callback",
        kiln_oidc_employment_claim="employment",
        kiln_oidc_employment_active_values="active, employed",
        _env_file=None,
    )
    assert configured.oidc_configured is True
    assert configured.oidc_scopes() == "openid profile email"
    assert configured.oidc_employment_active_values() == {"active", "employed"}
    assert configured.api_token_value() == LOCAL_API_TOKEN


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
    assert settings.llm_catalog() == ["demo"]
    assert settings.llm_request_default() == "demo"


def test_llm_models_strips_drops_empty_and_dedupes_in_order() -> None:
    settings = Settings(
        llm_provider="openai",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_models="  beta , alpha, , beta ,alpha, gamma ",
        llm_model="",
        _env_file=None,
    )
    assert settings.llm_model_ids() == ["beta", "alpha", "gamma"]
    assert settings.llm_catalog() == ["beta", "alpha", "gamma"]
    assert settings.llm_request_default() == "beta"
    repeated = Settings(
        llm_provider="off",
        llm_models=",".join(["same-model"] * 40),
        _env_file=None,
    )
    assert repeated.llm_model_ids() == ["same-model"]


def test_empty_llm_models_catalog_equals_llm_model() -> None:
    one = Settings(
        llm_provider="openai",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_models=" , ",
        llm_model="only-model",
        _env_file=None,
    )
    assert one.llm_model_ids() == []
    assert one.llm_catalog() == ["only-model"]
    assert one.llm_request_default() == "only-model"
    empty = Settings(
        llm_provider="openai",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_models="",
        llm_model="",
        _env_file=None,
    )
    assert empty.llm_catalog() == []
    assert empty.llm_request_default() == ""


def test_missing_llm_model_is_prepended_and_is_not_a_parse_error() -> None:
    listed = ",".join(f"model-{index}" for index in range(32))
    settings = Settings(
        llm_provider="openai",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_models=listed,
        llm_model="default-model",
        _env_file=None,
    )
    assert settings.llm_model_ids() == [f"model-{index}" for index in range(32)]
    catalog = settings.llm_catalog()
    assert catalog[0] == "default-model"
    assert len(catalog) == 33
    assert settings.llm_request_default() == "default-model"
    already = Settings(
        llm_provider="openai",
        llm_base_url="http://127.0.0.1:9/v1",
        llm_models="model-b, model-a",
        llm_model="model-a",
        _env_file=None,
    )
    assert already.llm_catalog() == ["model-b", "model-a"]
    assert already.llm_request_default() == "model-a"


def test_llm_models_item_longer_than_128_refuses_startup() -> None:
    # The whole process configuration is rejected. The long id is not truncated
    # and the earlier items are not kept.
    with pytest.raises(ValidationError, match="不会截断"):
        Settings(
            llm_provider="off",
            llm_models="ok," + ("m" * 129),
            _env_file=None,
        )
    exact = Settings(
        llm_provider="off",
        llm_models=" " + ("m" * 128) + " ",
        _env_file=None,
    )
    assert exact.llm_model_ids() == ["m" * 128]


def test_llm_models_over_32_items_refuses_startup() -> None:
    # More than 32 ids after trim, drop, and dedupe refuses startup. The first
    # 32 are not silently kept. Duplicates do not count as extra items.
    too_many = ",".join(f"model-{index}" for index in range(33))
    with pytest.raises(ValidationError, match="不会只保留前 32"):
        Settings(llm_provider="off", llm_models=too_many, _env_file=None)
    padded = ",".join(["model-0"] * 10 + [f"model-{index}" for index in range(32)])
    settings = Settings(llm_provider="off", llm_models=padded, _env_file=None)
    assert settings.llm_model_ids() == [f"model-{index}" for index in range(32)]


def test_mock_and_off_do_not_apply_the_model_catalog() -> None:
    for provider in ("mock", "off"):
        settings = Settings(
            llm_provider=provider,
            llm_model="default-model",
            llm_models="model-a, model-b",
            _env_file=None,
        )
        assert settings.llm_model_ids() == ["model-a", "model-b"]
        assert settings.llm_catalog() == []
        assert settings.llm_seed_ids() == ["default-model", "model-a", "model-b"]
