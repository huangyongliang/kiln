from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

SHA256_A = "ab" * 32


def test_version_reservation_and_stable_retrieve(client: TestClient) -> None:
    created = client.post(
        "/api/v1/artifacts",
        json={
            "name": "demo-lib",
            "group": "com.kiln",
            "description": "示例库",
            "package_type": "maven",
        },
    )
    assert created.status_code == 201
    artifact_id = created.json()["id"]
    assert created.json()["versions"] == []
    assert created.json()["package_type_label"] == "Maven"

    version = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        json={
            "version": "1.2.3",
            "checksum": {"algorithm": "sha256", "value": SHA256_A.upper()},
            "size_bytes": 128,
            "source": "upload",
        },
    )
    assert version.status_code == 201
    assert version.json()["checksum"]["value"] == SHA256_A
    assert version.json()["source"] == "upload"

    duplicate = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        json={"version": "1.2.3", "checksum": {"algorithm": "sha256", "value": SHA256_A}},
    )
    assert duplicate.status_code == 409

    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1.2.3/download-reservations",
        json={"requester": "builder_1", "ttl_seconds": 900},
    )
    assert reservation.status_code == 201
    reserved = reservation.json()
    assert reserved["retrieve_path"] == f"/api/v1/retrieve/{reserved['id']}"

    retrieved = client.get(reserved["retrieve_path"])
    assert retrieved.status_code == 200
    body = retrieved.json()
    assert body["status"] == "reserved"
    assert body["content_available"] is False
    assert body["checksum"]["value"] == SHA256_A
    assert body["version"] == "1.2.3"
    assert body["name"] == "demo-lib"


def test_checksum_must_match_algorithm(client: TestClient) -> None:
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "plain-pkg", "package_type": "generic"},
    )
    artifact_id = created.json()["id"]
    response = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        json={"version": "1", "checksum": {"algorithm": "sha256", "value": "abcd"}},
    )
    assert response.status_code == 422


def test_unknown_version_cannot_be_reserved(client: TestClient) -> None:
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "empty-pkg", "package_type": "python"},
    )
    response = client.post(
        f"/api/v1/artifacts/{created.json()['id']}/versions/9.9.9/download-reservations",
        json={"requester": "reader_1"},
    )
    assert response.status_code == 404


def test_expired_reservation_is_gone(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    current = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    monkeypatch.setattr("kiln.memory.utcnow", lambda: current["now"])
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "timed-pkg", "package_type": "frontend"},
    )
    artifact_id = created.json()["id"]
    assert (
        client.post(
            f"/api/v1/artifacts/{artifact_id}/versions",
            json={"version": "2026.1", "checksum": {"algorithm": "sha256", "value": SHA256_A}},
        ).status_code
        == 201
    )
    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/2026.1/download-reservations",
        json={"requester": "reader_1", "ttl_seconds": 60},
    )
    path = reservation.json()["retrieve_path"]
    current["now"] = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
    expired = client.get(path)
    assert expired.status_code == 410


def test_missing_reservation_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/retrieve/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
