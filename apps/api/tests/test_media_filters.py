from __future__ import annotations

from baserender_api.media.filters import filter_media_objects, is_media_object
from baserender_api.media.provider import CloudMediaObject


def test_is_media_object_accepts_supported_extensions_case_insensitively() -> None:
    assert is_media_object(CloudMediaObject(key="folder/Shot_A.MOV", size=123))
    assert is_media_object(CloudMediaObject(key="folder/plate.PNG", size=123))
    assert is_media_object(CloudMediaObject(key="folder/reference.Jpg", size=123))


def test_filter_media_objects_excludes_non_media_and_folder_markers() -> None:
    objects = (
        CloudMediaObject(key="test/", size=0),
        CloudMediaObject(key="test/Shot_A.mov", size=123),
        CloudMediaObject(key="test/plate.png", size=234),
        CloudMediaObject(key="test/metadata.json", size=456),
    )

    filtered = filter_media_objects(objects)

    assert filtered == (
        CloudMediaObject(key="test/Shot_A.mov", size=123),
        CloudMediaObject(key="test/plate.png", size=234),
    )
