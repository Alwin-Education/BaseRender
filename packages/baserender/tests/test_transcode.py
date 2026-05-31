from __future__ import annotations

import pytest

from baserender.transcode import build_transcode_output_key


def test_build_transcode_output_key_preserves_directory() -> None:
    assert (
        build_transcode_output_key(
            "projects/demo/day1/clipA.mov",
            container="mp4",
        )
        == "projects/demo/day1/clipA.mp4"
    )


def test_build_transcode_output_key_prepend_folder() -> None:
    assert (
        build_transcode_output_key(
            "projects/demo/day1/clipA.mov",
            container="mp4",
            prepend_folder="proxies",
        )
        == "proxies/projects/demo/day1/clipA.mp4"
    )


def test_build_transcode_output_key_append_folder() -> None:
    assert (
        build_transcode_output_key(
            "projects/demo/day1/clipA.mov",
            container="mp4",
            append_folder="proxies",
        )
        == "projects/demo/day1/proxies/clipA.mp4"
    )


def test_build_transcode_output_key_prepend_and_append() -> None:
    assert (
        build_transcode_output_key(
            "projects/demo/day1/clipA.mov",
            container="mov",
            prepend_folder="out",
            append_folder="proxies",
        )
        == "out/projects/demo/day1/proxies/clipA.mov"
    )


def test_build_transcode_output_key_root_level_file() -> None:
    assert (
        build_transcode_output_key(
            "clipA.mov",
            container="mp4",
            prepend_folder="proxies",
        )
        == "proxies/clipA.mp4"
    )


def test_build_transcode_output_key_strips_slashes() -> None:
    assert (
        build_transcode_output_key(
            "/projects/demo/clipA.mov",
            container="mp4",
            prepend_folder="/proxies/",
            append_folder="/renders/",
        )
        == "proxies/projects/demo/renders/clipA.mp4"
    )


def test_build_transcode_output_key_rejects_empty_source() -> None:
    with pytest.raises(ValueError, match="source_key"):
        build_transcode_output_key("", container="mp4")
