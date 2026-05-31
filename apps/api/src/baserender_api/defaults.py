from baserender.defaults import (
    default_audio_bitrate,
    default_audio_codec,
    default_container,
    default_fps,
    default_height,
    default_media_prefix,
    default_output_path,
    default_video_bitrate,
    default_video_codec,
    default_video_faststart,
    default_video_preset,
    default_width,
)
from baserender_api.media.prefix import normalize_s3_prefix


def allowed_media_prefix() -> str:
    value = default_media_prefix()
    if not value.strip():
        return ""
    return normalize_s3_prefix(value)


__all__ = [
    "allowed_media_prefix",
    "default_audio_bitrate",
    "default_audio_codec",
    "default_container",
    "default_fps",
    "default_height",
    "default_media_prefix",
    "default_output_path",
    "default_video_bitrate",
    "default_video_codec",
    "default_video_faststart",
    "default_video_preset",
    "default_width",
]
