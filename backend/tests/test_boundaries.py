import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]


def test_skeleton_does_not_shell_out() -> None:
    for path in (BACKEND / "kiln").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "subprocess" not in text
        assert "os.system" not in text
        assert "shell=True" not in text


def test_compose_contract_prefixes_container_names() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    names = re.findall(r"container_name:\s*(\S+)", text)
    assert names == [
        "kiln-postgres",
        "kiln-api",
        "kiln-console",
        "kiln-jenkins",
        "kiln-gitea",
    ]
    assert all(name.startswith("kiln-") for name in names)
    assert "profiles: [lab]" in text or 'profiles: ["lab"]' in text
    assert "postgres:" in text
    assert "jenkins/jenkins:" in text
    assert "gitea/gitea:" in text
    assert "sk-" not in text
    published = [
        "127.0.0.1:5432:5432",
        "127.0.0.1:8000:8000",
        "127.0.0.1:8080:80",
        "127.0.0.1:18080:8080",
        "127.0.0.1:13000:3000",
    ]
    for item in published:
        assert item in text
    assert re.findall(r'^\s+-\s+"(\d+:\d+)"\s*$', text, re.MULTILINE) == []
    assert "JENKINS_URL:" in text
    assert "GIT_PROVIDER:" in text
    assert "GIT_BASE_URL:" in text
    assert "KILN_ARTIFACT_ROOT:" in text
    assert "kiln-artifact-data:" in text
    assert "127.0.0.1:2222" not in text
    assert "127.0.0.1:3000:" not in text
    assert "127.0.0.1:50000" not in text


def test_api_image_drops_to_the_kiln_user_for_artifact_storage() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    script = (BACKEND / "docker-entrypoint.py").read_text(encoding="utf-8")
    assert "--uid 10001" in dockerfile
    assert "--gid 10001" in dockerfile
    assert "kiln-entrypoint" in dockerfile
    assert "KILN_ARTIFACT_ROOT" in script
    assert "os.setuid(KILN_UID)" in script
    assert "subprocess" not in script


def test_repository_does_not_invent_a_production_gitlab_or_nexus_url() -> None:
    pattern = re.compile(r"https?://[^\s\"'`<>]*?(gitlab|nexus)", re.IGNORECASE)
    skipped = {".git", "node_modules", ".venv", "venv", "dist", "__pycache__"}
    suffixes = {
        ".py",
        ".md",
        ".yml",
        ".yaml",
        ".vue",
        ".js",
        ".toml",
        ".css",
        ".html",
        ".sh",
        ".groovy",
    }
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or skipped.intersection(path.parts):
            continue
        if path.name != ".env.example" and path.suffix not in suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
