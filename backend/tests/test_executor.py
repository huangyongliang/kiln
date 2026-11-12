"""Jenkins compat executor.

Unauthenticated and unconfigured cases always run. Creating a job, tracking it,
and reading its log require a real Jenkins.
"""

import time
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from kiln.domain.jobs import JobCreate
from kiln.main import create_app
from tests.external import ExternalTargets, external_settings
from tests.support import auth_headers, truncate_domain


def test_job_marker_rejects_shell_metacharacters() -> None:
    with pytest.raises(ValidationError):
        JobCreate(marker="a;id")
    JobCreate(marker="kiln-ok")


def test_unconfigured_executor_does_not_deploy_and_refuses_jobs(client: TestClient) -> None:
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    document = spec.json()
    for path in ("/api/v1/executor", "/api/v1/jobs", "/api/v1/jobs/{job_id}/log"):
        assert path in document["paths"]
        for operation in document["paths"][path].values():
            if isinstance(operation, dict) and "responses" in operation:
                assert operation.get("security"), path
    assert "security" not in document["paths"]["/health"]["get"]

    info = client.get("/api/v1/executor")
    assert info.status_code == 200
    body = info.json()
    assert body == {
        "kind": "off",
        "configured": False,
        "base_url": None,
        "deploys_to_runtime": False,
    }
    created = client.post("/api/v1/jobs", json={"marker": "not-configured"})
    assert created.status_code == 503
    assert "JENKINS_URL" in created.json()["detail"]


def test_kiln_creates_tracks_and_reads_a_real_jenkins_job(
    external_targets: ExternalTargets,
) -> None:
    marker = f"kiln-ok-{uuid4().hex[:8]}"
    settings = external_settings(external_targets)
    app = create_app(settings)
    job_name = ""
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        try:
            info = client.get("/api/v1/executor")
            assert info.status_code == 200
            assert info.json()["kind"] == "jenkins"
            assert info.json()["configured"] is True
            assert info.json()["deploys_to_runtime"] is False
            assert info.json()["base_url"] == app.state.settings.jenkins_url

            created = client.post("/api/v1/jobs", json={"name": "compat", "marker": marker})
            assert created.status_code == 201, created.text
            job = created.json()
            assert job["deploys_to_runtime"] is False
            assert job["status"] == "queued"
            assert job["marker"] == marker
            job_name = job["jenkins_job_name"]
            assert job_name.startswith("kiln-")

            finished = _wait_until_finished(client, job["id"])
            if finished["status"] != "success":
                log = client.get(f"/api/v1/jobs/{job['id']}/log")
                raise AssertionError(f"{finished}\n{log.text}")
            assert finished["build_number"] >= 1
            assert finished["deploys_to_runtime"] is False

            log = client.get(f"/api/v1/jobs/{job['id']}/log")
            assert log.status_code == 200, log.text
            assert marker in log.json()["text"]
            assert log.json()["build_number"] == finished["build_number"]

            _assert_jenkins_has_the_build(
                external_targets,
                job_name,
                finished["build_number"],
                marker,
            )
            _assert_row_survives_restart(settings, job["id"], marker, finished["build_number"])
            _assert_anonymous_cannot_read_or_create(settings, external_targets, job["id"])
        finally:
            if job_name:
                jenkins = app.state.jenkins
                if jenkins is not None:
                    try:
                        jenkins.delete_job(job_name)
                    except Exception:
                        pass


def _wait_until_finished(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 180
    last: dict | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"success", "failure", "unstable", "aborted", "not_built"}:
            return last
        time.sleep(1)
    raise AssertionError(last)


def _assert_jenkins_has_the_build(
    targets: ExternalTargets,
    job_name: str,
    build_number: int,
    marker: str,
) -> None:
    encoded = quote(job_name, safe="")
    auth = (targets.jenkins_user, targets.jenkins_token)
    remote = httpx.get(
        f"{targets.jenkins_url}/job/{encoded}/lastBuild/api/json",
        auth=auth,
        timeout=30,
        trust_env=False,
    )
    assert remote.status_code == 200, remote.status_code
    body = remote.json()
    assert body["result"] == "SUCCESS"
    assert body["number"] == build_number
    console = httpx.get(
        f"{targets.jenkins_url}/job/{encoded}/{build_number}/consoleText",
        auth=auth,
        timeout=30,
        trust_env=False,
    )
    assert console.status_code == 200
    assert marker in console.text


def _assert_row_survives_restart(
    settings,
    job_id: str,
    marker: str,
    build_number: int,
) -> None:
    restarted = create_app(settings)
    with TestClient(restarted, headers=auth_headers()) as client:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success"
        assert body["marker"] == marker
        assert body["build_number"] == build_number
        with client.app.state.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, marker, build_number FROM executor_jobs WHERE id = :id"
                ),
                {"id": UUID(job_id)},
            ).one()
    assert row.status == "success"
    assert row.marker == marker
    assert row.build_number == build_number


def _assert_anonymous_cannot_read_or_create(
    settings,
    targets: ExternalTargets,
    job_id: str,
) -> None:
    before = _jenkins_names(targets)
    anon_app = create_app(settings)
    with TestClient(anon_app) as anon:
        assert anon.get("/api/v1/executor").status_code == 401
        assert anon.get("/api/v1/jobs").status_code == 401
        assert anon.get(f"/api/v1/jobs/{job_id}").status_code == 401
        denied = anon.get(f"/api/v1/jobs/{job_id}/log")
        assert denied.status_code == 401
        assert denied.json()["detail"] == "需要访问令牌"
        created = anon.post("/api/v1/jobs", json={"marker": "anonblocked"})
        assert created.status_code == 401
    assert _jenkins_names(targets) == before


def _jenkins_names(targets: ExternalTargets) -> set[str]:
    response = httpx.get(
        f"{targets.jenkins_url}/api/json",
        params={"tree": "jobs[name]"},
        auth=(targets.jenkins_user, targets.jenkins_token),
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 200, response.status_code
    return {item["name"] for item in response.json().get("jobs", [])}
