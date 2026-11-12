import pytest
from fastapi.testclient import TestClient

from kiln.config import Settings, get_settings
from kiln.main import create_app

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
    "LLM_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    app = create_app(Settings(llm_provider="off", _env_file=None))
    with TestClient(app) as test_client:
        yield test_client
