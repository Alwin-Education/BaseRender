from __future__ import annotations

import pytest

from baserender_api.media.prefix import (
    enforce_allowed_prefix,
    normalize_s3_prefix,
    validate_media_object_key,
)


def test_normalize_s3_prefix_rejects_bucket_escape() -> None:
    with pytest.raises(ValueError, match="relative"):
        normalize_s3_prefix("/absolute")

    with pytest.raises(ValueError, match="\\.\\."):
        normalize_s3_prefix("project/../secret")


def test_enforce_allowed_prefix_roots_relative_requests() -> None:
    assert enforce_allowed_prefix("", "projects/demo") == "projects/demo/"
    assert enforce_allowed_prefix("day1/footage/", "projects/demo") == "projects/demo/day1/footage/"
    assert enforce_allowed_prefix("projects/demo/day1", "projects/demo") == "projects/demo/day1"


def test_validate_media_object_key_rejects_outside_allowed_root() -> None:
    with pytest.raises(ValueError, match="allowed prefix"):
        validate_media_object_key("other/clipA.mov", "projects/demo")

    assert (
        validate_media_object_key("projects/demo/day1/clipA.mov", "projects/demo")
        == "projects/demo/day1/clipA.mov"
    )
