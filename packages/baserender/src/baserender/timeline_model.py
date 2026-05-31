from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias
from urllib.parse import unquote, urlparse

from baserender.animation import ClipAnimation, DissolveCurve


class BaseRenderError(Exception):
    """Base exception for expected converter failures."""


class UnsupportedTimelineError(BaseRenderError):
    """Raised when an OTIO timeline uses a feature this prototype does not support."""


class MediaReferenceError(BaseRenderError):
    """Raised when a clip cannot be resolved to renderable media."""


@dataclass(frozen=True)
class ClipTransform:
    """Static clip transform in output-canvas pixel space."""

    scale_x: float = 1.0
    scale_y: float = 1.0
    translate_x: float = 0.0
    translate_y: float = 0.0
    rotation_degrees: float = 0.0

    @property
    def is_identity(self) -> bool:
        return (
            self.scale_x == 1.0
            and self.scale_y == 1.0
            and self.translate_x == 0.0
            and self.translate_y == 0.0
            and self.rotation_degrees == 0.0
        )


@dataclass(frozen=True)
class ClipCrop:
    """Static post-transform crop as normalized canvas-edge insets (0..1)."""

    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0

    @property
    def is_identity(self) -> bool:
        return self.left == 0.0 and self.right == 0.0 and self.top == 0.0 and self.bottom == 0.0


@dataclass(frozen=True)
class ClipSegment:
    name: str
    media_url: str
    start_seconds: float
    duration_seconds: float
    lut_path: str | None = None
    transform: ClipTransform | None = None
    crop: ClipCrop | None = None
    animation: ClipAnimation | None = None

    @property
    def has_animation(self) -> bool:
        return self.animation is not None and not self.animation.is_identity


@dataclass(frozen=True)
class GapSegment:
    name: str
    duration_seconds: float


@dataclass(frozen=True)
class DissolveTransitionSegment:
    """Dissolve between the tail of one clip and the head of the next."""

    name: str
    duration_seconds: float
    outgoing: ClipSegment
    incoming: ClipSegment
    dissolve_curve: DissolveCurve | None = None


TimelineSegment: TypeAlias = ClipSegment | GapSegment | DissolveTransitionSegment


@dataclass(frozen=True)
class AudioClipSegment:
    name: str
    media_url: str
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class AudioGapSegment:
    name: str
    duration_seconds: float


@dataclass(frozen=True)
class DissolveAudioTransitionSegment:
    """Linear crossfade between the tail of one clip and the head of the next."""

    name: str
    duration_seconds: float
    outgoing: AudioClipSegment
    incoming: AudioClipSegment


AudioSegment: TypeAlias = (
    AudioClipSegment | AudioGapSegment | DissolveAudioTransitionSegment
)


@dataclass(frozen=True)
class AudioTimelineTrack:
    name: str
    segments: tuple[AudioSegment, ...]

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)


@dataclass(frozen=True)
class VideoTimelineTrack:
    name: str
    segments: tuple[TimelineSegment, ...]

    @property
    def duration_seconds(self) -> float:
        return sum(segment.duration_seconds for segment in self.segments)

    @property
    def has_gaps(self) -> bool:
        return any(isinstance(segment, GapSegment) for segment in self.segments)


@dataclass(frozen=True)
class TimelinePlan:
    name: str
    source_path: Path
    track_name: str
    segments: tuple[TimelineSegment, ...]
    video_tracks: tuple[VideoTimelineTrack, ...] = ()
    audio_tracks: tuple[AudioTimelineTrack, ...] = ()

    @property
    def effective_video_tracks(self) -> tuple[VideoTimelineTrack, ...]:
        if self.video_tracks:
            return self.video_tracks
        return (VideoTimelineTrack(self.track_name, self.segments),)

    @property
    def has_multiple_video_tracks(self) -> bool:
        return len(self.effective_video_tracks) > 1

    @property
    def duration_seconds(self) -> float:
        tracks = self.effective_video_tracks
        if len(tracks) > 1:
            video_duration = max(track.duration_seconds for track in tracks)
        else:
            video_duration = sum(segment.duration_seconds for segment in self.segments)

        if self.audio_tracks:
            audio_duration = max(track.duration_seconds for track in self.audio_tracks)
            return max(video_duration, audio_duration)
        return video_duration

    @property
    def has_gaps(self) -> bool:
        return any(track.has_gaps for track in self.effective_video_tracks)


@dataclass(frozen=True)
class RenderSettings:
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_sample_rate: int = 48000
    audio_channel_layout: str = "stereo"
    clip_luts: Mapping[str, str] = field(default_factory=dict)
    video_codec: str = "h264"
    video_bitrate: int = 8_000_000
    video_encoder_preset: str = "faster"
    video_faststart: bool = True
    audio_codec: str = "aac"
    audio_bitrate: int = 192_000
    video_crf: int | None = None

    @property
    def can_render_gaps(self) -> bool:
        return self.width is not None and self.height is not None and self.fps is not None


def parse_clip_lut_mapping(value: str) -> tuple[str, str]:
    """Parse ``SOURCE=LUT`` from a repeatable ``--clip-lut`` flag value."""
    if "=" not in value:
        raise ValueError(
            f"Invalid --clip-lut value {value!r}: expected SOURCE=LUT, for example "
            "'/media/a.mov=/looks/a.cube'."
        )

    source, lut_path = value.split("=", 1)
    source = source.strip()
    lut_path = lut_path.strip()
    if not source or not lut_path:
        raise ValueError(
            f"Invalid --clip-lut value {value!r}: source URL and LUT path must be non-empty."
        )
    return source, lut_path


def parse_clip_lut_mappings(values: list[str]) -> dict[str, str]:
    """Build a source-URL-to-LUT mapping from repeatable CLI values."""
    mappings: dict[str, str] = {}
    for value in values:
        source, lut_path = parse_clip_lut_mapping(value)
        mappings[source] = lut_path
    return mappings


def normalize_target_url(target_url: str) -> str:
    """Convert common OTIO file URLs into FFmpeg-friendly paths."""
    parsed = urlparse(target_url)

    if parsed.scheme == "":
        return target_url

    if parsed.scheme != "file":
        return target_url

    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc != "localhost":
        return f"//{parsed.netloc}{path}"
    return path
