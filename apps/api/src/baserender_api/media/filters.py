from __future__ import annotations

from pathlib import PurePosixPath

from baserender.media_extensions import all_assignable_media_extensions
from baserender_api.media.provider import CloudMediaObject


def is_media_object(obj: CloudMediaObject) -> bool:
    return (
        obj.size > 0
        and PurePosixPath(obj.key).suffix.casefold() in all_assignable_media_extensions()
    )


def filter_media_objects(objects: tuple[CloudMediaObject, ...]) -> tuple[CloudMediaObject, ...]:
    return tuple(obj for obj in objects if is_media_object(obj))
