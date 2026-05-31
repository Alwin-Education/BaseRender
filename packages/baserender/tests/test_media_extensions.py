from __future__ import annotations

import json

from baserender.media_extensions import (
    CONFIG_ENV_VAR,
    _load_media_extensions,
    all_assignable_media_extensions,
    still_image_extensions,
)


def test_repo_media_extensions_include_common_formats() -> None:
    _load_media_extensions.cache_clear()

    assert ".png" in all_assignable_media_extensions()
    assert ".mov" in all_assignable_media_extensions()
    assert ".wav" in all_assignable_media_extensions()
    assert ".png" in still_image_extensions()


def test_media_extensions_config_override_normalizes_values(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "media-extensions.json"
    config_path.write_text(
        json.dumps(
            {
                "video": ["MP4"],
                "still_image": ["png", ".JPG"],
                "audio": [".WAV"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _load_media_extensions.cache_clear()

    try:
        assert all_assignable_media_extensions() == frozenset({".mp4", ".png", ".jpg", ".wav"})
        assert still_image_extensions() == frozenset({".png", ".jpg"})
    finally:
        _load_media_extensions.cache_clear()
