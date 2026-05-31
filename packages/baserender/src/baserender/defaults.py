from __future__ import annotations

import json
import os
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


CONFIG_ENV_VAR = "BASERENDER_DEFAULTS_CONFIG"
CONFIG_RELATIVE_PATH = Path("config/defaults.json")

ALLOWED_CONTAINERS = frozenset({"mp4", "mov"})
ALLOWED_VIDEO_CODECS = frozenset({"h264", "hevc", "prores"})
ALLOWED_VIDEO_PRESETS = frozenset(
    {
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    }
)
ALLOWED_AUDIO_CODECS = frozenset({"aac", "pcm"})


@dataclass(frozen=True)
class Defaults:
    media_prefix: str
    output_path: str
    container: str
    width: int
    height: int
    fps: float
    video_codec: str
    video_bitrate: int
    video_preset: str
    video_faststart: bool
    audio_codec: str
    audio_bitrate: int


_FALLBACK_DEFAULTS = Defaults(
    media_prefix="",
    output_path="outputs/output.mp4",
    container="mp4",
    width=1920,
    height=1080,
    fps=24.0,
    video_codec="h264",
    video_bitrate=8_000_000,
    video_preset="faster",
    video_faststart=True,
    audio_codec="aac",
    audio_bitrate=192_000,
)


def default_media_prefix() -> str:
    return _load_defaults().media_prefix


def default_output_path() -> str:
    return _load_defaults().output_path


def default_container() -> str:
    return _load_defaults().container


def default_width() -> int:
    return _load_defaults().width


def default_height() -> int:
    return _load_defaults().height


def default_fps() -> float:
    return _load_defaults().fps


def default_video_codec() -> str:
    return _load_defaults().video_codec


def default_video_bitrate() -> int:
    return _load_defaults().video_bitrate


def default_video_preset() -> str:
    return _load_defaults().video_preset


def default_video_faststart() -> bool:
    return _load_defaults().video_faststart


def default_audio_codec() -> str:
    return _load_defaults().audio_codec


def default_audio_bitrate() -> int:
    return _load_defaults().audio_bitrate


@lru_cache
def _load_defaults() -> Defaults:
    config_path = _config_path()
    if config_path is None:
        return _FALLBACK_DEFAULTS

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read defaults config at {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid defaults config at {config_path}: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise ValueError(f"Defaults config at {config_path} must be a JSON object.")

    try:
        output_path = _normalize_output_path(
            _string_value(payload.get("output_path"), "output_path")
        )
    except ValueError:
        output_path = _FALLBACK_DEFAULTS.output_path

    try:
        media_prefix = _normalize_media_prefix(
            _string_value(payload.get("media_prefix"), "media_prefix")
        )
    except ValueError:
        media_prefix = _FALLBACK_DEFAULTS.media_prefix

    return Defaults(
        media_prefix=media_prefix,
        output_path=output_path,
        container=_enum_value(
            payload.get("container"),
            allowed=ALLOWED_CONTAINERS,
            default=_FALLBACK_DEFAULTS.container,
        ),
        width=_positive_int(payload.get("width"), default=_FALLBACK_DEFAULTS.width),
        height=_positive_int(payload.get("height"), default=_FALLBACK_DEFAULTS.height),
        fps=_positive_float(payload.get("fps"), default=_FALLBACK_DEFAULTS.fps),
        video_codec=_enum_value(
            payload.get("video_codec"),
            allowed=ALLOWED_VIDEO_CODECS,
            default=_FALLBACK_DEFAULTS.video_codec,
        ),
        video_bitrate=_positive_int(
            payload.get("video_bitrate"),
            default=_FALLBACK_DEFAULTS.video_bitrate,
        ),
        video_preset=_enum_value(
            payload.get("video_preset"),
            allowed=ALLOWED_VIDEO_PRESETS,
            default=_FALLBACK_DEFAULTS.video_preset,
        ),
        video_faststart=_bool_value(
            payload.get("video_faststart"),
            default=_FALLBACK_DEFAULTS.video_faststart,
        ),
        audio_codec=_enum_value(
            payload.get("audio_codec"),
            allowed=ALLOWED_AUDIO_CODECS,
            default=_FALLBACK_DEFAULTS.audio_codec,
        ),
        audio_bitrate=_positive_int(
            payload.get("audio_bitrate"),
            default=_FALLBACK_DEFAULTS.audio_bitrate,
        ),
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


def _string_value(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Defaults config key '{key}' must be a string.")
    return value


def _positive_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value > 0 else default


def _positive_float(value: object, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    numeric = float(value)
    return numeric if numeric > 0 else default


def _enum_value(value: object, *, allowed: frozenset[str], default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default


def _bool_value(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _normalize_media_prefix(value: str | None) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if raw in {"", "."}:
        return ""
    if raw.startswith("/"):
        raise ValueError("Media prefix must be relative.")

    had_trailing_slash = raw.endswith("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("Media prefix cannot contain '..'.")

    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", "."}:
        return ""
    return f"{normalized}/" if had_trailing_slash else normalized


def _normalize_output_path(value: str | None) -> str:
    raw = (value or "output.mp4").strip().replace("\\", "/")
    if raw in {"", "."}:
        return "output.mp4"
    if raw.startswith("/"):
        raise ValueError("Output path must be relative.")

    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts:
        return "output.mp4"
    if any(part == ".." for part in parts):
        raise ValueError("Output path cannot contain '..'.")

    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", "."}:
        return "output.mp4"
    if normalized.endswith("/"):
        raise ValueError("Output path must include a filename.")
    return normalized
