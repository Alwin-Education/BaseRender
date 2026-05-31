from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class KeyframePoint:
    """Single keyframe sample in seconds relative to segment start."""

    time_seconds: float
    value: float


@dataclass(frozen=True)
class AnimatedScalar:
    """Piecewise-linear scalar animation over segment-local time."""

    points: tuple[KeyframePoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("AnimatedScalar requires at least one keyframe point.")
        object.__setattr__(
            self,
            "points",
            tuple(sorted(self.points, key=lambda point: point.time_seconds)),
        )

    @property
    def is_constant(self) -> bool:
        return all(
            math.isclose(point.value, self.points[0].value, rel_tol=0, abs_tol=1e-9)
            for point in self.points
        )

    @property
    def constant_value(self) -> float:
        return self.points[0].value

    def evaluate(self, time_seconds: float) -> float:
        if time_seconds <= self.points[0].time_seconds:
            return self.points[0].value
        if time_seconds >= self.points[-1].time_seconds:
            return self.points[-1].value

        for left, right in zip(self.points, self.points[1:], strict=False):
            if left.time_seconds <= time_seconds <= right.time_seconds:
                span = right.time_seconds - left.time_seconds
                if span <= 0:
                    return right.value
                ratio = (time_seconds - left.time_seconds) / span
                return left.value + (right.value - left.value) * ratio

        return self.points[-1].value

    def to_ffmpeg_expr(self, time_var: str = "t") -> str:
        """Return an FFmpeg expression for this scalar in terms of ``time_var``."""
        if self.is_constant:
            return _format_number(self.constant_value)

        def build_segment(index: int) -> str:
            if index >= len(self.points) - 1:
                return _format_number(self.points[-1].value)

            left = self.points[index]
            right = self.points[index + 1]
            right_time = _format_number(right.time_seconds)
            span = _format_number(right.time_seconds - left.time_seconds)
            left_value = _format_number(left.value)
            right_value = _format_number(right.value)
            if span == "0":
                interpolated = right_value
            else:
                ratio = f"(({time_var}-{_format_number(left.time_seconds)})/{span})"
                interpolated = f"({left_value}+({right_value}-{left_value})*{ratio})"
            tail = build_segment(index + 1)
            return f"if(lte({time_var}\\,{right_time})\\,{interpolated}\\,{tail})"

        first = self.points[0]
        first_time = _format_number(first.time_seconds)
        first_value = _format_number(first.value)
        if len(self.points) == 1:
            return first_value
        return f"if(lte({time_var}\\,{first_time})\\,{first_value}\\,{build_segment(0)})"


@dataclass(frozen=True)
class DissolveCurve:
    """Incoming-blend weight curve over normalized transition progress [0, 1]."""

    points: tuple[KeyframePoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("DissolveCurve requires at least one keyframe point.")
        object.__setattr__(
            self,
            "points",
            tuple(sorted(self.points, key=lambda point: point.time_seconds)),
        )

    @property
    def is_linear(self) -> bool:
        if len(self.points) < 2:
            return True
        return (
            math.isclose(self.points[0].time_seconds, 0.0, abs_tol=1e-9)
            and math.isclose(self.points[0].value, 0.0, abs_tol=1e-9)
            and math.isclose(self.points[-1].time_seconds, 1.0, abs_tol=1e-9)
            and math.isclose(self.points[-1].value, 1.0, abs_tol=1e-9)
            and len(self.points) == 2
        )

    def to_ffmpeg_progress_expr(self, progress_var: str) -> str:
        """Map normalized progress in [0, 1] to incoming blend weight."""
        normalized = AnimatedScalar(
            tuple(
                KeyframePoint(
                    time_seconds=max(0.0, min(1.0, point.time_seconds)),
                    value=max(0.0, min(1.0, point.value)),
                )
                for point in self.points
            )
        )
        return normalized.to_ffmpeg_expr(progress_var)


@dataclass(frozen=True)
class ClipAnimation:
    """Time-varying clip transform, crop, and opacity in render coordinates."""

    scale_x: AnimatedScalar | None = None
    scale_y: AnimatedScalar | None = None
    translate_x: AnimatedScalar | None = None
    translate_y: AnimatedScalar | None = None
    rotation_degrees: AnimatedScalar | None = None
    crop_left: AnimatedScalar | None = None
    crop_right: AnimatedScalar | None = None
    crop_top: AnimatedScalar | None = None
    crop_bottom: AnimatedScalar | None = None
    opacity: AnimatedScalar | None = None

    @property
    def has_transform(self) -> bool:
        return any(
            field is not None
            for field in (
                self.scale_x,
                self.scale_y,
                self.translate_x,
                self.translate_y,
                self.rotation_degrees,
            )
        )

    @property
    def has_crop(self) -> bool:
        return any(
            field is not None
            for field in (
                self.crop_left,
                self.crop_right,
                self.crop_top,
                self.crop_bottom,
            )
        )

    @property
    def has_opacity(self) -> bool:
        return self.opacity is not None and not (
            self.opacity.is_constant and math.isclose(self.opacity.constant_value, 1.0)
        )

    @property
    def is_identity(self) -> bool:
        return not self.has_transform and not self.has_crop and not self.has_opacity


def scalar_from_constant(value: float) -> AnimatedScalar:
    return AnimatedScalar((KeyframePoint(0.0, value),))


def merge_scalar(
    base: float,
    animated: AnimatedScalar | None,
) -> AnimatedScalar:
    if animated is None:
        return scalar_from_constant(base)
    if animated.is_constant:
        return scalar_from_constant(animated.constant_value)
    return animated


def _format_number(value: float) -> str:
    if value == 0:
        return "0"
    if math.isclose(value, round(value)):
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")
