from __future__ import annotations

from typing import Any

import pytest

from baserender_api.output_storage import S3OutputStore, resolve_output_object_key


def test_resolve_output_object_key_uses_media_and_output_prefixes(
    monkeypatch,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    monkeypatch.setenv("BASERENDER_OUTPUT_PREFIX", "outputs")

    assert (
        resolve_output_object_key("episode-1/final.mp4")
        == "projects/demo/outputs/episode-1/final.mp4"
    )


def test_resolve_output_object_key_accepts_full_allowed_output_prefix(
    monkeypatch,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    monkeypatch.setenv("BASERENDER_OUTPUT_PREFIX", "projects/demo/renders")

    assert resolve_output_object_key("final.mp4") == "projects/demo/renders/final.mp4"


def test_resolve_output_object_key_rejects_bucket_escape(monkeypatch) -> None:
    monkeypatch.setenv("BASERENDER_OUTPUT_PREFIX", "outputs")

    with pytest.raises(ValueError, match="relative"):
        resolve_output_object_key("/final.mp4")

    with pytest.raises(ValueError, match="\\.\\."):
        resolve_output_object_key("../final.mp4")


def test_resolve_output_object_key_accepts_full_prefixed_path(
    monkeypatch,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    monkeypatch.setenv("BASERENDER_OUTPUT_PREFIX", "outputs")

    assert (
        resolve_output_object_key("projects/demo/outputs/episode-1/final.mp4")
        == "projects/demo/outputs/episode-1/final.mp4"
    )
    assert resolve_output_object_key("outputs/episode-1/final.mp4") == (
        "projects/demo/outputs/episode-1/final.mp4"
    )


def test_s3_output_store_round_trips_nested_output_path(fake_s3_client) -> None:
    store = S3OutputStore(bucket="test-bucket", client=fake_s3_client)

    location = store.put_bytes("outputs/episode-1/final.mp4", b"video")

    assert location == "s3://test-bucket/outputs/episode-1/final.mp4"
    assert store.size("outputs/episode-1/final.mp4") == 5


def test_s3_output_store_presigns_put_url() -> None:
    class FakeS3Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
            self.calls.append((operation, kwargs))
            return "https://s3.example/upload"

    client = FakeS3Client()
    store = S3OutputStore(bucket="media-bucket", client=client)

    url = store.presign_put_url(
        "outputs/final.mp4",
        content_type="video/mp4",
        expires_in=900,
    )

    assert url == "https://s3.example/upload"
    assert client.calls == [
        (
            "put_object",
            {
                "Params": {
                    "Bucket": "media-bucket",
                    "Key": "outputs/final.mp4",
                    "ContentType": "video/mp4",
                },
                "ExpiresIn": 900,
            },
        )
    ]
