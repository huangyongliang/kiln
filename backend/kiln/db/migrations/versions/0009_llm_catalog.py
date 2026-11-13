"""Model catalog, per-role assignment, and an explicit managed flag.

Downgrade drops the three tables. A saved catalog is not kept, and the
process returns to LLM_MODEL / LLM_MODELS. The migration does not store an
endpoint or a secret.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_llm_catalog"
down_revision: str | None = "0008_skill_created_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_catalog_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_llm_catalog_state_singleton"),
        sa.CheckConstraint("source IN ('env', 'db')", name="ck_llm_catalog_state_source"),
    )
    op.execute("INSERT INTO llm_catalog_state (id, source) VALUES (1, 'env')")
    op.create_table(
        "llm_catalog_models",
        sa.Column("model_id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 128",
            name="ck_llm_catalog_models_id_len",
        ),
        sa.CheckConstraint(
            "display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 128",
            name="ck_llm_catalog_models_display_name_len",
        ),
        sa.CheckConstraint("position >= 0", name="ck_llm_catalog_models_position"),
    )
    op.create_index(
        "uq_llm_catalog_models_one_default",
        "llm_catalog_models",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "llm_role_models",
        sa.Column("role_id", sa.Text(), primary_key=True),
        sa.Column("model_id", sa.Text(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "role_id IN ('reader', 'builder', 'publisher', 'admin')",
            name="ck_llm_role_models_role",
        ),
        sa.CheckConstraint("position >= 0", name="ck_llm_role_models_position"),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_catalog_models.model_id"],
            name="fk_llm_role_models_model_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "uq_llm_role_models_one_default",
        "llm_role_models",
        ["role_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_role_models_one_default", table_name="llm_role_models")
    op.drop_table("llm_role_models")
    op.drop_index("uq_llm_catalog_models_one_default", table_name="llm_catalog_models")
    op.drop_table("llm_catalog_models")
    op.drop_table("llm_catalog_state")
