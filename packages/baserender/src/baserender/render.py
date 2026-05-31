from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil

from baserender.ffmpeg_builder import FFmpegCommand, build_ffmpeg_command
from baserender.ffmpeg_progress import FfmpegProgress, run_ffmpeg_with_progress
from baserender.otio_reader import load_timeline_plan
from baserender.report import RenderReport
from baserender.timeline_model import BaseRenderError, RenderSettings


class FFmpegNotFoundError(BaseRenderError):
    """Raised when FFmpeg is not available on PATH."""


def render_otio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    settings: RenderSettings | None = None,
    track_index: int | None = None,
    dry_run: bool = False,
    overwrite: bool = True,
    fail_fast: bool = False,
    on_progress: Callable[[FfmpegProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[FFmpegCommand | None, RenderReport]:
    ensure_ffmpeg()
    settings = settings or RenderSettings()

    load_result = load_timeline_plan(
        input_path,
        track_index=track_index,
        settings=settings,
        fail_fast=fail_fast,
    )

    if load_result.plan is None:
        report = RenderReport.from_load_result(
            load_result,
            output_path=output_path,
            dry_run=dry_run,
        )
        return None, report

    command = build_ffmpeg_command(
        load_result.plan,
        output_path,
        settings=settings,
        overwrite=overwrite,
    )

    ffmpeg_shell = command.shell_string() if dry_run else None

    if not dry_run:
        run_ffmpeg_with_progress(
            command.args,
            duration_seconds=load_result.plan.duration_seconds,
            on_progress=on_progress,
            should_cancel=should_cancel,
            output_fps=settings.fps,
        )

    report = RenderReport.from_load_result(
        load_result,
        output_path=output_path,
        dry_run=dry_run,
        ffmpeg_shell=ffmpeg_shell,
    )
    return command, report


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise FFmpegNotFoundError(
            "ffmpeg was not found on PATH. Install it with `brew install ffmpeg`."
        )
