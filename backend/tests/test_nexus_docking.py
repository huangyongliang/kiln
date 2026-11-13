"""Real Nexus docking.

Skipped when ``KILN_TEST_NEXUS_URL`` is unset. ``KILN_REQUIRE_NEXUS_TESTS=1``
fails if configuration is missing and does not honor ``KILN_SKIP_EXTERNAL_TESTS``.
The build downloads a fixture through the group. It does not publish anything.
"""

import base64
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from kiln.main import create_app
from tests.support import app_settings, auth_headers, publish_skill, truncate_domain

_REQUIRED = (
    "KILN_TEST_NEXUS_URL",
    "KILN_TEST_NEXUS_REPOSITORY",
    "KILN_TEST_NEXUS_USERNAME",
    "KILN_TEST_NEXUS_PASSWORD",
    "KILN_TEST_NEXUS_BUILD_URL",
    "KILN_TEST_NEXUS_ADMIN_USER",
    "KILN_TEST_NEXUS_ADMIN_PASSWORD",
    "KILN_TEST_NEXUS_HOSTED_REPOSITORY",
    "KILN_TEST_NEXUS_FIXTURE_PATH",
    "KILN_TEST_JENKINS_URL",
    "KILN_TEST_JENKINS_USER",
    "KILN_TEST_JENKINS_API_TOKEN",
    "KILN_TEST_GIT_BASE_URL",
    "KILN_TEST_GIT_TOKEN",
    "KILN_TEST_GIT_CLONE_BASE_URL",
)
_FIXTURE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")


@dataclass(frozen=True)
class DockingTargets:
    nexus_url: str
    nexus_build_url: str
    repository: str
    username: str
    password: str
    admin_user: str
    admin_password: str
    hosted_repository: str
    fixture_path: str
    jenkins_url: str
    jenkins_user: str
    jenkins_token: str
    git_base_url: str
    git_token: str
    git_clone_base_url: str


def load_docking_targets() -> DockingTargets:
    required = os.environ.get("KILN_REQUIRE_NEXUS_TESTS") == "1"
    url = os.environ.get("KILN_TEST_NEXUS_URL", "").strip()
    if not url and not required:
        pytest.skip("KILN_TEST_NEXUS_URL is unset")
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        pytest.fail(
            "Nexus docking tests require "
            + ", ".join(missing)
            + ". KILN_SKIP_EXTERNAL_TESTS does not skip them."
        )
    provider = os.environ.get("KILN_TEST_GIT_PROVIDER", "gitea").strip().lower()
    if provider != "gitea":
        pytest.fail("KILN_TEST_GIT_PROVIDER must be gitea for the lab adapter tests")
    fixture_path = os.environ["KILN_TEST_NEXUS_FIXTURE_PATH"].strip()
    if _FIXTURE_PATH.fullmatch(fixture_path) is None or ".." in fixture_path.split("/"):
        pytest.fail("KILN_TEST_NEXUS_FIXTURE_PATH is not a safe relative path")
    targets = DockingTargets(
        nexus_url=os.environ["KILN_TEST_NEXUS_URL"].strip().rstrip("/"),
        nexus_build_url=os.environ["KILN_TEST_NEXUS_BUILD_URL"].strip().rstrip("/"),
        repository=os.environ["KILN_TEST_NEXUS_REPOSITORY"].strip(),
        username=os.environ["KILN_TEST_NEXUS_USERNAME"].strip(),
        password=os.environ["KILN_TEST_NEXUS_PASSWORD"].strip(),
        admin_user=os.environ["KILN_TEST_NEXUS_ADMIN_USER"].strip(),
        admin_password=os.environ["KILN_TEST_NEXUS_ADMIN_PASSWORD"].strip(),
        hosted_repository=os.environ["KILN_TEST_NEXUS_HOSTED_REPOSITORY"].strip(),
        fixture_path=fixture_path,
        jenkins_url=os.environ["KILN_TEST_JENKINS_URL"].strip().rstrip("/"),
        jenkins_user=os.environ["KILN_TEST_JENKINS_USER"].strip(),
        jenkins_token=os.environ["KILN_TEST_JENKINS_API_TOKEN"].strip(),
        git_base_url=os.environ["KILN_TEST_GIT_BASE_URL"].strip().rstrip("/"),
        git_token=os.environ["KILN_TEST_GIT_TOKEN"].strip(),
        git_clone_base_url=os.environ["KILN_TEST_GIT_CLONE_BASE_URL"].strip().rstrip("/"),
    )
    _require_ready(targets)
    return targets


@pytest.fixture(scope="module")
def docking() -> DockingTargets:
    return load_docking_targets()


def test_real_proxy_is_reachable(docking: DockingTargets, tmp_path: Path) -> None:
    response = _proxy_status(docking, tmp_path)
    body = response.json()
    assert response.status_code == 200, _show(response, docking)
    assert body["kind"] == "nexus"
    assert body["configured"] is True
    assert body["reachable"] is True
    assert body["auth"] == "credentials"
    assert body["repository"] == docking.repository
    assert body["base_url"] == docking.nexus_url
    assert body["publishes_artifacts"] is False
    assert body["detail"] is None
    _reject(response.text, docking)


def test_wrong_password_is_unreachable(docking: DockingTargets, tmp_path: Path) -> None:
    response = _proxy_status(
        docking,
        tmp_path,
        password=docking.password + "-wrong",
    )
    body = response.json()
    assert response.status_code == 200, _show(response, docking)
    assert body["reachable"] is False
    assert body["publishes_artifacts"] is False
    assert "拒绝访问" in body["detail"]
    _reject(response.text, docking)
    assert docking.password + "-wrong" not in response.text


def test_unknown_repository_is_unreachable(docking: DockingTargets, tmp_path: Path) -> None:
    response = _proxy_status(docking, tmp_path, repository="missing-repo")
    body = response.json()
    assert response.status_code == 200, _show(response, docking)
    assert body["reachable"] is False
    assert body["publishes_artifacts"] is False
    assert "没有这个仓库" in body["detail"]
    _reject(response.text, docking)


def test_build_reads_the_group_and_publishes_nothing(
    docking: DockingTargets,
    tmp_path: Path,
) -> None:
    expected = _download_fixture(docking)
    before = _hosted_snapshot(docking)
    assert before, "hosted repository snapshot was empty"
    repo_name = f"kiln-src-{uuid4().hex[:10]}"
    full_name = ""
    job_name = ""
    root = tmp_path / "artifacts"
    settings = _build_settings(docking, root)
    app = create_app(settings)
    try:
        created_repo = _create_gitea_repo(docking, repo_name)
        full_name = str(created_repo["full_name"])
        payload = f"fixture {uuid4().hex}\n".encode()
        _put_gitea_file(docking, full_name, "payload.txt", payload, "add payload")
        _put_gitea_file(
            docking,
            full_name,
            "kiln-build.sh",
            _build_script(docking.fixture_path).encode(),
            "add build",
        )
        sha = _commit_sha(docking, full_name, "main")
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(app.state.engine)
            skill = publish_skill(client, f"dock-{uuid4().hex[:8]}", [full_name])
            created = client.post(
                "/api/v1/builds",
                json={
                    "skill_id": skill["id"],
                    "repository": full_name,
                    "ref": "main",
                    "name": "proxy",
                    "artifact_name": f"pkg{uuid4().hex[:8]}",
                    "version": "1.0.0",
                    "package_type": "generic",
                },
            )
            assert created.status_code == 201, _show(created, docking)
            _reject(created.text, docking)
            build = created.json()
            job_name = build["jenkins_job_name"]
            finished = _wait_until_stored(client, build["id"], docking)
            assert finished["content_available"] is True
            assert finished["commit_sha"] == sha.lower()
            assert finished["deploys_to_runtime"] is False
            log = client.get(f"/api/v1/builds/{build['id']}/log")
            assert log.status_code == 200, _show(log, docking)
            _reject(log.text, docking)
            assert sha.lower() in log.json()["text"]
            artifact = client.get(f"/api/v1/artifacts/{finished['artifact_id']}")
            assert artifact.status_code == 200, _show(artifact, docking)
            version = artifact.json()["versions"][0]
            blob = root / "sha256" / version["checksum"]["value"]
            assert blob.read_bytes() == expected
            _assert_config_has_no_password(docking, job_name)
    finally:
        if job_name and app.state.jenkins is not None:
            try:
                app.state.jenkins.delete_job(job_name)
            except Exception:
                pass
        if full_name:
            _delete_gitea_repo(docking, full_name)
    assert _hosted_snapshot(docking) == before


def _proxy_status(
    targets: DockingTargets,
    tmp_path: Path,
    *,
    password: str | None = None,
    repository: str | None = None,
) -> httpx.Response:
    app = create_app(
        app_settings(
            kiln_artifact_root=str(tmp_path / "artifacts"),
            kiln_nexus_url=targets.nexus_url,
            kiln_nexus_repository=repository or targets.repository,
            kiln_nexus_username=targets.username,
            kiln_nexus_password=targets.password if password is None else password,
            kiln_nexus_timeout_seconds=30,
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        return client.get("/api/v1/dependency-proxy")


def _build_settings(targets: DockingTargets, root: Path):
    return app_settings(
        kiln_artifact_root=str(root),
        jenkins_url=targets.jenkins_url,
        jenkins_user=targets.jenkins_user,
        jenkins_api_token=targets.jenkins_token,
        jenkins_timeout_seconds=60,
        git_provider="gitea",
        git_base_url=targets.git_base_url,
        git_clone_base_url=targets.git_clone_base_url,
        git_token=targets.git_token,
        git_timeout_seconds=60,
        kiln_nexus_url=targets.nexus_url,
        kiln_nexus_repository=targets.repository,
        kiln_nexus_build_url=targets.nexus_build_url,
        kiln_nexus_username=targets.username,
        kiln_nexus_password=targets.password,
        kiln_nexus_timeout_seconds=30,
    )


_BUILD_SCRIPT = r"""#!/bin/sh
if [ -z "${KILN_CURL_BASH:-}" ]; then
  KILN_CURL_BASH=1 exec bash "$0" "$@"
fi
set +x
set -eu
mkdir -p kiln-dist
url="${KILN_NEXUS_URL%/}/repository/${KILN_NEXUS_REPOSITORY}/__FIXTURE__"
curl_user_config() {
  u=$1
  p=$2
  case "$u" in
    *$'\n'*|*$'\r'*)
      echo "refusing a credential that contains a newline" >&2
      exit 1
      ;;
  esac
  case "$p" in
    *$'\n'*|*$'\r'*)
      echo "refusing a credential that contains a newline" >&2
      exit 1
      ;;
  esac
  u=${u//\\/\\\\}; u=${u//\"/\\\"}
  p=${p//\\/\\\\}; p=${p//\"/\\\"}
  printf 'user = "%s:%s"\n' "$u" "$p"
}
if [ -n "${KILN_NEXUS_USERNAME}" ]; then
  config="$(curl_user_config "$KILN_NEXUS_USERNAME" "$KILN_NEXUS_PASSWORD")" || exit 1
  printf '%s\n' "$config" | curl -K - -fsS -o kiln-dist/artifact.bin "$url"
else
  curl -fsS -o kiln-dist/artifact.bin "$url"
fi
test -s kiln-dist/artifact.bin
"""


def _build_script(fixture_path: str) -> str:
    if "\n" in fixture_path or "__FIXTURE__" in fixture_path:
        raise ValueError("fixture path is not safe to embed")
    return _BUILD_SCRIPT.replace("__FIXTURE__", fixture_path, 1)


def _download_fixture(targets: DockingTargets) -> bytes:
    response = httpx.get(
        f"{targets.nexus_url}/repository/{targets.repository}/{targets.fixture_path}",
        auth=(targets.username, targets.password),
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    )
    assert response.status_code == 200, response.status_code
    assert targets.password not in response.text
    return response.content


def _hosted_snapshot(targets: DockingTargets) -> list[tuple[str, str, str]]:
    auth = (targets.admin_user, targets.admin_password)
    listed = httpx.get(
        f"{targets.nexus_url}/service/rest/v1/repositories",
        auth=auth,
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    )
    assert listed.status_code == 200, listed.status_code
    names = []
    for item in listed.json():
        if not isinstance(item, dict) or item.get("type") != "hosted":
            continue
        name = item.get("name")
        if isinstance(name, str):
            names.append(name)
    found: list[tuple[str, str, str]] = []
    for name in names:
        token = ""
        while True:
            params = {"repository": name}
            if token:
                params["continuationToken"] = token
            page = httpx.get(
                f"{targets.nexus_url}/service/rest/v1/components",
                params=params,
                auth=auth,
                timeout=30,
                trust_env=False,
                follow_redirects=False,
            )
            assert page.status_code == 200, page.status_code
            body = page.json()
            for item in body.get("items", []):
                if not isinstance(item, dict):
                    continue
                for asset in item.get("assets", []):
                    if not isinstance(asset, dict):
                        continue
                    checksum = asset.get("checksum")
                    digest = ""
                    if isinstance(checksum, dict) and isinstance(checksum.get("sha256"), str):
                        digest = checksum["sha256"]
                    path = asset.get("path")
                    found.append((name, path if isinstance(path, str) else "", digest))
            token = body.get("continuationToken") or ""
            if not token:
                break
    return sorted(found)


def _assert_config_has_no_password(targets: DockingTargets, job_name: str) -> None:
    response = httpx.get(
        f"{targets.jenkins_url}/job/{quote(job_name, safe='')}/config.xml",
        auth=(targets.jenkins_user, targets.jenkins_token),
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    )
    assert response.status_code == 200, response.status_code
    if targets.password and targets.password in response.text:
        raise AssertionError("nexus password appeared in Jenkins config.xml")
    assert "PasswordParameterDefinition" in response.text


def _require_ready(targets: DockingTargets) -> None:
    nexus_at = _public(targets.nexus_url)
    try:
        status = httpx.get(
            f"{targets.nexus_url}/service/rest/v1/status",
            auth=(targets.username, targets.password),
            timeout=20,
            trust_env=False,
            follow_redirects=False,
        )
        fixture = httpx.get(
            f"{targets.nexus_url}/repository/{targets.repository}/{targets.fixture_path}",
            auth=(targets.username, targets.password),
            timeout=20,
            trust_env=False,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        pytest.fail(f"Nexus is not reachable at {nexus_at}: {exc.__class__.__name__}")
    if status.status_code != 200:
        pytest.fail(f"Nexus at {nexus_at} returned HTTP {status.status_code}")
    if fixture.status_code != 200:
        pytest.fail(f"Nexus fixture at {nexus_at} returned HTTP {fixture.status_code}")


def _wait_until_stored(client: TestClient, build_id: str, targets: DockingTargets) -> dict:
    deadline = time.monotonic() + 180
    last = ""
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/builds/{build_id}")
        if response.status_code == 502:
            last = _show(response, targets)
            time.sleep(1)
            continue
        assert response.status_code == 200, _show(response, targets)
        _reject(response.text, targets)
        body = response.json()
        if body["status"] in {"failure", "unstable", "aborted", "not_built"}:
            raise AssertionError(_failure_detail(client, build_id, targets))
        if body["status"] == "success" and body["content_available"]:
            return body
        last = body["status"]
        time.sleep(1)
    raise AssertionError(f"{last}\n{_failure_detail(client, build_id, targets)}")


def _failure_detail(client: TestClient, build_id: str, targets: DockingTargets) -> str:
    log = client.get(f"/api/v1/builds/{build_id}/log")
    if log.status_code != 200:
        return _show(log, targets)
    return _redact(log.json().get("text", ""), targets)


def _show(response: httpx.Response, targets: DockingTargets) -> str:
    return f"HTTP {response.status_code}\n{_redact(response.text, targets)}"


def _reject(body: str, targets: DockingTargets) -> None:
    if targets.password and targets.password in body:
        raise AssertionError("nexus password appeared in an API response")
    if targets.git_token and targets.git_token in body:
        raise AssertionError("git token appeared in an API response")


def _redact(text: str, targets: DockingTargets) -> str:
    secrets = (
        targets.password,
        targets.git_token,
        targets.admin_password,
        targets.jenkins_token,
    )
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _public(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        return f"{parts.scheme}://{host}:{parts.port}"
    return f"{parts.scheme}://{host}"


def _gitea_headers(targets: DockingTargets) -> dict[str, str]:
    return {"Authorization": f"token {targets.git_token}", "Accept": "application/json"}


def _create_gitea_repo(targets: DockingTargets, name: str) -> dict:
    response = httpx.post(
        f"{targets.git_base_url}/api/v1/user/repos",
        headers=_gitea_headers(targets),
        json={"name": name, "private": True, "auto_init": True, "default_branch": "main"},
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 201, response.status_code
    body = response.json()
    assert isinstance(body, dict)
    return body


def _put_gitea_file(
    targets: DockingTargets,
    full_name: str,
    path: str,
    content: bytes,
    message: str,
) -> None:
    response = httpx.post(
        f"{targets.git_base_url}/api/v1/repos/{full_name}/contents/{path}",
        headers=_gitea_headers(targets),
        json={
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": "main",
        },
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 201, response.status_code


def _commit_sha(targets: DockingTargets, full_name: str, branch: str) -> str:
    response = httpx.get(
        f"{targets.git_base_url}/api/v1/repos/{full_name}/git/commits/{branch}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 200, response.status_code
    sha = response.json().get("sha")
    assert isinstance(sha, str) and sha
    return sha


def _delete_gitea_repo(targets: DockingTargets, full_name: str) -> None:
    httpx.delete(
        f"{targets.git_base_url}/api/v1/repos/{full_name}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )
