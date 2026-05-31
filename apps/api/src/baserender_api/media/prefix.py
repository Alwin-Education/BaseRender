from __future__ import annotations

import posixpath


def normalize_s3_prefix(value: str | None) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if raw in {"", "."}:
        return ""
    if raw.startswith("/"):
        raise ValueError("S3 prefix must be relative.")

    had_trailing_slash = raw.endswith("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("S3 prefix cannot contain '..'.")

    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", "."}:
        return ""
    return f"{normalized}/" if had_trailing_slash else normalized


def enforce_allowed_prefix(requested_prefix: str | None, allowed_root: str | None) -> str:
    root = normalize_s3_prefix(allowed_root)
    requested = normalize_s3_prefix(requested_prefix)
    if not root:
        return requested

    root_with_slash = _ensure_trailing_slash(root)
    if requested == root.rstrip("/") or requested.startswith(root_with_slash):
        return requested

    if not requested:
        return root_with_slash

    return f"{root_with_slash}{requested}"


def validate_media_object_key(object_key: str, allowed_root: str | None) -> str:
    """Return a normalized object key or raise if it is outside the allowed root."""
    normalized = object_key.strip().strip("/")
    if not normalized:
        raise ValueError("Object key must not be empty.")

    root = normalize_s3_prefix(allowed_root)
    if not root:
        return normalized

    root_with_slash = _ensure_trailing_slash(root)
    if normalized == root.rstrip("/") or normalized.startswith(root_with_slash):
        return normalized

    raise ValueError(f"Object key must be under allowed prefix {root!r}.")


def _ensure_trailing_slash(prefix: str) -> str:
    return prefix if prefix.endswith("/") else f"{prefix}/"
