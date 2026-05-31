from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from pathlib import Path

import opentimelineio as otio
from opentimelineio.algorithms import track_with_expanded_transitions
from opentimelineio.exceptions import TransitionFollowingATransitionError

from baserender.report import LoadTimelineResult, TimelineIssue
from baserender.resolve_effects import (
    effect_display_name,
    has_adjusted_resolve_composite_opacity,
    has_keyframed_resolve_effects,
    has_static_resolve_transform,
    needs_resolve_canvas,
    parse_resolve_clip_animation,
    parse_static_resolve_crop,
    parse_static_resolve_transform,
    unsupported_clip_effects,
)
from baserender.timeline_model import (
    AudioClipSegment,
    AudioGapSegment,
    AudioSegment,
    AudioTimelineTrack,
    ClipSegment,
    DissolveAudioTransitionSegment,
    DissolveTransitionSegment,
    GapSegment,
    MediaReferenceError,
    RenderSettings,
    TimelinePlan,
    TimelineSegment,
    UnsupportedTimelineError,
    VideoTimelineTrack,
    normalize_target_url,
)
from baserender.transitions import (
    dissolve_transition_issues,
    is_supported_dissolve,
    parse_dissolve_curve,
    transition_duration_seconds,
    unsupported_transition_type_issue,
)


def load_timeline_plan(
    path: str | Path,
    *,
    track_index: int | None = None,
    settings: RenderSettings | None = None,
    fail_fast: bool = False,
) -> LoadTimelineResult:
    source_path = Path(path)
    timeline = otio.adapters.read_from_file(str(source_path))
    settings = settings or RenderSettings()

    if not isinstance(timeline, otio.schema.Timeline):
        issue = TimelineIssue(
            code="invalid_timeline",
            severity="error",
            message=f"Expected an OTIO Timeline, got {type(timeline).__name__}.",
        )
        if fail_fast:
            raise UnsupportedTimelineError(issue.message)
        return LoadTimelineResult(plan=None, issues=(issue,))

    issues: list[TimelineIssue] = []
    items_skipped = 0

    try:
        if track_index is not None:
            video_composition = _select_video_composition(
                timeline,
                track_index=track_index,
                fail_fast=fail_fast,
            )
            video_layers, layer_issues, layer_skipped = _flatten_video_composition(
                video_composition,
                settings=settings,
                fail_fast=fail_fast,
            )
        else:
            video_layers, layer_issues, layer_skipped = _flatten_video_composition(
                timeline.tracks,
                settings=settings,
                fail_fast=fail_fast,
            )
            if not video_layers:
                message = "No video track found in timeline."
                if fail_fast:
                    raise UnsupportedTimelineError(message)
                return LoadTimelineResult(
                    plan=None,
                    issues=(
                        TimelineIssue(
                            code="no_video_track",
                            severity="error",
                            message=message,
                        ),
                    ),
                )
    except UnsupportedTimelineError:
        raise
    except _TrackSelectionError as exc:
        if fail_fast:
            raise UnsupportedTimelineError(exc.message) from exc
        return LoadTimelineResult(plan=None, issues=(exc.issue,))

    issues.extend(layer_issues)
    items_skipped += layer_skipped
    video_tracks = [
        VideoTimelineTrack(name=layer.name, segments=tuple(layer.segments))
        for layer in video_layers
        if layer.segments
    ]

    timeline_name = timeline.name or source_path.stem
    if not video_tracks:
        track_label = video_layers[0].name if len(video_layers) == 1 else "video tracks"
        empty_message = f"Track {track_label!r} does not contain renderable items."
        empty_issue = TimelineIssue(
            code="empty_timeline",
            severity="error",
            message=empty_message,
        )
        if fail_fast:
            raise UnsupportedTimelineError(empty_message)
        issues.append(empty_issue)
        return LoadTimelineResult(
            plan=None,
            issues=tuple(issues),
            items_skipped=items_skipped,
        )

    base_track = video_tracks[0]
    segments = base_track.segments
    if len(video_tracks) == 1:
        track_name = base_track.name
    else:
        track_name = ", ".join(track.name for track in video_tracks)

    audio_tracks: list[AudioTimelineTrack] = []

    embedded_audio = _embedded_audio_track(base_track.name, list(segments))

    audio_layers, audio_issues, skipped = _flatten_audio_composition(
        timeline.tracks,
        fail_fast=fail_fast,
    )
    issues.extend(audio_issues)
    items_skipped += skipped

    skip_embedded = False
    explicit_audio_tracks: list[AudioTimelineTrack] = []
    for audio_layer in audio_layers:
        if audio_layer.segments:
            explicit_track = AudioTimelineTrack(
                name=audio_layer.name,
                segments=tuple(audio_layer.segments),
            )
            explicit_audio_tracks.append(explicit_track)
            if embedded_audio is not None and _embedded_audio_matches_explicit_track(
                embedded_audio,
                explicit_track,
            ):
                skip_embedded = True

    if embedded_audio is not None and not skip_embedded:
        audio_tracks.append(embedded_audio)
    audio_tracks.extend(explicit_audio_tracks)

    plan = TimelinePlan(
        name=timeline_name,
        source_path=source_path,
        track_name=track_name,
        segments=segments,
        video_tracks=tuple(video_tracks),
        audio_tracks=tuple(audio_tracks),
    )
    return LoadTimelineResult(
        plan=plan,
        issues=tuple(issues),
        items_skipped=items_skipped,
    )


@dataclass
class _VideoLayer:
    name: str
    segments: list[TimelineSegment]

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)


@dataclass
class _AudioLayer:
    name: str
    segments: list[AudioSegment]

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)


def _flatten_video_composition(
    composition: otio.core.SerializableObject,
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[list[_VideoLayer], list[TimelineIssue], int]:
    if isinstance(composition, otio.schema.Track):
        layers, issues, skipped = _flatten_video_track(
            composition,
            fail_fast=fail_fast,
            settings=settings,
        )
    elif isinstance(composition, otio.schema.Stack):
        layers, issues, skipped = _flatten_video_stack(
            composition,
            settings=settings,
            fail_fast=fail_fast,
        )
    else:
        return [], [], 0

    start_seconds, duration_seconds = _trimmed_range_seconds(composition)
    if start_seconds > 0 or duration_seconds < _max_video_layer_duration(layers):
        layers = _slice_video_layers(
            layers,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
        )
    return layers, issues, skipped


def _flatten_video_stack(
    stack: otio.schema.Stack,
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[list[_VideoLayer], list[TimelineIssue], int]:
    layers: list[_VideoLayer] = []
    issues: list[TimelineIssue] = []
    items_skipped = 0

    for child in stack:
        if not _has_video_layers(child):
            continue
        child_layers, child_issues, child_skipped = _flatten_video_composition(
            child,
            settings=settings,
            fail_fast=fail_fast,
        )
        issues.extend(child_issues)
        items_skipped += child_skipped
        layers.extend(child_layers)

    return layers, issues, items_skipped


def _flatten_video_track(
    track: otio.schema.Track,
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[list[_VideoLayer], list[TimelineIssue], int]:
    if not _is_video_track(track) and not any(
        isinstance(item, otio.schema.Stack) and _has_video_layers(item) for item in track
    ):
        return [], [], 0

    layers: list[_VideoLayer] = []
    issues: list[TimelineIssue] = []
    items_skipped = 0
    pending_items: list[otio.core.SerializableObject] = []
    cursor_seconds = 0.0
    track_name = track.name or "video"

    def flush_pending() -> None:
        nonlocal cursor_seconds, items_skipped
        if not pending_items:
            return
        segments, chunk_issues, skipped = _video_segments_from_items(
            pending_items,
            track_name=track_name,
            settings=settings,
            fail_fast=fail_fast,
        )
        issues.extend(chunk_issues)
        items_skipped += skipped
        chunk_duration = sum(segment.duration_seconds for segment in segments)
        _append_video_serial_chunk(layers, track_name, segments, duration_seconds=chunk_duration)
        cursor_seconds += chunk_duration
        pending_items.clear()

    for item in track:
        if isinstance(item, otio.schema.Stack):
            flush_pending()
            stack_layers, stack_issues, stack_skipped = _flatten_video_composition(
                item,
                settings=settings,
                fail_fast=fail_fast,
            )
            issues.extend(stack_issues)
            items_skipped += stack_skipped
            stack_duration = _seconds(item.trimmed_range().duration)
            _append_video_parallel_chunk(
                layers,
                stack_layers,
                cursor_seconds=cursor_seconds,
                duration_seconds=stack_duration,
            )
            cursor_seconds += stack_duration
        elif _is_video_track(track):
            pending_items.append(item)

    flush_pending()
    if _is_video_track(track) and not layers:
        layers.append(_VideoLayer(track_name, []))
    return layers, issues, items_skipped


def _video_segments_from_items(
    items: list[otio.core.SerializableObject],
    *,
    track_name: str,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[list[TimelineSegment], list[TimelineIssue], int]:
    track = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Video)
    track.extend(copy.deepcopy(items))
    return _video_segments_from_track(
        track,
        settings=settings,
        fail_fast=fail_fast,
    )


def _append_video_serial_chunk(
    layers: list[_VideoLayer],
    layer_name: str,
    segments: list[TimelineSegment],
    *,
    duration_seconds: float,
) -> None:
    if duration_seconds <= 0:
        return
    if not layers:
        layers.append(_VideoLayer(layer_name, []))
    layers[0].segments.extend(segments)
    for layer in layers[1:]:
        layer.segments.append(GapSegment(name="stack gap", duration_seconds=duration_seconds))


def _append_video_parallel_chunk(
    layers: list[_VideoLayer],
    stack_layers: list[_VideoLayer],
    *,
    cursor_seconds: float,
    duration_seconds: float,
) -> None:
    if duration_seconds <= 0:
        return

    layer_count = max(len(layers), len(stack_layers))
    for index in range(layer_count):
        if index >= len(layers):
            leading_segments: list[TimelineSegment] = []
            if cursor_seconds > 0:
                leading_segments.append(
                    GapSegment(name="stack leading gap", duration_seconds=cursor_seconds)
                )
            layer_name = stack_layers[index].name if index < len(stack_layers) else "stack"
            layers.append(_VideoLayer(layer_name, leading_segments))

        if index < len(stack_layers):
            before_duration = layers[index].duration_seconds
            layers[index].segments.extend(stack_layers[index].segments)
            appended_duration = layers[index].duration_seconds - before_duration
            if appended_duration < duration_seconds:
                layers[index].segments.append(
                    GapSegment(
                        name="stack trailing gap",
                        duration_seconds=duration_seconds - appended_duration,
                    )
                )
        else:
            layers[index].segments.append(
                GapSegment(name="stack gap", duration_seconds=duration_seconds)
            )


def _slice_video_layers(
    layers: list[_VideoLayer],
    *,
    start_seconds: float,
    duration_seconds: float,
) -> list[_VideoLayer]:
    return [
        _VideoLayer(
            layer.name,
            _slice_video_segments(
                layer.segments,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            ),
        )
        for layer in layers
    ]


def _slice_video_segments(
    segments: list[TimelineSegment],
    *,
    start_seconds: float,
    duration_seconds: float,
) -> list[TimelineSegment]:
    sliced: list[TimelineSegment] = []
    end_seconds = start_seconds + duration_seconds
    cursor_seconds = 0.0

    for segment in segments:
        segment_end = cursor_seconds + segment.duration_seconds
        overlap_start = max(cursor_seconds, start_seconds)
        overlap_end = min(segment_end, end_seconds)
        overlap_duration = overlap_end - overlap_start
        if overlap_duration > 0:
            offset_seconds = overlap_start - cursor_seconds
            sliced_segment = _slice_video_segment(
                segment,
                offset_seconds=offset_seconds,
                duration_seconds=overlap_duration,
            )
            if sliced_segment is not None:
                sliced.append(sliced_segment)
        cursor_seconds = segment_end
        if cursor_seconds >= end_seconds:
            break

    return sliced


def _slice_video_segment(
    segment: TimelineSegment,
    *,
    offset_seconds: float,
    duration_seconds: float,
) -> TimelineSegment | None:
    if isinstance(segment, ClipSegment):
        return replace(
            segment,
            start_seconds=segment.start_seconds + offset_seconds,
            duration_seconds=duration_seconds,
        )

    if isinstance(segment, GapSegment):
        return replace(segment, duration_seconds=duration_seconds)

    if offset_seconds == 0 and duration_seconds == segment.duration_seconds:
        return segment

    return GapSegment(name=f"{segment.name} trimmed", duration_seconds=duration_seconds)


def _flatten_audio_composition(
    composition: otio.core.SerializableObject,
    *,
    fail_fast: bool,
) -> tuple[list[_AudioLayer], list[TimelineIssue], int]:
    if isinstance(composition, otio.schema.Track):
        layers, issues, skipped = _flatten_audio_track(composition, fail_fast=fail_fast)
    elif isinstance(composition, otio.schema.Stack):
        layers, issues, skipped = _flatten_audio_stack(composition, fail_fast=fail_fast)
    else:
        return [], [], 0

    start_seconds, duration_seconds = _trimmed_range_seconds(composition)
    if start_seconds > 0 or duration_seconds < _max_audio_layer_duration(layers):
        layers = _slice_audio_layers(
            layers,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
        )
    return layers, issues, skipped


def _flatten_audio_stack(
    stack: otio.schema.Stack,
    *,
    fail_fast: bool,
) -> tuple[list[_AudioLayer], list[TimelineIssue], int]:
    layers: list[_AudioLayer] = []
    issues: list[TimelineIssue] = []
    items_skipped = 0

    for child in stack:
        if not _has_audio_layers(child):
            continue
        child_layers, child_issues, child_skipped = _flatten_audio_composition(
            child,
            fail_fast=fail_fast,
        )
        issues.extend(child_issues)
        items_skipped += child_skipped
        layers.extend(child_layers)

    return layers, issues, items_skipped


def _flatten_audio_track(
    track: otio.schema.Track,
    *,
    fail_fast: bool,
) -> tuple[list[_AudioLayer], list[TimelineIssue], int]:
    if not _is_audio_track(track) and not any(
        isinstance(item, otio.schema.Stack) and _has_audio_layers(item) for item in track
    ):
        return [], [], 0

    layers: list[_AudioLayer] = []
    issues: list[TimelineIssue] = []
    items_skipped = 0
    pending_items: list[otio.core.SerializableObject] = []
    cursor_seconds = 0.0
    track_name = track.name or "audio"

    def flush_pending() -> None:
        nonlocal cursor_seconds, items_skipped
        if not pending_items:
            return
        segments, chunk_issues, skipped = _audio_segments_from_items(
            pending_items,
            track_name=track_name,
            fail_fast=fail_fast,
        )
        issues.extend(chunk_issues)
        items_skipped += skipped
        chunk_duration = sum(segment.duration_seconds for segment in segments)
        _append_audio_serial_chunk(layers, track_name, segments, duration_seconds=chunk_duration)
        cursor_seconds += chunk_duration
        pending_items.clear()

    for item in track:
        if isinstance(item, otio.schema.Stack):
            flush_pending()
            stack_layers, stack_issues, stack_skipped = _flatten_audio_composition(
                item,
                fail_fast=fail_fast,
            )
            issues.extend(stack_issues)
            items_skipped += stack_skipped
            stack_duration = _seconds(item.trimmed_range().duration)
            _append_audio_parallel_chunk(
                layers,
                stack_layers,
                cursor_seconds=cursor_seconds,
                duration_seconds=stack_duration,
            )
            cursor_seconds += stack_duration
        elif _is_audio_track(track):
            pending_items.append(item)
        else:
            cursor_seconds += _item_duration_seconds(item)

    flush_pending()
    return layers, issues, items_skipped


def _audio_segments_from_items(
    items: list[otio.core.SerializableObject],
    *,
    track_name: str,
    fail_fast: bool,
) -> tuple[list[AudioSegment], list[TimelineIssue], int]:
    track = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Audio)
    track.extend(copy.deepcopy(items))
    return _audio_segments_from_track(track, fail_fast=fail_fast)


def _append_audio_serial_chunk(
    layers: list[_AudioLayer],
    layer_name: str,
    segments: list[AudioSegment],
    *,
    duration_seconds: float,
) -> None:
    if duration_seconds <= 0:
        return
    if not layers:
        layers.append(_AudioLayer(layer_name, []))
    layers[0].segments.extend(segments)
    for layer in layers[1:]:
        layer.segments.append(AudioGapSegment(name="stack silence", duration_seconds=duration_seconds))


def _append_audio_parallel_chunk(
    layers: list[_AudioLayer],
    stack_layers: list[_AudioLayer],
    *,
    cursor_seconds: float,
    duration_seconds: float,
) -> None:
    if duration_seconds <= 0:
        return

    layer_count = max(len(layers), len(stack_layers))
    for index in range(layer_count):
        if index >= len(layers):
            leading_segments: list[AudioSegment] = []
            if cursor_seconds > 0:
                leading_segments.append(
                    AudioGapSegment(name="stack leading silence", duration_seconds=cursor_seconds)
                )
            layer_name = stack_layers[index].name if index < len(stack_layers) else "stack audio"
            layers.append(_AudioLayer(layer_name, leading_segments))

        if index < len(stack_layers):
            before_duration = layers[index].duration_seconds
            layers[index].segments.extend(stack_layers[index].segments)
            appended_duration = layers[index].duration_seconds - before_duration
            if appended_duration < duration_seconds:
                layers[index].segments.append(
                    AudioGapSegment(
                        name="stack trailing silence",
                        duration_seconds=duration_seconds - appended_duration,
                    )
                )
        else:
            layers[index].segments.append(
                AudioGapSegment(name="stack silence", duration_seconds=duration_seconds)
            )


def _slice_audio_layers(
    layers: list[_AudioLayer],
    *,
    start_seconds: float,
    duration_seconds: float,
) -> list[_AudioLayer]:
    return [
        _AudioLayer(
            layer.name,
            _slice_audio_segments(
                layer.segments,
                start_seconds=start_seconds,
                duration_seconds=duration_seconds,
            ),
        )
        for layer in layers
    ]


def _slice_audio_segments(
    segments: list[AudioSegment],
    *,
    start_seconds: float,
    duration_seconds: float,
) -> list[AudioSegment]:
    sliced: list[AudioSegment] = []
    end_seconds = start_seconds + duration_seconds
    cursor_seconds = 0.0

    for segment in segments:
        segment_end = cursor_seconds + segment.duration_seconds
        overlap_start = max(cursor_seconds, start_seconds)
        overlap_end = min(segment_end, end_seconds)
        overlap_duration = overlap_end - overlap_start
        if overlap_duration > 0:
            offset_seconds = overlap_start - cursor_seconds
            sliced_segment = _slice_audio_segment(
                segment,
                offset_seconds=offset_seconds,
                duration_seconds=overlap_duration,
            )
            if sliced_segment is not None:
                sliced.append(sliced_segment)
        cursor_seconds = segment_end
        if cursor_seconds >= end_seconds:
            break

    return sliced


def _slice_audio_segment(
    segment: AudioSegment,
    *,
    offset_seconds: float,
    duration_seconds: float,
) -> AudioSegment | None:
    if isinstance(segment, AudioClipSegment):
        return replace(
            segment,
            start_seconds=segment.start_seconds + offset_seconds,
            duration_seconds=duration_seconds,
        )

    if isinstance(segment, AudioGapSegment):
        return replace(segment, duration_seconds=duration_seconds)

    if offset_seconds == 0 and duration_seconds == segment.duration_seconds:
        return segment

    return AudioGapSegment(name=f"{segment.name} trimmed", duration_seconds=duration_seconds)


def _trimmed_range_seconds(
    item: otio.core.SerializableObject,
) -> tuple[float, float]:
    trimmed_range = item.trimmed_range()
    return _seconds(trimmed_range.start_time), _seconds(trimmed_range.duration)


def _item_duration_seconds(item: otio.core.SerializableObject) -> float:
    trimmed_range = getattr(item, "trimmed_range", None)
    if trimmed_range is None:
        return 0.0
    return _seconds(trimmed_range().duration)


def _max_video_layer_duration(layers: list[_VideoLayer]) -> float:
    return max((layer.duration_seconds for layer in layers), default=0.0)


def _max_audio_layer_duration(layers: list[_AudioLayer]) -> float:
    return max((layer.duration_seconds for layer in layers), default=0.0)


def _has_video_layers(item: otio.core.SerializableObject) -> bool:
    if isinstance(item, otio.schema.Track):
        return _is_video_track(item) or any(
            isinstance(child, otio.schema.Stack) and _has_video_layers(child) for child in item
        )
    if isinstance(item, otio.schema.Stack):
        return any(_has_video_layers(child) for child in item)
    if isinstance(item, otio.schema.Clip | otio.schema.Gap | otio.schema.Transition):
        return True
    return False


def _has_audio_layers(item: otio.core.SerializableObject) -> bool:
    if isinstance(item, otio.schema.Track):
        return _is_audio_track(item) or any(
            isinstance(child, otio.schema.Stack) and _has_audio_layers(child) for child in item
        )
    if isinstance(item, otio.schema.Stack):
        return any(_has_audio_layers(child) for child in item)
    if isinstance(item, otio.schema.Clip | otio.schema.Gap | otio.schema.Transition):
        return True
    return False


class _TrackSelectionError(Exception):
    def __init__(self, issue: TimelineIssue) -> None:
        self.issue = issue
        self.message = issue.message
        super().__init__(issue.message)


def _select_video_composition(
    timeline: otio.schema.Timeline,
    *,
    track_index: int | None,
    fail_fast: bool,
) -> otio.core.SerializableObject:
    tracks = list(timeline.tracks)
    if track_index is not None:
        try:
            composition = tracks[track_index]
        except IndexError as exc:
            message = f"Track index {track_index} is out of range for {len(tracks)} tracks."
            if fail_fast:
                raise UnsupportedTimelineError(message) from exc
            raise _TrackSelectionError(
                TimelineIssue(
                    code="invalid_track_index",
                    severity="error",
                    message=message,
                )
            ) from exc

        if not _has_video_layers(composition):
            message = f"Track index {track_index} is not a video track or stack."
            if fail_fast:
                raise UnsupportedTimelineError(message)
            raise _TrackSelectionError(
                TimelineIssue(
                    code="invalid_track_index",
                    severity="error",
                    message=message,
                )
            )
        return composition

    for composition in tracks:
        if _has_video_layers(composition):
            return composition

    message = "No video track found in timeline."
    if fail_fast:
        raise UnsupportedTimelineError(message)
    raise _TrackSelectionError(
        TimelineIssue(
            code="no_video_track",
            severity="error",
            message=message,
        )
    )


def _is_video_track(track: otio.schema.Track) -> bool:
    return str(track.kind).lower() == str(otio.schema.TrackKind.Video).lower()


def _video_tracks(timeline: otio.schema.Timeline) -> list[otio.schema.Track]:
    return [track for track in timeline.tracks if _is_video_track(track)]


def _video_segments_from_track(
    track: otio.schema.Track,
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[list[TimelineSegment], list[TimelineIssue], int]:
    segments: list[TimelineSegment] = []
    issues: list[TimelineIssue] = []
    items_skipped = 0

    try:
        expanded_track = track_with_expanded_transitions(track)
    except TransitionFollowingATransitionError as exc:
        message = str(exc)
        if fail_fast:
            raise UnsupportedTimelineError(message) from exc
        return [], [
            TimelineIssue(
                code="invalid_transition",
                severity="error",
                message=message,
            )
        ], 0

    for item in expanded_track:
        segment, item_issues, skipped = _segment_from_expanded_item(
            item,
            settings=settings,
            fail_fast=fail_fast,
        )
        issues.extend(item_issues)
        if skipped:
            items_skipped += 1
            continue
        if segment is not None:
            segments.append(segment)

    return segments, issues, items_skipped


def _audio_tracks(timeline: otio.schema.Timeline) -> list[otio.schema.Track]:
    return [track for track in timeline.tracks if _is_audio_track(track)]


def _is_audio_track(track: otio.schema.Track) -> bool:
    return str(track.kind).lower() == str(otio.schema.TrackKind.Audio).lower()


def _audio_segments_match(left: AudioSegment, right: AudioSegment) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, AudioClipSegment):
        if not isinstance(right, AudioClipSegment):
            return False
        return (
            left.media_url == right.media_url
            and left.start_seconds == right.start_seconds
            and left.duration_seconds == right.duration_seconds
        )
    if isinstance(left, AudioGapSegment):
        if not isinstance(right, AudioGapSegment):
            return False
        return left.duration_seconds == right.duration_seconds
    if isinstance(left, DissolveAudioTransitionSegment):
        if not isinstance(right, DissolveAudioTransitionSegment):
            return False
        return (
            left.duration_seconds == right.duration_seconds
            and _audio_segments_match(left.outgoing, right.outgoing)
            and _audio_segments_match(left.incoming, right.incoming)
        )
    return False


def _embedded_audio_matches_explicit_track(
    embedded: AudioTimelineTrack,
    explicit: AudioTimelineTrack,
) -> bool:
    if len(embedded.segments) != len(explicit.segments):
        return False
    return all(
        _audio_segments_match(left, right)
        for left, right in zip(embedded.segments, explicit.segments, strict=True)
    )


def _embedded_audio_track(
    video_track_name: str,
    segments: list[TimelineSegment],
) -> AudioTimelineTrack | None:
    audio_segments: list[AudioSegment] = []
    for segment in segments:
        if isinstance(segment, ClipSegment):
            audio_segments.append(
                AudioClipSegment(
                    name=segment.name,
                    media_url=segment.media_url,
                    start_seconds=segment.start_seconds,
                    duration_seconds=segment.duration_seconds,
                )
            )
        elif isinstance(segment, GapSegment):
            audio_segments.append(
                AudioGapSegment(
                    name=segment.name,
                    duration_seconds=segment.duration_seconds,
                )
            )
        elif isinstance(segment, DissolveTransitionSegment):
            audio_segments.append(
                DissolveAudioTransitionSegment(
                    name=segment.name,
                    duration_seconds=segment.duration_seconds,
                    outgoing=AudioClipSegment(
                        name=segment.outgoing.name,
                        media_url=segment.outgoing.media_url,
                        start_seconds=segment.outgoing.start_seconds,
                        duration_seconds=segment.outgoing.duration_seconds,
                    ),
                    incoming=AudioClipSegment(
                        name=segment.incoming.name,
                        media_url=segment.incoming.media_url,
                        start_seconds=segment.incoming.start_seconds,
                        duration_seconds=segment.incoming.duration_seconds,
                    ),
                )
            )

    if not audio_segments:
        return None

    return AudioTimelineTrack(
        name=f"{video_track_name} embedded audio",
        segments=tuple(audio_segments),
    )


def _segment_from_expanded_item(
    item: otio.core.SerializableObject | tuple[otio.core.SerializableObject, ...],
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[TimelineSegment | None, list[TimelineIssue], bool]:
    if isinstance(item, tuple) and len(item) == 3:
        return _dissolve_transition_segment(item, settings=settings, fail_fast=fail_fast)

    if isinstance(item, otio.schema.Clip):
        return _clip_segment(item, settings=settings, fail_fast=fail_fast)

    if isinstance(item, otio.schema.Gap):
        return _gap_segment(item, settings=settings, fail_fast=fail_fast)

    if isinstance(item, otio.schema.Transition):
        message = (
            f"Transition {item.name!r} was not expanded into adjacent clips. "
            "Check transition offsets and neighbors."
        )
        issue = TimelineIssue(
            code="unsupported_transition",
            severity="warning",
            message=message,
            item_name=item.name or "transition",
            item_type="Transition",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, [issue], True

    message = (
        f"Unsupported item {getattr(item, 'name', None)!r} of type {type(item).__name__} "
        "in video track."
    )
    issue = TimelineIssue(
        code="unsupported_item",
        severity="warning",
        message=message,
        item_name=getattr(item, "name", None) or "item",
        item_type=type(item).__name__,
    )
    if fail_fast:
        raise UnsupportedTimelineError(message)
    return None, [issue], True


def _dissolve_transition_segment(
    item: tuple[otio.core.SerializableObject, ...],
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[TimelineSegment | None, list[TimelineIssue], bool]:
    pre, transition, post = item
    if not isinstance(transition, otio.schema.Transition):
        message = "Expected a transition tuple from expanded OTIO track."
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, [
            TimelineIssue(
                code="unsupported_item",
                severity="warning",
                message=message,
                item_type="tuple",
            )
        ], True

    if not is_supported_dissolve(transition):
        issue = unsupported_transition_type_issue(transition)
        if fail_fast:
            raise UnsupportedTimelineError(issue.message)
        return None, [issue], True

    if not isinstance(pre, otio.schema.Clip) or not isinstance(post, otio.schema.Clip):
        message = (
            f"Transition {transition.name!r} must sit between two clips; "
            f"got {type(pre).__name__} and {type(post).__name__}."
        )
        issue = TimelineIssue(
            code="unsupported_transition",
            severity="warning",
            message=message,
            item_name=transition.name or "transition",
            item_type="Transition",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, [issue], True

    try:
        duration_seconds = transition_duration_seconds(transition)
    except ValueError as exc:
        message = str(exc)
        if fail_fast:
            raise UnsupportedTimelineError(message) from exc
        return None, [
            TimelineIssue(
                code="invalid_transition",
                severity="warning",
                message=message,
                item_name=transition.name or "transition",
                item_type="Transition",
            )
        ], True

    fps = settings.fps
    if fps is None and transition.in_offset is not None:
        fps = float(transition.in_offset.rate)

    dissolve_curve = parse_dissolve_curve(
        transition,
        duration_seconds=duration_seconds,
        fps=fps,
    )
    issues = dissolve_transition_issues(
        transition,
        duration_seconds=duration_seconds,
        fps=fps,
        dissolve_curve=dissolve_curve,
    )

    outgoing, outgoing_issues, outgoing_skipped = _clip_segment(
        pre,
        settings=settings,
        fail_fast=fail_fast,
    )
    if outgoing_skipped or outgoing is None:
        return None, issues + outgoing_issues, True

    incoming, incoming_issues, incoming_skipped = _clip_segment(
        post,
        settings=settings,
        fail_fast=fail_fast,
    )
    if incoming_skipped or incoming is None:
        return None, issues + outgoing_issues + incoming_issues, True

    segment = DissolveTransitionSegment(
        name=transition.name or "dissolve",
        duration_seconds=duration_seconds,
        outgoing=outgoing,
        incoming=incoming,
        dissolve_curve=dissolve_curve,
    )
    return segment, issues + outgoing_issues + incoming_issues, False


def _audio_segments_from_track(
    track: otio.schema.Track,
    *,
    fail_fast: bool,
) -> tuple[list[AudioSegment], list[TimelineIssue], int]:
    segments: list[AudioSegment] = []
    issues: list[TimelineIssue] = []
    items_skipped = 0

    try:
        expanded_track = track_with_expanded_transitions(track)
    except TransitionFollowingATransitionError as exc:
        if fail_fast:
            raise UnsupportedTimelineError(str(exc)) from exc
        return [], [
            TimelineIssue(
                code="invalid_transition",
                severity="error",
                message=str(exc),
            )
        ], 0

    for item in expanded_track:
        segment, item_issues, skipped = _audio_segment_from_expanded_item(
            item,
            fail_fast=fail_fast,
        )
        issues.extend(item_issues)
        if skipped:
            items_skipped += 1
            continue
        if segment is not None:
            segments.append(segment)

    return segments, issues, items_skipped


def _audio_segment_from_expanded_item(
    item: otio.core.SerializableObject | tuple[otio.core.SerializableObject, ...],
    *,
    fail_fast: bool,
) -> tuple[AudioSegment | None, list[TimelineIssue], bool]:
    if isinstance(item, tuple) and len(item) == 3:
        return _audio_dissolve_transition_segment(item, fail_fast=fail_fast)

    if isinstance(item, otio.schema.Clip):
        segment, issues, skipped = _clip_segment(
            item,
            settings=RenderSettings(),
            fail_fast=fail_fast,
        )
        if segment is None:
            return None, issues, skipped
        return (
            AudioClipSegment(
                name=segment.name,
                media_url=segment.media_url,
                start_seconds=segment.start_seconds,
                duration_seconds=segment.duration_seconds,
            ),
            issues,
            skipped,
        )

    if isinstance(item, otio.schema.Gap):
        segment = AudioGapSegment(
            name=item.name or "gap",
            duration_seconds=_seconds(item.trimmed_range().duration),
        )
        return segment, [], False

    if isinstance(item, otio.schema.Transition):
        message = (
            f"Audio transition {item.name!r} was not expanded into adjacent clips. "
            "Check transition offsets and neighbors."
        )
        issue = TimelineIssue(
            code="unsupported_transition",
            severity="warning",
            message=message,
            item_name=item.name or "transition",
            item_type="Transition",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, [issue], True

    message = (
        f"Unsupported item {getattr(item, 'name', None)!r} of type {type(item).__name__} "
        "in audio track."
    )
    issue = TimelineIssue(
        code="unsupported_item",
        severity="warning",
        message=message,
        item_name=getattr(item, "name", None) or "item",
        item_type=type(item).__name__,
    )
    if fail_fast:
        raise UnsupportedTimelineError(message)
    return None, [issue], True


def _audio_dissolve_transition_segment(
    item: tuple[otio.core.SerializableObject, ...],
    *,
    fail_fast: bool,
) -> tuple[AudioSegment | None, list[TimelineIssue], bool]:
    pre, transition, post = item
    if not isinstance(transition, otio.schema.Transition):
        message = "Expected a transition tuple from expanded OTIO track."
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, [
            TimelineIssue(
                code="unsupported_item",
                severity="warning",
                message=message,
                item_type="tuple",
            )
        ], True

    if not is_supported_dissolve(transition):
        issue = unsupported_transition_type_issue(transition)
        if fail_fast:
            raise UnsupportedTimelineError(issue.message)
        return None, [issue], True

    try:
        duration_seconds = transition_duration_seconds(transition)
    except ValueError as exc:
        message = str(exc)
        if fail_fast:
            raise UnsupportedTimelineError(message) from exc
        return None, [
            TimelineIssue(
                code="invalid_transition",
                severity="warning",
                message=message,
                item_name=transition.name or "transition",
                item_type="Transition",
            )
        ], True

    fps = None
    if transition.in_offset is not None:
        fps = float(transition.in_offset.rate)

    dissolve_curve = parse_dissolve_curve(
        transition,
        duration_seconds=duration_seconds,
        fps=fps,
    )
    issues = dissolve_transition_issues(
        transition,
        duration_seconds=duration_seconds,
        fps=fps,
        dissolve_curve=dissolve_curve,
    )

    if not isinstance(pre, otio.schema.Clip) or not isinstance(post, otio.schema.Clip):
        message = (
            f"Audio transition {transition.name!r} must sit between two clips; "
            f"got {type(pre).__name__} and {type(post).__name__}."
        )
        issue = TimelineIssue(
            code="unsupported_transition",
            severity="warning",
            message=message,
            item_name=transition.name or "transition",
            item_type="Transition",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, issues + [issue], True

    outgoing, outgoing_issues, outgoing_skipped = _clip_segment(
        pre,
        settings=RenderSettings(),
        fail_fast=fail_fast,
    )
    if outgoing_skipped or outgoing is None:
        return None, issues + outgoing_issues, True

    incoming, incoming_issues, incoming_skipped = _clip_segment(
        post,
        settings=RenderSettings(),
        fail_fast=fail_fast,
    )
    if incoming_skipped or incoming is None:
        return None, issues + outgoing_issues + incoming_issues, True

    segment = DissolveAudioTransitionSegment(
        name=transition.name or "dissolve",
        duration_seconds=duration_seconds,
        outgoing=AudioClipSegment(
            name=outgoing.name,
            media_url=outgoing.media_url,
            start_seconds=outgoing.start_seconds,
            duration_seconds=outgoing.duration_seconds,
        ),
        incoming=AudioClipSegment(
            name=incoming.name,
            media_url=incoming.media_url,
            start_seconds=incoming.start_seconds,
            duration_seconds=incoming.duration_seconds,
        ),
    )
    return segment, issues + outgoing_issues + incoming_issues, False


def _gap_segment(
    gap: otio.schema.Gap,
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[TimelineSegment | None, list[TimelineIssue], bool]:
    if not settings.can_render_gaps:
        message = "Gaps require --width, --height, and --fps so FFmpeg can generate black video."
        issue = TimelineIssue(
            code="gap_missing_output_shape",
            severity="warning",
            message=message,
            item_name=gap.name or "gap",
            item_type="Gap",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, [issue], True

    segment = GapSegment(
        name=gap.name or "gap",
        duration_seconds=_seconds(gap.trimmed_range().duration),
    )
    return segment, [], False


def _clip_segment(
    clip: otio.schema.Clip,
    *,
    settings: RenderSettings,
    fail_fast: bool,
) -> tuple[TimelineSegment | None, list[TimelineIssue], bool]:
    issues: list[TimelineIssue] = []
    transform = None
    crop = parse_static_resolve_crop(clip.effects)
    animation = None
    trimmed_range = clip.trimmed_range()
    duration_seconds = _seconds(trimmed_range.duration)
    fps = settings.fps
    if fps is None:
        fps = float(trimmed_range.duration.rate)

    unsupported = unsupported_clip_effects(clip.effects)
    if unsupported:
        names = ", ".join(effect_display_name(effect) for effect in unsupported)
        message = f"Clip {clip.name!r} has unsupported effects: {names}."
        issue = TimelineIssue(
            code="unsupported_effect",
            severity="warning",
            message=message,
            item_name=clip.name or "clip",
            item_type="Clip",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        issues.append(issue)

    needs_canvas = needs_resolve_canvas(clip.effects)
    if needs_canvas and (settings.width is None or settings.height is None):
        message = (
            f"Clip {clip.name!r} has Resolve transform or keyframed effects, but --width "
            "and --height are required to render them."
        )
        issue = TimelineIssue(
            code="transform_missing_output_shape",
            severity="warning",
            message=message,
            item_name=clip.name or "clip",
            item_type="Clip",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        issues.append(issue)
    elif settings.width is not None and settings.height is not None:
        if has_keyframed_resolve_effects(clip.effects) or has_adjusted_resolve_composite_opacity(
            clip.effects
        ):
            animation, animation_warnings = parse_resolve_clip_animation(
                clip.effects,
                output_width=settings.width,
                output_height=settings.height,
                duration_seconds=duration_seconds,
                fps=fps,
            )
            for warning in animation_warnings:
                issues.append(
                    TimelineIssue(
                        code="keyframe_approximation",
                        severity="warning",
                        message=warning,
                        item_name=clip.name or "clip",
                        item_type="Clip",
                    )
                )
            if animation is not None and animation.has_crop:
                crop = None
        if has_static_resolve_transform(clip.effects):
            transform = parse_static_resolve_transform(
                clip.effects,
                output_width=settings.width,
                output_height=settings.height,
            )

    media_reference = clip.media_reference
    if not isinstance(media_reference, otio.schema.ExternalReference):
        message = f"Clip {clip.name!r} must use an ExternalReference media reference."
        issue = TimelineIssue(
            code="invalid_media_reference",
            severity="warning",
            message=message,
            item_name=clip.name or "clip",
            item_type="Clip",
        )
        if fail_fast:
            raise MediaReferenceError(message)
        return None, issues + [issue], True

    if not media_reference.target_url:
        message = f"Clip {clip.name!r} has an empty media target URL."
        issue = TimelineIssue(
            code="invalid_media_reference",
            severity="warning",
            message=message,
            item_name=clip.name or "clip",
            item_type="Clip",
        )
        if fail_fast:
            raise MediaReferenceError(message)
        return None, issues + [issue], True

    if duration_seconds <= 0:
        message = f"Clip {clip.name!r} has a non-positive duration."
        issue = TimelineIssue(
            code="invalid_clip_duration",
            severity="warning",
            message=message,
            item_name=clip.name or "clip",
            item_type="Clip",
        )
        if fail_fast:
            raise UnsupportedTimelineError(message)
        return None, issues + [issue], True

    media_url = normalize_target_url(media_reference.target_url)
    lut_path = settings.clip_luts.get(media_url)

    segment = ClipSegment(
        name=clip.name or "clip",
        media_url=media_url,
        start_seconds=_seconds(trimmed_range.start_time),
        duration_seconds=duration_seconds,
        lut_path=lut_path,
        transform=transform,
        crop=crop,
        animation=animation,
    )
    return segment, issues, False


def _seconds(rational_time: otio.opentime.RationalTime) -> float:
    return float(rational_time.value) / float(rational_time.rate)
