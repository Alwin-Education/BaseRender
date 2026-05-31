from __future__ import annotations

from baserender_api.storage import S3ArtifactStore


def test_s3_artifact_store_round_trips_bytes(fake_s3_client) -> None:
    store = S3ArtifactStore(
        bucket="test-bucket",
        prefix="baserender",
        client=fake_s3_client,
    )

    location = store.put_bytes("jobs/job-1/output/report.json", b'{"status":"ok"}')

    assert location == "s3://test-bucket/baserender/jobs/job-1/output/report.json"
    assert store.get_bytes("jobs/job-1/output/report.json") == b'{"status":"ok"}'
