"""Filesystem layout for the FastAPI app package (independent of process cwd)."""

from __future__ import annotations

from pathlib import Path

# backend/backend/
BACKEND_DIR = Path(__file__).resolve().parent
STATIC_DIR = BACKEND_DIR / "static"


def media_path(db_relative_path: str) -> Path:
    """Resolve a DB path like ``static/memory/personal/uuid.jpg`` to an absolute file path."""
    s = (db_relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        return BACKEND_DIR / "__invalid__"
    return BACKEND_DIR / s
