from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest

from baserender_lambda.timeline import prepare_shot_timeline


def _time_range(
    start_frames: int,
    duration_frames: int,
    *,
    rate: int = 24,
) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start_frames, rate),
        duration=otio.opentime.RationalTime(duration_frames, rate),
    )


def _external_clip(
    name: str,
    target_url: str,
    *,
    start_frames: int = 0,
    duration_frames: int = 72,
    rate: int = 24,
) -> otio.schema.Clip:
    return otio.schema.Clip(
        name=name,
        media_reference=otio.schema.ExternalReference(
            target_url=target_url,
            available_range=_time_range(0, max(start_frames + duration_frames, duration_frames), rate=rate),
        ),
        source_range=_time_range(start_frames, duration_frames, rate=rate),
    )


def _resolve_effect(name: str) -> otio.schema.Effect:
    return otio.schema.Effect(
        effect_name="Resolve Effect",
        metadata={
            "Resolve_OTIO": {
                "Effect Name": name,
                "Enabled": True,
                "Parameters": [],
            }
        },
    )


def test_prepare_shot_timeline_retimes_clip_to_proxy(tmp_path: Path) -> None:
    otio_path = tmp_path / "source.otio"
    proxy_path = tmp_path / "proxy.mp4"
    dest_path = tmp_path / "timeline.otio"
    proxy_path.write_bytes(b"proxy")

    timeline = otio.schema.Timeline(name="Hybrid Timeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    clip = _external_clip(
        "Shot A",
        "file:///Volumes/Raid/Shot_A.mov",
        start_frames=12,
        duration_frames=72,
    )
    clip.effects.append(_resolve_effect("Transform"))
    track.append(clip)
    timeline.tracks.append(track)
    otio.adapters.write_to_file(timeline, str(otio_path))

    prepare_shot_timeline(
        otio_path,
        media_url="/Volumes/Raid/Shot_A.mov",
        proxy_path=proxy_path,
        source_in_seconds=0.5,
        dest_path=dest_path,
    )

    output = otio.adapters.read_from_file(str(dest_path))
    assert len(output.tracks) == 1
    assert len(output.tracks[0]) == 1

    shot_clip = output.tracks[0][0]
    assert isinstance(shot_clip, otio.schema.Clip)
    assert shot_clip.source_range.start_time.value == 0
    assert shot_clip.source_range.duration.value == 72
    reference = shot_clip.media_reference
    assert isinstance(reference, otio.schema.ExternalReference)
    assert reference.target_url == str(proxy_path)
    assert len(shot_clip.effects) == 1


def test_prepare_shot_timeline_raises_when_clip_not_found(tmp_path: Path) -> None:
    otio_path = tmp_path / "source.otio"
    timeline = otio.schema.Timeline(name="Hybrid Timeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(_external_clip("Shot A", "file:///other.mov", start_frames=0))
    timeline.tracks.append(track)
    otio.adapters.write_to_file(timeline, str(otio_path))

    with pytest.raises(ValueError, match="No clip found"):
        prepare_shot_timeline(
            otio_path,
            media_url="/Volumes/Raid/Shot_A.mov",
            proxy_path=tmp_path / "proxy.mp4",
            source_in_seconds=0.5,
            dest_path=tmp_path / "timeline.otio",
        )
