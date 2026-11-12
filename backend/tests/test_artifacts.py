import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kiln.main import create_app
from tests.support import app_settings, auth_headers, blob_files, content_headers, truncate_domain

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
    listed = client.get("/api/v1/artifacts")
    assert listed.status_code == 200
    assert listed.json()["persistence"] == "postgres"
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

    listed_version = client.get(f"/api/v1/artifacts/{artifact_id}").json()["versions"][0]
    assert listed_version["content_available"] is False

    retrieved = client.get(reserved["retrieve_path"])
    assert retrieved.status_code == 200
    body = retrieved.json()
    assert body["status"] == "reserved"
    assert body["content_available"] is False
    assert body["content_path"] is None
    assert body["checksum"]["value"] == SHA256_A
    assert body["version"] == "1.2.3"
    assert body["name"] == "demo-lib"
    missing = client.get(f"{reserved['retrieve_path']}/content")
    assert missing.status_code == 409


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
    monkeypatch.setattr("kiln.clock.utcnow", lambda: current["now"])
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
    assert client.get(f"{path}/content").status_code == 410


def test_artifact_name_is_unique_within_a_group(client: TestClient) -> None:
    first = client.post(
        "/api/v1/artifacts",
        json={"name": "shared", "package_type": "generic"},
    )
    assert first.status_code == 201
    again = client.post(
        "/api/v1/artifacts",
        json={"name": "shared", "package_type": "python"},
    )
    assert again.status_code == 409
    other_group = client.post(
        "/api/v1/artifacts",
        json={"name": "shared", "group": "com.kiln", "package_type": "maven"},
    )
    assert other_group.status_code == 201


def test_missing_reservation_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/retrieve/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    missing = client.get("/api/v1/retrieve/00000000-0000-0000-0000-000000000000/content")
    assert missing.status_code == 404


def test_stored_bytes_round_trip_from_the_local_directory(client: TestClient) -> None:
    payload = b"kiln-artifact\x00\xff"
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "byte-lib", "group": "com.kiln", "package_type": "generic"},
    )
    assert created.status_code == 201
    artifact_id = created.json()["id"]
    root = Path(client.app.state.blobs.root)
    assert blob_files(root) == []

    stored = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.2.3/content",
        content=payload,
        headers=content_headers(payload, source="build"),
    )
    assert stored.status_code == 201, stored.text
    body = stored.json()
    assert body["content_available"] is True
    assert body["size_bytes"] == len(payload)
    assert body["source"] == "build"
    assert body["checksum"]["algorithm"] == "sha256"
    assert body["checksum"]["value"] == hashlib.sha256(payload).hexdigest()

    digest = body["checksum"]["value"]
    on_disk = root / "sha256" / digest
    assert on_disk.is_file()
    assert on_disk.read_bytes() == payload

    listed = client.get(f"/api/v1/artifacts/{artifact_id}")
    assert listed.json()["versions"][0]["content_available"] is True

    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1.2.3/download-reservations",
        json={"requester": "builder_1"},
    )
    assert reservation.status_code == 201
    reserved = reservation.json()
    meta = client.get(reserved["retrieve_path"])
    assert meta.status_code == 200
    assert meta.json()["content_available"] is True
    assert meta.json()["content_path"] == f"{reserved['retrieve_path']}/content"

    downloaded = client.get(meta.json()["content_path"])
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert downloaded.headers["content-type"].startswith("application/octet-stream")
    assert downloaded.headers["x-kiln-checksum"] == digest
    assert downloaded.headers["x-kiln-content-available"] == "true"
    assert "byte-lib-1.2.3" in downloaded.headers["content-disposition"]

    again = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.2.3/content",
        content=payload,
        headers=content_headers(payload),
    )
    assert again.status_code == 200
    assert again.json()["content_available"] is True
    assert on_disk.read_bytes() == payload

    other = b"different-bytes"
    rejected = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.2.3/content",
        content=other,
        headers=content_headers(other),
    )
    assert rejected.status_code == 409
    assert client.get(meta.json()["content_path"]).content == payload
    assert on_disk.read_bytes() == payload


def test_checksum_mismatch_does_not_write_a_blob(client: TestClient) -> None:
    payload = b"not-the-declared-digest"
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "bad-sum", "package_type": "generic"},
    )
    artifact_id = created.json()["id"]
    root = Path(client.app.state.blobs.root)
    response = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1/content",
        content=payload,
        headers=content_headers(b"other", source="upload") | {"X-Kiln-Checksum": SHA256_A},
    )
    assert response.status_code == 400
    assert blob_files(root) == []
    listed = client.get(f"/api/v1/artifacts/{artifact_id}")
    assert listed.json()["versions"] == []


def test_upload_must_match_a_registered_version(client: TestClient) -> None:
    payload = b"registered-bytes"
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "registered-lib", "package_type": "python"},
    )
    artifact_id = created.json()["id"]
    registered = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        json={
            "version": "1.0.0",
            "checksum": {"algorithm": "sha256", "value": hashlib.sha256(payload).hexdigest()},
            "source": "build",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["content_available"] is False

    root = Path(client.app.state.blobs.root)
    wrong = b"other-payload"
    conflict = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
        content=wrong,
        headers=content_headers(wrong),
    )
    assert conflict.status_code == 409
    assert blob_files(root) == []

    stored = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1.0.0/content",
        content=payload,
        headers=content_headers(payload, source="upload"),
    )
    assert stored.status_code == 201
    assert stored.json()["content_available"] is True
    assert stored.json()["source"] == "build"
    assert stored.json()["size_bytes"] == len(payload)
    assert (root / "sha256" / hashlib.sha256(payload).hexdigest()).read_bytes() == payload


def test_sha512_bytes_round_trip(client: TestClient) -> None:
    payload = b"sha512-payload"
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "sha512-lib", "package_type": "frontend"},
    )
    artifact_id = created.json()["id"]
    stored = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/2026.1/content",
        content=payload,
        headers=content_headers(payload, algorithm="sha512"),
    )
    assert stored.status_code == 201
    digest = hashlib.sha512(payload).hexdigest()
    assert stored.json()["checksum"] == {"algorithm": "sha512", "value": digest}
    root = Path(client.app.state.blobs.root)
    assert (root / "sha512" / digest).read_bytes() == payload
    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/2026.1/download-reservations",
        json={"requester": "reader_1"},
    )
    downloaded = client.get(f"{reservation.json()['retrieve_path']}/content")
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert downloaded.headers["x-kiln-checksum-algorithm"] == "sha512"


def test_corrupt_blob_is_not_returned(client: TestClient) -> None:
    payload = b"intact-bytes"
    created = client.post(
        "/api/v1/artifacts",
        json={"name": "corrupt-lib", "package_type": "generic"},
    )
    artifact_id = created.json()["id"]
    stored = client.put(
        f"/api/v1/artifacts/{artifact_id}/versions/1/content",
        content=payload,
        headers=content_headers(payload),
    )
    assert stored.status_code == 201
    digest = stored.json()["checksum"]["value"]
    path = Path(client.app.state.blobs.root) / "sha256" / digest
    path.write_bytes(b"tampered-bytes")
    reservation = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions/1/download-reservations",
        json={"requester": "reader_1"},
    )
    downloaded = client.get(f"{reservation.json()['retrieve_path']}/content")
    assert downloaded.status_code == 500
    assert downloaded.content != payload
    assert b"tampered-bytes" not in downloaded.content


def test_oversized_artifact_is_not_stored(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    app = create_app(
        app_settings(
            llm_provider="off",
            kiln_artifact_root=str(root),
            kiln_artifact_max_bytes=4,
        )
    )
    with TestClient(app, headers=auth_headers()) as client:
        truncate_domain(app.state.engine)
        created = client.post(
            "/api/v1/artifacts",
            json={"name": "too-big", "package_type": "generic"},
        )
        assert created.status_code == 201
        payload = b"12345"
        response = client.put(
            f"/api/v1/artifacts/{created.json()['id']}/versions/1/content",
            content=payload,
            headers=content_headers(payload),
        )
        assert response.status_code == 413
        assert blob_files(root) == []
