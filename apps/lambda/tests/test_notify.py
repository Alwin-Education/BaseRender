from __future__ import annotations

from unittest.mock import patch

import pytest

from baserender_lambda.notify import emit_shot_complete_event


def test_emit_shot_complete_event_skips_when_bus_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASERENDER_EVENT_BUS", raising=False)

    assert emit_shot_complete_event(
        job_id="job-1",
        shot_index=0,
        output_key="baserender/jobs/job-1/working/shot-0.mp4",
        status="succeeded",
    ) is None


def test_emit_shot_complete_event_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASERENDER_EVENT_BUS", "default")
    monkeypatch.setenv("BASERENDER_EVENT_SOURCE", "baserender")

    class FakeEventsClient:
        def put_events(self, *, Entries):
            assert Entries[0]["DetailType"] == "BaseRender Shot Complete"
            detail = __import__("json").loads(Entries[0]["Detail"])
            assert detail["job_id"] == "job-1"
            assert detail["shot_index"] == 1
            return {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}

    with patch("boto3.client", return_value=FakeEventsClient()):
        event_id = emit_shot_complete_event(
            job_id="job-1",
            shot_index=1,
            output_key="baserender/jobs/job-1/working/shot-1.mp4",
            status="succeeded",
            bucket="test-bucket",
        )

    assert event_id == "evt-1"
