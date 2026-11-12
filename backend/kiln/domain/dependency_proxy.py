"""Optional dependency-proxy status.

The proxy is off unless configured. Artifact bytes stay on the platform, so
``publishes_artifacts`` is always false. The password is not a field here.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DependencyProxyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["nexus", "off"]
    configured: bool
    base_url: str | None
    repository: str | None
    auth: Literal["anonymous", "credentials"] | None
    reachable: bool | None
    detail: str | None
    publishes_artifacts: Literal[False] = False
