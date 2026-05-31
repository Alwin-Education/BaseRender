from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baserender_api.app import app
from baserender.media_inventory import load_media_inventory_from_text

from conftest import TEST_S3_BUCKET


class FakeMediaConvertClient:
    def __init__(self) -> None:
        self._counter = 0

    def create_job(self, settings, *, queue=None, user_metadata=None) -> str:
        self._counter += 1
        return f"mc-{self._counter}"

    def get_job(self, job_id: str) -> dict[str, Any]:
        return {"Id": job_id, "Status": "COMPLETE"}


class FakeEventBridgeClient:
    def put_event(self, detail_type, detail, *, source=None, bus=None) -> str:
        return "event-1"


@pytest.fixture
def cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASERENDER_RENDER_BACKEND", "cloud")
    monkeypatch.setenv("BASERENDER_MEDIACONVERT_ROLE_ARN", "arn:aws:iam::123456789012:role/MC")
    monkeypatch.setattr(
        "baserender_api.app.get_mediaconvert_client",
        lambda: FakeMediaConvertClient(),
    )
    monkeypatch.setattr(
        "baserender_api.app.get_eventbridge_client",
        lambda: FakeEventBridgeClient(),
    )


def test_cloud_job_returns_route_and_steps(
    authenticated_client: TestClient,
    cloud_env: None,
) -> None:
    otio_text = _resolve_style_otio_text()
    inventory = load_media_inventory_from_text(otio_text)
    reference = inventory.entries[0]

    response = authenticated_client.post(
        "/jobs",
        json={
            "output_path": "output.mp4",
            "dry_run": False,
            "settings": {"width": 1920, "height": 1080, "fps": 24},
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
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["backend"] == "cloud"
    assert payload["route"] == "full_mediaconvert"
    assert payload["status"] == "running"
    assert len(payload["steps"]) == 1
    assert payload["steps"][0]["kind"] == "full"
    assert payload["steps"][0]["external_id"] == "mc-1"


def test_cloud_job_not_claimable_by_worker(
    authenticated_client: TestClient,
    cloud_env: None,
) -> None:
    otio_text = _resolve_style_otio_text()
    inventory = load_media_inventory_from_text(otio_text)
    reference = inventory.entries[0]

    authenticated_client.post(
        "/jobs",
        json={
            "output_path": "output.mp4",
            "settings": {"width": 1920, "height": 1080, "fps": 24},
            "otio_content_base64": _base64_text(otio_text),
            "media_references": [
                {
                    "id": reference.id,
                    "normalized_url": reference.normalized_url,
                }
            ],
            "media_assignments": {reference.id: "projects/demo/day1/Shot_A.mov"},
        },
    )

    claim_response = authenticated_client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )

    assert claim_response.status_code == 200
    assert claim_response.json() is None


def test_internal_event_advances_full_mediaconvert_job(
    authenticated_client: TestClient,
    cloud_env: None,
) -> None:
    otio_text = _resolve_style_otio_text()
    inventory = load_media_inventory_from_text(otio_text)
    reference = inventory.entries[0]

    create_response = authenticated_client.post(
        "/jobs",
        json={
            "output_path": "output.mp4",
            "settings": {"width": 1920, "height": 1080, "fps": 24},
            "otio_content_base64": _base64_text(otio_text),
            "media_references": [
                {
                    "id": reference.id,
                    "normalized_url": reference.normalized_url,
                }
            ],
            "media_assignments": {reference.id: "projects/demo/day1/Shot_A.mov"},
        },
    )
    job_id = create_response.json()["id"]

    event_response = authenticated_client.post(
        "/internal/events",
        headers={"Authorization": "Bearer test-worker-token"},
        json={
            "job_id": job_id,
            "step_id": "full",
            "status": "succeeded",
        },
    )

    assert event_response.status_code == 200
    payload = event_response.json()
    assert payload["status"] == "succeeded"
    assert payload["steps"][0]["status"] == "succeeded"
    assert payload["output"]["key"].endswith("output.mp4")


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
