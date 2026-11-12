"""Local account and RBAC outlines. Authentication is not enforced."""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_store
from kiln.domain.accounts import Account, AccountCreate, AccountList
from kiln.domain.rbac import PERMISSIONS, ROLES, PermissionList, RoleList
from kiln.memory import MemoryStore

router = APIRouter(tags=["账号与权限"])


@router.get("/accounts", response_model=AccountList, summary="列出本地账号")
def list_accounts(store: MemoryStore = Depends(get_store)) -> AccountList:
    return AccountList(items=store.list_accounts())


@router.post("/accounts", response_model=Account, status_code=201, summary="创建本地账号")
def create_account(payload: AccountCreate, store: MemoryStore = Depends(get_store)) -> Account:
    return store.create_account(payload)


@router.get("/accounts/{account_id}", response_model=Account, summary="查看本地账号")
def get_account(account_id: UUID, store: MemoryStore = Depends(get_store)) -> Account:
    return store.get_account(account_id)


@router.get("/rbac/permissions", response_model=PermissionList, summary="权限目录")
def list_permissions() -> PermissionList:
    return PermissionList(items=list(PERMISSIONS))


@router.get("/rbac/roles", response_model=RoleList, summary="角色目录")
def list_roles() -> RoleList:
    return RoleList(items=list(ROLES))
