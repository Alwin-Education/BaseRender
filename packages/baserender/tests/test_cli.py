from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import opentimelineio as otio


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cli_dry_run_emits_json_report(tmp_path: Path) -> None:
    otio_path = tmp_path / "timeline.otio"
    timeline = otio.schema.Timeline(name="CLI Test")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///tmp/source.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24),
                    duration=otio.opentime.RationalTime(24, 24),
                ),
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    script = REPO_ROOT / "scripts" / "otio_to_ffmpeg.py"
    output_path = tmp_path / "out.mp4"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(otio_path),
            str(output_path),
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert report["video"]["tracks_included"] == 1
    assert report["audio"]["tracks_included"] == 1
    assert report["ffmpeg_shell"] is not None
    assert "ffmpeg" in report["ffmpeg_shell"]
    assert "[outa]" in report["ffmpeg_shell"]


def test_cli_clip_lut_flag_applies_lut3d(tmp_path: Path) -> None:
    otio_path = tmp_path / "timeline.otio"
    timeline = otio.schema.Timeline(name="LUT CLI")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///tmp/source.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24),
                    duration=otio.opentime.RationalTime(24, 24),
                ),
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    script = REPO_ROOT / "scripts" / "otio_to_ffmpeg.py"
    output_path = tmp_path / "out.mp4"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(otio_path),
            str(output_path),
            "--dry-run",
            "--clip-lut",
            "/tmp/source.mov=/looks/cli.cube",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert "lut3d=file=/looks/cli.cube" in report["ffmpeg_shell"]


def test_cli_rejects_invalid_clip_lut_flag(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts" / "otio_to_ffmpeg.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "missing.otio",
            "out.mp4",
            "--clip-lut",
            "not-a-mapping",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "expected SOURCE=LUT" in result.stderr
