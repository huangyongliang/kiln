"""PostgreSQL store for the domain HTTP API.

Records stay in the database when the API process exits. Artifact bytes stay
in the platform-owned blob store, which also outlives the process.
"""

import hashlib
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from kiln import clock
from kiln.db.models import (
    AccountRoleRow,
    AccountRow,
    ArtifactRow,
    ArtifactVersionRow,
    PermissionRow,
    ProductBuildRow,
    ReservationRow,
    RoleRow,
    SessionRow,
    SkillDependencyRow,
    SkillInputRow,
    SkillRepositoryRow,
    SkillRow,
    SkillStepRow,
)
from kiln.domain.accounts import Account, AccountCreate, SyncLink
from kiln.domain.artifacts import (
    BUILD_OWNED_WRITE_DETAIL,
    BUILD_SOURCE_FILL_DETAIL,
    FOREIGN_BUILD_VERSION_DETAIL,
    PACKAGE_LABELS,
    VERSION_PATTERN,
    Artifact,
    ArtifactCreate,
    ArtifactSource,
    ArtifactVersion,
    ArtifactVersionCreate,
    Checksum,
    DownloadReservationCreate,
    PackageType,
    Reservation,
    RetrievedBytes,
    RetrieveResult,
)
from kiln.domain.rbac import Permission, Role
from kiln.domain.skills import (
    Skill,
    SkillCreate,
    SkillInput,
    SkillScope,
    SkillStatus,
    SkillStep,
)
from kiln.errors import BadRequest, Conflict, Gone, NotFound, StorageError
from kiln.passwords import hash_password, verify_password
from kiln.paths import retrieve_content_path, retrieve_path
from kiln.storage.local import LocalBlobStore

_VERSION = re.compile(VERSION_PATTERN)

_SKILL_LOAD = (
    selectinload(SkillRow.inputs),
    selectinload(SkillRow.steps),
    selectinload(SkillRow.allowed_repositories),
    selectinload(SkillRow.allowed_dependencies),
)


class PostgresStore:
    def __init__(self, engine: Engine, blobs: LocalBlobStore) -> None:
        self.engine = engine
        self.blobs = blobs

    def _session(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    def list_permissions(self) -> list[Permission]:
        with self._session() as session:
            rows = session.scalars(
                select(PermissionRow).order_by(PermissionRow.position, PermissionRow.code)
            ).all()
            return [Permission(code=row.code, description=row.description) for row in rows]

    def list_roles(self) -> list[Role]:
        with self._session() as session:
            rows = session.scalars(
                select(RoleRow)
                .order_by(RoleRow.position, RoleRow.id)
                .options(selectinload(RoleRow.grants))
            ).all()
            return [
                Role(
                    id=row.id,
                    name=row.name,
                    description=row.description,
                    permissions=[
                        grant.permission_code
                        for grant in sorted(row.grants, key=lambda item: item.position)
                    ],
                )
                for row in rows
            ]

    def list_accounts(self) -> list[Account]:
        with self._session() as session:
            rows = session.scalars(
                select(AccountRow)
                .order_by(AccountRow.created_at, AccountRow.id)
                .options(selectinload(AccountRow.roles))
            ).all()
            return [_account(row) for row in rows]

    def get_account(self, account_id: UUID) -> Account:
        with self._session() as session:
            row = session.scalars(
                select(AccountRow)
                .where(AccountRow.id == account_id)
                .options(selectinload(AccountRow.roles))
            ).first()
            if row is None:
                raise NotFound("账号不存在")
            return _account(row)

    def create_account(self, payload: AccountCreate) -> Account:
        account_id = uuid4()
        created_at = clock.utcnow()
        password_hash = hash_password(payload.password) if payload.password is not None else None
        row = AccountRow(
            id=account_id,
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            status="active",
            password_hash=password_hash,
            created_at=created_at,
        )
        row.roles = [
            AccountRoleRow(role_id=role_id, position=index)
            for index, role_id in enumerate(payload.role_ids)
        ]
        with self._session() as session:
            existing = session.scalar(
                select(AccountRow.id).where(AccountRow.username == payload.username)
            )
            if existing is not None:
                raise Conflict("用户名已存在")
            session.add(row)
            _commit_unique(session, "用户名已存在")
            return _account(row)

    def authenticate_password(self, username: str, password: str) -> tuple[Account | None, str]:
        """Return the account, or a denial of `invalid` or `disabled`.

        A missing password hash is `invalid`. No session is created here.
        """

        with self._session() as session:
            row = session.scalars(
                select(AccountRow)
                .where(AccountRow.username == username)
                .options(selectinload(AccountRow.roles))
            ).first()
            if row is None or not row.password_hash:
                return None, "invalid"
            if not verify_password(password, row.password_hash):
                return None, "invalid"
            if row.status != "active":
                return None, "disabled"
            return _account(row), "ok"

    def link_synced_account(
        self,
        *,
        issuer: str,
        subject: str,
        username_seed: str,
        display_name: str,
        email: str | None,
        employment_status: str | None,
        employment_active: bool,
    ) -> SyncLink:
        """Link `(issuer, subject)` to a kiln account.

        An existing local account that only shares an email is left alone.
        Roles are not changed. A password is not set. Inactive employment
        does not create an account.
        """

        if not re.fullmatch(r"^[a-z][a-z0-9_-]{2,31}$", username_seed):
            raise BadRequest("用户名不正确")
        with self._session() as session:
            row = session.scalars(
                select(AccountRow)
                .where(AccountRow.idp_issuer == issuer, AccountRow.idp_subject == subject)
                .options(selectinload(AccountRow.roles))
            ).first()
            if row is None:
                if not employment_active:
                    return SyncLink(account=None, denial="employment")
                row = AccountRow(
                    id=uuid4(),
                    username=_unique_username(session, username_seed),
                    display_name=display_name[:80],
                    email=email,
                    status="active",
                    password_hash=None,
                    idp_issuer=issuer,
                    idp_subject=subject,
                    employment_status=employment_status,
                    created_at=clock.utcnow(),
                )
                row.roles = [AccountRoleRow(role_id="reader", position=0)]
                session.add(row)
                _commit_unique(session, "用户名已存在")
                return SyncLink(account=_account(row))
            row.display_name = display_name[:80]
            row.email = email
            row.employment_status = employment_status
            session.commit()
            if row.status != "active":
                return SyncLink(account=None, denial="disabled")
            if not employment_active:
                return SyncLink(account=None, denial="employment")
            return SyncLink(account=_account(row))

    def create_session(self, account_id: UUID, token_hash: str, ttl_seconds: int) -> datetime:
        created_at = clock.utcnow()
        expires_at = created_at + timedelta(seconds=ttl_seconds)
        row = SessionRow(
            id=uuid4(),
            token_hash=token_hash,
            account_id=account_id,
            created_at=created_at,
            expires_at=expires_at,
        )
        with self._session() as session:
            session.add(row)
            session.commit()
        return expires_at

    def account_for_session_hash(self, token_hash: str) -> Account | None:
        now = clock.utcnow()
        with self._session() as session:
            row = session.scalars(
                select(SessionRow).where(SessionRow.token_hash == token_hash)
            ).first()
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                return None
            account = session.scalars(
                select(AccountRow)
                .where(AccountRow.id == row.account_id)
                .options(selectinload(AccountRow.roles))
            ).first()
            if account is None or account.status != "active":
                return None
            return _account(account)

    def revoke_session_hash(self, token_hash: str) -> None:
        now = clock.utcnow()
        with self._session() as session:
            row = session.scalars(
                select(SessionRow).where(SessionRow.token_hash == token_hash)
            ).first()
            if row is None or row.revoked_at is not None:
                return
            row.revoked_at = now
            session.commit()

    def list_artifacts(self) -> list[Artifact]:
        with self._session() as session:
            rows = session.scalars(
                select(ArtifactRow)
                .order_by(ArtifactRow.created_at, ArtifactRow.id)
                .options(selectinload(ArtifactRow.versions))
            ).all()
            owned = _build_owned_ids(session)
            return [_artifact(row, build_owned=row.id in owned) for row in rows]

    def get_artifact(self, artifact_id: UUID) -> Artifact:
        with self._session() as session:
            row = session.scalars(
                select(ArtifactRow)
                .where(ArtifactRow.id == artifact_id)
                .options(selectinload(ArtifactRow.versions))
            ).first()
            if row is None:
                raise NotFound("制品不存在")
            return _artifact(row, build_owned=_artifact_is_build_owned(session, artifact_id))

    def create_artifact(self, payload: ArtifactCreate) -> Artifact:
        artifact_id = uuid4()
        created_at = clock.utcnow()
        row = ArtifactRow(
            id=artifact_id,
            name=payload.name,
            group_name=payload.group,
            description=payload.description,
            package_type=payload.package_type.value,
            created_at=created_at,
        )
        with self._session() as session:
            existing = session.scalar(
                select(ArtifactRow.id).where(
                    ArtifactRow.name == payload.name,
                    ArtifactRow.group_name.is_not_distinct_from(payload.group),
                )
            )
            if existing is not None:
                raise Conflict("同组内制品名称已存在")
            session.add(row)
            _commit_unique(session, "同组内制品名称已存在")
            return _artifact(row, build_owned=False)

    def add_version(
        self,
        artifact_id: UUID,
        payload: ArtifactVersionCreate,
        *,
        created_by: UUID | None = None,
    ) -> ArtifactVersion:
        with self._session() as session:
            artifact = session.scalar(
                select(ArtifactRow).where(ArtifactRow.id == artifact_id).with_for_update()
            )
            if artifact is None:
                raise NotFound("制品不存在")
            if _artifact_is_build_owned(session, artifact_id):
                raise Conflict(BUILD_OWNED_WRITE_DETAIL)
            existing = session.scalar(
                select(ArtifactVersionRow)
                .where(
                    ArtifactVersionRow.artifact_id == artifact_id,
                    ArtifactVersionRow.version == payload.version,
                )
                .with_for_update()
            )
            if existing is not None:
                raise Conflict("版本已存在且不可变")
            created_at = clock.utcnow()
            row = ArtifactVersionRow(
                artifact_id=artifact_id,
                version=payload.version,
                checksum_algorithm=payload.checksum.algorithm,
                checksum_value=payload.checksum.value,
                size_bytes=payload.size_bytes,
                source=payload.source.value,
                content_available=False,
                blob_key=None,
                created_by=created_by,
                created_at=created_at,
            )
            session.add(row)
            _commit_unique(session, "版本已存在且不可变")
            return _version(row)

    def reserve_download(
        self,
        artifact_id: UUID,
        version: str,
        payload: DownloadReservationCreate,
        *,
        requester: str,
        account_id: UUID | None,
    ) -> Reservation:
        with self._session() as session:
            artifact = session.get(ArtifactRow, artifact_id)
            if artifact is None:
                raise NotFound("制品不存在")
            matched = session.get(ArtifactVersionRow, (artifact_id, version))
            if matched is None:
                raise NotFound("版本不存在")
            created_at = clock.utcnow()
            reservation_id = uuid4()
            expires_at = created_at + timedelta(seconds=payload.ttl_seconds)
            package_type = PackageType(artifact.package_type)
            row = ReservationRow(
                id=reservation_id,
                artifact_id=artifact.id,
                artifact_name=artifact.name,
                version=matched.version,
                package_type=artifact.package_type,
                checksum_algorithm=matched.checksum_algorithm,
                checksum_value=matched.checksum_value,
                requester=requester,
                account_id=account_id,
                created_at=created_at,
                expires_at=expires_at,
            )
            session.add(row)
            session.commit()
            return Reservation(
                id=reservation_id,
                artifact_id=artifact.id,
                artifact_name=artifact.name,
                version=matched.version,
                package_type=package_type,
                package_type_label=PACKAGE_LABELS[package_type],
                checksum=Checksum(
                    algorithm=matched.checksum_algorithm,
                    value=matched.checksum_value,
                ),
                requester=requester,
                account_id=account_id,
                created_at=created_at,
                expires_at=expires_at,
                retrieve_path=retrieve_path(reservation_id),
            )

    def store_version_bytes(
        self,
        artifact_id: UUID,
        version: str,
        data: bytes,
        checksum: Checksum,
        source: ArtifactSource,
        *,
        created_by: UUID | None = None,
        manual: bool = False,
    ) -> tuple[ArtifactVersion, bool]:
        if _VERSION.fullmatch(version) is None:
            raise BadRequest("版本号不正确")
        actual = hashlib.new(checksum.algorithm, data).hexdigest()
        if actual != checksum.value:
            raise BadRequest("校验和与制品字节不一致")
        key = LocalBlobStore.key_for(checksum.algorithm, checksum.value)
        with self._session() as session:
            artifact = session.scalar(
                select(ArtifactRow).where(ArtifactRow.id == artifact_id).with_for_update()
            )
            if artifact is None:
                raise NotFound("制品不存在")
            if manual and _artifact_is_build_owned(session, artifact_id):
                raise Conflict(BUILD_OWNED_WRITE_DETAIL)
            row = session.scalar(
                select(ArtifactVersionRow)
                .where(
                    ArtifactVersionRow.artifact_id == artifact_id,
                    ArtifactVersionRow.version == version,
                )
                .with_for_update()
            )
            if (
                row is not None
                and manual
                and not row.content_available
                and row.source != ArtifactSource.upload.value
            ):
                raise Conflict(BUILD_SOURCE_FILL_DETAIL)
            if row is not None and not _same_version_bytes(row, checksum, len(data), key):
                raise Conflict("版本已存在且不可变")
            self.blobs.write(checksum.algorithm, checksum.value, data)
            if row is None:
                created_at = clock.utcnow()
                row = ArtifactVersionRow(
                    artifact_id=artifact_id,
                    version=version,
                    checksum_algorithm=checksum.algorithm,
                    checksum_value=checksum.value,
                    size_bytes=len(data),
                    source=source.value,
                    content_available=True,
                    blob_key=key,
                    created_by=created_by,
                    created_at=created_at,
                )
                session.add(row)
                _commit_unique(session, "版本已存在且不可变")
                return _version(row), True
            if row.content_available:
                return _version(row), False
            if row.created_by is None and created_by is not None:
                row.created_by = created_by
            row.size_bytes = len(data)
            row.content_available = True
            row.blob_key = key
            session.commit()
            return _version(row), True

    def get_retrieve(self, reservation_id: UUID) -> RetrieveResult:
        with self._session() as session:
            row = _load_reservation(session, reservation_id)
            version = session.get(ArtifactVersionRow, (row.artifact_id, row.version))
            available = bool(version is not None and version.content_available and version.blob_key)
            package_type = PackageType(row.package_type)
            source = ArtifactSource(version.source) if version is not None else None
            foreign = (
                version is not None
                and version.source != ArtifactSource.build.value
                and _artifact_is_build_owned(session, row.artifact_id)
            )
            if foreign:
                detail = FOREIGN_BUILD_VERSION_DETAIL
            elif available:
                detail = "制品字节已由平台保存，可从 content_path 取回。"
            else:
                detail = "该版本尚未保存制品字节。"
            return RetrieveResult(
                reservation_id=row.id,
                artifact_id=row.artifact_id,
                name=row.artifact_name,
                version=row.version,
                package_type=package_type,
                package_type_label=PACKAGE_LABELS[package_type],
                checksum=Checksum(
                    algorithm=row.checksum_algorithm,
                    value=row.checksum_value,
                ),
                content_available=available,
                content_path=retrieve_content_path(row.id) if available else None,
                source=source,
                detail=detail,
            )

    def read_reserved_bytes(self, reservation_id: UUID) -> RetrievedBytes:
        with self._session() as session:
            row = _load_reservation(session, reservation_id)
            version = session.get(ArtifactVersionRow, (row.artifact_id, row.version))
            if version is None or not version.content_available or not version.blob_key:
                raise Conflict("该版本还没有制品字节")
            data = self.blobs.read(version.blob_key)
            digest = hashlib.new(version.checksum_algorithm, data).hexdigest()
            if digest != version.checksum_value or len(data) != version.size_bytes:
                raise StorageError("制品字节与登记的校验和不一致")
            return RetrievedBytes(
                data=data,
                name=row.artifact_name,
                version=row.version,
                algorithm=version.checksum_algorithm,
                digest=version.checksum_value,
                source=version.source,
            )

    def list_skills(
        self,
        status: SkillStatus | None,
        *,
        scope: SkillScope | None = None,
        account_id: UUID | None = None,
    ) -> list[Skill]:
        if scope is SkillScope.mine and account_id is None:
            return []
        with self._session() as session:
            stmt = select(SkillRow).order_by(SkillRow.created_at, SkillRow.id).options(*_SKILL_LOAD)
            if scope is SkillScope.mine:
                stmt = stmt.where(SkillRow.created_by == account_id)
            elif scope is SkillScope.usable:
                stmt = stmt.where(SkillRow.status == SkillStatus.published.value)
            if status is not None:
                stmt = stmt.where(SkillRow.status == status.value)
            rows = session.scalars(stmt).all()
            return [_skill(row) for row in rows]

    def get_skill(self, skill_id: UUID) -> Skill:
        with self._session() as session:
            row = _load_skill(session, skill_id, lock=False)
            return _skill(row)

    def create_skill(self, payload: SkillCreate, created_by: UUID | None = None) -> Skill:
        skill_id = uuid4()
        now = clock.utcnow()
        row = SkillRow(
            id=skill_id,
            name=payload.name,
            version=payload.version,
            status=SkillStatus.draft.value,
            description=payload.description,
            toolchain=payload.toolchain,
            environment=payload.environment,
            output_spec=payload.output_spec,
            created_at=now,
            updated_at=now,
            created_by=created_by,
        )
        row.inputs = [
            SkillInputRow(
                position=index,
                name=item.name,
                description=item.description,
                required=item.required,
            )
            for index, item in enumerate(payload.inputs)
        ]
        row.steps = [
            SkillStepRow(position=index, kind=item.kind, description=item.description)
            for index, item in enumerate(payload.steps)
        ]
        row.allowed_repositories = [
            SkillRepositoryRow(position=index, value=item)
            for index, item in enumerate(payload.allowed_repositories)
        ]
        row.allowed_dependencies = [
            SkillDependencyRow(position=index, value=item)
            for index, item in enumerate(payload.allowed_dependencies)
        ]
        with self._session() as session:
            existing = session.scalar(
                select(SkillRow.id).where(
                    SkillRow.name == payload.name,
                    SkillRow.version == payload.version,
                )
            )
            if existing is not None:
                raise Conflict("同名同版本的 skill 已存在")
            session.add(row)
            _commit_unique(session, "同名同版本的 skill 已存在")
            return _skill(row)

    def update_skill(self, skill_id: UUID, payload: SkillCreate) -> Skill:
        """Replace mutable fields of a draft. Status and review fields stay.

        Callers decide who may edit. This method only refuses a non-draft and
        a name/version collision, and it does not clear a return.
        """

        with self._session() as session:
            row = _load_skill(session, skill_id, lock=True)
            if row.status != SkillStatus.draft.value:
                raise Conflict("只有草稿可以修改")
            taken = session.scalar(
                select(SkillRow.id).where(
                    SkillRow.name == payload.name,
                    SkillRow.version == payload.version,
                    SkillRow.id != skill_id,
                )
            )
            if taken is not None:
                raise Conflict("同名同版本的 skill 已存在")
            row.name = payload.name
            row.version = payload.version
            row.description = payload.description
            row.toolchain = payload.toolchain
            row.environment = payload.environment
            row.output_spec = payload.output_spec
            row.updated_at = clock.utcnow()
            row.inputs.clear()
            row.steps.clear()
            row.allowed_repositories.clear()
            row.allowed_dependencies.clear()
            session.flush()
            row.inputs = [
                SkillInputRow(
                    position=index,
                    name=item.name,
                    description=item.description,
                    required=item.required,
                )
                for index, item in enumerate(payload.inputs)
            ]
            row.steps = [
                SkillStepRow(position=index, kind=item.kind, description=item.description)
                for index, item in enumerate(payload.steps)
            ]
            row.allowed_repositories = [
                SkillRepositoryRow(position=index, value=item)
                for index, item in enumerate(payload.allowed_repositories)
            ]
            row.allowed_dependencies = [
                SkillDependencyRow(position=index, value=item)
                for index, item in enumerate(payload.allowed_dependencies)
            ]
            _commit_unique(session, "同名同版本的 skill 已存在")
            return _skill(row)

    def submit_skill_review(self, skill_id: UUID) -> Skill:
        return self._transition(
            skill_id,
            SkillStatus.draft,
            SkillStatus.in_review,
            "只有草稿可以提交审核",
        )

    def publish_skill(self, skill_id: UUID) -> Skill:
        return self._transition(
            skill_id,
            SkillStatus.in_review,
            SkillStatus.published,
            "只有审核中的 skill 可以发布",
        )

    def reject_skill(self, skill_id: UUID, reason: str, actor_id: UUID | None) -> Skill:
        with self._session() as session:
            row = _load_skill(session, skill_id, lock=True)
            if row.status != SkillStatus.in_review.value:
                raise Conflict("只有审核中的 skill 可以退回")
            now = clock.utcnow()
            row.status = SkillStatus.draft.value
            row.review_note = reason
            row.rejected_at = now
            row.rejected_by = actor_id
            row.updated_at = now
            session.commit()
            return _skill(row)

    def _transition(
        self,
        skill_id: UUID,
        expected: SkillStatus,
        target: SkillStatus,
        message: str,
    ) -> Skill:
        with self._session() as session:
            row = _load_skill(session, skill_id, lock=True)
            if row.status != expected.value:
                raise Conflict(message)
            row.status = target.value
            row.updated_at = clock.utcnow()
            session.commit()
            return _skill(row)


def _same_version_bytes(
    row: ArtifactVersionRow,
    checksum: Checksum,
    size: int,
    key: str,
) -> bool:
    if row.checksum_algorithm != checksum.algorithm or row.checksum_value != checksum.value:
        return False
    if row.size_bytes is not None and row.size_bytes != size:
        return False
    if row.content_available and row.blob_key != key:
        return False
    return True


def _load_reservation(session: Session, reservation_id: UUID) -> ReservationRow:
    row = session.get(ReservationRow, reservation_id)
    if row is None:
        raise NotFound("取包预约不存在")
    if row.expires_at <= clock.utcnow():
        raise Gone("取包预约已过期")
    return row


def _unique_username(session: Session, seed: str) -> str:
    if session.scalar(select(AccountRow.id).where(AccountRow.username == seed)) is None:
        return seed
    for index in range(1, 100):
        suffix = f"_{index}"
        candidate = seed[: 32 - len(suffix)] + suffix
        taken = session.scalar(select(AccountRow.id).where(AccountRow.username == candidate))
        if taken is None:
            return candidate
    raise Conflict("用户名已存在")


def _commit_unique(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if _is_unique_violation(exc):
            raise Conflict(message) from exc
        raise


def _is_unique_violation(exc: IntegrityError) -> bool:
    orig: object = exc.orig
    seen: set[int] = set()
    while orig is not None and id(orig) not in seen:
        if isinstance(orig, UniqueViolation):
            return True
        seen.add(id(orig))
        orig = getattr(orig, "orig", None)
    return False


def _load_skill(session: Session, skill_id: UUID, *, lock: bool) -> SkillRow:
    stmt = select(SkillRow).where(SkillRow.id == skill_id).options(*_SKILL_LOAD)
    if lock:
        stmt = stmt.with_for_update()
    row = session.scalars(stmt).first()
    if row is None:
        raise NotFound("skill 不存在")
    return row


def _account(row: AccountRow) -> Account:
    roles = sorted(row.roles, key=lambda item: item.position)
    return Account(
        id=row.id,
        username=row.username,
        display_name=row.display_name,
        email=row.email,
        status=row.status,  # type: ignore[arg-type]
        source="sync" if row.idp_subject is not None else "local",
        role_ids=[item.role_id for item in roles],
        created_at=row.created_at,
    )


def _version(row: ArtifactVersionRow) -> ArtifactVersion:
    return ArtifactVersion(
        version=row.version,
        checksum=Checksum(algorithm=row.checksum_algorithm, value=row.checksum_value),
        size_bytes=row.size_bytes,
        source=ArtifactSource(row.source),
        content_available=row.content_available,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _artifact(row: ArtifactRow, *, build_owned: bool) -> Artifact:
    package_type = PackageType(row.package_type)
    versions = sorted(row.versions, key=lambda item: (item.created_at, item.version))
    return Artifact(
        id=row.id,
        name=row.name,
        group=row.group_name,
        description=row.description,
        package_type=package_type,
        package_type_label=PACKAGE_LABELS[package_type],
        versions=[_version(item) for item in versions],
        build_owned=build_owned,
        created_at=row.created_at,
    )


def _build_owned_ids(session: Session) -> set[UUID]:
    return set(session.scalars(select(ProductBuildRow.artifact_id)).all())


def _artifact_is_build_owned(session: Session, artifact_id: UUID) -> bool:
    found = session.scalar(
        select(ProductBuildRow.id).where(ProductBuildRow.artifact_id == artifact_id).limit(1)
    )
    return found is not None


def _skill(row: SkillRow) -> Skill:
    inputs = sorted(row.inputs, key=lambda item: item.position)
    steps = sorted(row.steps, key=lambda item: item.position)
    repositories = sorted(row.allowed_repositories, key=lambda item: item.position)
    dependencies = sorted(row.allowed_dependencies, key=lambda item: item.position)
    return Skill(
        id=row.id,
        name=row.name,
        version=row.version,
        status=SkillStatus(row.status),
        description=row.description,
        inputs=[
            SkillInput(name=item.name, description=item.description, required=item.required)
            for item in inputs
        ],
        toolchain=row.toolchain,
        environment=row.environment,
        allowed_repositories=[item.value for item in repositories],
        allowed_dependencies=[item.value for item in dependencies],
        steps=[SkillStep(kind=item.kind, description=item.description) for item in steps],
        output_spec=row.output_spec,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        review_note=row.review_note,
        rejected_at=row.rejected_at,
        rejected_by=row.rejected_by,
    )
