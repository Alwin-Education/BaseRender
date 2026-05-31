from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query

from baserender.media_inventory import (
    MediaInventory,
    MediaReferenceEntry,
    dedupe_reference_entries,
    load_media_inventory,
    load_media_inventory_from_text,
)
from baserender_api.media.filters import filter_media_objects
from baserender_api.media.matching import suggest_matches
from baserender_api.media.prefix import enforce_allowed_prefix, normalize_s3_prefix
from baserender_api.media.provider import (
    CloudMediaObject,
    CloudMediaProvider,
    ListObjectsResult,
    get_media_provider,
)
from baserender_api.media.s3 import (
    DEFAULT_LIST_PAGE_SIZE,
    DEFAULT_MAX_LISTED_OBJECTS,
    MediaStorageError,
)
from baserender_api.media.schemas import (
    CloudMediaObjectPayload,
    MatchSuggestionPayload,
    MediaConfigResponse,
    MediaLinkingRequest,
    MediaLinkingResponse,
    MediaObjectsResponse,
    MediaReferencePayload,
)
from baserender_api.defaults import (
    allowed_media_prefix,
    default_media_prefix,
    default_audio_bitrate,
    default_audio_codec,
    default_container,
    default_fps,
    default_height,
    default_output_path,
    default_video_bitrate,
    default_video_codec,
    default_video_faststart,
    default_video_preset,
    default_width,
)


router = APIRouter(prefix="/media", tags=["media"])


@dataclass(frozen=True)
class MediaObjectListing:
    objects: tuple[CloudMediaObject, ...]
    next_continuation_token: str | None

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def truncated(self) -> bool:
        return self.next_continuation_token is not None


def media_provider_dependency() -> CloudMediaProvider:
    try:
        return get_media_provider()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/config", response_model=MediaConfigResponse)
def get_media_config() -> MediaConfigResponse:
    return MediaConfigResponse(
        provider=os.getenv("BASERENDER_MEDIA_PROVIDER", "s3").strip().lower(),
        allowed_prefix=allowed_media_prefix(),
        default_media_prefix=normalize_s3_prefix(default_media_prefix()),
        default_output_prefix=normalize_s3_prefix(os.getenv("BASERENDER_OUTPUT_PREFIX", "outputs")),
        default_output_path=default_output_path(),
        default_container=default_container(),
        default_width=default_width(),
        default_height=default_height(),
        default_fps=default_fps(),
        default_video_codec=default_video_codec(),
        default_video_bitrate=default_video_bitrate(),
        default_video_preset=default_video_preset(),
        default_video_faststart=default_video_faststart(),
        default_audio_codec=default_audio_codec(),
        default_audio_bitrate=default_audio_bitrate(),
    )


@router.get("/objects", response_model=MediaObjectsResponse)
def list_media_objects(
    prefix: str = "",
    continuation_token: str | None = None,
    max_keys: int | None = Query(default=None, ge=1, le=1000),
    provider: CloudMediaProvider = Depends(media_provider_dependency),
) -> MediaObjectsResponse:
    effective_prefix = _effective_prefix(prefix)
    result = _list_media_objects(
        provider,
        effective_prefix,
        continuation_token=continuation_token,
        max_keys=max_keys,
    )
    return MediaObjectsResponse(
        provider=provider.id,
        prefix=effective_prefix,
        objects=[_object_payload(obj) for obj in result.objects],
        next_continuation_token=result.next_continuation_token,
        object_count=result.object_count,
        truncated=result.truncated,
    )


@router.post("/linking", response_model=MediaLinkingResponse)
def create_media_linking_response(
    request: MediaLinkingRequest,
    provider: CloudMediaProvider = Depends(media_provider_dependency),
) -> MediaLinkingResponse:
    effective_prefix = _effective_prefix(request.prefix)
    result = _list_media_objects(
        provider,
        effective_prefix,
        continuation_token=request.continuation_token,
        max_keys=request.max_keys,
    )
    inventory = _inventory_from_request(request)
    entries = dedupe_reference_entries(inventory.entries)
    suggestions = suggest_matches(
        entries,
        result.objects,
        limit=request.suggestion_limit,
        min_score=request.min_score,
    )
    references = [
        _reference_payload(entry, suggestions.get(entry.id, ()))
        for entry in entries
    ]
    return MediaLinkingResponse(
        provider=provider.id,
        prefix=effective_prefix,
        objects=[_object_payload(obj) for obj in result.objects],
        next_continuation_token=result.next_continuation_token,
        object_count=result.object_count,
        truncated=result.truncated,
        references=references,
    )


def _effective_prefix(prefix: str | None) -> str:
    try:
        return enforce_allowed_prefix(prefix, allowed_media_prefix())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _list_media_objects(
    provider: CloudMediaProvider,
    prefix: str,
    *,
    continuation_token: str | None = None,
    max_keys: int | None = None,
) -> MediaObjectListing:
    result = _list_all_objects(
        provider,
        prefix,
        continuation_token=continuation_token,
        page_size=max_keys or DEFAULT_LIST_PAGE_SIZE,
    )
    return MediaObjectListing(
        objects=filter_media_objects(result.objects),
        next_continuation_token=result.next_continuation_token,
    )


def _list_all_objects(
    provider: CloudMediaProvider,
    prefix: str,
    *,
    continuation_token: str | None = None,
    page_size: int = DEFAULT_LIST_PAGE_SIZE,
) -> ListObjectsResult:
    if continuation_token:
        return _list_object_page(
            provider,
            prefix,
            continuation_token=continuation_token,
            max_keys=page_size,
        )

    list_all_objects = getattr(provider, "list_all_objects", None)
    if callable(list_all_objects):
        try:
            return list_all_objects(
                prefix,
                page_size=page_size,
                max_objects=DEFAULT_MAX_LISTED_OBJECTS,
            )
        except MediaStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    objects: list[CloudMediaObject] = []
    next_token: str | None = None
    while len(objects) < DEFAULT_MAX_LISTED_OBJECTS:
        result = _list_object_page(
            provider,
            prefix,
            continuation_token=next_token,
            max_keys=page_size,
        )
        objects.extend(result.objects)
        next_token = result.next_continuation_token
        if not next_token:
            break

    return ListObjectsResult(
        objects=tuple(objects),
        next_continuation_token=next_token if len(objects) >= DEFAULT_MAX_LISTED_OBJECTS else None,
    )


def _list_object_page(
    provider: CloudMediaProvider,
    prefix: str,
    *,
    continuation_token: str | None = None,
    max_keys: int | None = None,
) -> ListObjectsResult:
    try:
        return provider.list_objects(
            prefix,
            continuation_token=continuation_token,
            max_keys=max_keys,
        )
    except MediaStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _inventory_from_request(request: MediaLinkingRequest) -> MediaInventory:
    if request.timeline_path:
        try:
            return load_media_inventory(request.timeline_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not load OTIO timeline: {exc}") from exc

    try:
        otio_text = base64.b64decode(request.otio_content_base64 or "", validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Could not load OTIO timeline: {exc}") from exc

    try:
        return load_media_inventory_from_text(otio_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not load OTIO timeline: {exc}") from exc


def _object_payload(obj: CloudMediaObject) -> CloudMediaObjectPayload:
    return CloudMediaObjectPayload(
        key=obj.key,
        size=obj.size,
        last_modified=obj.last_modified,
        etag=obj.etag,
    )


def _reference_payload(
    entry: MediaReferenceEntry,
    suggestions: tuple,
) -> MediaReferencePayload:
    return MediaReferencePayload(
        id=entry.id,
        clip_name=entry.clip_name,
        track_path=entry.track_path,
        reference_kind=entry.reference_kind,
        target_url=entry.target_url,
        normalized_url=entry.normalized_url,
        status=entry.status,
        clip_count=entry.clip_count,
        suggestions=[
            MatchSuggestionPayload(key=suggestion.key, score=suggestion.score)
            for suggestion in suggestions
        ],
    )
