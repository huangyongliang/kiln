"""Replaceable git source.

Gitea is the lab adapter and is tested against a real Gitea. GitLab is the
production adapter type. These tests do not invent a production GitLab URL.
"""

import time
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from kiln.config import Settings
from kiln.git.gitea import GiteaSource, commit_path
from kiln.git.gitlab import GitLabSource, project_commit_path
from kiln.git.source import PRODUCTION_GIT_PROVIDER, build_git_source
from kiln.main import create_app
from tests.external import ExternalTargets, external_settings
from tests.support import auth_headers, truncate_domain


def test_gitlab_is_the_production_adapter_and_gitea_is_the_lab() -> None:
    assert PRODUCTION_GIT_PROVIDER == "gitlab"
    with httpx.Client(trust_env=False) as client:
        gitlab = build_git_source(
            Settings(
                git_provider="gitlab",
                git_base_url="http://127.0.0.1:9",
                git_token="local-only",
                _env_file=None,
            ),
            client,
        )
        gitea = build_git_source(
            Settings(
                git_provider="gitea",
                git_base_url="http://127.0.0.1:9",
                git_token="local-only",
                _env_file=None,
            ),
            client,
        )
        assert isinstance(gitlab, GitLabSource)
        assert gitlab.provider == "gitlab"
        assert gitlab.role == "production"
        assert isinstance(gitea, GiteaSource)
        assert gitea.provider == "gitea"
        assert gitea.role == "lab"
        assert build_git_source(Settings(git_provider="off", _env_file=None), client) is None
    assert project_commit_path("group", "app", "main") == (
        "/api/v4/projects/group%2Fapp/repository/commits/main"
    )
    assert commit_path("kiln", "sample", "main") == "/api/v1/repos/kiln/sample/commits/main"
    assert "/api/v4/" not in commit_path("kiln", "sample", "main")


def test_unconfigured_git_source_names_gitlab_without_a_url(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path in ("/api/v1/git/source", "/api/v1/git/repositories", "/api/v1/git/commits"):
        assert path in spec["paths"]
        for operation in spec["paths"][path].values():
            if isinstance(operation, dict) and "responses" in operation:
                assert operation.get("security"), path

    info = client.get("/api/v1/git/source")
    assert info.status_code == 200
    body = info.json()
    assert body["provider"] == "off"
    assert body["role"] == "unconfigured"
    assert body["production_provider"] == "gitlab"
    assert body["configured"] is False
    assert body["base_url"] is None
    assert "production_url" not in body
    listed = client.get("/api/v1/git/repositories")
    assert listed.status_code == 503
    assert "GIT_PROVIDER" in listed.json()["detail"]


def test_gitea_adapter_lists_and_resolves_a_real_repository(
    external_targets: ExternalTargets,
) -> None:
    repo_name = f"kiln-src-{uuid4().hex[:10]}"
    full_name = ""
    try:
        created = _create_gitea_repo(external_targets, repo_name)
        full_name = created["full_name"]
        owner, name = full_name.split("/", 1)
        branch = _default_branch(external_targets, full_name)
        sha = _commit_sha(external_targets, full_name, branch)
        settings = external_settings(external_targets)
        app = create_app(settings)
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(app.state.engine)
            info = client.get("/api/v1/git/source")
            assert info.status_code == 200
            described = info.json()
            assert described["provider"] == "gitea"
            assert described["role"] == "lab"
            assert described["production_provider"] == "gitlab"
            assert described["configured"] is True
            assert described["base_url"] == app.state.settings.git_base_url
            assert "production_url" not in described

            listed = client.get("/api/v1/git/repositories")
            assert listed.status_code == 200, listed.text
            catalog = listed.json()
            assert catalog["provider"] == "gitea"
            assert catalog["role"] == "lab"
            match = next(item for item in catalog["items"] if item["full_name"] == full_name)
            assert match["owner"] == owner
            assert match["name"] == name
            assert match["default_branch"] == branch

            resolved = client.get(
                "/api/v1/git/commits",
                params={"repository": full_name, "ref": branch},
            )
            assert resolved.status_code == 200, resolved.text
            commit = resolved.json()
            assert commit["sha"] == sha
            assert commit["provider"] == "gitea"
            assert commit["role"] == "lab"
            assert commit["owner"] == owner
            assert commit["ref"] == branch

            anon = create_app(settings)
            with TestClient(anon) as unauthenticated:
                assert unauthenticated.get("/api/v1/git/source").status_code == 401
                assert unauthenticated.get("/api/v1/git/repositories").status_code == 401
                denied = unauthenticated.get(
                    "/api/v1/git/commits",
                    params={"repository": full_name, "ref": branch},
                )
                assert denied.status_code == 401
                assert denied.json()["detail"] == "需要访问令牌"
    finally:
        if full_name:
            _delete_gitea_repo(external_targets, full_name)


def _gitea_headers(targets: ExternalTargets) -> dict[str, str]:
    return {"Authorization": f"token {targets.git_token}", "Accept": "application/json"}


def _create_gitea_repo(targets: ExternalTargets, name: str) -> dict:
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
    assert body["name"] == name
    return body


def _default_branch(targets: ExternalTargets, full_name: str) -> str:
    response = httpx.get(
        f"{targets.git_base_url}/api/v1/repos/{full_name}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 200, response.status_code
    branch = response.json().get("default_branch")
    assert isinstance(branch, str) and branch
    return branch


def _commit_sha(targets: ExternalTargets, full_name: str, branch: str) -> str:
    deadline = time.monotonic() + 20
    last_status = 0
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{targets.git_base_url}/api/v1/repos/{full_name}/commits/{branch}",
            headers=_gitea_headers(targets),
            timeout=30,
            trust_env=False,
        )
        last_status = response.status_code
        if response.status_code == 200:
            sha = response.json().get("sha")
            if isinstance(sha, str) and sha:
                return sha
        time.sleep(0.5)
    raise AssertionError(
        f"Gitea did not return a commit for {full_name}@{branch}: HTTP {last_status}"
    )


def _delete_gitea_repo(targets: ExternalTargets, full_name: str) -> None:
    httpx.delete(
        f"{targets.git_base_url}/api/v1/repos/{full_name}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )
