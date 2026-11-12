"""Dependency proxy status.

Reading it requires ``executor.read``. The password is not returned. When the
proxy is unset this route makes no outbound call. A failed probe is still HTTP
200 with ``reachable`` false. Nothing here publishes artifact bytes.
"""

from fastapi import APIRouter, Depends

from kiln.api.deps import get_nexus, get_settings
from kiln.auth import require_auth, require_permission
from kiln.config import Settings
from kiln.domain.dependency_proxy import DependencyProxyStatus
from kiln.errors import NEXUS_UNCONFIGURED_DETAIL, UpstreamFailure
from kiln.nexus.client import NexusClient

router = APIRouter(tags=["依赖代理"], dependencies=[Depends(require_auth)])


@router.get(
    "/dependency-proxy",
    response_model=DependencyProxyStatus,
    summary="依赖代理状态",
    dependencies=[Depends(require_permission("executor.read"))],
)
def dependency_proxy_status(
    settings: Settings = Depends(get_settings),
    nexus: NexusClient | None = Depends(get_nexus),
) -> DependencyProxyStatus:
    return read_status(settings, nexus)


def read_status(settings: Settings, nexus: NexusClient | None) -> DependencyProxyStatus:
    if not settings.nexus_configured:
        return DependencyProxyStatus(
            kind="off",
            configured=False,
            base_url=None,
            repository=None,
            auth=None,
            reachable=None,
            detail=NEXUS_UNCONFIGURED_DETAIL,
            publishes_artifacts=False,
        )
    if nexus is None:
        return _unreachable(settings, "依赖代理客户端未初始化")
    try:
        nexus.probe()
    except UpstreamFailure as exc:
        return _unreachable(settings, exc.detail)
    return _status(settings, reachable=True, detail=None)


def _unreachable(settings: Settings, detail: str) -> DependencyProxyStatus:
    secret = settings.nexus_password_value()
    return _status(settings, reachable=False, detail=_redact(detail, secret))


def _status(settings: Settings, *, reachable: bool, detail: str | None) -> DependencyProxyStatus:
    auth = "credentials" if settings.kiln_nexus_username else "anonymous"
    return DependencyProxyStatus(
        kind="nexus",
        configured=True,
        base_url=settings.kiln_nexus_url,
        repository=settings.kiln_nexus_repository,
        auth=auth,
        reachable=reachable,
        detail=detail,
        publishes_artifacts=False,
    )


def _redact(detail: str, secret: str) -> str:
    if secret:
        return detail.replace(secret, "***")
    return detail
