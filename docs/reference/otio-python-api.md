# OpenTimelineIO Python API Notes

Use these notes as the local starting point before editing BaseRender's OTIO parsing logic. For full API details, consult the official OpenTimelineIO documentation and Cursor `@Docs` entry if configured.

Canonical references:

- OpenTimelineIO docs: https://opentimelineio.readthedocs.io/
- Python API index: https://opentimelineio.readthedocs.io/en/latest/api/python/opentimelineio.html
- GitHub project: https://github.com/PixarAnimationStudios/OpenTimelineIO

## Project Usage

BaseRender currently reads timelines with `otio.adapters.read_from_file(...)` in `packages/baserender/src/baserender/otio_reader.py`, then recursively flattens video and audio `Track` / `Stack` compositions into internal layer plans.

Current supported OTIO features:

- `otio.schema.Timeline`
- top-level and nested video `otio.schema.Track` / `otio.schema.Stack` layers composited by default, or a selected direct video track/stack index via `--track-index`
- top-level and nested audio `otio.schema.Track` / `otio.schema.Stack` layers mixed into one output stream
- `otio.schema.Clip` with `otio.schema.ExternalReference.target_url`
- `otio.schema.Gap` when render dimensions and frame rate are supplied
- audio gaps rendered as generated silence
- disabled or default DaVinci Resolve per-clip pipeline effects
- DaVinci Resolve `Transform` metadata mapped into `ClipTransform` or keyframed `ClipAnimation`
- DaVinci Resolve `Cropping` metadata mapped into `ClipCrop` or keyframed `ClipAnimation`
- DaVinci Resolve `Composite` opacity mapped into `ClipAnimation`
- DaVinci Resolve `Dynamic Zoom` scale keyframes mapped into `ClipAnimation` (center motion is not rendered)
- `Transition` dissolves expanded with `opentimelineio.algorithms.track_with_expanded_transitions` and rendered as `DissolveTransitionSegment` / `DissolveAudioTransitionSegment`, including Resolve custom dissolve curves when present

Current unsupported or warning-producing features:

- non-dissolve transitions
- nested composition types other than serial `Track` and parallel `Stack`
- retiming, speed ramps, and effects outside supported Resolve Transform/Cropping/Composite/Dynamic Zoom metadata
- media references that are not `ExternalReference`
- embedded CDL or other color transforms not represented in supported Resolve metadata
- Resolve easing/bezier metadata on keyframes
- full Dynamic Zoom center motion (scale-only approximation with a warning)

## OTIO Object Model

Do not treat OTIO as plain JSON unless there is no API alternative. Prefer schema objects and methods:

- `Timeline` owns a top-level `tracks` stack.
- `Stack` composes multiple child tracks or compositions in parallel. In BaseRender, later stack layers overlay earlier video layers, and audio stack layers are mixed.
- `Track` is ordered in timeline order and has a `kind`, commonly `TrackKind.Video` or `TrackKind.Audio`.
- `Clip` represents source-backed media.
- `Gap` represents empty timeline duration.
- `Transition` overlaps adjacent timeline items. BaseRender expands them with `track_with_expanded_transitions` before mapping to dissolve segments.
- `ExternalReference` usually stores the source path or URL in `target_url`.
- `RationalTime` stores a value and rate.
- `TimeRange` stores `start_time` and `duration`.

Use `trimmed_range()` for the timeline-visible range of a clip, gap, track, or stack. When a nested stack has a source range, trim child layers to that visible range before creating FFmpeg inputs so rendering does not cover hidden source duration. Convert `RationalTime` to seconds as `value / rate`, and be careful when mixing rates.

## Implementation Guidance

When adding OTIO support:

- Add behavior through the OTIO Python API first, then map it into `timeline_model` types.
- Keep unsupported features visible through `TimelineIssue` reports instead of silently dropping behavior.
- Preserve `fail_fast` behavior for users who want strict errors.
- Add tests with representative OTIO schema objects, not only fixture JSON.
- Check how the feature interacts with FFmpeg timestamps and concat requirements before parsing it.
