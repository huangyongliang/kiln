"""Domain records are stored in PostgreSQL and survive an API process restart."""

import hashlib
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from kiln.config import LOCAL_API_TOKEN
from kiln.db.engine import create_db_engine
from kiln.db.migrate import upgrade_database
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
    } <= names
    columns = {column["name"] for column in inspect(engine).get_columns("artifact_versions")}
    assert {"content_available", "blob_key"} <= columns
    with engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_artifacts_group_name'"
            )
        ).scalar_one()
    assert "NULLS NOT DISTINCT" in definition


def test_rbac_catalog_is_read_from_postgres(client: TestClient) -> None:
    engine = client.app.state.engine
    with engine.begin() as connection:
        updated = connection.execute(
            text(
                "UPDATE rbac_permissions SET description = '来自数据库' "
                "WHERE code = 'account.read'"
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
                    "source": "build",
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
            connection.execute(text("SELECT count(*) FROM download_reservations")).scalar_one()
            == 1
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
    env["KILN_API_TOKEN"] = LOCAL_API_TOKEN
    env["JENKINS_URL"] = ""
    env["JENKINS_USER"] = ""
    env["JENKINS_API_TOKEN"] = ""
    env["GIT_PROVIDER"] = "off"
    env["GIT_BASE_URL"] = ""
    env["GIT_TOKEN"] = ""
    env["KILN_ARTIFACT_ROOT"] = artifact_root
    env["KILN_ARTIFACT_MAX_BYTES"] = str(64 * 1024 * 1024)
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
