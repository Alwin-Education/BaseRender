from __future__ import annotations

import json
from pathlib import Path

from baserender.report import (
    LoadTimelineResult,
    RenderReport,
    TimelineIssue,
    derive_status,
)
from baserender.timeline_model import AudioClipSegment, AudioTimelineTrack, ClipSegment, TimelinePlan


def test_derive_status() -> None:
    assert derive_status((), renderable=True) == "ok"
    assert (
        derive_status(
            (
                TimelineIssue(
                    code="unsupported_effect",
                    severity="warning",
                    message="warn",
                ),
            ),
            renderable=True,
        )
        == "warning"
    )
    assert (
        derive_status(
            (
                TimelineIssue(
                    code="empty_timeline",
                    severity="error",
                    message="err",
                ),
            ),
            renderable=False,
        )
        == "error"
    )


def test_render_report_to_json() -> None:
    plan = TimelinePlan(
        name="T",
        source_path=Path("in.otio"),
        track_name="V1",
        segments=(
            ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),
        ),
        audio_tracks=(
            AudioTimelineTrack(
                "A1",
                (
                    AudioClipSegment(
                        "A",
                        "/media/a.mov",
                        start_seconds=0,
                        duration_seconds=1,
                    ),
                ),
            ),
        ),
    )
    load_result = LoadTimelineResult(
        plan=plan,
        issues=(
            TimelineIssue(
                code="unsupported_effect",
                severity="warning",
                message="Clip 'A' has unsupported effects: Blur.",
                item_name="A",
                item_type="Clip",
            ),
        ),
        items_skipped=0,
    )
    report = RenderReport.from_load_result(
        load_result,
        output_path="out.mp4",
        dry_run=True,
        ffmpeg_shell="ffmpeg -y ...",
    )

    payload = json.loads(report.to_json())
    assert payload["status"] == "warning"
    assert payload["video"] == {"tracks_included": 1, "track_names": ["V1"]}
    assert payload["audio"] == {"tracks_included": 1, "track_names": ["A1"]}
    assert payload["segments"] == {"included": 1, "skipped": 0}
    assert payload["ffmpeg_shell"] == "ffmpeg -y ..."
    assert payload["issues"][0]["code"] == "unsupported_effect"
