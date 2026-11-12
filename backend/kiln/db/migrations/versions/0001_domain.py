"""Domain tables for accounts, RBAC, artifacts, reservations, and skills.

The RBAC rows themselves are upserted from kiln.domain.rbac when the API starts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_domain"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rbac_permissions",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_rbac_permissions_position"),
    )
    op.create_table(
        "rbac_roles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_rbac_roles_position"),
    )
    op.create_table(
        "rbac_role_permissions",
        sa.Column("role_id", sa.Text(), sa.ForeignKey("rbac_roles.id"), primary_key=True),
        sa.Column(
            "permission_code",
            sa.Text(),
            sa.ForeignKey("rbac_permissions.code"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_rbac_role_permissions_position"),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username", name="uq_accounts_username"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"),
    )
    op.create_table(
        "account_roles",
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role_id", sa.Text(), sa.ForeignKey("rbac_roles.id"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_account_roles_position"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("group_name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("package_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "package_type IN ('maven', 'python', 'generic', 'frontend')",
            name="ck_artifacts_package_type",
        ),
    )
    op.create_index(
        "uq_artifacts_group_name",
        "artifacts",
        ["group_name", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "artifact_versions",
        sa.Column(
            "artifact_id",
            sa.Uuid(),
            sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("checksum_algorithm", sa.Text(), nullable=False),
        sa.Column("checksum_value", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "checksum_algorithm IN ('sha256', 'sha512')",
            name="ck_artifact_versions_algorithm",
        ),
        sa.CheckConstraint("source IN ('build', 'upload')", name="ck_artifact_versions_source"),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_artifact_versions_size",
        ),
    )
    op.create_table(
        "download_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("package_type", sa.Text(), nullable=False),
        sa.Column("checksum_algorithm", sa.Text(), nullable=False),
        sa.Column("checksum_value", sa.Text(), nullable=False),
        sa.Column("requester", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id", "version"],
            ["artifact_versions.artifact_id", "artifact_versions.version"],
            name="fk_download_reservations_version",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "package_type IN ('maven', 'python', 'generic', 'frontend')",
            name="ck_download_reservations_package_type",
        ),
        sa.CheckConstraint(
            "checksum_algorithm IN ('sha256', 'sha512')",
            name="ck_download_reservations_algorithm",
        ),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("toolchain", sa.Text(), nullable=True),
        sa.Column("environment", sa.Text(), nullable=True),
        sa.Column("output_spec", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_skills_name_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'published')",
            name="ck_skills_status",
        ),
    )
    op.create_table(
        "skill_inputs",
        sa.Column(
            "skill_id",
            sa.Uuid(),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_skill_inputs_position"),
    )
    op.create_table(
        "skill_steps",
        sa.Column(
            "skill_id",
            sa.Uuid(),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_skill_steps_position"),
        sa.CheckConstraint(
            "kind IN ('checkout', 'compile', 'package', 'archive')",
            name="ck_skill_steps_kind",
        ),
    )
    op.create_table(
        "skill_allowed_repositories",
        sa.Column(
            "skill_id",
            sa.Uuid(),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_skill_allowed_repositories_position"),
    )
    op.create_table(
        "skill_allowed_dependencies",
        sa.Column(
            "skill_id",
            sa.Uuid(),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_skill_allowed_dependencies_position"),
    )


def downgrade() -> None:
    op.drop_table("skill_allowed_dependencies")
    op.drop_table("skill_allowed_repositories")
    op.drop_table("skill_steps")
    op.drop_table("skill_inputs")
    op.drop_table("skills")
    op.drop_table("download_reservations")
    op.drop_table("artifact_versions")
    op.drop_index("uq_artifacts_group_name", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("account_roles")
    op.drop_table("accounts")
    op.drop_table("rbac_role_permissions")
    op.drop_table("rbac_roles")
    op.drop_table("rbac_permissions")
