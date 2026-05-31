from __future__ import annotations

from pathlib import Path

from baserender.timeline_model import (
    AudioClipSegment,
    AudioTimelineTrack,
    ClipSegment,
    TimelinePlan,
    VideoTimelineTrack,
)


def test_timeline_duration_uses_longer_audio_track() -> None:
    plan = TimelinePlan(
        name="Mixed Duration",
        source_path=Path("timeline.otio"),
        track_name="V1",
        segments=(ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=10),),
        video_tracks=(
            VideoTimelineTrack(
                "V1",
                (ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=10),),
            ),
        ),
        audio_tracks=(
            AudioTimelineTrack(
                "A1",
                (
                    AudioClipSegment(
                        "Music",
                        "/media/music.wav",
                        start_seconds=0,
                        duration_seconds=74,
                    ),
                ),
            ),
        ),
    )

    assert plan.duration_seconds == 74
