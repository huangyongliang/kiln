"""Product build: real git checkout, real Jenkins, real stored bytes.

The compat marker job is not this path. These tests skip only when
KILN_SKIP_EXTERNAL_TESTS=1, and only the cases that need Jenkins and Gitea.
"""

import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from kiln.db.engine import create_db_engine
from kiln.domain.builds import repository_in_skill_scope
from kiln.domain.jobs import JobStatus
from kiln.errors import BadRequest
from kiln.executor.script import product_build_command, product_job_config
from kiln.git.refs import parse_ref
from kiln.main import create_app
from tests.external import ExternalTargets, external_settings
from tests.support import app_settings, auth_headers, postgres_url, publish_skill, truncate_domain
from tests.test_persistence import _start_api, _stop, _wait_healthy

_BUILD_SCRIPT = """#!/bin/sh
set -eu
mkdir -p kiln-dist
size=$(wc -c < payload.txt)
size=$(printf '%s' "$size" | tr -cd '0-9')
{
  printf 'kiln1 %s\\n' "$size"
  cat payload.txt
} > kiln-dist/artifact.bin
"""


def test_product_build_command_does_not_trace_the_token() -> None:
    token = "unit-token-value"
    command = product_build_command(
        clone_url="http://kiln-ci@git.internal:3000/ada/demo.git",
        ref="main",
        sha="ab" * 20,
        git_user="kiln-ci",
        git_token=token,
    )
    hidden, traced = command.split("set -x\n", 1)
    assert command.startswith("set +x\n")
    assert token in hidden
    assert token not in traced
    assert "kiln-build.sh" in traced
    assert "http://kiln-ci@git.internal:3000/ada/demo.git" in hidden
    assert token not in "http://kiln-ci@git.internal:3000/ada/demo.git"
    config = product_job_config(command)
    assert "hudson.tasks.ArtifactArchiver" in config
    assert "src/kiln-dist/artifact.bin" in config
    assert config.index("set +x") < config.index(token) < config.index("set -x")
    assert "deploys_to_runtime" not in config


def test_product_build_command_rejects_shell_metacharacters() -> None:
    kwargs = {
        "clone_url": "http://git.internal:3000/ada/demo.git",
        "ref": "main",
        "sha": "cd" * 20,
        "git_user": "kiln-ci",
        "git_token": "unit-token-value",
    }
    with pytest.raises(BadRequest):
        product_build_command(**{**kwargs, "ref": "main;touch"})
    with pytest.raises(BadRequest):
        product_build_command(**{**kwargs, "git_token": "quote'token"})
    with pytest.raises(BadRequest):
        product_build_command(**{**kwargs, "sha": "not-a-sha"})
    with pytest.raises(BadRequest):
        parse_ref("../main")


def test_unconfigured_product_build_does_not_deploy(client: TestClient) -> None:
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    document = spec.json()
    for path in ("/api/v1/builds", "/api/v1/builds/{build_id}", "/api/v1/builds/{build_id}/log"):
        assert path in document["paths"]
        for operation in document["paths"][path].values():
            if isinstance(operation, dict) and "responses" in operation:
                assert operation.get("security"), path
    created = client.post(
        "/api/v1/builds",
        json={
            "repository": "ada/demo",
            "ref": "main",
            "artifact_name": "demo-pkg",
            "version": "1",
            "package_type": "generic",
        },
    )
    assert created.status_code == 503
    assert "JENKINS_URL" in created.json()["detail"]
    assert client.get("/api/v1/builds").status_code == 503


def test_repository_scope_compares_normalized_owner_and_raw_entries() -> None:
    assert repository_in_skill_scope("group/demo", []) is True
    assert repository_in_skill_scope(" group/demo ", ["group/demo"]) is True
    assert repository_in_skill_scope("org/team/app", ["  org/team/app  "]) is True
    assert repository_in_skill_scope("group/demo", ["other/repo"]) is False
    assert repository_in_skill_scope("Group/Demo", ["group/demo"]) is False
    assert repository_in_skill_scope("group/demo", ["lab repo"]) is False
    assert repository_in_skill_scope("group/demo", ["group/demo/"]) is False


def test_product_build_requires_a_published_skill_and_keeps_bytes_local(tmp_path: Path) -> None:
    app, git, jenkins = _ready_executor(tmp_path)
    payload = b"kiln-bytes\n"
    jenkins.payload = payload
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        missing = client.post("/api/v1/builds", json=_body())
        assert missing.status_code == 422
        invalid = client.post("/api/v1/builds", json=_body(skill_id="not-a-uuid"))
        assert invalid.status_code == 422
        absent = client.post(
            "/api/v1/builds",
            json=_body(skill_id="00000000-0000-4000-8000-0000000000aa"),
        )
        assert absent.status_code == 404
        assert absent.json()["detail"] == "skill 不存在"

        draft = client.post(
            "/api/v1/skills",
            json={"name": "still-draft", "version": "0.1.0", "allowed_repositories": ["ada/demo"]},
        )
        assert draft.status_code == 201, draft.text
        draft_id = draft.json()["id"]
        denied_draft = client.post("/api/v1/builds", json=_body(skill_id=draft_id))
        assert denied_draft.status_code == 409
        assert denied_draft.json()["detail"] == "只有已发布的 Skill 可以发起构建"
        submitted = client.post(f"/api/v1/skills/{draft_id}/submit-review")
        assert submitted.status_code == 200, submitted.text
        denied_review = client.post("/api/v1/builds", json=_body(skill_id=draft_id))
        assert denied_review.status_code == 409

        narrow = publish_skill(client, "narrow-flow", ["other/repo"])
        outside = client.post("/api/v1/builds", json=_body(skill_id=narrow["id"]))
        assert outside.status_code == 422
        assert outside.json()["detail"] == "仓库不在该 Skill 的允许范围内"
        assert jenkins.calls == []
        assert git.calls == []
        with app.state.engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM artifacts")).scalar_one() == 0

        open_skill = publish_skill(client, "open-flow", [])
        created = client.post(
            "/api/v1/builds",
            json=_body(skill_id=open_skill["id"], artifact_name="open-pkg"),
        )
        assert created.status_code == 201, created.text
        build = created.json()
        assert build["skill_id"] == open_skill["id"]
        assert build["skill_name"] == "open-flow"
        assert build["deploys_to_runtime"] is False
        assert build["repository"] == "ada/demo"
        finished = client.get(f"/api/v1/builds/{build['id']}")
        assert finished.status_code == 200, finished.text
        stored = finished.json()
        assert stored["status"] == "success"
        assert stored["content_available"] is True
        assert stored["deploys_to_runtime"] is False
        assert stored["skill_id"] == open_skill["id"]
        digest = hashlib.sha256(payload).hexdigest()
        assert (tmp_path / "artifacts" / "sha256" / digest).read_bytes() == payload
        assert jenkins.parameters is None
        assert [call[0] for call in jenkins.calls].count("trigger_build") == 1

        spaced = publish_skill(client, "spaced-flow", ["  ada/demo  "])
        matched = client.post(
            "/api/v1/builds",
            json=_body(
                skill_id=spaced["id"],
                repository=" ada/demo ",
                artifact_name="spaced-pkg",
            ),
        )
        assert matched.status_code == 201, matched.text
        assert matched.json()["skill_name"] == "spaced-flow"
        assert matched.json()["repository"] == "ada/demo"

        legacy_id = uuid4()
        artifact = client.post(
            "/api/v1/artifacts",
            json={"name": "legacy-pkg", "package_type": "generic"},
        )
        assert artifact.status_code == 201, artifact.text
        app.state.builds.create_build(
            build_id=legacy_id,
            name="legacy",
            jenkins_job_name=f"kiln-build-{legacy_id.hex}",
            repository="ada/demo",
            ref="main",
            commit_sha="b" * 40,
            artifact_id=artifact.json()["id"],
            version="1.0.0",
            package_type="generic",
            queue_url=None,
        )
        app.state.builds.save_run(legacy_id, status=JobStatus.failure, build_number=1)
        listed = client.get("/api/v1/builds")
        assert listed.status_code == 200, listed.text
        legacy = next(item for item in listed.json()["items"] if item["id"] == str(legacy_id))
        assert legacy["skill_id"] is None
        assert legacy["skill_name"] is None
        detail = client.get(f"/api/v1/builds/{legacy_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["skill_id"] is None
        assert detail.json()["skill_name"] is None
        with app.state.engine.connect() as connection:
            builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
        assert builds == 3
        assert [call[0] for call in jenkins.calls].count("trigger_build") == 2
        assert git.calls


def test_post_build_requires_executor_run_and_skill_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, git, jenkins = _ready_executor(tmp_path)
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        skill = publish_skill(client, "gated-flow", ["ada/demo"])
        created = client.post(
            "/api/v1/accounts",
            json={
                "username": "ada_split",
                "display_name": "拆分",
                "password": "correct-horse-1",
                "role_ids": ["builder"],
            },
        )
        assert created.status_code == 201, created.text
        session = TestClient(app)
        logged = session.post(
            "/api/v1/auth/login",
            json={"username": "ada_split", "password": "correct-horse-1"},
        )
        assert logged.status_code == 200, logged.text
        payload = _body(skill_id=skill["id"])
        from kiln import auth

        for codes in (frozenset({"executor.run"}), frozenset({"skill.use"})):
            monkeypatch.setattr(
                auth,
                "permissions_for",
                lambda _role_ids, granted=codes: granted,
            )
            denied = session.post("/api/v1/builds", json=payload)
            assert denied.status_code == 403
            assert denied.json()["detail"] == "没有权限"
        with app.state.engine.connect() as connection:
            builds = connection.execute(text("SELECT count(*) FROM product_builds")).scalar_one()
            artifacts = connection.execute(text("SELECT count(*) FROM artifacts")).scalar_one()
        assert builds == 0
        assert artifacts == 0
        assert jenkins.calls == []
        assert git.calls == []


def test_product_build_stores_repository_bytes_and_retrieve_returns_them(
    external_targets: ExternalTargets,
    tmp_path: Path,
) -> None:
    token = external_targets.git_token
    payload = b"kiln-payload-" + uuid4().hex.encode() + b"\n\x00\xff"
    expected = f"kiln1 {len(payload)}\n".encode() + payload
    repo_name = f"kiln-src-{uuid4().hex[:10]}"
    artifact_name = f"pkg{uuid4().hex[:8]}"
    full_name = ""
    job_name = ""
    root = tmp_path / "artifacts"
    settings = external_settings(external_targets, artifact_root=str(root))
    app = create_app(settings)
    try:
        created_repo = _create_gitea_repo(external_targets, repo_name)
        full_name = created_repo["full_name"]
        _put_gitea_file(external_targets, full_name, "payload.txt", payload, "add payload")
        _put_gitea_file(
            external_targets,
            full_name,
            "kiln-build.sh",
            _BUILD_SCRIPT.encode(),
            "add build",
        )
        sha = _commit_sha(external_targets, full_name, "main")
        with TestClient(app, headers=auth_headers()) as client:
            truncate_domain(app.state.engine)
            skill = publish_skill(client, f"flow-{uuid4().hex[:8]}", [full_name])
            created = client.post(
                "/api/v1/builds",
                json={
                    "skill_id": skill["id"],
                    "repository": full_name,
                    "ref": "main",
                    "name": "product",
                    "artifact_name": artifact_name,
                    "version": "1.0.0",
                    "package_type": "generic",
                },
            )
            assert created.status_code == 201, _show(created, token)
            _reject_secret(created.text, token)
            build = created.json()
            assert build["skill_id"] == skill["id"]
            assert build["skill_name"] == skill["name"]
            assert build["deploys_to_runtime"] is False
            assert build["status"] == "queued"
            assert build["content_available"] is False
            assert build["commit_sha"] == sha.lower()
            assert build["repository"] == full_name
            job_name = build["jenkins_job_name"]
            assert job_name.startswith("kiln-build-")
            build_id = build["id"]

            finished = _wait_until_stored(client, build_id, token)
            assert finished["status"] == "success"
            assert finished["content_available"] is True
            assert finished["deploys_to_runtime"] is False
            assert finished["build_number"] >= 1
            assert finished["commit_sha"] == sha.lower()

            log = client.get(f"/api/v1/builds/{build_id}/log")
            assert log.status_code == 200, _show(log, token)
            log_text = log.json()["text"]
            if token in log_text:
                raise AssertionError("git token appeared in the Jenkins log")
            assert sha.lower() in log_text

            artifact = client.get(f"/api/v1/artifacts/{finished['artifact_id']}")
            assert artifact.status_code == 200, artifact.text
            version = artifact.json()["versions"][0]
            assert version["content_available"] is True
            assert version["source"] == "build"
            assert version["checksum"]["value"] == hashlib.sha256(expected).hexdigest()
            assert version["size_bytes"] == len(expected)

            reservation = client.post(
                f"/api/v1/artifacts/{finished['artifact_id']}/versions/1.0.0/download-reservations",
                json={"requester": "builder_1"},
            )
            assert reservation.status_code == 201, reservation.text
            retrieve_path = reservation.json()["retrieve_path"]
            meta = client.get(retrieve_path)
            assert meta.status_code == 200, meta.text
            assert meta.json()["content_available"] is True
            content_path = meta.json()["content_path"]
            downloaded = client.get(content_path)
            assert downloaded.status_code == 200
            assert downloaded.content == expected
            blob = root / "sha256" / version["checksum"]["value"]
            assert blob.read_bytes() == expected

            _assert_jenkins_archived(
                external_targets,
                job_name,
                finished["build_number"],
                expected,
            )
            _assert_anonymous_cannot_store_or_download(
                settings,
                external_targets,
                finished["artifact_id"],
                content_path,
                expected,
                token,
            )
        _assert_survives_restart(
            root,
            content_path,
            finished["artifact_id"],
            build_id,
            sha,
            expected,
        )
    finally:
        if job_name:
            jenkins = app.state.jenkins
            if jenkins is not None:
                try:
                    jenkins.delete_job(job_name)
                except Exception:
                    pass
        if full_name:
            _delete_gitea_repo(external_targets, full_name)


def _wait_until_stored(client: TestClient, build_id: str, token: str) -> dict:
    deadline = time.monotonic() + 180
    last = ""
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/builds/{build_id}")
        if response.status_code == 502:
            last = _show(response, token)
            time.sleep(1)
            continue
        assert response.status_code == 200, _show(response, token)
        _reject_secret(response.text, token)
        body = response.json()
        if body["status"] in {"failure", "unstable", "aborted", "not_built"}:
            raise AssertionError(_failure_detail(client, build_id, token))
        if body["status"] == "success" and body["content_available"]:
            return body
        last = body["status"]
        time.sleep(1)
    raise AssertionError(f"{last}\n{_failure_detail(client, build_id, token)}")


def _failure_detail(client: TestClient, build_id: str, token: str) -> str:
    log = client.get(f"/api/v1/builds/{build_id}/log")
    if log.status_code != 200:
        return _show(log, token)
    text = log.json().get("text", "")
    if token in text:
        return "git token appeared in the Jenkins log"
    return text


def _assert_jenkins_archived(
    targets: ExternalTargets,
    job_name: str,
    build_number: int,
    expected: bytes,
) -> None:
    encoded = quote(job_name, safe="")
    remote = httpx.get(
        f"{targets.jenkins_url}/job/{encoded}/{build_number}/artifact/src/kiln-dist/artifact.bin",
        auth=(targets.jenkins_user, targets.jenkins_token),
        timeout=30,
        trust_env=False,
    )
    assert remote.status_code == 200, remote.status_code
    assert remote.content == expected


def _assert_anonymous_cannot_store_or_download(
    settings,
    targets: ExternalTargets,
    artifact_id: str,
    content_path: str,
    expected: bytes,
    token: str,
) -> None:
    before = _jenkins_names(targets)
    anon_app = create_app(settings)
    with TestClient(anon_app) as anon:
        denied = anon.get(content_path)
        assert denied.status_code == 401
        assert denied.json()["detail"] == "需要访问令牌"
        assert expected not in denied.content
        planted = anon.put(
            f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
            content=b"replaced-bytes",
            headers={
                "Content-Type": "application/octet-stream",
                "X-Kiln-Checksum-Algorithm": "sha256",
                "X-Kiln-Checksum": hashlib.sha256(b"replaced-bytes").hexdigest(),
                "X-Kiln-Source": "upload",
            },
        )
        assert planted.status_code == 401
        assert expected not in planted.content
        created = anon.post(
            "/api/v1/builds",
            json={
                "repository": "ada/demo",
                "ref": "main",
                "artifact_name": "anon-pkg",
                "version": "1",
                "package_type": "generic",
            },
        )
        assert created.status_code == 401
        _reject_secret(created.text, token)
    assert _jenkins_names(targets) == before


def _assert_survives_restart(
    root: Path,
    content_path: str,
    artifact_id: str,
    build_id: str,
    sha: str,
    expected: bytes,
) -> None:
    database_url = postgres_url()
    proc, log, port = _start_api(database_url, str(root))
    try:
        base = _wait_healthy(proc, log, port)
        denied = httpx.get(f"{base}{content_path}", timeout=5, trust_env=False)
        assert denied.status_code == 401
        assert expected not in denied.content
        with httpx.Client(timeout=5, trust_env=False, headers=auth_headers()) as http:
            meta = http.get(f"{base}{content_path.rsplit('/content', 1)[0]}")
            assert meta.status_code == 200, meta.text
            assert meta.json()["content_available"] is True
            downloaded = http.get(f"{base}{content_path}")
            assert downloaded.status_code == 200
            assert downloaded.content == expected
            artifact = http.get(f"{base}/api/v1/artifacts/{artifact_id}")
            assert artifact.status_code == 200, artifact.text
            version = artifact.json()["versions"][0]
            assert version["content_available"] is True
            assert version["source"] == "build"
        engine = create_db_engine(database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT status, commit_sha, content_stored "
                        "FROM product_builds WHERE id = :id"
                    ),
                    {"id": UUID(build_id)},
                ).one()
        finally:
            engine.dispose()
        assert row.status == "success"
        assert row.commit_sha == sha.lower()
        assert row.content_stored is True
        digest = hashlib.sha256(expected).hexdigest()
        assert (root / "sha256" / digest).read_bytes() == expected
    finally:
        _stop(proc)
        log.close()


def _show(response: httpx.Response, token: str) -> str:
    body = response.text.replace(token, "***") if token else response.text
    return f"HTTP {response.status_code}\n{body}"


def _reject_secret(body: str, token: str) -> None:
    if token and token in body:
        raise AssertionError("git token appeared in an API response")


def _gitea_headers(targets: ExternalTargets) -> dict[str, str]:
    return {"Authorization": f"token {targets.git_token}", "Accept": "application/json"}


def _create_gitea_repo(targets: ExternalTargets, name: str) -> dict:
    response = httpx.post(
        f"{targets.git_base_url}/api/v1/user/repos",
        headers=_gitea_headers(targets),
        json={"name": name, "private": True, "auto_init": True, "default_branch": "main"},
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 201, response.status_code
    body = response.json()
    assert isinstance(body, dict)
    return body


def _put_gitea_file(
    targets: ExternalTargets,
    full_name: str,
    path: str,
    content: bytes,
    message: str,
) -> None:
    response = httpx.post(
        f"{targets.git_base_url}/api/v1/repos/{full_name}/contents/{path}",
        headers=_gitea_headers(targets),
        json={
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": "main",
        },
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 201, response.status_code


def _commit_sha(targets: ExternalTargets, full_name: str, branch: str) -> str:
    response = httpx.get(
        f"{targets.git_base_url}/api/v1/repos/{full_name}/git/commits/{branch}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 200, response.status_code
    sha = response.json().get("sha")
    assert isinstance(sha, str) and sha
    return sha


def _delete_gitea_repo(targets: ExternalTargets, full_name: str) -> None:
    httpx.delete(
        f"{targets.git_base_url}/api/v1/repos/{full_name}",
        headers=_gitea_headers(targets),
        timeout=30,
        trust_env=False,
    )


class _Resolved:
    def __init__(self, sha: str) -> None:
        self.sha = sha


class _RecordingGit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def resolve_ref(self, owner: str, name: str, ref: str) -> _Resolved:
        self.calls.append(("resolve_ref", owner, name, ref))
        return _Resolved("a" * 40)

    def authenticated_username(self) -> str:
        self.calls.append(("authenticated_username",))
        return "kiln"


class _RecordingJenkins:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.parameters: dict[str, str] | None = None
        self.payload = b"kiln-bytes\n"
        self.create_error: Exception | None = None
        self.trigger_error: Exception | None = None
        self.on_create: object | None = None
        self.on_trigger: object | None = None
        self.fail_read_progress = False

    def create_product_job(self, job_name: str, **_kwargs: object) -> None:
        self.calls.append(("create_product_job", job_name))
        if callable(self.on_create):
            self.on_create(job_name)
        if self.create_error is not None:
            raise self.create_error

    def trigger_build(
        self,
        job_name: str,
        *,
        parameters: dict[str, str] | None = None,
    ) -> str:
        self.calls.append(("trigger_build", job_name))
        self.parameters = parameters
        if callable(self.on_trigger):
            self.on_trigger(job_name)
        if self.trigger_error is not None:
            raise self.trigger_error
        return "http://127.0.0.1:9/queue/item/1/"

    def delete_job(self, job_name: str) -> None:
        self.calls.append(("delete_job", job_name))

    def read_progress(self, *_args: object, **_kwargs: object) -> tuple[str, int]:
        if self.fail_read_progress:
            raise AssertionError("read_progress was called")
        return "success", 1

    def download_artifact(self, *_args: object, **_kwargs: object) -> bytes:
        return self.payload

    def read_log(self, *_args: object, **_kwargs: object) -> tuple[str, bool]:
        return "log", False


class _NexusSpy:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"nexus {name} was called")


def _ready_executor(tmp_path: Path):
    app = create_app(
        app_settings(
            git_base_url="http://127.0.0.1:9",
            git_clone_base_url="http://127.0.0.1:9",
            git_token="test-token",
            kiln_artifact_root=str(tmp_path / "artifacts"),
        )
    )
    git = _RecordingGit()
    jenkins = _RecordingJenkins()
    app.state.git_source = git
    app.state.jenkins = jenkins
    app.state.nexus = _NexusSpy()
    return app, git, jenkins


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repository": "ada/demo",
        "ref": "main",
        "artifact_name": "demo-pkg",
        "version": "1.0.0",
        "package_type": "generic",
    }
    payload.update(overrides)
    return payload


def _jenkins_names(targets: ExternalTargets) -> set[str]:
    response = httpx.get(
        f"{targets.jenkins_url}/api/json",
        params={"tree": "jobs[name]"},
        auth=(targets.jenkins_user, targets.jenkins_token),
        timeout=30,
        trust_env=False,
    )
    assert response.status_code == 200, response.status_code
    return {item["name"] for item in response.json().get("jobs", [])}
