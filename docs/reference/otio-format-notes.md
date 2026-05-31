# OTIO Format Notes

These notes describe OTIO concepts that matter to BaseRender's renderer. They are not a replacement for the official OTIO documentation.

## Timeline Semantics

OTIO timelines describe editorial structure, not an FFmpeg command. A timeline can include nested composition, gaps, transitions, effects, metadata, and media references that require interpretation before rendering.

Important concepts:

- Timeline order is not always enough. Transitions can overlap adjacent items, and nested compositions can change how children are evaluated.
- Clip source ranges and timeline ranges are distinct. Use OTIO range APIs to understand the visible media range.
- Gaps have duration and must become generated media, such as a black `color` source, before FFmpeg can concatenate them.
- Effects can be schema objects with vendor metadata. DaVinci Resolve exports can include `Resolve_OTIO` metadata.
- Metadata is extensible and may contain application-specific keys. Treat it as optional and validate before using it.

## BaseRender Mapping

BaseRender maps OTIO into a simple render plan:

- `Clip` becomes `ClipSegment`
- `Gap` becomes `GapSegment`
- `Track` remains serial; nested stack items consume timeline duration at their position in the track
- `Stack` becomes parallel video or audio layers; child layers are padded with synthetic gaps/silence for alignment
- supported `Transition` (after `track_with_expanded_transitions`) becomes `DissolveTransitionSegment` with trimmed overlap clips
- audio `Clip` becomes `AudioClipSegment`
- audio `Gap` becomes `AudioGapSegment`
- supported audio `Transition` becomes `DissolveAudioTransitionSegment`
- unsupported features become `TimelineIssue` entries unless `fail_fast` is enabled
- `ExternalReference.target_url` becomes the FFmpeg media input path or URL
- `trimmed_range().start_time` and `trimmed_range().duration` become clip trim parameters; nested stack source ranges also trim flattened child layers before render-plan creation
- constant Resolve `Transform` metadata becomes a neutral `ClipTransform` with scale, pixel translation, and rotation; keyframed parameters become `ClipAnimation`
- constant Resolve `Cropping` metadata becomes a neutral `ClipCrop` with normalized post-transform canvas-edge left, right, top, and bottom insets; keyframed parameters become `ClipAnimation`

This mapping is intentionally conservative. When expanding it, update both parsing tests and FFmpeg builder tests so timeline semantics and render semantics stay aligned.

## Resolve Transform Metadata

Resolve exports built-in inspector controls as generic OTIO `Effect` objects with vendor data under `metadata["Resolve_OTIO"]`. BaseRender treats this as a Resolve dialect, not as a portable OTIO standard.

Supported `Effect Name == "Transform"` parameter IDs (constant values map to `ClipTransform`, keyframed values map to `ClipAnimation`):

- `transformationZoomX` and `transformationZoomY` become `ClipTransform.scale_x` and `scale_y`.
- `transformationPan` becomes horizontal pixel translation relative to the output canvas width.
- `transformationTilt` becomes vertical pixel translation relative to the output canvas height, with Resolve's positive-up value converted to FFmpeg's positive-down coordinates.
- `transformationRotationAngle` becomes `ClipTransform.rotation_degrees`, with Resolve's rotation direction converted to FFmpeg's canvas rotation direction.

Supported `Effect Name == "Cropping"` parameter IDs (constant values map to `ClipCrop`, keyframed values map to `ClipAnimation`):

- `cropLeft`, `cropRight`, `cropTop`, and `cropBottom` become `ClipCrop.left`, `right`, `top`, and `bottom` as normalized post-transform canvas-edge fractions.

Non-empty `Key Frames` on supported `Transform`, `Cropping`, `Composite` opacity, and `Dynamic Zoom` scale parameters are parsed into neutral `ClipAnimation` values. Custom dissolve curves on supported transitions become `DissolveCurve`. Unknown Resolve parameters and unsupported easing metadata should produce `TimelineIssue` warnings rather than being silently dropped.

## Feature Checklist

Before implementing a new OTIO feature, answer:

- Which OTIO schema object or metadata field represents the feature?
- Is it timeline-level, track-level, clip-level, or media-reference-level?
- Does it affect source time, timeline time, visual output, audio output, or metadata only?
- Can unsupported cases be represented as warnings, or should they be hard errors?
- What FFmpeg filters or input options are required to render it faithfully?
