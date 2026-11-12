"""Jenkins compat jobs tracked by kiln.

The table stores identifiers and status. It does not store credentials or
artifact bytes, and nothing in this schema deploys to a runtime.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_executor_jobs"
down_revision: str | None = "0001_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "executor_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("jenkins_job_name", sa.Text(), nullable=False),
        sa.Column("marker", sa.Text(), nullable=False),
        sa.Column("queue_url", sa.Text(), nullable=True),
        sa.Column("build_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("jenkins_job_name", name="uq_executor_jobs_jenkins_job_name"),
        sa.CheckConstraint(
            "status IN ("
            "'queued', 'running', 'success', 'failure', "
            "'unstable', 'aborted', 'not_built', 'unknown'"
            ")",
            name="ck_executor_jobs_status",
        ),
        sa.CheckConstraint(
            "build_number IS NULL OR build_number >= 1",
            name="ck_executor_jobs_build_number",
        ),
    )


def downgrade() -> None:
    op.drop_table("executor_jobs")
