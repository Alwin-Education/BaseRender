from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


class RecordingMediaConvertClient:
    def __init__(self) -> None:
        self.create_job_calls: list[dict[str, Any]] = []
        self._counter = 0

    def create_job(
        self,
        settings: dict[str, Any],
        *,
        queue: str | None = None,
        user_metadata: dict[str, str] | None = None,
    ) -> str:
        self._counter += 1
        self.create_job_calls.append(
            {
                "settings": settings,
                "queue": queue,
                "user_metadata": user_metadata,
            }
        )
        return f"mc-job-{self._counter}"

    def get_job(self, job_id: str) -> dict[str, Any]:
        return {"Id": job_id, "Status": "COMPLETE"}


@pytest.fixture
def mediaconvert_client(monkeypatch: pytest.MonkeyPatch) -> RecordingMediaConvertClient:
    client = RecordingMediaConvertClient()
    monkeypatch.setenv("BASERENDER_MEDIACONVERT_ROLE_ARN", "arn:aws:iam::123456789012:role/MediaConvertRole")
    monkeypatch.setenv("BASERENDER_RENDER_BACKEND", "worker")
    monkeypatch.setattr(
        "baserender_api.app.get_mediaconvert_client",
        lambda: client,
    )
    return client


def test_create_transcode_submits_one_mediaconvert_job_per_input(
    authenticated_client: TestClient,
    mediaconvert_client: RecordingMediaConvertClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="")
    response = authenticated_client.post(
        "/transcode",
        json={
            "inputs": [
                "projects/demo/day1/clipA.mov",
                "projects/demo/day1/clipB.mov",
            ],
            "settings": {
                "width": 1280,
                "height": 720,
                "video_codec": "h264",
            },
            "container": "mp4",
            "prepend_folder": "proxies",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert len(payload["results"]) == 2
    assert payload["results"][0] == {
        "source_key": "projects/demo/day1/clipA.mov",
        "output_key": "proxies/projects/demo/day1/clipA.mp4",
        "mediaconvert_job_id": "mc-job-1",
    }
    assert payload["results"][1]["mediaconvert_job_id"] == "mc-job-2"
    assert len(mediaconvert_client.create_job_calls) == 2

    first_call = mediaconvert_client.create_job_calls[0]
    assert first_call["user_metadata"] == {"transcode": "1"}
    assert first_call["settings"]["Inputs"][0]["FileInput"] == (
        "s3://test-bucket/projects/demo/day1/clipA.mov"
    )
    destination = first_call["settings"]["OutputGroups"][0]["OutputGroupSettings"][
        "FileGroupSettings"
    ]["Destination"]
    assert destination == "s3://test-bucket/proxies/projects/demo/day1/clipA"


def test_create_transcode_dry_run_skips_mediaconvert_submission(
    authenticated_client: TestClient,
    mediaconvert_client: RecordingMediaConvertClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="")
    response = authenticated_client.post(
        "/transcode",
        json={
            "inputs": ["projects/demo/clipA.mov"],
            "dry_run": True,
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["results"] == [
        {
            "source_key": "projects/demo/clipA.mov",
            "output_key": "projects/demo/clipA.mp4",
            "mediaconvert_job_id": None,
        }
    ]
    assert mediaconvert_client.create_job_calls == []


def test_create_transcode_rejects_input_outside_allowed_prefix(
    authenticated_client: TestClient,
    defaults_config,
    mediaconvert_client: RecordingMediaConvertClient,
) -> None:
    defaults_config(media_prefix="projects/demo/")

    response = authenticated_client.post(
        "/transcode",
        json={"inputs": ["other/clipA.mov"]},
    )

    assert response.status_code == 400
    assert "allowed prefix" in response.json()["detail"].lower()
    assert mediaconvert_client.create_job_calls == []


def test_create_transcode_requires_authentication() -> None:
    client = TestClient(__import__("baserender_api.app", fromlist=["app"]).app)
    response = client.post("/transcode", json={"inputs": ["projects/demo/clipA.mov"]})
    assert response.status_code == 401
