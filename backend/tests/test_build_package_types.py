"""Build package type catalog, skill binding, and Jenkins label pin.

Jenkins is mocked. These tests do not start compose, Nexus, Gitea, or Jenkins.
"""

from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.models import Base
from kiln.domain.package_types import (
    SEED_CODES,
    SEED_LABELS,
    TYPE_DISABLED_DRAFT,
    TYPE_MISMATCH,
    TYPE_REFERENCED,
    node_probe_failed,
    node_unavailable,
)
from kiln.errors import BadRequest, UpstreamFailure
from kiln.executor.jenkins import JenkinsClient
from kiln.executor.script import product_job_config
from tests.support import auth_headers, postgres_url, publish_skill, truncate_domain
from tests.test_nexus import _UNSET_JOB_CONFIG
from tests.test_persistence import _alembic_config
from tests.test_product_build import _body, _ready_executor, _RecordingJenkins

LOCAL_PASSWORD = "correct-horse-1"
_COMPUTER = {
    "displayName": "agent-1",
    "offline": False,
    "assignedLabels": [{"name": "linux"}, {"name": "agent-1"}],
}


def test_unset_job_config_stays_byte_compatible_and_pins_one_node() -> None:
    command = "echo kiln\n"
    assert product_job_config(command) == _UNSET_JOB_CONFIG
    assert product_job_config(command, assigned_node=None) == _UNSET_JOB_CONFIG
    assert product_job_config(command, assigned_node="") == _UNSET_JOB_CONFIG
    assert "assignedNode" not in _UNSET_JOB_CONFIG
    pinned = product_job_config(command, assigned_node="a<b>&c")
    assert "<canRoam>false</canRoam>" in pinned
    assert "<assignedNode>a&lt;b&gt;&amp;c</assignedNode>" in pinned
    assert "<canRoam>true</canRoam>" not in pinned
    with pytest.raises(BadRequest, match="&&"):
        product_job_config(command, assigned_node="linux && built-in")


def test_online_node_probe_uses_the_computer_api() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["tree"] = request.url.params["tree"]
        if request.url.params.get("case") == "down":
            return httpx.Response(503, json={"computer": []})
        if request.url.params.get("case") == "bad":
            return httpx.Response(200, json={"computer": {"no": "list"}})
        if request.url.params.get("case") == "offline":
            body = {
                "computer": [
                    {
                        "displayName": "agent-1",
                        "offline": True,
                        "assignedLabels": [{"name": "linux"}],
                    }
                ]
            }
            return httpx.Response(200, json=body)
        if request.url.params.get("case") == "name":
            body = {
                "computer": [
                    {
                        "displayName": "Built-In Node",
                        "offline": False,
                        "assignedLabels": [],
                    }
                ]
            }
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={"computer": [_COMPUTER]})

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        client = JenkinsClient("http://127.0.0.1:9", "kiln", "jenkins-token", http)
        assert client.has_online_node("linux") is True
        assert seen == {
            "path": "/computer/api/json",
            "tree": "computer[displayName,offline,assignedLabels[name]]",
        }
        assert client.has_online_node("missing") is False
    _assert_probe_shape("offline", False, "linux")
    _assert_probe_shape("name", True, "Built-In Node")
    _assert_probe_shape("down", None, "linux")
    _assert_probe_shape("bad", None, "linux")
    _assert_probe_timeout()


def test_migration_seeds_backfill_and_refuses_new_codes(client: TestClient) -> None:
    cfg = _alembic_config(postgres_url())
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0013_build_package_types"]
    revision = script.get_revision("0013_build_package_types")
    assert revision is not None
    assert revision.down_revision == "0012_account_lifecycle"
    _assert_empty_database_round_trip()

    engine = client.app.state.engine
    _assert_downgrade_keeps_new_codes(cfg, engine)
    names = {
        "draft": f"fill_draft_{uuid4().hex[:8]}",
        "in_review": f"fill_review_{uuid4().hex[:8]}",
        "published": f"fill_pub_{uuid4().hex[:8]}",
    }
    try:
        command.downgrade(cfg, "0012_account_lifecycle")
        with engine.begin() as connection:
            for status, name in names.items():
                connection.execute(
                    text(
                        """
                        INSERT INTO skills (
                            id, name, version, status, description, created_at, updated_at
                        ) VALUES (
                            :id, :name, '0.1.0', :status, '', now(), now()
                        )
                        """
                    ),
                    {"id": str(uuid4()), "name": name, "status": status},
                )
        command.upgrade(cfg, "head")
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT status, build_package_type
                    FROM skills
                    WHERE name LIKE 'fill_%'
                    ORDER BY status
                    """
                )
            ).all()
            seeds = connection.execute(
                text(
                    """
                    SELECT code, label, enabled, jenkins_label,
                           (SELECT count(*) FROM build_package_type_conditions c
                            WHERE c.package_type_code = build_package_types.code)
                    FROM build_package_types
                    ORDER BY position, code
                    """
                )
            ).all()
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
        assert [(status, code) for status, code in rows] == [
            ("draft", "generic"),
            ("in_review", "generic"),
            ("published", "generic"),
        ]
        assert [row[0] for row in seeds] == list(SEED_CODES)
        assert [(row[0], row[1]) for row in seeds] == list(SEED_LABELS.items())
        assert all(row[2] is True and row[3] is None and row[4] == 0 for row in seeds)
        assert diff == []
        command.downgrade(cfg, "0012_account_lifecycle")
        with engine.connect() as connection:
            catalog = connection.execute(
                text("SELECT to_regclass('public.build_package_types')")
            ).scalar_one()
            assert catalog is None
            column = connection.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_name = 'skills' AND column_name = 'build_package_type'
                    """
                )
            ).scalar_one()
            checks = set(
                connection.execute(
                    text(
                        """
                        SELECT conname FROM pg_constraint
                        WHERE conname IN (
                            'ck_artifacts_package_type',
                            'ck_download_reservations_package_type',
                            'ck_product_builds_package_type'
                        )
                        """
                    )
                ).scalars()
            )
        assert column == 0
        assert checks == {
            "ck_artifacts_package_type",
            "ck_download_reservations_package_type",
            "ck_product_builds_package_type",
        }
    finally:
        command.upgrade(cfg, "head")
        truncate_domain(engine)


def test_catalog_permissions_crud_and_reference_protection(client: TestClient) -> None:
    listed = client.get("/api/v1/build-package-types")
    assert listed.status_code == 200, listed.text
    assert [item["code"] for item in listed.json()["items"]] == list(SEED_CODES)
    assert listed.json()["items"][2]["label"] == "通用包"
    assert listed.json()["items"][2]["jenkins_label"] is None
    assert listed.json()["items"][2]["runtime_conditions"] == []

    builder = _login(client, f"ada_cat_{uuid4().hex[:6]}", ["builder"])
    reader = _login(client, f"ada_read_{uuid4().hex[:6]}", ["reader"])
    for session in (builder, reader):
        assert session.get("/api/v1/build-package-types").status_code == 200
        denied = session.get("/api/v1/build-package-types", params={"include_disabled": "true"})
        assert denied.status_code == 403
        assert denied.json()["detail"] == "没有权限（需要：build.catalog.manage）"
        posted = session.post("/api/v1/build-package-types", json=_node_body())
        assert posted.status_code == 403
        assert posted.json()["detail"] == "没有权限（需要：build.catalog.manage）"

    created = client.post("/api/v1/build-package-types", json=_node_body())
    assert created.status_code == 201, created.text
    assert created.json()["label"] == "Node"
    assert created.json()["runtime_conditions"] == [{"tool": "node", "version": "20"}]
    assert created.json()["jenkins_label"] == "linux"
    again = client.post("/api/v1/build-package-types", json=_node_body())
    assert again.status_code == 409
    enabled = client.get("/api/v1/build-package-types")
    assert "node" in [item["code"] for item in enabled.json()["items"]]

    disabled = client.put(
        "/api/v1/build-package-types/node",
        json=_node_body(enabled=False, include_code=False),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    visible = [item["code"] for item in client.get("/api/v1/build-package-types").json()["items"]]
    assert "node" not in visible
    full = client.get("/api/v1/build-package-types", params={"include_disabled": "true"})
    assert "node" in [item["code"] for item in full.json()["items"]]

    bound = client.post(
        "/api/v1/skills",
        json={"name": f"uses-node-{uuid4().hex[:6]}", "build_package_type": "node"},
    )
    assert bound.status_code == 422
    assert bound.json()["detail"] == TYPE_DISABLED_DRAFT
    client.put(
        "/api/v1/build-package-types/node",
        json=_node_body(include_code=False),
    )
    kept = client.post(
        "/api/v1/skills",
        json={"name": f"keeps-node-{uuid4().hex[:6]}", "build_package_type": "node"},
    )
    assert kept.status_code == 201, kept.text
    removed = client.delete("/api/v1/build-package-types/node")
    assert removed.status_code == 409
    assert removed.json()["detail"] == TYPE_REFERENCED
    anchored = client.post("/api/v1/skills", json={"name": f"holds-generic-{uuid4().hex[:6]}"})
    assert anchored.status_code == 201, anchored.text
    generic = client.delete("/api/v1/build-package-types/generic")
    assert generic.status_code == 409

    with client.app.state.engine.begin() as connection:
        connection.execute(text("DELETE FROM skills WHERE build_package_type = 'node'"))
    deleted = client.delete("/api/v1/build-package-types/node")
    assert deleted.status_code == 204, deleted.text
    assert _absent(client)


def test_skill_defaults_to_generic_and_published_stays(client: TestClient) -> None:
    created = client.post("/api/v1/skills", json={"name": f"plain-{uuid4().hex[:6]}"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["build_package_type"] == "generic"
    assert body["build_package_type_label"] == "通用包"
    assert body["build_runtime_conditions"] == []
    assert body["toolchain"] is None

    maven = client.post(
        "/api/v1/skills",
        json={"name": f"maven-{uuid4().hex[:6]}", "build_package_type": "maven"},
    )
    assert maven.status_code == 201, maven.text
    assert maven.json()["build_package_type"] == "maven"
    assert maven.json()["build_package_type_label"] == "Maven"

    published = publish_skill(client, f"locked-{uuid4().hex[:6]}", [])
    changed = client.patch(
        f"/api/v1/skills/{published['id']}",
        json={"name": published["name"], "version": "0.1.0", "build_package_type": "maven"},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"] == "只有草稿可以修改"
    assert client.get(f"/api/v1/skills/{published['id']}").json()["build_package_type"] == "generic"


def test_start_build_rejects_conflict_and_closes_when_the_probe_fails(tmp_path) -> None:
    app, git, _jenkins = _ready_executor(tmp_path)
    probe = _ProbeJenkins()
    app.state.jenkins = probe
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        _reset_extra_types(app.state.engine)
        try:
            skill = publish_skill(client, f"generic-flow-{uuid4().hex[:6]}", [])
            conflict = client.post(
                "/api/v1/builds",
                json=_body(skill_id=skill["id"], package_type="maven"),
            )
            assert conflict.status_code == 422
            assert conflict.json()["detail"] == TYPE_MISMATCH
            assert probe.calls == []
            assert git.calls == []
            _assert_no_build_rows(app.state.engine)

            created = client.post(
                "/api/v1/build-package-types",
                json=_node_body(jenkins_label="linux"),
            )
            assert created.status_code == 201, created.text
            draft = client.post(
                "/api/v1/skills",
                json={
                    "name": f"node-flow-{uuid4().hex[:6]}",
                    "build_package_type": "node",
                    "allowed_repositories": [],
                },
            )
            assert draft.status_code == 201, draft.text
            skill_id = draft.json()["id"]
            assert client.post(f"/api/v1/skills/{skill_id}/submit-review").status_code == 200
            published = client.post(f"/api/v1/skills/{skill_id}/publish")
            assert published.status_code == 200, published.text

            probe.online = False
            missing = client.post(
                "/api/v1/builds",
                json=_body(skill_id=skill_id, package_type="node"),
            )
            assert missing.status_code == 422
            assert missing.json()["detail"] == node_unavailable("Node", "linux")
            assert probe.calls == [("has_online_node", "linux")]
            assert git.calls == []
            _assert_no_build_rows(app.state.engine)

            probe.calls.clear()
            probe.probe_error = UpstreamFailure("down")
            failed = client.post(
                "/api/v1/builds",
                json=_body(skill_id=skill_id, package_type="node"),
            )
            assert failed.status_code == 422
            assert failed.json()["detail"] == node_probe_failed("Node", "linux")
            assert [call[0] for call in probe.calls] == ["has_online_node"]
            assert not any(call[0] == "create_product_job" for call in probe.calls)
            assert git.calls == []
            _assert_no_build_rows(app.state.engine)

            probe.calls.clear()
            probe.probe_error = None
            probe.online = True
            built = client.post(
                "/api/v1/builds",
                json=_body(skill_id=skill_id, package_type="node"),
            )
            assert built.status_code == 201, built.text
            assert built.json()["package_type"] == "node"
            assert built.json()["package_type_label"] == "Node"
            assert built.json()["deploys_to_runtime"] is False
            assert probe.assigned_node == "linux"
            assert any(call[0] == "create_product_job" for call in probe.calls)

            cleared = client.put(
                "/api/v1/build-package-types/node",
                json=_node_body(jenkins_label=None, include_code=False),
            )
            assert cleared.status_code == 200, cleared.text
            disabled = client.put(
                "/api/v1/build-package-types/node",
                json=_node_body(enabled=False, jenkins_label=None, include_code=False),
            )
            assert disabled.status_code == 200, disabled.text
            blocked = client.post(
                "/api/v1/skills",
                json={"name": f"new-node-{uuid4().hex[:6]}", "build_package_type": "node"},
            )
            assert blocked.status_code == 422
            assert blocked.json()["detail"] == TYPE_DISABLED_DRAFT
            probe.calls.clear()
            again = client.post(
                "/api/v1/builds",
                json=_body(
                    skill_id=skill_id,
                    package_type="node",
                    artifact_name="node-pkg",
                ),
            )
            assert again.status_code == 201, again.text
            assert not any(call[0] == "has_online_node" for call in probe.calls)
        finally:
            _reset_extra_types(app.state.engine)


class _ProbeJenkins(_RecordingJenkins):
    def __init__(self) -> None:
        super().__init__()
        self.online = True
        self.probe_error: Exception | None = None
        self.assigned_node: str | None = None

    def has_online_node(self, label: str) -> bool:
        self.calls.append(("has_online_node", label))
        if self.probe_error is not None:
            raise self.probe_error
        return self.online

    def create_product_job(self, job_name: str, **kwargs: object) -> None:
        node = kwargs.get("assigned_node")
        self.assigned_node = node if isinstance(node, str) else None
        super().create_product_job(job_name, **kwargs)


def _node_body(
    *,
    enabled: bool = True,
    jenkins_label: str | None = "linux",
    include_code: bool = True,
) -> dict[str, object]:
    body: dict[str, object] = {
        "label": "Node",
        "description": "",
        "enabled": enabled,
        "jenkins_label": jenkins_label,
        "runtime_conditions": [{"tool": "node", "version": "20"}],
    }
    if include_code:
        body["code"] = "node"
    return body


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
    logged = session.post(
        "/api/v1/auth/login",
        json={"username": username, "password": LOCAL_PASSWORD},
    )
    assert logged.status_code == 200, logged.text
    return session


def _assert_no_build_rows(engine: Engine) -> None:
    with engine.connect() as connection:
        builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
        artifacts = connection.execute(text("SELECT count(*) FROM artifacts")).scalar_one()
    assert builds == 0
    assert artifacts == 0


def _reset_extra_types(engine: Engine) -> None:
    seed = "('maven', 'python', 'generic', 'frontend')"
    with engine.begin() as connection:
        connection.execute(text(f"DELETE FROM product_builds WHERE package_type NOT IN {seed}"))
        connection.execute(text(f"DELETE FROM artifacts WHERE package_type NOT IN {seed}"))
        connection.execute(text(f"DELETE FROM skills WHERE build_package_type NOT IN {seed}"))
        connection.execute(
            text(
                "DELETE FROM build_package_type_conditions "
                f"WHERE package_type_code NOT IN {seed}"
            )
        )
        connection.execute(text(f"DELETE FROM build_package_types WHERE code NOT IN {seed}"))


def _absent(client: TestClient) -> bool:
    body = client.get("/api/v1/build-package-types", params={"include_disabled": "true"})
    return "node" not in [item["code"] for item in body.json()["items"]]


def _assert_probe_shape(case: str, expected: bool | None, label: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if case == "down":
            return httpx.Response(503, text="no")
        if case == "bad":
            return httpx.Response(200, json=["nope"])
        if case == "offline":
            return httpx.Response(
                200,
                json={
                    "computer": [
                        {
                            "displayName": "agent-1",
                            "offline": True,
                            "assignedLabels": [{"name": "linux"}],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "computer": [
                    {
                        "displayName": "Built-In Node",
                        "offline": False,
                        "assignedLabels": [],
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        client = JenkinsClient("http://127.0.0.1:9", "kiln", "jenkins-token", http)
        if expected is None:
            with pytest.raises(UpstreamFailure):
                client.has_online_node(label)
            return
        assert client.has_online_node(label) is expected


def _assert_probe_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as http:
        client = JenkinsClient("http://127.0.0.1:9", "kiln", "jenkins-token", http)
        with pytest.raises(UpstreamFailure):
            client.has_online_node("linux")


def _assert_downgrade_keeps_new_codes(cfg, engine: Engine) -> None:
    artifact_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO build_package_types (
                    code, label, description, enabled, jenkins_label,
                    position, created_at, updated_at
                ) VALUES ('node', 'Node', '', true, NULL, 9, now(), now())
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO artifacts (id, name, group_name, description, package_type, created_at)
                VALUES (:id, :name, NULL, '', 'node', now())
                """
            ),
            {"id": artifact_id, "name": f"node-{uuid4().hex[:8]}"},
        )
    with pytest.raises(Exception) as caught:
        command.downgrade(cfg, "0012_account_lifecycle")
    message = str(caught.value)
    assert "node" in message
    assert "降级" in message
    assert "不会删除" in message
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        still = connection.execute(
            text("SELECT count(*) FROM artifacts WHERE id = :id"),
            {"id": artifact_id},
        ).scalar_one()
    assert version == "0013_build_package_types"
    assert still == 1
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM artifacts WHERE id = :id"), {"id": artifact_id})
        connection.execute(text("DELETE FROM build_package_types WHERE code = 'node'"))


def _assert_empty_database_round_trip() -> None:
    name = f"kiln_btypes_{uuid4().hex[:8]}"
    admin = create_db_engine(postgres_url()).execution_options(isolation_level="AUTOCOMMIT")
    url = make_url(postgres_url()).set(database=name).render_as_string(hide_password=False)
    empty: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        upgrade_database(url)
        empty = create_db_engine(url)
        with empty.connect() as connection:
            codes = connection.execute(
                text("SELECT code FROM build_package_types ORDER BY position")
            ).scalars().all()
            nullable = connection.execute(
                text(
                    """
                    SELECT is_nullable FROM information_schema.columns
                    WHERE table_name = 'skills' AND column_name = 'build_package_type'
                    """
                )
            ).scalar_one()
        assert list(codes) == list(SEED_CODES)
        assert nullable == "NO"
        command.downgrade(_alembic_config(url), "0012_account_lifecycle")
        with empty.connect() as connection:
            catalog = connection.execute(
                text("SELECT to_regclass('public.build_package_types')")
            ).scalar_one()
            assert catalog is None
        command.upgrade(_alembic_config(url), "head")
        with empty.connect() as connection:
            again = connection.execute(
                text("SELECT count(*) FROM build_package_types")
            ).scalar_one()
        assert again == 4
    finally:
        if empty is not None:
            empty.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
