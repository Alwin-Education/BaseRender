from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LambdaShotEvent:
    """EventBridge-shaped payload for one Lambda-bound shot render."""

    job_id: str
    bucket: str
    shot_index: int
    media_url: str
    timeline_offset_seconds: float
    source_in_seconds: float
    source_out_seconds: float
    reasons: tuple[str, ...]
    proxy_key: str
    otio_key: str
    lut_keys: dict[str, str]
    output_key: str
    settings: dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> LambdaShotEvent:
        try:
            job_id = str(payload["job_id"])
            shot_index = int(payload["shot_index"])
            media_url = str(payload["media_url"])
            proxy_key = str(payload["proxy_key"])
            otio_key = str(payload["otio_key"])
            output_key = str(payload["output_key"])
        except KeyError as exc:
            raise ValueError(f"Missing required Lambda event field: {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError("Lambda event fields must be strings or integers.") from exc

        bucket = str(payload.get("bucket") or _required_env("BASERENDER_S3_BUCKET"))

        settings_payload = payload.get("settings") or {}
        if not isinstance(settings_payload, Mapping):
            raise ValueError("Lambda event field 'settings' must be an object.")

        lut_keys_payload = payload.get("lut_keys") or {}
        if not isinstance(lut_keys_payload, Mapping):
            raise ValueError("Lambda event field 'lut_keys' must be an object.")

        reasons_payload = payload.get("reasons") or ()
        if isinstance(reasons_payload, str):
            reasons = (reasons_payload,)
        elif isinstance(reasons_payload, Mapping):
            raise ValueError("Lambda event field 'reasons' must be a list of strings.")
        else:
            reasons = tuple(str(reason) for reason in reasons_payload)

        return cls(
            job_id=job_id,
            bucket=bucket,
            shot_index=shot_index,
            media_url=media_url,
            timeline_offset_seconds=_required_float(payload, "timeline_offset_seconds"),
            source_in_seconds=_required_float(payload, "source_in_seconds"),
            source_out_seconds=_required_float(payload, "source_out_seconds"),
            reasons=reasons,
            proxy_key=proxy_key,
            otio_key=otio_key,
            lut_keys={str(key): str(value) for key, value in lut_keys_payload.items()},
            output_key=output_key,
            settings=dict(settings_payload),
        )


def _required_env(name: str) -> str:
    import os

    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required Lambda event field 'bucket' and env var {name}.")
    return value


def _required_float(payload: Mapping[str, Any], field: str) -> float:
    if field not in payload:
        raise ValueError(f"Missing required Lambda event field: {field}")
    try:
        return float(payload[field])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Lambda event field '{field}' must be numeric.") from exc
