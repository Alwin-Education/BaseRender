from __future__ import annotations

from botocore.exceptions import ClientError

from baserender_api.media.s3 import MediaStorageError, S3MediaProvider, _media_storage_error


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("ContinuationToken") == "next-page":
            return {
                "Contents": [
                    {"Key": "nested/Shot_B.mov", "Size": 456},
                ],
            }

        return {
            "Contents": [
                {"Key": "Shot_A.mov", "Size": 123},
            ],
            "NextContinuationToken": "next-page",
        }


def _client_error(code: str, message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {}},
        "ListObjectsV2",
    )


def test_media_storage_error_maps_no_such_bucket() -> None:
    error = _media_storage_error("demo-bucket", _client_error("NoSuchBucket", "missing"))

    assert isinstance(error, MediaStorageError)
    assert "demo-bucket" in str(error)
    assert "does not exist" in str(error)
    assert "BASERENDER_S3_BUCKET" in str(error)


def test_media_storage_error_maps_access_denied() -> None:
    error = _media_storage_error("demo-bucket", _client_error("AccessDenied", "denied"))

    assert "Could not access S3 bucket" in str(error)


def test_media_storage_error_maps_unknown_client_error() -> None:
    error = _media_storage_error(
        "demo-bucket",
        _client_error("SlowDown", "Please reduce your request rate."),
    )

    assert "Could not list media objects" in str(error)
    assert "Please reduce your request rate." in str(error)


def test_s3_provider_list_all_objects_aggregates_pages() -> None:
    client = FakeS3Client()
    provider = S3MediaProvider(bucket="demo-bucket", client=client)

    result = provider.list_all_objects("nested/", page_size=1000)

    assert [obj.key for obj in result.objects] == ["Shot_A.mov", "nested/Shot_B.mov"]
    assert result.next_continuation_token is None
    assert client.calls == [
        {"Bucket": "demo-bucket", "Prefix": "nested/", "MaxKeys": 1000},
        {
            "Bucket": "demo-bucket",
            "Prefix": "nested/",
            "ContinuationToken": "next-page",
            "MaxKeys": 1000,
        },
    ]
