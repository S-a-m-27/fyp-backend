"""
Run from the backend/backend folder:

  cd backend/backend
  python scripts/export_generic_image_checklist.py

Writes generic_image_checklist.csv listing every on-disk generic image that has
a matching key in that bundle's manifest.json (same filename). Use it as a
template or audit — the API does not read this CSV; it syncs from folders +
manifest only.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.generic_memory_library import is_safe_library_segment  # noqa: E402

_STATIC_GENERIC = ROOT / "static" / "memory" / "generic"
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _load_manifest(bundle_dir: Path) -> dict:
    mf = bundle_dir / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        raw = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def main() -> None:
    out = ROOT / "static" / "memory" / "generic" / "generic_image_checklist.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[list[str]] = []
    if not _STATIC_GENERIC.is_dir():
        print(f"No folder {_STATIC_GENERIC}")
        return
    for topic_path in sorted(_STATIC_GENERIC.iterdir(), key=lambda p: p.name):
        if not topic_path.is_dir() or not is_safe_library_segment(topic_path.name):
            continue
        topic = topic_path.name
        for bundle_path in sorted(topic_path.iterdir(), key=lambda p: p.name):
            if (
                not bundle_path.is_dir()
                or not is_safe_library_segment(bundle_path.name)
            ):
                continue
            bundle = bundle_path.name
            manifest = _load_manifest(bundle_path)
            for fp in sorted(bundle_path.iterdir(), key=lambda p: p.name):
                if not fp.is_file() or fp.suffix.lower() not in _IMAGE_EXT:
                    continue
                meta = manifest.get(fp.name)
                if not isinstance(meta, dict):
                    continue
                title = str(meta.get("title") or "").strip()
                loc = str(meta.get("location") or "").strip()
                rel = f"static/memory/generic/{topic}/{bundle}/{fp.name}".replace(
                    "\\", "/",
                )
                mk = f"{topic}/{bundle}/{fp.name}".replace("\\", "/")
                rows.append([mk, topic, bundle, fp.name, title, loc, rel])

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "manifest_key",
                "topic",
                "bundle",
                "filename",
                "title",
                "location",
                "file_path_relative_to_backend",
            ],
        )
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
