"""Store the latest rejection on a skill.

review_note and rejected_at are set together. rejected_by points at the
account that rejected the skill, or stays null for a machine token.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_skill_rejection"
down_revision: str | None = "0005_local_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column(
        "skills",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("skills", sa.Column("rejected_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_skills_rejected_by_accounts",
        "skills",
        "accounts",
        ["rejected_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_skills_rejection_pair",
        "skills",
        "(review_note IS NULL) = (rejected_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_skills_rejection_pair", "skills", type_="check")
    op.drop_constraint("fk_skills_rejected_by_accounts", "skills", type_="foreignkey")
    op.drop_column("skills", "rejected_by")
    op.drop_column("skills", "rejected_at")
    op.drop_column("skills", "review_note")
