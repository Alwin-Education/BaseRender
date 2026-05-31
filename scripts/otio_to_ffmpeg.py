#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "packages" / "baserender" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from baserender.render import render_otio  # noqa: E402
from baserender.report import RenderReport  # noqa: E402
from baserender.timeline_model import (  # noqa: E402
    BaseRenderError,
    RenderSettings,
    parse_clip_lut_mappings,
)


def _write_stderr_summary(report: RenderReport) -> None:
    for issue in report.issues:
        prefix = "Error" if issue.severity == "error" else "Warning"
        print(f"{prefix}: {issue.message}", file=sys.stderr)

    if report.dry_run and report.ffmpeg_shell is not None:
        print("Dry run: FFmpeg command is in the JSON report (ffmpeg_shell).", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a basic OTIO audio/video timeline with FFmpeg."
    )
    parser.add_argument("input", help="Path to an .otio timeline.")
    parser.add_argument("output", help="Path to the output video file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the FFmpeg command without rendering; command is in the JSON report.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on the first unsupported timeline feature instead of reporting and continuing.",
    )
    parser.add_argument(
        "--track-index",
        type=int,
        default=None,
        help="Use a specific video track index instead of the first video track.",
    )
    parser.add_argument("--width", type=int, default=None, help="Output width for gaps.")
    parser.add_argument("--height", type=int, default=None, help="Output height for gaps.")
    parser.add_argument("--fps", type=float, default=None, help="Output FPS for gaps.")
    parser.add_argument(
        "--audio-sample-rate",
        type=int,
        default=48000,
        help="Sample rate for generated silence and normalized audio.",
    )
    parser.add_argument(
        "--audio-channel-layout",
        default="stereo",
        help="Channel layout for generated silence and normalized audio.",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not pass -y to FFmpeg.",
    )
    parser.add_argument(
        "--clip-lut",
        action="append",
        default=[],
        metavar="SOURCE=LUT",
        help=(
            "Apply a 3D LUT to clips whose normalized source URL matches SOURCE. "
            "Repeat for multiple mappings, for example "
            "'--clip-lut /media/a.mov=/looks/a.cube'."
        ),
    )
    args = parser.parse_args()

    try:
        clip_luts = parse_clip_lut_mappings(args.clip_lut)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    settings = RenderSettings(
        width=args.width,
        height=args.height,
        fps=args.fps,
        audio_sample_rate=args.audio_sample_rate,
        audio_channel_layout=args.audio_channel_layout,
        clip_luts=clip_luts,
    )

    try:
        _command, report = render_otio(
            args.input,
            args.output,
            settings=settings,
            track_index=args.track_index,
            dry_run=args.dry_run,
            overwrite=not args.no_overwrite,
            fail_fast=args.fail_fast,
        )
    except subprocess.CalledProcessError as exc:
        print(f"FFmpeg failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode
    except BaseRenderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(report.to_json())
    _write_stderr_summary(report)

    if report.status == "error":
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
