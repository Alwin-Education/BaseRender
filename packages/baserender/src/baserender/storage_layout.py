"""Pure S3 key and URI layout for BaseRender job artifacts and working directories."""

from __future__ import annotations

DEFAULT_ARTIFACT_PREFIX = "baserender"


def _normalize_prefix(prefix: str) -> str:
    return prefix.strip("/")


def _join_key(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part.strip("/"))


def job_prefix(job_id: str, *, artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX) -> str:
    """Return the artifact prefix for a job, e.g. ``baserender/jobs/{id}``."""
    return _join_key(_normalize_prefix(artifact_prefix), "jobs", job_id)


def working_prefix(job_id: str, *, artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX) -> str:
    """Return the working-directory prefix for a job."""
    return _join_key(job_prefix(job_id, artifact_prefix=artifact_prefix), "working")


def working_proxy_key(
    job_id: str,
    shot_index: int,
    *,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> str:
    """Return the S3 key prefix for a truncated Lambda proxy (no file extension)."""
    return _join_key(working_prefix(job_id, artifact_prefix=artifact_prefix), f"proxy-{shot_index}")


def shot_output_key(
    job_id: str,
    shot_index: int,
    *,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> str:
    """Return the S3 key prefix for a per-shot intermediate output (no file extension)."""
    return _join_key(working_prefix(job_id, artifact_prefix=artifact_prefix), f"shot-{shot_index}")


def stitch_output_key(job_id: str, *, artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX) -> str:
    """Return the S3 key prefix for the final stitched deliverable (no file extension)."""
    return _join_key(job_prefix(job_id, artifact_prefix=artifact_prefix), "final", "output")


def s3_uri(bucket: str, key: str) -> str:
    """Build an ``s3://`` URI from bucket and object key."""
    normalized = key.strip("/")
    return f"s3://{bucket}/{normalized}" if normalized else f"s3://{bucket}"


def job_uri(
    bucket: str,
    job_id: str,
    *segments: str,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> str:
    """Build an ``s3://`` URI under a job's artifact prefix."""
    key = _join_key(job_prefix(job_id, artifact_prefix=artifact_prefix), *segments)
    return s3_uri(bucket, key)


def working_uri(
    bucket: str,
    job_id: str,
    *segments: str,
    artifact_prefix: str = DEFAULT_ARTIFACT_PREFIX,
) -> str:
    """Build an ``s3://`` URI under a job's working directory."""
    key = _join_key(working_prefix(job_id, artifact_prefix=artifact_prefix), *segments)
    return s3_uri(bucket, key)
