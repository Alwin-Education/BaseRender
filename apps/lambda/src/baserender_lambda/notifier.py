from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def lambda_handler(event: dict[str, Any], context: object | None = None) -> dict[str, Any]:
    """Forward MediaConvert and BaseRender completion events to the API."""
    _ = context
    payload = normalize_event(event)
    if payload is None:
        return {"status": "ignored"}

    post_internal_event(payload)
    return {"status": "forwarded", "job_id": payload.get("job_id")}


def normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    detail_type = str(event.get("detail-type") or event.get("DetailType") or "")
    detail = event.get("detail") or event.get("Detail") or {}
    if isinstance(detail, str):
        detail = json.loads(detail)

    if detail_type == "MediaConvert Job State Change":
        return _normalize_mediaconvert_event(detail)
    if detail_type == "BaseRender Shot Complete":
        return _normalize_shot_complete_event(detail)
    return None


def _normalize_mediaconvert_event(detail: dict[str, Any]) -> dict[str, Any] | None:
    status = str(detail.get("status") or "").upper()
    if status not in {"COMPLETE", "ERROR", "CANCELED"}:
        return None

    user_metadata = detail.get("userMetadata") or {}
    job_id = user_metadata.get("job_id")
    step_id = user_metadata.get("step_id")
    if not job_id or not step_id:
        return None

    payload: dict[str, Any] = {
        "job_id": str(job_id),
        "step_id": str(step_id),
        "external_id": str(detail.get("jobId") or ""),
        "status": "succeeded" if status == "COMPLETE" else "failed",
    }
    if status != "COMPLETE":
        payload["error"] = {
            "message": f"MediaConvert job {status.lower()}.",
            "detail": str(detail.get("errorMessage") or detail.get("status") or status),
        }
    return payload


def _normalize_shot_complete_event(detail: dict[str, Any]) -> dict[str, Any]:
    status = str(detail.get("status") or "succeeded").lower()
    payload: dict[str, Any] = {
        "job_id": str(detail["job_id"]),
        "shot_index": int(detail["shot_index"]),
        "status": "succeeded" if status == "succeeded" else "failed",
    }
    output_key = detail.get("output_key")
    if output_key:
        payload["output_key"] = str(output_key)
    if payload["status"] == "failed":
        payload["error"] = {
            "message": str(detail.get("error_message") or "Lambda shot render failed."),
            "detail": detail.get("error_detail"),
        }
    return payload


def post_internal_event(payload: dict[str, Any]) -> None:
    api_base_url = os.getenv("BASERENDER_API_BASE_URL", "").rstrip("/")
    worker_token = os.getenv("BASERENDER_WORKER_TOKEN", "")
    if not api_base_url:
        raise RuntimeError("BASERENDER_API_BASE_URL must be set.")
    if not worker_token:
        raise RuntimeError("BASERENDER_WORKER_TOKEN must be set.")

    request = urllib.request.Request(
        f"{api_base_url}/internal/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {worker_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 400:
                raise RuntimeError(f"API returned HTTP {response.status}.")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API callback failed with HTTP {exc.code}: {body}") from exc
