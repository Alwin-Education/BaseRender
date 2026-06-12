"""Unified Lambda entry point: routes HTTP, EventBridge, and direct invokes.

Event shapes:
- Function URL / API Gateway v2 (``requestContext.http``) -> FastAPI app via Mangum.
- EventBridge envelope (``detail-type``) -> in-process orchestration advance or
  hybrid shot render. Replaces the notifier Lambda's HTTP callback hop.
- Direct invoke (``action``) -> scripted entry points.
"""

from __future__ import annotations

import json
from typing import Any

from mangum import Mangum

from baserender_api.app import app
from baserender_api.internal_events import (
    InternalEventConflict,
    InternalEventJobNotFound,
    process_internal_event,
)
from baserender_api.schemas import InternalRenderEvent

from baserender_lambda.events import LambdaShotEvent
from baserender_lambda.handler import handle_shot_event
from baserender_lambda.notifier import normalize_event

_LAMBDA_SHOT_DETAIL_TYPE = "BaseRender Lambda Shot"

_http = Mangum(app, lifespan="off")


def lambda_handler(event: dict[str, Any], context: object | None = None) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Unrecognized Lambda event shape.")
    if "http" in (event.get("requestContext") or {}):
        return _http(event, context)
    if event.get("detail-type") or event.get("DetailType"):
        return _handle_eventbridge(event)
    if event.get("action"):
        return _handle_direct(event)
    raise ValueError("Unrecognized Lambda event shape.")


def _handle_eventbridge(event: dict[str, Any]) -> dict[str, Any]:
    detail_type = str(event.get("detail-type") or event.get("DetailType") or "")
    if detail_type == _LAMBDA_SHOT_DETAIL_TYPE:
        shot_event = LambdaShotEvent.from_mapping(_event_detail(event))
        return handle_shot_event(shot_event)

    payload = normalize_event(event)
    if payload is None:
        return {"status": "ignored"}

    return _advance_job(payload)


def _handle_direct(event: dict[str, Any]) -> dict[str, Any]:
    action = str(event.get("action"))
    if action == "health":
        return {"status": "ok"}
    if action == "shot":
        shot_event = LambdaShotEvent.from_mapping(event.get("payload") or {})
        return handle_shot_event(shot_event)
    if action == "internal_event":
        return _advance_job(dict(event.get("payload") or {}))
    raise ValueError(f"Unknown direct-invoke action: {action}")


def _advance_job(payload: dict[str, Any]) -> dict[str, Any]:
    internal_event = InternalRenderEvent.model_validate(payload)
    try:
        job = process_internal_event(internal_event)
    except (InternalEventJobNotFound, InternalEventConflict) as exc:
        # Stale or duplicate completion events must not raise: EventBridge
        # async-invoke retries would replay them against the job store.
        return {
            "status": "ignored",
            "reason": str(exc),
            "job_id": internal_event.job_id,
        }
    return {"status": "advanced", "job_id": job.id, "job_status": job.status}


def _event_detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail") or event.get("Detail") or {}
    if isinstance(detail, str):
        detail = json.loads(detail)
    if not isinstance(detail, dict):
        raise ValueError("EventBridge event detail must be a JSON object.")
    return detail
