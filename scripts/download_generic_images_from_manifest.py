"""
One-time download helper (uses internet ONLY while you run this script).

Create a JSON map of ``topic/bundle/filename`` → direct image URL. Filenames can
be anything; they must match what you list in each bundle's ``manifest.json``.

Example ``generic_urls.json``::

  {
    "war_history/included/my_photo.jpg": "https://example.com/direct-link.jpg"
  }

Run from ``backend/backend``::

  python scripts/download_generic_images_from_manifest.py generic_urls.json

Files are saved under ``static/memory/generic/<key>``. Add matching
``manifest.json`` in that bundle folder (same filenames as keys), then restart
the API so rows sync into the database.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_GENERIC = ROOT / "static" / "memory" / "generic"


def download_one(url: str, dest: Path, timeout: int = 60) -> None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MemoryJoggerGenericImageSetup/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.write_bytes(data)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    manifest_path = Path(sys.argv[1]).resolve()
    if not manifest_path.is_file():
        print(f"Manifest not found: {manifest_path}")
        sys.exit(1)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        print("Manifest must be a JSON object: filename -> image URL string")
        sys.exit(1)

    STATIC_GENERIC.mkdir(parents=True, exist_ok=True)
    ok, skipped, failed = 0, 0, []

    for rel_key, url in raw.items():
        if not isinstance(rel_key, str) or not isinstance(url, str):
            failed.append((rel_key, "bad types"))
            continue
        rel_key = rel_key.strip().lstrip("/").replace("\\", "/")
        url = url.strip()
        if not url:
            skipped += 1
            continue
        dest = STATIC_GENERIC / rel_key
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            print(f"GET {rel_key} …")
            download_one(url, dest)
            ok += 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            failed.append((rel_key, str(e)))

    print(f"Done. Downloaded: {ok}, skipped empty URL: {skipped}, failed: {len(failed)}")
    for fn, err in failed:
        print(f"  FAIL {fn}: {err}")


if __name__ == "__main__":
    main()
