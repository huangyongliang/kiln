"""Local password hashes, external subject link, and user sessions.

Sessions store a hash of the bearer token. Synced accounts keep the issuer
and subject so 用户信息同步 can find them again. Permissions stay in kiln.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_local_login"
down_revision: str | None = "0004_product_builds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column("accounts", sa.Column("idp_issuer", sa.Text(), nullable=True))
    op.add_column("accounts", sa.Column("idp_subject", sa.Text(), nullable=True))
    op.add_column("accounts", sa.Column("employment_status", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_accounts_idp_issuer_subject",
        "accounts",
        ["idp_issuer", "idp_subject"],
    )
    op.create_check_constraint(
        "ck_accounts_idp_pair",
        "accounts",
        "(idp_issuer IS NULL AND idp_subject IS NULL) OR "
        "(idp_issuer IS NOT NULL AND idp_subject IS NOT NULL)",
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"),
    )


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_constraint("ck_accounts_idp_pair", "accounts", type_="check")
    op.drop_constraint("uq_accounts_idp_issuer_subject", "accounts", type_="unique")
    op.drop_column("accounts", "employment_status")
    op.drop_column("accounts", "idp_subject")
    op.drop_column("accounts", "idp_issuer")
    op.drop_column("accounts", "password_hash")
