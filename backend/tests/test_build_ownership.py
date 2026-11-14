"""Build ownership is committed before Jenkins, and version writes share one lock order.

Acceptance checks for the pending build row, the register/upload race, foreign
version conflicts, retrieve source, historical build-source fills, and the
documentation that keeps the two X-Kiln-Source headers apart.
"""

import hashlib
import socket
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from kiln.domain.artifacts import (
    BUILD_OWNED_WRITE_DETAIL,
    BUILD_SOURCE_FILL_DETAIL,
    FOREIGN_BUILD_VERSION_DETAIL,
    ArtifactSource,
    Checksum,
)
from kiln.domain.builds import ProductBuild
from kiln.errors import Conflict, UpstreamFailure
from tests.support import auth_headers, content_headers, publish_skill, truncate_domain
from tests.test_artifact_integrity import _login
from tests.test_product_build import _body, _ready_executor

OWNERSHIP_RACE_ROUNDS = 200
REGISTER_RACE_ROUNDS = 200
ROOT = Path(__file__).resolve().parents[2]
LOCAL_PASSWORD = "correct-horse-1"
_QUEUE = "http://127.0.0.1:9/queue/item/1/"


def test_pending_build_rejects_manual_writes_before_jenkins_returns(tmp_path: Path) -> None:
    """Acceptance 1. Both Jenkins callbacks see a build-owned artifact and no queue URL."""

    assert "queue_url" not in ProductBuild.model_fields
    app, _git, jenkins = _ready_executor(tmp_path)
    jenkins.fail_read_progress = True
    payload = b"owned-window"
    digest = hashlib.sha256(payload).hexdigest()
    seen: list[tuple[str, str, str, int, str]] = []
    with _running(app) as base:
        truncate_domain(app.state.engine)
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as http:
            skill = _publish(http)
            builder = _session(http, "ada_window", ["builder"])
            machine = auth_headers()

            def probe(phase: str, job_name: str) -> None:
                build_id = str(UUID(job_name.removeprefix("kiln-build-")))
                detail = http.get(f"/api/v1/builds/{build_id}", headers=machine)
                assert detail.status_code == 200, detail.text
                body = detail.json()
                assert body["status"] == "queued"
                assert body["build_number"] is None
                assert "queue_url" not in body
                listed = http.get("/api/v1/builds", headers=machine)
                assert listed.status_code == 200, listed.text
                match = next(item for item in listed.json()["items"] if item["id"] == build_id)
                assert match["status"] == "queued"
                assert match["build_number"] is None
                assert "queue_url" not in match
                artifact = http.get(f"/api/v1/artifacts/{body['artifact_id']}", headers=machine)
                assert artifact.status_code == 200, artifact.text
                assert artifact.json()["build_owned"] is True
                with app.state.engine.connect() as connection:
                    queue_url = connection.execute(
                        text("SELECT queue_url, build_number FROM product_builds WHERE id = :id"),
                        {"id": build_id},
                    ).one()
                assert queue_url == (None, None)
                for label, headers in (("builder", builder), ("machine", machine)):
                    registered = _register(
                        http, headers, body["artifact_id"], body["version"], digest
                    )
                    uploaded = _upload(
                        http, headers, body["artifact_id"], body["version"], payload
                    )
                    seen.append((phase, label, "register", registered.status_code, registered.text))
                    seen.append((phase, label, "upload", uploaded.status_code, uploaded.text))
                assert _version_count(app.state.engine, body["artifact_id"]) == 0

            jenkins.on_create = lambda job_name: probe("create_product_job", job_name)
            jenkins.on_trigger = lambda job_name: probe("trigger_build", job_name)
            created = http.post("/api/v1/builds", headers=builder, json=_body(skill_id=skill["id"]))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["build_number"] is None
    assert "queue_url" not in body
    assert [item[0] for item in jenkins.calls] == ["create_product_job", "trigger_build"]
    phases = {item[0] for item in seen}
    assert phases == {"create_product_job", "trigger_build"}
    assert len(seen) == 8
    for phase, _label, _kind, status, raw in seen:
        assert status == 409, (phase, raw)
        assert BUILD_OWNED_WRITE_DETAIL in raw
    assert _version_count(app.state.engine, body["artifact_id"]) == 0
    with app.state.engine.connect() as connection:
        stored = connection.execute(
            text("SELECT queue_url, build_number, status FROM product_builds WHERE id = :id"),
            {"id": body["id"]},
        ).one()
    assert stored == (_QUEUE, None, "queued")


def test_build_window_race_never_accepts_a_manual_write(tmp_path: Path) -> None:
    """Acceptance 2. A polling writer overlaps POST /builds for at least 200 rounds."""

    assert OWNERSHIP_RACE_ROUNDS >= 200
    app, _git, jenkins = _ready_executor(tmp_path)
    payload = b"jenkins-race-bytes"
    jenkins.payload = payload
    state = {"release": threading.Event()}

    def on_create(_job_name: str) -> None:
        if not state["release"].wait(timeout=8):
            raise TimeoutError("writer missed the build window")

    jenkins.on_create = on_create
    with _running(app) as base:
        truncate_domain(app.state.engine)
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as http:
            skill = _publish(http)
            builder = _session(http, "ada_race", ["builder"])
            machine = auth_headers()
            for round_id in range(OWNERSHIP_RACE_ROUNDS):
                state["release"] = threading.Event()
                name = f"win{round_id:04d}"
                barrier = threading.Barrier(2)
                outcome: dict[str, object] = {}

                def write() -> None:
                    barrier.wait()
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        listed = http.get("/api/v1/artifacts", headers=builder)
                        assert listed.status_code < 500, listed.status_code
                        match = next(
                            (item for item in listed.json()["items"] if item["name"] == name),
                            None,
                        )
                        if match is None:
                            time.sleep(0.005)
                            continue
                        uploaded = _upload(http, builder, match["id"], "1.0.0", b"manual-race")
                        outcome["status"] = uploaded.status_code
                        outcome["detail"] = uploaded.text
                        state["release"].set()
                        return
                    state["release"].set()
                    raise AssertionError(f"round {round_id} never saw {name}")

                def build() -> None:
                    barrier.wait()
                    created = http.post(
                        "/api/v1/builds",
                        headers=machine,
                        json=_body(skill_id=skill["id"], artifact_name=name),
                    )
                    outcome["build"] = created

                writer = threading.Thread(target=_capture(outcome, "writer", write))
                builder_thread = threading.Thread(target=_capture(outcome, "builder", build))
                writer.start()
                builder_thread.start()
                writer.join(timeout=15)
                builder_thread.join(timeout=15)
                assert "writer" not in outcome, outcome.get("writer")
                assert "builder" not in outcome, outcome.get("builder")
                assert not writer.is_alive()
                assert not builder_thread.is_alive()
                created = outcome["build"]
                assert isinstance(created, httpx.Response)
                assert created.status_code == 201, (round_id, created.text)
                assert outcome["status"] == 409, (round_id, outcome["detail"])
                assert BUILD_OWNED_WRITE_DETAIL in str(outcome["detail"])
                finished = http.get(f"/api/v1/builds/{created.json()['id']}", headers=machine)
                assert finished.status_code == 200, finished.text
                stored = finished.json()
                assert stored["content_conflict"] is None
                assert stored["content_available"] is True
                assert stored["status"] == "success"
                reserved = http.post(
                    f"/api/v1/artifacts/{stored['artifact_id']}/versions/1.0.0/download-reservations",
                    headers=builder,
                    json={},
                )
                assert reserved.status_code == 201, reserved.text
                downloaded = http.get(
                    f"{reserved.json()['retrieve_path']}/content",
                    headers=builder,
                )
                assert downloaded.status_code == 200, downloaded.text
                assert downloaded.content == payload
                assert _version_count(app.state.engine, stored["artifact_id"]) == 1


@pytest.mark.parametrize("where", ["create_product_job", "trigger_build", "attach_queue"])
def test_jenkins_failure_keeps_a_not_built_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    """Acceptance 3. Each Jenkins step failure marks not_built and still deletes the job."""

    app, _git, jenkins = _ready_executor(tmp_path)
    detail = {
        "create_product_job": "Jenkins 建任务失败（127.0.0.1:9）",
        "trigger_build": "Jenkins 调用失败（127.0.0.1:9，HTTP 500）",
        "attach_queue": "回写队列失败",
    }[where]
    if where == "create_product_job":
        jenkins.create_error = UpstreamFailure(detail)
    elif where == "trigger_build":
        jenkins.trigger_error = UpstreamFailure(detail)
    else:

        def fail_attach(_build_id: UUID, _queue_url: str | None) -> None:
            raise UpstreamFailure(detail)

        monkeypatch.setattr(app.state.builds, "attach_queue", fail_attach)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, f"fail-{where}", ["ada/demo"])
        builder, _account_id = _login(client, f"ada_{where[:6]}", ["builder"])
        created = client.post(
            "/api/v1/builds",
            json=_body(skill_id=skill["id"], artifact_name=f"fail-{where.replace('_', '-')[:24]}"),
        )
        assert created.status_code == 502, created.text
        assert created.json()["detail"] == detail
        assert [call[0] for call in jenkins.calls] == _expected_jenkins_calls(where)
        row = _build_row(app.state.engine)
        assert row.status == "not_built"
        assert row.queue_url is None
        assert row.build_number is None
        artifact = client.get(f"/api/v1/artifacts/{row.artifact_id}")
        assert artifact.status_code == 200, artifact.text
        assert artifact.json()["build_owned"] is True
        digest = hashlib.sha256(b"manual").hexdigest()
        for session in (builder, client):
            registered = session.post(
                f"/api/v1/artifacts/{row.artifact_id}/versions",
                json={"version": "1.0.0", "checksum": {"algorithm": "sha256", "value": digest}},
            )
            assert registered.status_code == 409, registered.text
            assert registered.json()["detail"] == BUILD_OWNED_WRITE_DETAIL
            uploaded = session.put(
                f"/api/v1/artifacts/{row.artifact_id}/versions/1.0.0/content",
                content=b"manual",
                headers=content_headers(b"manual"),
            )
            assert uploaded.status_code == 409, uploaded.text
        jenkins.fail_read_progress = True
        again = client.get(f"/api/v1/builds/{row.id}")
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "not_built"
        assert again.json()["build_number"] is None
        assert "queue_url" not in again.json()
        assert _version_count(app.state.engine, str(row.artifact_id)) == 0


def test_mark_not_built_failure_still_raises_the_original_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _git, jenkins = _ready_executor(tmp_path)
    detail = "Jenkins 建任务失败（127.0.0.1:9）"
    jenkins.create_error = UpstreamFailure(detail)

    def explode(_build_id: UUID) -> None:
        raise RuntimeError("mark failed")

    monkeypatch.setattr(app.state.builds, "mark_not_built", explode)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "mark-fail", ["ada/demo"])
        created = client.post("/api/v1/builds", json=_body(skill_id=skill["id"]))
        assert created.status_code == 502, created.text
        assert created.json()["detail"] == detail
        assert [call[0] for call in jenkins.calls] == ["create_product_job", "delete_job"]
        row = _build_row(app.state.engine)
        assert row.status == "queued"
        assert row.queue_url is None
        assert row.build_number is None
        assert client.get(f"/api/v1/artifacts/{row.artifact_id}").json()["build_owned"] is True


def test_register_and_upload_same_version_do_not_deadlock(tmp_path: Path) -> None:
    """Acceptance 4. Concurrent register and upload stay out of 500 for 200 rounds."""

    assert REGISTER_RACE_ROUNDS >= 200
    app, _git, jenkins = _ready_executor(tmp_path)
    payload = b"race-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    with _running(app) as base:
        truncate_domain(app.state.engine)
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as http:
            machine = auth_headers()
            for round_id in range(REGISTER_RACE_ROUNDS):
                created = http.post(
                    "/api/v1/artifacts",
                    headers=machine,
                    json={"name": f"plain{round_id:04d}", "package_type": "generic"},
                )
                assert created.status_code == 201, created.text
                artifact_id = created.json()["id"]
                barrier = threading.Barrier(2)
                outcome: dict[str, object] = {}

                def register() -> None:
                    barrier.wait()
                    response = _register(http, machine, artifact_id, "1.0.0", digest)
                    outcome["register"] = response.status_code

                def upload() -> None:
                    barrier.wait()
                    response = _upload(http, machine, artifact_id, "1.0.0", payload)
                    outcome["upload"] = response.status_code

                left = threading.Thread(target=_capture(outcome, "register_error", register))
                right = threading.Thread(target=_capture(outcome, "upload_error", upload))
                left.start()
                right.start()
                left.join(timeout=15)
                right.join(timeout=15)
                assert "register_error" not in outcome, outcome.get("register_error")
                assert "upload_error" not in outcome, outcome.get("upload_error")
                pair = (outcome["register"], outcome["upload"])
                assert pair[0] not in range(500, 600)
                assert pair[1] not in range(500, 600)
                assert pair in {(201, 201), (201, 200), (409, 201)}, (round_id, pair)
                with app.state.engine.connect() as connection:
                    row = connection.execute(
                        text(
                            """
                            SELECT source, content_available
                            FROM artifact_versions
                            WHERE artifact_id = :id
                            """
                        ),
                        {"id": artifact_id},
                    ).one()
                assert tuple(row) == ("upload", True)

            skill = _publish(http)
            owned = http.post(
                "/api/v1/builds",
                headers=machine,
                json=_body(skill_id=skill["id"], artifact_name="owned-race"),
            )
            assert owned.status_code == 201, owned.text
            artifact_id = owned.json()["artifact_id"]
            for round_id in range(REGISTER_RACE_ROUNDS):
                barrier = threading.Barrier(2)
                outcome = {}

                def register_owned() -> None:
                    barrier.wait()
                    response = _register(http, machine, artifact_id, "1.0.0", digest)
                    outcome["register"] = response.status_code

                def upload_owned() -> None:
                    barrier.wait()
                    response = _upload(http, machine, artifact_id, "1.0.0", b"nope")
                    outcome["upload"] = response.status_code

                left = threading.Thread(target=_capture(outcome, "register_error", register_owned))
                right = threading.Thread(target=_capture(outcome, "upload_error", upload_owned))
                left.start()
                right.start()
                left.join(timeout=15)
                right.join(timeout=15)
                assert "register_error" not in outcome, (round_id, outcome.get("register_error"))
                assert "upload_error" not in outcome, (round_id, outcome.get("upload_error"))
                assert outcome["register"] == 409, (round_id, outcome)
                assert outcome["upload"] == 409, (round_id, outcome)
            assert _version_count(app.state.engine, artifact_id) == 0
            assert jenkins.calls


def test_add_version_and_direct_build_insert_cannot_both_commit(client: TestClient) -> None:
    """Acceptance 5. Whichever transaction locks the artifact first excludes the other."""

    engine = client.app.state.engine
    store = client.app.state.store
    first = _plain_artifact(client, "lock-first")
    _build_wins(engine, store, first)
    second = _plain_artifact(client, "lock-second")
    _version_wins(engine, store, second)


def test_registered_upload_without_bytes_is_foreign_version(tmp_path: Path) -> None:
    """Acceptance 6. A historical upload row with no bytes is a visible conflict."""

    app, _git, jenkins = _ready_executor(tmp_path)

    def forbid_download(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("collect_build downloaded over a foreign version")

    jenkins.download_artifact = forbid_download  # type: ignore[method-assign]
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "empty-foreign", ["ada/demo"])
        created = client.post("/api/v1/builds", json=_body(skill_id=skill["id"]))
        assert created.status_code == 201, created.text
        build = created.json()
        _insert_version(
            app.state.engine,
            build["artifact_id"],
            build["version"],
            source="upload",
            digest=hashlib.sha256(b"unused").hexdigest(),
        )
        detail = client.get(f"/api/v1/builds/{build['id']}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["status"] == "success"
        assert body["content_conflict"] == "foreign_version"
        assert body["content_available"] is False
        with app.state.engine.connect() as connection:
            stored = connection.execute(
                text("SELECT content_stored FROM product_builds WHERE id = :id"),
                {"id": build["id"]},
            ).scalar_one()
        assert stored is False


def test_retrieve_reports_the_stored_source(tmp_path: Path) -> None:
    """Acceptance 7. Metadata, the byte header, and a foreign version detail carry source."""

    app, _git, jenkins = _ready_executor(tmp_path)
    payload = b"built-source"
    jenkins.payload = payload
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "source-flow", ["ada/demo"])
        created = client.post(
            "/api/v1/builds",
            json=_body(skill_id=skill["id"], artifact_name="source-pkg"),
        )
        assert created.status_code == 201, created.text
        build = created.json()
        finished = client.get(f"/api/v1/builds/{build['id']}")
        assert finished.json()["content_available"] is True
        reserved = client.post(
            f"/api/v1/artifacts/{build['artifact_id']}/versions/{build['version']}/download-reservations",
            json={"requester": "ops_user"},
        )
        assert reserved.status_code == 201, reserved.text
        meta = client.get(reserved.json()["retrieve_path"])
        assert meta.status_code == 200, meta.text
        assert meta.json()["source"] == "build"
        downloaded = client.get(f"{reserved.json()['retrieve_path']}/content")
        assert downloaded.status_code == 200
        assert downloaded.content == payload
        assert downloaded.headers["X-Kiln-Source"] == "build"

        hand_id = _plain_artifact(client, "hand-source")
        hand_bytes = b"hand-source"
        stored = client.put(
            f"/api/v1/artifacts/{hand_id}/versions/1.0.0/content",
            content=hand_bytes,
            headers=content_headers(hand_bytes, source="build"),
        )
        assert stored.status_code == 201, stored.text
        assert stored.json()["source"] == "upload"
        hand_reservation = client.post(
            f"/api/v1/artifacts/{hand_id}/versions/1.0.0/download-reservations",
            json={"requester": "ops_user"},
        )
        hand_meta = client.get(hand_reservation.json()["retrieve_path"])
        assert hand_meta.json()["source"] == "upload"
        hand_body = client.get(f"{hand_reservation.json()['retrieve_path']}/content")
        assert hand_body.headers["X-Kiln-Source"] == "upload"

        _insert_version(
            app.state.engine,
            build["artifact_id"],
            "9.9.9",
            source="upload",
            digest=hashlib.sha256(b"foreign").hexdigest(),
        )
        foreign_reservation = client.post(
            f"/api/v1/artifacts/{build['artifact_id']}/versions/9.9.9/download-reservations",
            json={"requester": "ops_user"},
        )
        assert foreign_reservation.status_code == 201, foreign_reservation.text
        foreign = client.get(foreign_reservation.json()["retrieve_path"])
        assert foreign.status_code == 200, foreign.text
        assert foreign.json()["source"] == "upload"
        assert FOREIGN_BUILD_VERSION_DETAIL in foreign.json()["detail"]
        assert "不是产品构建产物" in foreign.json()["detail"]

        reader, _reader_id = _login(client, "ada_src", ["reader"])
        denied = reader.get(reserved.json()["retrieve_path"])
        assert denied.status_code == 403
        assert denied.json()["detail"] == "没有权限"
        denied_bytes = reader.get(f"{reserved.json()['retrieve_path']}/content")
        assert denied_bytes.status_code == 403


def test_manual_fill_rejects_historical_build_source_rows(client: TestClient) -> None:
    """Acceptance 8. A pre-slice empty build row rejects manual bytes and still collects."""

    artifact_id = _plain_artifact(client, "hist-build-src")
    payload = b"historical-fill"
    digest = hashlib.sha256(payload).hexdigest()
    _insert_version(client.app.state.engine, artifact_id, "1.0.0", source="build", digest=digest)
    builder, _account_id = _login(client, "ada_hist", ["builder"])
    for session in (builder, client):
        uploaded = session.put(
            f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
            content=payload,
            headers=content_headers(payload),
        )
        assert uploaded.status_code == 409, uploaded.text
        assert uploaded.json()["detail"] == BUILD_SOURCE_FILL_DETAIL
    with client.app.state.engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT source, content_available, blob_key
                FROM artifact_versions
                WHERE artifact_id = :id AND version = '1.0.0'
                """
            ),
            {"id": artifact_id},
        ).one()
    assert tuple(row) == ("build", False, None)
    collected, created = client.app.state.store.store_version_bytes(
        UUID(artifact_id),
        "1.0.0",
        payload,
        Checksum(algorithm="sha256", value=digest),
        ArtifactSource.build,
        manual=False,
    )
    assert created is True
    assert collected.source == ArtifactSource.build
    assert collected.content_available is True


def test_docs_separate_the_two_source_headers() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")
    description = (ROOT / "backend" / "kiln" / "main.py").read_text(encoding="utf-8")
    retrieve = (ROOT / "backend" / "kiln" / "api" / "retrieve.py").read_text(encoding="utf-8")
    for document in (readme, deploy, description):
        assert "not_built" in document
        assert "foreign_bytes" in document
        assert "foreign_version" in document
        assert "0011_actor_indexes" in document
        assert "该版本登记为构建来源，不接受手工补传" in document
        assert "请求头" in document and "被忽略" in document
        assert "响应头" in document
        folded = document.casefold()
        assert "联邦" not in document
        assert "federation" not in folded
    assert "该版本不是产品构建产物" in readme
    assert "X-Kiln-Source" in retrieve
    assert 'enum": ["build", "upload"]' in retrieve or "build" in retrieve


def test_openapi_publish_source_header_and_hides_queue_url(client: TestClient) -> None:
    schema = client.get("/openapi.json")
    assert schema.status_code == 200, schema.text
    body = schema.json()
    content = body["paths"]["/api/v1/retrieve/{reservation_id}/content"]["get"]
    header = content["responses"]["200"]["headers"]["X-Kiln-Source"]["schema"]
    assert header["enum"] == ["build", "upload"]
    assert "不读取请求输入" in content["responses"]["200"]["description"]
    builds = body["components"]["schemas"]["ProductBuild"]["properties"]
    assert "queue_url" not in builds
    rendered = str(builds["content_conflict"])
    assert "foreign_bytes" in rendered
    assert "foreign_version" in rendered
    assert "source" in body["components"]["schemas"]["RetrieveResult"]["properties"]


def _build_wins(engine: Engine, store: object, artifact_id: str) -> None:
    holding = threading.Event()
    release = threading.Event()
    build_id = uuid4()

    def hold() -> None:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT id FROM artifacts WHERE id = :id FOR UPDATE"),
                {"id": artifact_id},
            )
            _insert_build(connection, artifact_id, build_id)
            holding.set()
            assert release.wait(timeout=10)
            transaction.commit()

    locker = threading.Thread(target=hold)
    locker.start()
    assert holding.wait(timeout=5)
    outcome: dict[str, object] = {}

    def add() -> None:
        try:
            store.add_version(  # type: ignore[attr-defined]
                UUID(artifact_id),
                _version_payload("1.0.0"),
            )
            outcome["status"] = "created"
        except Conflict as exc:
            outcome["status"] = "conflict"
            outcome["detail"] = exc.detail

    adder = threading.Thread(target=add)
    adder.start()
    time.sleep(0.2)
    assert adder.is_alive()
    release.set()
    adder.join(timeout=10)
    locker.join(timeout=10)
    assert outcome["status"] == "conflict"
    assert outcome["detail"] == BUILD_OWNED_WRITE_DETAIL
    assert _version_count(engine, artifact_id) == 0
    assert _build_count(engine, artifact_id) == 1


def _version_wins(engine: Engine, store: object, artifact_id: str) -> None:
    paused = threading.Event()
    go = threading.Event()

    def before_cursor_execute(
        _conn: object,
        _cursor: object,
        statement: object,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        sql = str(statement).lstrip().upper()
        if sql.startswith("INSERT") and "ARTIFACT_VERSIONS" in sql:
            paused.set()
            assert go.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    outcome: dict[str, object] = {}

    def add() -> None:
        store.add_version(UUID(artifact_id), _version_payload("1.0.0"))  # type: ignore[attr-defined]
        outcome["added"] = True

    adder = threading.Thread(target=add)
    adder.start()
    try:
        assert paused.wait(timeout=5)
        build_id = uuid4()

        def insert_build() -> None:
            with engine.connect() as connection:
                transaction = connection.begin()
                connection.execute(
                    text("SELECT id FROM artifacts WHERE id = :id FOR UPDATE"),
                    {"id": artifact_id},
                )
                found = connection.execute(
                    text("SELECT 1 FROM artifact_versions WHERE artifact_id = :id"),
                    {"id": artifact_id},
                ).first()
                if found is not None:
                    transaction.rollback()
                    outcome["build"] = "skipped"
                    return
                _insert_build(connection, artifact_id, build_id)
                transaction.commit()
                outcome["build"] = "inserted"

        builder = threading.Thread(target=insert_build)
        builder.start()
        time.sleep(0.2)
        assert builder.is_alive()
        go.set()
        adder.join(timeout=10)
        builder.join(timeout=10)
    finally:
        go.set()
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    assert outcome.get("added") is True
    assert outcome.get("build") == "skipped"
    assert _version_count(engine, artifact_id) == 1
    assert _build_count(engine, artifact_id) == 0
    with engine.connect() as connection:
        source = connection.execute(
            text("SELECT source FROM artifact_versions WHERE artifact_id = :id"),
            {"id": artifact_id},
        ).scalar_one()
    assert source == "upload"


def _insert_build(connection: object, artifact_id: str, build_id: UUID) -> None:
    connection.execute(  # type: ignore[attr-defined]
        text(
            """
            INSERT INTO product_builds (
                id, name, jenkins_job_name, repository, git_ref, commit_sha,
                artifact_id, version, package_type, queue_url, build_number,
                status, content_stored, created_at, updated_at
            ) VALUES (
                :id, 'direct', :job, 'ada/demo', 'main', :sha,
                :artifact_id, '1.0.0', 'generic', NULL, NULL,
                'queued', false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "id": build_id,
            "job": f"kiln-build-{build_id.hex}",
            "sha": "c" * 40,
            "artifact_id": artifact_id,
        },
    )


def _insert_version(
    engine: Engine,
    artifact_id: str,
    version: str,
    *,
    source: str,
    digest: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO artifact_versions (
                    artifact_id, version, checksum_algorithm, checksum_value,
                    size_bytes, source, content_available, blob_key, created_at
                ) VALUES (
                    :artifact_id, :version, 'sha256', :digest,
                    NULL, :source, false, NULL, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "artifact_id": artifact_id,
                "version": version,
                "digest": digest,
                "source": source,
            },
        )


def _version_payload(version: str) -> object:
    from kiln.domain.artifacts import ArtifactVersionCreate

    return ArtifactVersionCreate(
        version=version,
        checksum=Checksum(algorithm="sha256", value="ab" * 32),
    )


def _plain_artifact(client: TestClient, name: str) -> str:
    created = client.post(
        "/api/v1/artifacts",
        json={"name": name, "package_type": "generic"},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _publish(http: httpx.Client) -> dict:
    created = http.post(
        "/api/v1/skills",
        headers=auth_headers(),
        json={
            "name": f"own-{uuid4().hex[:8]}",
            "version": "0.1.0",
            "allowed_repositories": ["ada/demo"],
        },
    )
    assert created.status_code == 201, created.text
    skill_id = created.json()["id"]
    submitted = http.post(
        f"/api/v1/skills/{skill_id}/submit-review",
        headers=auth_headers(),
    )
    assert submitted.status_code == 200
    published = http.post(f"/api/v1/skills/{skill_id}/publish", headers=auth_headers())
    assert published.status_code == 200, published.text
    return published.json()


def _session(http: httpx.Client, username: str, role_ids: list[str]) -> dict[str, str]:
    created = http.post(
        "/api/v1/accounts",
        headers=auth_headers(),
        json={
            "username": username,
            "display_name": username,
            "password": LOCAL_PASSWORD,
            "role_ids": role_ids,
        },
    )
    assert created.status_code == 201, created.text
    logged = http.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_PASSWORD},
    )
    assert logged.status_code == 200, logged.text
    return {"Authorization": f"Bearer {logged.json()['token']}"}


def _register(
    http: httpx.Client,
    headers: dict[str, str],
    artifact_id: str,
    version: str,
    digest: str,
) -> httpx.Response:
    return http.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        headers=headers,
        json={"version": version, "checksum": {"algorithm": "sha256", "value": digest}},
    )


def _upload(
    http: httpx.Client,
    headers: dict[str, str],
    artifact_id: str,
    version: str,
    payload: bytes,
) -> httpx.Response:
    return http.put(
        f"/api/v1/artifacts/{artifact_id}/versions/{version}/content",
        headers={**headers, **content_headers(payload)},
        content=payload,
    )


def _version_count(engine: Engine, artifact_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM artifact_versions WHERE artifact_id = :id"),
                {"id": artifact_id},
            ).scalar_one()
        )


def _build_count(engine: Engine, artifact_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM product_builds WHERE artifact_id = :id"),
                {"id": artifact_id},
            ).scalar_one()
        )


class _BuildRow:
    def __init__(self, row: tuple) -> None:
        self.id, self.status, self.queue_url, self.build_number, self.artifact_id = row


def _build_row(engine: Engine) -> _BuildRow:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT id::text, status, queue_url, build_number, artifact_id::text
                FROM product_builds
                """
            )
        ).one()
    return _BuildRow(tuple(row))


def _expected_jenkins_calls(where: str) -> list[str]:
    if where == "create_product_job":
        return ["create_product_job", "delete_job"]
    return ["create_product_job", "trigger_build", "delete_job"]


def _capture(outcome: dict[str, object], key: str, function: object) -> object:
    def run() -> None:
        try:
            function()  # type: ignore[operator]
        except Exception as exc:
            outcome[key] = exc

    return run


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _running:
    def __init__(self, app: object) -> None:
        self.app = app
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                access_log=False,
            )
        )
        self.thread = threading.Thread(target=self.server.run, name="kiln-s1b-api", daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not self.thread.is_alive() and self.server.started is False:
                break
            try:
                response = httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=0.5)
            except httpx.HTTPError:
                time.sleep(0.05)
                continue
            if response.status_code == 200:
                return f"http://127.0.0.1:{self.port}"
            time.sleep(0.05)
        raise RuntimeError("kiln API did not start")

    def __exit__(self, *_exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
