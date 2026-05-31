from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from baserender.media_extensions import still_image_extensions
from baserender.timeline_model import (
    AudioClipSegment,
    ClipSegment,
    RenderSettings,
    normalize_target_url,
)


@dataclass(frozen=True)
class _MediaInputKey:
    url: str
    still_loop: bool
    still_framerate: str | None


class FFmpegInputRegistry:
    """Deduplicate FFmpeg ``-i`` inputs by normalized media URL and still-image options."""

    def __init__(self, args: list[str], settings: RenderSettings) -> None:
        self._args = args
        self._settings = settings
        self._index_by_key: dict[_MediaInputKey, int] = {}

    @property
    def args(self) -> list[str]:
        return self._args

    def append_clip_input(self, segment: ClipSegment) -> int:
        return self._get_or_append_media(
            segment.media_url,
            still_image=_is_still_image_url(segment.media_url),
        )

    def append_audio_clip_input(self, segment: AudioClipSegment) -> int:
        return self._get_or_append_media(segment.media_url, still_image=False)

    def _get_or_append_media(self, media_url: str, *, still_image: bool) -> int:
        normalized_url = normalize_target_url(media_url)
        still_framerate: str | None = None
        still_loop = still_image
        if still_image and self._settings.fps is not None:
            still_framerate = _format_seconds(self._settings.fps)

        key = _MediaInputKey(
            url=normalized_url,
            still_loop=still_loop,
            still_framerate=still_framerate,
        )
        existing = self._index_by_key.get(key)
        if existing is not None:
            return existing

        input_index = _current_input_count(self._args)
        if still_loop:
            self._args.extend(["-loop", "1"])
            if still_framerate is not None:
                self._args.extend(["-framerate", still_framerate])
        self._args.extend(["-i", normalized_url])
        self._index_by_key[key] = input_index
        return input_index


def _is_still_image_url(media_url: str) -> bool:
    parsed = urlparse(media_url)
    return Path(parsed.path).suffix.lower() in still_image_extensions()


def _current_input_count(args: list[str]) -> int:
    return sum(1 for arg in args if arg == "-i")


def _format_seconds(value: float) -> str:
    if value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")
