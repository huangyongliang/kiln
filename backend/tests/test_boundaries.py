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
