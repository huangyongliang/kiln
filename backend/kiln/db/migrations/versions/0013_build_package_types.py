"""Build package type catalog, skill binding, and package-type foreign keys.

Seeds only maven/Maven, python/Python, generic/通用包, and frontend/前端构建包.
Each seed is enabled, has no runtime conditions, and has an empty Jenkins
label so existing jobs keep roaming. Node is not seeded. No host address is
written here.

``skills.build_package_type`` is added nullable, every existing skill
(draft, in review, and published) is set to ``generic``, then the column is
made non-null. The three ``package_type`` check constraints are replaced by
foreign keys to ``build_package_types.code``. Historical text values stay the
four seed codes.

Downgrade restores those check constraints only when every ``package_type``
and every skill binding is still one of the four seed codes. If an
administrator has added a code and any artifact, reservation, product build,
or skill uses it, downgrade raises and leaves the rows in place. Unused extra
catalog rows are dropped with the catalog table because nothing references
them. Permission ``build.catalog.manage`` is not inserted here; ``seed_rbac``
upserts it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_build_package_types"
down_revision: str | None = "0012_account_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEED = ("maven", "python", "generic", "frontend")
_SEED_LABELS = (
    ("maven", "Maven", 0),
    ("python", "Python", 1),
    ("generic", "通用包", 2),
    ("frontend", "前端构建包", 3),
)
_PACKAGE_TYPE_CHECKS = (
    ("artifacts", "ck_artifacts_package_type"),
    ("download_reservations", "ck_download_reservations_package_type"),
    ("product_builds", "ck_product_builds_package_type"),
)
_PACKAGE_TYPE_FKS = (
    (
        "fk_artifacts_package_type_build_package_types",
        "artifacts",
        "package_type",
    ),
    (
        "fk_download_reservations_package_type_build_package_types",
        "download_reservations",
        "package_type",
    ),
    (
        "fk_product_builds_package_type_build_package_types",
        "product_builds",
        "package_type",
    ),
)
_SKILL_FK = "fk_skills_build_package_type_build_package_types"
_CHECK_SQL = "package_type IN ('maven', 'python', 'generic', 'frontend')"


def upgrade() -> None:
    op.create_table(
        "build_package_types",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("jenkins_label", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "char_length(code) BETWEEN 1 AND 32",
            name="ck_build_package_types_code_len",
        ),
        sa.CheckConstraint(
            "char_length(label) BETWEEN 1 AND 40",
            name="ck_build_package_types_label",
        ),
        sa.CheckConstraint(
            "char_length(description) <= 500",
            name="ck_build_package_types_description",
        ),
        sa.CheckConstraint(
            "jenkins_label IS NULL OR char_length(jenkins_label) BETWEEN 1 AND 128",
            name="ck_build_package_types_jenkins_label",
        ),
        sa.CheckConstraint("position >= 0", name="ck_build_package_types_position"),
    )
    op.create_table(
        "build_package_type_conditions",
        sa.Column("package_type_code", sa.Text(), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_build_package_type_conditions_position",
        ),
        sa.CheckConstraint(
            "tool IN ('jdk', 'maven', 'node', 'python')",
            name="ck_build_package_type_conditions_tool",
        ),
        sa.CheckConstraint(
            "char_length(version) BETWEEN 1 AND 32",
            name="ck_build_package_type_conditions_version",
        ),
        sa.ForeignKeyConstraint(
            ["package_type_code"],
            ["build_package_types.code"],
            name="fk_build_package_type_conditions_package_type_code",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "package_type_code",
            "tool",
            name="uq_build_package_type_conditions_tool",
        ),
    )
    bind = op.get_bind()
    for code, label, position in _SEED_LABELS:
        bind.execute(
            sa.text(
                """
                INSERT INTO build_package_types (
                    code, label, description, enabled, jenkins_label,
                    position, created_at, updated_at
                ) VALUES (
                    :code, :label, '', true, NULL, :position, now(), now()
                )
                """
            ),
            {"code": code, "label": label, "position": position},
        )
    op.add_column("skills", sa.Column("build_package_type", sa.Text(), nullable=True))
    op.create_foreign_key(
        _SKILL_FK,
        "skills",
        "build_package_types",
        ["build_package_type"],
        ["code"],
    )
    op.execute("UPDATE skills SET build_package_type = 'generic' WHERE build_package_type IS NULL")
    op.alter_column(
        "skills",
        "build_package_type",
        existing_type=sa.Text(),
        nullable=False,
    )
    for table, name in _PACKAGE_TYPE_CHECKS:
        op.drop_constraint(name, table, type_="check")
    for name, table, column in _PACKAGE_TYPE_FKS:
        op.create_foreign_key(name, table, "build_package_types", [column], ["code"])


def downgrade() -> None:
    offenders = _non_seed_usages()
    if offenders:
        listed = "、".join(offenders)
        raise RuntimeError(
            "无法降级 0013_build_package_types：已有数据使用了种子类型以外的构建类型码"
            f"（{listed}）。"
            "降级不会删除这些行。请先去掉这些引用，只保留 "
            "maven、python、generic、frontend 后再降级。"
        )
    for name, table, _column in _PACKAGE_TYPE_FKS:
        op.drop_constraint(name, table, type_="foreignkey")
    for table, name in _PACKAGE_TYPE_CHECKS:
        op.create_check_constraint(name, table, _CHECK_SQL)
    op.drop_constraint(_SKILL_FK, "skills", type_="foreignkey")
    op.drop_column("skills", "build_package_type")
    op.drop_table("build_package_type_conditions")
    op.drop_table("build_package_types")


def _non_seed_usages() -> list[str]:
    """Codes outside the four seeds that a downgrade would have to discard."""

    bind = op.get_bind()
    seed_sql = ", ".join(f"'{code}'" for code in _SEED)
    found: list[str] = []
    queries = (
        ("artifacts", "package_type"),
        ("download_reservations", "package_type"),
        ("product_builds", "package_type"),
        ("skills", "build_package_type"),
    )
    for table, column in queries:
        rows = bind.execute(
            sa.text(
                f"SELECT DISTINCT {column} FROM {table} "
                f"WHERE {column} NOT IN ({seed_sql}) ORDER BY {column}"
            )
        ).scalars()
        for code in rows:
            found.append(f"{table}.{column}={code}")
    return found
