# Media Rendering Workflow

This project has two separate responsibilities that should stay easy to reason about:

1. Read OTIO into a conservative internal timeline plan.
2. Convert that plan into an explicit FFmpeg command.

Keep these layers separate when adding features. OTIO parsing should understand editorial semantics. FFmpeg building should understand media processing and filtergraph constraints.

## Existing Flow

The current flow is:

- `scripts/otio_to_ffmpeg.py` parses CLI options.
- `packages/baserender/src/baserender/otio_reader.py` loads OTIO and returns a `LoadTimelineResult`.
- `packages/baserender/src/baserender/timeline_model.py` stores renderable video track data (one or more composited layers), clip transforms and animation, audio track data, and shared errors/settings.
- `packages/baserender/src/baserender/ffmpeg_builder.py` builds the FFmpeg argv and audio/video filtergraph.
- `packages/baserender/src/baserender/render.py` runs the command, optionally forwarding FFmpeg progress through `packages/baserender/src/baserender/ffmpeg_progress.py`.

Media object filtering for assignment uses `config/supported-media-extensions.json`. Extend that file when new video, still-image, or audio formats should appear in the media picker.

Default render settings for the web UI live in `config/defaults.json` and are loaded by `packages/baserender/src/baserender/defaults.py`. The API exposes them through `GET /media/config`. Edit that file to change the media prefix, output path, container, dimensions, frame rate, and codec defaults shown when the app loads. Set `BASERENDER_DEFAULTS_CONFIG` to point at an alternate JSON file.

## Cloud Render Flow

Both render backends reuse the same renderer package. The backend is selected by `BASERENDER_RENDER_BACKEND` (default `cloud`).

**Cloud backend (MediaConvert + Lambda, default):**

- The web app uploads or selects an OTIO timeline, links media references to S3 objects, attaches LUTs, and submits `POST /jobs`.
- The API classifies the timeline (`orchestrator.classify_job`), submits MediaConvert jobs and/or emits Lambda work, and tracks multi-step progress as `RenderStep`s in the single-slot S3 job state.
- EventBridge completion events flow back through the notifier Lambda to `POST /internal/events`, which calls `orchestrator.advance` to mark steps, emit Lambda shots after truncation, submit the final stitch, and finalize the job.
- The web app polls `GET /jobs/{id}` (returns `route` and `steps`) until the job succeeds or fails.

**Worker backend (Render.com poll loop, fallback):**

- The API stores job state in S3, prepares a worker payload with signed media URLs and artifact keys, and returns job status including progress.
- The worker service polls `POST /worker/jobs/claim` (only returns jobs with `backend == "worker"`), downloads the timeline and LUT artifacts, runs `baserender_worker.job.run_render_job`, sends encode progress through `POST /worker/jobs/{id}/heartbeat`, uploads the finished file, and completes the job with `POST /worker/jobs/{id}/complete`.
- Timelines that the cloud path cannot yet handle fall back to the worker.

Separate from `POST /jobs`, the **direct transcode** path (`POST /transcode`, web `/transcode` page) submits one fire-and-forget MediaConvert job per selected S3 file in parallel, with no job store or EventBridge orchestration.

NLE-to-OTIO conversion is not implemented yet. `POST /conversions` currently returns `not_implemented`.

## Routing Layer (MediaConvert + Lambda)

Cloud renders will be routed between **AWS MediaConvert** (LUT application, transcoding, truncation, stitching) and **AWS Lambda with FFmpeg** (keyframes, compositing, dissolves, and other features MediaConvert cannot express).

After OTIO is loaded into a `TimelinePlan`, `packages/baserender/src/baserender/routing.py` calls `classify_timeline()` to produce a `RoutingPlan`:

- **Full MediaConvert** — zero or one LUT, no complex compositing
- **Per-shot MediaConvert** — multiple LUTs, no complex compositing; final stitch job required
- **Hybrid** — MediaConvert truncates source media and applies LUTs where possible; Lambda runs FFmpeg on proxies; final MediaConvert stitch

For MediaConvert execution, `packages/baserender/src/baserender/mediaconvert.py` converts a `RoutingPlan` plus injected `s3://` URIs into `CreateJob` Settings payloads (`build_full_render_job`, `build_per_shot_lut_job`, `build_truncation_job`, `build_stitch_job`).

Working-directory S3 keys and URIs for intermediate MediaConvert outputs are built by `packages/baserender/src/baserender/storage_layout.py` (e.g. `baserender/jobs/{id}/working/proxy-{n}`, `shot-{n}`, and `final/output`). The API wraps boto3 clients in `apps/api/src/baserender_api/mediaconvert_client.py` and `eventbridge_client.py`; configure them with `BASERENDER_MEDIACONVERT_ROLE_ARN`, optional `BASERENDER_MEDIACONVERT_QUEUE_ARN` and `BASERENDER_MEDIACONVERT_ENDPOINT`, and optional `BASERENDER_EVENT_BUS` / `BASERENDER_EVENT_SOURCE`.

`POST /jobs` now drives this routing through `apps/api/src/baserender_api/orchestrator.py` when `BASERENDER_RENDER_BACKEND=cloud`, and the Lambda FFmpeg handler in `apps/lambda` runs hybrid shots. See [`mediaconvert-architecture.md`](mediaconvert-architecture.md) for the full design, phase roadmap, and implementation log. **When completing a phase, update that document's phase status and implementation log in the same change.**

## Change Strategy

When adding a feature:

- Start with the OTIO schema object or metadata that represents it.
- Decide whether the feature belongs in the internal timeline model.
- Keep vendor dialect parsing, such as Resolve `Resolve_OTIO`, outside the renderer and map it into neutral timeline model fields.
- Preserve structured warnings through `TimelineIssue` for unsupported or partially supported cases.
- Add unit tests for OTIO interpretation and FFmpeg command generation.
- Update `README.md` support notes when user-facing support changes.

## Documentation Strategy

For external details, prefer the official OTIO and FFmpeg docs. For project behavior, keep short notes in this directory so future agents can discover the intended design through codebase search.

If you add a Cursor `@Docs` entry for OpenTimelineIO or FFmpeg, keep this repo rule in place. The rule tells agents when those docs matter; the docs entry provides deeper API lookup.
