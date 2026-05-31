from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class MediaConfigResponse(BaseModel):
    provider: str
    allowed_prefix: str
    default_media_prefix: str
    default_output_prefix: str
    default_output_path: str
    default_container: str
    default_width: int
    default_height: int
    default_fps: float
    default_video_codec: str
    default_video_bitrate: int
    default_video_preset: str
    default_video_faststart: bool
    default_audio_codec: str
    default_audio_bitrate: int


class CloudMediaObjectPayload(BaseModel):
    key: str
    size: int
    last_modified: datetime | None = None
    etag: str | None = None


class MatchSuggestionPayload(BaseModel):
    key: str
    score: float


class MediaReferencePayload(BaseModel):
    id: str | None = None
    clip_name: str
    track_path: str = ""
    reference_kind: str = "ExternalReference"
    target_url: str | None = None
    normalized_url: str | None = None
    status: Literal["linked", "empty", "missing", "unsupported"] = "linked"
    clip_count: int = Field(default=1, ge=1)
    suggestions: list[MatchSuggestionPayload] = Field(default_factory=list)


class MediaObjectsResponse(BaseModel):
    provider: str
    prefix: str
    objects: list[CloudMediaObjectPayload]
    next_continuation_token: str | None = None
    object_count: int = 0
    truncated: bool = False


class MediaLinkingRequest(BaseModel):
    prefix: str = ""
    timeline_path: str | None = None
    otio_content_base64: str | None = None
    continuation_token: str | None = None
    max_keys: int | None = Field(default=None, ge=1, le=1000)
    suggestion_limit: int = Field(default=3, ge=1, le=20)
    min_score: float = Field(default=60.0, ge=0, le=100)

    @model_validator(mode="after")
    def require_single_timeline_source(self) -> Self:
        sources = [bool(self.timeline_path), bool(self.otio_content_base64)]
        if sum(sources) != 1:
            raise ValueError("Provide exactly one of timeline_path or otio_content_base64.")
        return self


class MediaLinkingResponse(MediaObjectsResponse):
    references: list[MediaReferencePayload]
