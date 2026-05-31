from __future__ import annotations

import json
import os
from typing import Any, Protocol


class EventBridgeError(Exception):
    """Raised when EventBridge cannot be accessed or an event cannot be published."""


class EventBridgeClient(Protocol):
    def put_event(
        self,
        detail_type: str,
        detail: dict[str, Any],
        *,
        source: str | None = None,
        bus: str | None = None,
    ) -> str:
        ...


class BotoEventBridgeClient:
    def __init__(
        self,
        *,
        event_bus: str = "default",
        event_source: str = "baserender",
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.event_source = event_source
        self.region_name = region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        self._client = client or self._create_client()

    def put_event(
        self,
        detail_type: str,
        detail: dict[str, Any],
        *,
        source: str | None = None,
        bus: str | None = None,
    ) -> str:
        entry = {
            "Source": source or self.event_source,
            "DetailType": detail_type,
            "Detail": json.dumps(detail),
            "EventBusName": bus or self.event_bus,
        }

        try:
            response = self._client.put_events(Entries=[entry])
        except Exception as exc:
            raise _eventbridge_error(exc) from exc

        failed = response.get("FailedEntryCount", 0)
        if failed:
            entries = response.get("Entries", [])
            error_code = entries[0].get("ErrorCode") if entries else "Unknown"
            error_message = entries[0].get("ErrorMessage") if entries else "EventBridge rejected the event."
            raise EventBridgeError(f"EventBridge put_events failed ({error_code}): {error_message}")

        entries = response.get("Entries", [])
        if not entries:
            raise EventBridgeError("EventBridge put_events returned no entries.")

        event_id = entries[0].get("EventId")
        if not event_id:
            raise EventBridgeError("EventBridge put_events response did not include an event id.")
        return str(event_id)

    def _create_client(self) -> Any:
        import boto3

        return boto3.client("events", region_name=self.region_name)


def get_eventbridge_client() -> EventBridgeClient:
    event_bus = os.getenv("BASERENDER_EVENT_BUS", "default")
    event_source = os.getenv("BASERENDER_EVENT_SOURCE", "baserender")
    region_name = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    return BotoEventBridgeClient(
        event_bus=event_bus,
        event_source=event_source,
        region_name=region_name,
    )


def _eventbridge_error(exc: Exception) -> EventBridgeError:
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return EventBridgeError(f"EventBridge request failed: {exc}")

    if not isinstance(exc, ClientError):
        return EventBridgeError(f"EventBridge request failed: {exc}")

    error = exc.response.get("Error", {})
    code = error.get("Code", "")
    message = error.get("Message", str(exc))

    if code in {"AccessDeniedException", "AccessDenied"}:
        return EventBridgeError(
            "Could not access EventBridge. Check AWS credentials and IAM permissions."
        )

    return EventBridgeError(f"EventBridge request failed: {message}")
