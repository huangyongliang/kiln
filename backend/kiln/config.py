"""Runtime configuration.

Values come from process environment variables and, when present, a local `.env`
file. Secrets stay out of the repository.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Kiln process settings.

    `DATABASE_URL` is accepted so the later lab can point at Postgres. This
    skeleton does not open a database connection.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kiln_env: str = "local"
    kiln_http_host: str = "0.0.0.0"
    kiln_http_port: int = Field(default=8000, ge=1, le=65535)
    kiln_cors_origins: str = "http://localhost:5173,http://localhost:8080"

    database_url: str = "postgresql://kiln:kiln@localhost:5432/kiln"

    llm_provider: Literal["openai", "mock", "off"] = "off"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)

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

    @field_validator("llm_base_url", "llm_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def openai_requires_base_url(self) -> "Settings":
        if self.llm_provider == "openai" and not self.llm_base_url:
            raise ValueError("LLM_PROVIDER=openai 时必须设置 LLM_BASE_URL")
        return self

    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.kiln_cors_origins.split(",") if item.strip()]

    def llm_api_key_value(self) -> str:
        return self.llm_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
