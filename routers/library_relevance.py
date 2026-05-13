"""Score generic library topics/bundles against the patient profile (caretaker is not scored)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence


def _tokenize(s: str) -> List[str]:
    t = (s or "").lower().strip()
    if not t:
        return []
    parts = re.split(r"[\s,_\-/;|]+", t)
    return [p for p in parts if len(p) > 1]


def profession_haystack_tokens(*raws: Optional[str]) -> List[str]:
    """Keywords from profession string(s) for topic ordering (typically patient only)."""
    acc: List[str] = []
    for r in raws:
        if r and str(r).strip():
            acc.extend(_tokenize(str(r)))
    return acc


def parse_json_string_list(raw: Optional[str]) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return _tokenize(str(raw))
    if isinstance(data, list):
        out: List[str] = []
        for x in data:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out
    if isinstance(data, str) and data.strip():
        return [data.strip()]
    return []


def _haystack(slug: str, label: str) -> str:
    return f"{slug} {label}".lower().replace("-", "_")


def topic_match_score(
    topic_slug: str,
    topic_label: str,
    interests: Sequence[str],
    sub_interests: Sequence[str],
    patient_profession_tokens: Sequence[str],
) -> float:
    """Higher = better fit for topic ordering (patient interests, sub-interests, profession)."""
    hay = _haystack(topic_slug, topic_label)
    score = 0.0
    for t in patient_profession_tokens:
        tl = (t or "").lower()
        if len(tl) > 2 and tl in hay:
            score += 0.38
    for interest in interests:
        il = (interest or "").lower().strip()
        if not il:
            continue
        il_underscore = il.replace(" ", "_")
        if il_underscore in hay or il in hay:
            score += 0.42
            continue
        for part in _tokenize(il):
            if len(part) > 2 and part in hay:
                score += 0.28
    for si in sub_interests:
        sl = (si or "").lower().strip()
        if len(sl) > 2 and sl in hay:
            score += 0.12
    return float(min(score, 1.0))


def _bundle_match_text_from_manifest(manifest: Optional[Dict[str, Any]]) -> str:
    """Collect human-readable bundle + image text for lexical matching (titles, blurbs, tags)."""
    if not manifest:
        return ""
    chunks: List[str] = []
    raw = manifest.get("__bundle__")
    if isinstance(raw, dict):
        for key in (
            "title",
            "name",
            "display_name",
            "label",
            "description",
            "blurb",
            "subtitle",
        ):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                chunks.append(val.strip())
        for key in ("keywords", "tags", "topics", "subtopics"):
            val = raw.get(key)
            if isinstance(val, list):
                chunks.extend(str(x).strip() for x in val if str(x).strip())

    max_chars = 4500
    used = sum(len(c) + 1 for c in chunks)
    for k, v in manifest.items():
        if k == "__bundle__" or used >= max_chars:
            break
        if not isinstance(v, dict):
            continue
        for fk in ("title", "description", "caption", "location"):
            s = v.get(fk)
            if isinstance(s, str) and (t := s.strip()):
                chunks.append(t)
                used += len(t) + 1
                if used >= max_chars:
                    break
    return " ".join(chunks)


def bundle_match_score(
    bundle_slug: str,
    display_name: str,
    interests: Sequence[str],
    sub_interests: Sequence[str],
    manifest: Optional[Dict[str, Any]] = None,
) -> float:
    """Higher = better fit for bundle ordering (slug, display name, manifest bundle title + image titles)."""
    extra = _bundle_match_text_from_manifest(manifest)
    text = f"{bundle_slug} {display_name} {extra}".lower().replace("_", " ")
    score = 0.0
    for si in sub_interests:
        sl = (si or "").lower().strip()
        if len(sl) > 1 and sl in text:
            score += 0.55
    for interest in interests:
        il = (interest or "").lower().strip()
        if len(il) > 1 and il in text:
            score += 0.22
    return float(min(score, 1.0))


def manifest_gender_penalty(manifest: Dict[str, Any], patient_gender: Optional[str]) -> float:
    """Return 1.0 if OK, or a small factor (e.g. 0.08) if bundle targets other genders."""
    if not patient_gender:
        return 1.0
    raw = manifest.get("__bundle__")
    if not isinstance(raw, dict):
        return 1.0
    genders = raw.get("genders") or raw.get("for_genders") or raw.get("target_genders")
    if not isinstance(genders, list) or not genders:
        return 1.0
    allowed = {str(x).strip().lower() for x in genders if str(x).strip()}
    if not allowed:
        return 1.0
    pg = patient_gender.strip().lower()
    if pg in allowed or "all" in allowed or "any" in allowed:
        return 1.0
    return 0.08
