from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from baserender.timeline_model import TimelinePlan


IssueSeverity = Literal["warning", "error"]
ReportStatus = Literal["ok", "warning", "error"]


@dataclass(frozen=True)
class TimelineIssue:
    code: str
    severity: IssueSeverity
    message: str
    item_name: str | None = None
    item_type: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.item_name is not None:
            payload["item_name"] = self.item_name
        if self.item_type is not None:
            payload["item_type"] = self.item_type
        return payload


@dataclass(frozen=True)
class LoadTimelineResult:
    plan: TimelinePlan | None
    issues: tuple[TimelineIssue, ...]
    items_skipped: int = 0

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)


@dataclass(frozen=True)
class RenderReport:
    status: ReportStatus
    timeline_name: str
    timeline_source: str
    track_name: str
    video_track_names: tuple[str, ...]
    audio_track_names: tuple[str, ...]
    segments_included: int
    segments_skipped: int
    duration_seconds: float
    issues: tuple[TimelineIssue, ...]
    output: str
    dry_run: bool
    ffmpeg_shell: str | None = None

    @classmethod
    def from_load_result(
        cls,
        load_result: LoadTimelineResult,
        *,
        output_path: str | Path,
        dry_run: bool = False,
        ffmpeg_shell: str | None = None,
    ) -> RenderReport:
        plan = load_result.plan
        if plan is not None:
            video_tracks = plan.effective_video_tracks
            segments_included = sum(len(track.segments) for track in video_tracks)
            duration_seconds = plan.duration_seconds
            video_track_names = tuple(track.name for track in video_tracks)
        else:
            segments_included = 0
            duration_seconds = 0.0
            video_track_names = ()

        if plan is None:
            timeline_name = ""
            timeline_source = ""
            track_name = ""
        else:
            timeline_name = plan.name
            timeline_source = str(plan.source_path)
            track_name = plan.track_name
        audio_track_names = (
            tuple(audio_track.name for audio_track in plan.audio_tracks)
            if plan is not None
            else ()
        )

        status = derive_status(load_result.issues, renderable=plan is not None and segments_included > 0)

        return cls(
            status=status,
            timeline_name=timeline_name,
            timeline_source=timeline_source,
            track_name=track_name,
            video_track_names=video_track_names,
            audio_track_names=audio_track_names,
            segments_included=segments_included,
            segments_skipped=load_result.items_skipped,
            duration_seconds=duration_seconds,
            issues=load_result.issues,
            output=str(output_path),
            dry_run=dry_run,
            ffmpeg_shell=ffmpeg_shell,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "timeline": {
                "name": self.timeline_name,
                "source": self.timeline_source,
                "track": self.track_name,
            },
            "video": {
                "tracks_included": len(self.video_track_names),
                "track_names": list(self.video_track_names),
            },
            "audio": {
                "tracks_included": len(self.audio_track_names),
                "track_names": list(self.audio_track_names),
            },
            "segments": {
                "included": self.segments_included,
                "skipped": self.segments_skipped,
            },
            "duration_seconds": self.duration_seconds,
            "issues": [issue.to_dict() for issue in self.issues],
            "output": self.output,
            "dry_run": self.dry_run,
            "ffmpeg_shell": self.ffmpeg_shell,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def derive_status(
    issues: tuple[TimelineIssue, ...],
    *,
    renderable: bool,
) -> ReportStatus:
    if not renderable or any(issue.severity == "error" for issue in issues):
        return "error"
    if issues:
        return "warning"
    return "ok"
