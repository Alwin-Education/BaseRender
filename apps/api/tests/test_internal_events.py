from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from baserender_api.app import app
from baserender_api.internal_events import (
    InternalEventJobNotFound,
    process_internal_event,
)
from baserender_api.schemas import InternalRenderEvent


def test_process_internal_event_unknown_job_raises(
    auth_env,
) -> None:
    payload = InternalRenderEvent(job_id="missing", step_id="full", status="succeeded")

    with pytest.raises(InternalEventJobNotFound):
        process_internal_event(payload)


def test_internal_events_route_returns_404_for_unknown_job(
    auth_env,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/internal/events",
        headers={"Authorization": "Bearer test-worker-token"},
        json={"job_id": "missing", "step_id": "full", "status": "succeeded"},
    )

    assert response.status_code == 404


def test_worker_routes_return_410_when_disabled(
    auth_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASERENDER_DISABLE_WORKER_ROUTES", "1")
    client = TestClient(app)

    response = client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )

    assert response.status_code == 410
    assert "disabled" in response.json()["detail"]


def test_worker_routes_active_without_flag(
    auth_env,
) -> None:
    client = TestClient(app)

    response = client.post(
        "/worker/jobs/claim",
        headers={"Authorization": "Bearer test-worker-token"},
    )

    assert response.status_code == 200
