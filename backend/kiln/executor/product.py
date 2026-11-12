"""Run a product build on Jenkins and store the archived file locally.

The git token is handed to Jenkins for the clone. An optional Nexus password
is sent only as a core Jenkins password parameter for that build. Neither is
written to Postgres. Both are stripped from logs returned by this service.
Nothing here deploys to a runtime or publishes bytes to Nexus. Configuring the
proxy does not probe it before the build starts.
"""

import hashlib
import re
from uuid import UUID, uuid4

from kiln.config import Settings
from kiln.db.builds import BuildStore, StoredBuild
from kiln.db.store import PostgresStore
from kiln.domain.artifacts import ArtifactCreate, ArtifactSource, Checksum
from kiln.domain.builds import BuildLog, ProductBuild, ProductBuildCreate
from kiln.domain.jobs import TERMINAL_STATUSES, JobStatus
from kiln.errors import Conflict, UpstreamFailure
from kiln.executor.jenkins import JenkinsClient
from kiln.executor.script import (
    NEXUS_PARAMETER_NAMES,
    WORKSPACE_ARTIFACT,
    clone_url_with_user,
    product_build_command,
)
from kiln.git.refs import parse_ref, parse_repository
from kiln.git.source import GitSource

_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


def create_product_build(
    builds: BuildStore,
    catalog: PostgresStore,
    jenkins: JenkinsClient,
    source: GitSource,
    settings: Settings,
    payload: ProductBuildCreate,
) -> StoredBuild:
    owner, name = parse_repository(payload.repository)
    ref = parse_ref(payload.ref)
    resolved = source.resolve_ref(owner, name, ref)
    if _SHA.fullmatch(resolved.sha) is None:
        raise UpstreamFailure("代码源返回的提交号无效")
    sha = resolved.sha.lower()
    username = source.authenticated_username()
    clone_url = clone_url_with_user(settings.git_clone_base(), owner, name, username)
    product_build_command(
        clone_url=clone_url,
        ref=ref,
        sha=sha,
        git_user=username,
        git_token=settings.git_token_value(),
    )
    repository = f"{owner}/{name}"
    artifact = catalog.create_artifact(
        ArtifactCreate(
            name=payload.artifact_name,
            description=f"由 {repository}@{sha[:12]} 构建",
            package_type=payload.package_type,
        )
    )
    build_id = uuid4()
    jenkins_job_name = f"kiln-build-{build_id.hex}"
    jenkins.create_product_job(
        jenkins_job_name,
        clone_url=clone_url,
        ref=ref,
        sha=sha,
        git_user=username,
        git_token=settings.git_token_value(),
        declare_nexus_parameters=settings.nexus_configured,
    )
    try:
        queue_url = jenkins.trigger_build(
            jenkins_job_name,
            parameters=nexus_build_parameters(settings),
        )
        return builds.create_build(
            build_id=build_id,
            name=payload.name or artifact.name,
            jenkins_job_name=jenkins_job_name,
            repository=repository,
            ref=ref,
            commit_sha=sha,
            artifact_id=artifact.id,
            version=payload.version,
            package_type=payload.package_type.value,
            queue_url=queue_url,
        )
    except Exception:
        try:
            jenkins.delete_job(jenkins_job_name)
        except Exception:
            pass
        raise


def sync_status(builds: BuildStore, jenkins: JenkinsClient, build_id: UUID) -> StoredBuild:
    current = builds.get_build(build_id)
    if current.status in TERMINAL_STATUSES and current.build_number is not None:
        return current
    status_name, number = jenkins.read_progress(
        current.jenkins_job_name,
        current.queue_url,
        current.build_number,
    )
    status = JobStatus(status_name)
    if status == current.status and number == current.build_number:
        return current
    return builds.save_run(current.id, status=status, build_number=number)


def collect_build(
    builds: BuildStore,
    catalog: PostgresStore,
    jenkins: JenkinsClient,
    settings: Settings,
    build_id: UUID,
) -> StoredBuild:
    current = sync_status(builds, jenkins, build_id)
    if current.status != JobStatus.success or current.build_number is None:
        return current
    if current.content_stored or builds.version_has_bytes(current.artifact_id, current.version):
        if not current.content_stored:
            return builds.mark_content_stored(current.id)
        return current
    data = jenkins.download_artifact(
        current.jenkins_job_name,
        current.build_number,
        WORKSPACE_ARTIFACT,
        settings.kiln_artifact_max_bytes,
    )
    digest = hashlib.sha256(data).hexdigest()
    catalog.store_version_bytes(
        current.artifact_id,
        current.version,
        data,
        Checksum(algorithm="sha256", value=digest),
        ArtifactSource.build,
    )
    return builds.mark_content_stored(current.id)


def list_builds(
    builds: BuildStore,
    catalog: PostgresStore,
    jenkins: JenkinsClient,
    settings: Settings,
) -> list[StoredBuild]:
    items: list[StoredBuild] = []
    for build in builds.list_builds():
        items.append(collect_build(builds, catalog, jenkins, settings, build.id))
    return items


def build_log(
    builds: BuildStore,
    jenkins: JenkinsClient,
    settings: Settings,
    build_id: UUID,
) -> BuildLog:
    current = sync_status(builds, jenkins, build_id)
    if current.build_number is None:
        raise Conflict("这次构建还没有日志")
    text, truncated = jenkins.read_log(current.jenkins_job_name, current.build_number)
    text = _redact(text, settings.git_token_value(), settings.nexus_password_value())
    return BuildLog(
        build_id=current.id,
        build_number=current.build_number,
        text=text,
        truncated=truncated,
    )


def nexus_build_parameters(settings: Settings) -> dict[str, str] | None:
    """Form body for ``buildWithParameters``. None keeps the trigger on ``/build``.

    Keys match the core parameter names declared on the job. The password is
    included only here, not in the job config.
    """

    if not settings.nexus_configured:
        return None
    values = {
        "KILN_NEXUS_URL": settings.nexus_build_base(),
        "KILN_NEXUS_REPOSITORY": settings.kiln_nexus_repository,
        "KILN_NEXUS_USERNAME": settings.kiln_nexus_username,
        "KILN_NEXUS_PASSWORD": settings.nexus_password_value(),
    }
    if tuple(values) != NEXUS_PARAMETER_NAMES:
        raise RuntimeError("nexus build parameters drifted from the job config")
    return values


def _redact(text: str, *secrets: str) -> str:
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def publish(builds: BuildStore, stored: StoredBuild) -> ProductBuild:
    return stored.public(
        content_available=builds.version_has_bytes(stored.artifact_id, stored.version)
    )
