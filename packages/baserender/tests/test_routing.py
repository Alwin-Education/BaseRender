from __future__ import annotations

from pathlib import Path

from baserender.animation import AnimatedScalar, ClipAnimation, KeyframePoint
from baserender.routing import RouteKind, ShotHandler, classify_timeline
from baserender.timeline_model import (
    ClipCrop,
    ClipSegment,
    ClipTransform,
    DissolveTransitionSegment,
    GapSegment,
    TimelinePlan,
    VideoTimelineTrack,
)


def _simple_plan(*segments: ClipSegment | GapSegment | DissolveTransitionSegment) -> TimelinePlan:
    return TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=segments,
    )


def test_no_lut_simple_cuts_routes_to_full_mediaconvert() -> None:
    plan = _simple_plan(
        ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=2),
        ClipSegment("B", "/media/b.mov", start_seconds=0, duration_seconds=3),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.FULL_MEDIACONVERT
    assert routing.distinct_lut_count == 0
    assert routing.requires_final_stitch is False
    assert all(shot.handler is ShotHandler.MEDIACONVERT for shot in routing.shots)
    assert len(routing.shots) == 2


def test_one_lut_across_all_clips_routes_to_full_mediaconvert() -> None:
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=0,
            duration_seconds=2,
            lut_path="/looks/shared.cube",
        ),
        ClipSegment(
            "B",
            "/media/b.mov",
            start_seconds=0,
            duration_seconds=3,
            lut_path="/looks/shared.cube",
        ),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.FULL_MEDIACONVERT
    assert routing.distinct_lut_count == 1
    assert routing.requires_final_stitch is False


def test_multiple_luts_routes_to_per_shot_mediaconvert() -> None:
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=0,
            duration_seconds=2,
            lut_path="/looks/a.cube",
        ),
        ClipSegment(
            "B",
            "/media/b.mov",
            start_seconds=0,
            duration_seconds=3,
            lut_path="/looks/b.cube",
        ),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.PER_SHOT_MEDIACONVERT
    assert routing.distinct_lut_count == 2
    assert routing.requires_final_stitch is True
    assert all(shot.handler is ShotHandler.MEDIACONVERT for shot in routing.shots)


def test_keyframed_clip_routes_to_hybrid() -> None:
    animation = ClipAnimation(
        scale_x=AnimatedScalar(
            (
                KeyframePoint(0.0, 1.0),
                KeyframePoint(2.0, 1.5),
            )
        )
    )
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=0,
            duration_seconds=2,
            animation=animation,
        ),
        ClipSegment("B", "/media/b.mov", start_seconds=0, duration_seconds=3),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.HYBRID
    assert routing.requires_final_stitch is True
    assert routing.shots[0].handler is ShotHandler.LAMBDA_FFMPEG
    assert "keyframes" in routing.shots[0].reasons
    assert routing.shots[1].handler is ShotHandler.MEDIACONVERT


def test_multi_track_overlay_routes_to_hybrid() -> None:
    plan = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(),
        video_tracks=(
            VideoTimelineTrack(
                "V1",
                (
                    ClipSegment("Base", "/media/a.mov", start_seconds=0, duration_seconds=4),
                ),
            ),
            VideoTimelineTrack(
                "V2",
                (
                    GapSegment("lead", duration_seconds=1),
                    ClipSegment("Overlay", "/media/b.mov", start_seconds=0, duration_seconds=2),
                ),
            ),
        ),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.HYBRID
    assert routing.requires_final_stitch is True
    assert all(shot.handler is ShotHandler.LAMBDA_FFMPEG for shot in routing.shots)
    assert all("multi_track_compositing" in shot.reasons for shot in routing.shots)


def test_dissolve_routes_to_hybrid() -> None:
    plan = _simple_plan(
        ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),
        DissolveTransitionSegment(
            name="Dissolve",
            duration_seconds=0.5,
            outgoing=ClipSegment(
                "A tail",
                "/media/a.mov",
                start_seconds=0.5,
                duration_seconds=0.5,
            ),
            incoming=ClipSegment(
                "B head",
                "/media/b.mov",
                start_seconds=0,
                duration_seconds=0.5,
            ),
        ),
        ClipSegment("B", "/media/b.mov", start_seconds=0.5, duration_seconds=1),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.HYBRID
    assert routing.requires_final_stitch is True
    dissolve_shots = [shot for shot in routing.shots if "dissolve" in shot.reasons]
    assert len(dissolve_shots) == 2
    assert routing.shots[0].handler is ShotHandler.MEDIACONVERT
    assert routing.shots[-1].handler is ShotHandler.MEDIACONVERT


def test_mixed_lut_and_keyframed_shot_routes_to_hybrid_with_timing() -> None:
    animation = ClipAnimation(
        translate_x=AnimatedScalar(
            (
                KeyframePoint(0.0, 0.0),
                KeyframePoint(1.0, 100.0),
            )
        )
    )
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=1.0,
            duration_seconds=2.0,
            lut_path="/looks/a.cube",
        ),
        ClipSegment(
            "B",
            "/media/b.mov",
            start_seconds=0.5,
            duration_seconds=3.0,
            lut_path="/looks/b.cube",
            animation=animation,
        ),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.HYBRID
    assert routing.distinct_lut_count == 2
    assert routing.shots[0].handler is ShotHandler.MEDIACONVERT
    assert routing.shots[0].timeline_offset_seconds == 0.0
    assert routing.shots[0].source_in_seconds == 1.0
    assert routing.shots[0].source_out_seconds == 3.0

    assert routing.shots[1].handler is ShotHandler.LAMBDA_FFMPEG
    assert routing.shots[1].timeline_offset_seconds == 2.0
    assert routing.shots[1].source_in_seconds == 0.5
    assert routing.shots[1].source_out_seconds == 3.5
    assert "keyframes" in routing.shots[1].reasons


def test_static_transform_routes_shot_to_lambda() -> None:
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=0,
            duration_seconds=2,
            transform=ClipTransform(scale_x=0.5, scale_y=0.5),
        ),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.HYBRID
    assert routing.shots[0].handler is ShotHandler.LAMBDA_FFMPEG
    assert "static_transform" in routing.shots[0].reasons


def test_static_crop_routes_shot_to_lambda() -> None:
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=0,
            duration_seconds=2,
            crop=ClipCrop(left=0.1, right=0.1),
        ),
    )

    routing = classify_timeline(plan)

    assert routing.route is RouteKind.HYBRID
    assert routing.shots[0].handler is ShotHandler.LAMBDA_FFMPEG
    assert "static_crop" in routing.shots[0].reasons


def test_gaps_advance_timeline_offset_without_creating_shots() -> None:
    plan = _simple_plan(
        GapSegment("gap", duration_seconds=1.5),
        ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=2),
    )

    routing = classify_timeline(plan)

    assert len(routing.shots) == 1
    assert routing.shots[0].timeline_offset_seconds == 1.5
