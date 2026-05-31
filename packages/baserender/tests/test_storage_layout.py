from __future__ import annotations

from baserender.storage_layout import (
    DEFAULT_ARTIFACT_PREFIX,
    job_prefix,
    job_uri,
    s3_uri,
    shot_output_key,
    stitch_output_key,
    working_prefix,
    working_proxy_key,
    working_uri,
)


def test_job_prefix_uses_artifact_prefix() -> None:
    assert job_prefix("job-1") == "baserender/jobs/job-1"
    assert job_prefix("job-1", artifact_prefix="custom") == "custom/jobs/job-1"


def test_working_prefix_appends_working_segment() -> None:
    assert working_prefix("job-1") == "baserender/jobs/job-1/working"


def test_working_proxy_key() -> None:
    assert working_proxy_key("job-1", 0) == "baserender/jobs/job-1/working/proxy-0"
    assert working_proxy_key("job-1", 3) == "baserender/jobs/job-1/working/proxy-3"


def test_shot_output_key() -> None:
    assert shot_output_key("job-1", 0) == "baserender/jobs/job-1/working/shot-0"
    assert shot_output_key("job-1", 2) == "baserender/jobs/job-1/working/shot-2"


def test_stitch_output_key() -> None:
    assert stitch_output_key("job-1") == "baserender/jobs/job-1/final/output"


def test_s3_uri() -> None:
    assert s3_uri("bucket", "baserender/jobs/job-1/working/shot-0") == (
        "s3://bucket/baserender/jobs/job-1/working/shot-0"
    )


def test_job_uri() -> None:
    assert job_uri("bucket", "job-1", "inputs", "timeline.otio") == (
        "s3://bucket/baserender/jobs/job-1/inputs/timeline.otio"
    )


def test_working_uri() -> None:
    assert working_uri("bucket", "job-1", "proxy-0") == (
        "s3://bucket/baserender/jobs/job-1/working/proxy-0"
    )
    assert working_uri("bucket", "job-1", "shot-0") == (
        "s3://bucket/baserender/jobs/job-1/working/shot-0"
    )


def test_default_artifact_prefix_constant() -> None:
    assert DEFAULT_ARTIFACT_PREFIX == "baserender"
