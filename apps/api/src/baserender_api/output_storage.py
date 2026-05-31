from __future__ import annotations

import os
import posixpath
from typing import Protocol

from baserender_api.defaults import allowed_media_prefix
from baserender_api.media.prefix import enforce_allowed_prefix, normalize_s3_prefix


class OutputStore(Protocol):
    def put_bytes(self, key: str, data: bytes) -> str:
        ...

    def location(self, key: str) -> str:
        ...

    def size(self, key: str) -> int | None:
        ...


class S3OutputStore:
    def __init__(self, *, bucket: str, client: object | None = None) -> None:
        self.bucket = bucket
        self._client = client or self._create_client()

    def put_bytes(self, key: str, data: bytes) -> str:
        object_key = _safe_relative_key(key)
        self._client.put_object(Bucket=self.bucket, Key=object_key, Body=data)
        return self.location(object_key)

    def presign_put_url(
        self,
        key: str,
        *,
        content_type: str = "video/mp4",
        expires_in: int = 3600,
    ) -> str:
        object_key = _safe_relative_key(key)
        return self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )

    def location(self, key: str) -> str:
        return f"s3://{self.bucket}/{_safe_relative_key(key)}"

    def size(self, key: str) -> int | None:
        response = self._client.head_object(Bucket=self.bucket, Key=_safe_relative_key(key))
        content_length = response.get("ContentLength")
        return int(content_length) if content_length is not None else None

    def _create_client(self) -> object:
        import boto3

        return boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        )


def resolve_output_object_key(output_path: str | None) -> str:
    user_path = _normalize_output_path(output_path)
    output_prefix = normalize_s3_prefix(os.getenv("BASERENDER_OUTPUT_PREFIX", "outputs"))
    media_prefix = allowed_media_prefix()
    effective_prefix = enforce_allowed_prefix(output_prefix, media_prefix)
    if not effective_prefix:
        return user_path
    if _path_has_prefix(user_path, effective_prefix):
        return user_path
    if output_prefix and _path_has_prefix(user_path, output_prefix):
        return enforce_allowed_prefix(user_path, media_prefix) or user_path
    return f"{effective_prefix.rstrip('/')}/{user_path}"


def get_output_store() -> OutputStore:
    bucket = os.getenv("BASERENDER_S3_BUCKET")
    if not bucket:
        raise ValueError("BASERENDER_S3_BUCKET must be set.")
    return S3OutputStore(bucket=bucket)


def get_output_upload_target(
    key: str,
    *,
    content_type: str = "video/mp4",
    expires_in: int = 3600,
) -> dict[str, object]:
    store = get_output_store()
    if not isinstance(store, S3OutputStore):
        raise ValueError("S3 render outputs are not configured correctly.")
    object_key = _safe_relative_key(key)
    return {
        "url": store.presign_put_url(
            key,
            content_type=content_type,
            expires_in=expires_in,
        ),
        "key": object_key,
        "headers": {"Content-Type": content_type},
    }


def _normalize_output_path(value: str | None) -> str:
    raw = (value or "output.mp4").strip().replace("\\", "/")
    if raw in {"", "."}:
        return "output.mp4"
    if raw.startswith("/"):
        raise ValueError("Output path must be relative.")

    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts:
        return "output.mp4"
    if any(part == ".." for part in parts):
        raise ValueError("Output path cannot contain '..'.")

    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", "."}:
        return "output.mp4"
    if normalized.endswith("/"):
        raise ValueError("Output path must include a filename.")
    return normalized


def _safe_relative_key(value: str) -> str:
    return _normalize_output_path(value)


def _path_has_prefix(path: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/")
    if not normalized_prefix:
        return False
    if path == normalized_prefix:
        return True
    return path.startswith(f"{normalized_prefix}/")
