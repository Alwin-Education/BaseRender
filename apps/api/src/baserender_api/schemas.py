from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from baserender.timeline_model import RenderSettings


class RenderSettingsPayload(BaseModel):
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_sample_rate: int = 48000
    audio_channel_layout: str = "stereo"
    clip_luts: dict[str, str] = Field(default_factory=dict)
    video_codec: str = "h264"
    video_bitrate: int = 8_000_000
    video_encoder_preset: str = "faster"
    video_faststart: bool = True
    audio_codec: str = "aac"
    audio_bitrate: int = 192_000
    video_crf: int | None = None

    def to_render_settings(self) -> RenderSettings:
        return RenderSettings(
            width=self.width,
            height=self.height,
            fps=self.fps,
            audio_sample_rate=self.audio_sample_rate,
            audio_channel_layout=self.audio_channel_layout,
            clip_luts=self.clip_luts,
            video_codec=self.video_codec,
            video_bitrate=self.video_bitrate,
            video_encoder_preset=self.video_encoder_preset,
            video_faststart=self.video_faststart,
            audio_codec=self.audio_codec,
            audio_bitrate=self.audio_bitrate,
            video_crf=self.video_crf,
        )


class RenderJobCreate(BaseModel):
    input_path: str | None = None
    output_path: str
    settings: RenderSettingsPayload = Field(default_factory=RenderSettingsPayload)
    track_index: int | None = None
    dry_run: bool = False
    overwrite: bool = True
    fail_fast: bool = False
    otio_content_base64: str | None = None
    media_references: list[dict[str, Any]] = Field(default_factory=list)
    media_assignments: dict[str, str] = Field(default_factory=dict)
    lut_files: list["RenderLutFile"] = Field(default_factory=list)
    lut_assignments: dict[str, str] = Field(default_factory=dict)

    def to_worker_payload(self) -> dict[str, Any]:
        settings_payload = (
            self.settings.model_dump()
            if hasattr(self.settings, "model_dump")
            else self.settings.dict()
        )
        return {
            "input_path": self.input_path or "",
            "output_path": self.output_path,
            "settings": settings_payload,
            "track_index": self.track_index,
            "dry_run": self.dry_run,
            "overwrite": self.overwrite,
            "fail_fast": self.fail_fast,
        }


class RenderLutFile(BaseModel):
    id: str
    name: str
    content_base64: str


class RenderOutput(BaseModel):
    path: str
    key: str | None = None
    size: int | None = None


class RenderJobError(BaseModel):
    message: str
    detail: str | None = None


class RenderProgress(BaseModel):
    percent: float
    elapsed_seconds: float
    eta_seconds: float | None = None
    out_time_seconds: float | None = None
    frame: int | None = None
    fps: float | None = None
    speed: float | None = None
    phase: Literal["encoding", "uploading"] | None = None


RenderStepKind = Literal["full", "per_shot_lut", "truncation", "lambda_shot", "stitch"]
RenderStepBackend = Literal["mediaconvert", "lambda"]
RenderStepStatus = Literal["pending", "running", "succeeded", "failed"]


class RenderStep(BaseModel):
    id: str
    kind: RenderStepKind
    backend: RenderStepBackend
    shot_index: int | None = None
    external_id: str | None = None
    status: RenderStepStatus = "pending"
    output_key: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    error: RenderJobError | None = None


class RenderJobStatus(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    job: RenderJobCreate
    worker_payload: dict[str, Any]
    backend: Literal["cloud", "worker"] = "worker"
    route: str | None = None
    steps: list[RenderStep] = Field(default_factory=list)
    report: dict[str, Any] | None = None
    output: RenderOutput | None = None
    error: RenderJobError | None = None
    progress: RenderProgress | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None


class WorkerJobClaim(BaseModel):
    id: str
    worker_payload: dict[str, Any]


class OutputUploadTarget(BaseModel):
    url: str
    key: str
    headers: dict[str, str] = Field(default_factory=dict)


class WorkerJobComplete(BaseModel):
    report: dict[str, Any]


class WorkerJobHeartbeat(BaseModel):
    progress: RenderProgress | None = None


class WorkerJobFail(BaseModel):
    message: str
    detail: str | None = None


class InternalRenderEvent(BaseModel):
    job_id: str
    step_id: str | None = None
    shot_index: int | None = None
    external_id: str | None = None
    status: Literal["succeeded", "failed"]
    output_key: str | None = None
    error: RenderJobError | None = None


class ConversionRequest(BaseModel):
    source_path: str
    source_format: Literal["otio", "xmeml", "fcpxml", "aaf"]
    output_path: str | None = None


class ConversionResponse(BaseModel):
    status: Literal["not_implemented"]
    message: str


class TranscodeJobCreate(BaseModel):
    inputs: list[str] = Field(min_length=1)
    settings: RenderSettingsPayload = Field(default_factory=RenderSettingsPayload)
    container: str = "mp4"
    prepend_folder: str | None = None
    append_folder: str | None = None
    dry_run: bool = False


class TranscodeResultItem(BaseModel):
    source_key: str
    output_key: str
    mediaconvert_job_id: str | None = None


class TranscodeResponse(BaseModel):
    results: list[TranscodeResultItem]
