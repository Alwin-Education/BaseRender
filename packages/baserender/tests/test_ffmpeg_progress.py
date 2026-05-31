from __future__ import annotations

import io
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from baserender.ffmpeg_progress import (
    FfmpegCancelledError,
    FfmpegProgress,
    _ProgressMonitorState,
    _emit_progress,
    _parse_out_time_seconds,
    compute_progress,
    parse_progress_block,
    parse_progress_line,
    run_ffmpeg_with_progress,
)


def test_parse_out_time_seconds_ignores_na_values() -> None:
    assert _parse_out_time_seconds({"out_time_ms": "N/A"}) == 0.0
    assert _parse_out_time_seconds({"out_time": "N/A"}) == 0.0
    assert parse_progress_line("out_time_ms=4680000") == ("out_time_ms", "4680000")
    assert parse_progress_line("  progress=continue  ") == ("progress", "continue")
    assert parse_progress_line("") is None
    assert parse_progress_line("no-equals-sign") is None


def test_parse_out_time_seconds_prefers_timecode_over_microsecond_ms_field() -> None:
    block = {
        "out_time_us": "4320000",
        "out_time_ms": "4320000",
        "out_time": "00:00:04.320000",
    }
    assert _parse_out_time_seconds(block) == pytest.approx(4.32)


def test_parse_progress_block() -> None:
    block = parse_progress_block(
        "\n".join(
            [
                "frame=10",
                "fps=1.3",
                "out_time_ms=2000",
                "speed=0.5x",
                "progress=continue",
            ]
        )
    )
    assert block["frame"] == "10"
    assert block["progress"] == "continue"


def test_compute_progress_percent_and_eta_from_speed() -> None:
    percent, eta = compute_progress(
        out_time_seconds=4.68,
        duration_seconds=60.0,
        speed=0.0522,
        elapsed_seconds=89.6,
    )

    assert percent == pytest.approx(7.8, rel=0.01)
    remaining_seconds = 60.0 * (1.0 - percent / 100.0)
    assert eta == pytest.approx(remaining_seconds / 0.0522, rel=0.01)


def test_compute_progress_caps_at_99_before_finish() -> None:
    percent, _eta = compute_progress(
        out_time_seconds=60.0,
        duration_seconds=60.0,
        speed=1.0,
        elapsed_seconds=60.0,
        finished=False,
    )
    assert percent == 99.0


def test_compute_progress_finished_sets_full_percent() -> None:
    percent, eta = compute_progress(
        out_time_seconds=60.0,
        duration_seconds=60.0,
        speed=1.0,
        elapsed_seconds=60.0,
        finished=True,
    )
    assert percent == 100.0
    assert eta == 0.0


def test_compute_progress_blends_time_and_frame_when_aligned() -> None:
    percent, eta = compute_progress(
        out_time_seconds=10.0,
        duration_seconds=74.0,
        speed=5.4,
        elapsed_seconds=120.0,
        frame=147,
        output_fps=25.0,
        encoding_fps=5.4,
    )

    time_percent = 10.0 / 74.0 * 100.0
    frame_percent = 147 / (74.0 * 25.0) * 100.0
    assert percent == pytest.approx((time_percent + frame_percent) / 2.0, rel=0.01)
    remaining_seconds = 74.0 * (1.0 - percent / 100.0)
    assert eta == pytest.approx(remaining_seconds / 5.4, rel=0.01)


def test_compute_progress_weights_frames_when_out_time_runs_ahead() -> None:
    percent, eta = compute_progress(
        out_time_seconds=70.0,
        duration_seconds=74.0,
        speed=5.4,
        elapsed_seconds=120.0,
        frame=147,
        output_fps=25.0,
        encoding_fps=5.4,
    )

    time_percent = 70.0 / 74.0 * 100.0
    frame_percent = 147 / (74.0 * 25.0) * 100.0
    assert percent == pytest.approx((2.0 * frame_percent + time_percent) / 3.0, rel=0.01)
    assert 20.0 < percent < 50.0
    remaining_seconds = 74.0 * (1.0 - percent / 100.0)
    assert eta == pytest.approx(remaining_seconds / 5.4, rel=0.01)
    assert eta > 5.0


def test_compute_progress_falls_back_to_frame_when_out_time_unavailable() -> None:
    percent, _eta = compute_progress(
        out_time_seconds=0.0,
        duration_seconds=74.0,
        speed=5.4,
        elapsed_seconds=120.0,
        frame=147,
        output_fps=25.0,
        encoding_fps=5.4,
    )

    assert percent == pytest.approx(147 / (74.0 * 25.0) * 100.0, rel=0.01)


def test_compute_progress_falls_back_to_linear_eta() -> None:
    percent, eta = compute_progress(
        out_time_seconds=30.0,
        duration_seconds=60.0,
        speed=None,
        elapsed_seconds=100.0,
    )
    assert percent == 50.0
    assert eta == pytest.approx(100.0)


def test_run_ffmpeg_with_progress_emits_throttled_callbacks(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.txt"
    stderr = io.StringIO("warning line\n")

    def write_progress(block: list[str]) -> None:
        progress_path.write_text("\n".join(block) + "\n", encoding="utf-8")

    def fake_wait() -> int:
        write_progress(
            [
                "frame=10",
                "fps=1.3",
                "out_time_ms=2000",
                "speed=0.5x",
                "progress=continue",
            ]
        )
        time.sleep(0.05)
        write_progress(
            [
                "frame=20",
                "fps=1.4",
                "out_time_ms=4000",
                "speed=0.5x",
                "progress=continue",
            ]
        )
        time.sleep(0.05)
        write_progress(
            [
                "frame=30",
                "out_time_ms=6000",
                "speed=0.5x",
                "progress=end",
            ]
        )
        time.sleep(0.05)
        return 0

    process = MagicMock()
    process.stderr = stderr
    process.wait.side_effect = fake_wait

    callbacks: list[FfmpegProgress] = []

    def fake_popen(command: tuple[str, ...], **kwargs: object) -> MagicMock:
        assert "-progress" in command
        assert command[command.index("-progress") + 1] != "pipe:1"
        return process

    with patch("baserender.ffmpeg_progress.subprocess.Popen", side_effect=fake_popen):
        with patch("baserender.ffmpeg_progress.tempfile.NamedTemporaryFile") as fake_temp:
            fake_temp.return_value.__enter__.return_value.name = str(progress_path)
            run_ffmpeg_with_progress(
                ("ffmpeg", "-y"),
                duration_seconds=10.0,
                on_progress=callbacks.append,
                heartbeat_interval=0.0,
                poll_interval=0.01,
            )

    assert len(callbacks) >= 2
    assert callbacks[0].frame == 10
    assert callbacks[0].speed == pytest.approx(0.5)
    assert callbacks[-1].percent == 100.0
    assert callbacks[-1].eta_seconds == 0.0


def test_run_ffmpeg_with_progress_raises_on_non_zero_exit(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.txt"
    progress_path.write_text("progress=end\n", encoding="utf-8")
    stderr = io.StringIO("encode failed\n")

    process = MagicMock()
    process.stderr = stderr
    process.wait.return_value = 1

    with patch("baserender.ffmpeg_progress.subprocess.Popen", return_value=process):
        with patch("baserender.ffmpeg_progress.tempfile.NamedTemporaryFile") as fake_temp:
            fake_temp.return_value.__enter__.return_value.name = str(progress_path)
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                run_ffmpeg_with_progress(
                    ("ffmpeg", "-y"),
                    duration_seconds=10.0,
                )

    assert exc_info.value.returncode == 1
    assert "encode failed" in exc_info.value.output


def test_run_ffmpeg_with_progress_cancels_when_requested(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.txt"
    progress_path.write_text(
        "\n".join(
            [
                "frame=10",
                "out_time_ms=2000",
                "speed=0.5x",
                "progress=continue",
            ]
        ),
        encoding="utf-8",
    )
    stderr = io.StringIO()

    process = MagicMock()
    process.stderr = stderr
    process.poll.return_value = None
    process.wait.return_value = -15
    cancel_after_callback = {"done": False}

    def fake_wait(timeout: float | None = None) -> int:
        time.sleep(0.05)
        return -15

    process.wait.side_effect = fake_wait

    def should_cancel() -> bool:
        return cancel_after_callback["done"]

    def on_progress(_progress: FfmpegProgress) -> None:
        cancel_after_callback["done"] = True

    with patch("baserender.ffmpeg_progress.subprocess.Popen", return_value=process):
        with patch("baserender.ffmpeg_progress.tempfile.NamedTemporaryFile") as fake_temp:
            fake_temp.return_value.__enter__.return_value.name = str(progress_path)
            with pytest.raises(FfmpegCancelledError):
                run_ffmpeg_with_progress(
                    ("ffmpeg", "-y"),
                    duration_seconds=10.0,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                    heartbeat_interval=0.0,
                    poll_interval=0.01,
                )

    process.terminate.assert_called_once()


def test_run_ffmpeg_with_progress_retries_throttled_signature(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.txt"
    stderr = io.StringIO("")

    def write_progress(block: list[str]) -> None:
        progress_path.write_text("\n".join(block) + "\n", encoding="utf-8")

    def fake_wait() -> int:
        write_progress(
            [
                "frame=10",
                "fps=1.3",
                "out_time_ms=2000",
                "speed=0.5x",
                "progress=continue",
            ]
        )
        time.sleep(0.02)
        write_progress(
            [
                "frame=20",
                "fps=1.4",
                "out_time_ms=4000",
                "speed=0.5x",
                "progress=continue",
            ]
        )
        time.sleep(0.15)
        write_progress(
            [
                "frame=30",
                "out_time_ms=6000",
                "speed=0.5x",
                "progress=end",
            ]
        )
        return 0

    process = MagicMock()
    process.stderr = stderr
    process.wait.side_effect = fake_wait

    callbacks: list[FfmpegProgress] = []

    with patch("baserender.ffmpeg_progress.subprocess.Popen", return_value=process):
        with patch("baserender.ffmpeg_progress.tempfile.NamedTemporaryFile") as fake_temp:
            fake_temp.return_value.__enter__.return_value.name = str(progress_path)
            run_ffmpeg_with_progress(
                ("ffmpeg", "-y"),
                duration_seconds=10.0,
                on_progress=callbacks.append,
                heartbeat_interval=0.1,
                poll_interval=0.01,
            )

    frames = [callback.frame for callback in callbacks]
    assert 10 in frames
    assert 20 in frames


def test_emit_progress_never_regresses_percent() -> None:
    started_at = time.monotonic()
    monitor_state = _ProgressMonitorState(
        started_at=started_at,
        last_emit_at=0.0,
        last_signature="",
        max_percent_seen=0.0,
    )
    callbacks: list[FfmpegProgress] = []

    _emit_progress(
        {
            "out_time_ms": "20000",
            "frame": "100",
            "fps": "25.0",
            "speed": "1.0x",
            "progress": "continue",
        },
        duration_seconds=100.0,
        on_progress=callbacks.append,
        heartbeat_interval=0.0,
        finished=False,
        monitor_state=monitor_state,
        output_fps=25.0,
    )
    _emit_progress(
        {
            "out_time_ms": "2000",
            "frame": "10",
            "fps": "25.0",
            "speed": "1.0x",
            "progress": "continue",
        },
        duration_seconds=100.0,
        on_progress=callbacks.append,
        heartbeat_interval=0.0,
        finished=False,
        monitor_state=monitor_state,
        output_fps=25.0,
    )

    assert callbacks[0].percent == pytest.approx(9.33, rel=0.02)
    assert callbacks[1].percent >= callbacks[0].percent
