from __future__ import annotations

import time
from pathlib import Path
from urllib.request import Request

from baserender.report import LoadTimelineResult, RenderReport
from baserender.timeline_model import ClipSegment, RenderSettings, TimelinePlan
from baserender.ffmpeg_progress import FfmpegProgress
from baserender_worker.job import RenderJob, run_render_job
from baserender_worker.service import (
    JobSupersededError,
    WorkerApiClient,
    download_luts,
    run_once,
    safe_relative_output_path,
)


def test_render_job_parses_settings() -> None:
    job = RenderJob.from_mapping(
        {
            "input_path": "timeline.otio",
            "output_path": "output.mp4",
            "track_index": 1,
            "dry_run": True,
            "settings": {
                "width": 1920,
                "height": 1080,
                "fps": 24,
                "clip_luts": {"/media/a.mov": "/looks/a.cube"},
                "video_codec": "hevc",
                "video_bitrate": 12_000_000,
                "video_encoder_preset": "medium",
                "video_faststart": False,
                "audio_codec": "aac",
                "audio_bitrate": 256_000,
            },
        }
    )

    assert job.input_path == "timeline.otio"
    assert job.output_path == "output.mp4"
    assert job.track_index == 1
    assert job.dry_run is True
    assert job.settings == RenderSettings(
        width=1920,
        height=1080,
        fps=24.0,
        clip_luts={"/media/a.mov": "/looks/a.cube"},
        video_codec="hevc",
        video_bitrate=12_000_000,
        video_encoder_preset="medium",
        video_faststart=False,
        audio_codec="aac",
        audio_bitrate=256_000,
    )


def test_run_render_job_returns_report_payload() -> None:
    def fake_render(input_path: str, output_path: str, **kwargs: object):
        plan = TimelinePlan(
            name="Worker Test",
            source_path=Path(input_path),
            track_name="V1",
            segments=(ClipSegment("A", "/media/a.mov", 0, 1),),
        )
        report = RenderReport.from_load_result(
            LoadTimelineResult(plan=plan, issues=()),
            output_path=output_path,
            dry_run=bool(kwargs["dry_run"]),
            ffmpeg_shell="ffmpeg -y ...",
        )
        return None, report

    payload = run_render_job(
        RenderJob(input_path="timeline.otio", output_path="output.mp4", dry_run=True),
        render_fn=fake_render,
    )

    assert payload["status"] == "ok"
    assert payload["timeline"]["source"] == "timeline.otio"
    assert payload["output"] == "output.mp4"
    assert payload["ffmpeg_shell"] == "ffmpeg -y ..."


def test_run_render_job_forwards_on_progress() -> None:
    received: list[FfmpegProgress] = []

    def fake_render(input_path: str, output_path: str, **kwargs: object):
        callback = kwargs.get("on_progress")
        if callable(callback):
            callback(
                FfmpegProgress(
                    out_time_seconds=5.0,
                    frame=100,
                    fps=24.0,
                    speed=0.5,
                    percent=42.0,
                    elapsed_seconds=10.0,
                    eta_seconds=20.0,
                )
            )
        plan = TimelinePlan(
            name="Worker Test",
            source_path=Path(input_path),
            track_name="V1",
            segments=(ClipSegment("A", "/media/a.mov", 0, 1),),
        )
        report = RenderReport.from_load_result(
            LoadTimelineResult(plan=plan, issues=()),
            output_path=output_path,
            dry_run=True,
        )
        return None, report

    run_render_job(
        RenderJob(input_path="timeline.otio", output_path="output.mp4", dry_run=True),
        render_fn=fake_render,
        on_progress=received.append,
    )

    assert len(received) == 1
    assert received[0].percent == 42.0


def test_worker_download_luts_keys_clip_luts_by_media_url(tmp_path: Path) -> None:
    class FakeClient:
        def download_lut(self, job_id: str, lut_id: str, destination: Path) -> None:
            destination.write_bytes(b"LUT")

    payload = {
        "artifacts": {"luts": [{"id": "lut-1", "name": "look.cube"}]},
        "clip_lut_artifacts": [
            {
                "normalized_url": "/Volumes/Raid/Shot_A.mov",
                "media_url": "https://fake-s3.test/get?bucket=test-bucket&key=Shot_A.mov",
                "lut_id": "lut-1",
            }
        ],
    }

    clip_luts = download_luts(FakeClient(), "job-1", payload, tmp_path)

    assert clip_luts["https://fake-s3.test/get?bucket=test-bucket&key=Shot_A.mov"] == str(
        tmp_path / "luts" / "look.cube"
    )
    assert clip_luts["/Volumes/Raid/Shot_A.mov"] == str(tmp_path / "luts" / "look.cube")


def test_worker_run_once_executes_claimed_job(monkeypatch, tmp_path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.completed: dict | None = None
            self.failed: dict | None = None
            self.uploaded = False
            self.heartbeats: list[dict | None] = []

        def claim_job(self) -> dict:
            return {
                "id": "job-1",
                "worker_payload": {
                    "input_path": "artifact://input",
                    "output_path": "episode-1/final.mp4",
                    "settings": {},
                    "dry_run": False,
                    "clip_lut_artifacts": [],
                    "artifacts": {"luts": []},
                },
            }

        def download_input(self, job_id: str, destination: Path) -> None:
            destination.write_text("{}", encoding="utf-8")

        def upload_output(self, job_id: str, path: Path) -> None:
            self.uploaded = True
            assert path.exists()
            assert path.name == "final.mp4"
            assert path.parent.name == "episode-1"

        def complete_job(self, job_id: str, report: dict) -> None:
            self.completed = report

        def heartbeat(self, job_id: str, *, progress: dict | None = None) -> dict:
            self.heartbeats.append(progress)
            return {"id": job_id, "status": "running"}

        def fail_job(self, job_id: str, message: str, detail: str | None = None) -> None:
            self.failed = {"message": message, "detail": detail}

    def fake_run_render_job(job: RenderJob, **kwargs: object) -> dict:
        on_progress = kwargs.get("on_progress")
        if callable(on_progress):
            on_progress(
                FfmpegProgress(
                    out_time_seconds=1.0,
                    frame=10,
                    fps=24.0,
                    speed=0.5,
                    percent=10.0,
                    elapsed_seconds=1.0,
                    eta_seconds=9.0,
                )
            )
        Path(job.output_path).write_bytes(b"video")
        return {"status": "ok"}

    client = FakeClient()
    monkeypatch.setattr("baserender_worker.service.run_render_job", fake_run_render_job)

    assert run_once(client) is True
    assert client.uploaded is True
    assert client.completed == {"status": "ok"}
    assert client.failed is None
    encoding_heartbeats = [
        heartbeat
        for heartbeat in client.heartbeats
        if heartbeat is not None and heartbeat.get("phase") == "encoding"
    ]
    uploading_heartbeats = [
        heartbeat
        for heartbeat in client.heartbeats
        if heartbeat is not None and heartbeat.get("phase") == "uploading"
    ]
    assert encoding_heartbeats[0]["phase"] == "encoding"
    assert uploading_heartbeats[-1]["percent"] == 100.0


def test_worker_run_once_stops_when_job_is_superseded(monkeypatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.completed: dict | None = None
            self.failed: dict | None = None
            self.uploaded = False
            self.heartbeat_calls = 0

        def claim_job(self) -> dict:
            return {
                "id": "job-1",
                "worker_payload": {
                    "input_path": "artifact://input",
                    "output_path": "final.mp4",
                    "settings": {},
                    "dry_run": False,
                    "clip_lut_artifacts": [],
                    "artifacts": {"luts": []},
                },
            }

        def download_input(self, job_id: str, destination: Path) -> None:
            destination.write_text("{}", encoding="utf-8")

        def upload_output(self, job_id: str, path: Path) -> None:
            self.uploaded = True

        def complete_job(self, job_id: str, report: dict) -> None:
            self.completed = report

        def heartbeat(self, job_id: str, *, progress: dict | None = None) -> dict:
            self.heartbeat_calls += 1
            raise JobSupersededError(f"Render job {job_id} was superseded.")

        def fail_job(self, job_id: str, message: str, detail: str | None = None) -> None:
            self.failed = {"message": message, "detail": detail}

    def fake_run_render_job(job: RenderJob, **kwargs: object) -> dict:
        on_progress = kwargs.get("on_progress")
        should_cancel = kwargs.get("should_cancel")
        if callable(on_progress):
            on_progress(
                FfmpegProgress(
                    out_time_seconds=1.0,
                    frame=10,
                    fps=24.0,
                    speed=0.5,
                    percent=10.0,
                    elapsed_seconds=1.0,
                    eta_seconds=9.0,
                )
            )
        if callable(should_cancel) and should_cancel():
            from baserender.ffmpeg_progress import FfmpegCancelledError

            raise FfmpegCancelledError("FFmpeg render was cancelled.")
        Path(job.output_path).write_bytes(b"video")
        return {"status": "ok"}

    client = FakeClient()
    monkeypatch.setattr("baserender_worker.service.run_render_job", fake_run_render_job)

    assert run_once(client) is True
    assert client.heartbeat_calls >= 1
    assert client.uploaded is False
    assert client.completed is None
    assert client.failed is None


def test_worker_run_once_poll_cancels_without_encode_progress(monkeypatch) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.completed: dict | None = None
            self.failed: dict | None = None
            self.uploaded = False
            self.poll_heartbeats = 0

        def claim_job(self) -> dict:
            return {
                "id": "job-1",
                "worker_payload": {
                    "input_path": "artifact://input",
                    "output_path": "final.mp4",
                    "settings": {},
                    "dry_run": True,
                    "clip_lut_artifacts": [],
                    "artifacts": {"luts": []},
                },
            }

        def download_input(self, job_id: str, destination: Path) -> None:
            destination.write_text("{}", encoding="utf-8")

        def upload_output(self, job_id: str, path: Path) -> None:
            self.uploaded = True

        def complete_job(self, job_id: str, report: dict) -> None:
            self.completed = report

        def heartbeat(self, job_id: str, *, progress: dict | None = None) -> dict:
            if progress is not None:
                return {"id": job_id, "status": "running"}
            self.poll_heartbeats += 1
            if self.poll_heartbeats >= 2:
                raise JobSupersededError(f"Render job {job_id} was superseded.")
            return {"id": job_id, "status": "running"}

        def fail_job(self, job_id: str, message: str, detail: str | None = None) -> None:
            self.failed = {"message": message, "detail": detail}

    def fake_run_render_job(job: RenderJob, **kwargs: object) -> dict:
        should_cancel = kwargs.get("should_cancel")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if callable(should_cancel) and should_cancel():
                from baserender.ffmpeg_progress import FfmpegCancelledError

                raise FfmpegCancelledError("FFmpeg render was cancelled.")
            time.sleep(0.01)
        raise AssertionError("Render job was not cancelled by the poll thread.")

    client = FakeClient()
    monkeypatch.setattr("baserender_worker.service.run_render_job", fake_run_render_job)

    assert run_once(client) is True
    assert client.poll_heartbeats >= 2
    assert client.uploaded is False
    assert client.completed is None
    assert client.failed is None


def test_safe_relative_output_path_preserves_nested_paths() -> None:
    assert safe_relative_output_path("episode-1/final.mp4") == "episode-1/final.mp4"
    assert safe_relative_output_path("") == "output.mp4"


def test_worker_upload_output_streams_to_s3_target(monkeypatch, tmp_path) -> None:
    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b""

    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: int) -> FakeResponse:
        headers = {key.lower(): value for key, value in request.header_items()}
        body = request.data
        captured["url"] = request.full_url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["body_type"] = type(body).__name__
        captured["body"] = body.read()
        return FakeResponse()

    output_path = tmp_path / "final.mp4"
    output_path.write_bytes(b"video")
    client = WorkerApiClient(base_url="https://api.example", token="worker-token")
    monkeypatch.setattr(
        client,
        "get_output_upload_target",
        lambda _job_id: {
            "url": "https://s3.example/upload",
            "key": "outputs/final.mp4",
            "headers": {"Content-Type": "video/mp4"},
        },
    )
    monkeypatch.setattr("baserender_worker.service.urlopen", fake_urlopen)

    client.upload_output("job-1", output_path)

    assert captured["url"] == "https://s3.example/upload"
    assert captured["body"] == b"video"
    assert captured["body_type"] != "bytes"
    assert captured["headers"]["content-type"] == "video/mp4"
    assert captured["headers"]["content-length"] == "5"
    assert "authorization" not in captured["headers"]

