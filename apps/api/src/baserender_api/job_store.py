from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Protocol

from baserender_api.schemas import (
    RenderJobError,
    RenderJobStatus,
    RenderOutput,
    RenderProgress,
    RenderStep,
    WorkerJobClaim,
)


ACTIVE_STATUSES = {"queued", "running"}


class JobStore(Protocol):
    def get(self, job_id: str) -> RenderJobStatus | None:
        ...

    def current(self) -> RenderJobStatus | None:
        ...

    def submit(self, job: RenderJobStatus) -> RenderJobStatus:
        ...

    def claim(self) -> WorkerJobClaim | None:
        ...

    def heartbeat(
        self,
        job_id: str,
        *,
        progress: RenderProgress | None = None,
    ) -> RenderJobStatus | None:
        ...

    def complete(
        self,
        job_id: str,
        *,
        report: dict,
        output: RenderOutput | None = None,
    ) -> RenderJobStatus | None:
        ...

    def fail(self, job_id: str, *, error: RenderJobError) -> RenderJobStatus | None:
        ...

    def cancel(self, job_id: str) -> RenderJobStatus | None:
        ...

    def dismiss(self) -> bool:
        ...

    def update_step(
        self,
        job_id: str,
        step_id: str,
        **fields: object,
    ) -> RenderJobStatus | None:
        ...

    def set_steps(
        self,
        job_id: str,
        steps: list[RenderStep],
        *,
        status: str | None = None,
        route: str | None = None,
        backend: str | None = None,
        output: RenderOutput | None = None,
        report: dict | None = None,
        error: RenderJobError | None = None,
    ) -> RenderJobStatus | None:
        ...


class SingleJobStore:
    """A one-slot job store; it is intentionally not a multi-job queue."""

    def __init__(self, backend: "JobStateBackend") -> None:
        self._backend = backend

    def get(self, job_id: str) -> RenderJobStatus | None:
        job = self.current()
        if job is None or job.id != job_id:
            return None
        return job

    def current(self) -> RenderJobStatus | None:
        job = self._load_current()
        if job is None:
            return None
        if is_job_stale(job):
            return self._fail_stale(job)
        return job

    def submit(self, job: RenderJobStatus) -> RenderJobStatus:
        current = self.current()
        if current is not None and current.status in ACTIVE_STATUSES:
            raise ActiveJobExistsError(current.id)
        now = _now()
        updated = _copy_model(job, created_at=now, updated_at=now)
        self._backend.write(_dump_model(updated))
        return updated

    def claim(self) -> WorkerJobClaim | None:
        job = self.current()
        if job is None or job.status != "queued" or job.backend != "worker":
            return None
        now = _now()
        updated = _copy_model(job, status="running", claimed_at=now, updated_at=now)
        self._backend.write(_dump_model(updated))
        return WorkerJobClaim(id=updated.id, worker_payload=updated.worker_payload)

    def heartbeat(
        self,
        job_id: str,
        *,
        progress: RenderProgress | None = None,
    ) -> RenderJobStatus | None:
        job = self._active_job(job_id)
        if job is None:
            return None
        updates: dict[str, object] = {
            "heartbeat_at": _now(),
            "updated_at": _now(),
        }
        if progress is not None:
            updates["progress"] = progress
        updated = _copy_model(job, **updates)
        if self._active_job(job_id) is None:
            return None
        self._backend.write(_dump_model(updated))
        return updated

    def complete(
        self,
        job_id: str,
        *,
        report: dict,
        output: RenderOutput | None = None,
    ) -> RenderJobStatus | None:
        job = self._active_job(job_id)
        if job is None:
            return None
        updated = _copy_model(
            job,
            status="succeeded",
            report=report,
            output=output,
            error=None,
            progress=None,
            updated_at=_now(),
        )
        if self._active_job(job_id) is None:
            return None
        self._backend.write(_dump_model(updated))
        return updated

    def fail(self, job_id: str, *, error: RenderJobError) -> RenderJobStatus | None:
        job = self._active_job(job_id)
        if job is None:
            return None
        updated = _copy_model(
            job,
            status="failed",
            error=error,
            updated_at=_now(),
        )
        if self._active_job(job_id) is None:
            return None
        self._backend.write(_dump_model(updated))
        return updated

    def cancel(self, job_id: str) -> RenderJobStatus | None:
        job = self.get(job_id)
        if job is None or job.status not in ACTIVE_STATUSES:
            return None
        cancelled = _copy_model(
            job,
            status="failed",
            error=RenderJobError(message="Render cancelled."),
            progress=None,
            updated_at=_now(),
        )
        self._backend.write(_dump_model(cancelled))
        return cancelled

    def dismiss(self) -> bool:
        if self._backend.read() is None:
            return False
        self._backend.delete()
        return True

    def update_step(
        self,
        job_id: str,
        step_id: str,
        **fields: object,
    ) -> RenderJobStatus | None:
        job = self._active_job(job_id)
        if job is None:
            return None
        updated_steps = _update_step_list(job.steps, step_id, fields)
        updated = _copy_model(job, steps=updated_steps, updated_at=_now())
        self._backend.write(_dump_model(updated))
        return updated

    def set_steps(
        self,
        job_id: str,
        steps: list[RenderStep],
        *,
        status: str | None = None,
        route: str | None = None,
        backend: str | None = None,
        output: RenderOutput | None = None,
        report: dict | None = None,
        error: RenderJobError | None = None,
    ) -> RenderJobStatus | None:
        job = self.get(job_id)
        if job is None:
            return None
        updates: dict[str, object] = {
            "steps": steps,
            "updated_at": _now(),
        }
        if status is not None:
            updates["status"] = status
        if route is not None:
            updates["route"] = route
        if backend is not None:
            updates["backend"] = backend
        if output is not None:
            updates["output"] = output
        if report is not None:
            updates["report"] = report
        if error is not None:
            updates["error"] = error
        updated = _copy_model(job, **updates)
        self._backend.write(_dump_model(updated))
        return updated

    def _load_current(self) -> RenderJobStatus | None:
        payload = self._backend.read()
        if payload is None:
            return None
        return RenderJobStatus(**payload)

    def _fail_stale(self, job: RenderJobStatus) -> RenderJobStatus:
        stale_seconds = job_stale_seconds()
        updated = _copy_model(
            job,
            status="failed",
            error=RenderJobError(
                message="Worker stopped responding.",
                detail=(
                    f"Job exceeded the stale timeout ({stale_seconds} seconds) "
                    "without finishing."
                ),
            ),
            progress=None,
            updated_at=_now(),
        )
        self._backend.write(_dump_model(updated))
        return updated

    def _active_job(self, job_id: str) -> RenderJobStatus | None:
        job = self.get(job_id)
        if job is None or job.status not in ACTIVE_STATUSES:
            return None
        return job


class ActiveJobExistsError(RuntimeError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Another render job is already active: {job_id}")
        self.job_id = job_id


class JobStateBackend(Protocol):
    def read(self) -> dict | None:
        ...

    def write(self, payload: dict) -> None:
        ...

    def delete(self) -> None:
        ...


class S3JobStateBackend:
    def __init__(
        self,
        *,
        bucket: str,
        key: str,
        client: object | None = None,
    ) -> None:
        self.bucket = bucket
        self.key = key.strip("/")
        self._client = client or self._create_client()

    def read(self) -> dict | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self.key)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise
        return json.loads(response["Body"].read().decode("utf-8"))

    def write(self, payload: dict) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self.key,
            Body=json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )

    def delete(self) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self.key)

    def _create_client(self) -> object:
        import boto3

        return boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        )


def job_stale_seconds() -> int:
    raw = os.getenv("BASERENDER_JOB_STALE_SECONDS", "3600")
    try:
        return max(60, int(raw))
    except ValueError:
        return 3600


def is_job_stale(job: RenderJobStatus) -> bool:
    if job.status not in ACTIVE_STATUSES:
        return False
    reference = _stale_reference_time(job)
    if reference is None:
        return False
    return (_now() - reference) > timedelta(seconds=job_stale_seconds())


def _stale_reference_time(job: RenderJobStatus) -> datetime | None:
    if job.status == "running":
        return job.heartbeat_at or job.claimed_at or job.updated_at or job.created_at
    return job.created_at


def get_job_store() -> SingleJobStore:
    bucket = os.getenv("BASERENDER_S3_BUCKET")
    if not bucket:
        raise ValueError("BASERENDER_S3_BUCKET must be set.")
    key = os.getenv("BASERENDER_JOB_STATE_KEY", "baserender/jobs/current.json")
    return SingleJobStore(S3JobStateBackend(bucket=bucket, key=key))


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_model(model: RenderJobStatus) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


def _copy_model(model: RenderJobStatus, **updates: object) -> RenderJobStatus:
    if hasattr(model, "model_copy"):
        return model.model_copy(update=updates)
    return model.copy(update=updates)


def _update_step_list(
    steps: list[RenderStep],
    step_id: str,
    fields: dict[str, object],
) -> list[RenderStep]:
    updated_steps: list[RenderStep] = []
    for step in steps:
        if step.id != step_id:
            updated_steps.append(step)
            continue
        if hasattr(step, "model_copy"):
            updated_steps.append(step.model_copy(update=fields))
        else:
            updated_steps.append(step.copy(update=fields))
    return updated_steps


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return code in {"NoSuchKey", "404", "NotFound"}
