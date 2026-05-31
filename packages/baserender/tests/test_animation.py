from __future__ import annotations

from baserender.animation import AnimatedScalar, DissolveCurve, KeyframePoint


def test_animated_scalar_to_ffmpeg_expr_interpolates() -> None:
    animated = AnimatedScalar(
        (
            KeyframePoint(0.0, 1.0),
            KeyframePoint(1.0, 2.0),
        )
    )

    expr = animated.to_ffmpeg_expr("t")
    assert "if(lte(t\\,0)" in expr
    assert "if(lte(t\\,1)" in expr


def test_dissolve_curve_progress_expr() -> None:
    curve = DissolveCurve(
        (
            KeyframePoint(0.0, 0.0),
            KeyframePoint(1.0, 1.0),
        )
    )

    assert curve.is_linear
    assert "if(lte(p\\,0)" in curve.to_ffmpeg_progress_expr("p")
