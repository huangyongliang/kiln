"""Request dependencies bound to application state."""

from fastapi import Request

from kiln.config import Settings
from kiln.db.builds import BuildStore
from kiln.db.jobs import JobStore
from kiln.db.store import PostgresStore
from kiln.errors import NotConfigured
from kiln.executor.jenkins import JenkinsClient
from kiln.git.source import GitSource
from kiln.llm.provider import LLMProvider


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> PostgresStore:
    return request.app.state.store


def get_llm(request: Request) -> LLMProvider:
    return request.app.state.llm


def get_job_store(request: Request) -> JobStore:
    return request.app.state.jobs


def get_build_store(request: Request) -> BuildStore:
    return request.app.state.builds


def get_jenkins(request: Request) -> JenkinsClient:
    client = request.app.state.jenkins
    if client is None:
        raise NotConfigured(
            "执行器未配置。请设置 JENKINS_URL、JENKINS_USER 和 JENKINS_API_TOKEN。"
        )
    return client


def get_git_source(request: Request) -> GitSource:
    source = request.app.state.git_source
    if source is None:
        raise NotConfigured(
            "代码源未配置。请将 GIT_PROVIDER 设为 gitea（实验室）或 gitlab（生产代码源），"
            "并设置 GIT_BASE_URL 与 GIT_TOKEN。"
        )
    return source
