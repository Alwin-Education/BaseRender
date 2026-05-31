from __future__ import annotations

import json

import opentimelineio as otio

from baserender.media_inventory import (
    dedupe_reference_entries,
    extract_media_inventory,
    load_media_inventory_from_text,
)


def _time_range() -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, 24),
        duration=otio.opentime.RationalTime(24, 24),
    )


def test_extract_media_inventory_reports_external_and_missing_references() -> None:
    timeline = otio.schema.Timeline(name="Inventory")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Shot A",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///tmp/Shot%20A.mov",
                available_range=_time_range(),
            ),
            source_range=_time_range(),
        )
    )
    track.append(
        otio.schema.Clip(
            name="Offline",
            media_reference=otio.schema.MissingReference(),
            source_range=_time_range(),
        )
    )

    inventory = extract_media_inventory(timeline)

    assert inventory.unique_urls == ("/tmp/Shot A.mov",)
    assert [entry.clip_name for entry in inventory.entries] == ["Shot A", "Offline"]
    assert inventory.entries[0].status == "linked"
    assert inventory.entries[0].target_url == "file:///tmp/Shot%20A.mov"
    assert inventory.entries[0].normalized_url == "/tmp/Shot A.mov"
    assert inventory.entries[1].status == "missing"
    assert inventory.entries[1].reference_kind == "MissingReference"


def test_extract_media_inventory_reports_empty_external_reference() -> None:
    timeline = otio.schema.Timeline(name="Inventory")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    track.append(
        otio.schema.Clip(
            name="Empty",
            media_reference=otio.schema.ExternalReference(target_url=""),
            source_range=_time_range(),
        )
    )

    inventory = extract_media_inventory(timeline)

    assert len(inventory.entries) == 1
    assert inventory.entries[0].status == "empty"
    assert inventory.entries[0].target_url is None
    assert inventory.entries[0].normalized_url is None


def test_dedupe_reference_entries_groups_by_source_url() -> None:
    timeline = otio.schema.Timeline(name="Inventory")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    timeline.tracks.append(track)
    for name in ("Shot A", "Shot A Replay"):
        track.append(
            otio.schema.Clip(
                name=name,
                media_reference=otio.schema.ExternalReference(
                    target_url="file:///tmp/Shot%20A.mov",
                    available_range=_time_range(),
                ),
                source_range=_time_range(),
            )
        )
    track.append(
        otio.schema.Clip(
            name="Offline",
            media_reference=otio.schema.MissingReference(),
            source_range=_time_range(),
        )
    )

    inventory = extract_media_inventory(timeline)
    deduped = dedupe_reference_entries(inventory.entries)

    assert [entry.clip_name for entry in deduped] == ["Shot A", "Offline"]
    assert deduped[0].normalized_url == "/tmp/Shot A.mov"
    assert deduped[0].clip_count == 2
    assert deduped[1].status == "missing"
    assert deduped[1].clip_count == 1


def test_load_media_inventory_from_text_reads_resolve_media_references() -> None:
    inventory = load_media_inventory_from_text(_resolve_style_otio_text())

    assert len(inventory.entries) == 1
    assert inventory.unique_urls == ("/Volumes/Raid/Shot_A.mov",)
    assert inventory.entries[0].clip_name == "Shot A"
    assert inventory.entries[0].status == "linked"
    assert inventory.entries[0].target_url == "file:///Volumes/Raid/Shot_A.mov"
    assert inventory.entries[0].normalized_url == "/Volumes/Raid/Shot_A.mov"


def _resolve_style_otio_text() -> str:
    return json.dumps(
        {
            "OTIO_SCHEMA": "Timeline.1",
            "metadata": {},
            "name": "Resolve Inventory",
            "global_start_time": None,
            "tracks": {
                "OTIO_SCHEMA": "Stack.1",
                "metadata": {},
                "name": "",
                "source_range": None,
                "effects": [],
                "markers": [],
                "enabled": True,
                "children": [
                    {
                        "OTIO_SCHEMA": "Track.1",
                        "metadata": {},
                        "name": "Video 1",
                        "source_range": None,
                        "effects": [],
                        "markers": [],
                        "enabled": True,
                        "children": [
                            {
                                "OTIO_SCHEMA": "Clip.2",
                                "metadata": {},
                                "name": "Shot A",
                                "source_range": None,
                                "effects": [],
                                "markers": [],
                                "enabled": True,
                                "media_references": {
                                    "DEFAULT_MEDIA": {
                                        "OTIO_SCHEMA": "ExternalReference.1",
                                        "metadata": {},
                                        "name": "Shot_A.mov",
                                        "available_range": None,
                                        "available_image_bounds": None,
                                        "target_url": "file:///Volumes/Raid/Shot_A.mov",
                                    }
                                },
                                "active_media_reference_key": "DEFAULT_MEDIA",
                            }
                        ],
                        "kind": "Video",
                    }
                ],
            },
        }
    )
