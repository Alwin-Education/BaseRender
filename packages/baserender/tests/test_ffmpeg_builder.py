from __future__ import annotations

from pathlib import Path

import pytest

from baserender.animation import (
    AnimatedScalar,
    ClipAnimation,
    DissolveCurve,
    KeyframePoint,
)
from baserender.ffmpeg_builder import build_ffmpeg_command
from baserender.timeline_model import (
    AudioClipSegment,
    AudioGapSegment,
    AudioTimelineTrack,
    ClipCrop,
    ClipTransform,
    ClipSegment,
    DissolveAudioTransitionSegment,
    DissolveTransitionSegment,
    GapSegment,
    RenderSettings,
    TimelinePlan,
    UnsupportedTimelineError,
    VideoTimelineTrack,
)


def test_build_ffmpeg_command_trims_and_concats_clips() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment("A", "/media/a.mov", start_seconds=1.5, duration_seconds=2),
            ClipSegment("B", "/media/b.mov", start_seconds=0, duration_seconds=3.25),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert command.args[:5] == ("ffmpeg", "-hide_banner", "-y", "-i", "/media/a.mov")
    assert "[0:v]trim=start=1.5:duration=2,setpts=PTS-STARTPTS[v0]" in command.filter_complex
    assert "[1:v]trim=start=0:duration=3.25,setpts=PTS-STARTPTS[v1]" in command.filter_complex
    assert "[v0][v1]concat=n=2:v=1:a=0[outv]" in command.filter_complex
    assert command.args[-1] == "output.mp4"


def test_gap_segments_require_render_settings() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(GapSegment("gap", duration_seconds=1),),
    )

    with pytest.raises(UnsupportedTimelineError, match="Gaps require"):
        build_ffmpeg_command(timeline, "output.mp4")


def test_gap_segments_generate_black_video_input() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(GapSegment("gap", duration_seconds=1),),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert "color=c=black:s=1920x1080:r=24:d=1" in command.args
    assert "[0:v]trim=duration=1,setpts=PTS-STARTPTS[v0]" in command.filter_complex


def test_still_image_clip_loops_input_for_timeline_duration() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "Overlay",
                "/media/overlay.png",
                start_seconds=0,
                duration_seconds=1.72,
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert command.args[:9] == (
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-loop",
        "1",
        "-framerate",
        "24",
        "-i",
        "/media/overlay.png",
    )
    assert "[0:v]trim=start=0:duration=1.72,setpts=PTS-STARTPTS[v0]" in command.filter_complex


def test_cropped_clip_requires_output_canvas() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                crop=ClipCrop(left=0.34),
            ),
        ),
    )

    with pytest.raises(UnsupportedTimelineError, match="Transforms and crops require"):
        build_ffmpeg_command(timeline, "output.mp4")


def test_cropped_clip_applies_edge_crop_and_normalizes_siblings() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                crop=ClipCrop(left=0.34),
            ),
            ClipSegment("B", "/media/b.mov", start_seconds=0, duration_seconds=1),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert (
        "[0:v]trim=start=0:duration=1,setpts=PTS-STARTPTS,"
        "scale=w=1920:h=1080:force_original_aspect_ratio=decrease,"
        "pad=w=1920:h=1080:x=(ow-iw)/2:y=(oh-ih)/2:color=black,"
        "setsar=1,format=yuv420p,"
        "crop=w=1920*(1-0.34-0):h=1080*(1-0-0):x=1920*0.34:y=1080*0,"
        "pad=w=1920:h=1080:x=1920*0.34:y=1080*0:color=black,"
        "setsar=1,format=yuv420p[v0]"
    ) in command.filter_complex
    assert (
        "[1:v]trim=start=0:duration=1,setpts=PTS-STARTPTS,"
        "scale=w=1920:h=1080:force_original_aspect_ratio=decrease,"
        "pad=w=1920:h=1080:x=(ow-iw)/2:y=(oh-ih)/2:color=black,"
        "setsar=1,format=yuv420p[v1]"
    ) in command.filter_complex
    assert "[v0][v1]concat=n=2:v=1:a=0[outv]" in command.filter_complex


def test_cropped_clip_with_lut_applies_crop_after_lut3d_and_canvas_fit() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=2,
                crop=ClipCrop(left=0.1),
                lut_path="/looks/a.cube",
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert (
        "[0:v]trim=start=0:duration=2,setpts=PTS-STARTPTS,"
        "lut3d=file=/looks/a.cube,"
        "scale=w=1920:h=1080:force_original_aspect_ratio=decrease,"
        "pad=w=1920:h=1080:x=(ow-iw)/2:y=(oh-ih)/2:color=black,"
        "setsar=1,format=yuv420p,"
        "crop=w=1920*(1-0.1-0):h=1080*(1-0-0):x=1920*0.1:y=1080*0,"
        "pad=w=1920:h=1080:x=1920*0.1:y=1080*0:color=black,"
        "setsar=1,format=yuv420p[v0]"
    ) in command.filter_complex


def test_cropped_clip_with_transform_applies_crop_after_scale() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                crop=ClipCrop(left=0.1),
                transform=ClipTransform(scale_x=2),
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert (
        "[0:v]trim=start=0:duration=1,setpts=PTS-STARTPTS,"
        "scale=iw*2:ih*1,"
        "crop=w=min(iw\\,1920):h=min(ih\\,1080):"
        "x=max(0\\,min(iw-1920\\,(iw-1920)/2-0)):"
        "y=max(0\\,min(ih-1080\\,(ih-1080)/2-0)),"
        "pad=w=1920:h=1080:"
        "x=max(0\\,min(ow-iw\\,(ow-iw)/2+if(lt(iw\\,ow)\\,0\\,0))):"
        "y=max(0\\,min(oh-ih\\,(oh-ih)/2+if(lt(ih\\,oh)\\,0\\,0))):"
        "color=black,setsar=1,format=yuv420p,"
        "crop=w=1920*(1-0.1-0):h=1080*(1-0-0):x=1920*0.1:y=1080*0,"
        "pad=w=1920:h=1080:x=1920*0.1:y=1080*0:color=black,"
        "setsar=1,format=yuv420p[v0]"
    ) in command.filter_complex


def test_transformed_clip_requires_output_canvas() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                transform=ClipTransform(scale_x=2),
            ),
        ),
    )

    with pytest.raises(UnsupportedTimelineError, match="Transforms and crops require"):
        build_ffmpeg_command(timeline, "output.mp4")


def test_transformed_clip_is_scaled_positioned_and_canvas_normalized() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                transform=ClipTransform(
                    scale_x=2,
                    scale_y=1.5,
                    translate_x=120,
                    translate_y=-54,
                    rotation_degrees=-1.1,
                ),
            ),
            ClipSegment("B", "/media/b.mov", start_seconds=0, duration_seconds=1),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert (
        "[0:v]trim=start=0:duration=1,setpts=PTS-STARTPTS,"
        "scale=iw*2:ih*1.5,"
        "rotate=-1.1*PI/180:ow=rotw(iw):oh=roth(ih):c=black,"
        "crop=w=min(iw\\,1920):h=min(ih\\,1080):"
        "x=max(0\\,min(iw-1920\\,(iw-1920)/2-120)):"
        "y=max(0\\,min(ih-1080\\,(ih-1080)/2+54)),"
        "pad=w=1920:h=1080:"
        "x=max(0\\,min(ow-iw\\,(ow-iw)/2+if(lt(iw\\,ow)\\,120\\,0))):"
        "y=max(0\\,min(oh-ih\\,(oh-ih)/2+if(lt(ih\\,oh)\\,-54\\,0))):"
        "color=black,setsar=1,format=yuv420p[v0]"
    ) in command.filter_complex
    assert (
        "[1:v]trim=start=0:duration=1,setpts=PTS-STARTPTS,"
        "scale=w=1920:h=1080:force_original_aspect_ratio=decrease,"
        "pad=w=1920:h=1080:x=(ow-iw)/2:y=(oh-ih)/2:color=black,"
        "setsar=1,format=yuv420p[v1]"
    ) in command.filter_complex
    assert "[v0][v1]concat=n=2:v=1:a=0[outv]" in command.filter_complex


def test_audio_tracks_are_concatenated_mixed_and_mapped() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment("A", "/media/a.mov", start_seconds=1, duration_seconds=2),
        ),
        audio_tracks=(
            AudioTimelineTrack(
                "V1 embedded audio",
                (
                    AudioClipSegment(
                        "A",
                        "/media/a.mov",
                        start_seconds=1,
                        duration_seconds=2,
                    ),
                ),
            ),
            AudioTimelineTrack(
                "A1",
                (
                    AudioClipSegment(
                        "Music",
                        "/media/music.wav",
                        start_seconds=0.5,
                        duration_seconds=1,
                    ),
                    AudioGapSegment("Silence", duration_seconds=1),
                ),
            ),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert "/media/music.wav" in command.args
    assert "anullsrc=channel_layout=stereo:sample_rate=48000:d=1" in command.args
    assert command.args.count("-i") == 3
    assert command.args.count("/media/a.mov") == 1
    assert (
        "[0:a]atrim=start=1:duration=2,asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo[a0_0]"
    ) in command.filter_complex
    assert (
        "[1:a]atrim=start=0.5:duration=1,asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo[a1_0]"
    ) in command.filter_complex
    assert "[2:a]atrim=duration=1,asetpts=PTS-STARTPTS" in command.filter_complex
    assert "[a0_0]acopy[at0]" in command.filter_complex
    assert "[a1_0][a1_1]concat=n=2:v=0:a=1[at1]" in command.filter_complex
    assert "[at0][at1]amix=inputs=2:duration=longest:normalize=0[outa]" in command.filter_complex
    assert "-map" in command.args
    assert "[outa]" in command.args
    assert "-c:a" in command.args
    assert "aac" in command.args


def test_dissolve_transition_uses_xfade_and_acrossfade() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),
            DissolveTransitionSegment(
                name="Dissolve",
                duration_seconds=0.5,
                outgoing=ClipSegment(
                    "A tail",
                    "/media/a.mov",
                    start_seconds=0.5,
                    duration_seconds=0.5,
                ),
                incoming=ClipSegment(
                    "B head",
                    "/media/b.mov",
                    start_seconds=0,
                    duration_seconds=0.5,
                ),
            ),
            ClipSegment("B", "/media/b.mov", start_seconds=0.5, duration_seconds=1),
        ),
        audio_tracks=(
            AudioTimelineTrack(
                "A1",
                (
                    DissolveAudioTransitionSegment(
                        name="Crossfade",
                        duration_seconds=0.5,
                        outgoing=AudioClipSegment(
                            "A tail",
                            "/media/a.mov",
                            start_seconds=0.5,
                            duration_seconds=0.5,
                        ),
                        incoming=AudioClipSegment(
                            "B head",
                            "/media/b.mov",
                            start_seconds=0,
                            duration_seconds=0.5,
                        ),
                    ),
                ),
            ),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert "xfade=transition=fade:duration=0.5:offset=0" in command.filter_complex
    assert "acrossfade=d=0.5:c1=tri:c2=tri" in command.filter_complex
    assert "[v0][blend1][v4]concat=n=3:v=1:a=0[outv]" in command.filter_complex


def test_multiple_video_tracks_require_output_shape() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="Video 1, Video 2",
        segments=(ClipSegment("Base", "/media/base.mov", start_seconds=0, duration_seconds=2),),
        video_tracks=(
            VideoTimelineTrack(
                "Video 1",
                (ClipSegment("Base", "/media/base.mov", start_seconds=0, duration_seconds=2),),
            ),
            VideoTimelineTrack(
                "Video 2",
                (GapSegment("gap", duration_seconds=1),),
            ),
        ),
    )

    with pytest.raises(UnsupportedTimelineError, match="Multiple video tracks require"):
        build_ffmpeg_command(timeline, "output.mp4")


def test_multiple_video_tracks_overlay_with_transparent_upper_gap() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="Video 1, Video 2",
        segments=(ClipSegment("Base", "/media/base.mov", start_seconds=0, duration_seconds=2),),
        video_tracks=(
            VideoTimelineTrack(
                "Video 1",
                (ClipSegment("Base", "/media/base.mov", start_seconds=0, duration_seconds=2),),
            ),
            VideoTimelineTrack(
                "Video 2",
                (
                    GapSegment("gap", duration_seconds=1),
                    ClipSegment("Overlay", "/media/overlay.png", start_seconds=0, duration_seconds=1),
                ),
            ),
        ),
    )
    settings = RenderSettings(width=1920, height=1080, fps=24)

    command = build_ffmpeg_command(timeline, "output.mp4", settings=settings)

    assert "color=c=black@0.0:s=1920x1080:r=24:d=1,format=yuva420p" in command.args
    assert (
        "[2:v]trim=start=0:duration=1,setpts=PTS-STARTPTS,"
        "scale=w=1920:h=1080:force_original_aspect_ratio=decrease,"
        "pad=w=1920:h=1080:x=(ow-iw)/2:y=(oh-ih)/2:color=black@0.0,"
        "setsar=1,format=yuva420p[v2]"
    ) in command.filter_complex
    assert "[v0]copy[vt0]" in command.filter_complex
    assert "[v1][v2]concat=n=2:v=1:a=0[vt1]" in command.filter_complex
    assert "[vt0][vt1]overlay=eof_action=pass:format=auto[outv]" in command.filter_complex


def test_keyframed_transform_uses_ffmpeg_expressions() -> None:
    animation = ClipAnimation(
        scale_x=AnimatedScalar(
            (
                KeyframePoint(0.0, 1.0),
                KeyframePoint(1.0, 2.0),
            )
        ),
    )
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                animation=animation,
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert "scale=w='iw*(" in command.filter_complex
    assert "if(lte(t\\,0)" in command.filter_complex


def test_custom_dissolve_curve_uses_blend_expression() -> None:
    curve = DissolveCurve(
        (
            KeyframePoint(0.0, 0.0),
            KeyframePoint(0.5, 0.2),
            KeyframePoint(1.0, 1.0),
        )
    )
    outgoing = ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=2)
    incoming = ClipSegment("B", "/media/b.mov", start_seconds=0, duration_seconds=2)
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            DissolveTransitionSegment(
                "dissolve",
                duration_seconds=1,
                outgoing=outgoing,
                incoming=incoming,
                dissolve_curve=curve,
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert "blend=all_expr=" in command.filter_complex
    assert "clip((t-" in command.filter_complex


def test_deduplicates_shared_media_inputs() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),
            DissolveTransitionSegment(
                name="Dissolve",
                duration_seconds=0.5,
                outgoing=ClipSegment(
                    "A tail",
                    "/media/a.mov",
                    start_seconds=0.5,
                    duration_seconds=0.5,
                ),
                incoming=ClipSegment(
                    "B head",
                    "/media/b.mov",
                    start_seconds=0,
                    duration_seconds=0.5,
                ),
            ),
            ClipSegment("B", "/media/b.mov", start_seconds=0.5, duration_seconds=1),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert command.args.count("-i") == 2
    assert "/media/a.mov" in command.args
    assert "/media/b.mov" in command.args
    assert "[0:v]trim=start=0:duration=1" in command.filter_complex
    assert "[0:v]trim=start=0.5:duration=0.5" in command.filter_complex
    assert "[1:v]trim=start=0:duration=0.5" in command.filter_complex
    assert "[1:v]trim=start=0.5:duration=1" in command.filter_complex


def test_deduplicates_shared_media_across_video_tracks() -> None:
    shared_clip = ClipSegment("Shared", "/media/shared.mov", start_seconds=0, duration_seconds=2)
    overlay = ClipSegment("Overlay", "/media/overlay.png", start_seconds=0, duration_seconds=1)
    settings = RenderSettings(width=1920, height=1080, fps=24)
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="Video 1, Video 2",
        segments=(shared_clip,),
        video_tracks=(
            VideoTimelineTrack("Video 1", (shared_clip,)),
            VideoTimelineTrack("Video 2", (overlay,)),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4", settings=settings)

    media_inputs = [command.args[index + 1] for index, arg in enumerate(command.args) if arg == "-i"]
    assert media_inputs.count("/media/shared.mov") == 1
    assert media_inputs.count("/media/overlay.png") == 1


def test_constant_opacity_uses_colorchannelmixer() -> None:
    animation = ClipAnimation(
        opacity=AnimatedScalar((KeyframePoint(0.0, 0.5),)),
    )
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                animation=animation,
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert "colorchannelmixer=aa=0.5" in command.filter_complex
    assert "geq=" not in command.filter_complex


def test_keyframed_opacity_still_uses_geq() -> None:
    animation = ClipAnimation(
        opacity=AnimatedScalar(
            (
                KeyframePoint(0.0, 1.0),
                KeyframePoint(1.0, 0.5),
            )
        ),
    )
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                animation=animation,
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    assert "geq=" in command.filter_complex
    assert "colorchannelmixer" not in command.filter_complex


def test_animation_with_static_crop_applies_crop_once() -> None:
    animation = ClipAnimation(
        crop_left=AnimatedScalar((KeyframePoint(0.0, 0.19),)),
        opacity=AnimatedScalar((KeyframePoint(0.0, 0.706422),)),
    )
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(
            ClipSegment(
                "A",
                "/media/a.mov",
                start_seconds=0,
                duration_seconds=1,
                animation=animation,
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(width=1920, height=1080, fps=24),
    )

    crop_blocks = command.filter_complex.count("1920*(1-")
    assert crop_blocks == 1


def test_linked_av_export_deduplicates_audio_inputs() -> None:
    clip = ClipSegment("A", "/media/a.mov", start_seconds=1, duration_seconds=2)
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(clip,),
        audio_tracks=(
            AudioTimelineTrack(
                "A1",
                (
                    AudioClipSegment(
                        "A",
                        "/media/a.mov",
                        start_seconds=1,
                        duration_seconds=2,
                    ),
                ),
            ),
        ),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    assert command.args.count("-i") == 1
    assert (
        "[0:a]atrim=start=1:duration=2,asetpts=PTS-STARTPTS,"
        "aresample=48000,aformat=channel_layouts=stereo[a0_0]"
    ) in command.filter_complex


def test_default_encoder_preset_is_faster() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),),
    )

    command = build_ffmpeg_command(timeline, "output.mp4")

    preset_index = command.args.index("-preset")
    assert command.args[preset_index + 1] == "faster"
    assert "-c:v" in command.args
    assert command.args[command.args.index("-c:v") + 1] == "libx264"
    assert "-b:v" in command.args
    assert command.args[command.args.index("-b:v") + 1] == "8000000"
    assert "-movflags" in command.args
    assert command.args[command.args.index("-movflags") + 1] == "+faststart"


def _single_clip_timeline() -> TimelinePlan:
    return TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),),
    )


def test_build_ffmpeg_command_uses_hevc_settings() -> None:
    command = build_ffmpeg_command(
        _single_clip_timeline(),
        "output.mp4",
        settings=RenderSettings(
            video_codec="hevc",
            video_bitrate=12_000_000,
            video_encoder_preset="medium",
            video_faststart=False,
        ),
    )

    assert command.args[command.args.index("-c:v") + 1] == "libx265"
    assert command.args[command.args.index("-b:v") + 1] == "12000000"
    assert command.args[command.args.index("-preset") + 1] == "medium"
    assert "-movflags" not in command.args


def test_build_ffmpeg_command_uses_prores_settings() -> None:
    command = build_ffmpeg_command(
        _single_clip_timeline(),
        "output.mov",
        settings=RenderSettings(video_codec="prores"),
    )

    assert command.args[command.args.index("-c:v") + 1] == "prores_ks"
    assert command.args[command.args.index("-profile:v") + 1] == "3"
    assert command.args[command.args.index("-pix_fmt") + 1] == "yuv422p10le"
    assert "-preset" not in command.args
    assert "-b:v" not in command.args


def test_build_ffmpeg_command_uses_audio_encoding_settings() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),),
        audio_tracks=(
            AudioTimelineTrack(
                "A1",
                (
                    AudioClipSegment(
                        "A",
                        "/media/a.mov",
                        start_seconds=0,
                        duration_seconds=1,
                    ),
                ),
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mp4",
        settings=RenderSettings(audio_codec="aac", audio_bitrate=256_000),
    )

    assert command.args[command.args.index("-c:a") + 1] == "aac"
    assert command.args[command.args.index("-b:a") + 1] == "256000"


def test_build_ffmpeg_command_pcm_audio_skips_bitrate() -> None:
    timeline = TimelinePlan(
        name="Timeline",
        source_path=Path("input.otio"),
        track_name="V1",
        segments=(ClipSegment("A", "/media/a.mov", start_seconds=0, duration_seconds=1),),
        audio_tracks=(
            AudioTimelineTrack(
                "A1",
                (
                    AudioClipSegment(
                        "A",
                        "/media/a.mov",
                        start_seconds=0,
                        duration_seconds=1,
                    ),
                ),
            ),
        ),
    )

    command = build_ffmpeg_command(
        timeline,
        "output.mov",
        settings=RenderSettings(audio_codec="pcm"),
    )

    assert command.args[command.args.index("-c:a") + 1] == "pcm_s16le"
    assert "-b:a" not in command.args
