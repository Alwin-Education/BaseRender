from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest

from baserender.ffmpeg_builder import build_ffmpeg_command
from baserender.otio_reader import load_timeline_plan
from baserender.timeline_model import (
    ClipSegment,
    RenderSettings,
    TimelinePlan,
    parse_clip_lut_mapping,
    parse_clip_lut_mappings,
)


def test_parse_clip_lut_mapping() -> None:
    assert parse_clip_lut_mapping("/media/a.mov=/looks/a.cube") == (
        "/media/a.mov",
        "/looks/a.cube",
    )


def test_parse_clip_lut_mapping_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="expected SOURCE=LUT"):
        parse_clip_lut_mapping("missing-equals-sign")

    with pytest.raises(ValueError, match="non-empty"):
        parse_clip_lut_mapping("=only-lut.cube")

    with pytest.raises(ValueError, match="non-empty"):
        parse_clip_lut_mapping("/media/a.mov=")


def test_parse_clip_lut_mappings_last_mapping_wins() -> None:
    mappings = parse_clip_lut_mappings(
        [
            "/media/a.mov=/looks/old.cube",
            "/media/a.mov=/looks/new.cube",
        ]
    )
    assert mappings == {"/media/a.mov": "/looks/new.cube"}


def test_load_timeline_plan_applies_lut_by_normalized_source_url(tmp_path: Path) -> None:
    otio_path = tmp_path / "lut.otio"
    timeline = otio.schema.Timeline(name="LUT Timeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///media/a.mov",
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

    result = load_timeline_plan(
        otio_path,
        settings=RenderSettings(clip_luts={"/media/a.mov": "/looks/a.cube"}),
    )

    assert result.plan is not None
    segment = result.plan.segments[0]
    assert isinstance(segment, ClipSegment)
    assert segment.media_url == "/media/a.mov"
    assert segment.lut_path == "/looks/a.cube"


def test_load_timeline_plan_applies_lut_by_prepared_media_url(tmp_path: Path) -> None:
    presigned_url = "https://example.test/get?bucket=demo&key=Shot_A.mov"
    otio_path = tmp_path / "lut.otio"
    timeline = otio.schema.Timeline(name="LUT Timeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
            media_reference=otio.schema.ExternalReference(
                target_url=presigned_url,
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

    result = load_timeline_plan(
        otio_path,
        settings=RenderSettings(
            clip_luts={presigned_url: "/tmp/luts/look.cube"},
        ),
    )

    assert result.plan is not None
    segment = result.plan.segments[0]
    assert isinstance(segment, ClipSegment)
    assert segment.media_url == presigned_url
    assert segment.lut_path == "/tmp/luts/look.cube"


def test_build_ffmpeg_command_applies_lut3d_before_concat() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=2,
                lut_path="/looks/a.cube",
            ),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert (
        "[0:v]trim=start=0:duration=2,setpts=PTS-STARTPTS,"
        "lut3d=file=/looks/a.cube[v0]"
    ) in command.filter_complex


def test_build_ffmpeg_command_escapes_lut_path_special_characters() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=2,
                lut_path="/looks/look[v1]:1.cube",
            ),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert (
        "lut3d=file=/looks/look\\[v1\\]\\:1.cube"
    ) in command.filter_complex
