from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_RESERVED_PATH_PREFIXES = ("auth/", "media/", "jobs/", "worker/", "health")


def static_dir() -> Path:
    configured = os.getenv("BASERENDER_STATIC_DIR")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2] / "static"


def register_static_routes(app: FastAPI) -> None:
    directory = static_dir()
    index_file = directory / "index.html"

    if not index_file.is_file():
        logger.warning(
            "Static frontend not found at %s. Build apps/web and copy dist/ to apps/api/static/.",
            directory,
        )
        return

    assets_dir = directory / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/")
    async def serve_root() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        normalized = full_path.lstrip("/")
        if normalized.startswith(_RESERVED_PATH_PREFIXES):
            raise HTTPException(status_code=404, detail="Not found.")

        candidate = directory / normalized
        if candidate.is_file():
            return FileResponse(candidate)

        return FileResponse(index_file)
