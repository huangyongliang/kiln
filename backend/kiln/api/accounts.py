"""Local account and RBAC outlines.

Calls require the local API token. Roles are not enforced. Passwords, login,
and identity federation are not implemented.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_store
from kiln.auth import require_api_token
from kiln.db.store import PostgresStore
from kiln.domain.accounts import Account, AccountCreate, AccountList
from kiln.domain.rbac import PermissionList, RoleList

router = APIRouter(tags=["账号与权限"], dependencies=[Depends(require_api_token)])


@router.get("/accounts", response_model=AccountList, summary="列出本地账号")
def list_accounts(store: PostgresStore = Depends(get_store)) -> AccountList:
    return AccountList(items=store.list_accounts())


@router.post("/accounts", response_model=Account, status_code=201, summary="创建本地账号")
def create_account(
    payload: AccountCreate,
    store: PostgresStore = Depends(get_store),
) -> Account:
    return store.create_account(payload)


@router.get("/accounts/{account_id}", response_model=Account, summary="查看本地账号")
def get_account(account_id: UUID, store: PostgresStore = Depends(get_store)) -> Account:
    return store.get_account(account_id)


@router.get("/rbac/permissions", response_model=PermissionList, summary="权限目录")
def list_permissions(store: PostgresStore = Depends(get_store)) -> PermissionList:
    return PermissionList(items=store.list_permissions())


@router.get("/rbac/roles", response_model=RoleList, summary="角色目录")
def list_roles(store: PostgresStore = Depends(get_store)) -> RoleList:
    return RoleList(items=store.list_roles())
