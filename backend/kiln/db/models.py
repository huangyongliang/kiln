"""Relational schema for accounts, RBAC, artifacts, reservations, and skills."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PermissionRow(Base):
    __tablename__ = "rbac_permissions"
    __table_args__ = (CheckConstraint("position >= 0", name="ck_rbac_permissions_position"),)

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class RoleRow(Base):
    __tablename__ = "rbac_roles"
    __table_args__ = (CheckConstraint("position >= 0", name="ck_rbac_roles_position"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    grants: Mapped[list[RolePermissionRow]] = relationship(
        back_populates="role",
        order_by="RolePermissionRow.position",
        cascade="all, delete-orphan",
    )


class RolePermissionRow(Base):
    __tablename__ = "rbac_role_permissions"
    __table_args__ = (CheckConstraint("position >= 0", name="ck_rbac_role_permissions_position"),)

    role_id: Mapped[str] = mapped_column(ForeignKey("rbac_roles.id"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("rbac_permissions.code"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[RoleRow] = relationship(back_populates="grants")


class AccountRow(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("username", name="uq_accounts_username"),
        UniqueConstraint("idp_issuer", "idp_subject", name="uq_accounts_idp_issuer_subject"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"),
        CheckConstraint(
            "(idp_issuer IS NULL AND idp_subject IS NULL) OR "
            "(idp_issuer IS NOT NULL AND idp_subject IS NOT NULL)",
            name="ck_accounts_idp_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    idp_issuer: Mapped[str | None] = mapped_column(Text)
    idp_subject: Mapped[str | None] = mapped_column(Text)
    employment_status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    roles: Mapped[list[AccountRoleRow]] = relationship(
        back_populates="account",
        order_by="AccountRoleRow.position",
        cascade="all, delete-orphan",
    )


class AccountRoleRow(Base):
    __tablename__ = "account_roles"
    __table_args__ = (CheckConstraint("position >= 0", name="ck_account_roles_position"),)

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(ForeignKey("rbac_roles.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    account: Mapped[AccountRow] = relationship(back_populates="roles")


class SessionRow(Base):
    """A user session. Only the token hash is stored."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index(
            "uq_artifacts_group_name",
            "group_name",
            "name",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "package_type IN ('maven', 'python', 'generic', 'frontend')",
            name="ck_artifacts_package_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    group_name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    package_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    versions: Mapped[list[ArtifactVersionRow]] = relationship(
        back_populates="artifact",
        order_by="ArtifactVersionRow.created_at",
        cascade="all, delete-orphan",
    )


class ArtifactVersionRow(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        CheckConstraint(
            "checksum_algorithm IN ('sha256', 'sha512')",
            name="ck_artifact_versions_algorithm",
        ),
        CheckConstraint(
            "source IN ('build', 'upload')",
            name="ck_artifact_versions_source",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_artifact_versions_size",
        ),
        CheckConstraint(
            "(content_available = false AND blob_key IS NULL) OR "
            "(content_available = true AND blob_key IS NOT NULL AND size_bytes IS NOT NULL)",
            name="ck_artifact_versions_content",
        ),
    )

    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    checksum_algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_value: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    content_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blob_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    artifact: Mapped[ArtifactRow] = relationship(back_populates="versions")


class ReservationRow(Base):
    __tablename__ = "download_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["artifact_id", "version"],
            ["artifact_versions.artifact_id", "artifact_versions.version"],
            name="fk_download_reservations_version",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "package_type IN ('maven', 'python', 'generic', 'frontend')",
            name="ck_download_reservations_package_type",
        ),
        CheckConstraint(
            "checksum_algorithm IN ('sha256', 'sha512')",
            name="ck_download_reservations_algorithm",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    artifact_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    artifact_name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    package_type: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_value: Mapped[str] = mapped_column(Text, nullable=False)
    requester: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SkillRow(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_skills_name_version"),
        CheckConstraint(
            "status IN ('draft', 'in_review', 'published')",
            name="ck_skills_status",
        ),
        CheckConstraint(
            "(review_note IS NULL) = (rejected_at IS NULL)",
            name="ck_skills_rejection_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    toolchain: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str | None] = mapped_column(Text)
    output_spec: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "accounts.id",
            name="fk_skills_rejected_by_accounts",
            ondelete="SET NULL",
        ),
    )
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "accounts.id",
            name="fk_skills_created_by_accounts",
            ondelete="SET NULL",
        ),
    )
    inputs: Mapped[list[SkillInputRow]] = relationship(
        back_populates="skill",
        order_by="SkillInputRow.position",
        cascade="all, delete-orphan",
    )
    steps: Mapped[list[SkillStepRow]] = relationship(
        back_populates="skill",
        order_by="SkillStepRow.position",
        cascade="all, delete-orphan",
    )
    allowed_repositories: Mapped[list[SkillRepositoryRow]] = relationship(
        back_populates="skill",
        order_by="SkillRepositoryRow.position",
        cascade="all, delete-orphan",
    )
    allowed_dependencies: Mapped[list[SkillDependencyRow]] = relationship(
        back_populates="skill",
        order_by="SkillDependencyRow.position",
        cascade="all, delete-orphan",
    )


class SkillInputRow(Base):
    __tablename__ = "skill_inputs"
    __table_args__ = (CheckConstraint("position >= 0", name="ck_skill_inputs_position"),)

    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    skill: Mapped[SkillRow] = relationship(back_populates="inputs")


class SkillStepRow(Base):
    __tablename__ = "skill_steps"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_skill_steps_position"),
        CheckConstraint(
            "kind IN ('checkout', 'compile', 'package', 'archive')",
            name="ck_skill_steps_kind",
        ),
    )

    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    skill: Mapped[SkillRow] = relationship(back_populates="steps")


class SkillRepositoryRow(Base):
    __tablename__ = "skill_allowed_repositories"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_skill_allowed_repositories_position"),
    )

    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    skill: Mapped[SkillRow] = relationship(back_populates="allowed_repositories")


class SkillDependencyRow(Base):
    __tablename__ = "skill_allowed_dependencies"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_skill_allowed_dependencies_position"),
    )

    skill_id: Mapped[UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    skill: Mapped[SkillRow] = relationship(back_populates="allowed_dependencies")


class ProductBuildRow(Base):
    """A Jenkins product build. Bytes live in artifact storage, not in this row.

    The row does not store a git token and does not deploy to a runtime.
    ``skill_id`` is null only for rows created before a published skill was
    required. New rows set it. Deleting a skill is out of scope.
    """

    __tablename__ = "product_builds"
    __table_args__ = (
        UniqueConstraint("jenkins_job_name", name="uq_product_builds_jenkins_job_name"),
        CheckConstraint(
            "status IN ("
            "'queued', 'running', 'success', 'failure', "
            "'unstable', 'aborted', 'not_built', 'unknown'"
            ")",
            name="ck_product_builds_status",
        ),
        CheckConstraint(
            "build_number IS NULL OR build_number >= 1",
            name="ck_product_builds_build_number",
        ),
        CheckConstraint(
            "package_type IN ('maven', 'python', 'generic', 'frontend')",
            name="ck_product_builds_package_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    jenkins_job_name: Mapped[str] = mapped_column(Text, nullable=False)
    repository: Mapped[str] = mapped_column(Text, nullable=False)
    git_ref: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    package_type: Mapped[str] = mapped_column(Text, nullable=False)
    queue_url: Mapped[str | None] = mapped_column(Text)
    build_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    content_stored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skill_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skills.id", name="fk_product_builds_skill_id_skills"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutorJobRow(Base):
    """A Jenkins build tracked by kiln. The row does not deploy anything."""

    __tablename__ = "executor_jobs"
    __table_args__ = (
        UniqueConstraint("jenkins_job_name", name="uq_executor_jobs_jenkins_job_name"),
        CheckConstraint(
            "status IN ("
            "'queued', 'running', 'success', 'failure', "
            "'unstable', 'aborted', 'not_built', 'unknown'"
            ")",
            name="ck_executor_jobs_status",
        ),
        CheckConstraint(
            "build_number IS NULL OR build_number >= 1",
            name="ck_executor_jobs_build_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    jenkins_job_name: Mapped[str] = mapped_column(Text, nullable=False)
    marker: Mapped[str] = mapped_column(Text, nullable=False)
    queue_url: Mapped[str | None] = mapped_column(Text)
    build_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LlmCatalogStateRow(Base):
    """Single row. source=env is unmanaged; source=db is the saved catalog.

    An empty model table together with source=db means an administrator saved
    an empty catalog. A missing row is treated as env. Downgrade drops the row.
    """

    __tablename__ = "llm_catalog_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_llm_catalog_state_singleton"),
        CheckConstraint("source IN ('env', 'db')", name="ck_llm_catalog_state_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)


class LlmCatalogModelRow(Base):
    """One platform model id. No endpoint and no secret is stored."""

    __tablename__ = "llm_catalog_models"
    __table_args__ = (
        CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 128",
            name="ck_llm_catalog_models_id_len",
        ),
        CheckConstraint(
            "display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 128",
            name="ck_llm_catalog_models_display_name_len",
        ),
        CheckConstraint("position >= 0", name="ck_llm_catalog_models_position"),
        Index(
            "uq_llm_catalog_models_one_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LlmRoleModelRow(Base):
    """Models assigned to one of the four fixed roles. Not an RBAC grant."""

    __tablename__ = "llm_role_models"
    __table_args__ = (
        CheckConstraint(
            "role_id IN ('reader', 'builder', 'publisher', 'admin')",
            name="ck_llm_role_models_role",
        ),
        CheckConstraint("position >= 0", name="ck_llm_role_models_position"),
        Index(
            "uq_llm_role_models_one_default",
            "role_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    role_id: Mapped[str] = mapped_column(Text, primary_key=True)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("llm_catalog_models.model_id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
