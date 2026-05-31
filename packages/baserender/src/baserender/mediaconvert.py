"""Build AWS MediaConvert CreateJob Settings payloads from routing plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from baserender.routing import RoutingPlan, ShotRouting
from baserender.timeline_model import RenderSettings

# Default color spaces for creative SDR .cube LUTs.
DEFAULT_LUT_INPUT_COLOR_SPACE = "REC_709"
DEFAULT_LUT_OUTPUT_COLOR_SPACE = "REC_709"

_MEDIACONVERT_CONTAINER = {
    "mp4": "MP4",
    "mov": "MOV",
}

_FFMPEG_PRESET_TO_H264_QUALITY = {
    "ultrafast": "SINGLE_PASS",
    "superfast": "SINGLE_PASS",
    "veryfast": "SINGLE_PASS",
    "faster": "SINGLE_PASS",
    "fast": "SINGLE_PASS",
    "medium": "SINGLE_PASS_HQ",
    "slow": "SINGLE_PASS_HQ",
    "slower": "SINGLE_PASS_HQ",
    "veryslow": "MULTI_PASS_HQ",
}


def seconds_to_timecode(seconds: float, fps: float) -> str:
    """Convert seconds to MediaConvert HH:MM:SS:FF timecode."""
    if fps <= 0:
        raise ValueError("fps must be positive")

    fps_int = max(1, round(fps))
    total_frames = round(seconds * fps)
    frames = total_frames % fps_int
    total_seconds = total_frames // fps_int
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def build_full_render_job(
    routing: RoutingPlan,
    *,
    media_uris: Mapping[str, str],
    output_destination: str,
    settings: RenderSettings,
    lut_uri: str | None = None,
    container: str = "mp4",
) -> dict[str, Any]:
    """Build a single MediaConvert job that renders the full timeline."""
    fps = _resolve_fps(settings)
    inputs = [
        _build_clipped_input(
            media_uris[shot.media_url],
            source_in_seconds=shot.source_in_seconds,
            source_out_seconds=shot.source_out_seconds,
            fps=fps,
        )
        for shot in routing.shots
    ]
    return _build_job_settings(
        inputs,
        output_destination=output_destination,
        settings=settings,
        container=container,
        lut_uri=lut_uri,
    )


def build_per_shot_lut_job(
    shot: ShotRouting,
    *,
    media_uri: str,
    lut_uri: str,
    output_destination: str,
    settings: RenderSettings,
    container: str = "mp4",
) -> dict[str, Any]:
    """Build a MediaConvert job that applies one LUT to a single shot."""
    fps = _resolve_fps(settings)
    inputs = [
        _build_clipped_input(
            media_uri,
            source_in_seconds=shot.source_in_seconds,
            source_out_seconds=shot.source_out_seconds,
            fps=fps,
        )
    ]
    return _build_job_settings(
        inputs,
        output_destination=output_destination,
        settings=settings,
        container=container,
        lut_uri=lut_uri,
    )


def build_truncation_job(
    shot: ShotRouting,
    *,
    media_uri: str,
    output_destination: str,
    settings: RenderSettings,
    container: str = "mp4",
) -> dict[str, Any]:
    """Build a MediaConvert job that truncates source media for Lambda proxies."""
    fps = _resolve_fps(settings)
    inputs = [
        _build_clipped_input(
            media_uri,
            source_in_seconds=shot.source_in_seconds,
            source_out_seconds=shot.source_out_seconds,
            fps=fps,
        )
    ]
    return _build_job_settings(
        inputs,
        output_destination=output_destination,
        settings=settings,
        container=container,
        lut_uri=None,
    )


def build_stitch_job(
    part_uris: Sequence[str],
    *,
    output_destination: str,
    settings: RenderSettings,
    container: str = "mp4",
) -> dict[str, Any]:
    """Build a MediaConvert job that concatenates intermediate outputs."""
    inputs = [_build_stitched_input(uri) for uri in part_uris]
    return _build_job_settings(
        inputs,
        output_destination=output_destination,
        settings=settings,
        container=container,
        lut_uri=None,
    )


def build_transcode_job(
    media_uri: str,
    *,
    output_destination: str,
    settings: RenderSettings,
    container: str = "mp4",
) -> dict[str, Any]:
    """Build a MediaConvert job that transcodes a single source file."""
    inputs = [_build_stitched_input(media_uri)]
    return _build_job_settings(
        inputs,
        output_destination=output_destination,
        settings=settings,
        container=container,
        lut_uri=None,
    )


def _build_job_settings(
    inputs: list[dict[str, Any]],
    *,
    output_destination: str,
    settings: RenderSettings,
    container: str,
    lut_uri: str | None,
) -> dict[str, Any]:
    job_settings: dict[str, Any] = {
        "Inputs": inputs,
        "OutputGroups": [
            _build_output_group(
                output_destination,
                settings=settings,
                container=container,
                apply_lut=lut_uri is not None,
            )
        ],
    }
    if lut_uri is not None:
        job_settings["ColorConversion3DLUTSettings"] = [_lut_setting(lut_uri)]
    return job_settings


def _build_clipped_input(
    file_input: str,
    *,
    source_in_seconds: float,
    source_out_seconds: float,
    fps: float,
) -> dict[str, Any]:
    return {
        "FileInput": file_input,
        "TimecodeSource": "ZEROBASED",
        "InputClippings": [
            {
                "StartTimecode": seconds_to_timecode(source_in_seconds, fps),
                "EndTimecode": seconds_to_timecode(source_out_seconds, fps),
            }
        ],
        "AudioSelectors": {
            "Audio Selector 1": {
                "DefaultSelection": "DEFAULT",
            }
        },
    }


def _build_stitched_input(file_input: str) -> dict[str, Any]:
    return {
        "FileInput": file_input,
        "AudioSelectors": {
            "Audio Selector 1": {
                "DefaultSelection": "DEFAULT",
            }
        },
    }


def _build_output_group(
    destination: str,
    *,
    settings: RenderSettings,
    container: str,
    apply_lut: bool,
) -> dict[str, Any]:
    return {
        "Name": "File Group",
        "OutputGroupSettings": {
            "Type": "FILE_GROUP_SETTINGS",
            "FileGroupSettings": {
                "Destination": destination,
            },
        },
        "Outputs": [
            _build_output(settings, container=container, apply_lut=apply_lut),
        ],
    }


def _build_output(
    settings: RenderSettings,
    *,
    container: str,
    apply_lut: bool,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "ContainerSettings": _container_settings(settings, container),
        "VideoDescription": _video_description(settings, apply_lut=apply_lut),
        "AudioDescriptions": [_audio_description(settings)],
    }
    return output


def _container_settings(settings: RenderSettings, container: str) -> dict[str, Any]:
    mc_container = _MEDIACONVERT_CONTAINER.get(container.lower(), "MP4")
    container_settings: dict[str, Any] = {"Container": mc_container}
    if mc_container == "MP4" and settings.video_faststart:
        container_settings["Mp4Settings"] = {
            "MoovPlacement": "PROGRESSIVE_DOWNLOAD",
        }
    return container_settings


def _video_description(settings: RenderSettings, *, apply_lut: bool) -> dict[str, Any]:
    description: dict[str, Any] = {
        "Width": settings.width or 1920,
        "Height": settings.height or 1080,
        "CodecSettings": _video_codec_settings(settings),
        "ScalingBehavior": "DEFAULT",
    }
    if apply_lut:
        description["VideoPreprocessors"] = {
            "ColorCorrector": {
                "ColorSpaceConversion": "FORCE_709",
            }
        }
    return description


def _video_codec_settings(settings: RenderSettings) -> dict[str, Any]:
    codec = settings.video_codec.lower()
    fps_settings = _fps_codec_settings(settings)

    if codec == "hevc":
        return {
            "Codec": "H_265",
            "H265Settings": {
                "RateControlMode": "CBR",
                "Bitrate": settings.video_bitrate,
                **fps_settings,
            },
        }
    if codec == "prores":
        return {
            "Codec": "PRORES",
            "ProresSettings": {
                "CodecProfile": "APPLE_PRORES_422",
                **fps_settings,
            },
        }

    quality = _FFMPEG_PRESET_TO_H264_QUALITY.get(
        settings.video_encoder_preset,
        "SINGLE_PASS",
    )
    return {
        "Codec": "H_264",
        "H264Settings": {
            "RateControlMode": "CBR",
            "Bitrate": settings.video_bitrate,
            "CodecProfile": "HIGH",
            "QualityTuningLevel": quality,
            **fps_settings,
        },
    }


def _audio_description(settings: RenderSettings) -> dict[str, Any]:
    codec = settings.audio_codec.lower()
    if codec == "pcm":
        return {
            "CodecSettings": {
                "Codec": "WAV",
                "WavSettings": {
                    "BitDepth": 16,
                    "SampleRate": settings.audio_sample_rate,
                },
            },
        }

    return {
        "CodecSettings": {
            "Codec": "AAC",
            "AacSettings": {
                "Bitrate": settings.audio_bitrate,
                "CodingMode": _audio_coding_mode(settings.audio_channel_layout),
                "SampleRate": settings.audio_sample_rate,
                "RateControlMode": "CBR",
            },
        },
    }


def _audio_coding_mode(channel_layout: str) -> str:
    if channel_layout.lower() in {"mono", "1.0"}:
        return "CODING_MODE_1_0"
    return "CODING_MODE_2_0"


def _fps_codec_settings(settings: RenderSettings) -> dict[str, Any]:
    if settings.fps is None:
        return {"FramerateControl": "INITIALIZE_FROM_SOURCE"}

    fps = settings.fps
    if abs(fps - (24000 / 1001)) < 0.01:
        return {
            "FramerateControl": "SPECIFIED",
            "FramerateNumerator": 24000,
            "FramerateDenominator": 1001,
        }
    if abs(fps - (30000 / 1001)) < 0.01:
        return {
            "FramerateControl": "SPECIFIED",
            "FramerateNumerator": 30000,
            "FramerateDenominator": 1001,
        }

    fps_int = round(fps)
    return {
        "FramerateControl": "SPECIFIED",
        "FramerateNumerator": fps_int,
        "FramerateDenominator": 1,
    }


def _lut_setting(lut_uri: str) -> dict[str, Any]:
    return {
        "FileInput": lut_uri,
        "InputColorSpace": DEFAULT_LUT_INPUT_COLOR_SPACE,
        "InputMasteringLuminance": 0,
        "OutputColorSpace": DEFAULT_LUT_OUTPUT_COLOR_SPACE,
        "OutputMasteringLuminance": 0,
    }


def _resolve_fps(settings: RenderSettings) -> float:
    if settings.fps is not None:
        return settings.fps
    return 24.0
