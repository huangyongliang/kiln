"""Request dependencies bound to application state."""

from fastapi import Request

from kiln.config import Settings
from kiln.db.store import PostgresStore
from kiln.llm.provider import LLMProvider


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> PostgresStore:
    return request.app.state.store


def get_llm(request: Request) -> LLMProvider:
    return request.app.state.llm
