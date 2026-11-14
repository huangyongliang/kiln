"""Retrieve API. Metadata stays JSON; the content path returns stored bytes."""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from kiln.api.deps import get_store
from kiln.auth import require_auth, require_permission
from kiln.db.store import PostgresStore
from kiln.domain.artifacts import RetrieveResult

router = APIRouter(tags=["取包"], dependencies=[Depends(require_auth)])


@router.get(
    "/retrieve/{reservation_id}",
    response_model=RetrieveResult,
    summary="按预约查看取包信息",
    dependencies=[Depends(require_permission("artifact.download"))],
)
def retrieve(
    reservation_id: UUID,
    store: PostgresStore = Depends(get_store),
) -> RetrieveResult:
    return store.get_retrieve(reservation_id)


@router.get(
    "/retrieve/{reservation_id}/content",
    summary="取回制品字节",
    response_class=Response,
    dependencies=[Depends(require_permission("artifact.download"))],
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
            "description": (
                "该版本已保存的制品字节。"
                "响应头 X-Kiln-Source 由平台按该版本实际 source 填写，值为 build 或 upload，"
                "不读取请求输入。"
                "PUT /artifacts/{id}/versions/{version}/content 上的同名请求头被忽略，不决定来源。"
            ),
            "headers": {
                "X-Kiln-Source": {
                    "description": (
                        "平台按该版本存储的 source 填写，值为 build 或 upload，不读取请求输入。"
                    ),
                    "schema": {"type": "string", "enum": ["build", "upload"]},
                }
            },
        }
    },
)
def retrieve_content(
    reservation_id: UUID,
    store: PostgresStore = Depends(get_store),
) -> Response:
    stored = store.read_reserved_bytes(reservation_id)
    filename = f"{stored.name}-{stored.version}"
    return Response(
        content=stored.data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Kiln-Checksum-Algorithm": stored.algorithm,
            "X-Kiln-Checksum": stored.digest,
            "X-Kiln-Content-Available": "true",
            "X-Kiln-Source": stored.source,
        },
    )
