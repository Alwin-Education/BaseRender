from __future__ import annotations

import copy
from pathlib import Path

import opentimelineio as otio

from baserender.timeline_model import normalize_target_url

SOURCE_MATCH_TOLERANCE_SECONDS = 0.001


def prepare_shot_timeline(
    otio_path: str | Path,
    *,
    media_url: str,
    proxy_path: str | Path,
    source_in_seconds: float,
    dest_path: str | Path,
) -> Path:
    """Build a single-clip OTIO timeline pointing at a truncated local proxy."""
    timeline = otio.adapters.read_from_file(str(otio_path))
    clip = _find_source_clip(timeline, media_url=media_url, source_in_seconds=source_in_seconds)
    shot_clip = copy.deepcopy(clip)
    _retime_clip_to_proxy(shot_clip, proxy_path=proxy_path)
    output_timeline = _single_clip_timeline(timeline.name, shot_clip)
    destination = Path(dest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(output_timeline, str(destination))
    return destination


def _find_source_clip(
    timeline: otio.schema.Timeline,
    *,
    media_url: str,
    source_in_seconds: float,
) -> otio.schema.Clip:
    matches: list[otio.schema.Clip] = []
    for track in timeline.tracks:
        if track.kind != otio.schema.TrackKind.Video:
            continue
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            reference = item.media_reference
            if not isinstance(reference, otio.schema.ExternalReference):
                continue
            normalized = normalize_target_url(reference.target_url)
            if normalized != media_url:
                continue
            clip_start_seconds = _seconds_from_rational(item.source_range.start_time)
            if abs(clip_start_seconds - source_in_seconds) > SOURCE_MATCH_TOLERANCE_SECONDS:
                continue
            matches.append(item)

    if not matches:
        raise ValueError(
            f"No clip found for media_url={media_url!r} "
            f"source_in_seconds={source_in_seconds}."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple clips matched media_url={media_url!r} "
            f"source_in_seconds={source_in_seconds}."
        )
    return matches[0]


def _retime_clip_to_proxy(clip: otio.schema.Clip, *, proxy_path: str | Path) -> None:
    duration = clip.source_range.duration
    rate = duration.rate
    clip.source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, rate),
        duration=duration,
    )
    reference = clip.media_reference
    if isinstance(reference, otio.schema.ExternalReference):
        reference.target_url = str(proxy_path)


def _single_clip_timeline(name: str, clip: otio.schema.Clip) -> otio.schema.Timeline:
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    output_timeline = otio.schema.Timeline(name=f"{name} - {clip.name}")
    output_timeline.tracks.append(track)
    return output_timeline


def _seconds_from_rational(value: otio.opentime.RationalTime) -> float:
    return float(value.value) / float(value.rate)
