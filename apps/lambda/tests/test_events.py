from __future__ import annotations

import pytest

from baserender_lambda.events import LambdaShotEvent


def _sample_event(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "job-1",
        "bucket": "test-bucket",
        "shot_index": 1,
        "media_url": "/media/shot.mov",
        "timeline_offset_seconds": 2.0,
        "source_in_seconds": 0.5,
        "source_out_seconds": 3.5,
        "reasons": ["keyframes"],
        "proxy_key": "baserender/jobs/job-1/working/proxy-1",
        "otio_key": "baserender/jobs/job-1/inputs/timeline.otio",
        "lut_keys": {"/media/shot.mov": "baserender/jobs/job-1/inputs/luts/lut-1"},
        "output_key": "baserender/jobs/job-1/working/shot-1",
        "settings": {"width": 1920, "height": 1080, "fps": 24},
    }
    payload.update(overrides)
    return payload


def test_lambda_shot_event_from_mapping() -> None:
    event = LambdaShotEvent.from_mapping(_sample_event())

    assert event.job_id == "job-1"
    assert event.bucket == "test-bucket"
    assert event.shot_index == 1
    assert event.media_url == "/media/shot.mov"
    assert event.timeline_offset_seconds == 2.0
    assert event.source_in_seconds == 0.5
    assert event.source_out_seconds == 3.5
    assert event.reasons == ("keyframes",)
    assert event.proxy_key.endswith("proxy-1")
    assert event.lut_keys["/media/shot.mov"].endswith("lut-1")


def test_lambda_shot_event_reads_bucket_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASERENDER_S3_BUCKET", "env-bucket")
    payload = _sample_event()
    del payload["bucket"]

    event = LambdaShotEvent.from_mapping(payload)
    assert event.bucket == "env-bucket"


def test_lambda_shot_event_missing_required_field_raises() -> None:
    payload = _sample_event()
    del payload["proxy_key"]

    with pytest.raises(ValueError, match="proxy_key"):
        LambdaShotEvent.from_mapping(payload)


def test_lambda_shot_event_invalid_settings_raises() -> None:
    with pytest.raises(ValueError, match="settings"):
        LambdaShotEvent.from_mapping(_sample_event(settings="bad"))


def test_lambda_shot_event_missing_bucket_and_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASERENDER_S3_BUCKET", raising=False)
    payload = _sample_event()
    del payload["bucket"]

    with pytest.raises(ValueError, match="bucket"):
        LambdaShotEvent.from_mapping(payload)
