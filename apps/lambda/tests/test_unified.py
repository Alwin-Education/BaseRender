from __future__ import annotations

import json
from typing import Any

import pytest

from baserender_api.internal_events import InternalEventJobNotFound
from baserender_api.schemas import InternalRenderEvent
from baserender_lambda.events import LambdaShotEvent
from baserender_lambda import unified


def _function_url_event(method: str, path: str) -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "example.lambda-url.eu-west-1.on.aws"},
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "pytest",
            },
            "stage": "$default",
        },
        "isBase64Encoded": False,
    }


def _shot_detail() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "shot_index": 1,
        "media_url": "/media/a.mov",
        "proxy_key": "jobs/job-1/working/shot-1-proxy.mp4",
        "otio_key": "jobs/job-1/inputs/timeline.otio",
        "output_key": "jobs/job-1/working/shot-1",
        "settings": {"width": 640, "height": 360, "fps": 24},
        "bucket": "test-bucket",
        "timeline_offset_seconds": 0.0,
        "source_in_seconds": 1.0,
        "source_out_seconds": 2.0,
    }


def test_http_health_routes_through_mangum() -> None:
    response = unified.lambda_handler(_function_url_event("GET", "/health"), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok"}


def test_lambda_shot_envelope_dispatches_shot_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_handle(shot_event: LambdaShotEvent) -> dict[str, Any]:
        captured["event"] = shot_event
        return {"status": "ok", "job_id": shot_event.job_id}

    monkeypatch.setattr(unified, "handle_shot_event", fake_handle)

    result = unified.lambda_handler(
        {
            "detail-type": "BaseRender Lambda Shot",
            "source": "baserender",
            "detail": _shot_detail(),
        },
        None,
    )

    assert result == {"status": "ok", "job_id": "job-1"}
    assert captured["event"].shot_index == 1
    assert captured["event"].bucket == "test-bucket"


def test_lambda_shot_envelope_accepts_json_string_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        unified,
        "handle_shot_event",
        lambda shot_event: {"status": "ok", "job_id": shot_event.job_id},
    )

    result = unified.lambda_handler(
        {
            "detail-type": "BaseRender Lambda Shot",
            "detail": json.dumps(_shot_detail()),
        },
        None,
    )

    assert result["status"] == "ok"


def test_mediaconvert_complete_advances_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeJob:
        id = "job-1"
        status = "succeeded"

    def fake_process(payload: InternalRenderEvent):
        captured["payload"] = payload
        return FakeJob()

    monkeypatch.setattr(unified, "process_internal_event", fake_process)

    result = unified.lambda_handler(
        {
            "detail-type": "MediaConvert Job State Change",
            "source": "aws.mediaconvert",
            "detail": {
                "status": "COMPLETE",
                "jobId": "mc-9",
                "userMetadata": {"job_id": "job-1", "step_id": "full"},
            },
        },
        None,
    )

    assert result == {"status": "advanced", "job_id": "job-1", "job_status": "succeeded"}
    payload = captured["payload"]
    assert isinstance(payload, InternalRenderEvent)
    assert payload.job_id == "job-1"
    assert payload.step_id == "full"
    assert payload.external_id == "mc-9"
    assert payload.status == "succeeded"


def test_mediaconvert_progressing_is_ignored() -> None:
    result = unified.lambda_handler(
        {
            "detail-type": "MediaConvert Job State Change",
            "detail": {"status": "PROGRESSING", "userMetadata": {"job_id": "job-1"}},
        },
        None,
    )

    assert result == {"status": "ignored"}


def test_stale_event_is_ignored_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_process(payload: InternalRenderEvent):
        raise InternalEventJobNotFound("Render job not found.")

    monkeypatch.setattr(unified, "process_internal_event", fake_process)

    result = unified.lambda_handler(
        {
            "detail-type": "BaseRender Shot Complete",
            "detail": {"job_id": "job-gone", "shot_index": 0, "status": "succeeded"},
        },
        None,
    )

    assert result["status"] == "ignored"
    assert result["job_id"] == "job-gone"


def test_direct_invoke_health() -> None:
    assert unified.lambda_handler({"action": "health"}, None) == {"status": "ok"}


def test_unknown_event_shape_raises() -> None:
    with pytest.raises(ValueError):
        unified.lambda_handler({"unexpected": True}, None)
