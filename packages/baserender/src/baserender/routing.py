"""Classify a TimelinePlan for MediaConvert vs Lambda FFmpeg execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from baserender.timeline_model import (
    ClipSegment,
    DissolveTransitionSegment,
    GapSegment,
    RenderSettings,
    TimelinePlan,
    TimelineSegment,
)

# Thresholds for routing shots to Lambda instead of MediaConvert.
# Tune these as MediaConvert capabilities are confirmed.
LAMBDA_ON_KEYFRAMES = True
LAMBDA_ON_STATIC_TRANSFORM = True
LAMBDA_ON_STATIC_CROP = True
LAMBDA_ON_OPACITY = True
LAMBDA_ON_DISSOLVE = True
LAMBDA_ON_MULTI_TRACK = True


class RouteKind(Enum):
    FULL_MEDIACONVERT = "full_mediaconvert"
    PER_SHOT_MEDIACONVERT = "per_shot_mediaconvert"
    HYBRID = "hybrid"


class ShotHandler(Enum):
    MEDIACONVERT = "mediaconvert"
    LAMBDA_FFMPEG = "lambda_ffmpeg"


@dataclass(frozen=True)
class ShotRouting:
    index: int
    name: str
    media_url: str
    handler: ShotHandler
    lut_path: str | None
    reasons: tuple[str, ...]
    timeline_offset_seconds: float
    source_in_seconds: float
    source_out_seconds: float


@dataclass(frozen=True)
class RoutingPlan:
    route: RouteKind
    shots: tuple[ShotRouting, ...]
    distinct_lut_count: int
    requires_final_stitch: bool


def classify_timeline(
    plan: TimelinePlan,
    *,
    settings: RenderSettings | None = None,
) -> RoutingPlan:
    """Return the render route and per-shot handler assignments for a timeline."""
    _ = settings  # Reserved for future settings-driven routing (e.g. output format).
    multi_track = plan.has_multiple_video_tracks
    shot_candidates = _collect_shot_candidates(plan, multi_track=multi_track)
    shots = _build_shot_routings(shot_candidates, multi_track=multi_track)
    distinct_lut_count = len({shot.lut_path for shot in shots if shot.lut_path is not None})
    has_lambda_shots = any(shot.handler is ShotHandler.LAMBDA_FFMPEG for shot in shots)

    if has_lambda_shots:
        route = RouteKind.HYBRID
        requires_final_stitch = True
    elif distinct_lut_count <= 1:
        route = RouteKind.FULL_MEDIACONVERT
        requires_final_stitch = False
    else:
        route = RouteKind.PER_SHOT_MEDIACONVERT
        requires_final_stitch = True

    return RoutingPlan(
        route=route,
        shots=shots,
        distinct_lut_count=distinct_lut_count,
        requires_final_stitch=requires_final_stitch,
    )


@dataclass(frozen=True)
class _ShotCandidate:
    clip: ClipSegment
    timeline_offset_seconds: float
    in_dissolve: bool


def _collect_shot_candidates(
    plan: TimelinePlan,
    *,
    multi_track: bool,
) -> tuple[_ShotCandidate, ...]:
    candidates: list[_ShotCandidate] = []
    for track in plan.effective_video_tracks:
        candidates.extend(_walk_track_segments(track.segments))
    return tuple(candidates)


def _walk_track_segments(
    segments: tuple[TimelineSegment, ...],
) -> list[_ShotCandidate]:
    candidates: list[_ShotCandidate] = []
    timeline_offset = 0.0

    for segment in segments:
        if isinstance(segment, ClipSegment):
            candidates.append(
                _ShotCandidate(
                    clip=segment,
                    timeline_offset_seconds=timeline_offset,
                    in_dissolve=False,
                )
            )
            timeline_offset += segment.duration_seconds
        elif isinstance(segment, GapSegment):
            timeline_offset += segment.duration_seconds
        elif isinstance(segment, DissolveTransitionSegment):
            candidates.append(
                _ShotCandidate(
                    clip=segment.outgoing,
                    timeline_offset_seconds=timeline_offset,
                    in_dissolve=True,
                )
            )
            candidates.append(
                _ShotCandidate(
                    clip=segment.incoming,
                    timeline_offset_seconds=timeline_offset,
                    in_dissolve=True,
                )
            )
            timeline_offset += segment.duration_seconds

    return candidates


def _build_shot_routings(
    candidates: tuple[_ShotCandidate, ...],
    *,
    multi_track: bool,
) -> tuple[ShotRouting, ...]:
    shots: list[ShotRouting] = []
    for index, candidate in enumerate(candidates):
        reasons = _lambda_reasons(
            candidate.clip,
            in_dissolve=candidate.in_dissolve,
            multi_track=multi_track,
        )
        handler = (
            ShotHandler.LAMBDA_FFMPEG if reasons else ShotHandler.MEDIACONVERT
        )
        clip = candidate.clip
        shots.append(
            ShotRouting(
                index=index,
                name=clip.name,
                media_url=clip.media_url,
                handler=handler,
                lut_path=clip.lut_path,
                reasons=reasons,
                timeline_offset_seconds=candidate.timeline_offset_seconds,
                source_in_seconds=clip.start_seconds,
                source_out_seconds=clip.start_seconds + clip.duration_seconds,
            )
        )
    return tuple(shots)


def _lambda_reasons(
    clip: ClipSegment,
    *,
    in_dissolve: bool,
    multi_track: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []

    if multi_track and LAMBDA_ON_MULTI_TRACK:
        reasons.append("multi_track_compositing")
    if in_dissolve and LAMBDA_ON_DISSOLVE:
        reasons.append("dissolve")
    if clip.has_animation and LAMBDA_ON_KEYFRAMES:
        reasons.append("keyframes")
    if (
        clip.transform is not None
        and not clip.transform.is_identity
        and LAMBDA_ON_STATIC_TRANSFORM
    ):
        reasons.append("static_transform")
    if clip.crop is not None and not clip.crop.is_identity and LAMBDA_ON_STATIC_CROP:
        reasons.append("static_crop")
    if (
        clip.animation is not None
        and clip.animation.has_opacity
        and LAMBDA_ON_OPACITY
    ):
        reasons.append("opacity")

    return tuple(reasons)
