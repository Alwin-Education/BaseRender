#!/usr/bin/env python3
"""Phase 2 validation: automated checks for local API flows."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
    from baserender_api.env import load_local_env

    load_local_env()


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    cookie: str | None = None,
) -> tuple[int, dict | str]:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    if cookie:
        headers["Cookie"] = cookie

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return exc.code, payload


def _login(base_url: str, password: str) -> str:
    status, payload = _request(
        base_url,
        "POST",
        "/auth/login",
        body={"password": password},
    )
    if status != 200:
        raise SystemExit(f"Login failed ({status}): {payload}")

    session_request = urllib.request.Request(
        f"{base_url.rstrip('/')}/auth/login",
        data=json.dumps({"password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(session_request, timeout=30) as response:
        cookies = response.headers.get("Set-Cookie")
        if not cookies:
            raise SystemExit("Login succeeded but no session cookie was returned.")
        return cookies.split(";", 1)[0]


def _otio_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _link_and_submit(
    base_url: str,
    cookie: str,
    *,
    otio_path: Path,
    assignments: dict[str, str],
    lut_files: list[dict] | None = None,
    lut_assignments: dict[str, str] | None = None,
) -> dict:
    otio_b64 = _otio_base64(otio_path)
    otio_text = otio_path.read_text(encoding="utf-8")
    link_status, link_payload = _request(
        base_url,
        "POST",
        "/media/linking",
        body={"otio_content_base64": otio_b64},
        cookie=cookie,
    )
    if link_status != 200:
        raise SystemExit(f"Media linking failed ({link_status}): {link_payload}")

    references = link_payload.get("references") or []
    media_refs = []
    media_assignments = {}
    for index, reference in enumerate(references):
        ref_id = reference.get("id") or f"ref-{index}"
        media_refs.append(
            {
                "id": ref_id,
                "clip_name": reference.get("clip_name"),
                "track_path": reference.get("track_path"),
                "reference_kind": reference.get("reference_kind"),
                "target_url": reference.get("target_url"),
                "normalized_url": reference.get("normalized_url"),
                "status": reference.get("status"),
                "clip_count": reference.get("clip_count"),
            }
        )
        normalized = reference.get("normalized_url") or ""
        for key, value in assignments.items():
            if key in normalized or key in (reference.get("target_url") or ""):
                media_assignments[ref_id] = value

    job_body: dict = {
        "output_path": "outputs/phase2-smoke.mp4",
        "dry_run": False,
        "settings": {"width": 640, "height": 360, "fps": 24},
        "otio_content_base64": otio_b64,
        "media_references": media_refs,
        "media_assignments": media_assignments,
    }
    if lut_files:
        job_body["lut_files"] = lut_files
    if lut_assignments:
        job_body["lut_assignments"] = lut_assignments

    status, payload = _request(base_url, "POST", "/jobs", body=job_body, cookie=cookie)
    if status != 202:
        raise SystemExit(f"Job submit failed ({status}): {payload}")
    return payload


def _transcode_smoke(base_url: str, cookie: str, *, input_key: str) -> dict:
    status, payload = _request(
        base_url,
        "POST",
        "/transcode",
        body={
            "inputs": [input_key],
            "settings": {"width": 640, "height": 360, "fps": 24},
            "container": "mp4",
            "dry_run": False,
        },
        cookie=cookie,
    )
    if status != 200:
        raise SystemExit(f"Transcode failed ({status}): {payload}")
    return payload


def _poll_job(base_url: str, cookie: str, *, timeout_seconds: int = 900) -> dict:
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status, payload = _request(base_url, "GET", "/jobs/current", cookie=cookie)
        if status == 404:
            time.sleep(5)
            continue
        if status != 200:
            raise SystemExit(f"Job poll failed ({status}): {payload}")
        job_status = payload.get("status")
        print(f"  job {payload.get('id')} status={job_status} route={payload.get('route')}")
        if job_status in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(10)
    raise SystemExit("Timed out waiting for job completion.")


def _cli_dry_run(otio_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "otio_to_ffmpeg.py"),
            str(otio_path),
            "out.mp4",
            "--dry-run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"CLI dry-run failed:\n{result.stderr}")
    report = json.loads(result.stdout)
    command = report.get("ffmpeg_shell") or report.get("command")
    if not command:
        raise SystemExit("CLI dry-run returned no FFmpeg command.")
    print(f"  CLI dry-run ok ({len(command)} char command)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 validation checks")
    parser.add_argument("--api-base-url", default=os.getenv("BASERENDER_VALIDATE_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--skip-cloud-jobs", action="store_true")
    parser.add_argument("--skip-transcode", action="store_true")
    args = parser.parse_args()

    _load_env()
    password = os.getenv("BASERENDER_AUTH_PASSWORD", "")
    if not password:
        raise SystemExit("Set BASERENDER_AUTH_PASSWORD in apps/api/.env")

    fixtures = ROOT / "fixtures"
    sample_otio = fixtures / "sample.otio"
    if not sample_otio.is_file():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_phase2_fixtures.py")], check=True)

    print("2.2 CLI sanity check")
    _cli_dry_run(sample_otio)

    print("2.2 Login / session")
    cookie = _login(args.api_base_url, password)
    session_status, session_payload = _request(args.api_base_url, "GET", "/auth/session", cookie=cookie)
    if session_status != 200 or not session_payload.get("authenticated"):
        raise SystemExit(f"Session check failed ({session_status}): {session_payload}")
    print("  session authenticated")

    prefix = json.loads((ROOT / "config" / "defaults.json").read_text())["media_prefix"]
    input_key = f"{prefix}Shot_A.mov"

    if not args.skip_transcode:
        print("2.2 Transcode smoke")
        transcode = _transcode_smoke(args.api_base_url, cookie, input_key=input_key)
        job_id = transcode[0].get("mediaconvert_job_id")
        if not job_id:
            raise SystemExit(f"Transcode returned no MediaConvert job id: {transcode}")
        print(f"  transcode mediaconvert_job_id={job_id}")

    if args.skip_cloud_jobs:
        print("Skipped cloud render jobs (--skip-cloud-jobs)")
        return

    bucket_prefix = prefix
    assignments = {
        "Shot_A.mov": f"{bucket_prefix}Shot_A.mov",
        "Shot_B.mov": f"{bucket_prefix}Shot_B.mov",
    }
    lut_bytes = (ROOT / "packages" / "baserender" / "tests" / "test.cube").read_bytes()

    print("2.3 full_mediaconvert smoke")
    full_job = _link_and_submit(
        args.api_base_url,
        cookie,
        otio_path=sample_otio,
        assignments=assignments,
    )
    assert full_job.get("route") == "full_mediaconvert", full_job
    print(f"  submitted route={full_job.get('route')} steps={len(full_job.get('steps') or [])}")

    print("2.3 per_shot_mediaconvert smoke")
    link_status, two_clip_link = _request(
        args.api_base_url,
        "POST",
        "/media/linking",
        body={"otio_content_base64": _otio_base64(fixtures / "two_clip.otio")},
        cookie=cookie,
    )
    if link_status != 200:
        raise SystemExit(f"Two-clip linking failed ({link_status}): {two_clip_link}")
    lut_assignments: dict[str, str] = {}
    for index, reference in enumerate(two_clip_link.get("references") or []):
        ref_id = reference.get("id") or f"ref-{index}"
        clip_name = reference.get("clip_name") or ""
        lut_assignments[ref_id] = "lut-a" if "A" in clip_name else "lut-b"

    per_shot = _link_and_submit(
        args.api_base_url,
        cookie,
        otio_path=fixtures / "two_clip.otio",
        assignments=assignments,
        lut_files=[
            {"id": "lut-a", "name": "a.cube", "content_base64": base64.b64encode(lut_bytes).decode("ascii")},
            {"id": "lut-b", "name": "b.cube", "content_base64": base64.b64encode(lut_bytes).decode("ascii")},
        ],
        lut_assignments=lut_assignments,
    )
    assert per_shot.get("route") == "per_shot_mediaconvert", per_shot
    print(f"  submitted route={per_shot.get('route')} steps={len(per_shot.get('steps') or [])}")

    print("2.3 hybrid smoke (submit only; poll separately if tunnel configured)")
    hybrid_link_status, hybrid_link = _request(
        args.api_base_url,
        "POST",
        "/media/linking",
        body={"otio_content_base64": _otio_base64(fixtures / "hybrid.otio")},
        cookie=cookie,
    )
    if hybrid_link_status != 200:
        raise SystemExit(f"Hybrid linking failed ({hybrid_link_status}): {hybrid_link}")
    hybrid_lut_assignments: dict[str, str] = {}
    for index, reference in enumerate(hybrid_link.get("references") or []):
        ref_id = reference.get("id") or f"ref-{index}"
        clip_name = reference.get("clip_name") or ""
        if "B" in clip_name:
            hybrid_lut_assignments[ref_id] = "lut-b"

    hybrid = _link_and_submit(
        args.api_base_url,
        cookie,
        otio_path=fixtures / "hybrid.otio",
        assignments=assignments,
        lut_files=[
            {"id": "lut-b", "name": "b.cube", "content_base64": base64.b64encode(lut_bytes).decode("ascii")},
        ],
        lut_assignments=hybrid_lut_assignments,
    )
    assert hybrid.get("route") == "hybrid", hybrid
    print(f"  submitted route={hybrid.get('route')} steps={len(hybrid.get('steps') or [])}")

    api_base_for_callbacks = os.getenv("BASERENDER_API_BASE_URL", "")
    if api_base_for_callbacks and "localhost" not in api_base_for_callbacks:
        print("2.3 polling full_mediaconvert job to completion")
        result = _poll_job(args.api_base_url, cookie)
        if result.get("status") != "succeeded":
            raise SystemExit(f"Cloud job did not succeed: {result}")
        print("  cloud job succeeded")
    else:
        print(
            "Skipping job completion poll (set BASERENDER_API_BASE_URL to a public tunnel URL "
            "and update baserender-notifier Lambda env to enable end-to-end completion)."
        )

    print("Phase 2 validation checks passed.")


if __name__ == "__main__":
    main()
