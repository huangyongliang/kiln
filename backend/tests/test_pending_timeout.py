"""S2a acceptance for pending-build expiry and the mark_not_built lock."""

import inspect
import threading
import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from kiln.domain.jobs import JobStatus
from kiln.errors import UpstreamFailure
from kiln.executor.product import sync_status
from tests.support import auth_headers, publish_skill, truncate_domain
from tests.test_product_build import _body, _ready_executor


def _backdate(engine: Engine, build_id: str, seconds: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE product_builds
                SET created_at = CURRENT_TIMESTAMP - make_interval(secs => :seconds)
                WHERE id = :id
                """
            ),
            {"seconds": seconds, "id": build_id},
        )


def _columns(engine: Engine, build_id: str) -> tuple[object, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                text(
                    """
                    SELECT status, build_number, queue_url
                    FROM product_builds
                    WHERE id = :id
                    """
                ),
                {"id": build_id},
            ).one()
        )


def _progress(jenkins: object, result: object) -> None:
    def read_progress(*args: object, **_kwargs: object) -> object:
        jenkins.calls.append(("read_progress",) + args)  # type: ignore[attr-defined]
        if isinstance(result, Exception):
            raise result
        return result

    jenkins.read_progress = read_progress  # type: ignore[attr-defined]


def _queued_build(client: TestClient, skill_id: str, name: str) -> dict[str, object]:
    created = client.post(
        "/api/v1/builds",
        json=_body(skill_id=skill_id, artifact_name=name),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["build_number"] is None
    return body


def test_old_queued_build_expires_and_later_reads_skip_jenkins(tmp_path: Path) -> None:
    """Acceptance 7. An old queued row with no number becomes not_built."""

    app, _git, jenkins = _ready_executor(tmp_path)
    app.state.settings = app.state.settings.model_copy(
        update={"kiln_build_pending_timeout_seconds": 60}
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "expire-old", ["ada/demo"])
        body = _queued_build(client, skill["id"], "expire-old")
        build_id = body["id"]
        queue_url = _columns(app.state.engine, build_id)[2]
        assert queue_url is not None
        _backdate(app.state.engine, build_id, 90)
        _progress(jenkins, ("queued", None))

        def delete_job(job_name: str) -> None:
            jenkins.calls.append(("delete_job", job_name))
            raise UpstreamFailure("Jenkins 删除失败")

        jenkins.delete_job = delete_job  # type: ignore[method-assign]
        detail = client.get(f"/api/v1/builds/{build_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "not_built"
        assert detail.json()["build_number"] is None
        assert _columns(app.state.engine, build_id) == ("not_built", None, queue_url)
        assert [call[0] for call in jenkins.calls].count("delete_job") == 1
        progress_after = [call for call in jenkins.calls if call[0] == "read_progress"]
        assert len(progress_after) == 1

        again = client.get(f"/api/v1/builds/{build_id}")
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "not_built"
        listed = client.get("/api/v1/builds")
        assert listed.status_code == 200, listed.text
        match = next(item for item in listed.json()["items"] if item["id"] == build_id)
        assert match["status"] == "not_built"
        assert match["build_number"] is None
        log = client.get(f"/api/v1/builds/{build_id}/log")
        assert log.status_code == 409, log.text
        assert log.json()["detail"] == "这次构建还没有日志"
        assert [call for call in jenkins.calls if call[0] == "read_progress"] == progress_after
        assert [call[0] for call in jenkins.calls].count("delete_job") == 1


def test_pending_timeout_leaves_young_running_and_upstream_rows(tmp_path: Path) -> None:
    """Acceptance 7 controls. Young, running, upstream failure, and the default threshold."""

    app, _git, jenkins = _ready_executor(tmp_path)
    app.state.settings = app.state.settings.model_copy(
        update={"kiln_build_pending_timeout_seconds": 60}
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "expire-controls", ["ada/demo"])
        young = _queued_build(client, skill["id"], "expire-young")
        _backdate(app.state.engine, young["id"], 30)
        _progress(jenkins, ("queued", None))
        detail = client.get(f"/api/v1/builds/{young['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "queued"
        assert detail.json()["build_number"] is None
        assert not any(call[0] == "delete_job" for call in jenkins.calls)

        running = _queued_build(client, skill["id"], "expire-running")
        _backdate(app.state.engine, running["id"], 90)
        _progress(jenkins, ("running", 1))
        moved = client.get(f"/api/v1/builds/{running['id']}")
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == "running"
        assert moved.json()["build_number"] == 1
        assert not any(call[0] == "delete_job" for call in jenkins.calls)

        failed = _queued_build(client, skill["id"], "expire-upstream")
        before = _columns(app.state.engine, failed["id"])
        _backdate(app.state.engine, failed["id"], 90)
        _progress(jenkins, UpstreamFailure("Jenkins 调用失败（127.0.0.1:9）"))
        upstream = client.get(f"/api/v1/builds/{failed['id']}")
        assert upstream.status_code == 502, upstream.text
        assert _columns(app.state.engine, failed["id"])[0] == "queued"
        assert _columns(app.state.engine, failed["id"])[1] is None
        assert _columns(app.state.engine, failed["id"])[2] == before[2]
        assert not any(call[0] == "delete_job" for call in jenkins.calls)

    default_app, _git, default_jenkins = _ready_executor(tmp_path)
    assert default_app.state.settings.kiln_build_pending_timeout_seconds == 1800
    with TestClient(default_app, headers=auth_headers()) as client:
        truncate_domain(default_app.state.engine)
        skill = publish_skill(client, "expire-default", ["ada/demo"])
        body = _queued_build(client, skill["id"], "expire-default")
        _backdate(default_app.state.engine, body["id"], 90)
        _progress(default_jenkins, ("queued", None))
        detail = client.get(f"/api/v1/builds/{body['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "queued"
        assert not any(call[0] == "delete_job" for call in default_jenkins.calls)


def test_expire_pending_loses_to_save_run_and_save_run_loses_to_expire(tmp_path: Path) -> None:
    """Acceptance 8. Hooks force both orders. Neither response is 5xx."""

    app, _git, jenkins = _ready_executor(tmp_path)
    app.state.settings = app.state.settings.model_copy(
        update={"kiln_build_pending_timeout_seconds": 60}
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "expire-race", ["ada/demo"])
        first = _queued_build(client, skill["id"], "expire-save-first")
        _backdate(app.state.engine, first["id"], 90)

        def read_after_save(*args: object, **_kwargs: object) -> tuple[str, None]:
            jenkins.calls.append(("read_progress",) + args)
            app.state.builds.save_run(
                UUID(first["id"]),
                status=JobStatus.running,
                build_number=1,
            )
            return "queued", None

        jenkins.read_progress = read_after_save  # type: ignore[method-assign]
        saved_first = client.get(f"/api/v1/builds/{first['id']}")
        assert saved_first.status_code == 200, saved_first.text
        assert saved_first.json()["status"] == "running"
        assert saved_first.json()["build_number"] == 1
        assert not any(call[0] == "delete_job" for call in jenkins.calls)
        assert _columns(app.state.engine, first["id"])[:2] == ("running", 1)

        second = _queued_build(client, skill["id"], "expire-first")
        _backdate(app.state.engine, second["id"], 90)
        saved: dict[str, object] = {}

        def read_queued(*args: object, **_kwargs: object) -> tuple[str, None]:
            jenkins.calls.append(("read_progress",) + args)
            return "queued", None

        def delete_then_save(job_name: str) -> None:
            jenkins.calls.append(("delete_job", job_name))
            saved["row"] = app.state.builds.save_run(
                UUID(second["id"]),
                status=JobStatus.running,
                build_number=1,
            )

        jenkins.read_progress = read_queued  # type: ignore[method-assign]
        jenkins.delete_job = delete_then_save  # type: ignore[method-assign]
        expired_first = client.get(f"/api/v1/builds/{second['id']}")
        assert expired_first.status_code == 200, expired_first.text
        assert expired_first.json()["status"] == "not_built"
        assert expired_first.json()["build_number"] is None
        assert saved["row"].status == JobStatus.not_built  # type: ignore[attr-defined]
        assert saved["row"].build_number is None  # type: ignore[attr-defined]
        assert _columns(app.state.engine, second["id"])[:2] == ("not_built", None)


def test_mark_not_built_rechecks_under_the_row_lock(tmp_path: Path) -> None:
    """Acceptance 9. mark_not_built waits, then keeps a newer status and queue URL."""

    app, _git, jenkins = _ready_executor(tmp_path)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "mark-lock", ["ada/demo"])
        waiting = _queued_build(client, skill["id"], "mark-waits")
        queue_url = _columns(app.state.engine, waiting["id"])[2]
        _assert_mark_waits_then_keeps_running(app.state.engine, app.state.builds, waiting["id"])

        fresh = _queued_build(client, skill["id"], "mark-queued")
        queued_url = _columns(app.state.engine, fresh["id"])[2]
        _assert_mark_waits_on_an_unchanged_row(app.state.engine, app.state.builds, fresh["id"])
        assert _columns(app.state.engine, fresh["id"]) == ("not_built", None, queued_url)

        running = _queued_build(client, skill["id"], "mark-running")
        app.state.builds.save_run(UUID(running["id"]), status=JobStatus.running, build_number=1)
        kept = app.state.builds.mark_not_built(UUID(running["id"]))
        assert kept.status == JobStatus.running
        assert kept.build_number == 1
        assert _columns(app.state.engine, running["id"])[:2] == ("running", 1)

        success = _queued_build(client, skill["id"], "mark-success")
        app.state.builds.save_run(UUID(success["id"]), status=JobStatus.success, build_number=1)
        still = app.state.builds.mark_not_built(UUID(success["id"]))
        assert still.status == JobStatus.success
        assert still.build_number == 1
        assert _columns(app.state.engine, success["id"])[0] == "success"

        numbered = _queued_build(client, skill["id"], "mark-numbered")
        app.state.builds.save_run(UUID(numbered["id"]), status=JobStatus.queued, build_number=1)
        unchanged = app.state.builds.mark_not_built(UUID(numbered["id"]))
        assert unchanged.status == JobStatus.queued
        assert unchanged.build_number == 1

        first = _queued_build(client, skill["id"], "mark-first")
        marked_url = _columns(app.state.engine, first["id"])[2]
        marked = app.state.builds.mark_not_built(UUID(first["id"]))
        assert marked.status == JobStatus.not_built
        assert marked.queue_url == marked_url
        assert marked.build_number is None
        assert _columns(app.state.engine, first["id"]) == ("not_built", None, marked_url)

        def read_after_mark(*_args: object, **_kwargs: object) -> tuple[str, int]:
            app.state.builds.mark_not_built(UUID(first["id"]))
            return "running", 1

        jenkins.read_progress = read_after_mark  # type: ignore[method-assign]
        followed = client.get(f"/api/v1/builds/{first['id']}")
        assert followed.status_code == 200, followed.text
        assert followed.json()["status"] == "not_built"
        assert followed.json()["build_number"] is None
        assert _columns(app.state.engine, first["id"]) == ("not_built", None, marked_url)

    assert queue_url is not None
    source = inspect.getsource(sync_status)
    tail = source.split("read_progress", 1)[1]
    assert "JobStatus.not_built" not in tail


def _assert_mark_waits_then_keeps_running(engine: Engine, builds: object, build_id: str) -> None:
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT id FROM product_builds WHERE id = :id FOR UPDATE"),
                {"id": build_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE product_builds
                    SET status = 'running', build_number = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """
                ),
                {"id": build_id},
            )
            holding.set()
            assert release.wait(timeout=10)
            transaction.commit()

    locker = threading.Thread(target=hold)
    locker.start()
    assert holding.wait(timeout=5)
    outcome: dict[str, object] = {}

    def mark() -> None:
        outcome["row"] = builds.mark_not_built(UUID(build_id))  # type: ignore[attr-defined]

    marker = threading.Thread(target=mark)
    marker.start()
    time.sleep(0.2)
    assert marker.is_alive()
    release.set()
    marker.join(timeout=10)
    locker.join(timeout=10)
    assert not marker.is_alive()
    assert outcome["row"].status == JobStatus.running  # type: ignore[attr-defined]
    assert outcome["row"].build_number == 1  # type: ignore[attr-defined]
    assert _columns(engine, build_id)[:2] == ("running", 1)


def _assert_mark_waits_on_an_unchanged_row(engine: Engine, builds: object, build_id: str) -> None:
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text("SELECT id FROM product_builds WHERE id = :id FOR UPDATE"),
                {"id": build_id},
            )
            holding.set()
            assert release.wait(timeout=10)
            transaction.commit()

    locker = threading.Thread(target=hold)
    locker.start()
    assert holding.wait(timeout=5)
    outcome: dict[str, object] = {}

    def mark() -> None:
        outcome["row"] = builds.mark_not_built(UUID(build_id))  # type: ignore[attr-defined]

    marker = threading.Thread(target=mark)
    marker.start()
    time.sleep(0.2)
    assert marker.is_alive()
    release.set()
    marker.join(timeout=10)
    locker.join(timeout=10)
    assert not marker.is_alive()
    assert outcome["row"].status == JobStatus.not_built  # type: ignore[attr-defined]
    assert outcome["row"].build_number is None  # type: ignore[attr-defined]


def test_save_run_hook_commits_before_mark_not_built_rechecks(tmp_path: Path) -> None:
    """Acceptance 9. A cursor hook commits save_run before mark_not_built locks the row."""

    app, _git, _jenkins = _ready_executor(tmp_path)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "mark-hook", ["ada/demo"])
        body = _queued_build(client, skill["id"], "mark-hook")
        state = {"armed": True}

        def before_cursor_execute(
            _conn: object,
            _cursor: object,
            statement: object,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            sql = " ".join(str(statement).lower().split())
            locking_build = (
                state["armed"]
                and sql.startswith("select")
                and "product_builds" in sql
                and "for update" in sql
            )
            if locking_build:
                state["armed"] = False
                app.state.builds.save_run(
                    UUID(body["id"]),
                    status=JobStatus.success,
                    build_number=1,
                )

        event.listen(app.state.engine, "before_cursor_execute", before_cursor_execute)
        try:
            row = app.state.builds.mark_not_built(UUID(body["id"]))
        finally:
            event.remove(app.state.engine, "before_cursor_execute", before_cursor_execute)
        assert state["armed"] is False
        assert row.status == JobStatus.success
        assert row.build_number == 1
        assert _columns(app.state.engine, body["id"])[:2] == ("success", 1)
