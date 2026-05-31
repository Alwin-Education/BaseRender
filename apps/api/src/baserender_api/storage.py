from __future__ import annotations

import os
from typing import Protocol


class ArtifactStore(Protocol):
    def put_bytes(self, key: str, data: bytes) -> str:
        ...

    def get_bytes(self, key: str) -> bytes:
        ...

    def location(self, key: str) -> str:
        ...


class S3ArtifactStore:
    def __init__(self, *, bucket: str, prefix: str = "", client: object | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = client or self._create_client()

    def put_bytes(self, key: str, data: bytes) -> str:
        object_key = self._object_key(key)
        self._client.put_object(Bucket=self.bucket, Key=object_key, Body=data)
        return f"s3://{self.bucket}/{object_key}"

    def get_bytes(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=self._object_key(key))
        return response["Body"].read()

    def location(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._object_key(key)}"

    def _object_key(self, key: str) -> str:
        normalized = key.strip("/")
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def _create_client(self) -> object:
        import boto3

        return boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        )


def get_artifact_store() -> ArtifactStore:
    bucket = os.getenv("BASERENDER_S3_BUCKET")
    if not bucket:
        raise ValueError("BASERENDER_S3_BUCKET must be set.")
    return S3ArtifactStore(
        bucket=bucket,
        prefix=os.getenv("BASERENDER_ARTIFACT_PREFIX", "baserender"),
    )
