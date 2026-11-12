"""HTTP client for a real Jenkins controller.

Talks to the Jenkins remote API. The compat job prints a validated marker.
A product build checks out git and archives one file. This client does not
deploy anywhere.
"""

import re
import time
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

import httpx

from kiln.errors import BadRequest, NotFound, PayloadTooLarge, UpstreamFailure
from kiln.executor.script import product_build_command, product_job_config

_MARKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_LOG_LIMIT = 200_000
_RESULT = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "UNSTABLE": "unstable",
    "ABORTED": "aborted",
    "NOT_BUILT": "not_built",
}


class JenkinsClient:
    def __init__(
        self,
        base_url: str,
        user: str,
        api_token: str,
        client: httpx.Client,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._netloc = urlsplit(self._base).netloc
        self._auth = httpx.BasicAuth(user, api_token)
        self._client = client

    def create_job(self, job_name: str, marker: str) -> None:
        _require_marker(marker)
        response = self._request(
            "POST",
            "/createItem",
            params={"name": job_name},
            content=_job_config(marker).encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        if response.status_code in {200, 201, 302}:
            return
        if response.status_code == 400 and "already exists" in response.text.lower():
            raise UpstreamFailure(f"Jenkins 上已有同名任务（{self._netloc}）")
        raise UpstreamFailure(_failure(response, self._netloc))

    def create_product_job(
        self,
        job_name: str,
        *,
        clone_url: str,
        ref: str,
        sha: str,
        git_user: str,
        git_token: str,
        declare_nexus_parameters: bool = False,
    ) -> None:
        command = product_build_command(
            clone_url=clone_url,
            ref=ref,
            sha=sha,
            git_user=git_user,
            git_token=git_token,
        )
        response = self._request(
            "POST",
            "/createItem",
            params={"name": job_name},
            content=product_job_config(
                command,
                nexus_parameters=declare_nexus_parameters,
            ).encode("utf-8"),
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        if response.status_code in {200, 201, 302}:
            return
        if response.status_code == 400 and "already exists" in response.text.lower():
            raise UpstreamFailure(f"Jenkins 上已有同名任务（{self._netloc}）")
        raise UpstreamFailure(_failure(response, self._netloc))

    def download_artifact(
        self,
        job_name: str,
        build_number: int,
        relative_path: str,
        max_bytes: int,
    ) -> bytes:
        path = "/".join(quote(part, safe="") for part in relative_path.split("/"))
        target = f"/job/{quote(job_name, safe='')}/{build_number}/artifact/{path}"
        last_status = 0
        for attempt in range(5):
            response = self._request("GET", target)
            last_status = response.status_code
            if response.status_code == 200:
                return _bounded(response, max_bytes, self._netloc)
            if response.status_code != 404 or attempt == 4:
                break
            time.sleep(0.5)
        if last_status == 404:
            raise UpstreamFailure(f"Jenkins 没有这个制品文件（{self._netloc}）")
        raise UpstreamFailure(f"Jenkins 调用失败（{self._netloc}，HTTP {last_status}）")

    def trigger_build(
        self,
        job_name: str,
        *,
        parameters: dict[str, str] | None = None,
    ) -> str | None:
        secret = ""
        if parameters is None:
            path = f"/job/{quote(job_name, safe='')}/build"
            data = None
        else:
            path = f"/job/{quote(job_name, safe='')}/buildWithParameters"
            data = parameters
            secret = parameters.get("KILN_NEXUS_PASSWORD", "")
        response = self._request("POST", path, data=data)
        if response.status_code not in {200, 201, 302}:
            raise UpstreamFailure(_failure(response, self._netloc, secret))
        queue = _queue_url(self._base, response.headers.get("location"))
        if secret and queue and secret in queue:
            raise UpstreamFailure(f"Jenkins 调用失败（{self._netloc}）")
        return queue

    def delete_job(self, job_name: str) -> None:
        response = self._request("POST", f"/job/{quote(job_name, safe='')}/doDelete")
        if response.status_code in {200, 204, 302, 404}:
            return
        raise UpstreamFailure(_failure(response, self._netloc))

    def read_progress(
        self,
        job_name: str,
        queue_url: str | None,
        build_number: int | None,
    ) -> tuple[str, int | None]:
        number = build_number
        if number is None and queue_url:
            cancelled, queued_number = self._number_from_queue(queue_url)
            if queued_number is not None:
                number = queued_number
            elif cancelled:
                return "aborted", None
        if number is None:
            number = self._last_build_number(job_name)
        if number is None:
            return "queued", None
        return self._build_status(job_name, number), number

    def read_log(self, job_name: str, build_number: int) -> tuple[str, bool]:
        response = self._request(
            "GET",
            f"/job/{quote(job_name, safe='')}/{build_number}/consoleText",
            headers={"Accept": "text/plain"},
        )
        if response.status_code == 404:
            raise NotFound("Jenkins 上已没有这次构建")
        if response.status_code != 200:
            raise UpstreamFailure(_failure(response, self._netloc))
        text = response.text
        if len(text) <= _LOG_LIMIT:
            return text, False
        return text[-_LOG_LIMIT:], True

    def _last_build_number(self, job_name: str) -> int | None:
        body = self._json(
            f"/job/{quote(job_name, safe='')}/api/json",
            {"tree": "lastBuild[number]"},
        )
        last = body.get("lastBuild")
        if not isinstance(last, dict):
            return None
        number = last.get("number")
        if isinstance(number, int) and number >= 1:
            return number
        return None

    def _build_status(self, job_name: str, build_number: int) -> str:
        body = self._json(
            f"/job/{quote(job_name, safe='')}/{build_number}/api/json",
            {"tree": "building,result"},
        )
        if body.get("building") or body.get("result") is None:
            return "running"
        result = body.get("result")
        if not isinstance(result, str):
            return "unknown"
        return _RESULT.get(result, "unknown")

    def _number_from_queue(self, queue_url: str) -> tuple[bool, int | None]:
        if not queue_url.startswith(self._base + "/"):
            return False, None
        target = queue_url if queue_url.endswith("/") else queue_url + "/"
        response = self._request_url(
            "GET",
            target + "api/json",
            headers={"Accept": "application/json"},
        )
        if response.status_code == 404:
            return False, None
        if response.status_code != 200:
            raise UpstreamFailure(_failure(response, self._netloc))
        body = _object(response, self._netloc)
        executable = body.get("executable")
        if isinstance(executable, dict) and isinstance(executable.get("number"), int):
            number = executable["number"]
            if number >= 1:
                return False, number
        return bool(body.get("cancelled")), None

    def _json(self, path: str, params: dict[str, str]) -> dict[str, object]:
        response = self._request(
            "GET",
            path,
            params=params,
            headers={"Accept": "application/json"},
        )
        if response.status_code == 404:
            raise NotFound("Jenkins 上已没有这个任务")
        if response.status_code != 200:
            raise UpstreamFailure(_failure(response, self._netloc))
        return _object(response, self._netloc)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        content: bytes | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self._request_url(
            method,
            self._base + path,
            params=params,
            content=content,
            data=data,
            headers=headers,
        )

    def _request_url(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        content: bytes | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        retry_crumb: bool = True,
    ) -> httpx.Response:
        if not url.startswith(self._base + "/") and url != self._base:
            raise UpstreamFailure(f"Jenkins 地址超出已配置的控制器（{self._netloc}）")
        extra = dict(headers or {})
        if method.upper() in {"POST", "PUT", "DELETE"}:
            extra.update(self._crumb_headers())
        try:
            response = self._client.request(
                method,
                url,
                params=params,
                content=content,
                data=data,
                headers=extra,
                auth=self._auth,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise UpstreamFailure(f"Jenkins 无法连接（{self._netloc}）") from exc
        if (
            retry_crumb
            and method.upper() in {"POST", "PUT", "DELETE"}
            and response.status_code in {401, 403}
            and "crumb" in response.text.lower()
        ):
            return self._request_url(
                method,
                url,
                params=params,
                content=content,
                data=data,
                headers=headers,
                retry_crumb=False,
            )
        return response

    def _crumb_headers(self) -> dict[str, str]:
        try:
            response = self._client.get(
                self._base + "/crumbIssuer/api/json",
                auth=self._auth,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise UpstreamFailure(f"Jenkins 无法连接（{self._netloc}）") from exc
        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            raise UpstreamFailure(_failure(response, self._netloc))
        body = _object(response, self._netloc)
        field = body.get("crumbRequestField")
        crumb = body.get("crumb")
        if not isinstance(field, str) or not isinstance(crumb, str) or not field or not crumb:
            raise UpstreamFailure(f"Jenkins crumb 无效（{self._netloc}）")
        return {field: crumb}


def _require_marker(marker: str) -> None:
    if _MARKER.fullmatch(marker) is None or any(char in marker for char in "'\"<>&"):
        raise BadRequest("marker 无效")


def _job_config(marker: str) -> str:
    command = escape(f"printf '%s\\n' '{marker}'")
    return (
        "<?xml version='1.1' encoding='UTF-8'?>\n"
        "<project>\n"
        "  <description>kiln compat executor</description>\n"
        "  <keepDependencies>false</keepDependencies>\n"
        "  <properties/>\n"
        '  <scm class="hudson.scm.NullSCM"/>\n'
        "  <canRoam>true</canRoam>\n"
        "  <disabled>false</disabled>\n"
        "  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>\n"
        "  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>\n"
        "  <triggers/>\n"
        "  <concurrentBuild>false</concurrentBuild>\n"
        "  <quietPeriod>0</quietPeriod>\n"
        "  <builders>\n"
        "    <hudson.tasks.Shell>\n"
        f"      <command>{command}</command>\n"
        "    </hudson.tasks.Shell>\n"
        "  </builders>\n"
        "  <publishers/>\n"
        "  <buildWrappers/>\n"
        "</project>\n"
    )


def _queue_url(base: str, location: str | None) -> str | None:
    if not location:
        return None
    if location.startswith("/"):
        location = base + location
    if location.startswith(base + "/queue/"):
        return location
    return None


def _object(response: httpx.Response, netloc: str) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError as exc:
        raise UpstreamFailure(f"Jenkins 返回的不是 JSON（{netloc}）") from exc
    if not isinstance(body, dict):
        raise UpstreamFailure(f"Jenkins 返回的不是 JSON 对象（{netloc}）")
    return body


def _bounded(response: httpx.Response, max_bytes: int, netloc: str) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.strip().isdigit() and int(declared) > max_bytes:
        raise PayloadTooLarge(f"制品字节超过上限（{max_bytes} 字节）")
    data = response.content
    if len(data) > max_bytes:
        raise PayloadTooLarge(f"制品字节超过上限（{max_bytes} 字节）")
    if not data:
        raise UpstreamFailure(f"Jenkins 产出的制品是空文件（{netloc}）")
    return data


def _failure(response: httpx.Response, netloc: str, secret: str = "") -> str:
    snippet = response.text[:180].lower()
    if "crumb" in snippet:
        message = f"Jenkins 拒绝访问（{netloc}，缺少有效 crumb）"
    elif response.status_code in {401, 403}:
        message = f"Jenkins 拒绝访问（{netloc}）"
    else:
        message = f"Jenkins 调用失败（{netloc}，HTTP {response.status_code}）"
    if secret:
        message = message.replace(secret, "***")
    return message
