import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from kiln.config import get_settings  # noqa: E402
from kiln.main import create_app  # noqa: E402
from tests.external import ExternalTargets, load_external_targets  # noqa: E402
from tests.support import (  # noqa: E402
    app_settings,
    auth_headers,
    require_database,
    truncate_domain,
)

ENV_KEYS = (
    "KILN_ENV",
    "KILN_HTTP_HOST",
    "KILN_HTTP_PORT",
    "KILN_CORS_ORIGINS",
    "DATABASE_URL",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MODELS",
    "LLM_TIMEOUT_SECONDS",
    "KILN_API_TOKEN",
    "JENKINS_URL",
    "JENKINS_USER",
    "JENKINS_API_TOKEN",
    "JENKINS_TIMEOUT_SECONDS",
    "GIT_PROVIDER",
    "GIT_BASE_URL",
    "GIT_CLONE_BASE_URL",
    "GIT_TOKEN",
    "GIT_TIMEOUT_SECONDS",
    "KILN_NEXUS_URL",
    "KILN_NEXUS_REPOSITORY",
    "KILN_NEXUS_BUILD_URL",
    "KILN_NEXUS_USERNAME",
    "KILN_NEXUS_PASSWORD",
    "KILN_NEXUS_TIMEOUT_SECONDS",
    "KILN_ARTIFACT_ROOT",
    "KILN_ARTIFACT_MAX_BYTES",
    "KILN_OIDC_ISSUER_URL",
    "KILN_OIDC_CLIENT_ID",
    "KILN_OIDC_CLIENT_SECRET",
    "KILN_OIDC_REDIRECT_URI",
    "KILN_OIDC_EMPLOYMENT_CLAIM",
    "KILN_OIDC_EMPLOYMENT_ACTIVE_VALUES",
    "KILN_OIDC_SCOPES",
    "KILN_SESSION_TTL_SECONDS",
    "KILN_SESSION_COOKIE_SECURE",
)


@pytest.fixture(scope="session", autouse=True)
def postgres_required() -> None:
    require_database()


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def external_targets() -> ExternalTargets:
    return load_external_targets()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    root = tmp_path / "artifacts"
    app = create_app(
        app_settings(llm_provider="off", kiln_artifact_root=str(root)),
    )
    with TestClient(app, headers=auth_headers()) as test_client:
        truncate_domain(app.state.engine)
        yield test_client
