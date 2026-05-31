from __future__ import annotations

import json
from typing import Any

import pytest

from baserender_api.eventbridge_client import (
    BotoEventBridgeClient,
    EventBridgeError,
    get_eventbridge_client,
)


class FakeEventBridgeClient:
    def __init__(self) -> None:
        self.put_events_calls: list[dict[str, Any]] = []

    def put_events(self, **kwargs: Any) -> dict[str, Any]:
        self.put_events_calls.append(kwargs)
        return {
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "event-123"}],
        }


def test_put_event_publishes_detail_to_bus() -> None:
    fake = FakeEventBridgeClient()
    client = BotoEventBridgeClient(
        event_bus="default",
        event_source="baserender",
        client=fake,
    )

    event_id = client.put_event(
        "MediaConvertJobComplete",
        {"job_id": "job-1", "mediaconvert_job_id": "mc-job-123"},
    )

    assert event_id == "event-123"
    assert len(fake.put_events_calls) == 1
    entry = fake.put_events_calls[0]["Entries"][0]
    assert entry["Source"] == "baserender"
    assert entry["DetailType"] == "MediaConvertJobComplete"
    assert entry["EventBusName"] == "default"
    assert json.loads(entry["Detail"]) == {
        "job_id": "job-1",
        "mediaconvert_job_id": "mc-job-123",
    }


def test_put_event_allows_source_and_bus_override() -> None:
    fake = FakeEventBridgeClient()
    client = BotoEventBridgeClient(
        event_bus="default",
        event_source="baserender",
        client=fake,
    )

    client.put_event(
        "TruncationComplete",
        {"job_id": "job-1"},
        source="baserender.orchestrator",
        bus="baserender-events",
    )

    entry = fake.put_events_calls[0]["Entries"][0]
    assert entry["Source"] == "baserender.orchestrator"
    assert entry["EventBusName"] == "baserender-events"


def test_get_eventbridge_client_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeEventBridgeClient()
    monkeypatch.setenv("BASERENDER_EVENT_BUS", "baserender-events")
    monkeypatch.setenv("BASERENDER_EVENT_SOURCE", "baserender.api")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(
        "baserender_api.eventbridge_client.BotoEventBridgeClient._create_client",
        lambda self: fake,
    )

    client = get_eventbridge_client()

    assert isinstance(client, BotoEventBridgeClient)
    assert client.event_bus == "baserender-events"
    assert client.event_source == "baserender.api"


def test_put_event_raises_when_entry_fails() -> None:
    class ErrorClient:
        def put_events(self, **kwargs: Any) -> dict[str, Any]:
            return {
                "FailedEntryCount": 1,
                "Entries": [
                    {
                        "ErrorCode": "InternalFailure",
                        "ErrorMessage": "Service unavailable",
                    }
                ],
            }

    client = BotoEventBridgeClient(client=ErrorClient())

    with pytest.raises(EventBridgeError, match="InternalFailure"):
        client.put_event("TestEvent", {"ok": True})


def test_put_event_maps_client_error() -> None:
    class ErrorClient:
        def put_events(self, **kwargs: Any) -> dict[str, Any]:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
                "PutEvents",
            )

    client = BotoEventBridgeClient(client=ErrorClient())

    with pytest.raises(EventBridgeError, match="Could not access EventBridge"):
        client.put_event("TestEvent", {"ok": True})
