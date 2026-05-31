from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


CONFIG_ENV_VAR = "BASERENDER_MEDIA_EXTENSIONS_CONFIG"
CONFIG_RELATIVE_PATH = Path("config/supported-media-extensions.json")


@dataclass(frozen=True)
class MediaExtensions:
    video: frozenset[str]
    still_image: frozenset[str]
    audio: frozenset[str]

    @property
    def assignable(self) -> frozenset[str]:
        return self.video | self.still_image | self.audio


_FALLBACK_EXTENSIONS = MediaExtensions(
    video=frozenset(
        {
            ".3gp",
            ".avi",
            ".flv",
            ".m2ts",
            ".m4v",
            ".mkv",
            ".mov",
            ".mp4",
            ".mpeg",
            ".mpg",
            ".mxf",
            ".ogv",
            ".ts",
            ".webm",
            ".wmv",
        }
    ),
    still_image=frozenset(
        {
            ".avif",
            ".bmp",
            ".dpx",
            ".exr",
            ".gif",
            ".heic",
            ".heif",
            ".jpeg",
            ".jpg",
            ".png",
            ".tif",
            ".tiff",
            ".webp",
        }
    ),
    audio=frozenset(
        {
            ".aac",
            ".aif",
            ".aiff",
            ".caf",
            ".flac",
            ".m4a",
            ".mp3",
            ".ogg",
            ".opus",
            ".wav",
            ".wma",
        }
    ),
)


def all_assignable_media_extensions() -> frozenset[str]:
    return _load_media_extensions().assignable


def still_image_extensions() -> frozenset[str]:
    return _load_media_extensions().still_image


@lru_cache
def _load_media_extensions() -> MediaExtensions:
    config_path = _config_path()
    if config_path is None:
        return _FALLBACK_EXTENSIONS

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read media extensions config at {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid media extensions config at {config_path}: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise ValueError(f"Media extensions config at {config_path} must be a JSON object.")

    return MediaExtensions(
        video=_normalize_extensions(payload.get("video", ()), config_path=config_path, key="video"),
        still_image=_normalize_extensions(
            payload.get("still_image", ()),
            config_path=config_path,
            key="still_image",
        ),
        audio=_normalize_extensions(payload.get("audio", ()), config_path=config_path, key="audio"),
    )


def _config_path() -> Path | None:
    override = os.getenv(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()

    for parent in Path(__file__).resolve().parents:
        candidate = parent / CONFIG_RELATIVE_PATH
        if candidate.is_file():
            return candidate

    return None


def _normalize_extensions(
    extensions: object,
    *,
    config_path: Path,
    key: str,
) -> frozenset[str]:
    if not isinstance(extensions, list):
        raise ValueError(f"Media extensions config key '{key}' in {config_path} must be a list.")

    normalized: set[str] = set()
    for extension in extensions:
        if not isinstance(extension, str) or not extension.strip():
            raise ValueError(
                f"Media extensions config key '{key}' in {config_path} must contain strings."
            )

        value = extension.strip().casefold()
        if not value.startswith("."):
            value = f".{value}"
        normalized.add(value)

    return frozenset(normalized)
