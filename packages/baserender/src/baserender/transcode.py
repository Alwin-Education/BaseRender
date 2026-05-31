"""Helpers for direct file transcoding (no OTIO)."""

from __future__ import annotations

from pathlib import PurePosixPath


def _normalize_folder(folder: str | None) -> str | None:
    if folder is None:
        return None
    normalized = folder.strip().strip("/")
    return normalized or None


def build_transcode_output_key(
    source_key: str,
    *,
    container: str,
    prepend_folder: str | None = None,
    append_folder: str | None = None,
) -> str:
    """Build the S3 output key for a transcode job.

    Preserves directory structure under the source key. Optional prepend/append
    folders are inserted before the full source path or before the filename,
    respectively.
    """
    normalized_key = source_key.strip().strip("/")
    if not normalized_key:
        raise ValueError("source_key must not be empty.")

    path = PurePosixPath(normalized_key)
    directory = path.parent.as_posix() if path.parent != PurePosixPath(".") else ""
    stem = path.stem
    suffix = container.strip().lower().lstrip(".")
    if not suffix:
        raise ValueError("container must not be empty.")

    parts: list[str] = []
    prepend = _normalize_folder(prepend_folder)
    append = _normalize_folder(append_folder)
    if prepend:
        parts.append(prepend)
    if directory:
        parts.append(directory)
    if append:
        parts.append(append)
    parts.append(stem)
    return "/".join(parts) + f".{suffix}"
