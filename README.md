# BaseRender

BaseRender is now organized as a monorepo for a cloud render product:

- `apps/api`: FastAPI app for the API, auth, render job orchestration, and serving the built React UI.
- `apps/web`: Vite + React frontend source (built into `apps/api/static/` for production).
- `apps/worker`: background worker process that executes prepared render jobs.
- `packages/baserender`: shared OTIO-to-FFmpeg renderer package.

The renderer remains the stable core. The web/API/worker apps are thin scaffolds around it so future work can add queueing, storage, and NLE conversion without rewriting the existing timeline or FFmpeg logic.

## Requirements

- Python 3.11, 3.12, or 3.13. Python 3.14 is not currently supported because OpenTimelineIO fails while loading its adapter manifest.
- Node.js 20+ for building the React frontend.
- FFmpeg on `PATH` for local rendering. The worker Docker image installs FFmpeg for Render.com.

## Setup

Copy the example env files and fill in credentials:

```sh
cp apps/api/.env.example apps/api/.env
cp apps/worker/.env.example apps/worker/.env
```

Then install dependencies:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e packages/baserender -e apps/api -e apps/worker "pytest>=8"
npm install
```

## Run Locally

Start the API (loads `apps/api/.env`, then repo-root `.env` if present):

```sh
uvicorn baserender_api.app:app --reload
```

Start the web app dev server (proxies `/auth`, `/media`, and `/jobs` to the API on port 8000):

```sh
npm run web:dev
```

Open http://localhost:5173 for the UI during development.

To run the unified production layout locally, build the frontend and copy it into the API static directory:

```sh
npm run web:build
rm -rf apps/api/static && cp -r apps/web/dist apps/api/static
uvicorn baserender_api.app:app --reload
```

Run the idle worker service (loads `apps/worker/.env`, then repo-root `.env` if present). The worker polls the API for queued jobs, reports encode progress through heartbeats, and uploads finished renders to S3:

```sh
python -m baserender_worker.service
```

Execute a prepared worker job from JSON:

```sh
python -m baserender_worker.main job.json
```

## Render An OTIO Timeline

The compatibility CLI still writes a JSON report to stdout and human-readable warnings to stderr:

```sh
python scripts/otio_to_ffmpeg.py input.otio output.mp4 --dry-run
```

Render with FFmpeg:

```sh
python scripts/otio_to_ffmpeg.py input.otio output.mp4
```

If the timeline contains gaps, provide the output shape:

```sh
python scripts/otio_to_ffmpeg.py input.otio output.mp4 --width 1920 --height 1080 --fps 24
```

Apply per-source 3D LUTs by normalized clip URL:

```sh
python scripts/otio_to_ffmpeg.py input.otio output.mp4 \
  --clip-lut /media/a.mov=/looks/a.cube \
  --clip-lut /media/b.mov=/looks/b.cube
```

## Configuration

Default render settings for the web UI live in `config/defaults.json`. The API serves them through `GET /media/config`. Edit that file to change the media prefix, output path, container, dimensions, frame rate, and codec defaults. Set `BASERENDER_DEFAULTS_CONFIG` to point at an alternate JSON file.

See `apps/api/.env.example` and `apps/worker/.env.example` for S3 credentials, auth, worker token, and optional MediaConvert/EventBridge settings (`BASERENDER_MEDIACONVERT_*`, `BASERENDER_EVENT_*`).

For a least-privilege AWS IAM policy template (read/write, no delete), see [`docs/reference/s3-iam-policy.md`](docs/reference/s3-iam-policy.md).

## Deployment

`render.yaml` defines two Render.com services:

- `baserender`: Python web service running FastAPI and serving the built React UI.
- `baserender-worker`: Docker worker image with FFmpeg installed.

Set `BASERENDER_API_BASE_URL` on the worker to the public URL of the `baserender` web service.

A hybrid **AWS MediaConvert + Lambda** render path is in progress. Phase 1 adds a routing engine that classifies timelines for MediaConvert vs Lambda execution; Phase 2 adds a MediaConvert JSON builder for full-render, per-shot LUT, truncation, and stitch jobs; Phase 3 adds boto3 MediaConvert/EventBridge client wrappers and a shared S3 working-directory layout. See [`docs/reference/mediaconvert-architecture.md`](docs/reference/mediaconvert-architecture.md) for the architecture, phase roadmap, and implementation log.

### Troubleshooting stuck jobs

BaseRender stores the active job in a single S3 object (`BASERENDER_JOB_STATE_KEY`, default `baserender/jobs/current.json`). New renders are blocked while that file says `queued` or `running`.

If the UI reports **Another render job is already active** or **Render output not found**:

1. Open `baserender/jobs/current.json` in your bucket and check `status`, `id`, `heartbeat_at`, and `output`.
2. For an active job, use **Cancel** in the web UI (`DELETE /jobs/current`). That marks the job `failed` in place; you do not need to delete per-job artifacts under `baserender/jobs/{id}/`.
3. To clear a finished job from the UI slot without using the AWS console, call `POST /jobs/current/dismiss` (removes `current.json`).
4. If the worker crashed mid-render, stale jobs are auto-failed after `BASERENDER_JOB_STALE_SECONDS` (default 3600) when the API reads job state or accepts a new render.
5. Confirm the worker can reach the API (`BASERENDER_API_BASE_URL`, matching `BASERENDER_WORKER_TOKEN`) and that IAM allows `PutObject` and `HeadObject` on your output prefix (see [`docs/reference/s3-iam-policy.md`](docs/reference/s3-iam-policy.md)). Output keys follow `media_prefix` in `config/defaults.json` plus `BASERENDER_OUTPUT_PREFIX`.

## Renderer Support

The shared renderer package currently supports native `.otio` timelines, top-level video/audio track assembly, stacks, gaps with configured output dimensions, dissolves (including Resolve custom dissolve curves), per-source LUTs, Resolve transform/crop/opacity animation, Dynamic Zoom scale animation, and JSON render reports. Unsupported features are preserved as structured report issues when possible.

Not supported yet: non-dissolve transitions, Resolve easing/bezier metadata, full Dynamic Zoom center motion, retiming/speed ramps, embedded CDL/LUT metadata, NLE format conversion in the API, and media relinking beyond paths or URLs already present in OTIO.

## Tests

```sh
python -m pytest
npm run web:test
```
