"""Helpers for tests that talk to a real PostgreSQL database."""

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from kiln.config import LOCAL_API_TOKEN, Settings
from kiln.db.engine import create_db_engine

_DEFAULT_DATABASE_URL = "postgresql://kiln:kiln@127.0.0.1:5432/kiln"
_IDENT = re.compile(r"^[a-z_]+$")
_TEST_ARTIFACT_ROOT = Path(tempfile.mkdtemp(prefix="kiln-test-artifacts-"))

DOMAIN_TABLES = (
    "download_reservations",
    "product_builds",
    "artifact_versions",
    "artifacts",
    "sessions",
    "account_roles",
    "accounts",
    "skill_allowed_repositories",
    "skill_allowed_dependencies",
    "skill_steps",
    "skill_inputs",
    "skills",
    "executor_jobs",
)


def postgres_url() -> str:
    return os.environ.get("KILN_TEST_DATABASE_URL", _DEFAULT_DATABASE_URL)


def redact_database_url(database_url: str) -> str:
    return re.sub(r"://([^:@/]+):([^@/]+)@", r"://\1:***@", database_url)


def auth_headers(token: str = LOCAL_API_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def app_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": postgres_url(),
        "kiln_artifact_root": str(_TEST_ARTIFACT_ROOT),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def content_headers(
    payload: bytes,
    *,
    algorithm: str = "sha256",
    source: str = "upload",
) -> dict[str, str]:
    return {
        "Content-Type": "application/octet-stream",
        "X-Kiln-Checksum-Algorithm": algorithm,
        "X-Kiln-Checksum": hashlib.new(algorithm, payload).hexdigest(),
        "X-Kiln-Source": source,
    }


def blob_files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".partial-")
    )


def require_database() -> None:
    url = postgres_url()
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(f"PostgreSQL is required at {redact_database_url(url)}: {exc}") from exc
    finally:
        engine.dispose()


def publish_skill(
    client: Any,
    name: str,
    repositories: list[str] | None = None,
) -> dict[str, Any]:
    """Create, submit, and publish a skill. Returns the published JSON body."""

    payload: dict[str, Any] = {"name": name, "version": "0.1.0"}
    if repositories is not None:
        payload["allowed_repositories"] = list(repositories)
    created = client.post("/api/v1/skills", json=payload)
    assert created.status_code == 201, created.text
    skill_id = created.json()["id"]
    submitted = client.post(f"/api/v1/skills/{skill_id}/submit-review")
    assert submitted.status_code == 200, submitted.text
    published = client.post(f"/api/v1/skills/{skill_id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    return published.json()


def truncate_domain(engine: Engine) -> None:
    if any(_IDENT.fullmatch(name) is None for name in DOMAIN_TABLES):
        raise RuntimeError("refusing to truncate an unexpected table name")
    statement = "TRUNCATE TABLE " + ", ".join(DOMAIN_TABLES)
    with engine.begin() as connection:
        connection.execute(text(statement))
