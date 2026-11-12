"""Production git adapter. Speaks the GitLab HTTP API at the configured base URL.

This module does not embed a GitLab host. The process must set GIT_BASE_URL.
"""

from urllib.parse import quote, urlsplit

import httpx

from kiln.domain.git import GitRepository, ResolvedCommit
from kiln.git.http import as_list, as_object, read_json, require_sha


class GitLabSource:
    """GitLab is the production code source."""

    provider = "gitlab"
    role = "production"

    def __init__(self, base_url: str, token: str, client: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._netloc = urlsplit(self._base).netloc
        self._client = client
        self._headers = {
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
        }

    def list_repositories(self) -> list[GitRepository]:
        found: list[GitRepository] = []
        for page in range(1, 6):
            payload = read_json(
                self._client,
                f"{self._base}/api/v4/projects",
                self._headers,
                params={
                    "membership": "true",
                    "simple": "true",
                    "per_page": 50,
                    "page": page,
                    "order_by": "last_activity_at",
                    "sort": "desc",
                },
            )
            rows = as_list(payload, self._netloc)
            for item in rows:
                mapped = _repository(item)
                if mapped is not None:
                    found.append(mapped)
            if len(rows) < 50:
                break
        return found

    def resolve_ref(self, owner: str, name: str, ref: str) -> ResolvedCommit:
        payload = read_json(
            self._client,
            f"{self._base}{project_commit_path(owner, name, ref)}",
            self._headers,
        )
        body = as_object(payload, self._netloc)
        return ResolvedCommit(
            provider=self.provider,
            role=self.role,
            owner=owner,
            name=name,
            ref=ref,
            sha=require_sha(body.get("id"), self._netloc),
        )


def project_commit_path(owner: str, name: str, ref: str) -> str:
    """GitLab project path used by resolve_ref. Safe to unit-test without a server."""

    project = quote(f"{owner}/{name}", safe="")
    return f"/api/v4/projects/{project}/repository/commits/{quote(ref, safe='')}"


def _repository(item: object) -> GitRepository | None:
    if not isinstance(item, dict):
        return None
    full_name = item.get("path_with_namespace")
    name = item.get("path")
    if not isinstance(full_name, str) or "/" not in full_name:
        return None
    if not isinstance(name, str) or not name:
        name = full_name.rsplit("/", 1)[1]
    if not full_name.endswith("/" + name):
        return None
    branch = item.get("default_branch")
    return GitRepository(
        owner=full_name[: -(len(name) + 1)],
        name=name,
        full_name=full_name,
        default_branch=branch if isinstance(branch, str) else "",
    )
