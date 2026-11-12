"""Local accounts and the RBAC catalog.

Calls require a user session or the machine token. Listing and reading an
account require `account.read`. Creating an account requires `account.manage`.
The response never includes a password. Role and permission catalog reads
stay session or machine-token only.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_store
from kiln.auth import require_auth, require_permission
from kiln.db.store import PostgresStore
from kiln.domain.accounts import Account, AccountCreate, AccountList
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


@router.get("/rbac/permissions", response_model=PermissionList, summary="权限目录")
def list_permissions(store: PostgresStore = Depends(get_store)) -> PermissionList:
    return PermissionList(items=store.list_permissions())


@router.get("/rbac/roles", response_model=RoleList, summary="角色目录")
def list_roles(store: PostgresStore = Depends(get_store)) -> RoleList:
    return RoleList(items=store.list_roles())
