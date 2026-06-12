from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from baserender.report import LoadTimelineResult, RenderReport
from baserender.timeline_model import ClipSegment, TimelinePlan

from baserender_lambda.events import LambdaShotEvent
from baserender_lambda.handler import handle_shot_event


def _sample_otio_bytes(tmp_path: Path) -> bytes:
    otio_path = tmp_path / "source.otio"
    timeline = otio.schema.Timeline(name="Hybrid Timeline")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    clip = otio.schema.Clip(
        name="Shot A",
        media_reference=otio.schema.ExternalReference(
            target_url="file:///media/shot.mov",
            available_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(120, 24),
            ),
        ),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(12, 24),
            duration=otio.opentime.RationalTime(72, 24),
        ),
    )
    track.append(clip)
    timeline.tracks.append(track)
    otio.adapters.write_to_file(timeline, str(otio_path))
    return otio_path.read_bytes()


class FakeS3Io:
    def __init__(self, *, otio_bytes: bytes) -> None:
        self.downloads: list[tuple[str, Path]] = []
        self.uploads: list[tuple[Path, str]] = []
        self.objects: dict[str, bytes] = {
            # MediaConvert writes the truncated proxy with the container extension.
            "baserender/jobs/job-1/working/proxy-1.mp4": b"proxy-bytes",
            "baserender/jobs/job-1/inputs/timeline.otio": otio_bytes,
            "baserender/jobs/job-1/inputs/luts/lut-1": b"LUT",
        }

    def download(self, key: str, destination: Path) -> None:
        self.downloads.append((key, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects.get(key, b""))

    def upload(self, path: Path, key: str, *, content_type: str = "video/mp4") -> None:
        self.uploads.append((path, key))
        self.objects[key] = path.read_bytes()


def test_handle_shot_event_stages_inputs_and_uploads_output(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_render(input_path: str, output_path: str, **kwargs: object):
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["settings"] = kwargs.get("settings")
        Path(output_path).write_bytes(b"rendered-video")
        plan = TimelinePlan(
            name="Lambda Test",
            source_path=Path(input_path),
            track_name="V1",
            segments=(ClipSegment("A", "/media/shot.mov", 0, 3),),
        )
        report = RenderReport.from_load_result(
            LoadTimelineResult(plan=plan, issues=()),
            output_path=output_path,
        )
        return None, report

    event = LambdaShotEvent.from_mapping(
        {
            "job_id": "job-1",
            "bucket": "test-bucket",
            "shot_index": 1,
            "media_url": "/media/shot.mov",
            "timeline_offset_seconds": 2.0,
            "source_in_seconds": 0.5,
            "source_out_seconds": 3.5,
            "reasons": ["keyframes"],
            "proxy_key": "baserender/jobs/job-1/working/proxy-1",
            "otio_key": "baserender/jobs/job-1/inputs/timeline.otio",
            "lut_keys": {"/media/shot.mov": "baserender/jobs/job-1/inputs/luts/lut-1"},
            "output_key": "baserender/jobs/job-1/working/shot-1",
            "settings": {"width": 1920, "height": 1080, "fps": 24},
        }
    )

    storage = FakeS3Io(otio_bytes=_sample_otio_bytes(tmp_path))
    result = handle_shot_event(event, s3_io=storage, render_fn=fake_render)

    assert result["status"] == "ok"
    assert result["job_id"] == "job-1"
    assert result["shot_index"] == 1
    assert result["output_key"] == "baserender/jobs/job-1/working/shot-1.mp4"
    assert result["report"]["output"].endswith("output.mp4")

    downloaded_keys = [key for key, _destination in storage.downloads]
    assert "baserender/jobs/job-1/working/proxy-1.mp4" in downloaded_keys
    assert "baserender/jobs/job-1/inputs/timeline.otio" in downloaded_keys
    assert "baserender/jobs/job-1/inputs/luts/lut-1" in downloaded_keys

    uploaded_key = storage.uploads[0][1]
    assert uploaded_key == "baserender/jobs/job-1/working/shot-1.mp4"
    assert storage.objects[uploaded_key] == b"rendered-video"

    settings = captured["settings"]
    assert settings is not None
    assert settings.clip_luts["/media/shot.mov"].endswith("lut-0.cube")


def test_handle_shot_event_emits_failed_event_on_crash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_emit(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("baserender_lambda.handler.emit_shot_complete_event", fake_emit)

    class ExplodingS3Io:
        def download(self, key: str, destination: Path) -> None:
            raise RuntimeError("proxy missing")

        def upload(self, path: Path, key: str, *, content_type: str = "video/mp4") -> None:
            raise AssertionError("should not upload")

    event = LambdaShotEvent.from_mapping(
        {
            "job_id": "job-9",
            "bucket": "test-bucket",
            "shot_index": 3,
            "media_url": "/media/shot.mov",
            "timeline_offset_seconds": 0.0,
            "source_in_seconds": 0.0,
            "source_out_seconds": 1.0,
            "proxy_key": "baserender/jobs/job-9/working/proxy-3",
            "otio_key": "baserender/jobs/job-9/inputs/timeline.otio",
            "output_key": "baserender/jobs/job-9/working/shot-3",
            "settings": {"width": 640, "height": 360, "fps": 24},
        }
    )

    result = handle_shot_event(event, s3_io=ExplodingS3Io())

    assert result["status"] == "error"
    assert result["job_id"] == "job-9"
    assert "proxy missing" in result["error"]
    assert captured["status"] == "failed"
    assert captured["shot_index"] == 3
    assert "proxy missing" in str(captured["error_message"])
