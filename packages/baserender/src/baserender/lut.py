"""Helpers for 3D LUT (.cube) files."""

from __future__ import annotations


def normalize_cube_bytes(data: bytes) -> bytes:
    """Rewrite .cube data values MediaConvert's validator rejects.

    MediaConvert fails LUT validation on value formats FFmpeg and Resolve
    accept: scientific notation (``4.57771e-05``) and bare integers (``0``).
    Both are rewritten as plain decimal floats; ordinary float tokens pass
    through untouched so no precision is lost. Non-UTF-8 content is returned
    unchanged.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    lines = []
    changed = False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(_is_number(part) for part in parts):
            normalized = " ".join(_normalize_token(part) for part in parts)
            if normalized != line:
                changed = True
            lines.append(normalized)
        else:
            lines.append(line)

    if not changed:
        return data
    return ("\n".join(lines) + "\n").encode("utf-8")


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def _normalize_token(token: str) -> str:
    if "e" in token.lower():
        fixed = f"{float(token):.10f}".rstrip("0")
        return f"{fixed}0" if fixed.endswith(".") else fixed
    if "." in token:
        return token
    return f"{token}.0"
