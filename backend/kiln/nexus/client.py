"""Read-only HTTP client for a Nexus repository manager.

Probe checks that the service is up and that the configured repository is
visible. It never uploads, and it never puts the password in a URL, a log
line, or an error string. Callers pass an ``httpx.Client(trust_env=False)``.
"""

from urllib.parse import quote, urlsplit

import httpx

from kiln.errors import UpstreamFailure


class NexusClient:
    def __init__(
        self,
        base_url: str,
        repository: str,
        username: str,
        password: str,
        client: httpx.Client,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._repository = repository
        self._netloc = urlsplit(self._base).netloc
        self._username = username
        self._password = password
        self._client = client

    def __repr__(self) -> str:
        auth = "credentials" if self._username else "anonymous"
        return (
            f"NexusClient(netloc={self._netloc!r}, repository={self._repository!r}, auth={auth!r})"
        )

    def probe(self) -> None:
        """Raise ``UpstreamFailure`` when the proxy cannot be used."""

        self._require_status()
        names = self._repository_names()
        if names is not None and self._repository in names:
            return
        self._require_repository_root()

    def _require_status(self) -> None:
        response = self._request("GET", "/service/rest/v1/status", accept_json=True)
        if response.status_code in {401, 403}:
            raise self._denied()
        if response.status_code != 200:
            raise self._http(response.status_code)
        if response.content.strip():
            try:
                response.json()
            except ValueError as exc:
                raise self._not_json() from exc

    def _repository_names(self) -> set[str] | None:
        """Names visible to this user, or None when the list cannot be read.

        A read-only account sometimes cannot see a group through
        ``/service/rest/v1/repositories``. Callers then fall back to
        ``GET /repository/<name>/``.
        """

        response = self._request("GET", "/service/rest/v1/repositories", accept_json=True)
        if response.status_code in {401, 403, 404}:
            return None
        if response.status_code != 200:
            raise self._http(response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise self._not_json() from exc
        if not isinstance(body, list):
            raise self._error(f"Nexus 返回的不是仓库列表（{self._netloc}）")
        names: set[str] = set()
        for item in body:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name:
                names.add(name)
        return names

    def _require_repository_root(self) -> None:
        """Count 200 and 400 as present. Nexus 3.98 returns 400 for a group root."""

        path = "/repository/" + quote(self._repository, safe="") + "/"
        response = self._request("GET", path)
        if response.status_code in {200, 400}:
            return
        if response.status_code == 404:
            raise self._missing_repository()
        if response.status_code in {401, 403}:
            raise self._denied()
        raise self._http(response.status_code)

    def _request(self, method: str, path: str, *, accept_json: bool = False) -> httpx.Response:
        headers = {"Accept": "application/json"} if accept_json else None
        auth = httpx.BasicAuth(self._username, self._password) if self._username else None
        try:
            return self._client.request(
                method,
                self._base + path,
                headers=headers,
                auth=auth,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise self._error(f"Nexus 无法连接（{self._netloc}）") from exc

    def _denied(self) -> UpstreamFailure:
        return self._error(f"Nexus 拒绝访问（{self._netloc}）")

    def _http(self, status_code: int) -> UpstreamFailure:
        return self._error(f"Nexus 调用失败（{self._netloc}，HTTP {status_code}）")

    def _not_json(self) -> UpstreamFailure:
        return self._error(f"Nexus 返回的不是 JSON（{self._netloc}）")

    def _missing_repository(self) -> UpstreamFailure:
        return self._error(f"Nexus 没有这个仓库（{self._netloc}）")

    def _error(self, detail: str) -> UpstreamFailure:
        if self._password:
            detail = detail.replace(self._password, "***")
        return UpstreamFailure(detail)
