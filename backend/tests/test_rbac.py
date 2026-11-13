"""Permission gates for catalog reads and for job, build, and chat writes.

These cases do not need Jenkins, Gitea, or an OIDC issuer. Unconfigured
Jenkins and git stay 503 only after the permission check has passed.
"""

from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.db.seed import seed_rbac
from kiln.domain.git import ResolvedCommit
from kiln.domain.jobs import JobStatus
from kiln.main import create_app
from tests.support import app_settings, auth_headers, content_headers, truncate_domain

LOCAL_PASSWORD = "correct-horse-1"

_JOB = {"marker": "gated"}
_BUILD = {
    "repository": "ada/demo",
    "ref": "main",
    "artifact_name": "gated-pkg",
    "version": "1",
    "package_type": "generic",
}
_CHAT = {"messages": [{"role": "user", "content": "你好"}]}
_MISSING = "00000000-0000-0000-0000-000000000001"
_EXECUTOR_READ = "查看兼容执行器状态、任务与产品构建及其日志"
_GIT_READ = "查看代码源适配器、仓库列表并解析引用"

_GATED_GETS = (
    "/api/v1/accounts",
    f"/api/v1/accounts/{_MISSING}",
    "/api/v1/artifacts",
    f"/api/v1/artifacts/{_MISSING}",
    "/api/v1/skills",
    f"/api/v1/skills/{_MISSING}",
    "/api/v1/executor",
    "/api/v1/dependency-proxy",
    "/api/v1/jobs",
    f"/api/v1/jobs/{_MISSING}",
    f"/api/v1/jobs/{_MISSING}/log",
    "/api/v1/builds",
    f"/api/v1/builds/{_MISSING}",
    f"/api/v1/builds/{_MISSING}/log",
    "/api/v1/git/source",
    "/api/v1/git/repositories",
    "/api/v1/git/commits?repository=ada/demo&ref=main",
)
_EXECUTOR_GETS = (
    "/api/v1/executor",
    "/api/v1/dependency-proxy",
    "/api/v1/jobs",
    f"/api/v1/jobs/{_MISSING}",
    f"/api/v1/jobs/{_MISSING}/log",
    "/api/v1/builds",
    f"/api/v1/builds/{_MISSING}",
    f"/api/v1/builds/{_MISSING}/log",
)
_GIT_GETS = (
    "/api/v1/git/source",
    "/api/v1/git/repositories",
    "/api/v1/git/commits?repository=ada/demo&ref=main",
)
_UNCONFIGURED_GETS = (
    "/api/v1/jobs",
    f"/api/v1/jobs/{_MISSING}",
    f"/api/v1/jobs/{_MISSING}/log",
    "/api/v1/builds",
    f"/api/v1/builds/{_MISSING}",
    f"/api/v1/builds/{_MISSING}/log",
    "/api/v1/git/repositories",
    "/api/v1/git/commits?repository=ada/demo&ref=main",
)


def test_anonymous_writes_are_unauthorized_and_insert_nothing(client: TestClient) -> None:
    anon = TestClient(client.app)
    assert anon.post("/api/v1/jobs", json=_JOB).status_code == 401
    assert anon.post("/api/v1/builds", json=_BUILD).status_code == 401
    assert anon.post("/api/v1/llm/chat", json=_CHAT).status_code == 401
    assert _row_counts(client.app.state.engine) == (0, 0)


def test_reader_is_forbidden_before_executor_configuration(client: TestClient) -> None:
    reader = _login(client, "ada_reader", ["reader"])
    for path, payload in (
        ("/api/v1/jobs", _JOB),
        ("/api/v1/builds", _BUILD),
        ("/api/v1/llm/chat", _CHAT),
    ):
        denied = reader.post(path, json=payload)
        assert denied.status_code == 403, path
        assert denied.json()["detail"] == "没有权限"
        assert "JENKINS" not in denied.text
        assert "模型未启用" not in denied.text
    assert _row_counts(client.app.state.engine) == (0, 0)
    assert reader.get("/api/v1/llm/provider").status_code == 200
    for path in _EXECUTOR_GETS + _GIT_GETS:
        denied = reader.get(path)
        assert denied.status_code == 403, path
        assert denied.json()["detail"] == "没有权限"
        assert "JENKINS_URL" not in denied.text
        assert "代码源未配置" not in denied.text


def test_builder_passes_the_gate_and_mock_chat_succeeds(tmp_path: Path) -> None:
    app = create_app(
        app_settings(
            llm_provider="mock",
            kiln_artifact_root=str(tmp_path / "artifacts"),
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        sessions = {
            role_id: _login(client, f"ada_{role_id}", [role_id])
            for role_id in ("builder", "publisher", "admin")
        }
        for role_id, session in sessions.items():
            job = session.post("/api/v1/jobs", json=_JOB)
            assert job.status_code == 503, role_id
            assert "JENKINS_URL" in job.json()["detail"]
            build = session.post("/api/v1/builds", json=_BUILD)
            assert build.status_code == 503, role_id
            assert "没有权限" not in build.text
        chat = sessions["builder"].post("/api/v1/llm/chat", json=_CHAT)
        assert chat.status_code == 200
        assert chat.json()["provider"] == "mock"
        assert "你好" in chat.json()["content"]
        machine_chat = client.post("/api/v1/llm/chat", json=_CHAT)
        assert machine_chat.status_code == 200
        machine_job = client.post("/api/v1/jobs", json=_JOB)
        machine_build = client.post("/api/v1/builds", json=_BUILD)
        assert machine_job.status_code == 503
        assert machine_build.status_code == 503
        assert _row_counts(app.state.engine) == (0, 0)


def test_machine_token_is_not_forbidden_when_unconfigured(client: TestClient) -> None:
    job = client.post("/api/v1/jobs", json=_JOB)
    build = client.post("/api/v1/builds", json=_BUILD)
    chat = client.post("/api/v1/llm/chat", json=_CHAT)
    assert job.status_code == 503
    assert "JENKINS_URL" in job.json()["detail"]
    assert build.status_code == 503
    assert chat.status_code == 503
    assert "没有权限" not in job.text
    assert "没有权限" not in build.text
    assert "没有权限" not in chat.text
    assert _row_counts(client.app.state.engine) == (0, 0)


def test_permission_catalog_includes_read_codes(client: TestClient) -> None:
    permissions = client.get("/api/v1/rbac/permissions")
    assert permissions.status_code == 200
    described = {item["code"]: item["description"] for item in permissions.json()["items"]}
    assert described["executor.run"] == "在兼容执行器上发起任务或产品构建"
    assert described["executor.read"] == _EXECUTOR_READ
    assert described["git.read"] == _GIT_READ
    assert described["skill.submit"] == "提交 skill 审核"
    assert described["skill.publish"] == "审核并发布 skill"
    assert "skill.review" not in described
    listed = {role["id"]: role for role in client.get("/api/v1/rbac/roles").json()["items"]}
    assert listed["reader"]["description"] == "查看账号、skill 与制品"
    assert "起草并提交审核" in listed["builder"]["description"]
    roles = {role_id: set(role["permissions"]) for role_id, role in listed.items()}
    assert "executor.run" not in roles["reader"]
    assert "agent.qa" not in roles["reader"]
    assert "executor.read" not in roles["reader"]
    assert "git.read" not in roles["reader"]
    assert "skill.submit" not in roles["reader"]
    assert "skill.publish" not in roles["reader"]
    assert "skill.publish" not in roles["builder"]
    assert "agent.draft_skill" in roles["builder"]
    assert "agent.draft_skill" in roles["publisher"]
    assert "agent.draft_skill" in roles["admin"]
    assert "agent.draft_skill" not in roles["reader"]
    assert "skill.publish" in roles["publisher"]
    assert "skill.publish" in roles["admin"]
    for role_id in ("builder", "publisher", "admin"):
        assert "executor.run" in roles[role_id]
        assert "agent.qa" in roles[role_id]
        assert "executor.read" in roles[role_id]
        assert "git.read" in roles[role_id]
        assert "skill.submit" in roles[role_id]


def test_seed_rbac_upserts_new_codes_onto_an_existing_catalog(client: TestClient) -> None:
    engine = client.app.state.engine
    with engine.begin() as connection:
        for code in ("executor.run", "executor.read", "git.read", "skill.submit"):
            removed = connection.execute(
                text("DELETE FROM rbac_role_permissions WHERE permission_code = :code"),
                {"code": code},
            )
            assert removed.rowcount >= 1
            deleted = connection.execute(
                text("DELETE FROM rbac_permissions WHERE code = :code"),
                {"code": code},
            )
            assert deleted.rowcount == 1
        updated = connection.execute(
            text("UPDATE rbac_permissions SET description = '旧说明' WHERE code = 'agent.qa'")
        )
        assert updated.rowcount == 1
        remaining = connection.execute(text("SELECT count(*) FROM rbac_permissions")).scalar_one()
    assert remaining >= 1
    before = client.get("/api/v1/rbac/permissions").json()["items"]
    removed_codes = {"executor.run", "executor.read", "git.read", "skill.submit"}
    assert all(item["code"] not in removed_codes for item in before)
    seed_rbac(engine)
    permissions = client.get("/api/v1/rbac/permissions").json()["items"]
    described = {item["code"]: item["description"] for item in permissions}
    assert described["executor.run"] == "在兼容执行器上发起任务或产品构建"
    assert described["executor.read"] == _EXECUTOR_READ
    assert described["git.read"] == _GIT_READ
    assert described["skill.submit"] == "提交 skill 审核"
    assert described["agent.qa"] == "使用平台内问答"
    assert len(permissions) == len({item["code"] for item in permissions})
    roles = {
        role["id"]: role["permissions"]
        for role in client.get("/api/v1/rbac/roles").json()["items"]
    }
    for code in ("executor.run", "executor.read", "git.read", "skill.submit"):
        assert code not in roles["reader"]
        assert roles["builder"].count(code) == 1
        assert roles["publisher"].count(code) == 1
        assert roles["admin"].count(code) == 1


def test_anonymous_gets_are_unauthorized_and_insert_nothing(client: TestClient) -> None:
    before = _domain_counts(client.app.state.engine)
    anon = TestClient(client.app)
    for path in _GATED_GETS:
        denied = anon.get(path)
        assert denied.status_code == 401, path
        assert denied.json()["detail"] == "需要访问令牌"
    assert _domain_counts(client.app.state.engine) == before
    assert _row_counts(client.app.state.engine) == (0, 0)


def test_reader_is_forbidden_on_executor_and_git_gets(client: TestClient) -> None:
    reader = _login(client, "ada_read_gate", ["reader"])
    for path in _EXECUTOR_GETS + _GIT_GETS:
        denied = reader.get(path)
        assert denied.status_code == 403, path
        assert denied.json()["detail"] == "没有权限"
        assert "JENKINS_URL" not in denied.text
        assert "代码源未配置" not in denied.text
    assert _row_counts(client.app.state.engine) == (0, 0)


def test_privileged_reads_are_503_until_configured_then_200(
    client: TestClient,
    tmp_path: Path,
) -> None:
    sessions = {
        role_id: _login(client, f"ada_read_{role_id}", [role_id])
        for role_id in ("builder", "publisher", "admin")
    }
    sessions["machine"] = client
    for role_id, session in sessions.items():
        for path in _UNCONFIGURED_GETS:
            denied = session.get(path)
            assert denied.status_code == 503, (role_id, path)
            assert "没有权限" not in denied.text
            if path.startswith("/api/v1/git/"):
                assert "代码源未配置" in denied.json()["detail"]
            else:
                assert "JENKINS_URL" in denied.json()["detail"]
        executor = session.get("/api/v1/executor")
        assert executor.status_code == 200, role_id
        assert executor.json()["configured"] is False
        assert executor.json()["deploys_to_runtime"] is False
        proxy = session.get("/api/v1/dependency-proxy")
        assert proxy.status_code == 200, role_id
        assert proxy.json()["configured"] is False
        assert proxy.json()["kind"] == "off"
        assert proxy.json()["reachable"] is None
        assert proxy.json()["publishes_artifacts"] is False
        source = session.get("/api/v1/git/source")
        assert source.status_code == 200, role_id
        assert source.json()["configured"] is False
        assert source.json()["production_provider"] == "gitlab"
    assert _row_counts(client.app.state.engine) == (0, 0)

    app = create_app(
        app_settings(
            jenkins_url="http://127.0.0.1:9",
            jenkins_user="kiln",
            jenkins_api_token="test-token",
            git_provider="gitea",
            git_base_url="http://127.0.0.1:9",
            git_token="test-token",
            kiln_artifact_root=str(tmp_path / "artifacts"),
        )
    )
    with TestClient(app, headers=auth_headers()) as machine:
        truncate_domain(app.state.engine)
        app.state.jenkins = _QuietJenkins()
        app.state.git_source = _QuietGit()
        reader = _login(machine, "ada_configured_reader", ["reader"])
        for path in _EXECUTOR_GETS + _GIT_GETS:
            denied = reader.get(path)
            assert denied.status_code == 403, path
            assert denied.json()["detail"] == "没有权限"
        configured = {
            role_id: _login(machine, f"ada_cfg_{role_id}", [role_id])
            for role_id in ("builder", "publisher", "admin")
        }
        configured["machine"] = machine
        artifact = machine.post(
            "/api/v1/artifacts",
            json={"name": "gated-bytes", "package_type": "generic"},
        )
        assert artifact.status_code == 201, artifact.text
        job_id, build_id = _store_finished_rows(app, UUID(artifact.json()["id"]))
        reads = (
            "/api/v1/executor",
            "/api/v1/dependency-proxy",
            "/api/v1/jobs",
            f"/api/v1/jobs/{job_id}",
            f"/api/v1/jobs/{job_id}/log",
            "/api/v1/builds",
            f"/api/v1/builds/{build_id}",
            f"/api/v1/builds/{build_id}/log",
            "/api/v1/git/source",
            "/api/v1/git/repositories",
            "/api/v1/git/commits?repository=ada/demo&ref=main",
        )
        for role_id, session in configured.items():
            for path in reads:
                allowed = session.get(path)
                assert allowed.status_code == 200, (role_id, path, allowed.text)
            assert session.get("/api/v1/executor").json()["configured"] is True
            source = session.get("/api/v1/git/source").json()
            assert source["configured"] is True
            assert source["provider"] == "gitea"
            assert source["production_provider"] == "gitlab"
            assert session.get(f"/api/v1/jobs/{job_id}/log").json()["text"] == "local-log"
            commit = session.get("/api/v1/git/commits?repository=ada/demo&ref=main").json()
            assert commit["sha"] == "a" * 40
            assert commit["provider"] == "gitea"


def test_reader_keeps_catalog_and_retrieve_metadata_reads(client: TestClient) -> None:
    payload = b"reader-bytes"
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "read-gate", "package_type": "generic"},
    )
    assert created.status_code == 201, created.text
    artifact_id = created.json()["id"]
    stored = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1/content",
        content=payload,
        headers=content_headers(payload),
    )
    assert stored.status_code == 201, stored.text
    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"requester": "ada_reader"},
    )
    assert reservation.status_code == 201, reservation.text
    meta_path = reservation.json()["retrieve_path"]
    skill = client.post("/api/v1/skills", json={"name": "只读技能", "version": "1.0.0"})
    assert skill.status_code == 201, skill.text
    reader = _login(client, "ada_catalog", ["reader"])
    account_id = _account_id(client, "ada_catalog")
    allowed = (
        "/api/v1/accounts",
        f"/api/v1/accounts/{account_id}",
        "/api/v1/artifacts",
        f"/api/v1/artifacts/{artifact_id}",
        "/api/v1/skills",
        f"/api/v1/skills/{skill.json()['id']}",
        "/api/v1/rbac/roles",
        "/api/v1/rbac/permissions",
        "/api/v1/llm/provider",
        meta_path,
    )
    for path in allowed:
        response = reader.get(path)
        assert response.status_code == 200, path
    meta = reader.get(meta_path)
    assert meta.json()["content_available"] is True
    content = reader.get(f"{meta_path}/content")
    assert content.status_code == 403
    assert content.json()["detail"] == "没有权限"

    builder = _login(client, "ada_download", ["builder"])
    downloaded = builder.get(f"{meta_path}/content")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == payload
    machine = client.get(f"{meta_path}/content")
    assert machine.status_code == 200
    assert machine.content == payload


def test_account_without_roles_cannot_read_catalog_lists(client: TestClient) -> None:
    bare = _login(client, "ada_none", [])
    for path in ("/api/v1/accounts", "/api/v1/artifacts", "/api/v1/skills"):
        denied = bare.get(path)
        assert denied.status_code == 403, path
        assert denied.json()["detail"] == "没有权限"
    assert client.get("/api/v1/accounts").status_code == 200
    assert client.get("/api/v1/artifacts").status_code == 200
    assert client.get("/api/v1/skills").status_code == 200


def _login(client: TestClient, username: str, role_ids: list[str]) -> TestClient:
    created = client.post(
        "/api/v1/accounts",
        json={
            "username": username,
            "display_name": username,
            "password": LOCAL_PASSWORD,
            "role_ids": role_ids,
        },
    )
    assert created.status_code == 201, created.text
    session = TestClient(client.app)
    logged_in = session.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["account"]["role_ids"] == role_ids
    return session


def _row_counts(engine) -> tuple[int, int]:
    with engine.connect() as connection:
        jobs = connection.execute(text("SELECT count(*) FROM executor_jobs")).scalar_one()
        builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
    return jobs, builds


def _domain_counts(engine) -> dict[str, int]:
    names = (
        "accounts",
        "artifacts",
        "skills",
        "executor_jobs",
        "product_builds",
        "sessions",
        "download_reservations",
    )
    with engine.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()
            for name in names
        }


def _account_id(client: TestClient, username: str) -> str:
    listed = client.get("/api/v1/accounts")
    assert listed.status_code == 200, listed.text
    return next(item["id"] for item in listed.json()["items"] if item["username"] == username)


def _store_finished_rows(app, artifact_id: UUID) -> tuple[UUID, UUID]:
    job_id = uuid4()
    app.state.jobs.create_job(
        job_id=job_id,
        name="compat",
        jenkins_job_name=f"kiln-{job_id.hex}",
        marker="gated",
        queue_url=None,
    )
    app.state.jobs.save_run(job_id, status=JobStatus.success, build_number=1)
    build_id = uuid4()
    app.state.builds.create_build(
        build_id=build_id,
        name="gated-bytes",
        jenkins_job_name=f"kiln-build-{build_id.hex}",
        repository="ada/demo",
        ref="main",
        commit_sha="b" * 40,
        artifact_id=artifact_id,
        version="1",
        package_type="generic",
        queue_url=None,
    )
    app.state.builds.save_run(build_id, status=JobStatus.failure, build_number=1)
    return job_id, build_id


class _QuietJenkins:
    def read_log(self, job_name: str, build_number: int) -> tuple[str, bool]:
        return "local-log", False

    def read_progress(self, *_args: object, **_kwargs: object) -> tuple[str, int | None]:
        raise AssertionError("a finished row does not ask Jenkins for progress")

    def download_artifact(self, *_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("a failed build does not download artifact bytes")


class _QuietGit:
    provider = "gitea"
    role = "lab"

    def list_repositories(self) -> list[object]:
        return []

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
