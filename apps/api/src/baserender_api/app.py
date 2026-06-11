from __future__ import annotations

import base64
import os
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
import opentimelineio as otio
from baserender.media_inventory import load_timeline_from_text

from baserender_api.auth.routes import router as auth_router
from baserender_api.auth.session import (
    get_auth_config,
    is_valid_proxy_bearer,
    is_valid_session_token,
)
from baserender_api.job_store import ActiveJobExistsError, get_job_store
from baserender_api.media.provider import get_media_provider
from baserender_api.media.prefix import validate_media_object_key
from baserender_api.media.routes import router as media_router
from baserender_api.defaults import allowed_media_prefix
from baserender_api.mediaconvert_client import get_mediaconvert_client
from baserender_api.eventbridge_client import get_eventbridge_client
from baserender_api.orchestrator import (
    AdvanceResult,
    build_classification_settings,
    build_cloud_artifacts,
    classify_job,
    routing_to_payload,
    should_use_cloud_backend,
    start_render,
    advance as advance_render,
)
from baserender_api.output_storage import (
    get_output_store,
    get_output_upload_target,
    resolve_output_object_key,
)
from baserender_api.schemas import (
    ConversionRequest,
    ConversionResponse,
    InternalRenderEvent,
    OutputUploadTarget,
    RenderJobCreate,
    RenderJobError,
    RenderJobStatus,
    RenderOutput,
    TranscodeJobCreate,
    TranscodeResponse,
    TranscodeResultItem,
    WorkerJobClaim,
    WorkerJobComplete,
    WorkerJobFail,
    WorkerJobHeartbeat,
)
from baserender.timeline_model import UnsupportedTimelineError
from baserender_api.storage import get_artifact_store
from baserender_api.static_files import register_static_routes
from baserender.storage_layout import s3_uri
from baserender.mediaconvert import build_transcode_job
from baserender.transcode import build_transcode_output_key
from baserender.timeline_model import normalize_target_url


app = FastAPI(title="BaseRender API")
app.include_router(auth_router)
app.include_router(media_router)

_PUBLIC_PATHS = {"/health", "/auth/login", "/auth/logout", "/auth/session"}
_WORKER_PATH_PREFIX = "/worker/"
_INTERNAL_PATH_PREFIX = "/internal/"


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    if request.url.path.startswith(_WORKER_PATH_PREFIX):
        return await call_next(request)
    if request.url.path.startswith(_INTERNAL_PATH_PREFIX):
        return await call_next(request)

    if is_valid_proxy_bearer(request.headers.get("authorization")):
        return await call_next(request)

    try:
        config = get_auth_config()
    except RuntimeError as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    token = request.cookies.get(config.cookie_name)
    if not is_valid_session_token(token, config):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Authentication required."},
        )

    return await call_next(request)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", response_model=RenderJobStatus, status_code=status.HTTP_202_ACCEPTED)
def create_job(job: RenderJobCreate) -> RenderJobStatus:
    job_id = str(uuid4())
    try:
        worker_payload, backend, route, steps = _prepare_job_submission(job_id, job)
        status_payload = RenderJobStatus(
            id=job_id,
            status="running" if backend == "cloud" else "queued",
            job=job,
            worker_payload=worker_payload,
            backend=backend,
            route=route,
            steps=steps,
        )
        return get_job_store().submit(status_payload)
    except ActiveJobExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/jobs/current", response_model=RenderJobStatus)
def get_current_job() -> RenderJobStatus:
    job = get_job_store().current()
    if job is None:
        raise HTTPException(status_code=404, detail="No render job found.")
    return job


@app.delete("/jobs/current", response_model=RenderJobStatus)
def cancel_current_job() -> RenderJobStatus:
    store = get_job_store()
    job = store.current()
    if job is None:
        raise HTTPException(status_code=404, detail="No render job found.")
    cancelled = store.cancel(job.id)
    if cancelled is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Render job is not active.",
        )
    return cancelled


@app.post("/jobs/current/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_current_job() -> None:
    if not get_job_store().dismiss():
        raise HTTPException(status_code=404, detail="No render job found.")


@app.get("/jobs/{job_id}", response_model=RenderJobStatus)
def get_job(job_id: str) -> RenderJobStatus:
    job = get_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return job


@app.get("/jobs/{job_id}/output/url")
def get_job_output_url(job_id: str) -> dict[str, str | None]:
    job = get_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    if job.status != "succeeded" or job.output is None:
        raise HTTPException(status_code=404, detail="Render output not found.")

    output_key = job.output.key or _output_object_key(job)
    provider = get_media_provider()
    return {
        "url": provider.presign_get_url(
            output_key,
            expires_in=int(os.getenv("BASERENDER_MEDIA_URL_TTL_SECONDS", "21600")),
        )
    }


@app.post("/worker/jobs/claim", response_model=WorkerJobClaim | None)
def claim_job(request: Request) -> WorkerJobClaim | None:
    _require_worker_token(request)
    return get_job_store().claim()


@app.post("/worker/jobs/{job_id}/heartbeat", response_model=RenderJobStatus)
def heartbeat_job(
    job_id: str,
    request: Request,
    payload: WorkerJobHeartbeat | None = None,
) -> RenderJobStatus:
    _require_worker_token(request)
    job = get_job_store().heartbeat(
        job_id,
        progress=None if payload is None else payload.progress,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return job


@app.post("/worker/jobs/{job_id}/complete", response_model=RenderJobStatus)
def complete_job(
    job_id: str,
    payload: WorkerJobComplete,
    request: Request,
) -> RenderJobStatus:
    _require_worker_token(request)
    current_job = _require_active_job(job_id)
    output = _output_for_job(current_job)
    job = get_job_store().complete(job_id, report=payload.report, output=output)
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    _store_report(job_id, payload.report)
    return job


@app.post("/worker/jobs/{job_id}/fail", response_model=RenderJobStatus)
def fail_job(job_id: str, payload: WorkerJobFail, request: Request) -> RenderJobStatus:
    _require_worker_token(request)
    job = get_job_store().fail(
        job_id,
        error=RenderJobError(message=payload.message, detail=payload.detail),
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return job


@app.post("/internal/events", response_model=RenderJobStatus)
def handle_internal_event(
    payload: InternalRenderEvent,
    request: Request,
) -> RenderJobStatus:
    _require_worker_token(request)
    store = get_job_store()
    job = store.get(payload.job_id)
    if job is None or job.backend != "cloud":
        raise HTTPException(status_code=404, detail="Render job not found.")
    if job.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Render job is not active.",
        )

    try:
        result: AdvanceResult = advance_render(
            job,
            payload,
            mediaconvert=get_mediaconvert_client(),
            eventbridge=get_eventbridge_client(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    updated = store.set_steps(
        job.id,
        result.job.steps,
        status=result.job.status,
        output=result.job.output,
        error=result.job.error,
        report=result.job.report,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return updated


@app.get("/worker/jobs/{job_id}/artifacts/input")
def get_job_input(job_id: str, request: Request) -> Response:
    _require_worker_token(request)
    _require_job(job_id)
    return Response(
        content=get_artifact_store().get_bytes(_input_key(job_id)),
        media_type="application/json",
    )


@app.get("/worker/jobs/{job_id}/artifacts/luts/{lut_id}")
def get_job_lut(job_id: str, lut_id: str, request: Request) -> Response:
    _require_worker_token(request)
    _require_job(job_id)
    return Response(
        content=get_artifact_store().get_bytes(_lut_key(job_id, lut_id)),
        media_type="application/octet-stream",
    )


@app.get(
    "/worker/jobs/{job_id}/artifacts/output/upload-target",
    response_model=OutputUploadTarget,
)
def get_job_output_upload_target(job_id: str, request: Request) -> OutputUploadTarget:
    _require_worker_token(request)
    job = _require_job(job_id)
    if job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Render job must be running before requesting an output upload target.",
        )
    return OutputUploadTarget(
        **get_output_upload_target(
            _output_object_key(job),
            expires_in=int(os.getenv("BASERENDER_MEDIA_URL_TTL_SECONDS", "21600")),
        )
    )


@app.post("/conversions", response_model=ConversionResponse, status_code=status.HTTP_202_ACCEPTED)
def create_conversion(_request: ConversionRequest) -> ConversionResponse:
    return ConversionResponse(
        status="not_implemented",
        message=(
            "NLE-to-OTIO conversion will run in the backend before submitting a "
            "prepared render job to the worker."
        ),
    )


@app.post("/transcode", response_model=TranscodeResponse, status_code=status.HTTP_202_ACCEPTED)
def create_transcode(request: TranscodeJobCreate) -> TranscodeResponse:
    bucket = os.getenv("BASERENDER_S3_BUCKET")
    if not bucket:
        raise HTTPException(status_code=500, detail="BASERENDER_S3_BUCKET is not configured.")

    allowed_prefix = allowed_media_prefix()
    settings = request.settings.to_render_settings()
    container = request.container.strip().lower().lstrip(".") or "mp4"
    mediaconvert = None if request.dry_run else get_mediaconvert_client()

    results: list[TranscodeResultItem] = []
    for source_key in request.inputs:
        normalized_source = source_key.strip().strip("/")
        if not normalized_source:
            raise HTTPException(status_code=400, detail="Each input key must be non-empty.")
        try:
            normalized_source = validate_media_object_key(normalized_source, allowed_prefix)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            output_key = build_transcode_output_key(
                normalized_source,
                container=container,
                prepend_folder=request.prepend_folder,
                append_folder=request.append_folder,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        media_uri = s3_uri(bucket, normalized_source)
        output_destination = _transcode_output_destination(bucket, output_key)
        job_settings = build_transcode_job(
            media_uri,
            output_destination=output_destination,
            settings=settings,
            container=container,
        )

        mediaconvert_job_id: str | None = None
        if mediaconvert is not None:
            mediaconvert_job_id = mediaconvert.create_job(
                job_settings,
                user_metadata={"transcode": "1"},
            )

        results.append(
            TranscodeResultItem(
                source_key=normalized_source,
                output_key=output_key,
                mediaconvert_job_id=mediaconvert_job_id,
            )
        )

    return TranscodeResponse(results=results)


def _prepare_job_submission(
    job_id: str,
    job: RenderJobCreate,
) -> tuple[dict, str, str | None, list]:
    if not job.otio_content_base64 and not job.input_path:
        raise ValueError("Either input_path or otio_content_base64 is required.")

    if should_use_cloud_backend() and job.otio_content_base64:
        bucket = os.getenv("BASERENDER_S3_BUCKET")
        if bucket:
            base_payload = job.to_worker_payload()
            base_payload["output_object_key"] = resolve_output_object_key(job.output_path)
            try:
                return _prepare_cloud_submission(
                    job_id,
                    job,
                    base_payload,
                    bucket=bucket,
                )
            except UnsupportedTimelineError:
                pass

    worker_payload = _prepare_worker_payload(job_id, job)
    return worker_payload, "worker", None, []


def _prepare_cloud_submission(
    job_id: str,
    job: RenderJobCreate,
    worker_payload: dict,
    *,
    bucket: str,
) -> tuple[dict, str, str | None, list]:
    artifact_prefix = os.getenv("BASERENDER_ARTIFACT_PREFIX", "baserender")
    cloud_artifacts = build_cloud_artifacts(
        job_id,
        job,
        bucket=bucket,
        artifact_prefix=artifact_prefix,
    )
    otio_text = base64.b64decode(job.otio_content_base64 or "").decode("utf-8")
    prepared_otio = _prepare_cloud_otio(otio_text, job)
    store = get_artifact_store()
    store.put_bytes(_input_key(job_id), prepared_otio.encode("utf-8"))

    for lut in job.lut_files:
        store.put_bytes(_lut_key(job_id, lut.id), base64.b64decode(lut.content_base64))

    classification_settings = build_classification_settings(job, cloud_artifacts.clip_lut_artifacts)
    routing = classify_job(
        prepared_otio,
        settings=classification_settings,
        track_index=job.track_index,
    )

    worker_payload["artifacts"] = {
        "input": _artifact_store_key(_input_key(job_id), artifact_prefix),
        "luts": [
            {"id": lut_id, "key": _artifact_store_key(lut_key, artifact_prefix)}
            for lut_id, lut_key in cloud_artifacts.lut_keys.items()
        ],
    }
    worker_payload["clip_lut_artifacts"] = cloud_artifacts.clip_lut_artifacts
    worker_payload["media_uris"] = cloud_artifacts.media_uris
    worker_payload["lut_uris"] = cloud_artifacts.lut_uris
    worker_payload["routing"] = routing_to_payload(routing)

    steps = start_render(
        job_id,
        job,
        routing,
        cloud_artifacts,
        mediaconvert=get_mediaconvert_client(),
        bucket=bucket,
        artifact_prefix=artifact_prefix,
    )
    return worker_payload, "cloud", routing.route.value, steps


def _prepare_worker_payload(job_id: str, job: RenderJobCreate) -> dict:
    payload = job.to_worker_payload()
    payload["output_object_key"] = resolve_output_object_key(job.output_path)
    if not job.otio_content_base64:
        if not job.input_path:
            raise ValueError("Either input_path or otio_content_base64 is required.")
        return payload

    otio_text = base64.b64decode(job.otio_content_base64).decode("utf-8")
    prepared_otio, url_rewrites = _prepare_otio(otio_text, job)
    store = get_artifact_store()
    store.put_bytes(_input_key(job_id), prepared_otio.encode("utf-8"))

    lut_artifacts = []
    for lut in job.lut_files:
        store.put_bytes(_lut_key(job_id, lut.id), base64.b64decode(lut.content_base64))
        lut_artifacts.append({"id": lut.id, "name": lut.name})

    payload["input_path"] = f"artifact://jobs/{job_id}/input.otio"
    payload["output_path"] = job.output_path
    payload["artifacts"] = {
        "input": _input_key(job_id),
        "luts": lut_artifacts,
    }
    payload["clip_lut_artifacts"] = _clip_lut_artifacts(job, lut_artifacts, url_rewrites)
    return payload


def _prepare_cloud_otio(otio_text: str, job: RenderJobCreate) -> str:
    """Keep normalized media URLs for cloud execution (no presigned rewrites)."""
    _ = job
    timeline = load_timeline_from_text(otio_text)
    return otio.adapters.write_to_string(timeline, adapter_name="otio_json")


def _prepare_otio(otio_text: str, job: RenderJobCreate) -> tuple[str, dict[str, str]]:
    timeline = load_timeline_from_text(otio_text)
    assignments = _media_assignment_by_normalized_url(job)
    url_rewrites: dict[str, str] = {}
    if assignments:
        provider = get_media_provider()
        for clip in timeline.find_clips():
            media_reference = clip.media_reference
            if not isinstance(media_reference, otio.schema.ExternalReference):
                continue
            target_url = media_reference.target_url or ""
            normalized_url = normalize_target_url(target_url) if target_url else None
            assigned_key = assignments.get(normalized_url or "")
            if assigned_key:
                presigned_url = provider.presign_get_url(
                    assigned_key,
                    expires_in=int(os.getenv("BASERENDER_MEDIA_URL_TTL_SECONDS", "21600")),
                )
                url_rewrites[str(normalized_url)] = presigned_url
                media_reference.target_url = presigned_url
    return otio.adapters.write_to_string(timeline, adapter_name="otio_json"), url_rewrites


def _media_assignment_by_normalized_url(job: RenderJobCreate) -> dict[str, str]:
    references_by_id = {
        str(reference.get("id")): reference
        for reference in job.media_references
        if reference.get("id") is not None
    }
    assignments: dict[str, str] = {}
    for reference_id, key in job.media_assignments.items():
        reference = references_by_id.get(reference_id)
        normalized_url = reference.get("normalized_url") if reference else None
        if normalized_url and key:
            assignments[str(normalized_url)] = key
    return assignments


def _clip_lut_artifacts(
    job: RenderJobCreate,
    lut_artifacts: list[dict[str, str]],
    url_rewrites: dict[str, str] | None = None,
) -> list[dict]:
    lut_ids = {artifact["id"] for artifact in lut_artifacts}
    references_by_id = {
        str(reference.get("id")): reference
        for reference in job.media_references
        if reference.get("id") is not None
    }
    artifacts = []
    for reference_id, lut_id in job.lut_assignments.items():
        if lut_id == "none" or lut_id not in lut_ids:
            continue
        reference = references_by_id.get(reference_id)
        normalized_url = reference.get("normalized_url") if reference else None
        if normalized_url:
            media_url = (url_rewrites or {}).get(str(normalized_url), normalized_url)
            artifacts.append(
                {
                    "normalized_url": normalized_url,
                    "media_url": media_url,
                    "lut_id": lut_id,
                }
            )
    return artifacts


def _require_worker_token(request: Request) -> None:
    expected = os.getenv("BASERENDER_WORKER_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="BASERENDER_WORKER_TOKEN is not configured.")
    token = request.headers.get("x-baserender-worker-token")
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if token != expected:
        raise HTTPException(status_code=401, detail="Worker authentication required.")


def _require_job(job_id: str) -> RenderJobStatus:
    job = get_job_store().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return job


def _require_active_job(job_id: str) -> RenderJobStatus:
    from baserender_api.job_store import ACTIVE_STATUSES

    job = _require_job(job_id)
    if job.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=404, detail="Render job not found.")
    return job


def _store_report(job_id: str, report: dict) -> None:
    import json

    get_artifact_store().put_bytes(
        _report_key(job_id),
        json.dumps(report, indent=2, sort_keys=True).encode("utf-8"),
    )


def _output_for_job(job: RenderJobStatus) -> RenderOutput | None:
    if job.job.dry_run:
        return None
    output_key = _output_object_key(job)
    output_store = get_output_store()
    output_size = _verified_output_size(output_store, output_key)
    return RenderOutput(
        path=output_store.location(output_key),
        key=output_key,
        size=output_size,
    )


def _output_object_key(job: RenderJobStatus) -> str:
    key = job.worker_payload.get("output_object_key")
    return str(key) if key else resolve_output_object_key(job.job.output_path)


def _output_size(output_store, output_key: str) -> int | None:
    try:
        return output_store.size(output_key)
    except Exception:
        return None


def _verified_output_size(output_store, output_key: str) -> int:
    output_size = _output_size(output_store, output_key)
    if output_size is None or output_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Render output was not uploaded or is empty.",
        )
    return output_size


def _input_key(job_id: str) -> str:
    return f"jobs/{job_id}/inputs/timeline.otio"


def _lut_key(job_id: str, lut_id: str) -> str:
    return f"jobs/{job_id}/inputs/luts/{lut_id}"


def _report_key(job_id: str) -> str:
    return f"jobs/{job_id}/output/report.json"


def _artifact_store_key(relative_key: str, artifact_prefix: str) -> str:
    normalized = relative_key.strip("/")
    prefix = artifact_prefix.strip("/")
    if prefix and not normalized.startswith(f"{prefix}/"):
        return f"{prefix}/{normalized}"
    return normalized


def _transcode_output_destination(bucket: str, output_key: str) -> str:
    normalized = output_key.strip("/")
    for suffix in (".mp4", ".mov"):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return s3_uri(bucket, normalized)


register_static_routes(app)
