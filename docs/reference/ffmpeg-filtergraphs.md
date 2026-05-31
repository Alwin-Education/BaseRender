# FFmpeg Filtergraph Notes

Use these notes before editing `packages/baserender/src/baserender/ffmpeg_builder.py` or render behavior. For full details, consult the official FFmpeg docs and Cursor `@Docs` entry if configured.

Canonical references:

- FFmpeg documentation: https://ffmpeg.org/documentation.html
- ffmpeg tool manual: https://ffmpeg.org/ffmpeg.html
- Filters manual: https://ffmpeg.org/ffmpeg-filters.html
- Formats manual: https://ffmpeg.org/ffmpeg-formats.html

## Project Usage

BaseRender currently builds an audio/video command that:

- appends one FFmpeg input per unique media URL (still-image loop/framerate options included in the key); per-segment `trim`/`atrim` select ranges from shared inputs
- uses `trim` to select the visible segment range
- uses `setpts=PTS-STARTPTS` to reset segment timestamps
- applies per-clip `lut3d` when a normalized source URL has a matching `--clip-lut` mapping
- applies clip transforms by scaling, optionally rotating, padding/cropping to the output canvas, and formatting before concat; keyframed Resolve transforms use time-expression filters instead of constant values
- uses `atrim` and `asetpts=PTS-STARTPTS` for audio segment ranges
- builds a video chain per track: per-segment `trim` filters, `xfade` for `DissolveTransitionSegment`, then `concat` of chain parts; multiple video tracks are composited with `overlay` into `[outv]` (upper-track gaps use transparent lavfi sources)
- builds an audio chain per track: per-segment `atrim` filters, `acrossfade` for `DissolveAudioTransitionSegment`, then `concat` of chain parts
- mixes multiple audio tracks with `amix`
- maps `[outv]` and, when audio exists, `[outa]` to the output
- encodes with configurable video/audio codecs. Defaults come from `config/defaults.json`: H.264 (`libx264`) preset `faster`, `8000000` bps video bitrate, `yuv420p`, AAC `192000` bps, and optional `-movflags +faststart` for MP4. Supported alternatives include HEVC (`libx265`), ProRes (`prores_ks`), and PCM (`pcm_s16le`) audio.
- reports encode progress through FFmpeg's `-progress` file output when `render.py` is called with an `on_progress` callback

Video gaps are rendered with a lavfi `color` input when width, height, and fps are provided. Audio gaps are rendered with a lavfi `anullsrc` input.

## Filtergraph Rules

When changing filtergraphs:

- Keep labels deterministic and unique, such as `[v0]`, `[v1]`, and `[outv]`.
- Reset timestamps before concat unless the filter explicitly preserves the desired timeline timing.
- Ensure streams entering `concat` are compatible in media type, dimensions, pixel format, frame rate expectations, and audio presence.
- Keep `-map` explicit so FFmpeg does not auto-select unexpected streams.
- Prefer argument lists over shell strings. Use shell rendering only for reports or debugging.
- Quote only when producing a shell string; do not pre-quote individual argv entries.

## Common Filters

Common filters likely to matter for future BaseRender features:

- `trim` and `atrim` for source ranges
- `setpts` and `asetpts` for timestamp normalization
- `concat` for timeline assembly
- `scale`, `pad`, `fps`, and `format` for stream compatibility
- `rotate` for clip rotation before final canvas normalization
- time-expression `scale`, `rotate`, `crop`, `pad`, `geq` alpha, and `blend` for keyframed transforms, opacity, and custom dissolve curves; constant opacity uses `colorchannelmixer=aa=…` after `format=yuva420p`
- `xfade` and `acrossfade` for linear dissolve transitions (`transition=fade` video, triangular `acrossfade` audio)
- `volume`, `aresample`, and `aformat` for audio support
- `color` and `anullsrc` for generated gaps
- `lut3d` for per-source 3D LUT color correction

Transforms require `--width` and `--height` so each transformed clip can be normalized to the same canvas before `concat`. When one clip in a sequence needs canvas normalization, untransformed clips are fit and padded to the same output dimensions so concat inputs remain compatible.

## Testing Guidance

For every FFmpeg feature, test both the raw argv tuple and the `filter_complex` string. If a change affects actual rendering behavior, add at least one dry-run-style assertion and consider a small integration render when practical.
