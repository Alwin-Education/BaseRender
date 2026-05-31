from __future__ import annotations

import copy
from pathlib import Path

import opentimelineio as otio
import pytest

from baserender.otio_reader import load_timeline_plan
from baserender.timeline_model import (
    AudioClipSegment,
    AudioGapSegment,
    ClipCrop,
    ClipTransform,
    ClipSegment,
    DissolveAudioTransitionSegment,
    DissolveTransitionSegment,
    GapSegment,
    MediaReferenceError,
    RenderSettings,
    UnsupportedTimelineError,
)


def _resolve_effect(
    name: str,
    *,
    enabled: bool = True,
    parameters: list[dict] | None = None,
) -> otio.schema.Effect:
    return otio.schema.Effect(
        effect_name="Resolve Effect",
        metadata={
            "Resolve_OTIO": {
                "Effect Name": name,
                "Enabled": enabled,
                "Parameters": parameters or [],
            }
        },
    )


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
    duration_frames: int = 24,
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


def test_load_timeline_plan_uses_clip_trimmed_range(tmp_path: Path) -> None:
    otio_path = tmp_path / "timeline.otio"
    timeline = otio.schema.Timeline(name="Test Timeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/source.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(240, 24),
        ),
    )
    clip = otio.schema.Clip(
        name="Shot A",
        media_reference=media_reference,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(48, 24),
            duration=otio.opentime.RationalTime(72, 24),
        ),
    )
    track.append(clip)
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)
    plan = result.plan
    assert plan is not None
    assert plan.name == "Test Timeline"
    assert plan.track_name == "V1"
    assert len(plan.segments) == 1
    segment = plan.segments[0]
    assert isinstance(segment, ClipSegment)
    assert segment.media_url == "/tmp/source.mov"
    assert segment.start_seconds == 2
    assert segment.duration_seconds == 3
    assert len(plan.audio_tracks) == 1
    audio_segment = plan.audio_tracks[0].segments[0]
    assert isinstance(audio_segment, AudioClipSegment)
    assert audio_segment.media_url == "/tmp/source.mov"
    assert audio_segment.start_seconds == 2
    assert audio_segment.duration_seconds == 3
    assert result.issues == ()


def test_missing_media_reference_is_skipped_by_default(tmp_path: Path) -> None:
    otio_path = tmp_path / "missing.otio"
    timeline = otio.schema.Timeline(name="Missing Media")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Offline",
            media_reference=otio.schema.MissingReference(),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)
    assert result.plan is None
    assert result.items_skipped == 1
    assert any(issue.code == "invalid_media_reference" for issue in result.issues)
    assert any(issue.code == "empty_timeline" for issue in result.issues)


def test_missing_media_reference_fail_fast(tmp_path: Path) -> None:
    otio_path = tmp_path / "missing.otio"
    timeline = otio.schema.Timeline(name="Missing Media")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Offline",
            media_reference=otio.schema.MissingReference(),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    with pytest.raises(MediaReferenceError, match="ExternalReference"):
        load_timeline_plan(otio_path, fail_fast=True)


def test_unsupported_effect_reports_warning_and_keeps_clip(tmp_path: Path) -> None:
    otio_path = tmp_path / "fx.otio"
    timeline = otio.schema.Timeline(name="FX")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
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
            effects=[otio.schema.Effect(effect_name="Gaussian Blur")],
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)
    assert result.plan is not None
    assert len(result.plan.segments) == 1
    assert any(issue.code == "unsupported_effect" for issue in result.issues)


def test_static_resolve_transform_populates_clip_segment(tmp_path: Path) -> None:
    otio_path = tmp_path / "transform.otio"
    timeline = otio.schema.Timeline(name="Transform")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
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
            effects=[
                _resolve_effect(
                    "Transform",
                    parameters=[
                        {
                            "Default Parameter Value": 1.0,
                            "Key Frames": {},
                            "Parameter ID": "transformationZoomX",
                            "Parameter Value": 1.25,
                            "Variant Type": "Double",
                        },
                        {
                            "Default Parameter Value": 0.0,
                            "Key Frames": {},
                            "Parameter ID": "transformationPan",
                            "Parameter Value": -0.1,
                            "Variant Type": "Double",
                        },
                    ],
                )
            ],
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(
        otio_path,
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert result.issues == ()
    assert result.plan is not None
    segment = result.plan.segments[0]
    assert isinstance(segment, ClipSegment)
    assert segment.transform == ClipTransform(scale_x=1.25, translate_x=-192.0)


def test_static_resolve_transform_requires_output_shape(tmp_path: Path) -> None:
    otio_path = tmp_path / "transform.otio"
    timeline = otio.schema.Timeline(name="Transform")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
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
            effects=[
                _resolve_effect(
                    "Transform",
                    parameters=[
                        {
                            "Default Parameter Value": 1.0,
                            "Key Frames": {},
                            "Parameter ID": "transformationZoomX",
                            "Parameter Value": 1.25,
                            "Variant Type": "Double",
                        }
                    ],
                )
            ],
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    segment = result.plan.segments[0]
    assert isinstance(segment, ClipSegment)
    assert segment.transform is None
    assert any(issue.code == "transform_missing_output_shape" for issue in result.issues)


def test_static_resolve_crop_populates_clip_segment(tmp_path: Path) -> None:
    otio_path = tmp_path / "crop.otio"
    timeline = otio.schema.Timeline(name="Crop")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
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
            effects=[
                _resolve_effect(
                    "Cropping",
                    parameters=[
                        {
                            "Default Parameter Value": 0.0,
                            "Key Frames": {},
                            "Parameter ID": "cropLeft",
                            "Parameter Value": 0.34,
                            "Variant Type": "Double",
                        }
                    ],
                )
            ],
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    segment = result.plan.segments[0]
    assert isinstance(segment, ClipSegment)
    assert segment.crop == ClipCrop(left=0.34)
    assert not any(issue.code == "unsupported_effect" for issue in result.issues)


def test_unsupported_effect_fail_fast(tmp_path: Path) -> None:
    otio_path = tmp_path / "fx.otio"
    timeline = otio.schema.Timeline(name="FX")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
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
            effects=[otio.schema.Effect(effect_name="Gaussian Blur")],
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    with pytest.raises(UnsupportedTimelineError, match="unsupported effects: Gaussian Blur"):
        load_timeline_plan(otio_path, fail_fast=True)


def test_dissolve_transition_expands_and_trims_clips(tmp_path: Path) -> None:
    otio_path = tmp_path / "transition.otio"
    timeline = otio.schema.Timeline(name="Transitions")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/a.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(48, 24),
        ),
    )
    track.append(
        otio.schema.Clip(
            name="A",
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    track.append(
        otio.schema.Transition(
            name="Dissolve",
            in_offset=otio.opentime.RationalTime(12, 24),
            out_offset=otio.opentime.RationalTime(12, 24),
            transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
        )
    )
    track.append(
        otio.schema.Clip(
            name="B",
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)
    assert result.plan is not None
    assert result.items_skipped == 0
    assert len(result.plan.segments) == 3

    head, dissolve, tail = result.plan.segments
    assert isinstance(head, ClipSegment)
    assert head.duration_seconds == 0.5
    assert isinstance(dissolve, DissolveTransitionSegment)
    assert dissolve.duration_seconds == 1.0
    assert dissolve.outgoing.duration_seconds == 1.0
    assert dissolve.incoming.duration_seconds == 1.0
    assert isinstance(tail, ClipSegment)
    assert tail.duration_seconds == 0.5
    assert result.plan.duration_seconds == 2.0


def test_unsupported_transition_type_is_skipped(tmp_path: Path) -> None:
    otio_path = tmp_path / "wipe.otio"
    timeline = otio.schema.Timeline(name="Transitions")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/a.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(48, 24),
        ),
    )
    clip = otio.schema.Clip(
        name="A",
        media_reference=media_reference,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(24, 24),
        ),
    )
    track.append(copy.deepcopy(clip))
    track.append(
        otio.schema.Transition(
            name="Wipe",
            in_offset=otio.opentime.RationalTime(12, 24),
            out_offset=otio.opentime.RationalTime(12, 24),
            transition_type=otio.schema.TransitionTypes.Custom,
        )
    )
    track.append(copy.deepcopy(clip))
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)
    assert result.plan is not None
    assert result.items_skipped == 1
    assert any(issue.code == "unsupported_transition_type" for issue in result.issues)


def test_linked_audio_track_skips_redundant_embedded_audio(tmp_path: Path) -> None:
    otio_path = tmp_path / "linked-audio.otio"
    timeline = otio.schema.Timeline(name="Linked Audio")
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline.tracks.extend([video_track, audio_track])

    clip = otio.schema.Clip(
        name="Video",
        media_reference=otio.schema.ExternalReference(
            target_url="file:///tmp/video.mov",
            available_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(48, 24),
            ),
        ),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(24, 24),
        ),
    )
    video_track.append(copy.deepcopy(clip))
    audio_track.append(copy.deepcopy(clip))
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    assert [track.name for track in result.plan.audio_tracks] == ["A1"]


def test_audio_tracks_are_parsed_and_gaps_become_silence(tmp_path: Path) -> None:
    otio_path = tmp_path / "audio.otio"
    timeline = otio.schema.Timeline(name="Audio")
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline.tracks.extend([video_track, audio_track])

    video_track.append(
        otio.schema.Clip(
            name="Video",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///tmp/video.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24),
                    duration=otio.opentime.RationalTime(48, 24),
                ),
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    audio_track.append(
        otio.schema.Clip(
            name="Music",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///tmp/music.wav",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 48),
                    duration=otio.opentime.RationalTime(96, 48),
                ),
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(24, 48),
                duration=otio.opentime.RationalTime(48, 48),
            ),
        )
    )
    audio_track.append(
        otio.schema.Gap(
            name="Silence",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 48),
                duration=otio.opentime.RationalTime(24, 48),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    assert [track.name for track in result.plan.audio_tracks] == ["V1 embedded audio", "A1"]
    music = result.plan.audio_tracks[1].segments[0]
    silence = result.plan.audio_tracks[1].segments[1]
    assert isinstance(music, AudioClipSegment)
    assert music.media_url == "/tmp/music.wav"
    assert music.start_seconds == 0.5
    assert music.duration_seconds == 1
    assert isinstance(silence, AudioGapSegment)
    assert silence.duration_seconds == 0.5


def test_audio_dissolve_transition_is_parsed(tmp_path: Path) -> None:
    otio_path = tmp_path / "audio-transition.otio"
    timeline = otio.schema.Timeline(name="Audio Transition")
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline.tracks.extend([video_track, audio_track])

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/source.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(48, 24),
        ),
    )
    clip = otio.schema.Clip(
        name="Clip",
        media_reference=media_reference,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(24, 24),
        ),
    )
    video_track.append(copy.deepcopy(clip))
    audio_track.append(copy.deepcopy(clip))
    audio_track.append(
        otio.schema.Transition(
            name="Crossfade",
            in_offset=otio.opentime.RationalTime(12, 24),
            out_offset=otio.opentime.RationalTime(12, 24),
            transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
        )
    )
    audio_track.append(copy.deepcopy(clip))
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    assert result.items_skipped == 0
    audio_segments = result.plan.audio_tracks[1].segments
    assert len(audio_segments) == 3
    assert isinstance(audio_segments[1], DissolveAudioTransitionSegment)
    assert audio_segments[1].duration_seconds == 1.0


def test_audio_transition_without_clips_fail_fast(tmp_path: Path) -> None:
    otio_path = tmp_path / "audio-transition.otio"
    timeline = otio.schema.Timeline(name="Audio Transition")
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    timeline.tracks.extend([video_track, audio_track])

    video_track.append(
        otio.schema.Clip(
            name="Video",
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
    audio_track.append(
        otio.schema.Transition(
            name="Crossfade",
            in_offset=otio.opentime.RationalTime(0, 24),
            out_offset=otio.opentime.RationalTime(0, 24),
            transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    with pytest.raises(UnsupportedTimelineError, match="must sit between two clips"):
        load_timeline_plan(otio_path, fail_fast=True)


def test_load_timeline_plan_loads_all_video_tracks_by_default(tmp_path: Path) -> None:
    otio_path = tmp_path / "multi-video.otio"
    timeline = otio.schema.Timeline(name="Multi Video")
    video_one = otio.schema.Track(name="Video 1", kind=otio.schema.TrackKind.Video)
    video_two = otio.schema.Track(name="Video 2", kind=otio.schema.TrackKind.Video)
    timeline.tracks.extend([video_one, video_two])

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/base.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(48, 24),
        ),
    )
    video_one.append(
        otio.schema.Clip(
            name="Base",
            media_reference=media_reference,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(24, 24),
            ),
        )
    )
    video_two.append(
        otio.schema.Gap(
            name="Overlay Gap",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(12, 24),
            ),
        )
    )
    video_two.append(
        otio.schema.Clip(
            name="Overlay",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///tmp/overlay.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24),
                    duration=otio.opentime.RationalTime(48, 24),
                ),
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(12, 24),
            ),
        )
    )
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(
        otio_path,
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert result.plan is not None
    assert [track.name for track in result.plan.video_tracks] == ["Video 1", "Video 2"]
    assert result.plan.track_name == "Video 1, Video 2"
    assert len(result.plan.video_tracks[0].segments) == 1
    assert len(result.plan.video_tracks[1].segments) == 2
    assert isinstance(result.plan.video_tracks[1].segments[0], GapSegment)
    assert result.plan.segments == result.plan.video_tracks[0].segments


def test_track_index_renders_single_video_track(tmp_path: Path) -> None:
    otio_path = tmp_path / "multi-video-index.otio"
    timeline = otio.schema.Timeline(name="Multi Video Index")
    video_one = otio.schema.Track(name="Video 1", kind=otio.schema.TrackKind.Video)
    video_two = otio.schema.Track(name="Video 2", kind=otio.schema.TrackKind.Video)
    timeline.tracks.extend([video_one, video_two])

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/base.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(24, 24),
        ),
    )
    for track in (video_one, video_two):
        track.append(
            otio.schema.Clip(
                name=f"{track.name} Clip",
                media_reference=media_reference,
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24),
                    duration=otio.opentime.RationalTime(24, 24),
                ),
            )
        )
    otio.adapters.write_to_file(timeline, str(otio_path))

    tracks = list(otio.adapters.read_from_file(str(otio_path)).tracks)
    video_two_index = next(index for index, track in enumerate(tracks) if track.name == "Video 2")

    result = load_timeline_plan(otio_path, track_index=video_two_index)

    assert result.plan is not None
    assert result.plan.track_name == "Video 2"
    assert len(result.plan.video_tracks) == 1
    assert result.plan.video_tracks[0].name == "Video 2"


def test_nested_stack_inside_track_is_aligned_and_trimmed(tmp_path: Path) -> None:
    otio_path = tmp_path / "nested-stack.otio"
    timeline = otio.schema.Timeline(name="Nested Stack")
    serial_track = otio.schema.Track(name="Main", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(serial_track)

    serial_track.append(_external_clip("Intro", "file:///tmp/intro.mov", duration_frames=24))

    lower = otio.schema.Track(name="Stack Lower", kind=otio.schema.TrackKind.Video)
    lower.append(
        _external_clip(
            "Lower Long",
            "file:///tmp/lower.mov",
            start_frames=48,
            duration_frames=72,
        )
    )
    upper = otio.schema.Track(name="Stack Upper", kind=otio.schema.TrackKind.Video)
    upper.append(
        _external_clip(
            "Upper Long",
            "file:///tmp/upper.mov",
            start_frames=120,
            duration_frames=72,
        )
    )
    nested_stack = otio.schema.Stack(
        name="Trimmed Stack",
        children=[lower, upper],
        source_range=_time_range(24, 24),
    )
    serial_track.append(nested_stack)
    serial_track.append(_external_clip("Outro", "file:///tmp/outro.mov", duration_frames=24))
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(
        otio_path,
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert result.issues == ()
    assert result.plan is not None
    assert [track.name for track in result.plan.video_tracks] == ["Main", "Stack Upper"]
    assert [segment.duration_seconds for segment in result.plan.video_tracks[0].segments] == [
        1,
        1,
        1,
    ]
    assert [type(segment) for segment in result.plan.video_tracks[1].segments] == [
        GapSegment,
        ClipSegment,
        GapSegment,
    ]
    lower_clip = result.plan.video_tracks[0].segments[1]
    upper_clip = result.plan.video_tracks[1].segments[1]
    assert isinstance(lower_clip, ClipSegment)
    assert isinstance(upper_clip, ClipSegment)
    assert lower_clip.start_seconds == 3
    assert lower_clip.duration_seconds == 1
    assert upper_clip.start_seconds == 6
    assert upper_clip.duration_seconds == 1


def test_track_index_can_select_top_level_stack(tmp_path: Path) -> None:
    otio_path = tmp_path / "selected-stack.otio"
    timeline = otio.schema.Timeline(name="Selected Stack")
    lower = otio.schema.Track(name="Lower", kind=otio.schema.TrackKind.Video)
    upper = otio.schema.Track(name="Upper", kind=otio.schema.TrackKind.Video)
    lower.append(_external_clip("Lower Clip", "file:///tmp/lower.mov"))
    upper.append(_external_clip("Upper Clip", "file:///tmp/upper.mov"))
    timeline.tracks.append(otio.schema.Stack(name="Composite", children=[lower, upper]))
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path, track_index=0)

    assert result.plan is not None
    assert [track.name for track in result.plan.video_tracks] == ["Lower", "Upper"]
    assert result.plan.track_name == "Lower, Upper"


def test_top_level_audio_stack_layers_are_mixed_with_alignment(tmp_path: Path) -> None:
    otio_path = tmp_path / "audio-stack.otio"
    timeline = otio.schema.Timeline(name="Audio Stack")
    video = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    video.append(_external_clip("Picture", "file:///tmp/picture.mov", duration_frames=48))
    timeline.tracks.append(video)

    music = otio.schema.Track(name="Music", kind=otio.schema.TrackKind.Audio)
    music.append(_external_clip("Music Clip", "file:///tmp/music.wav", duration_frames=48))
    effects = otio.schema.Track(name="Effects", kind=otio.schema.TrackKind.Audio)
    effects.append(otio.schema.Gap(name="Effect Delay", source_range=_time_range(0, 24)))
    effects.append(_external_clip("Effect Clip", "file:///tmp/effect.wav", duration_frames=24))
    timeline.tracks.append(otio.schema.Stack(name="Audio Layers", children=[music, effects]))
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    assert [track.name for track in result.plan.audio_tracks] == [
        "V1 embedded audio",
        "Music",
        "Effects",
    ]
    effects_segments = result.plan.audio_tracks[2].segments
    assert isinstance(effects_segments[0], AudioGapSegment)
    assert isinstance(effects_segments[1], AudioClipSegment)
    assert effects_segments[0].duration_seconds == 1
    assert effects_segments[1].duration_seconds == 1
