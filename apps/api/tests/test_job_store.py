from __future__ import annotations

from datetime import UTC, datetime

from baserender_api.job_store import SingleJobStore
from baserender_api.schemas import (
    RenderJobCreate,
    RenderJobStatus,
    RenderStep,
    WorkerJobClaim,
)


class MemoryJobStateBackend:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def read(self) -> dict | None:
        return self.payload

    def write(self, payload: dict) -> None:
        self.payload = payload

    def delete(self) -> None:
        self.payload = None


def _sample_job(*, backend: str = "worker", status: str = "queued") -> RenderJobStatus:
    return RenderJobStatus(
        id="job-1",
        status=status,
        backend=backend,
        job=RenderJobCreate(input_path="timeline.otio", output_path="output.mp4"),
        worker_payload={"output_object_key": "outputs/output.mp4"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_claim_only_returns_worker_backend_jobs() -> None:
    backend = MemoryJobStateBackend()
    store = SingleJobStore(backend)
    store.submit(_sample_job(backend="cloud", status="queued"))

    assert store.claim() is None


def test_claim_returns_worker_job() -> None:
    backend = MemoryJobStateBackend()
    store = SingleJobStore(backend)
    store.submit(_sample_job(backend="worker", status="queued"))

    claim = store.claim()

    assert isinstance(claim, WorkerJobClaim)
    assert claim.id == "job-1"


def test_update_step_persists_step_fields() -> None:
    backend = MemoryJobStateBackend()
    store = SingleJobStore(backend)
    store.submit(
        _sample_job(backend="cloud", status="running").model_copy(
            update={
                "steps": [
                    RenderStep(
                        id="full",
                        kind="full",
                        backend="mediaconvert",
                        status="running",
                        external_id="mc-1",
                    )
                ]
            }
        )
    )

    updated = store.update_step("job-1", "full", status="succeeded", output_key="outputs/output.mp4")

    assert updated is not None
    assert updated.steps[0].status == "succeeded"
    assert updated.steps[0].output_key == "outputs/output.mp4"


def test_set_steps_updates_job_status_and_output() -> None:
    backend = MemoryJobStateBackend()
    store = SingleJobStore(backend)
    store.submit(_sample_job(backend="cloud", status="running"))

    updated = store.set_steps(
        "job-1",
        [RenderStep(id="full", kind="full", backend="mediaconvert", status="succeeded")],
        status="succeeded",
        route="full_mediaconvert",
    )

    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.route == "full_mediaconvert"
    assert updated.steps[0].kind == "full"
