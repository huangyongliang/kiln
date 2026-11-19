"""Build package type catalog.

GET without include_disabled returns enabled rows and requires skill.read.
Managing the catalog requires build.catalog.manage. A caller missing that
permission gets 403, and the detail names build.catalog.manage.
"""

from fastapi import APIRouter, Depends, Query, Response

from kiln.api.deps import get_store
from kiln.auth import Principal, require_auth, require_permission
from kiln.db.store import PostgresStore
from kiln.domain.package_types import (
    BuildPackageType,
    BuildPackageTypeCreate,
    BuildPackageTypeList,
    BuildPackageTypeUpdate,
)
from kiln.errors import Forbidden

router = APIRouter(
    prefix="/build-package-types",
    tags=["构建类型"],
    dependencies=[Depends(require_auth)],
)

_CATALOG_FORBIDDEN = "没有权限（需要：build.catalog.manage）"


@router.get("", response_model=BuildPackageTypeList, summary="列出构建类型")
def list_build_package_types(
    include_disabled: bool = Query(default=False),
    principal: Principal = Depends(require_auth),
    store: PostgresStore = Depends(get_store),
) -> BuildPackageTypeList:
    """Enabled rows for skill.read. The full catalog requires build.catalog.manage."""

    if include_disabled:
        if "build.catalog.manage" not in principal.permissions:
            raise Forbidden(_CATALOG_FORBIDDEN)
        items = store.list_build_package_types(enabled_only=False)
    else:
        if "skill.read" not in principal.permissions:
            raise Forbidden()
        items = store.list_build_package_types(enabled_only=True)
    return BuildPackageTypeList(items=items)


@router.post(
    "",
    response_model=BuildPackageType,
    status_code=201,
    summary="新增构建类型",
    dependencies=[Depends(require_permission("build.catalog.manage", detail=_CATALOG_FORBIDDEN))],
)
def create_build_package_type(
    payload: BuildPackageTypeCreate,
    store: PostgresStore = Depends(get_store),
) -> BuildPackageType:
    return store.create_build_package_type(payload)


@router.put(
    "/{code}",
    response_model=BuildPackageType,
    summary="更新构建类型",
    dependencies=[Depends(require_permission("build.catalog.manage", detail=_CATALOG_FORBIDDEN))],
)
def update_build_package_type(
    code: str,
    payload: BuildPackageTypeUpdate,
    store: PostgresStore = Depends(get_store),
) -> BuildPackageType:
    return store.update_build_package_type(code, payload)


@router.delete(
    "/{code}",
    status_code=204,
    summary="删除未被引用的构建类型",
    dependencies=[Depends(require_permission("build.catalog.manage", detail=_CATALOG_FORBIDDEN))],
)
def delete_build_package_type(code: str, store: PostgresStore = Depends(get_store)) -> Response:
    store.delete_build_package_type(code)
    return Response(status_code=204)
