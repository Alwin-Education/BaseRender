from __future__ import annotations

import opentimelineio as otio

from baserender.report import TimelineIssue
from baserender.resolve_effects import parse_resolve_dissolve_curve


_RESOLVE_DISSOLVE_EFFECT_NAMES = frozenset(
    {
        "Cross Dissolve",
        "Cross Fade 0DB",
    }
)


def transition_duration_seconds(transition: otio.schema.Transition) -> float:
    """Return the overlap duration in seconds from OTIO transition offsets."""
    if transition.in_offset is None or transition.out_offset is None:
        raise ValueError(
            f"Transition {transition.name!r} is missing in_offset or out_offset."
        )
    total = transition.in_offset + transition.out_offset
    return float(total.value) / float(total.rate)


def is_supported_dissolve(transition: otio.schema.Transition) -> bool:
    """True when the transition can be rendered as a dissolve/crossfade."""
    transition_type = str(transition.transition_type)
    if transition_type == str(otio.schema.TransitionTypes.SMPTE_Dissolve):
        return True

    resolve_name = _resolve_transition_effect_name(transition)
    return resolve_name in _RESOLVE_DISSOLVE_EFFECT_NAMES


def dissolve_transition_issues(
    transition: otio.schema.Transition,
    *,
    duration_seconds: float,
    fps: float | None,
    dissolve_curve: object | None,
) -> list[TimelineIssue]:
    """Return non-fatal issues for a supported dissolve (e.g. unparsed curves)."""
    issues: list[TimelineIssue] = []
    if _has_resolve_transition_keyframes(transition) and dissolve_curve is None:
        issues.append(
            TimelineIssue(
                code="unsupported_transition_curve",
                severity="warning",
                message=(
                    f"Transition {transition.name!r} has custom dissolve curves that "
                    "could not be parsed; rendering a linear crossfade instead."
                ),
                item_name=transition.name or "transition",
                item_type="Transition",
            )
        )
    return issues


def parse_dissolve_curve(
    transition: otio.schema.Transition,
    *,
    duration_seconds: float,
    fps: float | None,
):
    """Parse Resolve dissolve curve metadata when available."""
    return parse_resolve_dissolve_curve(
        transition,
        duration_seconds=duration_seconds,
        fps=fps,
    )


def unsupported_transition_type_issue(
    transition: otio.schema.Transition,
) -> TimelineIssue:
    transition_type = str(transition.transition_type)
    resolve_name = _resolve_transition_effect_name(transition)
    detail = resolve_name or transition_type
    return TimelineIssue(
        code="unsupported_transition_type",
        severity="warning",
        message=(
            f"Transition {transition.name!r} ({detail}) is not a supported dissolve. "
            "Only SMPTE_Dissolve and Resolve cross-dissolve/crossfade are supported."
        ),
        item_name=transition.name or "transition",
        item_type="Transition",
    )


def _resolve_transition_effect_name(transition: otio.schema.Transition) -> str | None:
    resolve = _mapping(transition.metadata)
    if resolve is None:
        return None
    resolve = _mapping(resolve.get("Resolve_OTIO"))
    if resolve is None:
        return None

    effects = _mapping(resolve.get("Effects"))
    if effects is None:
        transition_type = resolve.get("Transition Type")
        if transition_type is not None:
            return str(transition_type)
        return None

    name = effects.get("Effect Name") or effects.get("Name")
    if name is None:
        return None
    return str(name)


def _has_resolve_transition_keyframes(transition: otio.schema.Transition) -> bool:
    resolve = _mapping(transition.metadata)
    if resolve is None:
        return False
    resolve = _mapping(resolve.get("Resolve_OTIO"))
    if resolve is None:
        return False

    effects = _mapping(resolve.get("Effects"))
    if effects is None:
        return False

    for parameter in effects.get("Parameters") or []:
        mapping = _mapping(parameter)
        if mapping is None:
            continue
        keyframes = mapping.get("Key Frames")
        if keyframes:
            return True
    return False


def _mapping(value: object) -> object | None:
    if value is None or not hasattr(value, "get"):
        return None
    return value
