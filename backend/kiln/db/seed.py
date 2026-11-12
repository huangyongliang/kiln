"""Upsert the closed RBAC catalog into PostgreSQL.

The catalog definition stays in kiln.domain.rbac. Startup writes it so the API
reads roles and permissions from the database.
"""

from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kiln.db.models import PermissionRow, RolePermissionRow, RoleRow
from kiln.domain.rbac import PERMISSIONS, ROLES


def seed_rbac(engine: Engine) -> None:
    with Session(engine) as session:
        for index, permission in enumerate(PERMISSIONS):
            row = session.get(PermissionRow, permission.code)
            if row is None:
                session.add(
                    PermissionRow(
                        code=permission.code,
                        description=permission.description,
                        position=index,
                    )
                )
            else:
                row.description = permission.description
                row.position = index
        for index, role in enumerate(ROLES):
            row = session.get(RoleRow, role.id)
            if row is None:
                session.add(
                    RoleRow(
                        id=role.id,
                        name=role.name,
                        description=role.description,
                        position=index,
                    )
                )
            else:
                row.name = role.name
                row.description = role.description
                row.position = index
        session.flush()
        for role in ROLES:
            session.execute(delete(RolePermissionRow).where(RolePermissionRow.role_id == role.id))
            for index, code in enumerate(role.permissions):
                session.add(
                    RolePermissionRow(
                        role_id=role.id,
                        permission_code=code,
                        position=index,
                    )
                )
        session.commit()
