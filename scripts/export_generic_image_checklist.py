"""
Run from the backend/backend folder:

  cd backend/backend
  python scripts/export_generic_image_checklist.py

Writes generic_image_checklist.csv (filename, title, location) so you can
match downloads to the exact files the app expects.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.generic_memory_library import iter_generic_image_jobs  # noqa: E402


def main() -> None:
    out = ROOT / "static" / "memory" / "generic" / "generic_image_checklist.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    jobs = iter_generic_image_jobs()
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
        for j in jobs:
            w.writerow(
                [
                    j["manifest_key"],
                    j["library_topic"],
                    j["library_collection_slug"],
                    j["filename"],
                    j["title"],
                    j["location"],
                    j["file_path"],
                ],
            )
    print(f"Wrote {len(jobs)} rows to {out}")


if __name__ == "__main__":
    main()
