"""Product builds that store Jenkins output in the platform catalog.

The table keeps the git commit and the artifact version. It does not store
credentials, artifact bytes, or a runtime deployment.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_product_builds"
down_revision: str | None = "0003_artifact_bytes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_builds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("jenkins_job_name", sa.Text(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("git_ref", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("package_type", sa.Text(), nullable=False),
        sa.Column("queue_url", sa.Text(), nullable=True),
        sa.Column("build_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("content_stored", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("jenkins_job_name", name="uq_product_builds_jenkins_job_name"),
        sa.CheckConstraint(
            "status IN ("
            "'queued', 'running', 'success', 'failure', "
            "'unstable', 'aborted', 'not_built', 'unknown'"
            ")",
            name="ck_product_builds_status",
        ),
        sa.CheckConstraint(
            "build_number IS NULL OR build_number >= 1",
            name="ck_product_builds_build_number",
        ),
        sa.CheckConstraint(
            "package_type IN ('maven', 'python', 'generic', 'frontend')",
            name="ck_product_builds_package_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("product_builds")
