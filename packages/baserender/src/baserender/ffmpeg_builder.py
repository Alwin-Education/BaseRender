from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex

from baserender.animation import ClipAnimation, DissolveCurve, scalar_from_constant
from baserender.ffmpeg_inputs import FFmpegInputRegistry
from baserender.timeline_model import (
    AudioClipSegment,
    AudioGapSegment,
    AudioSegment,
    ClipCrop,
    ClipTransform,
    ClipSegment,
    DissolveAudioTransitionSegment,
    DissolveTransitionSegment,
    GapSegment,
    RenderSettings,
    TimelinePlan,
    TimelineSegment,
    UnsupportedTimelineError,
    VideoTimelineTrack,
)


@dataclass(frozen=True)
class FFmpegCommand:
    args: tuple[str, ...]
    filter_complex: str

    def shell_string(self) -> str:
        return shlex.join(self.args)


def build_ffmpeg_command(
    timeline: TimelinePlan,
    output_path: str | Path,
    *,
    settings: RenderSettings | None = None,
    overwrite: bool = True,
) -> FFmpegCommand:
    settings = settings or RenderSettings()
    output_path = Path(output_path)

    video_tracks = timeline.effective_video_tracks
    if not video_tracks or not any(track.segments for track in video_tracks):
        raise UnsupportedTimelineError("Cannot build FFmpeg command for an empty timeline.")

    args: list[str] = ["ffmpeg", "-hide_banner"]
    if overwrite:
        args.append("-y")

    registry = FFmpegInputRegistry(args, settings)
    filter_parts: list[str] = []
    if timeline.has_multiple_video_tracks:
        if not settings.can_render_gaps:
            raise UnsupportedTimelineError(
                "Multiple video tracks require --width, --height, and --fps so FFmpeg "
                "can normalize layers and generate transparent gaps."
            )
        _build_composite_video_filters(
            registry, filter_parts, video_tracks, settings=settings
        )
    else:
        segments = video_tracks[0].segments
        has_transforms = _segments_have_transforms(segments)
        has_crops = _segments_have_crops(segments)
        if (has_transforms or has_crops) and (
            settings.width is None or settings.height is None
        ):
            raise UnsupportedTimelineError(
                "Transforms and crops require --width and --height so FFmpeg can build a canvas."
            )
        normalize_video = has_transforms or has_crops or (
            _segments_have_gaps(segments)
            and settings.width is not None
            and settings.height is not None
        ) or _segments_have_dissolves(segments)

        chain_labels, _ = _build_video_chain_labels(
            registry,
            filter_parts,
            segments,
            settings=settings,
            normalize_video=normalize_video,
            gap_transparent=False,
        )
        filter_parts.append(_concat_video_chain(chain_labels, "outv"))

    audio_output = _append_audio_filters(registry, filter_parts, timeline, settings)
    filter_complex = ";".join(filter_parts)

    args.extend(["-filter_complex", filter_complex, "-map", "[outv]"])
    if audio_output is not None:
        args.extend(["-map", audio_output])

    _append_output_encoding_args(args, settings, output_path, has_audio=audio_output is not None)

    args.append(str(output_path))

    return FFmpegCommand(args=tuple(args), filter_complex=filter_complex)


def _segments_have_transforms(segments: tuple[TimelineSegment, ...]) -> bool:
    for segment in segments:
        if isinstance(segment, ClipSegment) and _clip_has_transform(segment):
            return True
        if isinstance(segment, DissolveTransitionSegment):
            if _clip_has_transform(segment.outgoing) or _clip_has_transform(segment.incoming):
                return True
    return False


def _segments_have_crops(segments: tuple[TimelineSegment, ...]) -> bool:
    for segment in segments:
        if isinstance(segment, ClipSegment) and _clip_has_crop(segment):
            return True
        if isinstance(segment, DissolveTransitionSegment):
            if _clip_has_crop(segment.outgoing) or _clip_has_crop(segment.incoming):
                return True
    return False


def _clip_has_transform(segment: ClipSegment) -> bool:
    if segment.transform is not None:
        return True
    return segment.animation is not None and segment.animation.has_transform


def _clip_has_crop(segment: ClipSegment) -> bool:
    if segment.crop is not None:
        return True
    return segment.animation is not None and segment.animation.has_crop


def _clip_needs_canvas(segment: ClipSegment) -> bool:
    return _clip_has_transform(segment) or _clip_has_crop(segment) or (
        segment.animation is not None and segment.animation.has_opacity
    )


def _segments_have_dissolves(segments: tuple[TimelineSegment, ...]) -> bool:
    return any(isinstance(segment, DissolveTransitionSegment) for segment in segments)


def _segments_have_gaps(segments: tuple[TimelineSegment, ...]) -> bool:
    return any(isinstance(segment, GapSegment) for segment in segments)


def _build_composite_video_filters(
    registry: FFmpegInputRegistry,
    filter_parts: list[str],
    video_tracks: tuple[VideoTimelineTrack, ...],
    *,
    settings: RenderSettings,
) -> None:
    max_duration = max(track.duration_seconds for track in video_tracks)
    track_output_labels: list[str] = []
    label_index = 0

    for track_index, track in enumerate(video_tracks):
        gap_transparent = track_index > 0
        has_transforms = _segments_have_transforms(track.segments)
        has_crops = _segments_have_crops(track.segments)
        if (has_transforms or has_crops) and (
            settings.width is None or settings.height is None
        ):
            raise UnsupportedTimelineError(
                "Transforms and crops require --width and --height so FFmpeg can build a canvas."
            )
        normalize_video = True

        chain_labels, label_index = _build_video_chain_labels(
            registry,
            filter_parts,
            track.segments,
            settings=settings,
            normalize_video=normalize_video,
            gap_transparent=gap_transparent,
            label_index=label_index,
        )
        track_label = f"vt{track_index}"
        filter_parts.append(_concat_video_chain(chain_labels, track_label))

        pad_seconds = max_duration - track.duration_seconds
        if pad_seconds > 0:
            padded_label = f"vt{track_index}pad"
            if gap_transparent:
                pad_input_index = _append_transparent_gap_input(
                    registry.args,
                    pad_seconds,
                    settings=settings,
                )
                filter_parts.append(
                    f"[{track_label}][{pad_input_index}:v]"
                    f"concat=n=2:v=1:a=0,format=yuva420p[{padded_label}]"
                )
            else:
                filter_parts.append(
                    f"[{track_label}]tpad=stop_mode=add:stop_duration={_format_seconds(pad_seconds)}"
                    f"[{padded_label}]"
                )
            track_label = padded_label

        track_output_labels.append(track_label)

    current_label = track_output_labels[0]
    for overlay_index, overlay_label in enumerate(track_output_labels[1:], start=1):
        output_label = "outv" if overlay_index == len(track_output_labels) - 1 else f"vcomp{overlay_index}"
        filter_parts.append(
            f"[{current_label}][{overlay_label}]overlay=eof_action=pass:format=auto"
            f"[{output_label}]"
        )
        current_label = output_label


def _build_video_chain_labels(
    registry: FFmpegInputRegistry,
    filter_parts: list[str],
    segments: tuple[TimelineSegment, ...],
    *,
    settings: RenderSettings,
    normalize_video: bool,
    gap_transparent: bool,
    label_index: int = 0,
) -> tuple[list[str], int]:
    chain_labels: list[str] = []

    for segment in segments:
        if isinstance(segment, DissolveTransitionSegment):
            blend_label = f"blend{label_index}"
            label_index += 1
            filter_parts.extend(
                _dissolve_video_filters(
                    registry,
                    segment,
                    blend_label,
                    settings=settings,
                    normalize_video=normalize_video,
                    gap_transparent=gap_transparent,
                    label_index=label_index,
                )
            )
            label_index += 2
            chain_labels.append(f"[{blend_label}]")
            continue

        label = f"v{label_index}"
        label_index += 1
        input_index = _append_timeline_input(
            registry,
            segment,
            settings,
            gap_transparent=gap_transparent,
        )
        filter_parts.append(
            _segment_filter(
                input_index,
                segment,
                label,
                settings=settings,
                normalize_video=normalize_video,
                gap_transparent=gap_transparent,
            )
        )
        chain_labels.append(f"[{label}]")

    return chain_labels, label_index


def _dissolve_video_filters(
    registry: FFmpegInputRegistry,
    segment: DissolveTransitionSegment,
    blend_label: str,
    *,
    settings: RenderSettings,
    normalize_video: bool,
    gap_transparent: bool,
    label_index: int,
) -> list[str]:
    outgoing_index = registry.append_clip_input(segment.outgoing)
    incoming_index = registry.append_clip_input(segment.incoming)
    outgoing_label = f"v{label_index}"
    incoming_label = f"v{label_index + 1}"
    offset = segment.outgoing.duration_seconds - segment.duration_seconds
    if offset < 0:
        offset = 0

    return [
        _clip_segment_filter(
            outgoing_index,
            segment.outgoing,
            outgoing_label,
            settings=settings,
            normalize_video=normalize_video,
            gap_transparent=gap_transparent,
        ),
        _clip_segment_filter(
            incoming_index,
            segment.incoming,
            incoming_label,
            settings=settings,
            normalize_video=normalize_video,
            gap_transparent=gap_transparent,
        ),
        _dissolve_blend_filter(
            outgoing_label,
            incoming_label,
            blend_label,
            duration_seconds=segment.duration_seconds,
            offset_seconds=offset,
            dissolve_curve=segment.dissolve_curve,
        ),
    ]


def _concat_video_chain(chain_labels: list[str], output_label: str) -> str:
    if not chain_labels:
        raise UnsupportedTimelineError("Cannot build FFmpeg command for an empty timeline.")
    if len(chain_labels) == 1:
        return f"{chain_labels[0]}copy[{output_label}]"
    return "".join(chain_labels) + f"concat=n={len(chain_labels)}:v=1:a=0[{output_label}]"


def _append_transparent_gap_input(
    args: list[str],
    duration_seconds: float,
    *,
    settings: RenderSettings,
) -> int:
    if not settings.can_render_gaps:
        raise UnsupportedTimelineError(
            "Transparent gaps require --width, --height, and --fps."
        )
    input_index = _current_input_count(args)
    color = (
        f"color=c=black@0.0:s={settings.width}x{settings.height}:"
        f"r={_format_seconds(settings.fps)}:"
        f"d={_format_seconds(duration_seconds)},format=yuva420p"
    )
    args.extend(["-f", "lavfi", "-i", color])
    return input_index


def _append_timeline_input(
    registry: FFmpegInputRegistry,
    segment: TimelineSegment,
    settings: RenderSettings,
    *,
    gap_transparent: bool = False,
) -> int:
    args = registry.args

    if isinstance(segment, ClipSegment):
        return registry.append_clip_input(segment)

    input_index = _current_input_count(args)

    if isinstance(segment, GapSegment):
        if not settings.can_render_gaps:
            raise UnsupportedTimelineError(
                "Gaps require --width, --height, and --fps so FFmpeg can generate black video."
            )

        if gap_transparent:
            return _append_transparent_gap_input(args, segment.duration_seconds, settings=settings)

        color = (
            f"color=c=black:s={settings.width}x{settings.height}:"
            f"r={_format_seconds(settings.fps)}:d={_format_seconds(segment.duration_seconds)}"
        )
        args.extend(["-f", "lavfi", "-i", color])
        return input_index

    raise TypeError(f"Unknown timeline segment type: {type(segment).__name__}")


def _segment_filter(
    input_index: int,
    segment: TimelineSegment,
    label: str,
    *,
    settings: RenderSettings,
    normalize_video: bool,
    gap_transparent: bool = False,
) -> str:
    if isinstance(segment, ClipSegment):
        return _clip_segment_filter(
            input_index,
            segment,
            label,
            settings=settings,
            normalize_video=normalize_video,
            gap_transparent=gap_transparent,
        )

    if isinstance(segment, GapSegment):
        filters = (
            f"[{input_index}:v]"
            f"trim=duration={_format_seconds(segment.duration_seconds)},"
            f"setpts=PTS-STARTPTS"
        )
        if gap_transparent:
            filters += ",format=yuva420p"
        return f"{filters}[{label}]"

    raise TypeError(f"Unknown timeline segment type: {type(segment).__name__}")


def _clip_segment_filter(
    input_index: int,
    segment: ClipSegment,
    label: str,
    *,
    settings: RenderSettings,
    normalize_video: bool,
    gap_transparent: bool = False,
) -> str:
    filters = (
        f"[{input_index}:v]"
        f"trim=start={_format_seconds(segment.start_seconds)}:"
        f"duration={_format_seconds(segment.duration_seconds)},"
        f"setpts=PTS-STARTPTS"
    )
    if segment.lut_path is not None:
        filters += f",lut3d=file={_escape_filter_path(segment.lut_path)}"
    normalized_to_canvas = False
    if segment.animation is not None and not segment.animation.is_identity:
        filters += _animation_filter(
            segment.animation,
            settings,
            transparent=gap_transparent,
        )
        normalized_to_canvas = True
    elif segment.transform is not None:
        filters += _transform_filter(
            segment.transform,
            settings,
            transparent=gap_transparent,
        )
        normalized_to_canvas = True
    elif normalize_video and settings.width is not None and settings.height is not None:
        filters += _fit_canvas_filter(settings, transparent=gap_transparent)
        normalized_to_canvas = True
    if segment.crop is not None:
        filters += _edge_crop_filter(
            segment.crop,
            settings,
            transparent=gap_transparent,
        )
        normalized_to_canvas = True
    if gap_transparent and not normalized_to_canvas:
        filters += ",format=yuva420p"
    return f"{filters}[{label}]"


def _edge_crop_filter(
    crop: ClipCrop,
    settings: RenderSettings,
    *,
    transparent: bool = False,
) -> str:
    width, height = _canvas_size(settings)
    pad_color = _canvas_pad_color(transparent)
    output_format = _canvas_pixel_format(transparent)
    left = _format_seconds(crop.left)
    right = _format_seconds(crop.right)
    top = _format_seconds(crop.top)
    bottom = _format_seconds(crop.bottom)
    return (
        f",crop=w={width}*(1-{left}-{right}):h={height}*(1-{top}-{bottom}):"
        f"x={width}*{left}:y={height}*{top}"
        f",pad=w={width}:h={height}:x={width}*{left}:y={height}*{top}:"
        f"color={pad_color}"
        f",setsar=1,format={output_format}"
    )


def _transform_filter(
    transform: ClipTransform,
    settings: RenderSettings,
    *,
    transparent: bool = False,
) -> str:
    width, height = _canvas_size(settings)
    pad_color = _canvas_pad_color(transparent)
    output_format = _canvas_pixel_format(transparent)
    filters = (
        f",scale=iw*{_format_seconds(transform.scale_x)}:"
        f"ih*{_format_seconds(transform.scale_y)}"
    )

    if transform.rotation_degrees != 0:
        filters += (
            f",rotate={_format_seconds(transform.rotation_degrees)}*PI/180:"
            f"ow=rotw(iw):oh=roth(ih):c={pad_color}"
        )

    translate_x = _format_seconds(transform.translate_x)
    translate_y = _format_seconds(transform.translate_y)
    filters += (
        f",crop=w=min(iw\\,{width}):h=min(ih\\,{height}):"
        f"x={_crop_offset_expression('iw', width, translate_x)}:"
        f"y={_crop_offset_expression('ih', height, translate_y)}"
        f",pad=w={width}:h={height}:"
        f"x={_pad_offset_expression('iw', translate_x)}:"
        f"y={_pad_offset_expression('ih', translate_y)}:"
        f"color={pad_color}"
        f",setsar=1,format={output_format}"
    )
    return filters


def _animation_filter(
    animation: ClipAnimation,
    settings: RenderSettings,
    *,
    transparent: bool = False,
) -> str:
    width, height = _canvas_size(settings)
    pad_color = _canvas_pad_color(transparent)
    output_format = _canvas_pixel_format(transparent)
    needs_alpha = transparent or animation.has_opacity
    filters = ""

    if animation.has_transform:
        scale_x = animation.scale_x or scalar_from_constant(1.0)
        scale_y = animation.scale_y or scalar_from_constant(1.0)
        translate_x = animation.translate_x or scalar_from_constant(0.0)
        translate_y = animation.translate_y or scalar_from_constant(0.0)
        rotation = animation.rotation_degrees or scalar_from_constant(0.0)

        scale_x_expr = scale_x.to_ffmpeg_expr("t")
        scale_y_expr = scale_y.to_ffmpeg_expr("t")
        filters += f",scale=w='iw*({scale_x_expr})':h='ih*({scale_y_expr})'"

        if not (rotation.is_constant and rotation.constant_value == 0):
            rotation_expr = rotation.to_ffmpeg_expr("t")
            filters += (
                f",rotate='({rotation_expr})*PI/180':"
                f"ow=rotw(iw):oh=roth(ih):c={pad_color}"
            )

        translate_x_expr = translate_x.to_ffmpeg_expr("t")
        translate_y_expr = translate_y.to_ffmpeg_expr("t")
        filters += (
            f",crop=w='min(iw\\,{width})':h='min(ih\\,{height})':"
            f"x='{_crop_offset_expression('iw', width, translate_x_expr)}':"
            f"y='{_crop_offset_expression('ih', height, translate_y_expr)}'"
            f",pad=w={width}:h={height}:"
            f"x='{_pad_offset_expression('iw', translate_x_expr)}':"
            f"y='{_pad_offset_expression('ih', translate_y_expr)}':"
            f"color={pad_color}"
        )
    elif animation.has_crop or animation.has_opacity:
        filters += _fit_canvas_filter(settings, transparent=transparent)

    if animation.has_crop:
        left = animation.crop_left or scalar_from_constant(0.0)
        right = animation.crop_right or scalar_from_constant(0.0)
        top = animation.crop_top or scalar_from_constant(0.0)
        bottom = animation.crop_bottom or scalar_from_constant(0.0)
        left_expr = left.to_ffmpeg_expr("t")
        right_expr = right.to_ffmpeg_expr("t")
        top_expr = top.to_ffmpeg_expr("t")
        bottom_expr = bottom.to_ffmpeg_expr("t")
        filters += (
            f",crop=w='{width}*(1-({left_expr})-({right_expr}))':"
            f"h='{height}*(1-({top_expr})-({bottom_expr}))':"
            f"x='{width}*({left_expr})':y='{height}*({top_expr})'"
            f",pad=w={width}:h={height}:"
            f"x='{width}*({left_expr})':y='{height}*({top_expr})':color={pad_color}"
        )

    if animation.has_opacity:
        opacity = animation.opacity or scalar_from_constant(1.0)
        alpha_format = "yuva420p" if needs_alpha else output_format
        if opacity.is_constant:
            filters += (
                f",setsar=1,format={alpha_format},"
                f"colorchannelmixer=aa={_format_seconds(opacity.constant_value)}"
            )
        else:
            # geq uses uppercase T for timestamp; lowercase t is not defined there.
            opacity_expr = opacity.to_ffmpeg_expr("T")
            filters += (
                f",setsar=1,format={alpha_format},geq="
                f"r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='floor(alpha(X,Y)*({opacity_expr}))'"
            )
    else:
        filters += f",setsar=1,format={output_format}"

    return filters


def _dissolve_blend_filter(
    outgoing_label: str,
    incoming_label: str,
    blend_label: str,
    *,
    duration_seconds: float,
    offset_seconds: float,
    dissolve_curve: DissolveCurve | None,
) -> str:
    duration_literal = _format_seconds(duration_seconds)
    offset_literal = _format_seconds(offset_seconds)

    if dissolve_curve is None or dissolve_curve.is_linear:
        return (
            f"[{outgoing_label}][{incoming_label}]"
            f"xfade=transition=fade:duration={duration_literal}:"
            f"offset={offset_literal}"
            f"[{blend_label}]"
        )

    progress_expr = (
        f"clip((t-{offset_literal})/{duration_literal}\\,0\\,1)"
    )
    weight_expr = dissolve_curve.to_ffmpeg_progress_expr(progress_expr)
    return (
        f"[{outgoing_label}][{incoming_label}]"
        f"blend=all_expr="
        f"'if(lt(t\\,{offset_literal})\\,A\\,"
        f"if(gt(t\\,{offset_literal}+{duration_literal})\\,B\\,"
        f"A*(1-({weight_expr}))+B*({weight_expr})))'"
        f"[{blend_label}]"
    )


def _fit_canvas_filter(settings: RenderSettings, *, transparent: bool = False) -> str:
    width, height = _canvas_size(settings)
    pad_color = _canvas_pad_color(transparent)
    output_format = _canvas_pixel_format(transparent)
    return (
        f",scale=w={width}:h={height}:force_original_aspect_ratio=decrease"
        f",pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color={pad_color}"
        f",setsar=1,format={output_format}"
    )


def _canvas_pad_color(transparent: bool) -> str:
    return "black@0.0" if transparent else "black"


def _canvas_pixel_format(transparent: bool) -> str:
    return "yuva420p" if transparent else "yuv420p"


def _canvas_size(settings: RenderSettings) -> tuple[int, int]:
    if settings.width is None or settings.height is None:
        raise UnsupportedTimelineError(
            "Transforms require --width and --height so FFmpeg can build a canvas."
        )
    return settings.width, settings.height


def _crop_offset_expression(axis: str, canvas_size: int, translation: str) -> str:
    return (
        f"max(0\\,min({axis}-{canvas_size}\\,"
        f"{_subtract_expression(f'({axis}-{canvas_size})/2', translation)}))"
    )


def _pad_offset_expression(axis: str, translation: str) -> str:
    return (
        f"max(0\\,min(o{axis[1]}-{axis}\\,"
        f"(o{axis[1]}-{axis})/2+if(lt({axis}\\,o{axis[1]})\\,{translation}\\,0)))"
    )


def _subtract_expression(left: str, right: str) -> str:
    if right.startswith("-"):
        return f"{left}+{right[1:]}"
    return f"{left}-{right}"


def _append_audio_filters(
    registry: FFmpegInputRegistry,
    filter_parts: list[str],
    timeline: TimelinePlan,
    settings: RenderSettings,
) -> str | None:
    track_labels: list[str] = []

    for track_index, audio_track in enumerate(timeline.audio_tracks):
        chain_labels = _build_audio_chain_labels(
            registry,
            filter_parts,
            audio_track.segments,
            track_index=track_index,
            settings=settings,
        )

        if not chain_labels:
            continue

        output_label = "outa" if len(timeline.audio_tracks) == 1 else f"at{track_index}"
        filter_parts.append(_concat_audio_chain(chain_labels, output_label))
        track_labels.append(f"[{output_label}]")

    if not track_labels:
        return None

    if len(track_labels) == 1:
        return track_labels[0]

    filter_parts.append(
        "".join(track_labels)
        + f"amix=inputs={len(track_labels)}:duration=longest:normalize=0[outa]"
    )
    return "[outa]"


def _build_audio_chain_labels(
    registry: FFmpegInputRegistry,
    filter_parts: list[str],
    segments: tuple[AudioSegment, ...],
    *,
    track_index: int,
    settings: RenderSettings,
) -> list[str]:
    chain_labels: list[str] = []
    segment_index = 0

    for segment in segments:
        if isinstance(segment, DissolveAudioTransitionSegment):
            blend_label = f"a{track_index}_blend{segment_index}"
            segment_index += 1
            filter_parts.extend(
                _dissolve_audio_filters(
                    registry,
                    segment,
                    blend_label,
                    track_index=track_index,
                    segment_index=segment_index,
                    settings=settings,
                )
            )
            segment_index += 2
            chain_labels.append(f"[{blend_label}]")
            continue

        label = f"a{track_index}_{segment_index}"
        segment_index += 1
        input_index = _append_audio_input(registry, segment, settings)
        filter_parts.append(_audio_segment_filter(input_index, segment, label, settings))
        chain_labels.append(f"[{label}]")

    return chain_labels


def _dissolve_audio_filters(
    registry: FFmpegInputRegistry,
    segment: DissolveAudioTransitionSegment,
    blend_label: str,
    *,
    track_index: int,
    segment_index: int,
    settings: RenderSettings,
) -> list[str]:
    outgoing_index = registry.append_audio_clip_input(segment.outgoing)
    incoming_index = registry.append_audio_clip_input(segment.incoming)
    outgoing_label = f"a{track_index}_{segment_index}"
    incoming_label = f"a{track_index}_{segment_index + 1}"

    return [
        _audio_clip_segment_filter(outgoing_index, segment.outgoing, outgoing_label, settings),
        _audio_clip_segment_filter(incoming_index, segment.incoming, incoming_label, settings),
        (
            f"[{outgoing_label}][{incoming_label}]"
            f"acrossfade=d={_format_seconds(segment.duration_seconds)}:"
            f"c1=tri:c2=tri[{blend_label}]"
        ),
    ]


def _concat_audio_chain(chain_labels: list[str], output_label: str) -> str:
    if len(chain_labels) == 1:
        return f"{chain_labels[0]}acopy[{output_label}]"
    return "".join(chain_labels) + f"concat=n={len(chain_labels)}:v=0:a=1[{output_label}]"


def _append_audio_input(
    registry: FFmpegInputRegistry,
    segment: AudioSegment,
    settings: RenderSettings,
) -> int:
    args = registry.args
    input_index = _current_input_count(args)

    if isinstance(segment, AudioClipSegment):
        return registry.append_audio_clip_input(segment)

    if isinstance(segment, AudioGapSegment):
        source = (
            "anullsrc="
            f"channel_layout={settings.audio_channel_layout}:"
            f"sample_rate={settings.audio_sample_rate}:"
            f"d={_format_seconds(segment.duration_seconds)}"
        )
        args.extend(["-f", "lavfi", "-i", source])
        return input_index

    raise TypeError(f"Unknown audio segment type: {type(segment).__name__}")


def _audio_segment_filter(
    input_index: int,
    segment: AudioSegment,
    label: str,
    settings: RenderSettings,
) -> str:
    if isinstance(segment, AudioClipSegment):
        return _audio_clip_segment_filter(input_index, segment, label, settings)

    if isinstance(segment, AudioGapSegment):
        return (
            f"[{input_index}:a]"
            f"atrim=duration={_format_seconds(segment.duration_seconds)},"
            f"asetpts=PTS-STARTPTS,"
            f"aresample={settings.audio_sample_rate},"
            f"aformat=channel_layouts={settings.audio_channel_layout}[{label}]"
        )

    raise TypeError(f"Unknown audio segment type: {type(segment).__name__}")


def _audio_clip_segment_filter(
    input_index: int,
    segment: AudioClipSegment,
    label: str,
    settings: RenderSettings,
) -> str:
    return (
        f"[{input_index}:a]"
        f"atrim=start={_format_seconds(segment.start_seconds)}:"
        f"duration={_format_seconds(segment.duration_seconds)},"
        f"asetpts=PTS-STARTPTS,"
        f"aresample={settings.audio_sample_rate},"
        f"aformat=channel_layouts={settings.audio_channel_layout}[{label}]"
    )


def _current_input_count(args: list[str]) -> int:
    return sum(1 for arg in args if arg == "-i")


def _append_output_encoding_args(
    args: list[str],
    settings: RenderSettings,
    output_path: Path,
    *,
    has_audio: bool,
) -> None:
    video_codec = _ffmpeg_video_codec(settings.video_codec)
    args.extend(["-c:v", video_codec])

    if settings.video_codec in {"h264", "hevc"}:
        args.extend(["-preset", settings.video_encoder_preset])
        if settings.video_bitrate:
            args.extend(["-b:v", str(settings.video_bitrate)])
        args.extend(["-pix_fmt", "yuv420p"])
    elif settings.video_codec == "prores":
        args.extend(["-profile:v", "3", "-pix_fmt", "yuv422p10le"])

    if has_audio:
        audio_codec = _ffmpeg_audio_codec(settings.audio_codec)
        args.extend(["-c:a", audio_codec])
        if settings.audio_codec == "aac" and settings.audio_bitrate:
            args.extend(["-b:a", str(settings.audio_bitrate)])

    if settings.video_faststart and output_path.suffix.lower() == ".mp4":
        args.extend(["-movflags", "+faststart"])


def _ffmpeg_video_codec(video_codec: str) -> str:
    return {
        "h264": "libx264",
        "hevc": "libx265",
        "prores": "prores_ks",
    }.get(video_codec, "libx264")


def _ffmpeg_audio_codec(audio_codec: str) -> str:
    return {
        "aac": "aac",
        "pcm": "pcm_s16le",
    }.get(audio_codec, "aac")


def _format_seconds(value: float) -> str:
    if value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _escape_filter_path(path: str) -> str:
    """Escape a filesystem path for FFmpeg filter option values."""
    return (
        path.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
