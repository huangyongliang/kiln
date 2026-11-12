"""Record which artifact versions have platform-owned bytes.

The bytes themselves stay in the local directory from KILN_ARTIFACT_ROOT.
This table stores only the digest key, checksum, and availability flag.
Nexus is not this storage.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_artifact_bytes"
down_revision: str | None = "0002_executor_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "artifact_versions",
        sa.Column(
            "content_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "artifact_versions",
        sa.Column("blob_key", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_artifact_versions_content",
        "artifact_versions",
        "(content_available = false AND blob_key IS NULL) OR "
        "(content_available = true AND blob_key IS NOT NULL AND size_bytes IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_artifact_versions_content", "artifact_versions", type_="check")
    op.drop_column("artifact_versions", "blob_key")
    op.drop_column("artifact_versions", "content_available")
