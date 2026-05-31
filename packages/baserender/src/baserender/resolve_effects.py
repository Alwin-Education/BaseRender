from __future__ import annotations

import math

import opentimelineio as otio

from baserender.animation import (
    AnimatedScalar,
    ClipAnimation,
    DissolveCurve,
    KeyframePoint,
    scalar_from_constant,
)
from baserender.timeline_model import ClipCrop, ClipTransform


_RESOLVE_TRANSFORM_PARAMETER_IDS = {
    "transformationZoomX",
    "transformationZoomY",
    "transformationPan",
    "transformationTilt",
    "transformationRotationAngle",
}

_RESOLVE_CROP_PARAMETER_IDS = {
    "cropLeft",
    "cropRight",
    "cropTop",
    "cropBottom",
}

_RESOLVE_COMPOSITE_PARAMETER_IDS = {
    "opacity",
}

_RESOLVE_DYNAMIC_ZOOM_PARAMETER_IDS = {
    "dynamicZoomCenter",
    "dynamicZoomScale",
}

_RESOLVE_DISSOLVE_CURVE_PARAMETER_IDS = {
    "transitionCustomCurvesKeyframes",
}


def unsupported_clip_effects(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> list[otio.schema.Effect]:
    """Return clip effects that are not empty/disabled/supported Resolve nodes."""
    unsupported: list[otio.schema.Effect] = []
    for effect in effects:
        if not isinstance(effect, otio.schema.Effect):
            unsupported.append(
                otio.schema.Effect(
                    name=getattr(effect, "name", "") or "",
                    effect_name=type(effect).__name__,
                )
            )
            continue
        if not is_noop_resolve_effect(effect) and not is_supported_resolve_clip_effect(effect):
            unsupported.append(effect)
    return unsupported


def is_supported_resolve_clip_effect(effect: otio.schema.Effect) -> bool:
    """True when BaseRender can render this Resolve clip effect (static or keyframed)."""
    resolve = _resolve_metadata(effect)
    if resolve is None:
        return False
    if resolve.get("Enabled") is False:
        return True

    effect_name = _resolve_effect_name(resolve)
    if effect_name == "Transform":
        return _effect_has_only_parameters(resolve, _RESOLVE_TRANSFORM_PARAMETER_IDS)
    if effect_name == "Cropping":
        return _effect_has_only_parameters(resolve, _RESOLVE_CROP_PARAMETER_IDS)
    if effect_name == "Composite":
        return _effect_has_only_parameters(resolve, _RESOLVE_COMPOSITE_PARAMETER_IDS)
    if effect_name == "Dynamic Zoom":
        return _effect_has_only_parameters(resolve, _RESOLVE_DYNAMIC_ZOOM_PARAMETER_IDS)

    return False


def parse_resolve_clip_animation(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
    *,
    output_width: int,
    output_height: int,
    duration_seconds: float,
    fps: float | None,
) -> tuple[ClipAnimation | None, list[str]]:
    """Parse supported Resolve clip effects into a combined animation."""
    warnings: list[str] = []
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

    for effect in effects:
        if not isinstance(effect, otio.schema.Effect):
            continue
        resolve = _resolve_metadata(effect)
        if resolve is None or resolve.get("Enabled") is False:
            continue

        effect_name = _resolve_effect_name(resolve)
        if effect_name == "Transform":
            mapped, effect_warnings = _parse_transform_animation(
                resolve,
                output_width=output_width,
                output_height=output_height,
                duration_seconds=duration_seconds,
                fps=fps,
            )
            warnings.extend(effect_warnings)
            scale_x = _combine_scalar(scale_x, mapped.scale_x)
            scale_y = _combine_scalar(scale_y, mapped.scale_y)
            translate_x = _combine_scalar(translate_x, mapped.translate_x)
            translate_y = _combine_scalar(translate_y, mapped.translate_y)
            rotation_degrees = _combine_scalar(rotation_degrees, mapped.rotation_degrees)
        elif effect_name == "Cropping":
            mapped, effect_warnings = _parse_crop_animation(
                resolve,
                duration_seconds=duration_seconds,
                fps=fps,
            )
            warnings.extend(effect_warnings)
            crop_left = _combine_scalar(crop_left, mapped.crop_left)
            crop_right = _combine_scalar(crop_right, mapped.crop_right)
            crop_top = _combine_scalar(crop_top, mapped.crop_top)
            crop_bottom = _combine_scalar(crop_bottom, mapped.crop_bottom)
        elif effect_name == "Composite":
            mapped, effect_warnings = _parse_composite_animation(
                resolve,
                duration_seconds=duration_seconds,
                fps=fps,
            )
            warnings.extend(effect_warnings)
            opacity = _combine_scalar(opacity, mapped.opacity)
        elif effect_name == "Dynamic Zoom":
            mapped, effect_warnings = _parse_dynamic_zoom_animation(
                resolve,
                duration_seconds=duration_seconds,
                fps=fps,
            )
            warnings.extend(effect_warnings)
            scale_x = _combine_scalar(scale_x, mapped.scale_x)
            scale_y = _combine_scalar(scale_y, mapped.scale_y)
            translate_x = _combine_scalar(translate_x, mapped.translate_x)
            translate_y = _combine_scalar(translate_y, mapped.translate_y)

    animation = ClipAnimation(
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=translate_x,
        translate_y=translate_y,
        rotation_degrees=rotation_degrees,
        crop_left=crop_left,
        crop_right=crop_right,
        crop_top=crop_top,
        crop_bottom=crop_bottom,
        opacity=opacity,
    )
    if animation.is_identity:
        return None, warnings
    return animation, warnings


def parse_static_resolve_crop(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> ClipCrop | None:
    """Parse Resolve's static Cropping effect into normalized canvas-edge insets."""
    for effect in effects:
        if not isinstance(effect, otio.schema.Effect) or not is_static_resolve_crop(effect):
            continue

        values = _resolve_parameter_values(effect)
        crop = ClipCrop(
            left=values.get("cropLeft", 0.0),
            right=values.get("cropRight", 0.0),
            top=values.get("cropTop", 0.0),
            bottom=values.get("cropBottom", 0.0),
        )
        if not crop.is_identity:
            return crop

    return None


def has_static_resolve_crop(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    """True when a clip has a non-default static Resolve Cropping effect."""
    return any(
        isinstance(effect, otio.schema.Effect)
        and not is_noop_resolve_effect(effect)
        and is_static_resolve_crop(effect)
        for effect in effects
    )


def is_static_resolve_crop(effect: otio.schema.Effect) -> bool:
    """True for enabled, static Resolve Cropping parameters BaseRender can render."""
    resolve = _resolve_metadata(effect)
    if resolve is None or resolve.get("Enabled") is False:
        return False

    if _resolve_effect_name(resolve) != "Cropping":
        return False

    return _effect_parameters_are_static(resolve, _RESOLVE_CROP_PARAMETER_IDS)


def parse_static_resolve_transform(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
    *,
    output_width: int,
    output_height: int,
) -> ClipTransform | None:
    """Parse Resolve's static Transform effect into BaseRender canvas coordinates."""
    for effect in effects:
        if not isinstance(effect, otio.schema.Effect) or not is_static_resolve_transform(effect):
            continue

        values = _resolve_parameter_values(effect)
        transform = ClipTransform(
            scale_x=values.get("transformationZoomX", 1.0),
            scale_y=values.get("transformationZoomY", 1.0),
            translate_x=values.get("transformationPan", 0.0) * output_width,
            translate_y=-values.get("transformationTilt", 0.0) * output_height,
            rotation_degrees=-values.get("transformationRotationAngle", 0.0),
        )
        if not transform.is_identity:
            return transform

    return None


def has_static_resolve_transform(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    """True when a clip has a non-default static Resolve Transform effect."""
    return any(
        isinstance(effect, otio.schema.Effect)
        and not is_noop_resolve_effect(effect)
        and is_static_resolve_transform(effect)
        for effect in effects
    )


def has_keyframed_resolve_crop(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    return any(
        isinstance(effect, otio.schema.Effect)
        and not is_noop_resolve_effect(effect)
        and _resolve_effect_name(_resolve_metadata(effect) or {}) == "Cropping"
        and _effect_has_keyframed_parameters(
            _resolve_metadata(effect) or {},
            _RESOLVE_CROP_PARAMETER_IDS,
        )
        for effect in effects
    )


def has_keyframed_resolve_transform(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    return any(
        isinstance(effect, otio.schema.Effect)
        and not is_noop_resolve_effect(effect)
        and _resolve_effect_name(_resolve_metadata(effect) or {}) == "Transform"
        and _effect_has_keyframed_parameters(
            _resolve_metadata(effect) or {},
            _RESOLVE_TRANSFORM_PARAMETER_IDS,
        )
        for effect in effects
    )


def has_keyframed_resolve_effects(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    """True when any supported Resolve clip effect uses keyframes."""
    for effect in effects:
        if not isinstance(effect, otio.schema.Effect) or is_noop_resolve_effect(effect):
            continue
        resolve = _resolve_metadata(effect)
        if resolve is None or resolve.get("Enabled") is False:
            continue
        effect_name = _resolve_effect_name(resolve)
        if effect_name == "Transform" and _effect_has_keyframed_parameters(
            resolve, _RESOLVE_TRANSFORM_PARAMETER_IDS
        ):
            return True
        if effect_name == "Cropping" and _effect_has_keyframed_parameters(
            resolve, _RESOLVE_CROP_PARAMETER_IDS
        ):
            return True
        if effect_name == "Composite" and _effect_has_keyframed_parameters(
            resolve, _RESOLVE_COMPOSITE_PARAMETER_IDS
        ):
            return True
        if effect_name == "Dynamic Zoom" and _effect_has_keyframed_parameters(
            resolve, _RESOLVE_DYNAMIC_ZOOM_PARAMETER_IDS
        ):
            return True
    return False


def is_static_resolve_transform(effect: otio.schema.Effect) -> bool:
    """True for enabled, static Resolve Transform parameters BaseRender can render."""
    resolve = _resolve_metadata(effect)
    if resolve is None or resolve.get("Enabled") is False:
        return False

    if _resolve_effect_name(resolve) != "Transform":
        return False

    return _effect_parameters_are_static(resolve, _RESOLVE_TRANSFORM_PARAMETER_IDS)


def parse_resolve_dissolve_curve(
    transition: otio.schema.Transition,
    *,
    duration_seconds: float,
    fps: float | None,
) -> DissolveCurve | None:
    """Parse Resolve custom dissolve curve metadata into normalized progress samples."""
    resolve = _mapping(transition.metadata)
    if resolve is None:
        return None
    resolve = _mapping(resolve.get("Resolve_OTIO"))
    if resolve is None:
        return None

    effects = _mapping(resolve.get("Effects"))
    if effects is None:
        return None

    for parameter in effects.get("Parameters") or []:
        mapping = _mapping(parameter)
        if mapping is None:
            continue
        if mapping.get("Parameter ID") != "transitionCustomCurvesKeyframes":
            continue
        keyframes = mapping.get("Key Frames")
        if not keyframes:
            return None

        points = _parse_scalar_keyframes(
            keyframes,
            duration_seconds=duration_seconds,
            fps=fps,
            normalize_progress=True,
        )
        if not points:
            return None
        return DissolveCurve(points)

    return None


def effect_display_name(effect: otio.schema.Effect) -> str:
    resolve = _resolve_metadata(effect)
    if resolve is not None:
        name = resolve.get("Effect Name") or resolve.get("Name")
        if name:
            return str(name)
    return effect.effect_name or effect.name or "effect"


def is_noop_resolve_effect(effect: otio.schema.Effect) -> bool:
    """True for disabled or default Resolve OTIO pipeline nodes."""
    resolve = _resolve_metadata(effect)
    if resolve is None:
        return False

    if resolve.get("Enabled") is False:
        return True

    parameters = resolve.get("Parameters") or []
    if not parameters:
        return True

    return all(_resolve_parameter_is_default(parameter) for parameter in parameters)


def _parse_transform_animation(
    resolve: object,
    *,
    output_width: int,
    output_height: int,
    duration_seconds: float,
    fps: float | None,
) -> tuple[ClipAnimation, list[str]]:
    warnings: list[str] = []
    scale_x = _parameter_animation(
        resolve,
        "transformationZoomX",
        duration_seconds=duration_seconds,
        fps=fps,
        default=1.0,
    )
    scale_y = _parameter_animation(
        resolve,
        "transformationZoomY",
        duration_seconds=duration_seconds,
        fps=fps,
        default=1.0,
    )
    pan = _parameter_animation(
        resolve,
        "transformationPan",
        duration_seconds=duration_seconds,
        fps=fps,
        default=0.0,
        scale=output_width,
    )
    tilt = _parameter_animation(
        resolve,
        "transformationTilt",
        duration_seconds=duration_seconds,
        fps=fps,
        default=0.0,
        scale=-output_height,
    )
    rotation = _parameter_animation(
        resolve,
        "transformationRotationAngle",
        duration_seconds=duration_seconds,
        fps=fps,
        default=0.0,
        scale=-1.0,
    )
    return (
        ClipAnimation(
            scale_x=scale_x,
            scale_y=scale_y,
            translate_x=pan,
            translate_y=tilt,
            rotation_degrees=rotation,
        ),
        warnings,
    )


def _parse_crop_animation(
    resolve: object,
    *,
    duration_seconds: float,
    fps: float | None,
) -> tuple[ClipAnimation, list[str]]:
    return (
        ClipAnimation(
            crop_left=_parameter_animation(
                resolve,
                "cropLeft",
                duration_seconds=duration_seconds,
                fps=fps,
                default=0.0,
            ),
            crop_right=_parameter_animation(
                resolve,
                "cropRight",
                duration_seconds=duration_seconds,
                fps=fps,
                default=0.0,
            ),
            crop_top=_parameter_animation(
                resolve,
                "cropTop",
                duration_seconds=duration_seconds,
                fps=fps,
                default=0.0,
            ),
            crop_bottom=_parameter_animation(
                resolve,
                "cropBottom",
                duration_seconds=duration_seconds,
                fps=fps,
                default=0.0,
            ),
        ),
        [],
    )


def _parse_composite_animation(
    resolve: object,
    *,
    duration_seconds: float,
    fps: float | None,
) -> tuple[ClipAnimation, list[str]]:
    opacity = _parameter_animation(
        resolve,
        "opacity",
        duration_seconds=duration_seconds,
        fps=fps,
        default=100.0,
        scale=1.0 / 100.0,
    )
    return ClipAnimation(opacity=opacity), []


def _parse_dynamic_zoom_animation(
    resolve: object,
    *,
    duration_seconds: float,
    fps: float | None,
) -> tuple[ClipAnimation, list[str]]:
    warnings: list[str] = []
    scale = _parameter_animation(
        resolve,
        "dynamicZoomScale",
        duration_seconds=duration_seconds,
        fps=fps,
        default=1.0,
    )
    center_mapping = _parameter_mapping(resolve, "dynamicZoomCenter")
    translate_x = None
    translate_y = None
    if center_mapping is not None and _has_keyframes(center_mapping):
        warnings.append(
            "Dynamic Zoom center keyframes are approximated via uniform scale only."
        )
    return (
        ClipAnimation(
            scale_x=scale,
            scale_y=scale,
            translate_x=translate_x,
            translate_y=translate_y,
        ),
        warnings,
    )


def _parameter_animation(
    resolve: object,
    parameter_id: str,
    *,
    duration_seconds: float,
    fps: float | None,
    default: float,
    scale: float = 1.0,
) -> AnimatedScalar | None:
    mapping = _parameter_mapping(resolve, parameter_id)
    if mapping is None:
        return None

    if _has_keyframes(mapping):
        points = _parse_scalar_keyframes(
            mapping.get("Key Frames"),
            duration_seconds=duration_seconds,
            fps=fps,
        )
        if not points:
            return None
        if scale != 1.0:
            points = tuple(
                KeyframePoint(point.time_seconds, point.value * scale)
                for point in points
            )
        if len(points) == 1 and math.isclose(points[0].value, default * scale):
            return None
        return AnimatedScalar(points)

    value = _number(mapping.get("Parameter Value"))
    if value is None:
        return None
    scaled = value * scale
    if math.isclose(scaled, default * scale):
        return None
    return scalar_from_constant(scaled)


def _parse_scalar_keyframes(
    keyframes: object,
    *,
    duration_seconds: float,
    fps: float | None,
    normalize_progress: bool = False,
) -> tuple[KeyframePoint, ...]:
    mapping = _mapping(keyframes)
    if mapping is None:
        return ()

    entries: list[tuple[float, float]] = []
    for frame_key, data in mapping.items():
        value = _keyframe_scalar_value(data)
        if value is None:
            continue
        entries.append((float(frame_key), value))

    if not entries:
        return ()

    entries.sort(key=lambda item: item[0])
    frame_keys = [item[0] for item in entries]
    min_key = frame_keys[0]
    max_key = frame_keys[-1]
    span = max_key - min_key

    points: list[KeyframePoint] = []
    for frame_key, value in entries:
        if normalize_progress:
            if span > 0:
                progress = (frame_key - min_key) / span
            else:
                progress = 0.0
            points.append(
                KeyframePoint(time_seconds=max(0.0, min(1.0, progress)), value=value)
            )
            continue

        if span > 0:
            time_seconds = ((frame_key - min_key) / span) * duration_seconds
        elif fps is not None and fps > 0:
            time_seconds = frame_key / fps
        else:
            time_seconds = 0.0
        points.append(
            KeyframePoint(
                time_seconds=max(0.0, min(time_seconds, duration_seconds)),
                value=value,
            )
        )

    return tuple(points)


def _combine_scalar(
    existing: AnimatedScalar | None,
    new_value: AnimatedScalar | None,
) -> AnimatedScalar | None:
    if new_value is None:
        return existing
    if existing is None:
        return new_value
    if existing.is_constant and new_value.is_constant:
        return scalar_from_constant(existing.constant_value * new_value.constant_value)
    return new_value


def _effect_has_only_parameters(resolve: object, allowed_ids: set[str]) -> bool:
    mapping = _mapping(resolve)
    if mapping is None:
        return False

    parameters = mapping.get("Parameters") or []
    if not parameters:
        return True

    for parameter in parameters:
        parameter_mapping = _mapping(parameter)
        if parameter_mapping is None:
            return False
        parameter_id = parameter_mapping.get("Parameter ID")
        if parameter_id not in allowed_ids:
            return False
        if _keyframe_scalar_value({"Value": parameter_mapping.get("Parameter Value")}) is None and not _has_keyframes(
            parameter_mapping
        ):
            if parameter_mapping.get("Parameter Value") is None:
                return False
    return True


def _effect_parameters_are_static(resolve: object, allowed_ids: set[str]) -> bool:
    mapping = _mapping(resolve)
    if mapping is None:
        return False

    if _resolve_effect_name(resolve) is None:
        return False

    parameters = mapping.get("Parameters") or []
    if not parameters:
        return False

    for parameter in parameters:
        parameter_mapping = _mapping(parameter)
        if parameter_mapping is None:
            return False
        parameter_id = parameter_mapping.get("Parameter ID")
        if parameter_id not in allowed_ids:
            return False
        if _has_keyframes(parameter_mapping) or _number(parameter_mapping.get("Parameter Value")) is None:
            return False

    return True


def _effect_has_keyframed_parameters(resolve: object, allowed_ids: set[str]) -> bool:
    mapping = _mapping(resolve)
    if mapping is None:
        return False

    for parameter in mapping.get("Parameters") or []:
        parameter_mapping = _mapping(parameter)
        if parameter_mapping is None:
            continue
        if parameter_mapping.get("Parameter ID") not in allowed_ids:
            continue
        if _has_keyframes(parameter_mapping):
            return True
    return False


def _parameter_mapping(resolve: object, parameter_id: str) -> object | None:
    mapping = _mapping(resolve)
    if mapping is None:
        return None

    for parameter in mapping.get("Parameters") or []:
        parameter_mapping = _mapping(parameter)
        if parameter_mapping is None:
            continue
        if parameter_mapping.get("Parameter ID") == parameter_id:
            return parameter_mapping
    return None


def _resolve_parameter_values(effect: otio.schema.Effect) -> dict[str, float]:
    resolve = _resolve_metadata(effect)
    if resolve is None:
        return {}

    values: dict[str, float] = {}
    for parameter in resolve.get("Parameters") or []:
        mapping = _mapping(parameter)
        if mapping is None:
            continue

        parameter_id = mapping.get("Parameter ID")
        value = _number(mapping.get("Parameter Value"))
        if isinstance(parameter_id, str) and value is not None:
            values[parameter_id] = value

    return values


def _resolve_effect_name(resolve: object) -> str | None:
    mapping = _mapping(resolve)
    if mapping is None:
        return None

    name = mapping.get("Effect Name") or mapping.get("Name")
    if name is None:
        return None
    return str(name)


def _mapping(value: object) -> object | None:
    if value is None or not hasattr(value, "get"):
        return None
    return value


def _resolve_metadata(effect: otio.schema.Effect) -> object | None:
    metadata = _mapping(effect.metadata)
    if metadata is None:
        return None
    return _mapping(metadata.get("Resolve_OTIO"))


def _has_keyframes(parameter: object) -> bool:
    mapping = _mapping(parameter)
    if mapping is None:
        return False

    keyframes = mapping.get("Key Frames")
    if keyframes is None:
        return False
    return bool(keyframes)


def _keyframe_scalar_value(data: object) -> float | None:
    mapping = _mapping(data)
    if mapping is None:
        return _number(data)
    return _number(mapping.get("Value"))


def _resolve_parameter_is_default(parameter: object) -> bool:
    mapping = _mapping(parameter)
    if mapping is None:
        return False

    if _has_keyframes(mapping):
        return False

    if mapping.get("Parameter Value") is None:
        return False

    default = mapping.get("Default Parameter Value")
    if default is None:
        return False

    return _values_equal(mapping.get("Parameter Value"), default)


def has_adjusted_resolve_composite_opacity(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    """True when an enabled Composite effect changes opacity from the default."""
    for effect in effects:
        if not isinstance(effect, otio.schema.Effect):
            continue
        resolve = _resolve_metadata(effect)
        if resolve is None or resolve.get("Enabled") is False:
            continue
        if _resolve_effect_name(resolve) != "Composite":
            continue

        mapping = _parameter_mapping(resolve, "opacity")
        if mapping is None:
            continue
        if _has_keyframes(mapping):
            return True

        value = _number(mapping.get("Parameter Value"))
        if value is None:
            continue
        if not math.isclose(value, 100.0):
            return True

    return False


def needs_resolve_canvas(
    effects: list[otio.core.SerializableObject] | tuple[otio.core.SerializableObject, ...],
) -> bool:
    """True when clip effects require --width and --height to render faithfully."""
    return (
        has_static_resolve_transform(effects)
        or has_keyframed_resolve_effects(effects)
        or has_adjusted_resolve_composite_opacity(effects)
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _values_equal(left: object, right: object) -> bool:
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_values_equal(item, other) for item, other in zip(left, right, strict=True))

    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)

    return left == right
