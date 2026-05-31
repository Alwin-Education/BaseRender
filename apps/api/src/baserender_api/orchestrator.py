from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from baserender.mediaconvert import (
    build_full_render_job,
    build_per_shot_lut_job,
    build_stitch_job,
    build_truncation_job,
)
from baserender.otio_reader import load_timeline_plan
from baserender.routing import RouteKind, RoutingPlan, ShotHandler, ShotRouting, classify_timeline
from baserender.storage_layout import (
    DEFAULT_ARTIFACT_PREFIX,
    s3_uri,
    shot_output_key,
    working_proxy_key,
)
from baserender.timeline_model import RenderSettings, UnsupportedTimelineError

from baserender_api.eventbridge_client import EventBridgeClient
from baserender_api.mediaconvert_client import MediaConvertClient
from baserender_api.output_storage import resolve_output_object_key
from baserender_api.schemas import (
    InternalRenderEvent,
    RenderJobCreate,
    RenderJobError,
    RenderJobStatus,
    RenderOutput,
    RenderStep,
)


@dataclass(frozen=True)
class CloudArtifacts:
    input_key: str
    lut_keys: dict[str, str]
    clip_lut_artifacts: list[dict[str, Any]]
    media_uris: dict[str, str]
    lut_uris: dict[str, str]


@dataclass(frozen=True)
class AdvanceResult:
    job: RenderJobStatus
    completed: bool = False


def render_backend() -> str:
    return os.getenv("BASERENDER_RENDER_BACKEND", "cloud").strip().lower()


def should_use_cloud_backend() -> bool:
    return render_backend() == "cloud"


def classify_job(
    otio_text: str,
    *,
    settings: RenderSettings,
    track_index: int | None = None,
) -> RoutingPlan:
    with tempfile.NamedTemporaryFile("w", suffix=".otio", encoding="utf-8", delete=False) as handle:
        handle.write(otio_text)
        temp_path = handle.name

    try:
        load_result = load_timeline_plan(
            temp_path,
            track_index=track_index,
            settings=settings,
            fail_fast=True,
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)

    if load_result.plan is None:
        message = load_result.issues[0].message if load_result.issues else "Timeline could not be loaded."
        raise UnsupportedTimelineError(message)

    return classify_timeline(load_result.plan, settings=settings)


def build_cloud_artifacts(
    job_id: str,
    job: RenderJobCreate,
    *,
    bucket: str,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> CloudArtifacts:
    references_by_id = {
        str(reference.get("id")): reference
        for reference in job.media_references
        if reference.get("id") is not None
    }
    media_uris: dict[str, str] = {}
    for reference_id, key in job.media_assignments.items():
        reference = references_by_id.get(reference_id)
        normalized_url = reference.get("normalized_url") if reference else None
        if normalized_url and key:
            media_uris[str(normalized_url)] = s3_uri(bucket, key)

    lut_keys = {lut.id: _lut_key(job_id, lut.id) for lut in job.lut_files}
    lut_uris = {
        lut_id: s3_uri(bucket, _artifact_object_key(lut_key, artifact_prefix))
        for lut_id, lut_key in lut_keys.items()
    }

    clip_lut_artifacts = _clip_lut_artifacts(job, lut_keys)
    return CloudArtifacts(
        input_key=_input_key(job_id),
        lut_keys=lut_keys,
        clip_lut_artifacts=clip_lut_artifacts,
        media_uris=media_uris,
        lut_uris=lut_uris,
    )


def build_classification_settings(
    job: RenderJobCreate,
    clip_lut_artifacts: list[dict[str, Any]],
) -> RenderSettings:
    settings = job.settings.to_render_settings()
    if settings.clip_luts:
        return settings

    clip_luts = {
        str(artifact["normalized_url"]): f"lut://{artifact['lut_id']}"
        for artifact in clip_lut_artifacts
    }
    return RenderSettings(
        width=settings.width,
        height=settings.height,
        fps=settings.fps,
        audio_sample_rate=settings.audio_sample_rate,
        audio_channel_layout=settings.audio_channel_layout,
        clip_luts=clip_luts,
        video_codec=settings.video_codec,
        video_bitrate=settings.video_bitrate,
        video_encoder_preset=settings.video_encoder_preset,
        video_faststart=settings.video_faststart,
        audio_codec=settings.audio_codec,
        audio_bitrate=settings.audio_bitrate,
        video_crf=settings.video_crf,
    )


def start_render(
    job_id: str,
    job: RenderJobCreate,
    routing: RoutingPlan,
    artifacts: CloudArtifacts,
    *,
    mediaconvert: MediaConvertClient,
    bucket: str | None = None,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> list[RenderStep]:
    bucket = bucket or os.getenv("BASERENDER_S3_BUCKET", "")
    settings = job.settings.to_render_settings()
    container = _output_container(job)
    steps = _build_initial_steps(
        job_id,
        job,
        routing,
        artifacts,
        bucket=bucket,
        artifact_prefix=artifact_prefix,
    )

    submitted: list[RenderStep] = []
    for step in steps:
        if step.status != "pending" or step.depends_on:
            submitted.append(step)
            continue
        submitted.append(
            _submit_step(
                step,
                job_id=job_id,
                job=job,
                routing=routing,
                artifacts=artifacts,
                settings=settings,
                container=container,
                mediaconvert=mediaconvert,
                bucket=bucket,
                artifact_prefix=artifact_prefix,
            )
        )
    return submitted


def advance(
    job: RenderJobStatus,
    event: InternalRenderEvent,
    *,
    mediaconvert: MediaConvertClient,
    eventbridge: EventBridgeClient,
    bucket: str | None = None,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> AdvanceResult:
    bucket = bucket or os.getenv("BASERENDER_S3_BUCKET", "")
    step = _find_step(job.steps, event)
    if step is None:
        raise ValueError(f"No render step matched event for job {event.job_id!r}.")

    if event.status == "failed":
        failed_step = _copy_step(
            step,
            status="failed",
            error=event.error or RenderJobError(message="Render step failed."),
        )
        updated_steps = _replace_step(job.steps, failed_step)
        failed_job = _copy_job(
            job,
            status="failed",
            steps=updated_steps,
            error=failed_step.error,
            progress=None,
        )
        return AdvanceResult(job=failed_job)

    succeeded_step = _copy_step(
        step,
        status="succeeded",
        output_key=event.output_key or step.output_key,
    )
    steps = _replace_step(job.steps, succeeded_step)
    settings = job.job.settings.to_render_settings()
    container = _output_container(job.job)
    artifacts = _artifacts_from_job(job)

    if succeeded_step.kind == "truncation" and succeeded_step.shot_index is not None:
        lambda_step = _find_step_by_kind(steps, "lambda_shot", succeeded_step.shot_index)
        if lambda_step is not None:
            _emit_lambda_shot_event(
                job,
                routing_shot=_shot_from_step(job, lambda_step),
                artifacts=artifacts,
                eventbridge=eventbridge,
                bucket=bucket,
                artifact_prefix=artifact_prefix,
            )
            steps = _replace_step(
                steps,
                _copy_step(lambda_step, status="running"),
            )

    if _all_part_steps_complete(steps):
        stitch_step = _find_step_by_kind(steps, "stitch")
        if stitch_step is not None and stitch_step.status == "pending":
            routing = _routing_from_job(job)
            submitted = _submit_step(
                stitch_step,
                job_id=job.id,
                job=job.job,
                routing=routing,
                artifacts=artifacts,
                settings=settings,
                container=container,
                mediaconvert=mediaconvert,
                bucket=bucket,
                artifact_prefix=artifact_prefix,
                part_steps=_part_steps(steps),
            )
            steps = _replace_step(steps, submitted)
            updated_job = _copy_job(job, status="running", steps=steps)
            return AdvanceResult(job=updated_job)

    if _is_terminal_success(steps, routing_route=job.route):
        output = _output_for_terminal_step(job, succeeded_step, bucket=bucket)
        updated_job = _copy_job(
            job,
            status="succeeded",
            steps=steps,
            output=output,
            error=None,
            progress=None,
        )
        return AdvanceResult(job=updated_job, completed=True)

    runnable = _submit_runnable_steps(
        steps,
        job_id=job.id,
        job=job.job,
        routing=_routing_from_job(job),
        artifacts=artifacts,
        settings=settings,
        container=container,
        mediaconvert=mediaconvert,
        bucket=bucket,
        artifact_prefix=artifact_prefix,
    )
    return AdvanceResult(job=_copy_job(job, status="running", steps=runnable))


def _build_initial_steps(
    job_id: str,
    job: RenderJobCreate,
    routing: RoutingPlan,
    artifacts: CloudArtifacts,
    *,
    bucket: str,
    artifact_prefix: str,
) -> list[RenderStep]:
    if routing.route is RouteKind.FULL_MEDIACONVERT:
        output_key = resolve_output_object_key(job.output_path)
        return [
            RenderStep(
                id="full",
                kind="full",
                backend="mediaconvert",
                status="pending",
                output_key=output_key,
            )
        ]

    steps: list[RenderStep] = []
    for shot in routing.shots:
        if shot.handler is ShotHandler.LAMBDA_FFMPEG:
            truncate_id = f"truncate-{shot.index}"
            steps.append(
                RenderStep(
                    id=truncate_id,
                    kind="truncation",
                    backend="mediaconvert",
                    shot_index=shot.index,
                    status="pending",
                    output_key=working_proxy_key(job_id, shot.index, artifact_prefix=artifact_prefix),
                )
            )
            steps.append(
                RenderStep(
                    id=f"lambda-{shot.index}",
                    kind="lambda_shot",
                    backend="lambda",
                    shot_index=shot.index,
                    status="pending",
                    output_key=shot_output_key(job_id, shot.index, artifact_prefix=artifact_prefix),
                    depends_on=[truncate_id],
                )
            )
        else:
            steps.append(
                RenderStep(
                    id=f"lut-{shot.index}",
                    kind="per_shot_lut",
                    backend="mediaconvert",
                    shot_index=shot.index,
                    status="pending",
                    output_key=shot_output_key(job_id, shot.index, artifact_prefix=artifact_prefix),
                )
            )

    if routing.requires_final_stitch:
        steps.append(
            RenderStep(
                id="stitch",
                kind="stitch",
                backend="mediaconvert",
                status="pending",
                output_key=resolve_output_object_key(job.output_path),
                depends_on=[step.id for step in steps if step.kind != "stitch"],
            )
        )
    return steps


def _submit_step(
    step: RenderStep,
    *,
    job_id: str,
    job: RenderJobCreate,
    routing: RoutingPlan,
    artifacts: CloudArtifacts,
    settings: RenderSettings,
    container: str,
    mediaconvert: MediaConvertClient,
    bucket: str,
    artifact_prefix: str,
    part_steps: list[RenderStep] | None = None,
) -> RenderStep:
    if step.kind == "lambda_shot":
        return _copy_step(step, status="running")

    job_settings, user_metadata = _mediaconvert_payload(
        step,
        job_id=job_id,
        job=job,
        routing=routing,
        artifacts=artifacts,
        settings=settings,
        container=container,
        bucket=bucket,
        artifact_prefix=artifact_prefix,
        part_steps=part_steps,
    )
    external_id = mediaconvert.create_job(job_settings, user_metadata=user_metadata)
    return _copy_step(step, status="running", external_id=external_id)


def _mediaconvert_payload(
    step: RenderStep,
    *,
    job_id: str,
    job: RenderJobCreate,
    routing: RoutingPlan,
    artifacts: CloudArtifacts,
    settings: RenderSettings,
    container: str,
    bucket: str,
    artifact_prefix: str,
    part_steps: list[RenderStep] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    user_metadata = {"job_id": job_id, "step_id": step.id}

    if step.kind == "full":
        lut_uri = _single_lut_uri(routing, artifacts)
        destination = _s3_destination(bucket, step.output_key or resolve_output_object_key(job.output_path))
        settings_dict = build_full_render_job(
            routing,
            media_uris=artifacts.media_uris,
            output_destination=destination,
            settings=settings,
            lut_uri=lut_uri,
            container=container,
        )
        return settings_dict, user_metadata

    if step.kind == "per_shot_lut":
        shot = _shot_by_index(routing, step.shot_index)
        lut_uri = _lut_uri_for_shot(shot, artifacts)
        if lut_uri is None:
            raise ValueError(f"Shot {shot.index} is missing a LUT assignment.")
        destination = _s3_destination(bucket, step.output_key or "")
        settings_dict = build_per_shot_lut_job(
            shot,
            media_uri=artifacts.media_uris[shot.media_url],
            lut_uri=lut_uri,
            output_destination=destination,
            settings=settings,
            container=container,
        )
        return settings_dict, user_metadata

    if step.kind == "truncation":
        shot = _shot_by_index(routing, step.shot_index)
        destination = _s3_destination(bucket, step.output_key or "")
        settings_dict = build_truncation_job(
            shot,
            media_uri=artifacts.media_uris[shot.media_url],
            output_destination=destination,
            settings=settings,
            container=container,
        )
        return settings_dict, user_metadata

    if step.kind == "stitch":
        parts = part_steps or []
        part_uris = []
        for part in parts:
            if part.output_key is None:
                raise ValueError(f"Part step {part.id} is missing an output key.")
            part_uris.append(_output_uri(bucket, part.output_key, container=container))
        destination = _s3_destination(
            bucket,
            resolve_output_object_key(job.output_path),
        )
        settings_dict = build_stitch_job(
            part_uris,
            output_destination=destination,
            settings=settings,
            container=container,
        )
        return settings_dict, user_metadata

    raise ValueError(f"Step kind {step.kind!r} cannot be submitted to MediaConvert.")


def _emit_lambda_shot_event(
    job: RenderJobStatus,
    *,
    routing_shot: ShotRouting,
    artifacts: CloudArtifacts,
    eventbridge: EventBridgeClient,
    bucket: str,
    artifact_prefix: str,
) -> None:
    lut_keys = {
        artifact["media_url"]: artifacts.lut_keys[artifact["lut_id"]]
        for artifact in artifacts.clip_lut_artifacts
        if artifact.get("lut_id") in artifacts.lut_keys
    }
    detail = {
        "job_id": job.id,
        "bucket": bucket,
        "shot_index": routing_shot.index,
        "media_url": routing_shot.media_url,
        "timeline_offset_seconds": routing_shot.timeline_offset_seconds,
        "source_in_seconds": routing_shot.source_in_seconds,
        "source_out_seconds": routing_shot.source_out_seconds,
        "reasons": list(routing_shot.reasons),
        "proxy_key": _artifact_object_key(
            working_proxy_key(job.id, routing_shot.index, artifact_prefix=artifact_prefix),
            artifact_prefix,
        ),
        "otio_key": _artifact_object_key(artifacts.input_key, artifact_prefix),
        "lut_keys": {
            media_url: _artifact_object_key(lut_key, artifact_prefix)
            for media_url, lut_key in lut_keys.items()
        },
        "output_key": _artifact_object_key(
            shot_output_key(job.id, routing_shot.index, artifact_prefix=artifact_prefix),
            artifact_prefix,
        ),
        "settings": job.job.settings.model_dump()
        if hasattr(job.job.settings, "model_dump")
        else job.job.settings.dict(),
    }
    eventbridge.put_event("BaseRender Lambda Shot", detail)


def _submit_runnable_steps(
    steps: list[RenderStep],
    *,
    job_id: str,
    job: RenderJobCreate,
    routing: RoutingPlan,
    artifacts: CloudArtifacts,
    settings: RenderSettings,
    container: str,
    mediaconvert: MediaConvertClient,
    bucket: str,
    artifact_prefix: str,
) -> list[RenderStep]:
    completed = {step.id for step in steps if step.status == "succeeded"}
    updated = list(steps)
    for index, step in enumerate(updated):
        if step.status != "pending":
            continue
        if any(dep not in completed for dep in step.depends_on):
            continue
        if step.kind == "lambda_shot":
            continue
        submitted = _submit_step(
            step,
            job_id=job_id,
            job=job,
            routing=routing,
            artifacts=artifacts,
            settings=settings,
            container=container,
            mediaconvert=mediaconvert,
            bucket=bucket,
            artifact_prefix=artifact_prefix,
        )
        updated[index] = submitted
    return updated


def _all_part_steps_complete(steps: list[RenderStep]) -> bool:
    part_steps = _part_steps(steps)
    if not part_steps:
        return False
    stitch = _find_step_by_kind(steps, "stitch")
    if stitch is None:
        return False
    return all(step.status == "succeeded" for step in part_steps)


def _part_steps(steps: list[RenderStep]) -> list[RenderStep]:
    return [step for step in steps if step.kind in {"per_shot_lut", "lambda_shot"}]


def _is_terminal_success(steps: list[RenderStep], *, routing_route: str | None) -> bool:
    if routing_route == RouteKind.FULL_MEDIACONVERT.value:
        full = _find_step_by_kind(steps, "full")
        return full is not None and full.status == "succeeded"
    stitch = _find_step_by_kind(steps, "stitch")
    return stitch is not None and stitch.status == "succeeded"


def _output_for_terminal_step(
    job: RenderJobStatus,
    step: RenderStep,
    *,
    bucket: str,
) -> RenderOutput:
    container = _output_container(job.job)
    output_key = step.output_key or resolve_output_object_key(job.job.output_path)
    suffix = f".{container}"
    if not output_key.endswith(suffix):
        output_key = f"{output_key}{suffix}"
    return RenderOutput(
        path=s3_uri(bucket, output_key),
        key=output_key,
    )


def _artifacts_from_job(job: RenderJobStatus) -> CloudArtifacts:
    worker_payload = job.worker_payload
    artifacts = worker_payload.get("artifacts") or {}
    lut_entries = artifacts.get("luts") or []
    lut_keys = {entry["id"]: entry["key"] for entry in lut_entries if entry.get("id") and entry.get("key")}
    clip_lut_artifacts = worker_payload.get("clip_lut_artifacts") or []
    media_uris = worker_payload.get("media_uris") or {}
    lut_uris = worker_payload.get("lut_uris") or {}
    return CloudArtifacts(
        input_key=str(artifacts.get("input") or _input_key(job.id)),
        lut_keys=lut_keys,
        clip_lut_artifacts=clip_lut_artifacts,
        media_uris=media_uris,
        lut_uris=lut_uris,
    )


def _routing_from_job(job: RenderJobStatus) -> RoutingPlan:
    payload = job.worker_payload.get("routing") or {}
    shots = tuple(
        ShotRouting(
            index=int(shot["index"]),
            name=str(shot["name"]),
            media_url=str(shot["media_url"]),
            handler=ShotHandler(shot["handler"]),
            lut_path=shot.get("lut_path"),
            reasons=tuple(shot.get("reasons") or ()),
            timeline_offset_seconds=float(shot["timeline_offset_seconds"]),
            source_in_seconds=float(shot["source_in_seconds"]),
            source_out_seconds=float(shot["source_out_seconds"]),
        )
        for shot in payload.get("shots") or []
    )
    return RoutingPlan(
        route=RouteKind(payload["route"]),
        shots=shots,
        distinct_lut_count=int(payload.get("distinct_lut_count") or 0),
        requires_final_stitch=bool(payload.get("requires_final_stitch")),
    )


def routing_to_payload(routing: RoutingPlan) -> dict[str, Any]:
    return {
        "route": routing.route.value,
        "distinct_lut_count": routing.distinct_lut_count,
        "requires_final_stitch": routing.requires_final_stitch,
        "shots": [
            {
                "index": shot.index,
                "name": shot.name,
                "media_url": shot.media_url,
                "handler": shot.handler.value,
                "lut_path": shot.lut_path,
                "reasons": list(shot.reasons),
                "timeline_offset_seconds": shot.timeline_offset_seconds,
                "source_in_seconds": shot.source_in_seconds,
                "source_out_seconds": shot.source_out_seconds,
            }
            for shot in routing.shots
        ],
    }


def _find_step(steps: list[RenderStep], event: InternalRenderEvent) -> RenderStep | None:
    if event.step_id:
        match = _find_step_by_id(steps, event.step_id)
        if match is not None:
            return match
    if event.external_id:
        for step in steps:
            if step.external_id == event.external_id:
                return step
    if event.shot_index is not None:
        for step in steps:
            if step.shot_index != event.shot_index or step.status == "succeeded":
                continue
            if event.step_id is None and step.kind == "truncation":
                continue
            return step
    return None


def _find_step_by_id(steps: list[RenderStep], step_id: str) -> RenderStep | None:
    for step in steps:
        if step.id == step_id:
            return step
    return None


def _find_step_by_kind(
    steps: list[RenderStep],
    kind: str,
    shot_index: int | None = None,
) -> RenderStep | None:
    for step in steps:
        if step.kind != kind:
            continue
        if shot_index is not None and step.shot_index != shot_index:
            continue
        return step
    return None


def _shot_by_index(routing: RoutingPlan, shot_index: int | None) -> ShotRouting:
    if shot_index is None:
        raise ValueError("MediaConvert step is missing shot_index.")
    for shot in routing.shots:
        if shot.index == shot_index:
            return shot
    raise ValueError(f"Shot index {shot_index} was not found in routing plan.")


def _shot_from_step(job: RenderJobStatus, step: RenderStep) -> ShotRouting:
    routing = _routing_from_job(job)
    return _shot_by_index(routing, step.shot_index)


def _single_lut_uri(routing: RoutingPlan, artifacts: CloudArtifacts) -> str | None:
    lut_paths = {shot.lut_path for shot in routing.shots if shot.lut_path}
    if len(lut_paths) != 1:
        return None
    lut_path = next(iter(lut_paths))
    for shot in routing.shots:
        if shot.lut_path == lut_path:
            return _lut_uri_for_shot(shot, artifacts)
    return None


def _lut_uri_for_shot(shot: ShotRouting, artifacts: CloudArtifacts) -> str | None:
    for artifact in artifacts.clip_lut_artifacts:
        if artifact.get("normalized_url") != shot.media_url:
            continue
        lut_id = artifact.get("lut_id")
        if lut_id and lut_id in artifacts.lut_uris:
            return artifacts.lut_uris[lut_id]
    return None


def _clip_lut_artifacts(
    job: RenderJobCreate,
    lut_keys: dict[str, str],
) -> list[dict[str, Any]]:
    references_by_id = {
        str(reference.get("id")): reference
        for reference in job.media_references
        if reference.get("id") is not None
    }
    artifacts = []
    for reference_id, lut_id in job.lut_assignments.items():
        if lut_id == "none" or lut_id not in lut_keys:
            continue
        reference = references_by_id.get(reference_id)
        normalized_url = reference.get("normalized_url") if reference else None
        if normalized_url:
            artifacts.append(
                {
                    "normalized_url": normalized_url,
                    "media_url": normalized_url,
                    "lut_id": lut_id,
                }
            )
    return artifacts


def _copy_step(step: RenderStep, **updates: object) -> RenderStep:
    if hasattr(step, "model_copy"):
        return step.model_copy(update=updates)
    return step.copy(update=updates)


def _replace_step(steps: list[RenderStep], updated: RenderStep) -> list[RenderStep]:
    return [_copy_step(step) if step.id != updated.id else updated for step in steps]


def _copy_job(job: RenderJobStatus, **updates: object) -> RenderJobStatus:
    if hasattr(job, "model_copy"):
        return job.model_copy(update=updates)
    return job.copy(update=updates)


def _s3_destination(bucket: str, key_prefix: str) -> str:
    normalized = key_prefix.strip("/")
    if normalized.endswith((".mp4", ".mov")):
        normalized = normalized.rsplit(".", 1)[0]
    return s3_uri(bucket, normalized)


def _output_uri(bucket: str, key_prefix: str, *, container: str) -> str:
    normalized = key_prefix.strip("/")
    suffix = f".{container}"
    if not normalized.endswith(suffix):
        normalized = f"{normalized}{suffix}"
    return s3_uri(bucket, normalized)


def _input_key(job_id: str) -> str:
    return f"jobs/{job_id}/inputs/timeline.otio"


def _lut_key(job_id: str, lut_id: str) -> str:
    return f"jobs/{job_id}/inputs/luts/{lut_id}"


def _artifact_object_key(relative_key: str, artifact_prefix: str) -> str:
    normalized = relative_key.strip("/")
    prefix = artifact_prefix.strip("/")
    if prefix and not normalized.startswith(f"{prefix}/"):
        return f"{prefix}/{normalized}"
    return normalized


def _output_container(job: RenderJobCreate) -> str:
    output_path = job.output_path or "output.mp4"
    suffix = Path(output_path).suffix.lower().lstrip(".")
    return suffix or "mp4"
