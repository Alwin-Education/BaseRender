from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
import threading
import time


@dataclass(frozen=True)
class FfmpegProgress:
    out_time_seconds: float
    frame: int | None
    fps: float | None
    speed: float | None
    percent: float
    elapsed_seconds: float
    eta_seconds: float | None


class FfmpegCancelledError(RuntimeError):
    """Raised when an FFmpeg render is cancelled before completion."""


def parse_progress_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    return key, value


def parse_progress_block(content: str) -> dict[str, str]:
    block: dict[str, str] = {}
    for line in content.splitlines():
        parsed = parse_progress_line(line)
        if parsed is None:
            continue
        key, value = parsed
        block[key] = value
    return block


def compute_progress(
    *,
    out_time_seconds: float,
    duration_seconds: float,
    speed: float | None,
    elapsed_seconds: float,
    frame: int | None = None,
    output_fps: float | None = None,
    encoding_fps: float | None = None,
    finished: bool = False,
) -> tuple[float, float | None]:
    if finished:
        return 100.0, 0.0

    time_percent = 0.0
    if duration_seconds > 0:
        time_percent = (out_time_seconds / duration_seconds) * 100.0

    frame_percent: float | None = None
    expected_frames = 0.0
    if (
        frame is not None
        and output_fps is not None
        and output_fps > 0
        and duration_seconds > 0
    ):
        expected_frames = duration_seconds * output_fps
        if expected_frames > 0:
            frame_percent = (frame / expected_frames) * 100.0

    if frame_percent is not None and out_time_seconds > 0 and duration_seconds > 0:
        percent = _combine_time_and_frame_percent(time_percent, frame_percent)
    elif frame_percent is not None:
        percent = frame_percent
    else:
        percent = time_percent

    percent = min(99.0, max(0.0, percent))
    remaining_seconds = duration_seconds * (1.0 - percent / 100.0)

    if speed is not None and speed > 0 and duration_seconds > 0:
        eta_seconds = remaining_seconds / speed
    elif (
        encoding_fps is not None
        and encoding_fps > 0
        and expected_frames > 0
    ):
        remaining_frames = expected_frames * (1.0 - percent / 100.0)
        eta_seconds = remaining_frames / encoding_fps
    elif percent > 0:
        eta_seconds = elapsed_seconds * ((100.0 / percent) - 1.0)
    else:
        eta_seconds = None

    return percent, eta_seconds


def _combine_time_and_frame_percent(
    time_percent: float,
    frame_percent: float,
) -> float:
    gap = time_percent - frame_percent
    if abs(gap) <= 15.0:
        return (time_percent + frame_percent) / 2.0
    if gap > 15.0:
        # out_time often runs ahead through filtergraphs; weight frames more.
        return (2.0 * frame_percent + time_percent) / 3.0
    return (frame_percent + 2.0 * time_percent) / 3.0


def run_ffmpeg_with_progress(
    args: tuple[str, ...],
    *,
    duration_seconds: float,
    on_progress: Callable[[FfmpegProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    heartbeat_interval: float = 1.0,
    poll_interval: float = 0.25,
    output_fps: float | None = None,
) -> None:
    with tempfile.NamedTemporaryFile(prefix="ffmpeg-progress-", suffix=".txt", delete=False) as handle:
        progress_path = handle.name

    command = (
        *args,
        "-progress",
        progress_path,
        "-nostats",
        "-loglevel",
        "warning",
    )
    process = subprocess.Popen(
        command,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stderr is None:
        raise RuntimeError("Failed to capture FFmpeg stderr.")

    stderr_lines: list[str] = []
    stop_event = threading.Event()
    started_at = time.monotonic()
    monitor_state = _ProgressMonitorState(
        started_at=started_at,
        last_emit_at=0.0,
        last_signature="",
        max_percent_seen=0.0,
    )

    def drain_stderr() -> None:
        for line in process.stderr:
            stderr_lines.append(line)
            if line.strip():
                print(line, end="", flush=True)

    def stop_process() -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def monitor_progress() -> None:
        while not stop_event.is_set():
            if should_cancel is not None and should_cancel():
                stop_process()
                return
            block = _read_progress_file(progress_path)
            progress_state = block.get("progress")
            if progress_state not in {"continue", "end"}:
                time.sleep(poll_interval)
                continue

            signature = "|".join(f"{key}={value}" for key, value in sorted(block.items()))
            finished = progress_state == "end"
            now = time.monotonic()
            signature_changed = signature != monitor_state.last_signature
            heartbeat_due = finished or (
                monitor_state.last_emit_at == 0.0
                or (now - monitor_state.last_emit_at) >= heartbeat_interval
            )

            if signature_changed or heartbeat_due:
                emitted = _emit_progress(
                    block,
                    duration_seconds=duration_seconds,
                    on_progress=on_progress,
                    heartbeat_interval=heartbeat_interval,
                    finished=finished,
                    monitor_state=monitor_state,
                    output_fps=output_fps,
                )
                if emitted:
                    monitor_state.last_signature = signature
            if finished:
                break
            time.sleep(poll_interval)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    progress_thread = threading.Thread(target=monitor_progress, daemon=True)
    stderr_thread.start()
    progress_thread.start()

    return_code = process.wait()
    stop_event.set()
    progress_thread.join(timeout=2.0)
    stderr_thread.join(timeout=1.0)
    Path(progress_path).unlink(missing_ok=True)

    if should_cancel is not None and should_cancel():
        raise FfmpegCancelledError("FFmpeg render was cancelled.")

    if return_code != 0:
        stderr_text = "".join(stderr_lines).strip()
        raise subprocess.CalledProcessError(return_code, command, output=stderr_text)


@dataclass
class _ProgressMonitorState:
    started_at: float
    last_emit_at: float
    last_signature: str
    max_percent_seen: float


def _read_progress_file(progress_path: str) -> dict[str, str]:
    try:
        content = Path(progress_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_progress_block(content)


def _emit_progress(
    block: dict[str, str],
    *,
    duration_seconds: float,
    on_progress: Callable[[FfmpegProgress], None] | None,
    heartbeat_interval: float,
    finished: bool,
    monitor_state: _ProgressMonitorState,
    output_fps: float | None = None,
) -> bool:
    if on_progress is None:
        return False

    now = time.monotonic()
    if not finished and (now - monitor_state.last_emit_at) < heartbeat_interval:
        return False

    out_time_seconds = _parse_out_time_seconds(block)
    frame = _parse_optional_int(block.get("frame"))
    fps = _parse_optional_float(block.get("fps"))
    speed = _parse_speed(block.get("speed"))
    elapsed_seconds = now - monitor_state.started_at
    raw_percent, eta_seconds = compute_progress(
        out_time_seconds=out_time_seconds,
        duration_seconds=duration_seconds,
        speed=speed,
        elapsed_seconds=elapsed_seconds,
        frame=frame,
        output_fps=output_fps,
        encoding_fps=fps,
        finished=finished,
    )
    percent = raw_percent
    if not finished:
        percent = max(percent, monitor_state.max_percent_seen)
        monitor_state.max_percent_seen = percent
        if percent > raw_percent and duration_seconds > 0:
            remaining_seconds = duration_seconds * (1.0 - percent / 100.0)
            if speed is not None and speed > 0:
                eta_seconds = remaining_seconds / speed
            elif (
                fps is not None
                and fps > 0
                and output_fps is not None
                and output_fps > 0
            ):
                expected_frames = duration_seconds * output_fps
                remaining_frames = expected_frames * (1.0 - percent / 100.0)
                eta_seconds = remaining_frames / fps
    try:
        on_progress(
            FfmpegProgress(
                out_time_seconds=out_time_seconds,
                frame=frame,
                fps=fps,
                speed=speed,
                percent=percent,
                elapsed_seconds=elapsed_seconds,
                eta_seconds=eta_seconds,
            )
        )
    except Exception:
        # Progress callbacks must not crash the monitor thread.
        pass
    monitor_state.last_emit_at = now
    return True


def _parse_out_time_seconds(block: dict[str, str]) -> float:
    out_time = block.get("out_time")
    if out_time and out_time.strip().lower() != "n/a":
        try:
            return _parse_timecode(out_time)
        except ValueError:
            pass
    if "out_time_us" in block:
        parsed = _parse_optional_int(block["out_time_us"])
        if parsed is not None:
            return max(0.0, parsed / 1_000_000.0)
    if "out_time_ms" in block:
        parsed = _parse_optional_int(block["out_time_ms"])
        if parsed is not None:
            # FFmpeg often writes microseconds to out_time_ms; large values are not ms.
            if parsed >= 100_000:
                return max(0.0, parsed / 1_000_000.0)
            return max(0.0, parsed / 1000.0)
    return 0.0


def _parse_timecode(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_speed(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip().lower().removesuffix("x")
    try:
        return float(normalized)
    except ValueError:
        return None
