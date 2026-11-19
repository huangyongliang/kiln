"""Local accounts and the RBAC catalog.

Calls require a user session or the machine token. Listing and reading an
account require `account.read`. Creating an account requires `account.manage`.
The response never includes a password. Role and permission catalog reads
stay session or machine-token only.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from kiln.api.deps import get_store
from kiln.auth import Principal, require_auth, require_permission
from kiln.db.store import PostgresStore
from kiln.domain.accounts import Account, AccountCreate, AccountList, AccountUpdate, PasswordReset
from kiln.domain.rbac import PermissionList, RoleList

router = APIRouter(tags=["账号与权限"], dependencies=[Depends(require_auth)])


@router.get(
    "/accounts",
    response_model=AccountList,
    summary="列出本地账号",
    dependencies=[Depends(require_permission("account.read"))],
)
def list_accounts(store: PostgresStore = Depends(get_store)) -> AccountList:
    return AccountList(items=store.list_accounts())


@router.post(
    "/accounts",
    response_model=Account,
    status_code=201,
    summary="创建本地账号",
    dependencies=[Depends(require_permission("account.manage"))],
)
def create_account(
    payload: AccountCreate,
    store: PostgresStore = Depends(get_store),
) -> Account:
    return store.create_account(payload)


@router.get(
    "/accounts/{account_id}",
    response_model=Account,
    summary="查看本地账号",
    dependencies=[Depends(require_permission("account.read"))],
)
def get_account(account_id: UUID, store: PostgresStore = Depends(get_store)) -> Account:
    return store.get_account(account_id)


@router.patch(
    "/accounts/{account_id}",
    response_model=Account,
    summary="修改账号状态或角色",
)
def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    principal: Principal = Depends(require_permission("account.manage")),
    store: PostgresStore = Depends(get_store),
) -> Account:
    actor_id = None if principal.account is None else principal.account.id
    self_edit = actor_id is not None and actor_id == account_id
    return store.update_account(
        account_id,
        status=payload.status,
        role_ids=payload.role_ids,
        actor_id=actor_id,
        self_edit=self_edit,
    )


@router.post(
    "/accounts/{account_id}/password",
    status_code=204,
    summary="重置账号口令",
)
def reset_password(
    account_id: UUID,
    payload: PasswordReset,
    principal: Principal = Depends(require_permission("account.manage")),
    store: PostgresStore = Depends(get_store),
) -> Response:
    keep_hash = None
    if principal.account is not None and principal.account.id == account_id:
        keep_hash = principal.session_hash
    actor_id = None if principal.account is None else principal.account.id
    store.reset_password(account_id, payload.password, actor_id=actor_id, keep_hash=keep_hash)
    return Response(status_code=204)


@router.get("/rbac/permissions", response_model=PermissionList, summary="权限目录")
def list_permissions(store: PostgresStore = Depends(get_store)) -> PermissionList:
    return PermissionList(items=store.list_permissions())


@router.get("/rbac/roles", response_model=RoleList, summary="角色目录")
def list_roles(store: PostgresStore = Depends(get_store)) -> RoleList:
    return RoleList(items=store.list_roles())
