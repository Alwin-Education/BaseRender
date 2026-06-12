from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from baserender.ffmpeg_builder import FFmpegCommand
from baserender.render import render_otio
from baserender.report import RenderReport
from baserender.timeline_model import normalize_target_url

from baserender_lambda.events import LambdaShotEvent
from baserender_lambda.notify import emit_shot_complete_event
from baserender_lambda.s3_io import BotoS3Io, S3Io
from baserender_lambda.settings import output_container, parse_render_settings
from baserender_lambda.timeline import prepare_shot_timeline

RenderFn = Callable[..., tuple[FFmpegCommand | None, RenderReport]]


def lambda_handler(event: dict[str, Any], context: object | None = None) -> dict[str, Any]:
    """AWS Lambda entrypoint for one hybrid-route shot render."""
    _ = context
    shot_event = LambdaShotEvent.from_mapping(event)
    return handle_shot_event(shot_event)


def handle_shot_event(
    shot_event: LambdaShotEvent,
    *,
    s3_io: S3Io | None = None,
    render_fn: RenderFn = render_otio,
) -> dict[str, Any]:
    storage = s3_io or BotoS3Io(shot_event.bucket)
    workspace = Path(
        tempfile.mkdtemp(prefix=f"baserender-{shot_event.job_id}-{shot_event.shot_index}-")
    )
    try:
        return _run_shot_render(shot_event, storage, workspace, render_fn=render_fn)
    except Exception as exc:
        # Report the failure to the orchestrator; a swallowed crash would leave
        # the job stuck "running" until the stale timeout.
        logging.getLogger(__name__).exception(
            "Shot render failed (job %s, shot %s)", shot_event.job_id, shot_event.shot_index
        )
        emit_shot_complete_event(
            job_id=shot_event.job_id,
            shot_index=shot_event.shot_index,
            output_key="",
            status="failed",
            bucket=shot_event.bucket,
            error_message=f"Lambda shot render failed: {exc}",
        )
        return {
            "status": "error",
            "job_id": shot_event.job_id,
            "shot_index": shot_event.shot_index,
            "error": str(exc),
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _run_shot_render(
    shot_event: LambdaShotEvent,
    storage: S3Io,
    workspace: Path,
    *,
    render_fn: RenderFn,
) -> dict[str, Any]:
    container = output_container(shot_event.settings)
    proxy_path = workspace / f"proxy.{container}"
    otio_path = workspace / "source.otio"
    timeline_path = workspace / "timeline.otio"
    output_path = workspace / f"output.{container}"

    # MediaConvert appends the container extension to its destination prefix,
    # so the truncated proxy lands at proxy_key + ".<container>".
    storage.download(_output_object_key(shot_event.proxy_key, container=container), proxy_path)
    storage.download(shot_event.otio_key, otio_path)

    clip_luts = _download_luts(storage, shot_event, workspace)
    prepare_shot_timeline(
        otio_path,
        media_url=shot_event.media_url,
        proxy_path=proxy_path,
        source_in_seconds=shot_event.source_in_seconds,
        dest_path=timeline_path,
    )
    # The shot timeline's clip now points at the local proxy, and the renderer
    # matches clip_luts by that URL — re-key this shot's LUT accordingly.
    shot_lut = clip_luts.get(shot_event.media_url)
    if shot_lut is not None:
        clip_luts[normalize_target_url(str(proxy_path))] = shot_lut

    settings = parse_render_settings(shot_event.settings, clip_luts=clip_luts)
    _command, report = render_fn(
        timeline_path,
        output_path,
        settings=settings,
        overwrite=True,
    )
    report_dict = json.loads(report.to_json())

    output_object_key = _output_object_key(shot_event.output_key, container=container)
    storage.upload(output_path, output_object_key)

    render_status = str(report_dict.get("status", "error"))
    event_status = "succeeded" if render_status == "ok" else "failed"
    emit_shot_complete_event(
        job_id=shot_event.job_id,
        shot_index=shot_event.shot_index,
        output_key=output_object_key,
        status=event_status,
        bucket=shot_event.bucket,
    )

    return {
        "status": render_status,
        "job_id": shot_event.job_id,
        "shot_index": shot_event.shot_index,
        "output_key": output_object_key,
        "report": report_dict,
    }


def _download_luts(
    storage: S3Io,
    shot_event: LambdaShotEvent,
    workspace: Path,
) -> dict[str, str]:
    if not shot_event.lut_keys:
        return {}

    lut_dir = workspace / "luts"
    lut_dir.mkdir(exist_ok=True)
    clip_luts: dict[str, str] = {}
    for index, (media_url, lut_key) in enumerate(shot_event.lut_keys.items()):
        lut_path = lut_dir / f"lut-{index}.cube"
        storage.download(lut_key, lut_path)
        lut_path_text = str(lut_path)
        clip_luts[media_url] = lut_path_text
    return clip_luts


def _output_object_key(output_key: str, *, container: str) -> str:
    suffix = f".{container}"
    if output_key.endswith(suffix):
        return output_key
    return f"{output_key}{suffix}"
