from __future__ import annotations

from pathlib import Path

from baserender.mediaconvert import (
    build_full_render_job,
    build_per_shot_lut_job,
    build_stitch_job,
    build_transcode_job,
    build_truncation_job,
    seconds_to_timecode,
)
from baserender.routing import RouteKind, ShotHandler, ShotRouting, classify_timeline
from baserender.timeline_model import ClipSegment, RenderSettings, TimelinePlan


def _settings(**overrides: object) -> RenderSettings:
    defaults = {
        "width": 1920,
        "height": 1080,
        "fps": 24.0,
        "video_codec": "h264",
        "video_bitrate": 8_000_000,
        "video_encoder_preset": "faster",
        "video_faststart": True,
        "audio_codec": "aac",
        "audio_bitrate": 192_000,
    }
    defaults.update(overrides)
    return RenderSettings(**defaults)


def _simple_plan(*segments: ClipSegment) -> TimelinePlan:
    return TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=segments,
    )


def _shot(
    *,
    index: int = 0,
    media_url: str = "/media/a.mov",
    source_in: float = 0.0,
    source_out: float = 2.0,
    lut_path: str | None = None,
) -> ShotRouting:
    return ShotRouting(
        index=index,
        name=f"Shot {index}",
        media_url=media_url,
        handler=ShotHandler.MEDIACONVERT,
        lut_path=lut_path,
        reasons=(),
        timeline_offset_seconds=0.0,
        source_in_seconds=source_in,
        source_out_seconds=source_out,
    )


def test_seconds_to_timecode_rounds_to_nearest_frame() -> None:
    assert seconds_to_timecode(0.0, 24.0) == "00:00:00:00"
    assert seconds_to_timecode(1.0, 24.0) == "00:00:01:00"
    assert seconds_to_timecode(2.5, 24.0) == "00:00:02:12"
    assert seconds_to_timecode(61.0, 24.0) == "00:01:01:00"


def test_full_render_job_stitches_inputs_in_timeline_order() -> None:
    plan = _simple_plan(
        ClipSegment("A", "/media/a.mov", start_seconds=1.0, duration_seconds=2.0),
        ClipSegment("B", "/media/b.mov", start_seconds=0.5, duration_seconds=3.0),
    )
    routing = classify_timeline(plan)
    settings = _settings()

    job = build_full_render_job(
        routing,
        media_uris={
            "/media/a.mov": "s3://bucket/media/a.mov",
            "/media/b.mov": "s3://bucket/media/b.mov",
        },
        output_destination="s3://bucket/jobs/job-1/output",
        settings=settings,
    )

    assert routing.route is RouteKind.FULL_MEDIACONVERT
    inputs = job["Inputs"]
    assert len(inputs) == 2
    assert inputs[0]["FileInput"] == "s3://bucket/media/a.mov"
    assert inputs[0]["TimecodeSource"] == "ZEROBASED"
    assert inputs[0]["InputClippings"][0]["StartTimecode"] == "00:00:01:00"
    assert inputs[0]["InputClippings"][0]["EndTimecode"] == "00:00:03:00"
    assert inputs[1]["FileInput"] == "s3://bucket/media/b.mov"
    assert inputs[1]["InputClippings"][0]["StartTimecode"] == "00:00:00:12"
    assert inputs[1]["InputClippings"][0]["EndTimecode"] == "00:00:03:12"


def test_full_render_job_applies_lut_when_provided() -> None:
    plan = _simple_plan(
        ClipSegment(
            "A",
            "/media/a.mov",
            start_seconds=0,
            duration_seconds=2,
            lut_path="/looks/shared.cube",
        ),
    )
    routing = classify_timeline(plan)
    settings = _settings()

    job = build_full_render_job(
        routing,
        media_uris={"/media/a.mov": "s3://bucket/media/a.mov"},
        output_destination="s3://bucket/jobs/job-1/output",
        settings=settings,
        lut_uri="s3://bucket/luts/shared.cube",
    )

    assert job["ColorConversion3DLUTSettings"] == [
        {
            "FileInput": "s3://bucket/luts/shared.cube",
            "InputColorSpace": "REC_709",
            "InputMasteringLuminance": 0,
            "OutputColorSpace": "REC_709",
            "OutputMasteringLuminance": 0,
        }
    ]
    video = job["OutputGroups"][0]["Outputs"][0]["VideoDescription"]
    assert video["VideoPreprocessors"]["ColorCorrector"]["ColorSpaceConversion"] == "FORCE_709"


def test_full_render_job_omits_lut_when_not_provided() -> None:
    plan = _simple_plan(
        ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=2),
    )
    routing = classify_timeline(plan)
    settings = _settings()

    job = build_full_render_job(
        routing,
        media_uris={"/media/a.mov": "s3://bucket/media/a.mov"},
        output_destination="s3://bucket/jobs/job-1/output",
        settings=settings,
    )

    assert "ColorConversion3DLUTSettings" not in job
    video = job["OutputGroups"][0]["Outputs"][0]["VideoDescription"]
    assert "VideoPreprocessors" not in video


def test_full_render_job_maps_h264_mp4_output_settings() -> None:
    plan = _simple_plan(
        ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=2),
    )
    routing = classify_timeline(plan)
    settings = _settings()

    job = build_full_render_job(
        routing,
        media_uris={"/media/a.mov": "s3://bucket/media/a.mov"},
        output_destination="s3://bucket/jobs/job-1/output",
        settings=settings,
        container="mp4",
    )

    output = job["OutputGroups"][0]["Outputs"][0]
    assert output["ContainerSettings"]["Container"] == "MP4"
    assert output["ContainerSettings"]["Mp4Settings"]["MoovPlacement"] == "PROGRESSIVE_DOWNLOAD"
    assert output["VideoDescription"]["CodecSettings"]["Codec"] == "H_264"
    assert output["VideoDescription"]["CodecSettings"]["H264Settings"]["Bitrate"] == 8_000_000
    assert output["AudioDescriptions"][0]["CodecSettings"]["Codec"] == "AAC"
    assert (
        job["OutputGroups"][0]["OutputGroupSettings"]["FileGroupSettings"]["Destination"]
        == "s3://bucket/jobs/job-1/output"
    )


def test_per_shot_lut_job_applies_single_lut_to_one_shot() -> None:
    shot = _shot(source_in=1.0, source_out=3.0, lut_path="/looks/a.cube")
    settings = _settings()

    job = build_per_shot_lut_job(
        shot,
        media_uri="s3://bucket/media/a.mov",
        lut_uri="s3://bucket/luts/a.cube",
        output_destination="s3://bucket/jobs/job-1/working/shot-0",
        settings=settings,
    )

    assert len(job["Inputs"]) == 1
    assert job["Inputs"][0]["InputClippings"][0]["StartTimecode"] == "00:00:01:00"
    assert job["Inputs"][0]["InputClippings"][0]["EndTimecode"] == "00:00:03:00"
    assert job["ColorConversion3DLUTSettings"][0]["FileInput"] == "s3://bucket/luts/a.cube"
    assert (
        job["OutputGroups"][0]["OutputGroupSettings"]["FileGroupSettings"]["Destination"]
        == "s3://bucket/jobs/job-1/working/shot-0"
    )


def test_truncation_job_clips_source_range_for_lambda_proxy() -> None:
    shot = _shot(source_in=0.5, source_out=3.5)
    settings = _settings()

    job = build_truncation_job(
        shot,
        media_uri="s3://bucket/media/a.mov",
        output_destination="s3://bucket/jobs/job-1/working/proxy-0",
        settings=settings,
    )

    assert job["Inputs"][0]["TimecodeSource"] == "ZEROBASED"
    assert job["Inputs"][0]["InputClippings"][0]["StartTimecode"] == "00:00:00:12"
    assert job["Inputs"][0]["InputClippings"][0]["EndTimecode"] == "00:00:03:12"
    assert "ColorConversion3DLUTSettings" not in job
    assert (
        job["OutputGroups"][0]["OutputGroupSettings"]["FileGroupSettings"]["Destination"]
        == "s3://bucket/jobs/job-1/working/proxy-0"
    )


def test_stitch_job_concatenates_parts_in_order() -> None:
    settings = _settings()

    job = build_stitch_job(
        [
            "s3://bucket/jobs/job-1/working/shot-0.mp4",
            "s3://bucket/jobs/job-1/working/shot-1.mp4",
        ],
        output_destination="s3://bucket/jobs/job-1/final/output",
        settings=settings,
    )

    inputs = job["Inputs"]
    assert len(inputs) == 2
    assert inputs[0]["FileInput"] == "s3://bucket/jobs/job-1/working/shot-0.mp4"
    assert inputs[1]["FileInput"] == "s3://bucket/jobs/job-1/working/shot-1.mp4"
    assert "InputClippings" not in inputs[0]
    assert "InputClippings" not in inputs[1]
    assert (
        job["OutputGroups"][0]["OutputGroupSettings"]["FileGroupSettings"]["Destination"]
        == "s3://bucket/jobs/job-1/final/output"
    )


def test_codec_matrix_hevc_and_prores() -> None:
    shot = _shot()

    hevc_job = build_truncation_job(
        shot,
        media_uri="s3://bucket/media/a.mov",
        output_destination="s3://bucket/jobs/job-1/working/proxy-0",
        settings=_settings(video_codec="hevc"),
    )
    hevc_codec = hevc_job["OutputGroups"][0]["Outputs"][0]["VideoDescription"]["CodecSettings"]
    assert hevc_codec["Codec"] == "H_265"
    assert hevc_codec["H265Settings"]["Bitrate"] == 8_000_000

    prores_job = build_truncation_job(
        shot,
        media_uri="s3://bucket/media/a.mov",
        output_destination="s3://bucket/jobs/job-1/working/proxy-0",
        settings=_settings(video_codec="prores"),
        container="mov",
    )
    prores_output = prores_job["OutputGroups"][0]["Outputs"][0]
    prores_codec = prores_output["VideoDescription"]["CodecSettings"]
    assert prores_output["ContainerSettings"]["Container"] == "MOV"
    assert prores_codec["Codec"] == "PRORES"
    assert prores_codec["ProresSettings"]["CodecProfile"] == "APPLE_PRORES_422"


def test_mov_container_omits_faststart_settings() -> None:
    shot = _shot()

    job = build_stitch_job(
        ["s3://bucket/jobs/job-1/working/shot-0.mov"],
        output_destination="s3://bucket/jobs/job-1/final/output",
        settings=_settings(video_faststart=True),
        container="mov",
    )

    container_settings = job["OutputGroups"][0]["Outputs"][0]["ContainerSettings"]
    assert container_settings["Container"] == "MOV"
    assert "Mp4Settings" not in container_settings


def test_transcode_job_uses_single_full_file_input() -> None:
    job = build_transcode_job(
        "s3://bucket/projects/demo/clipA.mov",
        output_destination="s3://bucket/proxies/projects/demo/clipA",
        settings=_settings(),
        container="mp4",
    )

    assert len(job["Inputs"]) == 1
    assert job["Inputs"][0]["FileInput"] == "s3://bucket/projects/demo/clipA.mov"
    assert "InputClippings" not in job["Inputs"][0]
    assert job["OutputGroups"][0]["OutputGroupSettings"]["FileGroupSettings"]["Destination"] == (
        "s3://bucket/proxies/projects/demo/clipA"
    )
    assert "ColorConversion3DLUTSettings" not in job
