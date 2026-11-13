"""Dependency proxy unit tests. No network: Nexus is an httpx.MockTransport."""

import base64
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import text

from kiln.config import Settings
from kiln.domain.git import ResolvedCommit
from kiln.errors import NEXUS_UNCONFIGURED_DETAIL, UpstreamFailure
from kiln.executor.jenkins import JenkinsClient
from kiln.executor.product import build_log, nexus_build_parameters
from kiln.executor.script import product_build_command, product_job_config
from kiln.main import create_app
from kiln.nexus.client import NexusClient
from tests.support import app_settings, auth_headers, truncate_domain
from tests.test_nexus_docking import _REQUIRED, _build_script, load_docking_targets

_PASSWORD = "nexus-secret-value"
_URL = "http://127.0.0.1:9"
_REPO = "demo.group"
_UNSET_JOB_CONFIG = (
    "<?xml version='1.1' encoding='UTF-8'?>\n"
    "<project>\n"
    "  <description>kiln product build</description>\n"
    "  <keepDependencies>false</keepDependencies>\n"
    "  <properties/>\n"
    '  <scm class="hudson.scm.NullSCM"/>\n'
    "  <canRoam>true</canRoam>\n"
    "  <disabled>false</disabled>\n"
    "  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>\n"
    "  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>\n"
    "  <triggers/>\n"
    "  <concurrentBuild>false</concurrentBuild>\n"
    "  <quietPeriod>0</quietPeriod>\n"
    "  <builders>\n"
    "    <hudson.tasks.Shell>\n"
    "      <command>echo kiln\n"
    "</command>\n"
    "    </hudson.tasks.Shell>\n"
    "  </builders>\n"
    "  <publishers>\n"
    "    <hudson.tasks.ArtifactArchiver>\n"
    "      <artifacts>src/kiln-dist/artifact.bin</artifacts>\n"
    "      <allowEmptyArchive>false</allowEmptyArchive>\n"
    "      <onlyIfSuccessful>true</onlyIfSuccessful>\n"
    "      <fingerprint>false</fingerprint>\n"
    "      <defaultExcludes>true</defaultExcludes>\n"
    "      <caseSensitive>true</caseSensitive>\n"
    "      <followSymlinks>false</followSymlinks>\n"
    "    </hudson.tasks.ArtifactArchiver>\n"
    "  </publishers>\n"
    "  <buildWrappers/>\n"
    "</project>\n"
)


def test_nexus_is_off_until_configured() -> None:
    settings = Settings(_env_file=None)
    assert settings.kiln_nexus_url == ""
    assert settings.kiln_nexus_repository == ""
    assert settings.kiln_nexus_build_url == ""
    assert settings.nexus_password_value() == ""
    assert settings.kiln_nexus_timeout_seconds == 10
    assert settings.nexus_configured is False
    assert settings.nexus_build_base() == ""
    assert nexus_build_parameters(settings) is None


def test_nexus_url_and_repository_are_paired() -> None:
    with pytest.raises(ValidationError, match="KILN_NEXUS_URL 和 KILN_NEXUS_REPOSITORY"):
        Settings(kiln_nexus_url=_URL, _env_file=None)
    with pytest.raises(ValidationError, match="KILN_NEXUS_URL 和 KILN_NEXUS_REPOSITORY"):
        Settings(kiln_nexus_repository=_REPO, _env_file=None)


def test_nexus_credentials_are_paired_and_blank_is_anonymous() -> None:
    with pytest.raises(ValidationError, match="KILN_NEXUS_USERNAME 和 KILN_NEXUS_PASSWORD"):
        _configured(kiln_nexus_username="reader", kiln_nexus_password="")
    with pytest.raises(ValidationError, match="KILN_NEXUS_USERNAME 和 KILN_NEXUS_PASSWORD"):
        _configured(kiln_nexus_username="", kiln_nexus_password=_PASSWORD)
    anonymous = _configured(kiln_nexus_username="  ", kiln_nexus_password="  ")
    assert anonymous.kiln_nexus_username == ""
    assert anonymous.nexus_password_value() == ""
    assert anonymous.nexus_configured is True


def test_build_url_or_credentials_require_the_proxy_url() -> None:
    with pytest.raises(ValidationError, match="未设置 KILN_NEXUS_URL"):
        Settings(kiln_nexus_build_url=_URL, _env_file=None)
    with pytest.raises(ValidationError, match="未设置 KILN_NEXUS_URL"):
        Settings(
            kiln_nexus_username="reader",
            kiln_nexus_password=_PASSWORD,
            _env_file=None,
        )


def test_nexus_url_rejects_userinfo_query_and_bad_repository() -> None:
    with pytest.raises(ValidationError, match="用户名或口令"):
        _configured(kiln_nexus_url=f"http://reader:{_PASSWORD}@127.0.0.1:9")
    with pytest.raises(ValidationError, match="查询串或片段"):
        _configured(kiln_nexus_url="http://127.0.0.1:9/path?q=1")
    with pytest.raises(ValidationError, match="KILN_NEXUS_REPOSITORY 格式不正确"):
        _configured(kiln_nexus_repository="../group")
    with pytest.raises(ValidationError, match="KILN_NEXUS_REPOSITORY 格式不正确"):
        _configured(kiln_nexus_repository="a" * 129)
    accepted = _configured(kiln_nexus_repository="a" * 128, kiln_nexus_build_url="http://127.0.0.1:10")
    assert accepted.nexus_build_base() == "http://127.0.0.1:10"
    fallback = _configured()
    assert fallback.nexus_build_base() == _URL


def test_nexus_password_rejects_newlines_and_overlong_values() -> None:
    with pytest.raises(ValidationError, match="不能包含换行") as newline:
        _configured(kiln_nexus_password=f"line1\n{_PASSWORD}")
    assert _PASSWORD not in str(newline.value)
    with pytest.raises(ValidationError, match="长度不能超过 256") as huge:
        _configured(kiln_nexus_password="p" * 257)
    assert "p" * 257 not in str(huge.value)
    with pytest.raises(ValidationError, match="KILN_NEXUS_USERNAME 不能包含换行"):
        _configured(kiln_nexus_username="reader\nname", kiln_nexus_password=_PASSWORD)
    with pytest.raises(ValidationError):
        _configured(kiln_nexus_timeout_seconds=0)
    with pytest.raises(ValidationError):
        _configured(kiln_nexus_timeout_seconds=61)
    assert _configured(kiln_nexus_timeout_seconds=60).kiln_nexus_timeout_seconds == 60


def test_secret_is_absent_from_settings_and_client_repr() -> None:
    settings = _configured()
    assert _PASSWORD not in repr(settings)
    assert _PASSWORD not in str(settings)
    client = _nexus_client(lambda _request: httpx.Response(500))
    assert _PASSWORD not in repr(client)
    assert _PASSWORD not in str(client)
    assert isinstance(settings.kiln_nexus_password, SecretStr)


def test_probe_maps_status_codes_and_hides_the_secret() -> None:
    secret_body = f"denied {_PASSWORD}"

    def status(code: int, body: bytes | None = None, json_body: object | None = None) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.query == b""
            assert _PASSWORD not in str(request.url)
            if request.url.path != "/service/rest/v1/status":
                raise AssertionError(request.url.path)
            if json_body is not None:
                return httpx.Response(code, json=json_body)
            return httpx.Response(code, content=body or b"")

        return handler

    ok = _probe(status(200, json_body={"status": "ok"}), repositories=[{"name": _REPO}])
    assert ok is None
    denied = _probe(status(401, body=secret_body.encode()))
    assert isinstance(denied, UpstreamFailure)
    assert "拒绝访问" in denied.detail
    assert _PASSWORD not in denied.detail
    assert _PASSWORD not in repr(denied)
    forbidden = _probe(status(403))
    assert forbidden is not None
    assert "拒绝访问" in forbidden.detail
    missing = _probe(status(404))
    assert missing is not None
    assert "HTTP 404" in missing.detail
    assert "没有这个仓库" not in missing.detail

    def connect(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    offline = _probe(connect)
    assert offline is not None
    assert offline.detail == "Nexus 无法连接（127.0.0.1:9）"
    assert _PASSWORD not in offline.detail
    assert offline.__cause__ is not None
    assert _PASSWORD not in str(offline.__cause__)

    messy = _probe(status(200, body=b"not-json"))
    assert messy is not None
    assert "不是 JSON" in messy.detail
    assert _PASSWORD not in messy.detail


def test_probe_does_not_follow_redirects() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(302, headers={"Location": f"{_URL}/?pw={_PASSWORD}"})

    failure = _probe(handler)
    assert calls == ["/service/rest/v1/status"]
    assert failure is not None
    assert "HTTP 302" in failure.detail
    assert _PASSWORD not in failure.detail


def test_probe_falls_back_when_the_repository_list_hides_the_group() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/service/rest/v1/status":
            return httpx.Response(200, json={})
        if request.url.path == "/service/rest/v1/repositories":
            return httpx.Response(403, text=f"hidden {_PASSWORD}")
        if request.url.path == f"/repository/{_REPO}/":
            return httpx.Response(200, content=b"ok")
        raise AssertionError(request.url.path)

    assert _probe(handler) is None
    assert calls == [
        "/service/rest/v1/status",
        "/service/rest/v1/repositories",
        f"/repository/{_REPO}/",
    ]


def test_probe_falls_back_when_the_list_omits_the_repository() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status"):
            return httpx.Response(200, content=b"")
        if request.url.path.endswith("/repositories"):
            return httpx.Response(200, json=[{"name": "other-repo"}])
        if request.url.path == "/repository/missing-repo/":
            return httpx.Response(404, text=f"gone {_PASSWORD}")
        raise AssertionError(request.url.path)

    client = _nexus_client(handler, repository="missing-repo")
    with pytest.raises(UpstreamFailure) as caught:
        client.probe()
    assert "没有这个仓库" in caught.value.detail
    assert _PASSWORD not in caught.value.detail


def test_group_root_400_is_reachable_and_404_is_not() -> None:
    def root_400(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/service/rest/v1/status":
            return httpx.Response(200, json={})
        if request.url.path == "/service/rest/v1/repositories":
            return httpx.Response(403)
        if request.url.path == f"/repository/{_REPO}/":
            return httpx.Response(400, content=b"group root")
        raise AssertionError(request.url.path)

    reached = _status(root_400)
    assert reached["reachable"] is True
    assert reached["detail"] is None

    def root_404(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/service/rest/v1/status":
            return httpx.Response(200, content=b"")
        if request.url.path == "/service/rest/v1/repositories":
            return httpx.Response(200, json=[{"name": "other-repo"}])
        if request.url.path == f"/repository/{_REPO}/":
            return httpx.Response(404, text=f"gone {_PASSWORD}")
        raise AssertionError(request.url.path)

    missing = _status(root_404)
    assert missing["reachable"] is False
    assert "没有这个仓库" in str(missing["detail"])
    assert _PASSWORD not in str(missing)

    def root_401(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/service/rest/v1/status":
            return httpx.Response(200, json={})
        if request.url.path == "/service/rest/v1/repositories":
            return httpx.Response(404)
        if request.url.path == f"/repository/{_REPO}/":
            return httpx.Response(401, text=f"denied {_PASSWORD}")
        raise AssertionError(request.url.path)

    denied = _status(root_401)
    assert denied["reachable"] is False
    assert "拒绝访问" in str(denied["detail"])
    assert _PASSWORD not in str(denied)


def test_probe_sends_basic_auth_only_when_credentials_are_set() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json=[{"name": _REPO}])

    assert _probe(handler, username="", password="") is None
    assert seen["authorization"] is None
    assert _probe(handler) is None
    header = seen["authorization"]
    assert header is not None
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == f"reader:{_PASSWORD}"


def test_unset_status_makes_no_outbound_call() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unset proxy called {request.method} {request.url}")

    with httpx.Client(transport=httpx.MockTransport(explode), trust_env=False) as http:
        app = create_app(app_settings(), nexus_http_client=http)
        with TestClient(app, headers=auth_headers()) as client:
            response = client.get("/api/v1/dependency-proxy")
    assert response.status_code == 200
    assert response.json() == {
        "kind": "off",
        "configured": False,
        "base_url": None,
        "repository": None,
        "auth": None,
        "reachable": None,
        "detail": NEXUS_UNCONFIGURED_DETAIL,
        "publishes_artifacts": False,
    }
    assert _PASSWORD not in response.text


def test_configured_status_reports_reachability_without_the_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert _PASSWORD not in str(request.url)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={})
        return httpx.Response(401, text=f"nope {_PASSWORD}")

    body = _status(handler)
    assert body["kind"] == "nexus"
    assert body["configured"] is True
    assert body["reachable"] is False
    assert body["auth"] == "credentials"
    assert body["publishes_artifacts"] is False
    assert body["base_url"] == _URL
    assert "拒绝访问" in body["detail"]
    assert _PASSWORD not in str(body)

    def ok(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repositories"):
            return httpx.Response(200, json=[{"name": _REPO}])
        return httpx.Response(200, json={})

    reached = _status(ok)
    assert reached["reachable"] is True
    assert reached["detail"] is None
    assert reached["publishes_artifacts"] is False
    assert _PASSWORD not in str(reached)


def test_reader_is_forbidden_before_the_proxy_is_called() -> None:
    calls: list[str] = []

    def explode(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise AssertionError("permission check probed Nexus")

    with httpx.Client(transport=httpx.MockTransport(explode), trust_env=False) as http:
        app = create_app(_settings(), nexus_http_client=http)
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(app.state.engine)
            created = client.post(
                "/api/v1/accounts",
                json={
                    "username": "ada_proxy",
                    "display_name": "ada_proxy",
                    "password": "correct-horse-1",
                    "role_ids": ["reader"],
                },
            )
            assert created.status_code == 201, created.text
            reader = TestClient(app)
            logged = reader.post(
                "/api/v1/auth/login",
                json={"username": "ada_proxy", "password": "correct-horse-1"},
            )
            assert logged.status_code == 200, logged.text
            denied = reader.get("/api/v1/dependency-proxy")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "没有权限"
    assert calls == []
    assert _PASSWORD not in denied.text


def test_unset_product_job_config_matches_the_golden() -> None:
    command = "echo kiln\n"
    assert product_job_config(command) == _UNSET_JOB_CONFIG
    assert product_job_config(command, nexus_parameters=False) == _UNSET_JOB_CONFIG
    assert "KILN_NEXUS_" not in _UNSET_JOB_CONFIG
    assert "ParametersDefinitionProperty" not in _UNSET_JOB_CONFIG


def test_nexus_job_config_declares_empty_parameter_defaults() -> None:
    xml = product_job_config("echo kiln\n", nexus_parameters=True)
    assert xml != _UNSET_JOB_CONFIG
    assert _PASSWORD not in xml
    assert "hudson.model.ParametersDefinitionProperty" in xml
    assert "hudson.model.PasswordParameterDefinition" in xml
    for name in (
        "KILN_NEXUS_URL",
        "KILN_NEXUS_REPOSITORY",
        "KILN_NEXUS_USERNAME",
        "KILN_NEXUS_PASSWORD",
    ):
        assert f"<name>{name}</name>" in xml
    assert xml.count("<defaultValue></defaultValue>") == 4
    assert "pip.conf" not in xml
    assert "PIP_INDEX_URL" not in xml
    command = product_build_command(
        clone_url="http://kiln@127.0.0.1:9/ada/demo.git",
        ref="main",
        sha="ab" * 20,
        git_user="kiln",
        git_token="unit-token-value",
    )
    assert "KILN_NEXUS_PASSWORD" not in command
    assert command.index("unset KILN_GIT_TOKEN") < command.index("set -x")
    assert command.index("set -x") < command.index("sh kiln-build.sh")


def test_kiln_does_not_configure_package_managers() -> None:
    root = Path(__file__).resolve().parents[1] / "kiln"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "PIP_INDEX_URL" not in text, path
        assert "pip.conf" not in text, path
        assert ".npmrc" not in text, path
        assert "settings.xml" not in text, path
        assert "subprocess" not in text, path


def test_trigger_without_proxy_stays_on_build() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/crumbIssuer/api/json"):
            return httpx.Response(404)
        seen.append(request)
        return httpx.Response(201, headers={"Location": "/queue/item/4/"})

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        JenkinsClient(_URL, "kiln", "jenkins-token", http).trigger_build("demo")
    assert len(seen) == 1
    assert seen[0].url.path == "/job/demo/build"
    assert "buildWithParameters" not in seen[0].url.path
    assert seen[0].url.query == b""
    assert seen[0].content == b""


def test_trigger_posts_parameters_in_the_body_and_redacts_errors() -> None:
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/crumbIssuer/api/json"):
            return httpx.Response(
                200,
                json={"crumbRequestField": "Jenkins-Crumb", "crumb": "abc"},
            )
        posts.append(request)
        assert request.url.query == b""
        assert _PASSWORD not in str(request.url)
        assert request.headers["Jenkins-Crumb"] == "abc"
        parsed = parse_qs(request.content.decode(), keep_blank_values=True)
        assert parsed["KILN_NEXUS_PASSWORD"] == [_PASSWORD]
        assert parsed["KILN_NEXUS_URL"] == ["http://127.0.0.1:10"]
        if len(posts) == 1:
            return httpx.Response(403, text=f"No valid crumb was included {_PASSWORD}")
        return httpx.Response(500, text=f"boom {_PASSWORD}")

    parameters = {
        "KILN_NEXUS_URL": "http://127.0.0.1:10",
        "KILN_NEXUS_REPOSITORY": _REPO,
        "KILN_NEXUS_USERNAME": "reader",
        "KILN_NEXUS_PASSWORD": _PASSWORD,
    }
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        client = JenkinsClient(_URL, "kiln", "jenkins-token", http)
        with pytest.raises(UpstreamFailure) as caught:
            client.trigger_build("demo", parameters=parameters)
    assert len(posts) == 2
    assert all(item.url.path == "/job/demo/buildWithParameters" for item in posts)
    assert _PASSWORD not in caught.value.detail
    assert "HTTP 500" in caught.value.detail


def test_create_product_job_posts_empty_parameter_defaults() -> None:
    posted = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        if request.url.path.endswith("/crumbIssuer/api/json"):
            return httpx.Response(404)
        posted = request.content.decode()
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        JenkinsClient(_URL, "kiln", "jenkins-token", http).create_product_job(
            "demo",
            clone_url="http://kiln@127.0.0.1:9/ada/demo.git",
            ref="main",
            sha="cd" * 20,
            git_user="kiln",
            git_token="unit-token-value",
            declare_nexus_parameters=True,
        )
    assert "PasswordParameterDefinition" in posted
    assert "<defaultValue></defaultValue>" in posted
    assert _PASSWORD not in posted


def test_build_log_redacts_the_nexus_password(tmp_path: Path) -> None:
    git_token = "git-token-value"
    settings = _settings(git_provider="gitea", git_base_url=_URL, git_token=git_token)
    app = create_app(settings, nexus_http_client=_idle_client())

    class Jenkins:
        def read_progress(self, *_args: object, **_kwargs: object) -> tuple[str, int]:
            return "success", 1

        def read_log(self, _job_name: str, _build_number: int) -> tuple[str, bool]:
            return f"token {git_token} password {_PASSWORD}\n", False

    app.state.jenkins = Jenkins()
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        artifact = client.post(
            "/api/v1/artifacts",
            json={"name": "proxy-log", "package_type": "generic"},
        )
        assert artifact.status_code == 201, artifact.text
        build_id = uuid4()
        app.state.builds.create_build(
            build_id=build_id,
            name="proxy-log",
            jenkins_job_name=f"kiln-build-{build_id.hex}",
            repository="ada/demo",
            ref="main",
            commit_sha="ab" * 20,
            artifact_id=artifact.json()["id"],
            version="1",
            package_type="generic",
            queue_url=None,
        )
        logged = build_log(app.state.builds, app.state.jenkins, settings, build_id)
        via_api = client.get(f"/api/v1/builds/{build_id}/log")
        with app.state.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM product_builds")).one()
    assert _PASSWORD not in logged.text
    assert git_token not in logged.text
    assert "***" in logged.text
    assert via_api.status_code == 200
    assert _PASSWORD not in via_api.text
    assert git_token not in via_api.text
    assert _PASSWORD not in str(tuple(row))


def test_product_build_does_not_preflight_the_proxy(tmp_path: Path) -> None:
    calls: list[str] = []
    recorded: dict[str, object] = {}

    def explode(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise AssertionError("product build probed Nexus")

    app = create_app(
        _settings(
            kiln_nexus_build_url="http://127.0.0.1:10",
            kiln_artifact_root=str(tmp_path),
            git_provider="gitea",
            git_base_url=_URL,
            git_token="unit-token-value",
        ),
        nexus_http_client=httpx.Client(transport=httpx.MockTransport(explode), trust_env=False),
    )
    app.state.git_source = _Git()
    app.state.jenkins = _Jenkins(recorded)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        created = client.post(
            "/api/v1/builds",
            json={
                "repository": "ada/demo",
                "ref": "main",
                "artifact_name": "proxy-pkg",
                "version": "1.0.0",
                "package_type": "generic",
            },
        )
        assert created.status_code == 201, created.text
        assert _PASSWORD not in created.text
        with app.state.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM product_builds")).one()
    assert calls == []
    assert recorded["declare"] is True
    parameters = recorded["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["KILN_NEXUS_URL"] == "http://127.0.0.1:10"
    assert parameters["KILN_NEXUS_PASSWORD"] == _PASSWORD
    assert _PASSWORD not in str(tuple(row))


def test_docking_skips_when_the_nexus_url_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_docking_env(monkeypatch)
    monkeypatch.setenv("KILN_SKIP_EXTERNAL_TESTS", "1")
    monkeypatch.setenv("KILN_TEST_NEXUS_PASSWORD", _PASSWORD)
    with pytest.raises(pytest.skip.Exception) as caught:
        load_docking_targets()
    assert "KILN_TEST_NEXUS_URL" in str(caught.value)
    assert _PASSWORD not in str(caught.value)


_CI_NEXUS = Path(__file__).resolve().parents[2] / "scripts" / "ci-nexus.sh"
_CURL_USER_ESCAPE = r'u=${u//\\/\\\\}; u=${u//\"/\\\"}'
_CURL_PASS_ESCAPE = r'p=${p//\\/\\\\}; p=${p//\"/\\\"}'
# Quote, backslash, colon, and a space. The backslash is followed by n, not a newline.
_SPECIAL_USER = 'a"b\\c'
_SPECIAL_PASSWORD = 'p"a\\n :word'


class _AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _AuthServer)
        server.authorization = self.headers.get("Authorization")
        body = server.body
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class _AuthServer(ThreadingHTTPServer):
    authorization: str | None
    body: bytes

    def __init__(self, body: bytes) -> None:
        super().__init__(("127.0.0.1", 0), _AuthHandler)
        self.body = body
        self.authorization = None


@contextmanager
def _auth_stub(body: bytes) -> Iterator[_AuthServer]:
    server = _AuthServer(body)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _escape_curl_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _expected_config(user: str, password: str) -> bytes:
    return (
        'user = "' + _escape_curl_value(user) + ":" + _escape_curl_value(password) + '"\n'
    ).encode()


def _basic(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _run_curl_user_config(user: str, password: str) -> subprocess.CompletedProcess[bytes]:
    program = r"""
set -euo pipefail
source "$1"
IFS= read -r -d '' cred_user || true
IFS= read -r -d '' cred_password || true
curl_user_config "$cred_user" "$cred_password"
"""
    payload = user.encode() + b"\0" + password.encode() + b"\0"
    return subprocess.run(
        ["bash", "-c", program, "bash", str(_CI_NEXUS)],
        input=payload,
        capture_output=True,
        check=False,
    )


def _authorization_for_config(config: bytes) -> str | None:
    with _auth_stub(b"ok") as server:
        port = server.server_address[1]
        proc = subprocess.run(
            [
                "curl",
                "--noproxy",
                "*",
                "--config",
                "-",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                f"http://127.0.0.1:{port}/probe",
            ],
            input=config,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == b"200"
        return server.authorization


def test_curl_user_config_round_trips_special_characters() -> None:
    user = _SPECIAL_USER
    password = _SPECIAL_PASSWORD
    assert '"' in password and "\\" in password and ":" in password and " " in password
    assert "\n" not in password and "\r" not in password
    script = _CI_NEXUS.read_text(encoding="utf-8")
    assert script.index(_CURL_USER_ESCAPE) < script.index(_CURL_PASS_ESCAPE)
    assert script.count("curl -K -") == 5
    assert script.count('auth_config="$(curl_user_config ') == 5
    assert "--user" not in script
    produced = _run_curl_user_config(user, password)
    assert produced.returncode == 0, produced.stderr
    assert produced.stdout == _expected_config(user, password)
    assert password.encode() not in produced.stderr
    assert _authorization_for_config(produced.stdout) == _basic(user, password)


def test_curl_user_config_rejects_a_newline_without_printing_it() -> None:
    token = "kiln-cred-newline-token"
    cases = (
        ("reader", f"{token}\nmore"),
        ("reader", f"{token}\rmore"),
        (f"{token}\nreader", "ordinary"),
    )
    for user, password in cases:
        result = _run_curl_user_config(user, password)
        assert result.returncode == 1
        assert token.encode() not in result.stdout
        assert token.encode() not in result.stderr
        assert b"newline" in result.stderr
        assert result.stdout == b""


def test_build_script_round_trips_special_characters(tmp_path: Path) -> None:
    user = _SPECIAL_USER
    password = _SPECIAL_PASSWORD
    script_text = _build_script("fixture.bin")
    assert password not in script_text
    assert script_text.index(_CURL_USER_ESCAPE) < script_text.index(_CURL_PASS_ESCAPE)
    path = tmp_path / "kiln-build.sh"
    path.write_text(script_text, encoding="utf-8")
    body = b"fixture-bytes"
    with _auth_stub(body) as server:
        port = server.server_address[1]
        proc = subprocess.run(
            ["sh", str(path)],
            cwd=tmp_path,
            env={
                "PATH": "/usr/bin:/bin",
                "KILN_NEXUS_URL": f"http://127.0.0.1:{port}",
                "KILN_NEXUS_REPOSITORY": "group",
                "KILN_NEXUS_USERNAME": user,
                "KILN_NEXUS_PASSWORD": password,
            },
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert password.encode() not in proc.stdout
        assert password.encode() not in proc.stderr
        assert (tmp_path / "kiln-dist" / "artifact.bin").read_bytes() == body
        assert server.authorization == _basic(user, password)


def test_build_script_rejects_a_newline_without_printing_it(tmp_path: Path) -> None:
    token = "kiln-cred-newline-token"
    path = tmp_path / "kiln-build.sh"
    path.write_text(_build_script("fixture.bin"), encoding="utf-8")
    proc = subprocess.run(
        ["sh", str(path)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "KILN_NEXUS_URL": "http://127.0.0.1:9",
            "KILN_NEXUS_REPOSITORY": "group",
            "KILN_NEXUS_USERNAME": "reader",
            "KILN_NEXUS_PASSWORD": f"{token}\nmore",
        },
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 1
    assert token.encode() not in proc.stdout
    assert token.encode() not in proc.stderr
    assert b"newline" in proc.stderr


def test_required_nexus_tests_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_docking_env(monkeypatch)
    monkeypatch.setenv("KILN_REQUIRE_NEXUS_TESTS", "1")
    monkeypatch.setenv("KILN_SKIP_EXTERNAL_TESTS", "1")
    monkeypatch.setenv("KILN_TEST_NEXUS_PASSWORD", _PASSWORD)
    with pytest.raises(pytest.fail.Exception) as caught:
        load_docking_targets()
    message = str(caught.value)
    assert "KILN_TEST_NEXUS_URL" in message
    assert "KILN_SKIP_EXTERNAL_TESTS" in message
    assert _PASSWORD not in message


def _configured(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "kiln_nexus_url": _URL,
        "kiln_nexus_repository": _REPO,
        "kiln_nexus_username": "reader",
        "kiln_nexus_password": _PASSWORD,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "kiln_nexus_url": _URL,
        "kiln_nexus_repository": _REPO,
        "kiln_nexus_username": "reader",
        "kiln_nexus_password": _PASSWORD,
    }
    values.update(overrides)
    return app_settings(**values)


def _nexus_client(
    handler,
    *,
    repository: str = _REPO,
    username: str = "reader",
    password: str = _PASSWORD,
) -> NexusClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    return NexusClient(_URL, repository, username, password, http)


def _probe(
    handler,
    *,
    repositories: list[object] | None = None,
    username: str = "reader",
    password: str = _PASSWORD,
) -> UpstreamFailure | None:
    def routed(request: httpx.Request) -> httpx.Response:
        if repositories is not None and request.url.path.endswith("/repositories"):
            return httpx.Response(200, json=repositories)
        return handler(request)

    client = _nexus_client(routed, username=username, password=password)
    try:
        client.probe()
    except UpstreamFailure as exc:
        return exc
    return None


def _status(handler) -> dict[str, object]:
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        app = create_app(_settings(), nexus_http_client=http)
        with TestClient(app, headers=auth_headers()) as client:
            response = client.get("/api/v1/dependency-proxy")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _idle_client() -> httpx.Client:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected nexus call {request.url}")

    return httpx.Client(transport=httpx.MockTransport(explode), trust_env=False)


def _clear_docking_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _REQUIRED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("KILN_REQUIRE_NEXUS_TESTS", raising=False)
    monkeypatch.delenv("KILN_SKIP_EXTERNAL_TESTS", raising=False)
    monkeypatch.delenv("KILN_TEST_GIT_PROVIDER", raising=False)


class _Git:
    provider = "gitea"
    role = "lab"

    def resolve_ref(self, owner: str, name: str, ref: str) -> ResolvedCommit:
        return ResolvedCommit(
            provider="gitea",
            role="lab",
            owner=owner,
            name=name,
            ref=ref,
            sha="a" * 40,
        )

    def authenticated_username(self) -> str:
        return "kiln"


class _Jenkins:
    def __init__(self, recorded: dict[str, object]) -> None:
        self._recorded = recorded

    def create_product_job(self, _job_name: str, **kwargs: object) -> None:
        self._recorded["declare"] = kwargs["declare_nexus_parameters"]

    def trigger_build(
        self,
        _job_name: str,
        *,
        parameters: dict[str, str] | None = None,
    ) -> str:
        self._recorded["parameters"] = parameters
        return "http://127.0.0.1:9/queue/item/1/"

    def delete_job(self, _job_name: str) -> None:
        return None
