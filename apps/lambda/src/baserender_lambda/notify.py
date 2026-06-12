from __future__ import annotations

import json
import os
from typing import Any


def emit_shot_complete_event(
    *,
    job_id: str,
    shot_index: int,
    output_key: str,
    status: str,
    bucket: str | None = None,
    error_message: str | None = None,
    error_detail: str | None = None,
) -> str | None:
    """Publish a BaseRender Shot Complete event when EventBridge is configured."""
    if not os.getenv("BASERENDER_EVENT_BUS"):
        return None

    import boto3

    detail = {
        "job_id": job_id,
        "shot_index": shot_index,
        "output_key": output_key,
        "status": status,
    }
    if bucket:
        detail["bucket"] = bucket
    if error_message:
        detail["error_message"] = error_message
    if error_detail:
        detail["error_detail"] = error_detail

    client = boto3.client(
        "events",
        region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
    )
    response = client.put_events(
        Entries=[
            {
                "Source": os.getenv("BASERENDER_EVENT_SOURCE", "baserender"),
                "DetailType": "BaseRender Shot Complete",
                "Detail": json.dumps(detail),
                "EventBusName": os.getenv("BASERENDER_EVENT_BUS", "default"),
            }
        ]
    )
    failed = int(response.get("FailedEntryCount") or 0)
    if failed:
        raise RuntimeError("EventBridge rejected the BaseRender Shot Complete event.")
    entries = response.get("Entries") or []
    if not entries:
        return None
    event_id = entries[0].get("EventId")
    return str(event_id) if event_id else None
