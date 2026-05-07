import json
import os
import random
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import and_, func, or_, tuple_
from sqlalchemy.orm import Session

import models
import schemas
from app_paths import STATIC_DIR, media_path
from data.generic_memory_library import (
    DEFAULT_GENERIC_BUNDLE_SLUG,
    bump_generic_library_disk_cache,
    discover_generic_topic_cards,
    get_free_generic_bundle_pairs,
    is_safe_library_segment,
)
from database import get_db
from routers.memory_catalog import (
    _patient_auth as _catalog_patient_auth,
    generic_topic_image_counts,
)

router = APIRouter(prefix="/memory", tags=["Memory Management"])

TRAINING_SESSION_IMAGE_TARGET = 8


_GENERIC_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_GENERIC_MANIFEST_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_MANIFEST_MAX_FIELD_LEN = 4000


def _load_bundle_manifest(bundle_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Parse ``manifest.json`` in a bundle folder: ``{ \"photo.jpg\": { \"title\", \"location\", \"description\" } }``."""
    mf = bundle_dir / "manifest.json"
    if not mf.is_file():
        return {}
    try:
        raw = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        key = k.strip()
        if not key:
            continue
        out[key] = v
    return out


def _meta_strings(meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    def clip(s: Optional[str]) -> Optional[str]:
        if s is None:
            return None
        t = str(s).strip()
        if not t:
            return None
        return t[:_MANIFEST_MAX_FIELD_LEN]

    title = clip(meta.get("title"))
    location = clip(meta.get("location"))
    description = clip(meta.get("description"))
    return title, location, description


def _manifest_meta_for_file(
    manifest: Dict[str, Dict[str, Any]], fname: str
) -> Dict[str, Any]:
    """Resolve manifest entry when keys differ by extension (e.g. ``1.jpg`` vs ``1.png``)."""
    if not fname or not manifest:
        return {}
    direct = manifest.get(fname)
    if isinstance(direct, dict) and direct:
        return direct
    stem = Path(fname).stem
    if not stem:
        return {}
    for ext in _GENERIC_MANIFEST_IMAGE_EXTS:
        key = f"{stem}{ext}"
        if key == fname:
            continue
        m = manifest.get(key)
        if isinstance(m, dict) and m:
            return m
    for key, m in manifest.items():
        if not isinstance(key, str) or not isinstance(m, dict) or not m:
            continue
        if Path(key).stem == stem:
            return m
    return {}


def sync_disk_generic_library_to_db(db: Session) -> int:
    """Create ``MemoryItem`` rows only for image files that exist on disk **and**
    have a matching entry in that bundle's ``manifest.json`` (exact filename key,
    non-empty ``title``).

    There is **no** curated seed of placeholder rows: the database reflects your
    folders + manifests only. Restart the API after adding files or editing
    ``manifest.json`` to re-sync.
    """
    root = STATIC_DIR / "memory" / "generic"
    if not root.is_dir():
        return 0
    (STATIC_DIR / "memory" / "generic").mkdir(parents=True, exist_ok=True)
    existing = {
        fp
        for (fp,) in db.query(models.MemoryItem.file_path)
        .filter(
            models.MemoryItem.library_type == "generic",
            models.MemoryItem.file_path.isnot(None),
        )
        .all()
        if fp
    }
    added = 0
    updated = 0
    for topic_path in sorted(root.iterdir(), key=lambda p: p.name):
        if not topic_path.is_dir() or topic_path.name.startswith("."):
            continue
        topic = topic_path.name
        if not is_safe_library_segment(topic):
            continue
        for bundle_path in sorted(topic_path.iterdir(), key=lambda p: p.name):
            if not bundle_path.is_dir() or bundle_path.name.startswith("."):
                continue
            bundle = bundle_path.name
            if not is_safe_library_segment(bundle):
                continue
            manifest_path = bundle_path / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = _load_bundle_manifest(bundle_path)
            if not manifest:
                continue
            for fp in sorted(bundle_path.iterdir(), key=lambda p: p.name):
                if not fp.is_file() or fp.suffix.lower() not in _GENERIC_IMAGE_SUFFIXES:
                    continue
                rel = f"static/memory/generic/{topic}/{bundle}/{fp.name}".replace("\\", "/")
                raw_meta = manifest.get(fp.name)
                if not isinstance(raw_meta, dict):
                    continue
                mt, mloc, mdesc = _meta_strings(raw_meta)
                if not mt:
                    continue
                if rel in existing:
                    continue
                db.add(
                    models.MemoryItem(
                        patient_id=None,
                        title=mt,
                        description=mdesc,
                        related_person_name=None,
                        related_person_relation=None,
                        category="image",
                        library_type="generic",
                        library_topic=topic,
                        library_collection_slug=bundle,
                        memory_type="general",
                        year=None,
                        location=mloc,
                        caretaker_email=None,
                        file_path=rel,
                        extra_file_paths=None,
                    ),
                )
                existing.add(rel)
                added += 1

            # Apply manifest updates to existing rows (stem/extension match allowed).
            for fp in sorted(bundle_path.iterdir(), key=lambda p: p.name):
                if not fp.is_file() or fp.suffix.lower() not in _GENERIC_IMAGE_SUFFIXES:
                    continue
                rel_path = f"static/memory/generic/{topic}/{bundle}/{fp.name}".replace(
                    "\\",
                    "/",
                )
                meta_raw = _manifest_meta_for_file(manifest, fp.name)
                if not isinstance(meta_raw, dict):
                    continue
                mt, mloc, mdesc = _meta_strings(meta_raw)
                if mt is None and mloc is None and mdesc is None:
                    continue
                row = (
                    db.query(models.MemoryItem)
                    .filter(
                        models.MemoryItem.library_type == "generic",
                        models.MemoryItem.file_path == rel_path,
                    )
                    .first()
                )
                if not row:
                    continue
                changed = False
                if mt is not None and row.title != mt:
                    row.title = mt
                    changed = True
                if mloc is not None and row.location != mloc:
                    row.location = mloc
                    changed = True
                if mdesc is not None and row.description != mdesc:
                    row.description = mdesc
                    changed = True
                if changed:
                    updated += 1

    if added or updated:
        db.commit()
    bump_generic_library_disk_cache()
    return added + updated


def _generic_manifest_title_for_memory(m: models.MemoryItem) -> Optional[str]:
    """Resolve display title from bundle ``manifest.json`` when present."""
    if (m.library_type or "") != "generic":
        return None
    fp = (m.file_path or "").replace("\\", "/")
    if "/generic/" not in fp:
        return None
    try:
        tail = fp.split("/generic/", 1)[1]
        parts = tail.split("/")
        if len(parts) < 3:
            return None
        topic, bundle, fname = parts[0], parts[1], parts[2]
        bundle_dir = STATIC_DIR / "memory" / "generic" / topic / bundle
        manifest = _load_bundle_manifest(bundle_dir)
        meta = _manifest_meta_for_file(manifest, fname)
        title, _, _ = _meta_strings(meta)
        return title
    except (IndexError, OSError, TypeError):
        return None


def _memory_to_gallery_dict(m: models.MemoryItem) -> Dict[str, Any]:
    title = m.title
    if (m.library_type or "") == "generic":
        mt = _generic_manifest_title_for_memory(m)
        if mt:
            title = mt
    return {
        "id": m.id,
        "title": title,
        "location": m.location,
        "description": m.description,
        "file_path": m.file_path,
        "category": m.category,
        "library_type": m.library_type,
        "library_topic": getattr(m, "library_topic", None),
        "library_collection_slug": getattr(m, "library_collection_slug", None),
        "memory_type": m.memory_type,
        "patient_id": m.patient_id,
        "related_person_name": getattr(m, "related_person_name", None),
        "related_person_relation": getattr(m, "related_person_relation", None),
    }


def _training_image_count(mems: List[models.MemoryItem]) -> int:
    return sum(
        1
        for m in mems
        if (m.category or "") == "image" and (m.file_path or "").strip()
    )


def _norm_training_file_path(fp: Optional[str]) -> str:
    return (fp or "").replace("\\", "/").strip()


def _slug_segment(label: str) -> str:
    s = (label or "").strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "custom"


def _eligible_memories_query(db: Session, patient_id: int):
    """Memories visible to a patient = primary + shared + generic (starter + purchased)."""
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id)
        .first()
    )
    if not patient:
        return db.query(models.MemoryItem).filter(models.MemoryItem.id == -1)

    shared_subq = (
        db.query(models.memory_patient_access.c.memory_id)
        .filter(models.memory_patient_access.c.patient_id == patient_id)
        .subquery()
    )

    ce = patient.caretaker_email or ""
    purchase_rows = (
        db.query(
            models.CaretakerBundlePurchase.library_topic,
            models.CaretakerBundlePurchase.library_collection_slug,
        )
        .filter(
            models.CaretakerBundlePurchase.patient_id == patient_id,
            models.CaretakerBundlePurchase.caretaker_email == ce,
            models.CaretakerBundlePurchase.locked.is_(False),
        )
        .all()
    )
    purchase_tuples = [(a, b) for a, b in purchase_rows if a and b is not None]

    free_pairs = get_free_generic_bundle_pairs()
    conds = []
    if free_pairs:
        conds.append(
            tuple_(
                models.MemoryItem.library_topic,
                models.MemoryItem.library_collection_slug,
            ).in_(list(free_pairs)),
        )
    if purchase_tuples:
        conds.append(
            tuple_(
                models.MemoryItem.library_topic,
                models.MemoryItem.library_collection_slug,
            ).in_(purchase_tuples),
        )

    if conds:
        generic_ok = and_(
            models.MemoryItem.library_type == "generic",
            or_(*conds),
        )
    else:
        generic_ok = and_(
            models.MemoryItem.library_type == "generic",
            models.MemoryItem.id.in_([]),
        )

    return db.query(models.MemoryItem).filter(
        (models.MemoryItem.patient_id == patient_id)
        | (models.MemoryItem.id.in_(shared_subq))
        | generic_ok,
    )


# --- 1. UPLOAD MEMORY ---
# Generic branch: optional admin/dev uploads. Production generic rows come from
# on-disk folders + manifest.json via sync_disk_generic_library_to_db at startup.
# Generic images are never passed through face-embedding training (quiz answers
# are memory titles / manifest metadata). Only POST /memory/personal/upload trains.
@router.post("/upload")
async def upload_memory(
    title: str = Form(...),
    category: str = Form(...),  # image, video, audio
    library_type: str = Form(...),  # generic, personal
    patient_id: int = Form(None),
    library_topic: Optional[str] = Form(None),
    library_collection_slug: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    lt = (library_topic or "").strip()
    lc = (library_collection_slug or "").strip()
    if library_type == "generic" and lt:
        bundle = _slug_segment(lc) if lc else DEFAULT_GENERIC_BUNDLE_SLUG
        topic = _slug_segment(lt)
        abs_upload_dir = STATIC_DIR / "memory" / "generic" / topic / bundle
    else:
        abs_upload_dir = STATIC_DIR / "memory" / library_type

    abs_upload_dir.mkdir(parents=True, exist_ok=True)

    file_ext = file.filename.split(".")[-1]
    filename = f"{uuid.uuid4().hex}.{file_ext}"
    rel_parts = ["static", "memory"]
    if library_type == "generic" and lt:
        rel_parts += ["generic", topic, bundle, filename]
    else:
        rel_parts += [library_type, filename]
    file_path = "/".join(rel_parts)

    abs_file = media_path(file_path)
    with open(abs_file, "wb") as f:
        f.write(await file.read())

    new_memory = models.MemoryItem(
        patient_id=patient_id,
        title=title,
        category=category,
        library_type=library_type,
        library_topic=_slug_segment(lt) if lt and library_type == "generic" else None,
        library_collection_slug=(
            (_slug_segment(lc) if lc else DEFAULT_GENERIC_BUNDLE_SLUG)
            if lt and library_type == "generic"
            else None
        ),
        file_path=file_path,
    )
    db.add(new_memory)
    db.commit()

    return {"status": "success", "message": "Memory added successfully!"}


# --- Generic library metadata ---
@router.get("/generic/topics", response_model=List[schemas.GenericTopicInfo])
def list_generic_topics(db: Session = Depends(get_db)):
    """Topic cards from on-disk folders; ``approx_count`` = generic images in DB for that topic."""
    topics = discover_generic_topic_cards()
    counts = generic_topic_image_counts(db)
    return [
        schemas.GenericTopicInfo(
            slug=t["slug"],
            label=t["label"],
            blurb=t.get("blurb") or "",
            approx_count=counts.get(t["slug"], 0),
            default_bundle_slug=t.get("default_bundle_slug") or "",
        )
        for t in topics
    ]


# --- Patient training gallery (JSON-safe) ---
@router.get("/patient-training-gallery/{patient_id}", response_model=List[schemas.MemoryGalleryItem])
def get_patient_training_gallery(
    patient_id: int,
    exclude_ids: str = Query(
        "",
        description="Comma-separated memory ids to skip (last session). Requires passcode or qr_token.",
    ),
    passcode: Optional[str] = Query(None),
    qr_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """All memories a patient may see: generic library + personal + shared.

    Optional ``exclude_ids`` omits items from the *previous* training session so
    the next session prefers different photos. The filtered list is used whenever
    at least one eligible training image remains after exclusions; the full list
    is returned only when exclusions would leave no training images at all.
    """
    excl = {int(x) for x in exclude_ids.split(",") if x.strip().isdigit()}
    if excl and not (passcode or qr_token):
        raise HTTPException(
            status_code=400,
            detail="exclude_ids requires passcode or qr_token query parameters.",
        )
    if excl and (passcode or qr_token):
        _catalog_patient_auth(db, patient_id, passcode, qr_token)

    rows = _eligible_memories_query(db, patient_id).order_by(models.MemoryItem.id).all()
    if excl and (passcode or qr_token):
        excluded_paths: Set[str] = set()
        for m in rows:
            if m.id in excl and (m.category or "") == "image" and (m.file_path or "").strip():
                excluded_paths.add(_norm_training_file_path(m.file_path))

        def _training_row_kept_after_exclude(m: models.MemoryItem) -> bool:
            if m.id in excl:
                return False
            if excluded_paths and (m.category or "") == "image":
                if _norm_training_file_path(m.file_path) in excluded_paths:
                    return False
            return True

        filtered = [m for m in rows if _training_row_kept_after_exclude(m)]
        cnt_f = _training_image_count(filtered)
        # Use the filtered pool whenever at least one training image remains, so the
        # next session is not forced to repeat last session's photos when the total
        # library is smaller than TRAINING_SESSION_IMAGE_TARGET (common with few
        # personal photos plus generics). Only fall back to the full list when
        # exclusions would remove every training image (e.g. patient has ≤8 total).
        if cnt_f >= TRAINING_SESSION_IMAGE_TARGET:
            rows = filtered
        elif cnt_f >= 1:
            rows = filtered

    return [_memory_to_gallery_dict(m) for m in rows]


@router.get("/all/{patient_id}")
async def get_all_memories(patient_id: int, db: Session = Depends(get_db)):
    """Same pool as the training gallery; list of plain dicts for older clients."""
    rows = _eligible_memories_query(db, patient_id).all()
    return [_memory_to_gallery_dict(m) for m in rows]


# --- Patient app: wellness intro, training sessions, per-image ratings ---
@router.get(
    "/patient/training-progress",
    response_model=schemas.PatientTrainingProgressResponse,
)
def get_patient_training_progress(
    patient_id: int = Query(..., description="Patient id"),
    passcode: Optional[str] = Query(None),
    qr_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    p = _catalog_patient_auth(db, patient_id, passcode, qr_token)
    ses = int(getattr(p, "training_sessions_completed", 0) or 0)
    return schemas.PatientTrainingProgressResponse(
        wellness_intro_completed=bool(
            getattr(p, "wellness_intro_completed", False),
        ),
        training_sessions_completed=min(3, ses),
        quiz_unlocked=ses >= 3,
        memory_training_completed=bool(p.memory_training_completed),
    )


@router.post("/patient/wellness-intro-complete", response_model=dict)
def complete_wellness_intro(
    payload: schemas.PatientWellnessIntroCompleteRequest,
    db: Session = Depends(get_db),
):
    p = _catalog_patient_auth(db, payload.patient_id, payload.passcode, payload.qr_token)
    p.wellness_intro_completed = True
    db.commit()
    return {"status": "ok", "wellness_intro_completed": True}


@router.post("/patient/training-session-finish", response_model=dict)
def finish_training_session(
    payload: schemas.PatientTrainingSessionFinishRequest,
    db: Session = Depends(get_db),
):
    """Increment completed training sessions (max 3). Patient must be authenticated."""
    p = _catalog_patient_auth(db, payload.patient_id, payload.passcode, payload.qr_token)
    cur = int(getattr(p, "training_sessions_completed", 0) or 0)
    p.training_sessions_completed = min(3, cur + 1)
    db.add(
        models.Session(
            patient_id=p.id,
            patient_name=p.name or "",
            mode="Training session",
            duration_minutes=0,
        ),
    )
    db.commit()
    db.refresh(p)
    return {
        "status": "ok",
        "training_sessions_completed": p.training_sessions_completed,
        "quiz_unlocked": p.training_sessions_completed >= 3,
    }


@router.post("/patient/image-rating", response_model=dict)
def save_patient_image_rating(
    payload: schemas.PatientImageRatingRequest,
    db: Session = Depends(get_db),
):
    if payload.stars < 1 or payload.stars > 5:
        raise HTTPException(status_code=400, detail="stars must be 1–5")
    p = _catalog_patient_auth(db, payload.patient_id, payload.passcode, payload.qr_token)
    ok = (
        _eligible_memories_query(db, p.id)
        .filter(models.MemoryItem.id == payload.memory_item_id)
        .first()
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not available for this patient")

    if (ok.library_type or "") != "generic":
        raise HTTPException(
            status_code=400,
            detail="Star ratings apply only to library (generic) photos, not personal memories.",
        )

    row = (
        db.query(models.MemoryImageRating)
        .filter(
            models.MemoryImageRating.patient_id == p.id,
            models.MemoryImageRating.memory_item_id == payload.memory_item_id,
        )
        .first()
    )
    if row:
        row.stars = payload.stars
    else:
        db.add(
            models.MemoryImageRating(
                patient_id=p.id,
                memory_item_id=payload.memory_item_id,
                stars=payload.stars,
            ),
        )
    db.commit()
    return {"status": "ok", "memory_item_id": payload.memory_item_id, "stars": payload.stars}


# --- Mark patient onboarding done (after guided training) ---
@router.post("/patient-training-complete", response_model=schemas.PatientTrainingCompleteResponse)
def complete_patient_memory_training(
    payload: schemas.PatientTrainingCompleteRequest,
    db: Session = Depends(get_db),
):
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == payload.patient_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    qt = (payload.qr_token or "").strip()
    pt = (payload.passcode or "").strip()
    ok = (qt and (patient.qr_token or "").strip() == qt) or (
        pt and patient.passcode == pt
    )

    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid passcode or QR token for this patient",
        )

    patient.memory_training_completed = True
    db.commit()
    db.refresh(patient)

    return schemas.PatientTrainingCompleteResponse(
        status="ok",
        memory_training_completed=True,
    )


# --- GET QUIZ QUESTION (With Logic for No Repeats) ---
@router.get("/quiz/{patient_id}")
async def get_memory_quiz(
    patient_id: int,
    exclude_ids: str = Query(""),  # Format: "1,2,3" from Frontend
    passcode: Optional[str] = Query(None),
    qr_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Selects a random item that HAS NOT been answered correctly yet.

    When ``passcode`` or ``qr_token`` is sent, the patient must have completed
    three training sessions (quiz mode unlock).
    """
    if passcode or qr_token:
        p = _catalog_patient_auth(db, patient_id, passcode, qr_token)
        if int(getattr(p, "training_sessions_completed", 0) or 0) < 3:
            raise HTTPException(
                status_code=403,
                detail="Complete three gentle training sessions to unlock quiz mode.",
            )

    excluded_list = [int(i) for i in exclude_ids.split(",") if i.strip().isdigit()]

    base_query = _eligible_memories_query(db, patient_id)

    available_memories = base_query.filter(
        models.MemoryItem.id.not_in(excluded_list),
    ).all()

    if not available_memories:
        return {"status": "finished", "message": "All items completed!"}

    correct_item = random.choice(available_memories)

    all_memories = base_query.all()
    other_titles = list(
        {m.title for m in all_memories if m.title and m.title != correct_item.title},
    )

    if len(other_titles) < 3:
        raise HTTPException(
            status_code=400,
            detail="Not enough unique memory titles to generate multiple choices.",
        )

    distractors = random.sample(other_titles, 3)
    shuffled_options = distractors + [correct_item.title]
    random.shuffle(shuffled_options)

    return {
        "status": "ongoing",
        "question_item": {
            "id": correct_item.id,
            "title": correct_item.title,
            "location": correct_item.location,
            "description": correct_item.description,
            "file_path": correct_item.file_path,
            "category": correct_item.category,
            "library_type": correct_item.library_type,
            "library_topic": getattr(correct_item, "library_topic", None),
            "library_collection_slug": getattr(
                correct_item, "library_collection_slug", None
            ),
        },
        "shuffled_options": shuffled_options,
    }
