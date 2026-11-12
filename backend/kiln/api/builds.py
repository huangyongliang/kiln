"""Product build HTTP API.

Creating a build, reading it, and reading its log require the local API token.
The build stores artifact bytes on the platform. It does not deploy them.
"""

from uuid import UUID

from fastapi import APIRouter, Depends

from kiln.api.deps import get_build_store, get_git_source, get_jenkins, get_settings, get_store
from kiln.auth import require_api_token
from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.db.store import PostgresStore
from kiln.domain.builds import BuildLog, ProductBuild, ProductBuildCreate, ProductBuildList
from kiln.executor.jenkins import JenkinsClient
from kiln.executor.product import (
    build_log,
    collect_build,
    create_product_build,
    list_builds,
    publish,
)
from kiln.git.source import GitSource

router = APIRouter(tags=["产品构建"], dependencies=[Depends(require_api_token)])


@router.get("/builds", response_model=ProductBuildList, summary="列出产品构建")
def get_builds(
    builds: BuildStore = Depends(get_build_store),
    catalog: PostgresStore = Depends(get_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
    settings: Settings = Depends(get_settings),
) -> ProductBuildList:
    items = list_builds(builds, catalog, jenkins, settings)
    return ProductBuildList(items=[publish(builds, item) for item in items])


@router.post("/builds", response_model=ProductBuild, status_code=201, summary="从代码仓库构建制品")
def post_build(
    payload: ProductBuildCreate,
    builds: BuildStore = Depends(get_build_store),
    catalog: PostgresStore = Depends(get_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
    source: GitSource = Depends(get_git_source),
    settings: Settings = Depends(get_settings),
) -> ProductBuild:
    stored = create_product_build(builds, catalog, jenkins, source, settings, payload)
    return publish(builds, stored)


@router.get("/builds/{build_id}", response_model=ProductBuild, summary="查看产品构建")
def get_build(
    build_id: UUID,
    builds: BuildStore = Depends(get_build_store),
    catalog: PostgresStore = Depends(get_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
    settings: Settings = Depends(get_settings),
) -> ProductBuild:
    stored = collect_build(builds, catalog, jenkins, settings, build_id)
    return publish(builds, stored)


@router.get("/builds/{build_id}/log", response_model=BuildLog, summary="读取产品构建日志")
def get_build_log(
    build_id: UUID,
    builds: BuildStore = Depends(get_build_store),
    jenkins: JenkinsClient = Depends(get_jenkins),
    settings: Settings = Depends(get_settings),
) -> BuildLog:
    return build_log(builds, jenkins, settings, build_id)
