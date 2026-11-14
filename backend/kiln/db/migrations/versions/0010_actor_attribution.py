"""Record which account started a build or job, registered a version, or reserved a download.

The four columns are nullable. NULL means a machine token, a row written
before this migration, or a version stored by in-process build collection.
Existing rows are not backfilled. Deleting the account sets the column to
null and does not delete the business row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_actor_attribution"
down_revision: str | None = "0009_llm_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product_builds", sa.Column("requested_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_product_builds_requested_by_accounts",
        "product_builds",
        "accounts",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("executor_jobs", sa.Column("requested_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_executor_jobs_requested_by_accounts",
        "executor_jobs",
        "accounts",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("artifact_versions", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_artifact_versions_created_by_accounts",
        "artifact_versions",
        "accounts",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("download_reservations", sa.Column("account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_download_reservations_account_id_accounts",
        "download_reservations",
        "accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_download_reservations_account_id_accounts",
        "download_reservations",
        type_="foreignkey",
    )
    op.drop_column("download_reservations", "account_id")
    op.drop_constraint(
        "fk_artifact_versions_created_by_accounts",
        "artifact_versions",
        type_="foreignkey",
    )
    op.drop_column("artifact_versions", "created_by")
    op.drop_constraint(
        "fk_executor_jobs_requested_by_accounts",
        "executor_jobs",
        type_="foreignkey",
    )
    op.drop_column("executor_jobs", "requested_by")
    op.drop_constraint(
        "fk_product_builds_requested_by_accounts",
        "product_builds",
        type_="foreignkey",
    )
    op.drop_column("product_builds", "requested_by")
