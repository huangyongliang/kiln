"""Build-artifact integrity and actor attribution.

HTTP registration and upload cannot stamp source=build. An artifact referenced
by a product build rejects manual writes. Session uploads are capped at 1 MiB.
Actor columns record the signed-in account and stay null for a machine token
or for bytes stored by collect_build.
"""

import hashlib
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.auth import Principal
from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.domain.artifacts import (
    BUILD_OWNED_WRITE_DETAIL,
    SESSION_UPLOAD_MAX_BYTES,
    ArtifactSource,
    Checksum,
)
from kiln.domain.builds import ProductBuild
from kiln.domain.rbac import permissions_for
from kiln.llm.tools import ToolContext, _build_artifact
from tests.support import auth_headers, content_headers, publish_skill, truncate_domain
from tests.test_product_build import _body, _ready_executor

LOCAL_PASSWORD = "correct-horse-1"
ROOT = Path(__file__).resolve().parents[2]


def test_http_put_records_upload_even_when_the_header_says_build(client: TestClient) -> None:
    builder, account_id = _login(client, "ada_upload", ["builder"])
    artifact_id = _artifact(builder, "header-lib")
    payload = b"header-bytes"
    stored = builder.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
        content=payload,
        headers=content_headers(payload, source="build"),
    )
    assert stored.status_code == 201, stored.text
    body = stored.json()
    assert body["source"] == "upload"
    assert body["created_by"] == account_id
    assert _sources(client, artifact_id) == ["upload"]


def test_http_register_rejects_build_and_defaults_to_upload(client: TestClient) -> None:
    builder, account_id = _login(client, "ada_reg", ["builder"])
    artifact_id = _artifact(builder, "register-lib")
    payload = b"registered-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    rejected = builder.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        json={
            "version": "1.0.0",
            "checksum": {"algorithm": "sha256", "value": digest},
            "source": "build",
        },
    )
    assert rejected.status_code == 422
    assert _version_count(client, artifact_id) == 0

    omitted = builder.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        json={"version": "1.0.0", "checksum": {"algorithm": "sha256", "value": digest}},
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["source"] == "upload"
    assert omitted.json()["created_by"] == account_id

    stored = builder.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
        content=payload,
        headers=content_headers(payload, source="not-a-source"),
    )
    assert stored.status_code == 201, stored.text
    assert stored.json()["source"] == "upload"
    assert _sources(client, artifact_id) == ["upload"]


def test_build_owned_artifact_rejects_manual_writes_then_collects(tmp_path: Path) -> None:
    app, _git, jenkins = _ready_executor(tmp_path)
    payload = b"j" * (SESSION_UPLOAD_MAX_BYTES + 1)
    jenkins.payload = payload
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "owned-flow", ["ada/demo"])
        builder, account_id = _login(client, "ada_owner", ["builder"])
        created = builder.post("/api/v1/builds", json=_body(skill_id=skill["id"]))
        assert created.status_code == 201, created.text
        build = created.json()
        assert build["requested_by"] == account_id
        assert build["deploys_to_runtime"] is False
        assert build["content_conflict"] is None
        artifact_id = build["artifact_id"]
        assert _version_count(client, artifact_id) == 0

        digest = hashlib.sha256(b"manual").hexdigest()
        for session in (builder, client):
            registered = session.post(
                f"/api/v1/artifacts/{artifact_id}/versions",
                json={
                    "version": "1.0.0",
                    "checksum": {"algorithm": "sha256", "value": digest},
                },
            )
            assert registered.status_code == 409, registered.text
            assert registered.json()["detail"] == BUILD_OWNED_WRITE_DETAIL
            uploaded = session.put(
                f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
                content=b"manual",
                headers=content_headers(b"manual", source="build"),
            )
            assert uploaded.status_code == 409, uploaded.text
            assert uploaded.json()["detail"] == BUILD_OWNED_WRITE_DETAIL
        assert _version_count(client, artifact_id) == 0

        finished = builder.get(f"/api/v1/builds/{build['id']}")
        assert finished.status_code == 200, finished.text
        stored = finished.json()
        assert stored["status"] == "success"
        assert stored["content_available"] is True
        assert stored["content_conflict"] is None
        assert stored["requested_by"] == account_id
        assert stored["deploys_to_runtime"] is False
        version = client.get(f"/api/v1/artifacts/{artifact_id}").json()["versions"][0]
        assert version["source"] == "build"
        assert version["created_by"] is None
        assert version["content_available"] is True
        assert client.get(f"/api/v1/artifacts/{artifact_id}").json()["build_owned"] is True

        reserved = builder.post(
            f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/download-reservations",
            json={},
        )
        assert reserved.status_code == 201, reserved.text
        downloaded = builder.get(f"{reserved.json()['retrieve_path']}/content")
        assert downloaded.status_code == 200
        assert downloaded.content == payload

        again = client.put(
            f"/api/v1/artifacts/{artifact_id}/versions/9.9.9/content",
            content=b"later",
            headers=content_headers(b"later"),
        )
        assert again.status_code == 409
        assert _version_count(client, artifact_id) == 1

        plain = _artifact(client, "hand-lib")
        hand = client.post(
            f"/api/v1/artifacts/{plain}/versions",
            json={"version": "1", "checksum": {"algorithm": "sha256", "value": digest}},
        )
        assert hand.status_code == 201, hand.text
        assert hand.json()["source"] == "upload"
        assert hand.json()["created_by"] is None

        machine_build = client.post(
            "/api/v1/builds",
            json=_body(skill_id=skill["id"], artifact_name="machine-pkg"),
        )
        assert machine_build.status_code == 201, machine_build.text
        assert machine_build.json()["requested_by"] is None


def test_foreign_bytes_are_not_collected_as_build_output(tmp_path: Path) -> None:
    app, _git, jenkins = _ready_executor(tmp_path)
    payload = b"historical-upload"

    def forbid_download(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("collect_build downloaded over non-build bytes")

    jenkins.download_artifact = forbid_download  # type: ignore[method-assign]
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "foreign-flow", ["ada/demo"])
        created = client.post("/api/v1/builds", json=_body(skill_id=skill["id"]))
        assert created.status_code == 201, created.text
        build = created.json()
        digest = hashlib.sha256(payload).hexdigest()
        app.state.store.store_version_bytes(
            UUID(build["artifact_id"]),
            build["version"],
            payload,
            Checksum(algorithm="sha256", value=digest),
            ArtifactSource.upload,
        )
        detail = client.get(f"/api/v1/builds/{build['id']}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["status"] == "success"
        assert body["content_available"] is False
        assert body["content_conflict"] == "foreign_bytes"
        version = client.get(f"/api/v1/artifacts/{build['artifact_id']}").json()["versions"][0]
        assert version["source"] == "upload"
        assert version["content_available"] is True
        with app.state.engine.connect() as connection:
            stored = connection.execute(
                text("SELECT content_stored FROM product_builds WHERE id = :id"),
                {"id": build["id"]},
            ).scalar_one()
        assert stored is False


def test_build_artifact_tool_records_the_session_account(tmp_path: Path) -> None:
    app, _git, _jenkins = _ready_executor(tmp_path)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "tool-flow", ["ada/demo"])
        _builder, account_id = _login(client, "ada_tool", ["builder"])
        account = app.state.store.get_account(UUID(account_id))
        principal = Principal(
            kind="user",
            permissions=permissions_for(account.role_ids),
            account=account,
        )
        context = ToolContext(
            catalog=app.state.store,
            builds=app.state.builds,
            jenkins=app.state.jenkins,
            git_source=app.state.git_source,
            settings=app.state.settings,
        )
        result = _build_artifact(
            _body(skill_id=skill["id"], artifact_name="chat-pkg"),
            context,
            principal,
        )
        body = json.loads(result.content)
        assert body["requested_by"] == account_id
        assert body["deploys_to_runtime"] is False
        assert _requested_by(app.state.builds, body["id"]) == account_id

        machine = _build_artifact(
            _body(skill_id=skill["id"], artifact_name="machine-chat"),
            context,
            Principal(kind="machine", permissions=frozenset(), account=None),
        )
        machine_body = json.loads(machine.content)
        assert machine_body["requested_by"] is None
        assert _requested_by(app.state.builds, machine_body["id"]) is None


def test_session_job_records_requested_by(client: TestClient) -> None:
    client.app.state.jenkins = _JobJenkins()
    builder, account_id = _login(client, "ada_job", ["builder"])
    created = builder.post("/api/v1/jobs", json={"name": "compat", "marker": "ada-job"})
    assert created.status_code == 201, created.text
    assert created.json()["requested_by"] == account_id
    assert created.json()["deploys_to_runtime"] is False
    with client.app.state.engine.connect() as connection:
        stored = connection.execute(
            text("SELECT requested_by::text FROM executor_jobs WHERE id = :id"),
            {"id": created.json()["id"]},
        ).scalar_one()
    assert stored == account_id
    machine = client.post("/api/v1/jobs", json={"marker": "machine-job"})
    assert machine.status_code == 201, machine.text
    assert machine.json()["requested_by"] is None


def test_session_upload_records_created_by_and_collect_does_not(client: TestClient) -> None:
    builder, account_id = _login(client, "ada_bytes", ["builder"])
    artifact_id = _artifact(builder, "actor-lib")
    payload = b"session-bytes"
    stored = builder.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1/content",
        content=payload,
        headers=content_headers(payload),
    )
    assert stored.status_code == 201, stored.text
    assert stored.json()["created_by"] == account_id
    assert stored.json()["source"] == "upload"


def test_reservation_requester_comes_from_the_session(client: TestClient) -> None:
    builder, account_id = _login(client, "ada_fetch", ["builder"])
    artifact_id = _artifact(builder, "reserve-lib")
    assert (
        builder.post(
            f"/api/v1/artifacts/{artifact_id}/versions",
            json={
                "version": "1",
                "checksum": {"algorithm": "sha256", "value": "ab" * 32},
            },
        ).status_code
        == 201
    )
    omitted = builder.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"ttl_seconds": 900},
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["requester"] == "ada_fetch"
    assert omitted.json()["account_id"] == account_id

    mismatch = builder.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"requester": "other_user"},
    )
    assert mismatch.status_code == 422

    missing = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"ttl_seconds": 900},
    )
    assert missing.status_code == 422

    machine = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"requester": "ops_user"},
    )
    assert machine.status_code == 201, machine.text
    assert machine.json()["requester"] == "ops_user"
    assert machine.json()["account_id"] is None


def test_session_upload_cap_is_one_mebibyte_and_machine_token_is_not(client: TestClient) -> None:
    assert SESSION_UPLOAD_MAX_BYTES == 1_048_576
    assert Settings.model_fields["kiln_artifact_max_bytes"].default == 64 * 1024 * 1024
    builder, _account_id = _login(client, "ada_cap", ["builder"])
    artifact_id = _artifact(builder, "cap-lib")
    over = b"x" * (SESSION_UPLOAD_MAX_BYTES + 1)
    denied = builder.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1/content",
        content=over,
        headers=content_headers(over, source="build"),
    )
    assert denied.status_code == 413
    assert "1048576" in denied.json()["detail"]
    assert _version_count(client, artifact_id) == 0

    exact = b"y" * SESSION_UPLOAD_MAX_BYTES
    allowed = builder.put(
        f"/api/v1/artifacts/{artifact_id}/versions/2/content",
        content=exact,
        headers=content_headers(exact),
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["size_bytes"] == SESSION_UPLOAD_MAX_BYTES

    machine = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/3/content",
        content=over,
        headers=content_headers(over, source="upload"),
    )
    assert machine.status_code == 201, machine.text
    assert machine.json()["size_bytes"] == SESSION_UPLOAD_MAX_BYTES + 1
    assert machine.json()["created_by"] is None


def test_retrieve_metadata_requires_artifact_download(client: TestClient) -> None:
    artifact_id = _artifact(client, "meta-lib")
    payload = b"meta-bytes"
    assert (
        client.put(
            f"/api/v1/artifacts/{artifact_id}/versions/1/content",
            content=payload,
            headers=content_headers(payload),
        ).status_code
        == 201
    )
    reserved = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"requester": "ops_user"},
    )
    assert reserved.status_code == 201, reserved.text
    path = reserved.json()["retrieve_path"]
    reader, _reader_id = _login(client, "ada_meta", ["reader"])
    denied = reader.get(path)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "没有权限"
    builder, _builder_id = _login(client, "ada_meta_b", ["builder"])
    allowed = builder.get(path)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["content_available"] is True

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "取包元数据仍只需会话或机器令牌" not in readme
    assert "取包元数据 `GET /retrieve/{id}` 与取包字节都需要 `artifact.download`" in readme


def test_docs_and_runtime_flag_stay_within_the_slice() -> None:
    assert ProductBuild.model_fields["deploys_to_runtime"].default is False
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for document in (readme, deploy):
        assert "只有显式空字符串会拒绝启动" in document
        assert "未设置时回落 `kiln-local`" in document
        assert "1048576" in document
        assert BUILD_OWNED_WRITE_DETAIL in document
        assert "不回填" in document
    assert "留空时 API 拒绝启动" not in example
    checked = [
        ROOT / "README.md",
        ROOT / "docs" / "DEPLOY.md",
        ROOT / ".env.example",
        ROOT / "backend" / "kiln" / "api" / "artifacts.py",
        ROOT / "backend" / "kiln" / "api" / "retrieve.py",
        ROOT / "backend" / "kiln" / "main.py",
        ROOT / "backend" / "kiln" / "domain" / "artifacts.py",
        ROOT / "backend" / "kiln" / "executor" / "product.py",
        ROOT / "backend" / "kiln" / "llm" / "tools.py",
        ROOT / "backend" / "kiln" / "db" / "migrations" / "versions" / "0010_actor_attribution.py",
        ROOT / "console" / "src" / "actors.js",
        ROOT / "console" / "src" / "views" / "ArtifactsView.vue",
        ROOT / "console" / "src" / "views" / "ArtifactDetailView.vue",
        ROOT / "console" / "src" / "views" / "BuildDetailView.vue",
    ]
    for path in checked:
        document = path.read_text(encoding="utf-8")
        folded = document.casefold()
        assert "联邦" not in document
        assert "身份联邦" not in document
        assert "federation" not in folded


def _login(client: TestClient, username: str, role_ids: list[str]) -> tuple[TestClient, str]:
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
    return session, created.json()["id"]


def _artifact(client: TestClient, name: str) -> str:
    created = client.post(
        "/api/v1/artifacts",
        json={"name": name, "package_type": "generic"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _version_count(client: TestClient, artifact_id: str) -> int:
    with client.app.state.engine.connect() as connection:
        return connection.execute(
            text("SELECT count(*) FROM artifact_versions WHERE artifact_id = :id"),
            {"id": artifact_id},
        ).scalar_one()


def _sources(client: TestClient, artifact_id: str) -> list[str]:
    with client.app.state.engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT source FROM artifact_versions
                WHERE artifact_id = :id
                ORDER BY version
                """
            ),
            {"id": artifact_id},
        ).scalars()
        return list(rows)


def _requested_by(builds: BuildStore, build_id: str) -> str | None:
    stored = builds.get_build(UUID(build_id))
    if stored.requested_by is None:
        return None
    return str(stored.requested_by)


class _JobJenkins:
    def create_job(self, job_name: str, marker: str) -> None:
        return None

    def trigger_build(self, job_name: str, **_kwargs: object) -> str:
        return "http://127.0.0.1:9/queue/item/1/"

    def delete_job(self, job_name: str) -> None:
        return None
