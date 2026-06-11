#!/usr/bin/env python3
"""Generate OTIO fixtures for Phase 2 validation and smoke tests."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def _clip(
    name: str,
    target_url: str,
    *,
    duration_frames: int = 24,
    effects: list | None = None,
) -> otio.schema.Clip:
    return otio.schema.Clip(
        name=name,
        media_reference=otio.schema.ExternalReference(
            target_url=target_url,
            available_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(duration_frames, 24),
            ),
        ),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(duration_frames, 24),
        ),
        effects=effects or [],
    )


def _resolve_transform_keyframes() -> otio.schema.Effect:
    return otio.schema.Effect(
        effect_name="Resolve Effect",
        metadata={
            "Resolve_OTIO": {
                "Effect Name": "Transform",
                "Enabled": True,
                "Parameters": [
                    {
                        "Default Parameter Value": 1.0,
                        "Key Frames": {"0": {"Value": 1.0}, "24": {"Value": 1.5}},
                        "Parameter ID": "transformationZoomX",
                        "Parameter Value": 1.5,
                        "Variant Type": "Double",
                    },
                ],
            }
        },
    )


def _write_timeline(path: Path, name: str, clips: list[otio.schema.Clip]) -> None:
    timeline = otio.schema.Timeline(name=name)
    track = otio.schema.Track(name="Video 1", kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    timeline.tracks.append(track)
    path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(path))
    print(f"Wrote {path.relative_to(ROOT)}")


def main() -> None:
    _write_timeline(
        FIXTURES / "sample.otio",
        "Sample",
        [_clip("Shot A", "file:///test/Shot_A.mov")],
    )
    _write_timeline(
        FIXTURES / "two_clip.otio",
        "Two Clip",
        [
            _clip("Shot A", "file:///test/Shot_A.mov"),
            _clip("Shot B", "file:///test/Shot_B.mov", duration_frames=48),
        ],
    )
    _write_timeline(
        FIXTURES / "hybrid.otio",
        "Hybrid Keyframes",
        [
            _clip(
                "Shot A",
                "file:///test/Shot_A.mov",
                effects=[_resolve_transform_keyframes()],
            ),
            _clip("Shot B", "file:///test/Shot_B.mov", duration_frames=48),
        ],
    )


if __name__ == "__main__":
    main()
