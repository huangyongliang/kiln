"""Lab git adapter. Speaks the Gitea HTTP API at the configured base URL."""

from urllib.parse import quote, urlsplit

import httpx

from kiln.domain.git import GitRepository, ResolvedCommit
from kiln.errors import NotFound
from kiln.git.http import as_list, as_object, read_json, require_sha


class GiteaSource:
    """Gitea is the lab stand-in, not the production code source."""

    provider = "gitea"
    role = "lab"

    def __init__(self, base_url: str, token: str, client: httpx.Client) -> None:
        self._base = base_url.rstrip("/")
        self._netloc = urlsplit(self._base).netloc
        self._client = client
        self._headers = {
            "Authorization": f"token {token}",
            "Accept": "application/json",
        }

    def list_repositories(self) -> list[GitRepository]:
        found: list[GitRepository] = []
        for page in range(1, 6):
            payload = read_json(
                self._client,
                f"{self._base}/api/v1/user/repos",
                self._headers,
                params={"limit": 50, "page": page, "sort": "recentupdate"},
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
        # Gitea has no GitHub-style /commits/{branch}. A branch lives at
        # /branches/{ref} (commit.id). A sha lives at /git/commits/{sha}.
        # A tag lives at /tags/{ref} (commit.sha).
        for path, nested in (
            (branch_path(owner, name, ref), True),
            (git_commit_path(owner, name, ref), False),
            (tag_path(owner, name, ref), True),
        ):
            try:
                payload = read_json(self._client, f"{self._base}{path}", self._headers)
            except NotFound:
                continue
            body = as_object(payload, self._netloc)
            source = body.get("commit") if nested else body
            if not isinstance(source, dict):
                source = {}
            return ResolvedCommit(
                provider=self.provider,
                role=self.role,
                owner=owner,
                name=name,
                ref=ref,
                sha=require_sha(source.get("id") or source.get("sha"), self._netloc),
            )
        raise NotFound("仓库或引用不存在")


def branch_path(owner: str, name: str, ref: str) -> str:
    """Gitea branch path. The commit id is `commit.id`."""

    return f"{_repo_path(owner, name)}/branches/{quote(ref, safe='')}"


def git_commit_path(owner: str, name: str, ref: str) -> str:
    """Gitea git commit path. The commit id is `sha`."""

    return f"{_repo_path(owner, name)}/git/commits/{quote(ref, safe='')}"


def tag_path(owner: str, name: str, ref: str) -> str:
    return f"{_repo_path(owner, name)}/tags/{quote(ref, safe='')}"


def _repo_path(owner: str, name: str) -> str:
    return f"/api/v1/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _repository(item: object) -> GitRepository | None:
    if not isinstance(item, dict):
        return None
    owner_obj = item.get("owner")
    owner = ""
    if isinstance(owner_obj, dict):
        login = owner_obj.get("login") or owner_obj.get("username")
        if isinstance(login, str):
            owner = login
    name = item.get("name")
    full_name = item.get("full_name")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(full_name, str) or not full_name:
        if not owner:
            return None
        full_name = f"{owner}/{name}"
    if "/" not in full_name:
        return None
    branch = item.get("default_branch")
    return GitRepository(
        owner=full_name.rsplit("/", 1)[0],
        name=name,
        full_name=full_name,
        default_branch=branch if isinstance(branch, str) else "",
    )
