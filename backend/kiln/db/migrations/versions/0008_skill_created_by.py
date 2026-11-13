"""Record which account created a skill.

created_by is nullable. Existing rows stay null because the author cannot be
recovered. Deleting the account sets the column to null, matching rejected_by.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_skill_created_by"
down_revision: str | None = "0007_product_build_skill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("skills", sa.Column("created_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_skills_created_by_accounts",
        "skills",
        "accounts",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_skills_created_by_accounts", "skills", type_="foreignkey")
    op.drop_column("skills", "created_by")
