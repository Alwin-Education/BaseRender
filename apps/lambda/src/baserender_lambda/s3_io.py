from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class S3Io(Protocol):
    def download(self, key: str, destination: Path) -> None: ...

    def upload(
        self,
        path: Path,
        key: str,
        *,
        content_type: str = "video/mp4",
    ) -> None: ...


class BotoS3Io:
    def __init__(self, bucket: str, *, client: Any | None = None) -> None:
        self.bucket = bucket
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def download(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))

    def upload(
        self,
        path: Path,
        key: str,
        *,
        content_type: str = "video/mp4",
    ) -> None:
        extra_args = {"ContentType": content_type}
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra_args)
