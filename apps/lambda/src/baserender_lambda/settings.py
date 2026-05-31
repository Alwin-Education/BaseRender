from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from baserender.timeline_model import RenderSettings


def parse_render_settings(
    payload: Mapping[str, Any],
    *,
    clip_luts: Mapping[str, str] | None = None,
) -> RenderSettings:
    """Parse render settings from a Lambda event settings object."""
    return RenderSettings(
        width=_optional_int(payload.get("width")),
        height=_optional_int(payload.get("height")),
        fps=_optional_float(payload.get("fps")),
        audio_sample_rate=int(payload.get("audio_sample_rate", 48000)),
        audio_channel_layout=str(payload.get("audio_channel_layout", "stereo")),
        clip_luts=_string_mapping(clip_luts or payload.get("clip_luts") or {}),
        video_codec=str(payload.get("video_codec", "h264")),
        video_bitrate=_positive_int(payload.get("video_bitrate"), default=8_000_000),
        video_encoder_preset=str(
            payload.get("video_encoder_preset")
            or payload.get("video_preset")
            or "faster"
        ),
        video_faststart=bool(payload.get("video_faststart", True)),
        audio_codec=str(payload.get("audio_codec", "aac")),
        audio_bitrate=_positive_int(payload.get("audio_bitrate"), default=192_000),
        video_crf=_optional_int(payload.get("video_crf")),
    )


def output_container(settings_payload: Mapping[str, Any]) -> str:
    container = str(settings_payload.get("container") or "mp4").strip().lower()
    return container or "mp4"


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _positive_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("Render settings field 'clip_luts' must be an object.")
    return {str(key): str(item) for key, item in value.items()}
