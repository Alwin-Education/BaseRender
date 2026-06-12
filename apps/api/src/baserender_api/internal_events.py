"""Shared handling of internal render events (MediaConvert/Lambda completions).

Used by both the HTTP ``POST /internal/events`` route and the unified Lambda
handler's in-process EventBridge path.
"""

from __future__ import annotations

from baserender_api.eventbridge_client import get_eventbridge_client
from baserender_api.job_store import get_job_store
from baserender_api.mediaconvert_client import get_mediaconvert_client
from baserender_api.orchestrator import AdvanceResult, advance as advance_render
from baserender_api.schemas import InternalRenderEvent, RenderJobStatus


class InternalEventError(Exception):
    """Base class for internal render event failures."""


class InternalEventJobNotFound(InternalEventError):
    """The referenced job does not exist or is not a cloud job."""


class InternalEventConflict(InternalEventError):
    """The referenced job is not in an active state."""


class InternalEventInvalid(InternalEventError):
    """The event payload does not match the job's steps."""


def process_internal_event(payload: InternalRenderEvent) -> RenderJobStatus:
    """Advance the active cloud job in response to a completion event."""
    store = get_job_store()
    job = store.get(payload.job_id)
    if job is None or job.backend != "cloud":
        raise InternalEventJobNotFound("Render job not found.")
    if job.status not in {"queued", "running"}:
        raise InternalEventConflict("Render job is not active.")

    try:
        result: AdvanceResult = advance_render(
            job,
            payload,
            mediaconvert=get_mediaconvert_client(),
            eventbridge=get_eventbridge_client(),
        )
    except ValueError as exc:
        raise InternalEventInvalid(str(exc)) from exc

    updated = store.set_steps(
        job.id,
        result.job.steps,
        status=result.job.status,
        output=result.job.output,
        error=result.job.error,
        report=result.job.report,
    )
    if updated is None:
        raise InternalEventJobNotFound("Render job not found.")
    return updated
