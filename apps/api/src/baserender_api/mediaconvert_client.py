from __future__ import annotations

import os
from typing import Any, Protocol


class MediaConvertError(Exception):
    """Raised when MediaConvert cannot be accessed or a job cannot be submitted."""


class MediaConvertClient(Protocol):
    def create_job(
        self,
        settings: dict[str, Any],
        *,
        queue: str | None = None,
        user_metadata: dict[str, str] | None = None,
    ) -> str:
        ...

    def get_job(self, job_id: str) -> dict[str, Any]:
        ...


class BotoMediaConvertClient:
    def __init__(
        self,
        *,
        role_arn: str,
        queue_arn: str | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.role_arn = role_arn
        self.queue_arn = queue_arn
        self.endpoint_url = endpoint_url
        self.region_name = region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
        self._client = client or self._create_client()

    def create_job(
        self,
        settings: dict[str, Any],
        *,
        queue: str | None = None,
        user_metadata: dict[str, str] | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "Role": self.role_arn,
            "Settings": settings,
        }
        resolved_queue = queue or self.queue_arn
        if resolved_queue:
            request["Queue"] = resolved_queue
        if user_metadata:
            request["UserMetadata"] = user_metadata

        try:
            response = self._client.create_job(**request)
        except Exception as exc:
            raise _mediaconvert_error(exc) from exc

        job = response.get("Job", {})
        job_id = job.get("Id")
        if not job_id:
            raise MediaConvertError("MediaConvert create_job response did not include a job id.")
        return str(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        try:
            response = self._client.get_job(Id=job_id)
        except Exception as exc:
            raise _mediaconvert_error(exc) from exc

        job = response.get("Job")
        if not isinstance(job, dict):
            raise MediaConvertError(f"MediaConvert get_job response for {job_id!r} did not include job data.")
        return job

    def _create_client(self) -> Any:
        import boto3

        if self.endpoint_url:
            return boto3.client(
                "mediaconvert",
                region_name=self.region_name,
                endpoint_url=self.endpoint_url,
            )

        base_client = boto3.client("mediaconvert", region_name=self.region_name)
        try:
            response = base_client.describe_endpoints(MaxResults=1)
        except Exception as exc:
            raise _mediaconvert_error(exc) from exc

        endpoints = response.get("Endpoints", [])
        if not endpoints:
            raise MediaConvertError(
                "MediaConvert describe_endpoints returned no endpoints. "
                "Set BASERENDER_MEDIACONVERT_ENDPOINT explicitly."
            )

        endpoint = endpoints[0].get("Url")
        if not endpoint:
            raise MediaConvertError(
                "MediaConvert describe_endpoints returned an endpoint without a URL. "
                "Set BASERENDER_MEDIACONVERT_ENDPOINT explicitly."
            )

        return boto3.client(
            "mediaconvert",
            region_name=self.region_name,
            endpoint_url=endpoint,
        )


def get_mediaconvert_client() -> MediaConvertClient:
    role_arn = os.getenv("BASERENDER_MEDIACONVERT_ROLE_ARN")
    if not role_arn:
        raise ValueError("BASERENDER_MEDIACONVERT_ROLE_ARN must be set.")

    queue_arn = os.getenv("BASERENDER_MEDIACONVERT_QUEUE_ARN") or None
    endpoint_url = os.getenv("BASERENDER_MEDIACONVERT_ENDPOINT") or None
    region_name = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    return BotoMediaConvertClient(
        role_arn=role_arn,
        queue_arn=queue_arn,
        endpoint_url=endpoint_url,
        region_name=region_name,
    )


def _mediaconvert_error(exc: Exception) -> MediaConvertError:
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return MediaConvertError(f"MediaConvert request failed: {exc}")

    if not isinstance(exc, ClientError):
        return MediaConvertError(f"MediaConvert request failed: {exc}")

    error = exc.response.get("Error", {})
    code = error.get("Code", "")
    message = error.get("Message", str(exc))

    if code in {"AccessDeniedException", "AccessDenied"}:
        return MediaConvertError(
            "Could not access MediaConvert. Check AWS credentials, IAM permissions, "
            "and BASERENDER_MEDIACONVERT_ROLE_ARN."
        )
    if code in {"InvalidParameterValueException", "BadRequestException"}:
        return MediaConvertError(f"MediaConvert rejected the request: {message}")
    if code in {"NotFoundException", "ResourceNotFoundException"}:
        return MediaConvertError(f"MediaConvert resource was not found: {message}")

    return MediaConvertError(f"MediaConvert request failed: {message}")
