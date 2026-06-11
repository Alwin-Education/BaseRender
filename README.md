# BaseRender

BaseRender is now organized as a monorepo for a cloud render product:

- `apps/api`: FastAPI app for the API, auth, render job orchestration, and serving the built React UI.
- `apps/web`: Next.js frontend (App Router, Tailwind, shadcn/ui).
- `apps/worker`: background worker process that executes prepared render jobs (Render.com fallback backend).
- `apps/lambda`: AWS Lambda FFmpeg handler for hybrid-render shots, plus the EventBridge notifier Lambda.
- `packages/baserender`: shared OTIO-to-FFmpeg renderer package.

The renderer remains the stable core. The web/API/worker apps are thin scaffolds around it so future work can add queueing, storage, and NLE conversion without rewriting the existing timeline or FFmpeg logic.

For the overall product direction (Amplify UI, unified Lambda backend, validation checklist), see [`docs/roadmap.md`](docs/roadmap.md).

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
python -m pip install -e packages/baserender -e apps/api -e apps/worker -e apps/lambda "pytest>=8"
npm install
```

## Run Locally

Start the API (loads `apps/api/.env`, then repo-root `.env` if present):

```sh
uvicorn baserender_api.app:app --reload
```

Start the web app dev server (rewrites `/media`, `/jobs`, and `/transcode` to the API on port 8000):

```sh
npm run web:dev
```

Open http://localhost:3000 for the UI during development.

### Authentication

The web UI signs users in against the shared Cognito user pool (passwordless email
OTP, per-app roles via `baserender:*` groups) using the Auth.js layer described in
[`docs/reference/auth-cognito.md`](docs/reference/auth-cognito.md). One-time setup
for a new environment:

```sh
scripts/aws/setup-cognito.sh baserender [https://your-amplify-domain]
```

Paste the six printed vars into `apps/web/.env.local` (with `AUTH_URL=http://localhost:3000`
for local dev). Then pick a shared secret and set `BASERENDER_PROXY_TOKEN` to the same
value in both `apps/web/.env.local` and `apps/api/.env` — after Cognito sign-in, the
Next.js middleware forwards API calls with that bearer token instead of the legacy
FastAPI session cookie.

### Phase 2 validation

After `aws configure` (one-time for a new AWS account):

```sh
python scripts/aws/bootstrap_local_env.py
python scripts/aws/provision_stack.py --bucket-name baserender-dev-UNIQUE
python scripts/aws/setup_dev.py
npm run phase2:test
# Terminal 1: uvicorn baserender_api.app:app --host 0.0.0.0 --port 8000
# Terminal 2: npm run web:dev
# Terminal 3: scripts/aws/start_tunnel.sh  → python scripts/aws/update_notifier_url.py https://YOUR-TUNNEL-URL
python scripts/phase2_validate.py
```

Or run `scripts/phase2_run.sh` for bootstrap + tests + local checks.

> **Note:** The FastAPI static UI copy step below is legacy (Render.com deploy). Local development uses the Next.js dev server on port 3000. Production UI deploy is planned for Amplify (see [`docs/roadmap.md`](docs/roadmap.md)).

To run the legacy unified production layout locally (Render.com path only), build the frontend and copy it into the API static directory:

```sh
npm run web:build
# Legacy Render path — Next.js output is not copied to apps/api/static. Use Amplify for UI deploy.
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

### Render backends

`BASERENDER_RENDER_BACKEND` selects how `POST /jobs` executes a render:

- `cloud` (default): the API classifies the OTIO timeline and runs a hybrid **AWS MediaConvert + Lambda** pipeline. MediaConvert handles LUT application, transcoding, truncation, and final stitching; the `apps/lambda` FFmpeg handler renders shots MediaConvert cannot express (keyframes, compositing, dissolves). EventBridge completion events flow back through a notifier Lambda to `POST /internal/events`, and the API tracks multi-step progress (`route` and `steps`) on the job. Timelines the cloud path cannot yet handle fall back to the worker.
- `worker`: the Render.com `baserender-worker` poll loop described above.

The cloud path needs `BASERENDER_MEDIACONVERT_ROLE_ARN` (plus optional MediaConvert/EventBridge settings) and the EventBridge rule + Lambda wiring described in [`docs/reference/s3-iam-policy.md`](docs/reference/s3-iam-policy.md). See [`docs/reference/mediaconvert-architecture.md`](docs/reference/mediaconvert-architecture.md) for the full architecture, phase roadmap, and implementation log.

### Direct transcode

Separate from timeline renders, the web **Transcode** page (`/transcode`) and `POST /transcode` submit one fire-and-forget MediaConvert job per selected S3 file in parallel — no job store or EventBridge orchestration.

### Troubleshooting stuck jobs

BaseRender stores the active job in a single S3 object (`BASERENDER_JOB_STATE_KEY`, default `baserender/jobs/current.json`). New renders are blocked while that file says `queued` or `running`. For cloud-backend jobs the same object also records the `route` and per-step `steps` list; `GET /jobs/{id}` surfaces both so you can see which MediaConvert/Lambda step stalled.

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
