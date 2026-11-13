"""Read and replace the model catalog in one transaction.

``source=db`` is the only managed state. A missing row, or a row whose source
is ``env``, means the process still follows ``LLM_MODEL`` / ``LLM_MODELS``.
Saving an empty catalog still writes ``source=db``. There is no ETag: a later
replace overwrites the earlier one.
"""

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kiln.db.models import LlmCatalogModelRow, LlmCatalogStateRow, LlmRoleModelRow
from kiln.domain.llm_catalog import (
    ROLE_ORDER,
    CatalogModel,
    CatalogSnapshot,
    RoleAssignment,
)


class LlmCatalogStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def read(self) -> CatalogSnapshot | None:
        """Return the saved catalog, or None when the deployment is unmanaged."""

        with Session(self.engine) as session:
            state = session.get(LlmCatalogStateRow, 1)
            if state is None or state.source != "db":
                return None
            model_rows = session.scalars(
                select(LlmCatalogModelRow).order_by(
                    LlmCatalogModelRow.position,
                    LlmCatalogModelRow.model_id,
                )
            ).all()
            link_rows = session.scalars(
                select(LlmRoleModelRow).order_by(
                    LlmRoleModelRow.position,
                    LlmRoleModelRow.model_id,
                )
            ).all()
        models = tuple(
            CatalogModel(id=row.model_id, display_name=row.display_name) for row in model_rows
        )
        default_model = next((row.model_id for row in model_rows if row.is_default), None)
        grouped: dict[str, list[LlmRoleModelRow]] = {role_id: [] for role_id in ROLE_ORDER}
        for row in link_rows:
            grouped.setdefault(row.role_id, []).append(row)
        assignments: list[RoleAssignment] = []
        catalog_ids = {item.id for item in models}
        for role_id in ROLE_ORDER:
            rows = grouped.get(role_id, [])
            chosen = tuple(row.model_id for row in rows if row.model_id in catalog_ids)
            role_default = next(
                (row.model_id for row in rows if row.is_default and row.model_id in chosen),
                None,
            )
            assignments.append(RoleAssignment(role_id, chosen, role_default))
        return CatalogSnapshot(
            source="db",
            models=models,
            default_model=default_model if default_model in catalog_ids else None,
            assignments=tuple(assignments),
        )

    def replace(self, snapshot: CatalogSnapshot) -> None:
        """Replace models, assignments, and managed state in one transaction."""

        if snapshot.source != "db":
            raise RuntimeError("a catalog replace must be stored as source=db")
        with Session(self.engine) as session:
            with session.begin():
                session.execute(delete(LlmRoleModelRow))
                session.execute(delete(LlmCatalogModelRow))
                for position, model in enumerate(snapshot.models):
                    session.add(
                        LlmCatalogModelRow(
                            model_id=model.id,
                            display_name=model.display_name,
                            position=position,
                            is_default=model.id == snapshot.default_model,
                        )
                    )
                session.flush()
                for assignment in snapshot.assignments:
                    for position, model_id in enumerate(assignment.models):
                        session.add(
                            LlmRoleModelRow(
                                role_id=assignment.role_id,
                                model_id=model_id,
                                position=position,
                                is_default=model_id == assignment.default_model,
                            )
                        )
                state = session.get(LlmCatalogStateRow, 1)
                if state is None:
                    session.add(LlmCatalogStateRow(id=1, source="db"))
                else:
                    state.source = "db"
