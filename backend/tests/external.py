"""Real Jenkins and Gitea targets for tests.

The default is to fail when the endpoints are missing. Set
KILN_SKIP_EXTERNAL_TESTS=1 to skip. CI starts throwaway servers and exports
KILN_TEST_JENKINS_* and KILN_TEST_GIT_*. The same variables can point at an
already running lab. Tokens are read from the environment and are not
hard-coded.
"""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import pytest

from kiln.config import Settings
from tests.support import app_settings

_REQUIRED = (
    "KILN_TEST_JENKINS_URL",
    "KILN_TEST_JENKINS_USER",
    "KILN_TEST_JENKINS_API_TOKEN",
    "KILN_TEST_GIT_BASE_URL",
    "KILN_TEST_GIT_TOKEN",
)


@dataclass(frozen=True)
class ExternalTargets:
    jenkins_url: str
    jenkins_user: str
    jenkins_token: str
    git_base_url: str
    git_token: str
    # URL Jenkins can clone. May differ from git_base_url when Jenkins runs in
    # another network namespace. Not a secret and not a production host.
    git_clone_base_url: str


def load_external_targets() -> ExternalTargets:
    if os.environ.get("KILN_SKIP_EXTERNAL_TESTS") == "1":
        pytest.skip("KILN_SKIP_EXTERNAL_TESTS=1; real Jenkins and Gitea tests were skipped")
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        pytest.fail(
            "Real Jenkins and Gitea tests require "
            + ", ".join(missing)
            + ". Point them at a running server, or set KILN_SKIP_EXTERNAL_TESTS=1 to skip."
        )
    provider = os.environ.get("KILN_TEST_GIT_PROVIDER", "gitea").strip().lower()
    if provider != "gitea":
        pytest.fail("KILN_TEST_GIT_PROVIDER must be gitea for the lab adapter tests")
    git_base_url = os.environ["KILN_TEST_GIT_BASE_URL"].strip().rstrip("/")
    clone_base = os.environ.get("KILN_TEST_GIT_CLONE_BASE_URL", "").strip().rstrip("/")
    targets = ExternalTargets(
        jenkins_url=os.environ["KILN_TEST_JENKINS_URL"].strip().rstrip("/"),
        jenkins_user=os.environ["KILN_TEST_JENKINS_USER"].strip(),
        jenkins_token=os.environ["KILN_TEST_JENKINS_API_TOKEN"].strip(),
        git_base_url=git_base_url,
        git_token=os.environ["KILN_TEST_GIT_TOKEN"].strip(),
        git_clone_base_url=clone_base or git_base_url,
    )
    _probe(targets)
    return targets


def external_settings(
    targets: ExternalTargets,
    *,
    artifact_root: str | None = None,
) -> Settings:
    values: dict[str, object] = {
        "jenkins_url": targets.jenkins_url,
        "jenkins_user": targets.jenkins_user,
        "jenkins_api_token": targets.jenkins_token,
        "jenkins_timeout_seconds": 60,
        "git_provider": "gitea",
        "git_base_url": targets.git_base_url,
        "git_clone_base_url": targets.git_clone_base_url,
        "git_token": targets.git_token,
        "git_timeout_seconds": 60,
    }
    if artifact_root is not None:
        values["kiln_artifact_root"] = artifact_root
    return app_settings(**values)


def _probe(targets: ExternalTargets) -> None:
    jenkins_at = _public(targets.jenkins_url)
    git_at = _public(targets.git_base_url)
    try:
        jenkins = httpx.get(
            f"{targets.jenkins_url}/api/json",
            params={"tree": "mode"},
            auth=(targets.jenkins_user, targets.jenkins_token),
            timeout=20,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        pytest.fail(f"Jenkins is not reachable at {jenkins_at}: {exc.__class__.__name__}")
    if jenkins.status_code != 200:
        pytest.fail(f"Jenkins at {jenkins_at} returned HTTP {jenkins.status_code}")
    try:
        health = httpx.get(f"{targets.git_base_url}/api/healthz", timeout=20, trust_env=False)
        user = httpx.get(
            f"{targets.git_base_url}/api/v1/user",
            headers={"Authorization": f"token {targets.git_token}"},
            timeout=20,
            trust_env=False,
        )
    except httpx.HTTPError as exc:
        pytest.fail(f"Gitea is not reachable at {git_at}: {exc.__class__.__name__}")
    if health.status_code != 200:
        pytest.fail(f"Gitea health at {git_at} returned HTTP {health.status_code}")
    if user.status_code != 200:
        pytest.fail(f"Gitea API at {git_at} returned HTTP {user.status_code}")


def _public(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        return f"{parts.scheme}://{host}:{parts.port}"
    return f"{parts.scheme}://{host}"
