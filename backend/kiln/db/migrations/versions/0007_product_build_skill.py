"""Link a product build to the skill that authorized it.

skill_id is nullable so rows created before this revision stay readable.
New builds set it. Deleting a skill is out of scope.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_product_build_skill"
down_revision: str | None = "0006_skill_rejection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product_builds", sa.Column("skill_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_product_builds_skill_id_skills",
        "product_builds",
        "skills",
        ["skill_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_product_builds_skill_id_skills",
        "product_builds",
        type_="foreignkey",
    )
    op.drop_column("product_builds", "skill_id")
