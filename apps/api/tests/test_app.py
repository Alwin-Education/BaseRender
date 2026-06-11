from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from baserender_api.app import app
from baserender.media_inventory import load_media_inventory_from_text

from conftest import TEST_S3_BUCKET


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_session_is_public() -> None:
    client = TestClient(app)

    response = client.get("/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}


def test_serves_static_frontend(tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI

    from baserender_api.static_files import register_static_routes

    index_file = tmp_path / "index.html"
    index_file.write_text("<html><body>BaseRender</body></html>", encoding="utf-8")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")

    monkeypatch.setenv("BASERENDER_STATIC_DIR", str(tmp_path))

    static_app = FastAPI()
    register_static_routes(static_app)
    client = TestClient(static_app)

    root_response = client.get("/")
    assert root_response.status_code == 200
    assert "BaseRender" in root_response.text

    spa_response = client.get("/login")
    assert spa_response.status_code == 200
    assert spa_response.text == index_file.read_text(encoding="utf-8")

    asset_response = client.get("/assets/app.js")
    assert asset_response.status_code == 200
    assert asset_response.text == "console.log('ok');"


def test_protected_routes_require_auth() -> None:
    client = TestClient(app)

    response = client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )

    assert response.status_code == 401

    media_response = client.get("/media/config")

    assert media_response.status_code == 401


def test_proxy_bearer_token_allows_protected_routes(monkeypatch) -> None:
    monkeypatch.setenv("BASERENDER_PROXY_TOKEN", "proxy-secret")
    client = TestClient(app)

    response = client.get(
        "/media/config", headers={"Authorization": "Bearer proxy-secret"}
    )

    assert response.status_code == 200


def test_proxy_bearer_token_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setenv("BASERENDER_PROXY_TOKEN", "proxy-secret")
    client = TestClient(app)

    response = client.get(
        "/media/config", headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401


def test_proxy_bearer_token_disabled_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("BASERENDER_PROXY_TOKEN", raising=False)
    client = TestClient(app)

    response = client.get(
        "/media/config", headers={"Authorization": "Bearer proxy-secret"}
    )

    assert response.status_code == 401


def test_login_rejects_invalid_password() -> None:
    client = TestClient(app)

    response = client.post("/auth/login", json={"password": "wrong-password"})

    assert response.status_code == 401


def test_login_success_allows_session_requests(auth_password: str) -> None:
    client = TestClient(app)

    login_response = client.post("/auth/login", json={"password": auth_password})
    session_response = client.get("/auth/session")

    assert login_response.status_code == 200
    assert login_response.json() == {"authenticated": True}
    assert session_response.status_code == 200
    assert session_response.json() == {"authenticated": True}


def test_logout_clears_session(auth_password: str) -> None:
    client = TestClient(app)

    login_response = client.post("/auth/login", json={"password": auth_password})
    logout_response = client.post("/auth/logout")
    session_response = client.get("/auth/session")

    assert login_response.status_code == 200
    assert logout_response.status_code == 200
    assert logout_response.json() == {"authenticated": False}
    assert session_response.status_code == 200
    assert session_response.json() == {"authenticated": False}


def test_create_and_get_job(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
            "settings": {"width": 1920, "height": 1080, "fps": 24},
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["worker_payload"]["input_path"] == "timeline.otio"
    assert payload["worker_payload"]["output_object_key"].endswith("outputs/output.mp4")
    assert payload["worker_payload"]["settings"]["width"] == 1920

    status_response = authenticated_client.get(f"/jobs/{payload['id']}")
    assert status_response.status_code == 200
    assert status_response.json()["id"] == payload["id"]


def test_rejects_second_active_job(authenticated_client: TestClient) -> None:
    first_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    second_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "other.otio",
            "output_path": "other.mp4",
            "dry_run": True,
        },
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 409


def test_cancel_active_job(authenticated_client: TestClient) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    job_id = create_response.json()["id"]

    cancel_response = authenticated_client.delete("/jobs/current")
    assert cancel_response.status_code == 200
    payload = cancel_response.json()
    assert payload["id"] == job_id
    assert payload["status"] == "failed"
    assert payload["error"]["message"] == "Render cancelled."

    current_response = authenticated_client.get("/jobs/current")
    assert current_response.status_code == 200
    assert current_response.json()["status"] == "failed"
    assert current_response.json()["error"]["message"] == "Render cancelled."

    create_after_cancel = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    assert create_after_cancel.status_code == 202


def test_cancel_running_job_blocks_worker_updates(
    authenticated_client: TestClient,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]
    worker_headers = {"Authorization": "Bearer test-worker-token"}

    claim_response = authenticated_client.post(
        "/worker/jobs/claim",
        headers=worker_headers,
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["id"] == job_id

    cancel_response = authenticated_client.delete("/jobs/current")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "failed"

    heartbeat_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers=worker_headers,
        json={},
    )
    complete_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/complete",
        headers=worker_headers,
        json={"report": {"status": "ok"}},
    )
    fail_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/fail",
        headers=worker_headers,
        json={"message": "worker failure"},
    )

    assert heartbeat_response.status_code == 404
    assert complete_response.status_code == 404
    assert fail_response.status_code == 404

    current_response = authenticated_client.get("/jobs/current")
    assert current_response.status_code == 200
    assert current_response.json()["status"] == "failed"


def test_heartbeat_after_cancel_does_not_resurrect_job(
    authenticated_client: TestClient,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]
    worker_headers = {"Authorization": "Bearer test-worker-token"}

    authenticated_client.post("/worker/jobs/claim", headers=worker_headers)
    authenticated_client.delete("/jobs/current")

    heartbeat_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers=worker_headers,
        json={
            "progress": {
                "percent": 55.0,
                "elapsed_seconds": 30.0,
                "eta_seconds": 25.0,
            }
        },
    )
    current_response = authenticated_client.get("/jobs/current")

    assert heartbeat_response.status_code == 404
    assert current_response.status_code == 200
    assert current_response.json()["status"] == "failed"
    assert current_response.json()["progress"] is None


def test_worker_claim_complete_and_fail_lifecycle(authenticated_client: TestClient) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    job_id = create_response.json()["id"]

    claim_response = authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )
    duplicate_claim_response = authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )
    complete_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/complete",
        headers={"Authorization": "Bearer test-worker-token"},
        json={"report": {"status": "ok"}},
    )

    assert claim_response.status_code == 200
    assert claim_response.json()["id"] == job_id
    assert duplicate_claim_response.status_code == 200
    assert duplicate_claim_response.json() is None
    assert complete_response.status_code == 200
    payload = complete_response.json()
    assert payload["status"] == "succeeded"
    assert payload["report"] == {"status": "ok"}


def test_worker_heartbeat_rejected_after_job_succeeds(
    authenticated_client: TestClient,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    job_id = create_response.json()["id"]
    worker_headers = {"Authorization": "Bearer test-worker-token"}

    authenticated_client.post("/worker/jobs/claim", headers=worker_headers)
    authenticated_client.post(
        f"/worker/jobs/{job_id}/complete",
        headers=worker_headers,
        json={"report": {"status": "ok"}},
    )

    heartbeat_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers=worker_headers,
        json={},
    )

    assert heartbeat_response.status_code == 404


def test_worker_heartbeat_without_progress_preserves_existing_progress(
    authenticated_client: TestClient,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]
    worker_headers = {"Authorization": "Bearer test-worker-token"}

    authenticated_client.post("/worker/jobs/claim", headers=worker_headers)
    authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers=worker_headers,
        json={
            "progress": {
                "percent": 42.5,
                "elapsed_seconds": 90.0,
                "eta_seconds": 120.0,
            }
        },
    )
    poll_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers=worker_headers,
        json={},
    )

    assert poll_response.status_code == 200
    assert poll_response.json()["progress"]["percent"] == 42.5


def test_worker_heartbeat_persists_progress(
    authenticated_client: TestClient,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]

    authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )
    heartbeat_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers={"Authorization": "Bearer test-worker-token"},
        json={
            "progress": {
                "percent": 42.5,
                "elapsed_seconds": 90.0,
                "eta_seconds": 120.0,
                "out_time_seconds": 4.68,
                "frame": 119,
                "fps": 1.3,
                "speed": 0.0522,
            }
        },
    )
    status_response = authenticated_client.get(f"/jobs/{job_id}")

    assert heartbeat_response.status_code == 200
    assert heartbeat_response.json()["progress"]["percent"] == 42.5
    assert status_response.status_code == 200
    assert status_response.json()["progress"]["frame"] == 119
    assert status_response.json()["heartbeat_at"] is not None


def test_worker_heartbeat_persists_uploading_phase(
    authenticated_client: TestClient,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]

    authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )
    heartbeat_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/heartbeat",
        headers={"Authorization": "Bearer test-worker-token"},
        json={
            "progress": {
                "percent": 100.0,
                "elapsed_seconds": 120.0,
                "eta_seconds": None,
                "phase": "uploading",
            }
        },
    )
    status_response = authenticated_client.get(f"/jobs/{job_id}")

    assert heartbeat_response.status_code == 200
    assert heartbeat_response.json()["progress"]["phase"] == "uploading"
    assert status_response.status_code == 200
    assert status_response.json()["progress"]["percent"] == 100.0
    assert status_response.json()["progress"]["phase"] == "uploading"


def test_worker_uploads_output_to_resolved_output_path(
    authenticated_client: TestClient,
    fake_s3_client,
) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "episode-1/final.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]
    assert create_response.json()["worker_payload"]["output_object_key"].endswith(
        "outputs/episode-1/final.mp4"
    )

    authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )
    upload_target_response = authenticated_client.get(
        f"/worker/jobs/{job_id}/artifacts/output/upload-target",
        headers={"Authorization": "Bearer test-worker-token"},
    )
    upload_target = upload_target_response.json()
    fake_s3_client.put_object(
        Bucket="test-bucket",
        Key=upload_target["key"],
        Body=b"video",
    )
    complete_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/complete",
        headers={"Authorization": "Bearer test-worker-token"},
        json={"report": {"status": "ok"}},
    )

    assert upload_target_response.status_code == 200
    assert upload_target["key"].endswith("outputs/episode-1/final.mp4")
    assert upload_target["headers"] == {"Content-Type": "video/mp4"}
    assert "episode-1/final.mp4" in upload_target["url"]
    assert complete_response.status_code == 200
    output = complete_response.json()["output"]
    assert output["key"].endswith("outputs/episode-1/final.mp4")
    assert output["size"] == 5
    assert output["path"].endswith("outputs/episode-1/final.mp4")

    url_response = authenticated_client.get(f"/jobs/{job_id}/output/url")
    assert url_response.status_code == 200
    assert "episode-1/final.mp4" in url_response.json()["url"]


def test_worker_complete_rejects_missing_s3_output(
    authenticated_client: TestClient,
    monkeypatch,
) -> None:
    class MissingOutputClient:
        def head_object(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("missing")

    monkeypatch.setattr(
        "baserender_api.output_storage.S3OutputStore._create_client",
        lambda self: MissingOutputClient(),
    )
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "final.mp4",
            "dry_run": False,
        },
    )
    job_id = create_response.json()["id"]
    authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )

    complete_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/complete",
        headers={"Authorization": "Bearer test-worker-token"},
        json={"report": {"status": "ok"}},
    )

    assert complete_response.status_code == 409
    assert "not uploaded or is empty" in complete_response.json()["detail"]


def test_create_job_rejects_invalid_output_path(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "../final.mp4",
            "dry_run": False,
        },
    )

    assert response.status_code == 400
    assert "Output path cannot contain '..'" in response.json()["detail"]


def test_worker_fail_records_error(authenticated_client: TestClient) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    job_id = create_response.json()["id"]
    authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )

    fail_response = authenticated_client.post(
        f"/worker/jobs/{job_id}/fail",
        headers={"Authorization": "Bearer test-worker-token"},
        json={"message": "render failed", "detail": "traceback"},
    )

    assert fail_response.status_code == 200
    payload = fail_response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["message"] == "render failed"


def test_worker_payload_clip_lut_artifacts_use_prepared_media_url(
    authenticated_client: TestClient,
) -> None:
    otio_text = _resolve_style_otio_text()
    inventory = load_media_inventory_from_text(otio_text)
    reference = inventory.entries[0]
    lut_content = base64.b64encode(b"LUT_CONTENT").decode("ascii")

    create_response = authenticated_client.post(
        "/jobs",
        json={
            "output_path": "output.mp4",
            "dry_run": True,
            "otio_content_base64": _base64_text(otio_text),
            "media_references": [
                {
                    "id": reference.id,
                    "clip_name": reference.clip_name,
                    "track_path": reference.track_path,
                    "reference_kind": reference.reference_kind,
                    "target_url": reference.target_url,
                    "normalized_url": reference.normalized_url,
                    "status": reference.status,
                    "clip_count": reference.clip_count,
                }
            ],
            "media_assignments": {
                reference.id: "projects/demo/day1/Shot_A.mov",
            },
            "lut_files": [
                {
                    "id": "lut-1",
                    "name": "look.cube",
                    "content_base64": lut_content,
                }
            ],
            "lut_assignments": {
                reference.id: "lut-1",
            },
        },
    )

    assert create_response.status_code == 202
    clip_lut_artifacts = create_response.json()["worker_payload"]["clip_lut_artifacts"]
    assert len(clip_lut_artifacts) == 1
    assert clip_lut_artifacts[0]["normalized_url"] == "/Volumes/Raid/Shot_A.mov"
    assert clip_lut_artifacts[0]["media_url"].startswith("https://fake-s3.test/get?")
    assert "projects/demo/day1/Shot_A.mov" in clip_lut_artifacts[0]["media_url"]


def _base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _resolve_style_otio_text() -> str:
    return """{
        "OTIO_SCHEMA": "Timeline.1",
        "metadata": {},
        "name": "Resolve Inventory",
        "global_start_time": null,
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "metadata": {},
            "name": "",
            "source_range": null,
            "effects": [],
            "markers": [],
            "enabled": true,
            "children": [
                {
                    "OTIO_SCHEMA": "Track.1",
                    "metadata": {},
                    "name": "Video 1",
                    "source_range": null,
                    "effects": [],
                    "markers": [],
                    "enabled": true,
                    "children": [
                        {
                            "OTIO_SCHEMA": "Clip.2",
                            "metadata": {},
                            "name": "Shot A",
                            "source_range": {
                                "OTIO_SCHEMA": "TimeRange.1",
                                "duration": {
                                    "OTIO_SCHEMA": "RationalTime.1",
                                    "rate": 24.0,
                                    "value": 24.0
                                },
                                "start_time": {
                                    "OTIO_SCHEMA": "RationalTime.1",
                                    "rate": 24.0,
                                    "value": 0.0
                                }
                            },
                            "effects": [],
                            "markers": [],
                            "enabled": true,
                            "media_references": {
                                "DEFAULT_MEDIA": {
                                    "OTIO_SCHEMA": "ExternalReference.1",
                                    "metadata": {},
                                    "name": "Shot_A.mov",
                                    "available_range": {
                                        "OTIO_SCHEMA": "TimeRange.1",
                                        "duration": {
                                            "OTIO_SCHEMA": "RationalTime.1",
                                            "rate": 24.0,
                                            "value": 24.0
                                        },
                                        "start_time": {
                                            "OTIO_SCHEMA": "RationalTime.1",
                                            "rate": 24.0,
                                            "value": 0.0
                                        }
                                    },
                                    "available_image_bounds": null,
                                    "target_url": "file:///Volumes/Raid/Shot_A.mov"
                                }
                            },
                            "active_media_reference_key": "DEFAULT_MEDIA"
                        }
                    ],
                    "kind": "Video"
                }
            ]
        }
    }"""


def _write_current_job_state(fake_s3_client, payload: dict[str, Any]) -> None:
    fake_s3_client.put_object(
        Bucket=TEST_S3_BUCKET,
        Key="baserender/jobs/current.json",
        Body=json.dumps(payload, default=str).encode("utf-8"),
    )


def _sample_render_job_create() -> dict[str, Any]:
    return {
        "input_path": "timeline.otio",
        "output_path": "output.mp4",
        "dry_run": False,
    }


def test_stale_running_job_auto_fails_before_submit(
    authenticated_client: TestClient,
    fake_s3_client,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BASERENDER_JOB_STALE_SECONDS", "60")
    stale_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    job_body = _sample_render_job_create()
    _write_current_job_state(
        fake_s3_client,
        {
            "id": "stale-job-id",
            "status": "running",
            "job": job_body,
            "worker_payload": {"output_object_key": "outputs/output.mp4"},
            "created_at": stale_time,
            "updated_at": stale_time,
            "claimed_at": stale_time,
            "heartbeat_at": stale_time,
        },
    )

    response = authenticated_client.post("/jobs", json=job_body)

    assert response.status_code == 202
    assert response.json()["id"] != "stale-job-id"
    assert response.json()["status"] == "queued"


def test_dismiss_clears_current_job(authenticated_client: TestClient) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json={
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "dry_run": True,
        },
    )
    assert create_response.status_code == 202

    dismiss_response = authenticated_client.post("/jobs/current/dismiss")
    assert dismiss_response.status_code == 204

    current_response = authenticated_client.get("/jobs/current")
    assert current_response.status_code == 404


def test_output_url_rejects_running_job(authenticated_client: TestClient) -> None:
    create_response = authenticated_client.post(
        "/jobs",
        json=_sample_render_job_create(),
    )
    job_id = create_response.json()["id"]
    worker_headers = {"Authorization": "Bearer test-worker-token"}
    authenticated_client.post("/worker/jobs/claim", headers=worker_headers)

    url_response = authenticated_client.get(f"/jobs/{job_id}/output/url")

    assert url_response.status_code == 404
    assert url_response.json()["detail"] == "Render output not found."


def test_get_current_job_auto_fails_stale_running_job(
    authenticated_client: TestClient,
    fake_s3_client,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BASERENDER_JOB_STALE_SECONDS", "60")
    stale_time = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    job_body = _sample_render_job_create()
    _write_current_job_state(
        fake_s3_client,
        {
            "id": "stale-job-id",
            "status": "running",
            "job": job_body,
            "worker_payload": {"output_object_key": "outputs/output.mp4"},
            "created_at": stale_time,
            "updated_at": stale_time,
            "claimed_at": stale_time,
            "heartbeat_at": stale_time,
        },
    )

    current_response = authenticated_client.get("/jobs/current")

    assert current_response.status_code == 200
    payload = current_response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["message"] == "Worker stopped responding."
