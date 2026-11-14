"""Domain records are stored in PostgreSQL and survive an API process restart."""

import hashlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO
from uuid import uuid4

import httpx
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, inspect, text
from sqlalchemy.engine import Engine, make_url

from kiln.config import LOCAL_API_TOKEN
from kiln.db import migrate as db_migrate
from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
from kiln.db.url import to_sqlalchemy_url
from kiln.domain.rbac import PERMISSIONS, ROLES
from tests.support import auth_headers, content_headers, postgres_url, truncate_domain

BACKEND = Path(__file__).resolve().parents[1]
SHA256_A = "ab" * 32


def test_schema_is_postgresql(client: TestClient) -> None:
    engine = client.app.state.engine
    assert engine.dialect.name == "postgresql"
    names = set(inspect(engine).get_table_names())
    assert {
        "accounts",
        "account_roles",
        "rbac_permissions",
        "rbac_roles",
        "rbac_role_permissions",
        "artifacts",
        "artifact_versions",
        "download_reservations",
        "skills",
        "skill_inputs",
        "skill_steps",
        "skill_allowed_repositories",
        "skill_allowed_dependencies",
        "executor_jobs",
        "product_builds",
        "sessions",
    } <= names
    account_columns = {column["name"] for column in inspect(engine).get_columns("accounts")}
    assert {
        "password_hash",
        "idp_issuer",
        "idp_subject",
        "employment_status",
    } <= account_columns
    columns = {column["name"] for column in inspect(engine).get_columns("artifact_versions")}
    assert {"content_available", "blob_key"} <= columns
    with engine.connect() as connection:
        definition = connection.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_artifacts_group_name'")
        ).scalar_one()
    assert "NULLS NOT DISTINCT" in definition


def test_rbac_catalog_is_read_from_postgres(client: TestClient) -> None:
    engine = client.app.state.engine
    with engine.begin() as connection:
        updated = connection.execute(
            text(
                "UPDATE rbac_permissions SET description = '来自数据库' WHERE code = 'account.read'"
            )
        )
        assert updated.rowcount == 1
    listed = client.get("/api/v1/rbac/permissions")
    assert listed.status_code == 200
    item = next(row for row in listed.json()["items"] if row["code"] == "account.read")
    assert item["description"] == "来自数据库"


def test_records_survive_api_process_restart() -> None:
    database_url = postgres_url()
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    truncate_domain(engine)
    artifact_root = tempfile.mkdtemp(prefix="kiln-meta-artifacts-")

    first, first_log, first_port = _start_api(database_url, artifact_root)
    try:
        base = _wait_healthy(first, first_log, first_port)
        with httpx.Client(timeout=5, trust_env=False, headers=auth_headers()) as http:
            account = _post(
                http,
                f"{base}/api/v1/accounts",
                {
                    "username": "restart_user",
                    "display_name": "重启",
                    "role_ids": ["publisher", "reader"],
                },
            )
            artifact = _post(
                http,
                f"{base}/api/v1/artifacts",
                {
                    "name": "restart-lib",
                    "group": "com.kiln",
                    "description": "重启后仍在",
                    "package_type": "maven",
                },
            )
            _post(
                http,
                f"{base}/api/v1/artifacts/{artifact['id']}/versions",
                {
                    "version": "1.0.0",
                    "checksum": {"algorithm": "sha256", "value": SHA256_A},
                    "size_bytes": 32,
                },
            )
            reservation = _post(
                http,
                f"{base}/api/v1/artifacts/{artifact['id']}/versions/1.0.0/download-reservations",
                {"requester": "restart_user", "ttl_seconds": 900},
            )
            skill = _post(
                http,
                f"{base}/api/v1/skills",
                {
                    "name": "重启技能",
                    "version": "1.0.0",
                    "steps": [{"kind": "compile", "description": "编译"}],
                },
            )
    finally:
        _stop(first)
        first_log.close()

    assert first.poll() is not None

    with engine.connect() as connection:
        stored_account = connection.execute(
            text("SELECT id::text, username, display_name FROM accounts")
        ).one()
        assert stored_account.username == "restart_user"
        assert stored_account.id == account["id"]
        assert stored_account.display_name == "重启"
        assert connection.execute(text("SELECT count(*) FROM rbac_roles")).scalar_one() == len(
            ROLES
        )
        assert connection.execute(
            text("SELECT count(*) FROM rbac_permissions")
        ).scalar_one() == len(PERMISSIONS)
        assert (
            connection.execute(text("SELECT version FROM artifact_versions")).scalar_one()
            == "1.0.0"
        )
        assert (
            connection.execute(text("SELECT count(*) FROM download_reservations")).scalar_one() == 1
        )
        stored_skill = connection.execute(text("SELECT name, status FROM skills")).one()
        assert stored_skill.name == "重启技能"
        assert stored_skill.status == "draft"
        assert connection.execute(text("SELECT kind FROM skill_steps")).scalar_one() == "compile"
    engine.dispose()

    second, second_log, second_port = _start_api(database_url, artifact_root)
    try:
        assert second.pid != first.pid
        base = _wait_healthy(second, second_log, second_port)
        denied = httpx.get(f"{base}/api/v1/accounts", timeout=5, trust_env=False)
        assert denied.status_code == 401
        with httpx.Client(timeout=5, trust_env=False, headers=auth_headers()) as http:
            accounts = http.get(f"{base}/api/v1/accounts")
            assert accounts.status_code == 200
            body = accounts.json()
            assert body["persistence"] == "postgres"
            assert body["items"][0]["id"] == account["id"]
            assert body["items"][0]["username"] == "restart_user"
            assert body["items"][0]["role_ids"] == ["publisher", "reader"]

            artifacts = http.get(f"{base}/api/v1/artifacts")
            assert artifacts.status_code == 200
            assert artifacts.json()["persistence"] == "postgres"
            restored = artifacts.json()["items"][0]
            assert restored["id"] == artifact["id"]
            assert restored["versions"][0]["version"] == "1.0.0"
            assert restored["versions"][0]["checksum"]["value"] == SHA256_A
            assert restored["versions"][0]["source"] == "upload"

            retrieved = http.get(f"{base}{reservation['retrieve_path']}")
            assert retrieved.status_code == 200
            retrieved_body = retrieved.json()
            assert retrieved_body["reservation_id"] == reservation["id"]
            assert retrieved_body["content_available"] is False
            assert retrieved_body["content_path"] is None
            assert retrieved_body["name"] == "restart-lib"
            missing_bytes = http.get(f"{base}{reservation['retrieve_path']}/content")
            assert missing_bytes.status_code == 409

            skills = http.get(f"{base}/api/v1/skills")
            assert skills.status_code == 200
            assert skills.json()["persistence"] == "postgres"
            assert skills.json()["items"][0]["id"] == skill["id"]
            assert skills.json()["items"][0]["status"] == "draft"
            assert skills.json()["items"][0]["steps"] == [
                {"kind": "compile", "description": "编译"}
            ]

            roles = http.get(f"{base}/api/v1/rbac/roles")
            assert roles.status_code == 200
            assert {item["id"] for item in roles.json()["items"]} == {
                "reader",
                "builder",
                "publisher",
                "admin",
            }
    finally:
        _stop(second)
        second_log.close()


def test_artifact_bytes_survive_api_process_restart() -> None:
    database_url = postgres_url()
    upgrade_database(database_url)
    engine = create_db_engine(database_url)
    truncate_domain(engine)
    engine.dispose()

    payload = b"restart-bytes\x00\xff"
    digest = hashlib.sha256(payload).hexdigest()
    artifact_root = tempfile.mkdtemp(prefix="kiln-byte-artifacts-")
    blob = Path(artifact_root) / "sha256" / digest

    first, first_log, first_port = _start_api(database_url, artifact_root)
    try:
        base = _wait_healthy(first, first_log, first_port)
        with httpx.Client(timeout=5, trust_env=False, headers=auth_headers()) as http:
            artifact = _post(
                http,
                f"{base}/api/v1/artifacts",
                {"name": "byte-restart", "package_type": "generic"},
            )
            stored = http.put(
                f"{base}/api/v1/artifacts/{artifact['id']}/versions/1.0.0/content",
                content=payload,
                headers=content_headers(payload),
            )
            assert stored.status_code == 201, stored.text
            assert stored.json()["content_available"] is True
            reservation = _post(
                http,
                f"{base}/api/v1/artifacts/{artifact['id']}/versions/1.0.0/download-reservations",
                {"requester": "restart_user"},
            )
            content_path = f"{reservation['retrieve_path']}/content"
            assert http.get(f"{base}{content_path}").content == payload
    finally:
        _stop(first)
        first_log.close()

    assert first.poll() is not None
    assert blob.is_file()
    assert blob.read_bytes() == payload

    second, second_log, second_port = _start_api(database_url, artifact_root)
    try:
        assert second.pid != first.pid
        base = _wait_healthy(second, second_log, second_port)
        denied = httpx.get(f"{base}{content_path}", timeout=5, trust_env=False)
        assert denied.status_code == 401
        assert payload not in denied.content
        with httpx.Client(timeout=5, trust_env=False, headers=auth_headers()) as http:
            artifacts = http.get(f"{base}/api/v1/artifacts")
            restored = artifacts.json()["items"][0]["versions"][0]
            assert restored["content_available"] is True
            assert restored["checksum"]["value"] == digest
            meta = http.get(f"{base}{reservation['retrieve_path']}")
            assert meta.status_code == 200
            assert meta.json()["content_available"] is True
            downloaded = http.get(f"{base}{content_path}")
            assert downloaded.status_code == 200
            assert downloaded.content == payload
            replacement = b"should-not-land"
            planted = httpx.put(
                f"{base}/api/v1/artifacts/{artifact['id']}/versions/9/content",
                content=replacement,
                headers=content_headers(replacement),
                timeout=5,
                trust_env=False,
            )
            assert planted.status_code == 401
        assert blob.read_bytes() == payload
        rejected = Path(artifact_root) / "sha256" / hashlib.sha256(replacement).hexdigest()
        assert not rejected.exists()
    finally:
        _stop(second)
        second_log.close()


def test_product_build_skill_migration_round_trip(client: TestClient) -> None:
    """skill_id appears at head and is gone at 0006_skill_rejection."""

    cfg = _alembic_config(postgres_url())
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0011_actor_indexes"]
    engine = client.app.state.engine
    try:
        command.downgrade(cfg, "0006_skill_rejection")
        names = {column["name"] for column in inspect(engine).get_columns("product_builds")}
        assert "skill_id" not in names
        command.upgrade(cfg, "head")
        columns = {
            column["name"]: column for column in inspect(engine).get_columns("product_builds")
        }
        assert columns["skill_id"]["nullable"] is True
        with engine.connect() as connection:
            constraint = connection.execute(
                text(
                    "SELECT confrelid::regclass::text FROM pg_constraint "
                    "WHERE conname = 'fk_product_builds_skill_id_skills'"
                )
            ).scalar_one()
        assert constraint == "skills"
    finally:
        command.upgrade(cfg, "head")


def test_skill_rejection_migration_round_trip(client: TestClient) -> None:
    """Drop the rejection columns at 0005_local_login, then restore them at head."""

    cfg = _alembic_config(postgres_url())
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0011_actor_indexes"]
    engine = client.app.state.engine
    try:
        command.downgrade(cfg, "0005_local_login")
        assert _rejection_schema(engine) == (set(), set())
        command.upgrade(cfg, "head")
        _assert_rejection_schema(engine)
    finally:
        command.upgrade(cfg, "head")


def test_skill_created_by_migration_round_trip(client: TestClient) -> None:
    """created_by appears at head and is gone at 0007_product_build_skill."""

    cfg = _alembic_config(postgres_url())
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0011_actor_indexes"]
    engine = client.app.state.engine
    try:
        _assert_created_by_schema(engine)
        command.downgrade(cfg, "0007_product_build_skill")
        with engine.connect() as connection:
            present = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'skills'
                      AND column_name = 'created_by'
                    """
                )
            ).scalar_one()
        assert present == 0
        command.upgrade(cfg, "head")
        _assert_created_by_schema(engine)
    finally:
        command.upgrade(cfg, "head")


def test_llm_catalog_migration_round_trip(client: TestClient) -> None:
    """Downgrade drops a saved catalog. Upgrade again is back to env behavior."""

    cfg = _alembic_config(postgres_url())
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0011_actor_indexes"]
    engine = client.app.state.engine
    try:
        _assert_llm_catalog_tables(engine, present=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO llm_catalog_state (id, source) VALUES (1, 'db') "
                    "ON CONFLICT (id) DO UPDATE SET source = 'db'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO llm_catalog_models "
                    "(model_id, display_name, position, is_default) "
                    "VALUES ('model-a', '甲', 0, true)"
                )
            )
        command.downgrade(cfg, "0008_skill_created_by")
        _assert_llm_catalog_tables(engine, present=False)
        command.upgrade(cfg, "head")
        _assert_llm_catalog_tables(engine, present=True)
        with engine.connect() as connection:
            source = connection.execute(
                text("SELECT source FROM llm_catalog_state WHERE id = 1")
            ).scalar_one()
            saved = connection.execute(
                text("SELECT count(*) FROM llm_catalog_models")
            ).scalar_one()
        assert source == "env"
        assert saved == 0
    finally:
        command.upgrade(cfg, "head")


def test_actor_attribution_migration_on_empty_and_existing_databases(client: TestClient) -> None:
    """Add nullable actor columns at head. Existing rows stay null. Delete sets null."""

    cfg = _alembic_config(postgres_url())
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0011_actor_indexes"]
    revision = script.get_revision("0010_actor_attribution")
    assert revision is not None
    assert revision.down_revision == "0009_llm_catalog"
    _assert_actor_columns_on_empty_database()

    engine = client.app.state.engine
    try:
        command.downgrade(cfg, "0009_llm_catalog")
        _assert_actor_columns(engine, present=False)
        planted = _insert_pre_actor_rows(engine)
        command.upgrade(cfg, "head")
        _assert_actor_columns(engine, present=True)
        _assert_pre_actor_rows_unchanged(engine, planted)
        _point_actor_columns_at_account(engine, planted)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM accounts WHERE id = :id"),
                {"id": planted["account_id"]},
            )
        _assert_account_delete_nulls_actor_columns(engine, planted)
    finally:
        command.upgrade(cfg, "head")
        truncate_domain(engine)


_ACTOR_INDEXES = (
    "ix_artifact_versions_created_by",
    "ix_download_reservations_account_id",
    "ix_product_builds_requested_by",
    "ix_executor_jobs_requested_by",
    "ix_product_builds_artifact_id",
)


def test_actor_index_migration_on_empty_and_populated_databases(client: TestClient) -> None:
    """0011 adds five plain indexes. Downgrade removes them and leaves rows alone."""

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from kiln.db.models import Base

    cfg = _alembic_config(postgres_url())
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["0011_actor_indexes"]
    revision = script.get_revision("0011_actor_indexes")
    assert revision is not None
    assert revision.down_revision == "0010_actor_attribution"
    versions = Path(db_migrate.__file__).resolve().parent / "migrations" / "versions"
    assert (versions / "0011_actor_indexes.py").is_file()
    _assert_actor_indexes_on_empty_database()

    engine = client.app.state.engine
    planted: dict[str, str] | None = None
    try:
        command.downgrade(cfg, "0010_actor_attribution")
        assert _actor_index_names(engine) == set()
        planted = _insert_indexed_actor_rows(engine)
        before = _indexed_actor_snapshot(engine, planted)
        command.upgrade(cfg, "head")
        _assert_actor_indexes(engine, present=True)
        assert _indexed_actor_snapshot(engine, planted) == before
        command.downgrade(cfg, "0010_actor_attribution")
        assert _actor_index_names(engine) == set()
        assert _indexed_actor_snapshot(engine, planted) == before
        command.upgrade(cfg, "head")
        _assert_actor_indexes(engine, present=True)
        assert _indexed_actor_snapshot(engine, planted) == before
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
        assert diff == []
    finally:
        command.upgrade(cfg, "head")
        truncate_domain(engine)


def _actor_index_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        found = connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN :names
                """
            ).bindparams(bindparam("names", expanding=True)),
            {"names": list(_ACTOR_INDEXES)},
        ).scalars()
        return set(found)


def _assert_actor_indexes(engine: Engine, *, present: bool) -> None:
    found = _actor_index_names(engine)
    if present:
        assert found == set(_ACTOR_INDEXES)
        with engine.connect() as connection:
            for name in _ACTOR_INDEXES:
                definition = connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                    {"name": name},
                ).scalar_one()
                assert "USING btree" in definition
                assert "UNIQUE" not in definition
    else:
        assert found == set()


def _assert_actor_indexes_on_empty_database() -> None:
    name = f"kiln_s1b_{uuid4().hex[:12]}"
    assert re.fullmatch(r"kiln_s1b_[0-9a-f]{12}", name)
    admin = create_db_engine(postgres_url()).execution_options(isolation_level="AUTOCOMMIT")
    url = make_url(postgres_url()).set(database=name).render_as_string(hide_password=False)
    empty: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        upgrade_database(url)
        empty = create_db_engine(url)
        _assert_actor_indexes(empty, present=True)
        with empty.connect() as connection:
            builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
            versions = connection.execute(
                text("SELECT count(*) FROM artifact_versions")
            ).scalar_one()
        assert builds == 0
        assert versions == 0
        command.downgrade(_alembic_config(url), "0010_actor_attribution")
        _assert_actor_indexes(empty, present=False)
        command.upgrade(_alembic_config(url), "head")
        _assert_actor_indexes(empty, present=True)
        assert ScriptDirectory.from_config(_alembic_config(url)).get_heads() == [
            "0011_actor_indexes"
        ]
    finally:
        if empty is not None:
            empty.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _insert_indexed_actor_rows(engine: Engine) -> dict[str, str]:
    planted = {
        "account_id": str(uuid4()),
        "artifact_id": str(uuid4()),
        "build_id": str(uuid4()),
        "job_id": str(uuid4()),
        "reservation_id": str(uuid4()),
        "username": f"idx_{uuid4().hex[:8]}",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO accounts (id, username, display_name, status, created_at)
                VALUES (:id, :username, '索引', 'active', TIMESTAMPTZ '2026-01-01T00:00:00Z')
                """
            ),
            {"id": planted["account_id"], "username": planted["username"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                    (id, name, group_name, description, package_type, created_at)
                VALUES
                    (:id, :name, NULL, '索引制品', 'generic',
                     TIMESTAMPTZ '2026-01-01T00:00:00Z')
                """
            ),
            {"id": planted["artifact_id"], "name": planted["username"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO artifact_versions (
                    artifact_id, version, checksum_algorithm, checksum_value,
                    size_bytes, source, content_available, blob_key, created_by, created_at
                ) VALUES (
                    :artifact_id, '1.0.0', 'sha256', :digest,
                    4, 'upload', false, NULL, :account_id,
                    TIMESTAMPTZ '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "artifact_id": planted["artifact_id"],
                "digest": SHA256_A,
                "account_id": planted["account_id"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO product_builds (
                    id, name, jenkins_job_name, repository, git_ref, commit_sha,
                    artifact_id, version, package_type, queue_url, build_number,
                    status, content_stored, requested_by, created_at, updated_at
                ) VALUES (
                    :id, 'idx-build', :job_name, 'ada/demo', 'main', :sha,
                    :artifact_id, '1.0.0', 'generic', NULL, NULL,
                    'queued', false, :account_id,
                    TIMESTAMPTZ '2026-01-01T00:00:00Z', TIMESTAMPTZ '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "id": planted["build_id"],
                "job_name": f"kiln-build-{planted['build_id'].replace('-', '')}",
                "sha": "b" * 40,
                "artifact_id": planted["artifact_id"],
                "account_id": planted["account_id"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO executor_jobs (
                    id, name, jenkins_job_name, marker, queue_url, build_number,
                    status, requested_by, created_at, updated_at
                ) VALUES (
                    :id, 'idx-job', :job_name, 'idx', NULL, NULL,
                    'queued', :account_id,
                    TIMESTAMPTZ '2026-01-01T00:00:00Z', TIMESTAMPTZ '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "id": planted["job_id"],
                "job_name": f"kiln-{planted['job_id'].replace('-', '')}",
                "account_id": planted["account_id"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO download_reservations (
                    id, artifact_id, artifact_name, version, package_type,
                    checksum_algorithm, checksum_value, requester, account_id,
                    created_at, expires_at
                ) VALUES (
                    :id, :artifact_id, :name, '1.0.0', 'generic',
                    'sha256', :digest, 'idx_user', :account_id,
                    TIMESTAMPTZ '2026-01-01T00:00:00Z', TIMESTAMPTZ '2026-01-02T00:00:00Z'
                )
                """
            ),
            {
                "id": planted["reservation_id"],
                "artifact_id": planted["artifact_id"],
                "name": planted["username"],
                "digest": SHA256_A,
                "account_id": planted["account_id"],
            },
        )
    return planted


def _indexed_actor_snapshot(engine: Engine, planted: dict[str, str]) -> tuple:
    with engine.connect() as connection:
        version = connection.execute(
            text(
                """
                SELECT version, source, content_available, size_bytes, created_by::text
                FROM artifact_versions
                WHERE artifact_id = :id
                """
            ),
            {"id": planted["artifact_id"]},
        ).one()
        build = connection.execute(
            text(
                """
                SELECT status, queue_url, build_number, requested_by::text, content_stored
                FROM product_builds
                WHERE id = :id
                """
            ),
            {"id": planted["build_id"]},
        ).one()
        job = connection.execute(
            text(
                """
                SELECT status, requested_by::text
                FROM executor_jobs
                WHERE id = :id
                """
            ),
            {"id": planted["job_id"]},
        ).one()
        reservation = connection.execute(
            text(
                """
                SELECT requester, account_id::text
                FROM download_reservations
                WHERE id = :id
                """
            ),
            {"id": planted["reservation_id"]},
        ).one()
    return (tuple(version), tuple(build), tuple(job), tuple(reservation))


_ACTOR_COLUMNS = (
    ("product_builds", "requested_by", "fk_product_builds_requested_by_accounts"),
    ("executor_jobs", "requested_by", "fk_executor_jobs_requested_by_accounts"),
    ("artifact_versions", "created_by", "fk_artifact_versions_created_by_accounts"),
    ("download_reservations", "account_id", "fk_download_reservations_account_id_accounts"),
)


def _assert_actor_columns(engine: Engine, *, present: bool) -> None:
    with engine.connect() as connection:
        for table, column, constraint in _ACTOR_COLUMNS:
            kind = connection.execute(
                text(
                    """
                    SELECT data_type || ' ' || is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :table
                      AND column_name = :column
                    """
                ),
                {"table": table, "column": column},
            ).scalar_one_or_none()
            rule = connection.execute(
                text(
                    """
                    SELECT delete_rule
                    FROM information_schema.referential_constraints
                    WHERE constraint_schema = 'public'
                      AND constraint_name = :name
                    """
                ),
                {"name": constraint},
            ).scalar_one_or_none()
            if present:
                assert kind == "uuid YES", (table, column, kind)
                assert rule == "SET NULL", (constraint, rule)
            else:
                assert kind is None, (table, column, kind)
                assert rule is None, (constraint, rule)


def _assert_actor_columns_on_empty_database() -> None:
    name = f"kiln_s1_{uuid4().hex[:12]}"
    assert re.fullmatch(r"kiln_s1_[0-9a-f]{12}", name)
    admin = create_db_engine(postgres_url()).execution_options(isolation_level="AUTOCOMMIT")
    url = make_url(postgres_url()).set(database=name).render_as_string(hide_password=False)
    empty: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        upgrade_database(url)
        empty = create_db_engine(url)
        _assert_actor_columns(empty, present=True)
        with empty.connect() as connection:
            builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
            versions = connection.execute(
                text("SELECT count(*) FROM artifact_versions")
            ).scalar_one()
        assert builds == 0
        assert versions == 0
        upgrade_database(url)
        assert ScriptDirectory.from_config(_alembic_config(url)).get_heads() == [
            "0011_actor_indexes"
        ]
    finally:
        if empty is not None:
            empty.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _insert_pre_actor_rows(engine: Engine) -> dict[str, str]:
    planted = {
        "account_id": str(uuid4()),
        "artifact_id": str(uuid4()),
        "build_id": str(uuid4()),
        "job_id": str(uuid4()),
        "reservation_id": str(uuid4()),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO accounts (id, username, display_name, status, created_at)
                VALUES (:id, 'actor_hist', '历史', 'active', TIMESTAMPTZ '2026-01-01T00:00:00Z')
                """
            ),
            {"id": planted["account_id"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                    (id, name, group_name, description, package_type, created_at)
                VALUES
                    (:id, 'hist-pkg', NULL, '历史制品', 'generic',
                     TIMESTAMPTZ '2026-01-01T00:00:00Z')
                """
            ),
            {"id": planted["artifact_id"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO artifact_versions (
                    artifact_id, version, checksum_algorithm, checksum_value,
                    size_bytes, source, content_available, blob_key, created_at
                ) VALUES (
                    :artifact_id, '1.0.0', 'sha256', :digest,
                    NULL, 'upload', false, NULL, TIMESTAMPTZ '2026-01-01T00:00:00Z'
                )
                """
            ),
            {"artifact_id": planted["artifact_id"], "digest": SHA256_A},
        )
        connection.execute(
            text(
                """
                INSERT INTO product_builds (
                    id, name, jenkins_job_name, repository, git_ref, commit_sha,
                    artifact_id, version, package_type, queue_url, build_number,
                    status, content_stored, created_at, updated_at
                ) VALUES (
                    :id, 'hist-build', :job_name, 'ada/demo', 'main', :sha,
                    :artifact_id, '1.0.0', 'generic', NULL, NULL,
                    'queued', false,
                    TIMESTAMPTZ '2026-01-01T00:00:00Z', TIMESTAMPTZ '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "id": planted["build_id"],
                "job_name": f"kiln-build-{planted['build_id'].replace('-', '')}",
                "sha": "a" * 40,
                "artifact_id": planted["artifact_id"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO executor_jobs (
                    id, name, jenkins_job_name, marker, queue_url, build_number,
                    status, created_at, updated_at
                ) VALUES (
                    :id, 'hist-job', :job_name, 'hist', NULL, NULL,
                    'queued',
                    TIMESTAMPTZ '2026-01-01T00:00:00Z', TIMESTAMPTZ '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "id": planted["job_id"],
                "job_name": f"kiln-{planted['job_id'].replace('-', '')}",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO download_reservations (
                    id, artifact_id, artifact_name, version, package_type,
                    checksum_algorithm, checksum_value, requester, created_at, expires_at
                ) VALUES (
                    :id, :artifact_id, 'hist-pkg', '1.0.0', 'generic',
                    'sha256', :digest, 'actor_hist',
                    TIMESTAMPTZ '2026-01-01T00:00:00Z', TIMESTAMPTZ '2026-01-02T00:00:00Z'
                )
                """
            ),
            {
                "id": planted["reservation_id"],
                "artifact_id": planted["artifact_id"],
                "digest": SHA256_A,
            },
        )
    return planted


def _assert_pre_actor_rows_unchanged(engine: Engine, planted: dict[str, str]) -> None:
    with engine.connect() as connection:
        artifact = connection.execute(
            text("SELECT name FROM artifacts WHERE id = :id"),
            {"id": planted["artifact_id"]},
        ).one()
        version = connection.execute(
            text(
                """
                SELECT source, created_by
                FROM artifact_versions
                WHERE artifact_id = :id AND version = '1.0.0'
                """
            ),
            {"id": planted["artifact_id"]},
        ).one()
        build = connection.execute(
            text(
                """
                SELECT repository, status, requested_by, content_stored
                FROM product_builds WHERE id = :id
                """
            ),
            {"id": planted["build_id"]},
        ).one()
        job = connection.execute(
            text("SELECT marker, requested_by FROM executor_jobs WHERE id = :id"),
            {"id": planted["job_id"]},
        ).one()
        reservation = connection.execute(
            text(
                """
                SELECT requester, account_id
                FROM download_reservations WHERE id = :id
                """
            ),
            {"id": planted["reservation_id"]},
        ).one()
    assert artifact.name == "hist-pkg"
    assert version.source == "upload"
    assert version.created_by is None
    assert build.repository == "ada/demo"
    assert build.status == "queued"
    assert build.requested_by is None
    assert build.content_stored is False
    assert job.marker == "hist"
    assert job.requested_by is None
    assert reservation.requester == "actor_hist"
    assert reservation.account_id is None


def _point_actor_columns_at_account(engine: Engine, planted: dict[str, str]) -> None:
    account_id = planted["account_id"]
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE product_builds SET requested_by = :account WHERE id = :id"),
            {"account": account_id, "id": planted["build_id"]},
        )
        connection.execute(
            text("UPDATE executor_jobs SET requested_by = :account WHERE id = :id"),
            {"account": account_id, "id": planted["job_id"]},
        )
        connection.execute(
            text(
                """
                UPDATE artifact_versions
                SET created_by = :account
                WHERE artifact_id = :id AND version = '1.0.0'
                """
            ),
            {"account": account_id, "id": planted["artifact_id"]},
        )
        connection.execute(
            text("UPDATE download_reservations SET account_id = :account WHERE id = :id"),
            {"account": account_id, "id": planted["reservation_id"]},
        )


def _assert_account_delete_nulls_actor_columns(engine: Engine, planted: dict[str, str]) -> None:
    with engine.connect() as connection:
        accounts = connection.execute(
            text("SELECT count(*) FROM accounts WHERE id = :id"),
            {"id": planted["account_id"]},
        ).scalar_one()
        artifacts = connection.execute(
            text("SELECT count(*) FROM artifacts WHERE id = :id"),
            {"id": planted["artifact_id"]},
        ).scalar_one()
        versions = connection.execute(
            text("SELECT count(*) FROM artifact_versions WHERE artifact_id = :id"),
            {"id": planted["artifact_id"]},
        ).scalar_one()
        created_by = connection.execute(
            text(
                """
                SELECT created_by FROM artifact_versions
                WHERE artifact_id = :id AND version = '1.0.0'
                """
            ),
            {"id": planted["artifact_id"]},
        ).scalar_one()
        builds = connection.execute(
            text("SELECT count(*) FROM product_builds WHERE id = :id"),
            {"id": planted["build_id"]},
        ).scalar_one()
        requested_by = connection.execute(
            text("SELECT requested_by FROM product_builds WHERE id = :id"),
            {"id": planted["build_id"]},
        ).scalar_one()
        jobs = connection.execute(
            text("SELECT count(*) FROM executor_jobs WHERE id = :id"),
            {"id": planted["job_id"]},
        ).scalar_one()
        job_actor = connection.execute(
            text("SELECT requested_by FROM executor_jobs WHERE id = :id"),
            {"id": planted["job_id"]},
        ).scalar_one()
        reservations = connection.execute(
            text("SELECT count(*) FROM download_reservations WHERE id = :id"),
            {"id": planted["reservation_id"]},
        ).scalar_one()
        reservation_actor = connection.execute(
            text("SELECT account_id FROM download_reservations WHERE id = :id"),
            {"id": planted["reservation_id"]},
        ).scalar_one()
    assert accounts == 0
    assert artifacts == 1
    assert versions == 1
    assert created_by is None
    assert builds == 1
    assert requested_by is None
    assert jobs == 1
    assert job_actor is None
    assert reservations == 1
    assert reservation_actor is None


def _assert_llm_catalog_tables(engine: Engine, *, present: bool) -> None:
    with engine.connect() as connection:
        found = connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'llm_catalog_state',
                    'llm_catalog_models',
                    'llm_role_models'
                  )
                """
            )
        ).scalars().all()
    expected = {"llm_catalog_state", "llm_catalog_models", "llm_role_models"}
    assert set(found) == (expected if present else set())


def _assert_created_by_schema(engine: Engine) -> None:
    with engine.connect() as connection:
        kind = connection.execute(
            text(
                """
                SELECT data_type || ' ' || is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'skills'
                  AND column_name = 'created_by'
                """
            )
        ).scalar_one()
        delete_rule = connection.execute(
            text(
                """
                SELECT delete_rule
                FROM information_schema.referential_constraints
                WHERE constraint_schema = 'public'
                  AND constraint_name = 'fk_skills_created_by_accounts'
                """
            )
        ).scalar_one()
    assert kind == "uuid YES"
    assert delete_rule == "SET NULL"


def _alembic_config(database_url: str) -> Config:
    script_location = str(Path(db_migrate.__file__).resolve().parent / "migrations")
    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    return cfg


def _rejection_schema(engine: Engine) -> tuple[set[str], set[str]]:
    with engine.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'skills'
                      AND column_name IN ('review_note', 'rejected_at', 'rejected_by')
                    """
                )
            ).scalars()
        )
        constraints = set(
            connection.execute(
                text(
                    """
                    SELECT constraint_name
                    FROM information_schema.table_constraints
                    WHERE table_schema = 'public'
                      AND table_name = 'skills'
                      AND constraint_name IN (
                          'fk_skills_rejected_by_accounts',
                          'ck_skills_rejection_pair'
                      )
                    """
                )
            ).scalars()
        )
    return columns, constraints


def _assert_rejection_schema(engine: Engine) -> None:
    columns, constraints = _rejection_schema(engine)
    assert columns == {"review_note", "rejected_at", "rejected_by"}
    assert constraints == {
        "fk_skills_rejected_by_accounts",
        "ck_skills_rejection_pair",
    }
    with engine.connect() as connection:
        kinds = dict(
            connection.execute(
                text(
                    """
                    SELECT column_name, data_type || ' ' || is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'skills'
                      AND column_name IN ('review_note', 'rejected_at', 'rejected_by')
                    """
                )
            ).all()
        )
        delete_rule = connection.execute(
            text(
                """
                SELECT delete_rule
                FROM information_schema.referential_constraints
                WHERE constraint_schema = 'public'
                  AND constraint_name = 'fk_skills_rejected_by_accounts'
                """
            )
        ).scalar_one()
    assert kinds == {
        "review_note": "text YES",
        "rejected_at": "timestamp with time zone YES",
        "rejected_by": "uuid YES",
    }
    assert delete_rule == "SET NULL"


def _post(http: httpx.Client, url: str, payload: dict) -> dict:
    response = http.post(url, json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_api(
    database_url: str,
    artifact_root: str,
) -> tuple[subprocess.Popen[bytes], IO[bytes], int]:
    log = tempfile.TemporaryFile()
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "kiln.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=BACKEND,
        env=_api_env(database_url, artifact_root),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log, port


def _api_env(database_url: str, artifact_root: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["LLM_PROVIDER"] = "off"
    env["LLM_BASE_URL"] = ""
    env["LLM_API_KEY"] = ""
    env["LLM_MODEL"] = ""
    env["LLM_MODELS"] = ""
    env["KILN_API_TOKEN"] = LOCAL_API_TOKEN
    env["JENKINS_URL"] = ""
    env["JENKINS_USER"] = ""
    env["JENKINS_API_TOKEN"] = ""
    env["GIT_PROVIDER"] = "off"
    env["GIT_BASE_URL"] = ""
    env["GIT_CLONE_BASE_URL"] = ""
    env["GIT_TOKEN"] = ""
    env["KILN_ARTIFACT_ROOT"] = artifact_root
    env["KILN_ARTIFACT_MAX_BYTES"] = str(64 * 1024 * 1024)
    env["KILN_OIDC_ISSUER_URL"] = ""
    env["KILN_OIDC_CLIENT_ID"] = ""
    env["KILN_OIDC_CLIENT_SECRET"] = ""
    env["KILN_OIDC_REDIRECT_URI"] = ""
    env["KILN_OIDC_EMPLOYMENT_CLAIM"] = ""
    env["KILN_OIDC_EMPLOYMENT_ACTIVE_VALUES"] = ""
    env["KILN_OIDC_SCOPES"] = ""
    env["KILN_SESSION_COOKIE_SECURE"] = "false"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _wait_healthy(proc: subprocess.Popen[bytes], log: IO[bytes], port: int) -> str:
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(_log_text(log))
        try:
            response = httpx.get(f"{base}/health", timeout=0.5, trust_env=False)
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if response.status_code == 200:
            return base
        time.sleep(0.1)
    raise AssertionError(_log_text(log))


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _log_text(log: IO[bytes]) -> str:
    log.seek(0)
    return log.read().decode("utf-8", errors="replace")
