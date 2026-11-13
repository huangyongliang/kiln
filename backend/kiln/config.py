"""Runtime configuration.

Values come from process environment variables and, when present, a local `.env`
file. Secrets stay out of the repository.

Jenkins and the git source are optional endpoints. Neither has a built-in
production host. GitLab is the production provider name; Gitea is the lab
provider name. Callers supply the base URL.

Optional OIDC settings start 用户信息同步. Empty values leave local password
login in place. The company user system is not given a default address.

Optional Nexus settings are a read-only dependency proxy. Empty values leave
product builds unchanged. Artifact bytes stay in KILN_ARTIFACT_ROOT and are
never published to Nexus.

Optional ``LLM_MODELS`` is a comma-separated allow-list of model ids for the
already configured OpenAI-compatible provider. The list is not fetched from
the endpoint. An id longer than 128 characters, or more than 32 ids after
trim, drop, and dedupe, refuses process startup. The id is not truncated and
the list is not cut down to the first 32.
"""

import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shared by local compose, .env.example, and tests. Not a production secret.
LOCAL_API_TOKEN = "kiln-local"

# LLM_MODELS items. The 32-item cap applies to the parsed allow-list only.
# Prepending a missing LLM_MODEL happens later and may add one extra catalog
# entry. That prepend is required and is not a parse error.
LLM_MODEL_ID_MAX_LENGTH = 128
LLM_MODELS_MAX_ITEMS = 32


class Settings(BaseSettings):
    """Kiln process settings.

    `DATABASE_URL` is the PostgreSQL URL for domain data. Startup opens it,
    applies migrations, and then reads and writes accounts, the RBAC catalog,
    artifacts, versions, download reservations, and skills. The default URL
    is for local use and CI only.

    `KILN_API_TOKEN` is required. The default is for local use and CI only.
    An empty token is rejected so the gate cannot be turned off by leaving
    the variable blank. It remains a machine credential with every catalog
    permission, alongside user sessions.

    `KILN_ARTIFACT_ROOT` is the platform-owned directory for artifact bytes.
    It is a local path, not a Nexus URL.

    OIDC settings are optional and empty by default. When any required OIDC
    value is set, all of them must be set. They configure 用户信息同步 only.
    Kiln accounts and kiln permissions stay on the platform.

    Nexus settings are optional and empty by default. ``KILN_NEXUS_URL`` and
    ``KILN_NEXUS_REPOSITORY`` are set together. Credentials are optional and
    are also set together. The password is never written into a job config.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    kiln_env: str = "local"
    kiln_http_host: str = "0.0.0.0"
    kiln_http_port: int = Field(default=8000, ge=1, le=65535)
    kiln_cors_origins: str = "http://localhost:5173,http://localhost:8080"

    database_url: str = "postgresql://kiln:kiln@localhost:5432/kiln"
    kiln_api_token: SecretStr = SecretStr(LOCAL_API_TOKEN)

    llm_provider: Literal["openai", "mock", "off"] = "off"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_models: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)

    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_api_token: SecretStr = SecretStr("")
    jenkins_timeout_seconds: float = Field(default=30, gt=0, le=120)

    # `gitea` is the lab stand-in. `gitlab` is the production code source.
    # The base URL is never defaulted to a production host.
    git_provider: Literal["gitea", "gitlab", "off"] = "off"
    git_base_url: str = ""
    # Optional URL Jenkins uses to clone. Empty means GIT_BASE_URL. Set this
    # when the API and Jenkins do not see the git server at the same address.
    # It is not a production host.
    git_clone_base_url: str = ""
    git_token: SecretStr = SecretStr("")
    git_timeout_seconds: float = Field(default=30, gt=0, le=120)

    # Optional read-only dependency proxy. Both empty means off. Not a place
    # artifact bytes are published.
    kiln_nexus_url: str = ""
    kiln_nexus_repository: str = ""
    # Optional URL Jenkins uses. Empty means KILN_NEXUS_URL.
    kiln_nexus_build_url: str = ""
    kiln_nexus_username: str = ""
    kiln_nexus_password: SecretStr = SecretStr("")
    kiln_nexus_timeout_seconds: float = Field(default=10, gt=0, le=60)

    # Platform-owned local directory. Not a Nexus URL.
    kiln_artifact_root: str = "var/kiln-artifacts"
    kiln_artifact_max_bytes: int = Field(default=64 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)

    # Optional 用户信息同步. All required values stay empty unless configured.
    kiln_oidc_issuer_url: str = ""
    kiln_oidc_client_id: str = ""
    kiln_oidc_client_secret: SecretStr = SecretStr("")
    kiln_oidc_redirect_uri: str = ""
    kiln_oidc_employment_claim: str = ""
    kiln_oidc_employment_active_values: str = ""
    kiln_oidc_scopes: str = ""
    kiln_session_ttl_seconds: int = Field(default=12 * 60 * 60, ge=60, le=14 * 24 * 60 * 60)
    kiln_session_cookie_secure: bool = False

    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_llm_provider(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip().lower()
        aliases = {
            "none": "off",
            "disabled": "off",
            "openai-compatible": "openai",
        }
        return aliases.get(text, text)

    @field_validator("llm_base_url", "llm_model", "jenkins_user")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("llm_models")
    @classmethod
    def validate_llm_models(cls, value: str) -> str:
        # Illegal values refuse startup. parse_llm_model_ids does not truncate.
        text = value.strip()
        parse_llm_model_ids(text)
        return text

    @field_validator("git_provider", mode="before")
    @classmethod
    def normalize_git_provider(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        text = value.strip().lower()
        if text in {"", "none", "disabled"}:
            return "off"
        return text

    @field_validator(
        "jenkins_url",
        "git_base_url",
        "git_clone_base_url",
        "kiln_nexus_url",
        "kiln_nexus_build_url",
        "kiln_oidc_issuer_url",
        "kiln_oidc_redirect_uri",
        mode="before",
    )
    @classmethod
    def normalize_service_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return _clean_http_url(value)

    @field_validator(
        "kiln_oidc_client_id",
        "kiln_oidc_employment_claim",
        "kiln_oidc_employment_active_values",
        "kiln_oidc_scopes",
    )
    @classmethod
    def strip_oidc_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("jenkins_api_token", "git_token", "kiln_oidc_client_secret", mode="before")
    @classmethod
    def strip_optional_secret(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("kiln_nexus_repository")
    @classmethod
    def strip_nexus_repository(cls, value: str) -> str:
        return value.strip()

    @field_validator("kiln_nexus_username")
    @classmethod
    def strip_nexus_username(cls, value: str) -> str:
        text = value.strip()
        if "\r" in text or "\n" in text:
            raise ValueError("KILN_NEXUS_USERNAME 不能包含换行")
        return text

    @field_validator("kiln_nexus_password", mode="before")
    @classmethod
    def nexus_password(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            text = value.strip()
            if "\r" in text or "\n" in text:
                raise ValueError("KILN_NEXUS_PASSWORD 不能包含换行")
            if len(text) > 256:
                raise ValueError("KILN_NEXUS_PASSWORD 长度不能超过 256")
            return text
        return value

    @field_validator("kiln_artifact_root")
    @classmethod
    def artifact_root_is_local(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("必须设置 KILN_ARTIFACT_ROOT")
        if "://" in text:
            raise ValueError("KILN_ARTIFACT_ROOT 必须是本地目录，不是服务地址")
        return text

    @field_validator("kiln_api_token", mode="before")
    @classmethod
    def api_token_required(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("必须设置 KILN_API_TOKEN")
        return value.strip()

    @model_validator(mode="after")
    def openai_requires_base_url(self) -> "Settings":
        if self.llm_provider == "openai" and not self.llm_base_url:
            raise ValueError("LLM_PROVIDER=openai 时必须设置 LLM_BASE_URL")
        return self

    @model_validator(mode="after")
    def external_endpoints_are_complete(self) -> "Settings":
        jenkins = (self.jenkins_url, self.jenkins_user, self.jenkins_api_token_value())
        if any(jenkins) and not all(jenkins):
            raise ValueError(
                "配置 Jenkins 时必须同时设置 JENKINS_URL、JENKINS_USER 和 JENKINS_API_TOKEN"
            )
        if self.git_provider != "off":
            if not self.git_base_url:
                raise ValueError("GIT_PROVIDER 为 gitea 或 gitlab 时必须设置 GIT_BASE_URL")
            if not self.git_token_value():
                raise ValueError("GIT_PROVIDER 为 gitea 或 gitlab 时必须设置 GIT_TOKEN")
        return self

    @model_validator(mode="after")
    def oidc_settings_are_complete(self) -> "Settings":
        required = (
            self.kiln_oidc_issuer_url,
            self.kiln_oidc_client_id,
            self.oidc_client_secret_value(),
            self.kiln_oidc_redirect_uri,
            self.kiln_oidc_employment_claim,
            self.kiln_oidc_employment_active_values,
        )
        if any(required) and not all(required):
            raise ValueError(
                "配置用户信息同步时必须同时设置 KILN_OIDC_ISSUER_URL、"
                "KILN_OIDC_CLIENT_ID、KILN_OIDC_CLIENT_SECRET、KILN_OIDC_REDIRECT_URI、"
                "KILN_OIDC_EMPLOYMENT_CLAIM 和 KILN_OIDC_EMPLOYMENT_ACTIVE_VALUES"
            )
        claim = self.kiln_oidc_employment_claim
        if claim and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", claim):
            raise ValueError("KILN_OIDC_EMPLOYMENT_CLAIM 格式不正确")
        if all(required) and not self.oidc_employment_active_values():
            raise ValueError("KILN_OIDC_EMPLOYMENT_ACTIVE_VALUES 不能为空")
        return self

    @model_validator(mode="after")
    def nexus_settings_are_complete(self) -> "Settings":
        url = self.kiln_nexus_url
        repository = self.kiln_nexus_repository
        username = self.kiln_nexus_username
        password = self.nexus_password_value()
        build_url = self.kiln_nexus_build_url
        if bool(url) != bool(repository):
            raise ValueError("配置依赖代理时必须同时设置 KILN_NEXUS_URL 和 KILN_NEXUS_REPOSITORY")
        if bool(username) != bool(password):
            raise ValueError(
                "配置依赖代理认证时必须同时设置 KILN_NEXUS_USERNAME 和 KILN_NEXUS_PASSWORD"
            )
        if not url and (build_url or username or password):
            raise ValueError(
                "未设置 KILN_NEXUS_URL 时不能设置 KILN_NEXUS_BUILD_URL、"
                "KILN_NEXUS_USERNAME 或 KILN_NEXUS_PASSWORD"
            )
        if repository and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", repository) is None:
            raise ValueError("KILN_NEXUS_REPOSITORY 格式不正确")
        return self

    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.kiln_cors_origins.split(",") if item.strip()]

    def llm_model_ids(self) -> list[str]:
        """Parsed ``LLM_MODELS`` allow-list. Empty when the variable is empty.

        This is the list before a missing ``LLM_MODEL`` is prepended. The
        parse rules, including the startup refusals, live in
        ``parse_llm_model_ids``.
        """

        return parse_llm_model_ids(self.llm_models)

    def llm_seed_ids(self) -> list[str]:
        """Environment seed for the admin catalog preview.

        Same construction as the openai catalog, and independent of
        ``LLM_PROVIDER``: parsed ``LLM_MODELS``, or ``[LLM_MODEL]`` when that
        list is empty, with a missing ``LLM_MODEL`` prepended. ``mock`` and
        ``off`` still publish an empty list from ``GET /llm/provider``.
        """

        listed = self.llm_model_ids()
        if not listed:
            return [self.llm_model] if self.llm_model else []
        if self.llm_model and self.llm_model not in listed:
            return [self.llm_model, *listed]
        return list(listed)

    def llm_catalog(self) -> list[str]:
        """Model ids the console may pick. Empty unless the provider is openai.

        An empty allow-list means the catalog is the configured ``LLM_MODEL``,
        or an empty list when that is also empty. A set ``LLM_MODEL`` that is
        missing from the allow-list is prepended so the default stays
        selectable. That extra entry can make the catalog 33 items and is not
        a parse error. ``mock`` and ``off`` do not publish a catalog.
        """

        if self.llm_provider != "openai":
            return []
        return self.llm_seed_ids()

    def llm_request_default(self) -> str:
        """Model id used when a chat request omits ``model``.

        ``LLM_MODEL`` when it is set. Otherwise the first catalog item, when
        the openai catalog is non-empty. Empty when neither is available.
        """

        if self.llm_model:
            return self.llm_model
        catalog = self.llm_catalog()
        if catalog:
            return catalog[0]
        return ""

    def llm_api_key_value(self) -> str:
        return self.llm_api_key.get_secret_value()

    def api_token_value(self) -> str:
        return self.kiln_api_token.get_secret_value()

    def jenkins_api_token_value(self) -> str:
        return self.jenkins_api_token.get_secret_value()

    def git_token_value(self) -> str:
        return self.git_token.get_secret_value()

    def oidc_client_secret_value(self) -> str:
        return self.kiln_oidc_client_secret.get_secret_value()

    def nexus_password_value(self) -> str:
        return self.kiln_nexus_password.get_secret_value()

    def oidc_employment_active_values(self) -> frozenset[str]:
        return frozenset(
            item.strip()
            for item in self.kiln_oidc_employment_active_values.split(",")
            if item.strip()
        )

    def oidc_scopes(self) -> str:
        if not self.oidc_configured:
            return ""
        return self.kiln_oidc_scopes or "openid profile email"

    @property
    def oidc_configured(self) -> bool:
        return bool(
            self.kiln_oidc_issuer_url
            and self.kiln_oidc_client_id
            and self.oidc_client_secret_value()
            and self.kiln_oidc_redirect_uri
            and self.kiln_oidc_employment_claim
            and self.oidc_employment_active_values()
        )

    def git_clone_base(self) -> str:
        """Base URL Jenkins uses to clone. Falls back to the API's git URL."""

        return self.git_clone_base_url or self.git_base_url

    def nexus_build_base(self) -> str:
        """Base URL Jenkins uses for the dependency proxy. Falls back to the API URL."""

        return self.kiln_nexus_build_url or self.kiln_nexus_url

    @property
    def nexus_configured(self) -> bool:
        return bool(self.kiln_nexus_url and self.kiln_nexus_repository)

    @property
    def executor_configured(self) -> bool:
        return bool(self.jenkins_url and self.jenkins_user and self.jenkins_api_token_value())

    @property
    def git_configured(self) -> bool:
        return self.git_provider != "off" and bool(self.git_base_url and self.git_token_value())


def parse_llm_model_ids(value: str) -> list[str]:
    """Parse ``LLM_MODELS`` the same way as other comma-separated settings.

    Split on commas, trim whitespace, drop empty items, and dedupe by first
    occurrence. An item longer than 128 characters refuses process startup;
    the id is not truncated. More than 32 items after that cleanup also
    refuses startup; the list is not silently cut to the first 32. Counts
    are taken after trim, drop, and dedupe, so repeated ids do not consume
    extra slots.

    Prepending a missing ``LLM_MODEL`` is not done here. That step runs when
    the openai catalog is built, may add one extra entry, and is not a parse
    error.
    """

    seen: list[str] = []
    known: set[str] = set()
    for item in value.split(","):
        text = item.strip()
        if not text or text in known:
            continue
        if len(text) > LLM_MODEL_ID_MAX_LENGTH:
            raise ValueError("LLM_MODELS 中的单项不能超过 128 个字符，不会截断")
        known.add(text)
        seen.append(text)
    if len(seen) > LLM_MODELS_MAX_ITEMS:
        raise ValueError("LLM_MODELS 不能超过 32 项，不会只保留前 32 项")
    return seen


def _clean_http_url(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = text.rstrip("/")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("地址必须是 http 或 https URL")
    if parts.username or parts.password:
        raise ValueError("地址里不能带用户名或口令")
    if parts.query or parts.fragment:
        raise ValueError("地址不能带查询串或片段")
    return text


@lru_cache
def get_settings() -> Settings:
    return Settings()
