"""Repository and ref checks shared by the git API and product builds.

These rules keep owner, name, and ref out of shell metacharacters. They do not
embed a host.
"""

import re

from kiln.errors import BadRequest

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


def parse_repository(value: str) -> tuple[str, str]:
    parts = value.strip().split("/")
    if len(parts) < 2 or any(_SEGMENT.fullmatch(part) is None for part in parts):
        raise BadRequest("仓库必须是 owner/name")
    name = parts[-1]
    owner = "/".join(parts[:-1])
    return owner, name


def parse_ref(value: str) -> str:
    text = value.strip()
    if (
        _REF.fullmatch(text) is None
        or text.startswith("/")
        or text.endswith("/")
        or ".." in text
        or "//" in text
    ):
        raise BadRequest("引用无效")
    return text
