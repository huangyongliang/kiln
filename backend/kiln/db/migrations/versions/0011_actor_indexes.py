"""Add plain B-tree indexes for actor columns and product-build artifact lookups.

No data change and no unique constraint. Existing rows stay as they are.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_actor_indexes"
down_revision: str | None = "0010_actor_attribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_artifact_versions_created_by",
        "artifact_versions",
        ["created_by"],
    )
    op.create_index(
        "ix_download_reservations_account_id",
        "download_reservations",
        ["account_id"],
    )
    op.create_index(
        "ix_product_builds_requested_by",
        "product_builds",
        ["requested_by"],
    )
    op.create_index(
        "ix_executor_jobs_requested_by",
        "executor_jobs",
        ["requested_by"],
    )
    op.create_index(
        "ix_product_builds_artifact_id",
        "product_builds",
        ["artifact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_builds_artifact_id", table_name="product_builds")
    op.drop_index("ix_executor_jobs_requested_by", table_name="executor_jobs")
    op.drop_index("ix_product_builds_requested_by", table_name="product_builds")
    op.drop_index("ix_download_reservations_account_id", table_name="download_reservations")
    op.drop_index("ix_artifact_versions_created_by", table_name="artifact_versions")
