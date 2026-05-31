# baserender

Shared Python package for reading OpenTimelineIO timelines and building/running FFmpeg render commands.

The package is used by the local CLI (`scripts/otio_to_ffmpeg.py`), the FastAPI backend for validation-oriented work, and the background worker for actual renders.

## Key Modules

- `otio_reader.py`: load OTIO and flatten tracks/stacks into render plans
- `timeline_model.py`: internal segment/transform/animation types and `RenderSettings`
- `resolve_effects.py`: parse DaVinci Resolve `Resolve_OTIO` clip metadata
- `ffmpeg_builder.py`: build FFmpeg argv and `filter_complex` graphs
- `ffmpeg_progress.py`: parse FFmpeg `-progress` output for encode status
- `render.py`: orchestrate load, build, and execute
- `defaults.py`: load shared defaults from `config/defaults.json`

Default render settings for the web UI and API live in repo-root `config/defaults.json`. Override the path with `BASERENDER_DEFAULTS_CONFIG`.
