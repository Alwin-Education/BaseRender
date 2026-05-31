from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from baserender.ffmpeg_builder import FFmpegCommand
from baserender.ffmpeg_progress import FfmpegProgress
from baserender.render import render_otio
from baserender.report import RenderReport
from baserender.timeline_model import RenderSettings


RenderFn = Callable[..., tuple[FFmpegCommand | None, RenderReport]]


@dataclass(frozen=True)
class RenderJob:
    """Prepared render job consumed by the background worker."""

    input_path: str
    output_path: str
    settings: RenderSettings = field(default_factory=RenderSettings)
    track_index: int | None = None
    dry_run: bool = False
    overwrite: bool = True
    fail_fast: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> RenderJob:
        try:
            input_path = str(payload["input_path"])
            output_path = str(payload["output_path"])
        except KeyError as exc:
            raise ValueError(f"Missing required render job field: {exc.args[0]}") from exc

        settings_payload = payload.get("settings") or {}
        if not isinstance(settings_payload, Mapping):
            raise ValueError("Render job field 'settings' must be an object.")

        settings = RenderSettings(
            width=_optional_int(settings_payload.get("width")),
            height=_optional_int(settings_payload.get("height")),
            fps=_optional_float(settings_payload.get("fps")),
            audio_sample_rate=int(settings_payload.get("audio_sample_rate", 48000)),
            audio_channel_layout=str(settings_payload.get("audio_channel_layout", "stereo")),
            clip_luts=_string_mapping(settings_payload.get("clip_luts") or {}),
            video_codec=str(settings_payload.get("video_codec", "h264")),
            video_bitrate=_positive_int(
                settings_payload.get("video_bitrate"),
                default=8_000_000,
            ),
            video_encoder_preset=str(
                settings_payload.get("video_encoder_preset")
                or settings_payload.get("video_preset")
                or "faster"
            ),
            video_faststart=bool(settings_payload.get("video_faststart", True)),
            audio_codec=str(settings_payload.get("audio_codec", "aac")),
            audio_bitrate=_positive_int(
                settings_payload.get("audio_bitrate"),
                default=192_000,
            ),
            video_crf=_optional_int(settings_payload.get("video_crf")),
        )

        return cls(
            input_path=input_path,
            output_path=output_path,
            settings=settings,
            track_index=_optional_int(payload.get("track_index")),
            dry_run=bool(payload.get("dry_run", False)),
            overwrite=bool(payload.get("overwrite", True)),
            fail_fast=bool(payload.get("fail_fast", False)),
        )


def run_render_job(
    job: RenderJob,
    *,
    render_fn: RenderFn = render_otio,
    on_progress: Callable[[FfmpegProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _command, report = render_fn(
        job.input_path,
        job.output_path,
        settings=job.settings,
        track_index=job.track_index,
        dry_run=job.dry_run,
        overwrite=job.overwrite,
        fail_fast=job.fail_fast,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    return json.loads(report.to_json())


def load_job(path: str | Path | None) -> RenderJob:
    if path is None:
        payload = json.loads(input())
    else:
        payload = json.loads(Path(path).read_text())
    return RenderJob.from_mapping(payload)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _positive_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("Render settings field 'clip_luts' must be an object.")
    return {str(key): str(item) for key, item in value.items()}
