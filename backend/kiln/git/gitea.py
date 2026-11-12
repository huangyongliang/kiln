"""Lab git adapter. Speaks the Gitea HTTP API at the configured base URL."""

from urllib.parse import quote, urlsplit

import httpx

from kiln.domain.git import GitRepository, ResolvedCommit
from kiln.git.http import as_list, as_object, read_json, require_sha, require_username


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
        # Gitea 1.24 has no GitHub-style /commits/{ref} (that path is 404).
        # The single-commit route is /git/commits/{ref} and its body has `sha`.
        payload = read_json(
            self._client,
            f"{self._base}{commit_path(owner, name, ref)}",
            self._headers,
        )
        body = as_object(payload, self._netloc)
        return ResolvedCommit(
            provider=self.provider,
            role=self.role,
            owner=owner,
            name=name,
            ref=ref,
            sha=require_sha(body.get("sha"), self._netloc),
        )

    def authenticated_username(self) -> str:
        payload = read_json(
            self._client,
            f"{self._base}/api/v1/user",
            self._headers,
        )
        return require_username(as_object(payload, self._netloc), self._netloc)


def commit_path(owner: str, name: str, ref: str) -> str:
    """Gitea single-commit path. Response field is `sha`."""

    return (
        f"/api/v1/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/git/commits/{quote(ref, safe='')}"
    )


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
