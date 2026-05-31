from __future__ import annotations

import os
from typing import Any

from baserender_api.media.provider import CloudMediaObject, ListObjectsResult


DEFAULT_LIST_PAGE_SIZE = 1000
DEFAULT_MAX_LISTED_OBJECTS = 10_000


class MediaStorageError(Exception):
    """Raised when media storage cannot be accessed."""


class S3MediaProvider:
    id = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self._client = client or self._create_client(region_name=region_name)

    @classmethod
    def from_env(cls) -> S3MediaProvider:
        bucket = os.getenv("BASERENDER_S3_BUCKET") or os.getenv("S3_BUCKET")
        if not bucket:
            raise ValueError("BASERENDER_S3_BUCKET must be set for S3 media linking.")
        return cls(
            bucket=bucket,
            region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        )

    def list_objects(
        self,
        prefix: str,
        *,
        continuation_token: str | None = None,
        max_keys: int | None = None,
    ) -> ListObjectsResult:
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Prefix": prefix,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        if max_keys is not None:
            kwargs["MaxKeys"] = max_keys

        try:
            response = self._client.list_objects_v2(**kwargs)
        except Exception as exc:
            raise _media_storage_error(self.bucket, exc) from exc

        objects = tuple(
            CloudMediaObject(
                key=str(item["Key"]),
                size=int(item.get("Size", 0)),
                last_modified=item.get("LastModified"),
                etag=_strip_etag_quotes(item.get("ETag")),
            )
            for item in response.get("Contents", [])
        )
        return ListObjectsResult(
            objects=objects,
            next_continuation_token=response.get("NextContinuationToken"),
        )

    def list_all_objects(
        self,
        prefix: str,
        *,
        page_size: int = DEFAULT_LIST_PAGE_SIZE,
        max_objects: int = DEFAULT_MAX_LISTED_OBJECTS,
    ) -> ListObjectsResult:
        objects: list[CloudMediaObject] = []
        continuation_token: str | None = None

        while len(objects) < max_objects:
            result = self.list_objects(
                prefix,
                continuation_token=continuation_token,
                max_keys=page_size,
            )
            objects.extend(result.objects)
            continuation_token = result.next_continuation_token
            if not continuation_token:
                break

        return ListObjectsResult(
            objects=tuple(objects),
            next_continuation_token=continuation_token if len(objects) >= max_objects else None,
        )

    def presign_get_url(self, key: str, *, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def _create_client(self, *, region_name: str | None) -> Any:
        import boto3

        return boto3.client("s3", region_name=region_name)


def _strip_etag_quotes(value: str | None) -> str | None:
    return value.strip('"') if value else None


def _media_storage_error(bucket: str, exc: Exception) -> MediaStorageError:
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return MediaStorageError(f"Could not list media objects from S3 bucket {bucket!r}: {exc}")

    if not isinstance(exc, ClientError):
        return MediaStorageError(f"Could not list media objects from S3 bucket {bucket!r}: {exc}")

    error = exc.response.get("Error", {})
    code = error.get("Code", "")
    message = error.get("Message", str(exc))

    if code == "NoSuchBucket":
        return MediaStorageError(
            f"The configured S3 bucket {bucket!r} does not exist. "
            "Check BASERENDER_S3_BUCKET in the API environment."
        )
    if code in {"AccessDenied", "AllAccessDisabled"}:
        return MediaStorageError(
            f"Could not access S3 bucket {bucket!r}. Check AWS credentials and bucket permissions."
        )
    if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch"}:
        return MediaStorageError(
            "Could not authenticate with S3. Check AWS credentials in the API environment."
        )

    return MediaStorageError(
        f"Could not list media objects from S3 bucket {bucket!r}: {message}"
    )
