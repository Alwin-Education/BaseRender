from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class CloudMediaObject:
    key: str
    size: int
    last_modified: datetime | None = None
    etag: str | None = None


@dataclass(frozen=True)
class ListObjectsResult:
    objects: tuple[CloudMediaObject, ...]
    next_continuation_token: str | None = None


class CloudMediaProvider(Protocol):
    id: str

    def list_objects(
        self,
        prefix: str,
        *,
        continuation_token: str | None = None,
        max_keys: int | None = None,
    ) -> ListObjectsResult:
        ...

    def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        ...


def get_media_provider() -> CloudMediaProvider:
    provider = os.getenv("BASERENDER_MEDIA_PROVIDER", "s3").strip().lower()
    if provider != "s3":
        raise ValueError(f"Unsupported media provider: {provider}")

    from baserender_api.media.s3 import S3MediaProvider

    return S3MediaProvider.from_env()
