from __future__ import annotations

import json

import pytest

from baserender.defaults import (
    CONFIG_ENV_VAR,
    _load_defaults,
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


def test_defaults_use_repo_config() -> None:
    _load_defaults.cache_clear()

    try:
        assert default_output_path() == "outputs/output.mp4"
        assert default_container() == "mp4"
        assert default_width() == 1920
        assert default_height() == 1080
        assert default_fps() == 24.0
        assert default_video_codec() == "h264"
        assert default_video_bitrate() == 8_000_000
        assert default_video_preset() == "faster"
        assert default_video_faststart() is True
        assert default_audio_codec() == "aac"
        assert default_audio_bitrate() == 192_000
    finally:
        _load_defaults.cache_clear()


def test_defaults_read_config_values(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    config_path = tmp_path / "defaults.json"
    config_path.write_text(
        json.dumps(
            {
                "output_path": "renders/final.mov",
                "container": "mov",
                "width": 1280,
                "height": 720,
                "fps": 30,
                "video_codec": "hevc",
                "video_bitrate": 12_000_000,
                "video_preset": "medium",
                "video_faststart": False,
                "audio_codec": "pcm",
                "audio_bitrate": 1_536_000,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _load_defaults.cache_clear()

    try:
        assert default_output_path() == "renders/final.mov"
        assert default_container() == "mov"
        assert default_width() == 1280
        assert default_height() == 720
        assert default_fps() == 30.0
        assert default_video_codec() == "hevc"
        assert default_video_bitrate() == 12_000_000
        assert default_video_preset() == "medium"
        assert default_video_faststart() is False
        assert default_audio_codec() == "pcm"
        assert default_audio_bitrate() == 1_536_000
    finally:
        _load_defaults.cache_clear()


def test_defaults_fall_back_on_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_path = tmp_path / "defaults.json"
    config_path.write_text(
        json.dumps(
            {
                "output_path": "../bad.mp4",
                "container": "avi",
                "width": "not-a-number",
                "height": -1,
                "fps": 0,
                "video_codec": "vp9",
                "video_bitrate": 0,
                "video_preset": "turbo",
                "video_faststart": "yes",
                "audio_codec": "opus",
                "audio_bitrate": -500,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _load_defaults.cache_clear()

    try:
        assert default_output_path() == "outputs/output.mp4"
        assert default_container() == "mp4"
        assert default_width() == 1920
        assert default_height() == 1080
        assert default_fps() == 24.0
        assert default_video_codec() == "h264"
        assert default_video_bitrate() == 8_000_000
        assert default_video_preset() == "faster"
        assert default_video_faststart() is True
        assert default_audio_codec() == "aac"
        assert default_audio_bitrate() == 192_000
    finally:
        _load_defaults.cache_clear()
