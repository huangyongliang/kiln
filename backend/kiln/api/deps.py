"""Request dependencies bound to application state."""

import httpx
from fastapi import Request

from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.db.jobs import JobStore
from kiln.db.store import PostgresStore
from kiln.errors import GIT_SOURCE_UNCONFIGURED_DETAIL, JENKINS_UNCONFIGURED_DETAIL, NotConfigured
from kiln.executor.jenkins import JenkinsClient
from kiln.git.source import GitSource
from kiln.llm.provider import LLMProvider
from kiln.nexus.client import NexusClient


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> PostgresStore:
    return request.app.state.store


def get_oidc_http(request: Request) -> httpx.Client:
    return request.app.state.oidc_http


def get_llm(request: Request) -> LLMProvider:
    return request.app.state.llm


def get_job_store(request: Request) -> JobStore:
    return request.app.state.jobs


def get_build_store(request: Request) -> BuildStore:
    return request.app.state.builds


def get_jenkins(request: Request) -> JenkinsClient:
    client = request.app.state.jenkins
    if client is None:
        raise NotConfigured(JENKINS_UNCONFIGURED_DETAIL)
    return client


def get_git_source(request: Request) -> GitSource:
    source = request.app.state.git_source
    if source is None:
        raise NotConfigured(GIT_SOURCE_UNCONFIGURED_DETAIL)
    return source


def get_nexus(request: Request) -> NexusClient | None:
    return request.app.state.nexus
