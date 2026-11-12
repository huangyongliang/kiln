"""Shell and job config for a product build.

The script runs on Jenkins, not in the kiln process. The git token is assigned
only after `set +x`, so Jenkins' `sh -xe` does not echo it. The clone URL
carries the username and never the token.
"""

import re
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

from kiln.errors import BadRequest
from kiln.git.refs import parse_ref

# Jenkins archives this workspace-relative file after `kiln-build.sh` runs.
WORKSPACE_ARTIFACT = "src/kiln-dist/artifact.bin"

# Core Jenkins parameter names. Defaults in config.xml stay empty. The password
# is sent only on the build request, never written into this file.
NEXUS_PARAMETER_NAMES = (
    "KILN_NEXUS_URL",
    "KILN_NEXUS_REPOSITORY",
    "KILN_NEXUS_USERNAME",
    "KILN_NEXUS_PASSWORD",
)

_NEXUS_PROPERTIES = """  <properties>
    <hudson.model.ParametersDefinitionProperty>
      <parameterDefinitions>
        <hudson.model.StringParameterDefinition>
          <name>KILN_NEXUS_URL</name>
          <description></description>
          <defaultValue></defaultValue>
          <trim>false</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>KILN_NEXUS_REPOSITORY</name>
          <description></description>
          <defaultValue></defaultValue>
          <trim>false</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>KILN_NEXUS_USERNAME</name>
          <description></description>
          <defaultValue></defaultValue>
          <trim>false</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.PasswordParameterDefinition>
          <name>KILN_NEXUS_PASSWORD</name>
          <description></description>
          <defaultValue></defaultValue>
        </hudson.model.PasswordParameterDefinition>
      </parameterDefinitions>
    </hudson.model.ParametersDefinitionProperty>
  </properties>
"""

_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/-]{8,256}$")
_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def clone_url(base: str, owner: str, name: str) -> str:
    """HTTP clone URL with no userinfo. `owner` may contain slashes."""

    root = base.rstrip("/")
    parts = [quote(part, safe="") for part in (*owner.split("/"), name)]
    return f"{root}/{'/'.join(parts)}.git"


def clone_url_with_user(base: str, owner: str, name: str, user: str) -> str:
    """Clone URL that asks git for a password, not a username."""

    if _USER.fullmatch(user) is None:
        raise BadRequest("代码源用户名无法用于克隆")
    plain = clone_url(base, owner, name)
    parts = urlsplit(plain)
    if parts.username or parts.password or not parts.hostname:
        raise BadRequest("克隆地址无效")
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(user, safe='')}@{host}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def product_build_command(
    *,
    clone_url: str,
    ref: str,
    sha: str,
    git_user: str,
    git_token: str,
) -> str:
    """Fixed Jenkins shell. Callers do not supply the script."""

    checked_ref = parse_ref(ref)
    checked_sha = sha.strip().lower()
    if _USER.fullmatch(git_user) is None:
        raise BadRequest("代码源用户名无法用于克隆")
    if _SHA.fullmatch(checked_sha) is None:
        raise BadRequest("提交号无效")
    if _TOKEN.fullmatch(git_token) is None:
        raise BadRequest("代码源令牌无法用于构建")
    if not clone_url.startswith(("http://", "https://")) or any(
        char in clone_url for char in " \t\r\n'\""
    ):
        raise BadRequest("克隆地址无效")
    if git_token in clone_url:
        raise BadRequest("克隆地址不能包含令牌")
    # Jenkins runs this file with `sh -xe`. `set +x` is the first command so
    # the token assignment is not traced. `set -x` comes back only after the
    # token and the askpass file are gone.
    return (
        "set +x\n"
        "set -eu\n"
        "umask 077\n"
        'ask="$WORKSPACE/.kiln-askpass"\n'
        "cat > \"$ask\" << 'EOF'\n"
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *[Uu]sername*) printf '%s\\n' \"$KILN_GIT_USER\" ;;\n"
        "  *) printf '%s\\n' \"$KILN_GIT_TOKEN\" ;;\n"
        "esac\n"
        "EOF\n"
        'chmod 700 "$ask"\n'
        'export GIT_ASKPASS="$ask"\n'
        "export GIT_TERMINAL_PROMPT=0\n"
        "export LC_ALL=C\n"
        f"export KILN_GIT_USER='{git_user}'\n"
        f"export KILN_GIT_TOKEN='{git_token}'\n"
        "rm -rf src\n"
        "git init src\n"
        f"git -C src remote add origin '{clone_url}'\n"
        "git -c credential.helper= -c http.followRedirects=false "
        "-C src fetch --depth 1 origin "
        f"'{checked_ref}'\n"
        "git -C src checkout --detach FETCH_HEAD\n"
        'rm -f "$ask"\n'
        "unset KILN_GIT_TOKEN KILN_GIT_USER GIT_ASKPASS\n"
        "set -x\n"
        "actual=$(git -C src rev-parse HEAD)\n"
        "printf '%s\\n' \"$actual\"\n"
        f"test \"$actual\" = '{checked_sha}'\n"
        "cd src\n"
        "sh kiln-build.sh\n"
        "test -s kiln-dist/artifact.bin\n"
    )


def product_job_config(command: str, *, nexus_parameters: bool = False) -> str:
    """Freestyle job XML. Unset Nexus leaves this byte-identical to the original.

    When ``nexus_parameters`` is true the job declares four core parameters with
    empty defaults. The password value is not an argument of this function.
    """

    escaped = escape(command)
    archived = escape(WORKSPACE_ARTIFACT)
    properties = _NEXUS_PROPERTIES if nexus_parameters else "  <properties/>\n"
    return (
        "<?xml version='1.1' encoding='UTF-8'?>\n"
        "<project>\n"
        "  <description>kiln product build</description>\n"
        "  <keepDependencies>false</keepDependencies>\n"
        f"{properties}"
        '  <scm class="hudson.scm.NullSCM"/>\n'
        "  <canRoam>true</canRoam>\n"
        "  <disabled>false</disabled>\n"
        "  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>\n"
        "  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>\n"
        "  <triggers/>\n"
        "  <concurrentBuild>false</concurrentBuild>\n"
        "  <quietPeriod>0</quietPeriod>\n"
        "  <builders>\n"
        "    <hudson.tasks.Shell>\n"
        f"      <command>{escaped}</command>\n"
        "    </hudson.tasks.Shell>\n"
        "  </builders>\n"
        "  <publishers>\n"
        "    <hudson.tasks.ArtifactArchiver>\n"
        f"      <artifacts>{archived}</artifacts>\n"
        "      <allowEmptyArchive>false</allowEmptyArchive>\n"
        "      <onlyIfSuccessful>true</onlyIfSuccessful>\n"
        "      <fingerprint>false</fingerprint>\n"
        "      <defaultExcludes>true</defaultExcludes>\n"
        "      <caseSensitive>true</caseSensitive>\n"
        "      <followSymlinks>false</followSymlinks>\n"
        "    </hudson.tasks.ArtifactArchiver>\n"
        "  </publishers>\n"
        "  <buildWrappers/>\n"
        "</project>\n"
    )
