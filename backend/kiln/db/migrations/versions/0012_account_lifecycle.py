"""Record the last account change and index sessions by account.

``updated_at`` is backfilled from ``created_at``, then made NOT NULL with a
``now()`` server default. Inserts still set the column explicitly. ``updated_by``
follows the S1 actor columns: nullable, and deleting the actor sets it null.
``ix_sessions_account_id`` supports revoking one account's sessions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_account_lifecycle"
down_revision: str | None = "0011_actor_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE accounts SET updated_at = created_at")
    op.alter_column(
        "accounts",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.add_column("accounts", sa.Column("updated_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_accounts_updated_by_accounts",
        "accounts",
        "accounts",
        ["updated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_accounts_updated_by", "accounts", ["updated_by"])
    op.create_index("ix_sessions_account_id", "sessions", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_account_id", table_name="sessions")
    op.drop_index("ix_accounts_updated_by", table_name="accounts")
    op.drop_constraint("fk_accounts_updated_by_accounts", "accounts", type_="foreignkey")
    op.drop_column("accounts", "updated_by")
    op.drop_column("accounts", "updated_at")
