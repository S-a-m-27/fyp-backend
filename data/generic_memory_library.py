"""Generic memory library — fully disk-driven (no fixed category list).

Layout::

  static/memory/generic/<topic_folder>/<bundle_folder>/manifest.json
  static/memory/generic/<topic_folder>/<bundle_folder>/<any_image_name>.jpg

- **Topic** = any safe top-level folder name under ``generic/`` (e.g. ``cricket``).
  Topics appear in the API only if they contain at least one bundle subfolder
  that has a ``manifest.json``.
- **Bundle** = subfolder with ``manifest.json`` + images. There is no image count
  cap; each file listed in the manifest (exact filename key, non-empty
  ``title``) can become a DB row at sync time.

**Bundle pricing** (optional) lives in the same ``manifest.json`` under the
reserved key ``__bundle__``::

    {
      "__bundle__": { "free": true },
      "photo_a.jpg": { "title": "...", "location": "..." }
    }

Paid example::

    {
      "__bundle__": { "price_cents": 499, "currency": "USD" },
      "photo_a.jpg": { "title": "..." }
    }

You may also use ``"price": 4.99`` (dollars) instead of ``price_cents``. If
``__bundle__`` is missing or has no paid price, the bundle is treated as **free**
(all patients can see it without a purchase).

You may also set ``title`` / ``display_name`` / ``name`` on ``__bundle__`` for the
catalog card label; optional ``description``, ``keywords``, ``tags`` and each
image's ``title`` are included when ranking bundles against a patient's interests.

The constant ``DEFAULT_GENERIC_BUNDLE_SLUG`` is only a **suggested** default folder
name for docs; visibility is driven by ``__bundle__``, not by folder name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from app_paths import STATIC_DIR

# Suggested default bundle folder name (docs / legacy); not used for access control.
DEFAULT_GENERIC_BUNDLE_SLUG = "included"

BUNDLE_MANIFEST_META_KEY = "__bundle__"
SAFE_SEGMENT_MAX_LEN = 120

_disk_cache_generation = 0
_cached_free_pairs: Tuple[int, Set[Tuple[str, str]]] = (0, set())


def bump_generic_library_disk_cache() -> None:
    """Call after ``sync_disk_generic_library_to_db`` so free-bundle sets refresh."""
    global _disk_cache_generation
    _disk_cache_generation += 1


def is_safe_library_segment(name: str) -> bool:
    if not name or len(name) > SAFE_SEGMENT_MAX_LEN:
        return False
    if name.startswith(".") or ".." in name:
        return False
    if "/" in name or "\\" in name or name.strip() != name:
        return False
    return True


def humanize_folder_label(slug: str) -> str:
    t = (slug or "").replace("_", " ").replace("-", " ").strip()
    return t.title() if t else slug


def generic_library_root() -> Path:
    return (STATIC_DIR / "memory" / "generic").resolve()


def load_bundle_manifest_dict(bundle_dir: Path) -> Dict[str, Any]:
    mf = bundle_dir / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        raw = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def parse_bundle_pricing(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``is_free``, ``price_cents``, ``currency`` from ``__bundle__`` meta."""
    raw = manifest.get(BUNDLE_MANIFEST_META_KEY)
    if not isinstance(raw, dict):
        return {"is_free": True, "price_cents": 0, "currency": "USD"}
    if raw.get("free") is True:
        return {
            "is_free": True,
            "price_cents": 0,
            "currency": str(raw.get("currency") or "USD"),
        }
    pc = raw.get("price_cents")
    if isinstance(pc, (int, float)) and int(pc) > 0:
        return {
            "is_free": False,
            "price_cents": int(pc),
            "currency": str(raw.get("currency") or "USD"),
        }
    price = raw.get("price")
    if price is not None:
        try:
            dollars = float(price)
            if dollars > 0:
                return {
                    "is_free": False,
                    "price_cents": int(round(dollars * 100)),
                    "currency": str(raw.get("currency") or "USD"),
                }
        except (TypeError, ValueError):
            pass
    return {"is_free": True, "price_cents": 0, "currency": "USD"}


def bundle_is_free_on_disk(topic_slug: str, bundle_slug: str) -> bool:
    root = generic_library_root()
    bd = root / topic_slug / bundle_slug
    if not bd.is_dir():
        return False
    manifest = load_bundle_manifest_dict(bd)
    return bool(parse_bundle_pricing(manifest)["is_free"])


def _compute_free_generic_bundle_pairs(root: Path) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    if not root.is_dir():
        return out
    for topic_path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not topic_path.is_dir() or not is_safe_library_segment(topic_path.name):
            continue
        topic = topic_path.name
        for bundle_path in sorted(topic_path.iterdir(), key=lambda p: p.name.lower()):
            if not bundle_path.is_dir() or not is_safe_library_segment(bundle_path.name):
                continue
            if not (bundle_path / "manifest.json").is_file():
                continue
            manifest = load_bundle_manifest_dict(bundle_path)
            if parse_bundle_pricing(manifest)["is_free"]:
                out.add((topic, bundle_path.name))
    return out


def get_free_generic_bundle_pairs() -> Set[Tuple[str, str]]:
    global _cached_free_pairs
    gen = _disk_cache_generation
    if _cached_free_pairs[0] == gen:
        return _cached_free_pairs[1]
    pairs = _compute_free_generic_bundle_pairs(generic_library_root())
    _cached_free_pairs = (gen, pairs)
    return pairs


def discover_generic_topic_cards() -> List[Dict[str, Any]]:
    """Topics for catalog UI: one card per on-disk topic that has ≥1 valid bundle."""
    root = generic_library_root()
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    for topic_path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not topic_path.is_dir() or not is_safe_library_segment(topic_path.name):
            continue
        slug = topic_path.name
        bundle_slugs = [
            c.name
            for c in sorted(topic_path.iterdir(), key=lambda p: p.name.lower())
            if c.is_dir()
            and is_safe_library_segment(c.name)
            and (c / "manifest.json").is_file()
        ]
        if not bundle_slugs:
            continue
        out.append(
            {
                "slug": slug,
                "label": humanize_folder_label(slug),
                "blurb": "",
                "default_bundle_slug": bundle_slugs[0],
            },
        )
    return out


def list_bundle_slugs_on_disk(topic_slug: str) -> List[str]:
    root = generic_library_root()
    td = root / topic_slug
    if not td.is_dir() or not is_safe_library_segment(topic_slug):
        return []
    return [
        c.name
        for c in sorted(td.iterdir(), key=lambda p: p.name.lower())
        if c.is_dir()
        and is_safe_library_segment(c.name)
        and (c / "manifest.json").is_file()
    ]


def topic_exists_on_disk(topic_slug: str) -> bool:
    return bool(list_bundle_slugs_on_disk(topic_slug))
