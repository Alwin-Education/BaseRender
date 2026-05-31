from __future__ import annotations

from pathlib import Path

from baserender.routing import RouteKind, ShotHandler, ShotRouting, classify_timeline
from baserender.timeline_model import ClipSegment, RenderSettings, TimelinePlan

from baserender_api.orchestrator import (
    AdvanceResult,
    build_cloud_artifacts,
    routing_to_payload,
    start_render,
)
from baserender_api.orchestrator import advance as advance_render
from baserender_api.schemas import (
    InternalRenderEvent,
    RenderJobCreate,
    RenderJobStatus,
    RenderLutFile,
    RenderSettingsPayload,
    RenderStep,
)


class FakeMediaConvertClient:
    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self._counter = 0

    def create_job(self, settings, *, queue=None, user_metadata=None) -> str:
        self._counter += 1
        self.jobs.append(
            {
                "settings": settings,
                "queue": queue,
                "user_metadata": user_metadata or {},
            }
        )
        return f"mc-{self._counter}"

    def get_job(self, job_id: str) -> dict:
        return {"Id": job_id, "Status": "COMPLETE"}


class FakeEventBridgeClient:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def put_event(self, detail_type, detail, *, source=None, bus=None) -> str:
        self.events.append(
            {
                "detail_type": detail_type,
                "detail": detail,
                "source": source,
                "bus": bus,
            }
        )
        return "event-1"


def _simple_plan(*segments: ClipSegment) -> TimelinePlan:
    return TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=segments,
    )


def _job(**settings: object) -> RenderJobCreate:
    return RenderJobCreate(
        output_path="output.mp4",
        settings=RenderSettingsPayload(width=1920, height=1080, fps=24, **settings),
        media_references=[
            {
                "id": "ref-a",
                "normalized_url": "/media/a.mov",
            }
        ],
        media_assignments={"ref-a": "projects/demo/a.mov"},
    )


def test_start_render_full_route_submits_one_mediaconvert_job() -> None:
    routing = classify_timeline(
        _simple_plan(
            ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=2),
        )
    )
    artifacts = build_cloud_artifacts("job-1", _job(), bucket="test-bucket")
    mc = FakeMediaConvertClient()

    steps = start_render(
        "job-1",
        _job(),
        routing,
        artifacts,
        mediaconvert=mc,
        bucket="test-bucket",
    )

    assert routing.route is RouteKind.FULL_MEDIACONVERT
    assert len(steps) == 1
    assert steps[0].kind == "full"
    assert steps[0].status == "running"
    assert steps[0].external_id == "mc-1"
    assert len(mc.jobs) == 1
    assert mc.jobs[0]["user_metadata"] == {"job_id": "job-1", "step_id": "full"}


def test_start_render_per_shot_route_submits_lut_jobs_and_pending_stitch() -> None:
    routing = classify_timeline(
        _simple_plan(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=2,
                lut_path="/looks/a.cube",
            ),
            ClipSegment(
                "B",
                "/media/b.mov",
                start_seconds=0,
                duration_seconds=3,
                lut_path="/looks/b.cube",
            ),
        )
    )
    job = _job()
    job.media_references.append({"id": "ref-b", "normalized_url": "/media/b.mov"})
    job.media_assignments["ref-b"] = "projects/demo/b.mov"
    job.lut_files = [
        RenderLutFile(id="lut-a", name="a.cube", content_base64="YQ=="),
        RenderLutFile(id="lut-b", name="b.cube", content_base64="Yg=="),
    ]
    job.lut_assignments = {"ref-a": "lut-a", "ref-b": "lut-b"}
    artifacts = build_cloud_artifacts("job-1", job, bucket="test-bucket")
    mc = FakeMediaConvertClient()

    steps = start_render("job-1", job, routing, artifacts, mediaconvert=mc, bucket="test-bucket")

    assert routing.route is RouteKind.PER_SHOT_MEDIACONVERT
    assert len(mc.jobs) == 2
    assert {step.kind for step in steps} == {"per_shot_lut", "stitch"}
    stitch = next(step for step in steps if step.kind == "stitch")
    assert stitch.status == "pending"


def test_advance_truncation_emits_lambda_shot_event() -> None:
    routing = classify_timeline(
        _simple_plan(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=2,
                animation=None,
            )
        )
    )
    # Force hybrid by using keyframes - simpler to construct routing manually
    shot = ShotRouting(
        index=0,
        name="A",
        media_url="/media/a.mov",
        handler=ShotHandler.LAMBDA_FFMPEG,
        lut_path=None,
        reasons=("keyframes",),
        timeline_offset_seconds=0.0,
        source_in_seconds=0.0,
        source_out_seconds=2.0,
    )
    from baserender.routing import RoutingPlan

    routing = RoutingPlan(
        route=RouteKind.HYBRID,
        shots=(shot,),
        distinct_lut_count=0,
        requires_final_stitch=True,
    )
    job = RenderJobStatus(
        id="job-1",
        status="running",
        backend="cloud",
        route=routing.route.value,
        job=_job(),
        worker_payload={
            "output_object_key": "outputs/output.mp4",
            "routing": routing_to_payload(routing),
            "artifacts": {
                "input": "baserender/jobs/job-1/inputs/timeline.otio",
                "luts": [],
            },
            "clip_lut_artifacts": [],
            "media_uris": {"/media/a.mov": "s3://test-bucket/projects/demo/a.mov"},
            "lut_uris": {},
        },
        steps=[
            RenderStep(
                id="truncate-0",
                kind="truncation",
                backend="mediaconvert",
                shot_index=0,
                status="running",
                external_id="mc-1",
                output_key="baserender/jobs/job-1/working/proxy-0",
            ),
            RenderStep(
                id="lambda-0",
                kind="lambda_shot",
                backend="lambda",
                shot_index=0,
                status="pending",
                depends_on=["truncate-0"],
                output_key="baserender/jobs/job-1/working/shot-0",
            ),
            RenderStep(
                id="stitch",
                kind="stitch",
                backend="mediaconvert",
                status="pending",
                depends_on=["lambda-0"],
                output_key="outputs/output.mp4",
            ),
        ],
    )
    mc = FakeMediaConvertClient()
    eb = FakeEventBridgeClient()

    result = advance_render(
        job,
        InternalRenderEvent(
            job_id="job-1",
            step_id="truncate-0",
            status="succeeded",
            output_key="baserender/jobs/job-1/working/proxy-0.mp4",
        ),
        mediaconvert=mc,
        eventbridge=eb,
    )

    assert len(eb.events) == 1
    assert eb.events[0]["detail_type"] == "BaseRender Lambda Shot"
    assert eb.events[0]["detail"]["shot_index"] == 0
    lambda_step = next(step for step in result.job.steps if step.id == "lambda-0")
    assert lambda_step.status == "running"


def test_advance_stitch_completion_marks_job_succeeded() -> None:
    job = RenderJobStatus(
        id="job-1",
        status="running",
        backend="cloud",
        route="per_shot_mediaconvert",
        job=_job(),
        worker_payload={
            "output_object_key": "outputs/output.mp4",
            "routing": routing_to_payload(
                classify_timeline(
                    _simple_plan(
                        ClipSegment(
                            "A",
                            "/media/a.mov",
                            start_seconds=0,
                            duration_seconds=2,
                            lut_path="/looks/a.cube",
                        ),
                        ClipSegment(
                            "B",
                            "/media/b.mov",
                            start_seconds=0,
                            duration_seconds=3,
                            lut_path="/looks/b.cube",
                        ),
                    )
                )
            ),
            "artifacts": {"input": "baserender/jobs/job-1/inputs/timeline.otio", "luts": []},
            "clip_lut_artifacts": [],
            "media_uris": {
                "/media/a.mov": "s3://test-bucket/projects/demo/a.mov",
                "/media/b.mov": "s3://test-bucket/projects/demo/b.mov",
            },
            "lut_uris": {},
        },
        steps=[
            RenderStep(
                id="lut-0",
                kind="per_shot_lut",
                backend="mediaconvert",
                shot_index=0,
                status="succeeded",
                output_key="baserender/jobs/job-1/working/shot-0",
            ),
            RenderStep(
                id="lut-1",
                kind="per_shot_lut",
                backend="mediaconvert",
                shot_index=1,
                status="succeeded",
                output_key="baserender/jobs/job-1/working/shot-1",
            ),
            RenderStep(
                id="stitch",
                kind="stitch",
                backend="mediaconvert",
                status="running",
                external_id="mc-stitch",
                output_key="outputs/output.mp4",
            ),
        ],
    )
    mc = FakeMediaConvertClient()
    eb = FakeEventBridgeClient()

    result: AdvanceResult = advance_render(
        job,
        InternalRenderEvent(job_id="job-1", step_id="stitch", status="succeeded"),
        mediaconvert=mc,
        eventbridge=eb,
    )

    assert result.completed is True
    assert result.job.status == "succeeded"
    assert result.job.output is not None
    assert result.job.output.key == "outputs/output.mp4"
