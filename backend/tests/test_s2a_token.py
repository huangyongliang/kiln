"""S2a acceptance for the machine token, compose, docs, and timeout bounds."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from kiln.config import (
    API_TOKEN_ERROR,
    REJECTED_API_TOKENS,
    Settings,
    get_settings,
)
from kiln.domain.builds import ProductBuild
from kiln.main import create_app, run
from tests.support import TEST_API_TOKEN, app_settings, auth_headers

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def test_missing_api_token_refuses_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance 1. Unset KILN_API_TOKEN fails in Settings and create_app."""

    monkeypatch.delenv("KILN_API_TOKEN", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="必须设置 KILN_API_TOKEN") as caught:
        Settings(_env_file=None)
    assert "kiln-local" not in str(caught.value)
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="必须设置 KILN_API_TOKEN"):
        create_app()
    listened = {"called": False}

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        listened["called"] = True
        raise AssertionError("kiln-api listened without a token")

    monkeypatch.setattr("kiln.main.uvicorn.run", fail_if_called)
    get_settings.cache_clear()
    with pytest.raises(ValidationError, match="必须设置 KILN_API_TOKEN"):
        run()
    assert listened["called"] is False


def test_rejected_and_short_tokens_do_not_echo_the_value() -> None:
    """Acceptance 2. Strip first, then reject examples, blanks, and short values."""

    short = "b" * 31
    padded_short = f" {short} "
    accepted = "c" * 32
    padded_accepted = f"  {accepted}  "
    rejected = [
        "kiln-local",
        "  kiln-local  ",
        "",
        " ",
        "\t\n",
        short,
        padded_short,
    ]
    for value in rejected:
        with pytest.raises(ValidationError, match="必须设置 KILN_API_TOKEN") as caught:
            Settings(kiln_api_token=value, _env_file=None)
        rendered = str(caught.value)
        assert "input_value" not in rendered
        if value.strip():
            assert value not in rendered
        else:
            assert repr(value) not in rendered
        stripped = value.strip()
        if stripped and stripped not in API_TOKEN_ERROR:
            assert stripped not in rendered
    ok = Settings(kiln_api_token=accepted, _env_file=None)
    assert ok.api_token_value() == accepted
    padded = Settings(kiln_api_token=padded_accepted, _env_file=None)
    assert padded.api_token_value() == accepted
    trailing = Settings(kiln_api_token=f"{'x' * 32} ", _env_file=None)
    assert trailing.api_token_value() == "x" * 32
    assert REJECTED_API_TOKENS == {"kiln-local"}
    assert "ALLOW_INSECURE" not in (BACKEND / "kiln" / "config.py").read_text(encoding="utf-8")
    assert "kiln_allow_insecure" not in (BACKEND / "kiln" / "config.py").read_text(encoding="utf-8")


def test_pending_timeout_bounds_refuse_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance 7, bounds. 60 and 86400 start; outside that range does not."""

    for value in (60, 1800, 86400):
        settings = Settings(kiln_build_pending_timeout_seconds=value, _env_file=None)
        assert settings.kiln_build_pending_timeout_seconds == value
    for value in (59, 86401):
        with pytest.raises(ValidationError):
            Settings(kiln_build_pending_timeout_seconds=value, _env_file=None)
    monkeypatch.setenv("KILN_BUILD_PENDING_TIMEOUT_SECONDS", "59")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        create_app()
    monkeypatch.setenv("KILN_BUILD_PENDING_TIMEOUT_SECONDS", "86401")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        create_app()


def test_compose_requires_a_generated_token_and_keeps_loopback_ports() -> None:
    """Acceptance 3. The token line uses :?, and every published port is loopback."""

    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "KILN_API_TOKEN: ${KILN_API_TOKEN:?请在 .env 中设置 KILN_API_TOKEN（至少 32 个字符）}"
        in text
    )
    assert "kiln-local" not in text
    assert "KILN_BUILD_PENDING_TIMEOUT_SECONDS: ${KILN_BUILD_PENDING_TIMEOUT_SECONDS:-1800}" in text
    assert "数据库口令只用于本地；KILN_API_TOKEN 必须自行生成" in text
    bindings = re.findall(r'^\s+-\s+"([^"]+)"\s*$', text, re.MULTILINE)
    assert bindings
    assert all(item.startswith("127.0.0.1:") for item in bindings)
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "不是密钥，只供 compose 插值" in workflow
    placeholder = "ci-compose-config-placeholder-0000000000"
    assert placeholder in workflow
    assert len(placeholder) >= 32
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert 'python -c "import secrets; print(secrets.token_hex(32))"' in example
    assert re.search(r"^KILN_API_TOKEN=\s*$", example, re.MULTILINE)
    assert "KILN_API_TOKEN=kiln-local" not in example
    for line in example.splitlines():
        if "kiln-local" in line:
            assert line.lstrip().startswith("#")


def test_docs_reject_the_old_token_fallback_without_banning_the_explanation() -> None:
    """Acceptance 4. Fallback wording is gone. Rejection text may name kiln-local."""

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    vue = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    config = (BACKEND / "kiln" / "config.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    phrase = "必填；缺失、空、`kiln-local` 或少于 32 个字符时拒绝启动"
    for document in (readme, deploy, example, vue):
        assert "未设置时回落" not in document
    assert phrase in readme
    assert phrase in deploy
    assert "kiln-local" not in compose
    assert config.count("kiln-local") == 1
    assert 'REJECTED_API_TOKENS = {"kiln-local"}' in config
    assert "SecretStr(LOCAL_API_TOKEN)" not in config
    assert "Bearer kiln-local" not in readme
    assert "Bearer kiln-local" not in deploy
    assert "Bearer kiln-local" not in vue
    assert "kiln-local" not in vue
    for line in deploy.splitlines():
        if "Authorization: Bearer" not in line or "KILN_API_TOKEN" not in line:
            continue
        if "<KILN_API_TOKEN>" in line:
            continue
        assert "$KILN_API_TOKEN" in line
    assert "## 首个管理员" in deploy
    assert "kiln-admin bootstrap-admin" in deploy
    assert "已存在管理员，未做改动" in deploy
    assert "库里只剩已停用的管理员时，引导命令仍退出 3" in deploy
    assert "PATCH" in deploy
    assert "POST /accounts" in deploy
    assert "KILN_BUILD_PENDING_TIMEOUT_SECONDS" in readme
    assert "KILN_BUILD_PENDING_TIMEOUT_SECONDS" in deploy
    assert "Jenkins 长期繁忙时调大这个值" in readme
    assert "Jenkins 长期繁忙时调大这个值" in deploy
    assert "not_built" in readme
    assert "not_built" in deploy


def test_runtime_flag_wording_and_test_token_stay_in_bounds() -> None:
    """Acceptance 10. The slice does not open runtime deploy or rename 用户信息同步."""

    assert ProductBuild.model_fields["deploys_to_runtime"].default is False
    assert len(TEST_API_TOKEN) >= 32
    assert "test" in TEST_API_TOKEN
    assert "not-a-secret" in TEST_API_TOKEN
    assert auth_headers()["Authorization"] == f"Bearer {TEST_API_TOKEN}"
    assert app_settings().api_token_value() == TEST_API_TOKEN
    checked = [
        ROOT / "README.md",
        ROOT / "docs" / "DEPLOY.md",
        ROOT / "backend" / "kiln" / "main.py",
        ROOT / "backend" / "kiln" / "executor" / "product.py",
        ROOT / "backend" / "kiln" / "cli.py",
        ROOT / "console" / "src" / "App.vue",
    ]
    for path in checked:
        document = path.read_text(encoding="utf-8")
        folded = document.casefold()
        assert "联邦" not in document
        assert "federation" not in folded
        assert "deploys_to_runtime = true" not in folded
        assert "://gitlab" not in folded
        assert "://nexus" not in folded
