"""Load local .env files for development."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "apps" / "api").is_dir() and (parent / "packages" / "baserender").is_dir():
            return parent
    return Path.cwd()


def load_local_env() -> None:
    """Load service and repo-root .env without overriding existing environment variables."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    root = _repo_root()
    service_dir = Path(__file__).resolve().parents[2]

    for path in (service_dir / ".env", root / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)
