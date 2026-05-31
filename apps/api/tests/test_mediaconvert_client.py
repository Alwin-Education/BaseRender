from __future__ import annotations

from typing import Any

import pytest

from baserender_api.mediaconvert_client import (
    BotoMediaConvertClient,
    MediaConvertError,
    get_mediaconvert_client,
)


class FakeMediaConvertClient:
    def __init__(self) -> None:
        self.create_job_calls: list[dict[str, Any]] = []
        self.get_job_calls: list[str] = []
        self.describe_endpoints_calls = 0
        self.endpoint_url = "https://abc123.mediaconvert.us-east-1.amazonaws.com"

    def create_job(self, **kwargs: Any) -> dict[str, Any]:
        self.create_job_calls.append(kwargs)
        return {"Job": {"Id": "mc-job-123", "Status": "SUBMITTED"}}

    def get_job(self, **kwargs: Any) -> dict[str, Any]:
        job_id = str(kwargs["Id"])
        self.get_job_calls.append(job_id)
        return {
            "Job": {
                "Id": job_id,
                "Status": "COMPLETE",
                "Settings": {"Inputs": []},
            }
        }

    def describe_endpoints(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_endpoints_calls += 1
        return {"Endpoints": [{"Url": self.endpoint_url}]}


def test_create_job_assembles_role_settings_and_queue() -> None:
    fake = FakeMediaConvertClient()
    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        queue_arn="arn:aws:mediaconvert:us-east-1:123456789012:queues/Default",
        endpoint_url="https://abc123.mediaconvert.us-east-1.amazonaws.com",
        client=fake,
    )

    job_id = client.create_job(
        {"Inputs": []},
        user_metadata={"job_id": "job-1"},
    )

    assert job_id == "mc-job-123"
    assert len(fake.create_job_calls) == 1
    call = fake.create_job_calls[0]
    assert call["Role"] == "arn:aws:iam::123456789012:role/MediaConvertRole"
    assert call["Settings"] == {"Inputs": []}
    assert call["Queue"] == "arn:aws:mediaconvert:us-east-1:123456789012:queues/Default"
    assert call["UserMetadata"] == {"job_id": "job-1"}


def test_create_job_allows_queue_override() -> None:
    fake = FakeMediaConvertClient()
    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        queue_arn="arn:aws:mediaconvert:us-east-1:123456789012:queues/Default",
        endpoint_url="https://abc123.mediaconvert.us-east-1.amazonaws.com",
        client=fake,
    )

    client.create_job({"Inputs": []}, queue="arn:aws:mediaconvert:us-east-1:123456789012:queues/Priority")

    assert fake.create_job_calls[0]["Queue"] == (
        "arn:aws:mediaconvert:us-east-1:123456789012:queues/Priority"
    )


def test_create_job_omits_queue_when_not_configured() -> None:
    fake = FakeMediaConvertClient()
    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        endpoint_url="https://abc123.mediaconvert.us-east-1.amazonaws.com",
        client=fake,
    )

    client.create_job({"Inputs": []})

    assert "Queue" not in fake.create_job_calls[0]


def test_get_job_returns_job_payload() -> None:
    fake = FakeMediaConvertClient()
    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        endpoint_url="https://abc123.mediaconvert.us-east-1.amazonaws.com",
        client=fake,
    )

    job = client.get_job("mc-job-456")

    assert fake.get_job_calls == ["mc-job-456"]
    assert job["Id"] == "mc-job-456"
    assert job["Status"] == "COMPLETE"


def test_create_client_discovers_endpoint_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMediaConvertClient()
    boto_calls: list[dict[str, Any]] = []

    def fake_boto3_client(
        service: str,
        *,
        region_name: str | None = None,
        endpoint_url: str | None = None,
    ) -> FakeMediaConvertClient:
        boto_calls.append(
            {"service": service, "region_name": region_name, "endpoint_url": endpoint_url}
        )
        return fake

    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=fake_boto3_client))

    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        region_name="us-east-1",
    )

    assert client._client is fake
    assert fake.describe_endpoints_calls == 1
    assert len(boto_calls) == 2
    assert boto_calls[0]["endpoint_url"] is None
    assert boto_calls[1]["endpoint_url"] == fake.endpoint_url


def test_create_client_uses_explicit_endpoint_without_discovery() -> None:
    fake = FakeMediaConvertClient()
    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        endpoint_url="https://explicit.mediaconvert.us-east-1.amazonaws.com",
        client=fake,
    )

    assert client._client is fake
    assert fake.describe_endpoints_calls == 0


def test_get_mediaconvert_client_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMediaConvertClient()
    monkeypatch.setenv("BASERENDER_MEDIACONVERT_ROLE_ARN", "arn:aws:iam::123456789012:role/MediaConvertRole")
    monkeypatch.setenv("BASERENDER_MEDIACONVERT_QUEUE_ARN", "arn:aws:mediaconvert:us-east-1:123456789012:queues/Default")
    monkeypatch.setenv("BASERENDER_MEDIACONVERT_ENDPOINT", "https://abc123.mediaconvert.us-east-1.amazonaws.com")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(
        "baserender_api.mediaconvert_client.BotoMediaConvertClient._create_client",
        lambda self: fake,
    )

    client = get_mediaconvert_client()

    assert isinstance(client, BotoMediaConvertClient)
    assert client.role_arn == "arn:aws:iam::123456789012:role/MediaConvertRole"
    assert client.queue_arn == "arn:aws:mediaconvert:us-east-1:123456789012:queues/Default"
    assert client.endpoint_url == "https://abc123.mediaconvert.us-east-1.amazonaws.com"


def test_get_mediaconvert_client_requires_role_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASERENDER_MEDIACONVERT_ROLE_ARN", raising=False)

    with pytest.raises(ValueError, match="BASERENDER_MEDIACONVERT_ROLE_ARN"):
        get_mediaconvert_client()


def test_create_job_maps_client_error() -> None:
    class ErrorClient:
        def create_job(self, **kwargs: Any) -> dict[str, Any]:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
                "CreateJob",
            )

    client = BotoMediaConvertClient(
        role_arn="arn:aws:iam::123456789012:role/MediaConvertRole",
        endpoint_url="https://abc123.mediaconvert.us-east-1.amazonaws.com",
        client=ErrorClient(),
    )

    with pytest.raises(MediaConvertError, match="Could not access MediaConvert"):
        client.create_job({"Inputs": []})
