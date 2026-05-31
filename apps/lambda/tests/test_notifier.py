from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from baserender_lambda.notifier import normalize_event, post_internal_event


def test_normalize_mediaconvert_complete_event() -> None:
    payload = normalize_event(
        {
            "detail-type": "MediaConvert Job State Change",
            "detail": {
                "status": "COMPLETE",
                "jobId": "mc-123",
                "userMetadata": {"job_id": "job-1", "step_id": "full"},
            },
        }
    )

    assert payload == {
        "job_id": "job-1",
        "step_id": "full",
        "external_id": "mc-123",
        "status": "succeeded",
    }


def test_normalize_mediaconvert_ignores_in_progress() -> None:
    assert (
        normalize_event(
            {
                "detail-type": "MediaConvert Job State Change",
                "detail": {"status": "PROGRESSING", "userMetadata": {"job_id": "job-1"}},
            }
        )
        is None
    )


def test_normalize_shot_complete_event() -> None:
    payload = normalize_event(
        {
            "detail-type": "BaseRender Shot Complete",
            "detail": {
                "job_id": "job-1",
                "shot_index": 2,
                "output_key": "baserender/jobs/job-1/working/shot-2.mp4",
                "status": "succeeded",
            },
        }
    )

    assert payload == {
        "job_id": "job-1",
        "shot_index": 2,
        "status": "succeeded",
        "output_key": "baserender/jobs/job-1/working/shot-2.mp4",
    }


def test_post_internal_event_sends_worker_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request, timeout=30):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("BASERENDER_API_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("BASERENDER_WORKER_TOKEN", "secret-token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    post_internal_event({"job_id": "job-1", "step_id": "full", "status": "succeeded"})

    assert captured["url"] == "https://api.example.com/internal/events"
    assert "Bearer secret-token" in captured["headers"]["Authorization"]
    assert captured["body"]["step_id"] == "full"
