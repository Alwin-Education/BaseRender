from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import traceback
import posixpath
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from baserender.ffmpeg_progress import FfmpegCancelledError, FfmpegProgress
from baserender_worker.job import RenderJob, run_render_job


class JobSupersededError(RuntimeError):
    """Raised when the API no longer recognizes the worker's job."""


ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
CANCEL_POLL_INTERVAL_SECONDS = 0.25


class WorkerApiClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def claim_job(self) -> dict[str, Any] | None:
        payload = self._json_request("POST", "/worker/jobs/claim")
        return payload or None

    def download_input(self, job_id: str, destination: Path) -> None:
        destination.write_bytes(self._bytes_request("GET", f"/worker/jobs/{job_id}/artifacts/input"))

    def download_lut(self, job_id: str, lut_id: str, destination: Path) -> None:
        destination.write_bytes(
            self._bytes_request("GET", f"/worker/jobs/{job_id}/artifacts/luts/{lut_id}")
        )

    def get_output_upload_target(self, job_id: str) -> dict[str, Any]:
        payload = self._json_request(
            "GET",
            f"/worker/jobs/{job_id}/artifacts/output/upload-target",
        )
        if not payload:
            raise ValueError("Output upload target was empty.")
        return payload

    def upload_output(self, job_id: str, path: Path) -> str:
        target = self.get_output_upload_target(job_id)
        url = target.get("url")
        if not url:
            raise ValueError("Output upload target did not include a URL.")
        headers = {
            str(key): str(value)
            for key, value in dict(target.get("headers") or {}).items()
        }
        self._put_file(str(url), path, headers=headers)
        return str(target.get("key") or "")

    def complete_job(self, job_id: str, report: dict[str, Any]) -> None:
        try:
            self._json_request(
                "POST",
                f"/worker/jobs/{job_id}/complete",
                {"report": report},
            )
        except HTTPError as exc:
            if exc.code == 404:
                raise JobSupersededError(f"Render job {job_id} was superseded.") from exc
            if exc.code == 409:
                raise exc
            raise

    def fail_job(self, job_id: str, message: str, detail: str | None = None) -> None:
        try:
            self._json_request(
                "POST",
                f"/worker/jobs/{job_id}/fail",
                {"message": message, "detail": detail},
            )
        except HTTPError as exc:
            if exc.code == 404:
                return
            raise

    def heartbeat(
        self,
        job_id: str,
        *,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {"progress": progress} if progress is not None else {}
        try:
            response = self._json_request("POST", f"/worker/jobs/{job_id}/heartbeat", payload)
        except HTTPError as exc:
            if exc.code == 404:
                raise JobSupersededError(f"Render job {job_id} was superseded.") from exc
            raise
        if not response:
            raise JobSupersededError(f"Render job {job_id} was superseded.")
        return response

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        data = self._bytes_request(method, path, body=body, content_type="application/json")
        return json.loads(data.decode("utf-8")) if data else None

    def _bytes_request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        headers = {"Authorization": f"Bearer {self.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        with urlopen(request, timeout=60) as response:
            return response.read()

    def _put_file(
        self,
        url: str,
        path: Path,
        *,
        headers: dict[str, str],
        authenticate: bool = False,
    ) -> None:
        request_headers = dict(headers)
        request_headers["Content-Length"] = str(path.stat().st_size)
        if authenticate:
            request_headers["Authorization"] = f"Bearer {self.token}"

        with path.open("rb") as body:
            request = Request(
                url,
                data=body,
                headers=request_headers,
                method="PUT",
            )
            with urlopen(request, timeout=60) as response:
                response.read()


def _mark_job_cancelled(cancelled: threading.Event) -> None:
    cancelled.set()


def _poll_for_cancel(
    client: WorkerApiClient,
    job_id: str,
    *,
    cancelled: threading.Event,
    stop_poll: threading.Event,
) -> None:
    while not stop_poll.is_set():
        try:
            job = client.heartbeat(job_id)
            if job.get("status") not in ACTIVE_JOB_STATUSES:
                _mark_job_cancelled(cancelled)
                return
        except JobSupersededError:
            _mark_job_cancelled(cancelled)
            return
        stop_poll.wait(CANCEL_POLL_INTERVAL_SECONDS)


def run_once(client: WorkerApiClient) -> bool:
    claim = client.claim_job()
    if claim is None:
        return False

    job_id = str(claim["id"])
    payload = dict(claim["worker_payload"])
    cancelled = threading.Event()
    stop_poll = threading.Event()
    poll_thread = threading.Thread(
        target=_poll_for_cancel,
        args=(client, job_id),
        kwargs={"cancelled": cancelled, "stop_poll": stop_poll},
        daemon=True,
    )
    poll_thread.start()

    job_finished = False
    last_upload_key = str(payload.get("output_object_key") or "")

    try:
        with tempfile.TemporaryDirectory(prefix=f"baserender-{job_id}-") as workspace:
            workspace_path = Path(workspace)
            input_path = workspace_path / "timeline.otio"
            output_path = workspace_path / safe_relative_output_path(
                str(payload.get("output_path") or "output.mp4")
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if str(payload.get("input_path", "")).startswith("artifact://"):
                client.download_input(job_id, input_path)
            else:
                input_path = Path(str(payload["input_path"]))

            settings = dict(payload.get("settings") or {})
            settings["clip_luts"] = download_luts(client, job_id, payload, workspace_path)
            payload["settings"] = settings
            payload["input_path"] = str(input_path)
            payload["output_path"] = str(output_path)

            def on_progress(progress: FfmpegProgress) -> None:
                try:
                    print(
                        f"Encode {progress.percent:.1f}% | "
                        f"frame {progress.frame} | speed {progress.speed}x",
                        flush=True,
                    )
                    client.heartbeat(
                        job_id,
                        progress={
                            "percent": progress.percent,
                            "elapsed_seconds": progress.elapsed_seconds,
                            "eta_seconds": progress.eta_seconds,
                            "out_time_seconds": progress.out_time_seconds,
                            "frame": progress.frame,
                            "fps": progress.fps,
                            "speed": progress.speed,
                            "phase": "encoding",
                        },
                    )
                except JobSupersededError:
                    cancelled.set()

            encode_started_at = time.monotonic()
            report = run_render_job(
                RenderJob.from_mapping(payload),
                on_progress=on_progress if not bool(payload.get("dry_run", False)) else None,
                should_cancel=cancelled.is_set,
            )
            if cancelled.is_set():
                raise FfmpegCancelledError("FFmpeg render was cancelled.")
            if not bool(payload.get("dry_run", False)) and output_path.exists():
                try:
                    client.heartbeat(
                        job_id,
                        progress={
                            "percent": 100.0,
                            "elapsed_seconds": time.monotonic() - encode_started_at,
                            "eta_seconds": None,
                            "phase": "uploading",
                        },
                    )
                except JobSupersededError:
                    cancelled.set()
                    raise
                if cancelled.is_set():
                    raise FfmpegCancelledError("FFmpeg render was cancelled.")
                last_upload_key = client.upload_output(job_id, output_path) or last_upload_key
            client.complete_job(job_id, report)
            job_finished = True
    except JobSupersededError:
        job_finished = True
        _mark_job_cancelled(cancelled)
        print(f"Render job {job_id} was superseded; stopping worker.", flush=True)
    except FfmpegCancelledError:
        job_finished = True
        print(f"Render job {job_id} was cancelled; stopping worker.", flush=True)
    except HTTPError as exc:
        if exc.code == 404:
            job_finished = True
            print(f"Render job {job_id} was superseded; stopping worker.", flush=True)
        elif exc.code == 409:
            print(
                "Render complete rejected with HTTP 409. "
                f"Expected S3 output key: {last_upload_key or 'unknown'}",
                flush=True,
            )
            client.fail_job(
                job_id,
                "Render output was not uploaded or is empty.",
                traceback.format_exc(),
            )
            job_finished = True
        else:
            client.fail_job(job_id, str(exc), traceback.format_exc())
            job_finished = True
    except Exception as exc:
        client.fail_job(job_id, str(exc), traceback.format_exc())
        job_finished = True
    finally:
        stop_poll.set()
        poll_thread.join(timeout=2.0)
        if not job_finished:
            try:
                client.fail_job(
                    job_id,
                    "Worker exited before completing the render job.",
                )
            except Exception:
                pass
    return True


def download_luts(
    client: WorkerApiClient,
    job_id: str,
    payload: dict[str, Any],
    workspace_path: Path,
) -> dict[str, str]:
    lut_dir = workspace_path / "luts"
    lut_dir.mkdir(exist_ok=True)
    luts_by_id = {
        str(lut["id"]): lut
        for lut in (payload.get("artifacts") or {}).get("luts", [])
    }
    clip_luts: dict[str, str] = {}
    for item in payload.get("clip_lut_artifacts") or []:
        lut_id = str(item["lut_id"])
        normalized_url = str(item["normalized_url"])
        lut = luts_by_id.get(lut_id)
        if lut is None:
            continue
        lut_path = lut_dir / safe_filename(str(lut.get("name") or f"{lut_id}.cube"))
        client.download_lut(job_id, lut_id, lut_path)
        lut_path_text = str(lut_path)
        media_url = str(item.get("media_url") or normalized_url)
        clip_luts[media_url] = lut_path_text
        clip_luts[normalized_url] = lut_path_text
    return clip_luts


def safe_relative_output_path(value: str) -> str:
    raw = (value or "output.mp4").strip().replace("\\", "/")
    if raw in {"", "."}:
        return "output.mp4"
    if raw.startswith("/"):
        raise ValueError("Output path must be relative.")

    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts:
        return "output.mp4"
    if any(part == ".." for part in parts):
        raise ValueError("Output path cannot contain '..'.")
    return posixpath.normpath("/".join(parts))


def safe_filename(value: str) -> str:
    return Path(value.replace("\\", "/")).name or "lut.cube"


def main() -> int:
    interval_seconds = int(os.environ.get("BASERENDER_WORKER_IDLE_SECONDS", "60"))
    api_base_url = os.environ.get("BASERENDER_API_BASE_URL", "http://localhost:8000")
    token = os.environ.get("BASERENDER_WORKER_TOKEN")
    if not token:
        print("BASERENDER_WORKER_TOKEN must be set.", flush=True)
        return 2

    client = WorkerApiClient(base_url=api_base_url, token=token)
    print("BaseRender worker started. Waiting for a single active job.", flush=True)
    while True:
        try:
            did_work = run_once(client)
        except HTTPError as exc:
            print(f"Worker API request failed with HTTP {exc.code}.", flush=True)
            did_work = False
        except Exception as exc:
            print(f"Worker loop failed: {exc}", flush=True)
            did_work = False
        if did_work:
            continue
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
