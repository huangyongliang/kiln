"""Shared HTTP reads for git adapters. Tokens stay in headers, not URLs."""

from urllib.parse import urlsplit

import httpx

from kiln.errors import NotFound, UpstreamFailure


def read_json(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    params: dict[str, str | int] | None = None,
) -> object:
    netloc = urlsplit(url).netloc
    try:
        response = client.get(
            url,
            headers=headers,
            params=params,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise UpstreamFailure(f"代码源无法连接（{netloc}）") from exc
    if response.status_code == 404:
        raise NotFound("仓库或引用不存在")
    if response.status_code in {401, 403}:
        raise UpstreamFailure(f"代码源拒绝访问（{netloc}）")
    if response.status_code != 200:
        raise UpstreamFailure(f"代码源调用失败（{netloc}，HTTP {response.status_code}）")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamFailure(f"代码源返回的不是 JSON（{netloc}）") from exc


def as_list(payload: object, netloc: str) -> list[object]:
    if not isinstance(payload, list):
        raise UpstreamFailure(f"代码源返回的不是仓库列表（{netloc}）")
    return payload


def as_object(payload: object, netloc: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise UpstreamFailure(f"代码源返回的不是 JSON 对象（{netloc}）")
    return payload


def require_sha(value: object, netloc: str) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise UpstreamFailure(f"代码源没有返回提交号（{netloc}）")
    return value
