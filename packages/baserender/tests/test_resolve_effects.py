from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest

from baserender.otio_reader import load_timeline_plan
from baserender.resolve_effects import (
    has_keyframed_resolve_effects,
    is_noop_resolve_effect,
    is_static_resolve_crop,
    is_static_resolve_transform,
    is_supported_resolve_clip_effect,
    parse_resolve_clip_animation,
    parse_static_resolve_crop,
    parse_static_resolve_transform,
    unsupported_clip_effects,
)
from baserender.timeline_model import ClipCrop, ClipTransform, UnsupportedTimelineError


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


def test_is_noop_resolve_effect_handles_disabled_and_empty() -> None:
    assert is_noop_resolve_effect(_resolve_effect("Transform"))
    assert is_noop_resolve_effect(
        _resolve_effect(
            "Dynamic Zoom",
            enabled=False,
            parameters=[
                {
                    "Default Parameter Value": 1.0,
                    "Parameter ID": "dynamicZoomScale",
                    "Parameter Value": 1.0,
                    "Variant Type": "Double",
                }
            ],
        )
    )


def test_is_noop_resolve_effect_rejects_non_default_parameters() -> None:
    effect = _resolve_effect(
        "Transform",
        parameters=[
            {
                "Default Parameter Value": 0.0,
                "Parameter ID": "positionX",
                "Parameter Value": 120.0,
                "Variant Type": "Double",
            }
        ],
    )
    assert not is_noop_resolve_effect(effect)


def test_parse_static_resolve_transform_maps_to_clip_transform() -> None:
    effect = _resolve_effect(
        "Transform",
        parameters=[
            {
                "Default Parameter Value": 1.0,
                "Key Frames": {},
                "Parameter ID": "transformationZoomX",
                "Parameter Value": 2.0,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 1.0,
                "Key Frames": {},
                "Parameter ID": "transformationZoomY",
                "Parameter Value": 1.5,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "transformationPan",
                "Parameter Value": 0.25,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "transformationTilt",
                "Parameter Value": -0.1,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "transformationRotationAngle",
                "Parameter Value": -1.1,
                "Variant Type": "Double",
            },
        ],
    )

    assert is_static_resolve_transform(effect)
    assert unsupported_clip_effects([effect]) == []
    assert parse_static_resolve_transform(
        [effect],
        output_width=1920,
        output_height=1080,
    ) == ClipTransform(
        scale_x=2.0,
        scale_y=1.5,
        translate_x=480.0,
        translate_y=108.0,
        rotation_degrees=1.1,
    )


def test_parse_static_resolve_crop_maps_normalized_edges() -> None:
    effect = _resolve_effect(
        "Cropping",
        parameters=[
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "cropLeft",
                "Parameter Value": 0.34,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "cropRight",
                "Parameter Value": 0.1,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "cropTop",
                "Parameter Value": 0.05,
                "Variant Type": "Double",
            },
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "cropBottom",
                "Parameter Value": 0.15,
                "Variant Type": "Double",
            },
        ],
    )

    assert is_static_resolve_crop(effect)
    assert unsupported_clip_effects([effect]) == []
    assert parse_static_resolve_crop([effect]) == ClipCrop(
        left=0.34,
        right=0.1,
        top=0.05,
        bottom=0.15,
    )


def test_parse_static_resolve_crop_allows_partial_edges() -> None:
    effect = _resolve_effect(
        "Cropping",
        parameters=[
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {},
                "Parameter ID": "cropLeft",
                "Parameter Value": 0.34,
                "Variant Type": "Double",
            },
        ],
    )

    assert parse_static_resolve_crop([effect]) == ClipCrop(left=0.34)


def test_keyframed_resolve_crop_is_supported() -> None:
    effect = _resolve_effect(
        "Cropping",
        parameters=[
            {
                "Default Parameter Value": 0.0,
                "Key Frames": {"0": {"Value": 0.0}, "10": {"Value": 0.5}},
                "Parameter ID": "cropLeft",
                "Parameter Value": 0.34,
                "Variant Type": "Double",
            },
        ],
    )

    assert not is_static_resolve_crop(effect)
    assert is_supported_resolve_clip_effect(effect)
    assert unsupported_clip_effects([effect]) == []
    animation, _ = parse_resolve_clip_animation(
        [effect],
        output_width=1920,
        output_height=1080,
        duration_seconds=1.0,
        fps=24.0,
    )
    assert animation is not None
    assert animation.crop_left is not None
    assert animation.crop_left.evaluate(0.0) == pytest.approx(0.0)
    assert animation.crop_left.evaluate(1.0) == pytest.approx(0.5)


def test_keyframed_composite_with_default_parameter_value_is_not_noop() -> None:
    effect = _resolve_effect(
        "Composite",
        parameters=[
            {
                "Default Parameter Value": 100.0,
                "Key Frames": {"0": {"Value": 0.0}, "24": {"Value": 50.0}},
                "Parameter ID": "opacity",
                "Parameter Value": 100.0,
                "Variant Type": "Double",
            },
        ],
    )

    assert not is_noop_resolve_effect(effect)
    assert has_keyframed_resolve_effects([effect])


def test_keyframed_resolve_transform_is_supported() -> None:
    effect = _resolve_effect(
        "Transform",
        parameters=[
            {
                "Default Parameter Value": 1.0,
                "Key Frames": {"0": {"Value": 1.0}, "10": {"Value": 2.0}},
                "Parameter ID": "transformationZoomX",
                "Parameter Value": 2.0,
                "Variant Type": "Double",
            },
        ],
    )

    assert not is_static_resolve_transform(effect)
    assert is_supported_resolve_clip_effect(effect)
    assert unsupported_clip_effects([effect]) == []
    animation, _ = parse_resolve_clip_animation(
        [effect],
        output_width=1920,
        output_height=1080,
        duration_seconds=1.0,
        fps=24.0,
    )
    assert animation is not None
    assert animation.scale_x is not None
    assert animation.scale_x.evaluate(0.0) == pytest.approx(1.0)
    assert animation.scale_x.evaluate(1.0) == pytest.approx(2.0)


def test_load_timeline_plan_ignores_resolve_pipeline_effects(tmp_path: Path) -> None:
    otio_path = tmp_path / "resolve.otio"
    timeline = otio.schema.Timeline(name="Resolve Pipeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)

    media_reference = otio.schema.ExternalReference(
        target_url="file:///tmp/source.mov",
        available_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(48, 24),
        ),
    )
    clip = otio.schema.Clip(
        name="Shot A",
        media_reference=media_reference,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(24, 24),
        ),
        effects=[
            _resolve_effect("Transform"),
            _resolve_effect("Composite"),
            _resolve_effect("Dynamic Zoom", enabled=False),
        ],
    )
    track.append(clip)
    otio.adapters.write_to_file(timeline, str(otio_path))

    result = load_timeline_plan(otio_path)

    assert result.plan is not None
    assert len(result.plan.segments) == 1


def test_non_resolve_effect_is_rejected(tmp_path: Path) -> None:
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
