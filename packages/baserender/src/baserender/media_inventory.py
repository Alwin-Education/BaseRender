from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
import tempfile
from typing import Literal

import opentimelineio as otio

from baserender.timeline_model import normalize_target_url


MediaReferenceStatus = Literal["linked", "empty", "missing", "unsupported"]


@dataclass(frozen=True)
class MediaReferenceEntry:
    """A single OTIO clip media reference for UI relinking workflows."""

    id: str
    clip_name: str
    track_path: str
    reference_kind: str
    target_url: str | None
    normalized_url: str | None
    status: MediaReferenceStatus
    clip_count: int = 1


@dataclass(frozen=True)
class MediaInventory:
    entries: tuple[MediaReferenceEntry, ...]
    unique_urls: tuple[str, ...]


def load_media_inventory(path: str | Path) -> MediaInventory:
    timeline = otio.adapters.read_from_file(str(path))
    return extract_media_inventory(timeline)


def load_timeline_from_text(otio_text: str) -> otio.schema.Timeline:
    with tempfile.NamedTemporaryFile("w+", suffix=".otio", encoding="utf-8") as handle:
        handle.write(otio_text)
        handle.flush()
        return otio.adapters.read_from_file(handle.name)


def load_media_inventory_from_text(otio_text: str) -> MediaInventory:
    timeline = load_timeline_from_text(otio_text)
    return extract_media_inventory(timeline)


def extract_media_inventory(timeline: otio.schema.Timeline) -> MediaInventory:
    entries = tuple(_walk_timeline(timeline))
    unique_urls = tuple(dict.fromkeys(entry.normalized_url for entry in entries if entry.normalized_url))
    return MediaInventory(entries=entries, unique_urls=unique_urls)


def dedupe_reference_entries(
    entries: Iterable[MediaReferenceEntry],
) -> tuple[MediaReferenceEntry, ...]:
    deduped: list[MediaReferenceEntry] = []
    index_by_url: dict[str, int] = {}

    for entry in entries:
        key = entry.normalized_url or entry.target_url
        if key is None:
            deduped.append(entry)
            continue

        existing_index = index_by_url.get(key)
        if existing_index is None:
            index_by_url[key] = len(deduped)
            deduped.append(entry)
            continue

        existing = deduped[existing_index]
        deduped[existing_index] = replace(existing, clip_count=existing.clip_count + entry.clip_count)

    return tuple(deduped)


def _walk_timeline(timeline: otio.schema.Timeline) -> Iterable[MediaReferenceEntry]:
    yield from _walk_children(timeline.tracks, [timeline.name or "Timeline"])


def _walk_children(
    composition: otio.schema.Track | otio.schema.Stack,
    path: list[str],
) -> Iterable[MediaReferenceEntry]:
    for index, item in enumerate(composition):
        item_label = item.name or f"{item.__class__.__name__} {index + 1}"
        item_path = [*path, item_label]
        if isinstance(item, otio.schema.Clip):
            yield _entry_for_clip(item, "/".join(item_path), index)
        elif isinstance(item, (otio.schema.Track, otio.schema.Stack)):
            yield from _walk_children(item, item_path)


def _entry_for_clip(
    clip: otio.schema.Clip,
    track_path: str,
    index: int,
) -> MediaReferenceEntry:
    media_reference = clip.media_reference
    reference_kind = media_reference.__class__.__name__ if media_reference is not None else "None"
    target_url: str | None = None
    normalized_url: str | None = None
    status: MediaReferenceStatus = "unsupported"

    if isinstance(media_reference, otio.schema.ExternalReference):
        target_url = media_reference.target_url or None
        if target_url:
            normalized_url = normalize_target_url(target_url)
            status = "linked"
        else:
            status = "empty"
    elif isinstance(media_reference, otio.schema.MissingReference):
        status = "missing"

    return MediaReferenceEntry(
        id=_reference_id(track_path, clip.name or "clip", target_url, index),
        clip_name=clip.name or "clip",
        track_path=track_path,
        reference_kind=reference_kind,
        target_url=target_url,
        normalized_url=normalized_url,
        status=status,
    )


def _reference_id(track_path: str, clip_name: str, target_url: str | None, index: int) -> str:
    value = "\0".join((track_path, clip_name, target_url or "", str(index)))
    return sha256(value.encode("utf-8")).hexdigest()[:16]
