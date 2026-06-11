#!/usr/bin/env python3
"""Bootstrap local secrets in apps/api/.env for Phase 2 development."""

from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "apps" / "api" / ".env"
EXAMPLE_PATH = ROOT / "apps" / "api" / ".env.example"

PLACEHOLDERS = {
    "BASERENDER_AUTH_PASSWORD": {"change-me", "replace-with-a-long-random-string"},
    "BASERENDER_SESSION_SECRET": {"replace-with-a-long-random-string"},
    "BASERENDER_WORKER_TOKEN": {"replace-with-a-long-random-worker-token"},
    "AWS_ACCESS_KEY_ID": {"your-access-key-id", ""},
    "AWS_SECRET_ACCESS_KEY": {"your-secret-access-key", ""},
    "BASERENDER_S3_BUCKET": {"your-bucket", ""},
}


def _token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def main() -> None:
    if not ENV_PATH.is_file():
        ENV_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Created {ENV_PATH.relative_to(ROOT)} from example.")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    updates = {
        "BASERENDER_AUTH_PASSWORD": _token(18),
        "BASERENDER_SESSION_SECRET": _token(32),
        "BASERENDER_WORKER_TOKEN": _token(32),
    }

    new_lines: list[str] = []
    applied: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            placeholders = PLACEHOLDERS.get(key)
            if placeholders and value in placeholders and key in updates:
                new_lines.append(f"{key}={updates[key]}")
                applied.append(key)
                continue
        new_lines.append(line)

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    if applied:
        print("Generated local secrets for:", ", ".join(applied))
    else:
        print("Local secrets already configured.")
    print(
        "\nConfigure AWS credentials in apps/api/.env, then run:\n"
        "  python scripts/aws/setup_dev.py"
    )


if __name__ == "__main__":
    main()
