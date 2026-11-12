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
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PermissionRow(Base):
    __tablename__ = "rbac_permissions"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_rbac_permissions_position"),
    )

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
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_rbac_role_permissions_position"),
    )

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
        CheckConstraint("status IN ('active', 'disabled')", name="ck_accounts_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    roles: Mapped[list[AccountRoleRow]] = relationship(
        back_populates="account",
        order_by="AccountRoleRow.position",
        cascade="all, delete-orphan",
    )


class AccountRoleRow(Base):
    __tablename__ = "account_roles"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_account_roles_position"),
    )

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(ForeignKey("rbac_roles.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    account: Mapped[AccountRow] = relationship(back_populates="roles")


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
