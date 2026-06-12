from __future__ import annotations

from baserender.lut import normalize_cube_bytes


def test_integer_values_become_floats() -> None:
    cube = b'TITLE "Test"\nLUT_3D_SIZE 2\n\n0 0 0.205936\n1 0.5 1\n'

    normalized = normalize_cube_bytes(cube).decode("utf-8")

    assert "0.0 0.0 0.205936" in normalized
    assert "1.0 0.5 1.0" in normalized
    # Header lines untouched (LUT_3D_SIZE 2 is two tokens, not a data line).
    assert 'TITLE "Test"' in normalized
    assert "LUT_3D_SIZE 2" in normalized


def test_float_values_pass_through_unchanged() -> None:
    cube = b'TITLE "Test"\nLUT_3D_SIZE 2\n\n0.0 0.0 0.2059364321\n'

    assert normalize_cube_bytes(cube) == cube


def test_scientific_notation_rewritten_as_fixed_point() -> None:
    cube = b"LUT_3D_SIZE 2\n\n4.57771e-05 0.5 0.25\n1e-05 1 0.25\n"

    normalized = normalize_cube_bytes(cube).decode("utf-8")

    assert "0.0000457771 0.5 0.25" in normalized
    assert "0.00001 1.0 0.25" in normalized


def test_non_utf8_returned_unchanged() -> None:
    blob = b"\xff\xfe\x00binary"

    assert normalize_cube_bytes(blob) == blob
