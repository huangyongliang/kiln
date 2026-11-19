"""Replaceable git source.

Gitea is the lab adapter and is tested against a real Gitea. GitLab is the
production adapter type. These tests do not invent a production GitLab URL.
"""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from kiln.config import Settings
from kiln.errors import NotFound, UpstreamFailure
from kiln.git.gitea import GiteaSource, branches_path, commit_path
from kiln.git.gitlab import GitLabSource, project_branches_path, project_commit_path
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
    assert commit_path("kiln", "sample", "main") == "/api/v1/repos/kiln/sample/git/commits/main"
    assert "/api/v4/" not in commit_path("kiln", "sample", "main")
    assert project_branches_path("group/sub", "app") == (
        "/api/v4/projects/group%2Fsub%2Fapp/repository/branches"
    )
    assert branches_path("group/sub", "app") == "/api/v1/repos/group%2Fsub/app/branches"
    assert "/api/v4/" not in branches_path("kiln", "sample")


def test_unconfigured_git_source_names_gitlab_without_a_url(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for path in (
        "/api/v1/git/source",
        "/api/v1/git/repositories",
        "/api/v1/git/branches",
        "/api/v1/git/commits",
    ):
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
    branches = client.get("/api/v1/git/branches", params={"repository": "ada/demo"})
    assert branches.status_code == 503
    assert "GIT_PROVIDER" in branches.json()["detail"]
    assert "代码源未配置" in branches.json()["detail"]


def test_gitea_and_gitlab_list_branches_with_mock_http() -> None:
    gitea = _list_with_mock(
        GiteaSource,
        "limit",
        b"/api/v1/repos/group%2Fsub/app/branches",
        "token local-only",
        "Authorization",
    )
    gitlab = _list_with_mock(
        GitLabSource,
        "per_page",
        b"/api/v4/projects/group%2Fsub%2Fapp/repository/branches",
        "local-only",
        "PRIVATE-TOKEN",
    )
    assert gitea.names == ["main", "feature/demo"]
    assert gitlab.names == ["main", "feature/demo"]
    for listed in (gitea, gitlab):
        assert listed.pages == [1]
        assert [item.model_dump() for item in listed.branches] == [
            {"name": "main"},
            {"name": "feature/demo"},
        ]
        assert all("sha" not in item.model_dump() for item in listed.branches)

    gitea_pages = _paginate(GiteaSource, "limit", "per_page")
    gitlab_pages = _paginate(GitLabSource, "per_page", "limit")
    assert gitea_pages.short_pages == [1, 2]
    assert gitlab_pages.short_pages == [1, 2]
    assert gitea_pages.full_pages == [1, 2, 3, 4, 5]
    assert gitlab_pages.full_pages == [1, 2, 3, 4, 5]
    assert gitea_pages.names[0] == "b-1-0"
    assert gitea_pages.names[-1] == "b-2-0"
    assert len(gitea_pages.full_names) == 250

    for source_type in (GiteaSource, GitLabSource):
        missing = _exchange(
            source_type,
            lambda _request: httpx.Response(404, json={"message": "no"}),
        )
        with pytest.raises(NotFound) as not_found:
            missing.list_branches("ada", "demo")
        assert not_found.value.detail == "仓库或引用不存在"
        assert "no" not in not_found.value.detail

        broken = _exchange(
            source_type,
            lambda _request: httpx.Response(200, json={"message": "nope"}),
        )
        with pytest.raises(UpstreamFailure) as failed:
            broken.list_branches("ada", "demo")
        assert failed.value.detail == "代码源返回的不是分支列表（127.0.0.1:9）"
        assert "仓库列表" not in failed.value.detail
        assert "nope" not in failed.value.detail


class _Listed:
    def __init__(self, branches: list, pages: list[int]) -> None:
        self.branches = branches
        self.pages = pages

    @property
    def names(self) -> list[str]:
        return [item.name for item in self.branches]


class _Pages:
    def __init__(
        self,
        short_pages: list[int],
        full_pages: list[int],
        names: list[str],
        full_names: list[str],
    ) -> None:
        self.short_pages = short_pages
        self.full_pages = full_pages
        self.names = names
        self.full_names = full_names


def _list_with_mock(
    source_type: type,
    page_param: str,
    encoded_path: bytes,
    token_value: str,
    token_header: str,
) -> _Listed:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 9
        assert encoded_path in request.url.raw_path
        assert page_param in request.url.params
        assert request.url.params[page_param] == "50"
        assert request.url.params["page"] == "1"
        assert token_value not in str(request.url)
        assert request.headers[token_header] == token_value
        return httpx.Response(
            200,
            json=[
                {"name": "main", "commit": {"id": "abc", "sha": "should-not-surface"}},
                {"commit": {"id": "hidden"}},
                {"name": ""},
                {"name": "feature/demo"},
                "not-a-branch",
            ],
        )

    source = _exchange(source_type, handler)
    branches = source.list_branches("group/sub", "app")
    return _Listed(branches, [int(item.url.params["page"]) for item in seen])


def _paginate(source_type: type, page_param: str, absent_param: str) -> _Pages:
    short = _pages_for(source_type, page_param, absent_param, {1: 50, 2: 1})
    full = _pages_for(source_type, page_param, absent_param, {page: 50 for page in range(1, 6)})
    return _Pages(short[0], full[0], short[1], full[1])


def _pages_for(
    source_type: type,
    page_param: str,
    absent_param: str,
    counts: dict[int, int],
) -> tuple[list[int], list[str]]:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        assert request.url.params[page_param] == "50"
        assert absent_param not in request.url.params
        assert page in counts
        rows = [
            {"name": f"b-{page}-{index}", "commit": {"id": "abc"}}
            for index in range(counts[page])
        ]
        return httpx.Response(200, json=rows)

    source = _exchange(source_type, handler)
    branches = source.list_branches("ada", "demo")
    return seen, [item.name for item in branches]


def _exchange(source_type: type, handler) -> GiteaSource | GitLabSource:
    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    return source_type("http://127.0.0.1:9", "local-only", client)


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
                assert (
                    unauthenticated.get(
                        "/api/v1/git/branches",
                        params={"repository": full_name},
                    ).status_code
                    == 401
                )
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
    # Direct Gitea call, not through kiln. /commits/{ref} is 404 on Gitea 1.24;
    # /git/commits/{ref} returns the commit object and its sha.
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


def _delete_gitea_repo(targets: ExternalTargets, full_name: str) -> None:
    httpx.delete(
        f"{targets.git_base_url}/api/v1/repos/{full_name}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )
