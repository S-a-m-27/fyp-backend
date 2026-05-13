"""Personal-memory endpoints (caretaker-facing).

A "personal memory" is owned by a patient. The same caretaker may also share
a personal memory with their other patients (e.g. siblings sharing childhood
photos), so each memory has both:

  * a primary owner (`patient_id` on `MemoryItem`)
  * a list of additional patients in the `memory_patient_access` M2M table

This router covers everything the caretaker needs from the dashboard:

  POST   /memory/personal/upload                  upload a new memory for one patient
  GET    /memory/personal/patient/{patient_id}    list memories visible to a patient
  PATCH  /memory/personal/{memory_id}             edit metadata
  DELETE /memory/personal/{memory_id}             delete the memory + its file
  POST   /memory/personal/{memory_id}/share       grant access to extra patients
  GET    /memory/personal/{memory_id}/shareable-patients
                                                  list this caretaker's other patients
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from app_paths import STATIC_DIR, media_path
from database import get_db
from utils.caretaker_patient_access import (
    caretaker_can_access_patient,
    require_patient_for_caretaker,
)
from routers.ai_training import train_faces_from_paths

router = APIRouter(prefix="/memory/personal", tags=["Personal Memories"])

UPLOAD_DIR = str(STATIC_DIR / "memory" / "personal")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---------- helpers ----------


def _all_file_paths(memory: models.MemoryItem) -> List[str]:
    """Ordered list of all media paths (primary + extras)."""
    out: List[str] = []
    if memory.file_path:
        out.append(memory.file_path)
    raw = getattr(memory, "extra_file_paths", None)
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, list):
                for p in extra:
                    if isinstance(p, str) and p.strip():
                        out.append(p.strip())
        except (json.JSONDecodeError, TypeError):
            pass
    return out


def _serialize_library_memory_item(memory: models.MemoryItem) -> dict:
    """Generic library row in the same JSON shape as personal `_serialize` (read-only in UI)."""
    shared = memory.shared_with or []
    paths = _all_file_paths(memory)
    created = memory.created_at or datetime.now(timezone.utc)
    return {
        "id": memory.id,
        "patient_id": memory.patient_id,
        "title": (memory.title or "").strip() or "Library memory",
        "description": memory.description,
        "related_person_name": memory.related_person_name,
        "related_person_relation": memory.related_person_relation,
        "category": memory.category,
        "library_type": "generic",
        "library_topic": getattr(memory, "library_topic", None),
        "library_collection_slug": getattr(memory, "library_collection_slug", None),
        "memory_type": memory.memory_type or "general",
        "year": memory.year,
        "location": memory.location,
        "caretaker_email": memory.caretaker_email,
        "file_path": memory.file_path or (paths[0] if paths else ""),
        "file_paths": paths,
        "created_at": created,
        "shared_with_ids": [p.id for p in shared],
        "shared_with_names": [p.name or "" for p in shared],
    }


def _serialize(memory: models.MemoryItem) -> dict:
    """Build a dict matching `MemoryItemSchema`, with shared patient info."""
    shared = memory.shared_with or []
    paths = _all_file_paths(memory)
    return {
        "id": memory.id,
        "patient_id": memory.patient_id,
        "title": (memory.title or memory.related_person_name or "").strip()
        or "Personal memory",
        "description": memory.description,
        "related_person_name": memory.related_person_name,
        "related_person_relation": memory.related_person_relation,
        "category": memory.category,
        "library_type": memory.library_type,
        "library_topic": getattr(memory, "library_topic", None),
        "library_collection_slug": getattr(memory, "library_collection_slug", None),
        "memory_type": memory.memory_type,
        "year": memory.year,
        "location": memory.location,
        "caretaker_email": memory.caretaker_email,
        "file_path": memory.file_path or (paths[0] if paths else ""),
        "file_paths": paths,
        "created_at": memory.created_at,
        "shared_with_ids": [p.id for p in shared],
        "shared_with_names": [p.name or "" for p in shared],
    }


def append_eligible_generic_memory_dicts(
    db: Session,
    patient_id: int,
    out: List[dict],
) -> None:
    """Append serialized generic library rows the patient may use (same as Memories UI)."""
    from routers import memory as mem_router

    generic_rows = (
        mem_router._eligible_memories_query(db, patient_id)
        .filter(models.MemoryItem.library_type == "generic")
        .options(joinedload(models.MemoryItem.shared_with))
        .order_by(
            models.MemoryItem.library_topic,
            models.MemoryItem.library_collection_slug,
            models.MemoryItem.id,
        )
        .all()
    )
    out.extend(_serialize_library_memory_item(m) for m in generic_rows)


def _get_owned_memory(
    memory_id: int, caretaker_email: str, db: Session
) -> models.MemoryItem:
    """Fetch a memory the caretaker may edit (primary or delegated for that patient)."""
    memory = (
        db.query(models.MemoryItem)
        .options(joinedload(models.MemoryItem.shared_with))
        .filter(models.MemoryItem.id == memory_id)
        .first()
    )
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    pid = memory.patient_id
    if pid is not None:
        if not caretaker_can_access_patient(db, caretaker_email, int(pid)):
            raise HTTPException(
                status_code=403,
                detail="You don't have permission to modify this memory",
            )
        return memory
    if memory.caretaker_email and memory.caretaker_email.strip().casefold() != (
        caretaker_email or ""
    ).strip().casefold():
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to modify this memory",
        )
    return memory


# ---------- endpoints ----------

@router.post("/upload", response_model=schemas.MemoryItemSchema)
async def upload_personal_memory(
    title: Optional[str] = Form(None),
    patient_id: int = Form(...),
    caretaker_email: str = Form(...),
    memory_type: str = Form("specific"),  # "specific" | "general"
    category: str = Form("image"),         # "image" | "video" | "audio"
    year: Optional[int] = Form(None),
    location: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    related_person_name: Optional[str] = Form(None),
    related_person_relation: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="At least one image is required")

    # 1. Patient must exist and caller must be primary or delegated caretaker.
    patient = require_patient_for_caretaker(db, caretaker_email, patient_id)

    # 2. Save all uploaded files to disk (same memory, multiple angles of the person).
    saved_paths: List[str] = []
    for upload in files:
        ext = (upload.filename or "").rsplit(".", 1)[-1].lower() or "bin"
        safe_name = f"{uuid.uuid4().hex}.{ext}"
        rel_path = f"static/memory/personal/{safe_name}".replace("\\", "/")
        abs_path = media_path(rel_path)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(abs_path, "wb") as f:
            f.write(await upload.read())
        saved_paths.append(rel_path)

    primary = saved_paths[0]
    extra_json = json.dumps(saved_paths[1:]) if len(saved_paths) > 1 else None

    # 3. Persist.
    rname = (related_person_name or "").strip() or None
    rrel = (related_person_relation or "").strip() or None
    title_clean = (title or "").strip() or None
    if not title_clean and rname:
        title_clean = rname

    memory = models.MemoryItem(
        patient_id=patient_id,
        title=title_clean,
        description=(description or "").strip() or None,
        related_person_name=rname,
        related_person_relation=rrel,
        category=category,
        library_type="personal",
        memory_type=memory_type,
        year=year,
        location=(location or "").strip() or None,
        caretaker_email=(patient.caretaker_email or "").strip() or caretaker_email,
        file_path=primary,
        extra_file_paths=extra_json,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)

    out = _serialize(memory)
    ai_training_result: Optional[schemas.FaceTrainingResult] = None
    # Face embeddings only for personal relative photos (unknown faces in uploads).
    # Generic library images are never trained — quizzes use titles / manifest, not DeepFace.
    _personal_prefix = "static/memory/personal/"
    run_face_training = (
        category == "image"
        and bool(rname)
        and bool(saved_paths)
        and all(
            (p or "").replace("\\", "/").startswith(_personal_prefix)
            for p in saved_paths
        )
    )
    if run_face_training:
        try:
            train_out = train_faces_from_paths(
                rname,
                (rrel or "Unknown").strip(),
                saved_paths,
            )
            ai_training_result = schemas.FaceTrainingResult(
                status="ok",
                detail=train_out.get("message"),
                images_processed=train_out.get("images_processed"),
            )
        except HTTPException as he:
            ai_training_result = schemas.FaceTrainingResult(
                status="error",
                detail=str(he.detail) if he.detail else "Face training failed",
                images_processed=0,
            )
        except Exception as e:
            ai_training_result = schemas.FaceTrainingResult(
                status="error",
                detail=str(e),
                images_processed=0,
            )
    if ai_training_result is not None:
        out["ai_training"] = ai_training_result.model_dump()

    return out


@router.get("/patient/{patient_id}", response_model=List[schemas.MemoryItemSchema])
def list_personal_memories(
    patient_id: int,
    caretaker_email: str = Query(..., alias="email"),
    year: Optional[int] = Query(None),
    location: Optional[str] = Query(None),
    memory_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    include_eligible_generic: bool = Query(
        True,
        description="Append generic library photos this patient may see (included + purchased bundles).",
    ),
    db: Session = Depends(get_db),
):
    """Memories visible to this patient = memories they own + memories shared
    with them. Scoped to the caretaker so you can't peek into someone else's
    library.

    When ``include_eligible_generic`` is true (default), also returns generic
    library items so the Memories screen matches training/quiz visibility.
    """
    patient = require_patient_for_caretaker(db, caretaker_email, patient_id)

    shared_ids_subq = (
        db.query(models.memory_patient_access.c.memory_id)
        .filter(models.memory_patient_access.c.patient_id == patient_id)
        .subquery()
    )

    q = (
        db.query(models.MemoryItem)
        .options(joinedload(models.MemoryItem.shared_with))
        .filter(
            models.MemoryItem.library_type == "personal",
            or_(
                models.MemoryItem.patient_id == patient_id,
                models.MemoryItem.id.in_(shared_ids_subq),
            ),
        )
    )

    if year is not None:
        q = q.filter(models.MemoryItem.year == year)
    if location:
        q = q.filter(models.MemoryItem.location.ilike(f"%{location}%"))
    if memory_type:
        q = q.filter(models.MemoryItem.memory_type == memory_type)
    if category:
        q = q.filter(models.MemoryItem.category == category)

    memories = q.order_by(models.MemoryItem.created_at.desc()).all()
    out: List[dict] = [_serialize(m) for m in memories]

    if include_eligible_generic:
        append_eligible_generic_memory_dicts(db, patient_id, out)

    return out


@router.patch("/{memory_id}", response_model=schemas.MemoryItemSchema)
def update_personal_memory(
    memory_id: int,
    caretaker_email: str = Query(..., alias="email"),
    title: Optional[str] = Form(None),
    year: Optional[int] = Form(None),
    location: Optional[str] = Form(None),
    memory_type: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    related_person_name: Optional[str] = Form(None),
    related_person_relation: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    memory = _get_owned_memory(memory_id, caretaker_email, db)

    if title is not None:
        memory.title = title.strip()
    if year is not None:
        memory.year = year
    if location is not None:
        memory.location = location.strip() or None
    if memory_type is not None:
        memory.memory_type = memory_type
    if description is not None:
        memory.description = description.strip() or None
    if related_person_name is not None:
        memory.related_person_name = related_person_name.strip() or None
    if related_person_relation is not None:
        memory.related_person_relation = related_person_relation.strip() or None

    db.commit()
    db.refresh(memory)
    return _serialize(memory)


@router.delete("/{memory_id}")
def delete_personal_memory(
    memory_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    memory = _get_owned_memory(memory_id, caretaker_email, db)

    paths_to_remove = _all_file_paths(memory)
    db.delete(memory)
    db.commit()

    for p in paths_to_remove:
        if not p:
            continue
        abs_p = media_path(p)
        if abs_p.is_file():
            try:
                abs_p.unlink()
            except OSError:
                pass

    return {"status": "success", "message": "Memory deleted"}


@router.post("/{memory_id}/share", response_model=schemas.MemoryItemSchema)
def share_personal_memory(
    memory_id: int,
    payload: schemas.ShareMemoryRequest,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    """Replace the set of patients (besides the primary owner) who can access
    this memory. Only patients of the same caretaker can be granted access."""
    memory = _get_owned_memory(memory_id, caretaker_email, db)

    mem_owner = (
        db.query(models.Patient)
        .filter(models.Patient.id == memory.patient_id)
        .first()
    )
    if not mem_owner:
        raise HTTPException(status_code=400, detail="Memory has no primary patient")
    primary_email = (mem_owner.caretaker_email or "").strip()
    pel = primary_email.casefold()

    # Validate that every requested patient belongs to the same primary account.
    requested_ids = list({pid for pid in payload.patient_ids if pid != memory.patient_id})
    if requested_ids:
        valid_patients = (
            db.query(models.Patient)
            .filter(
                models.Patient.id.in_(requested_ids),
                func.lower(models.Patient.caretaker_email) == pel,
            )
            .all()
        )
        if len(valid_patients) != len(requested_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more patients don't belong to this caretaker",
            )
        memory.shared_with = valid_patients
    else:
        memory.shared_with = []

    db.commit()
    db.refresh(memory)
    return _serialize(memory)


@router.get(
    "/{memory_id}/shareable-patients",
    response_model=List[schemas.ShareablePatient],
)
def list_shareable_patients(
    memory_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    """List all of the caretaker's patients (excluding the memory's primary
    owner) so the UI can render a multi-select with current access state."""
    memory = _get_owned_memory(memory_id, caretaker_email, db)
    current_shared_ids = {p.id for p in (memory.shared_with or [])}

    mem_owner = (
        db.query(models.Patient)
        .filter(models.Patient.id == memory.patient_id)
        .first()
    )
    if not mem_owner:
        raise HTTPException(status_code=400, detail="Memory has no primary patient")
    primary_email = (mem_owner.caretaker_email or "").strip()
    pel = primary_email.casefold()

    others = (
        db.query(models.Patient)
        .filter(
            func.lower(models.Patient.caretaker_email) == pel,
            models.Patient.id != memory.patient_id,
        )
        .order_by(models.Patient.name)
        .all()
    )

    return [
        {
            "id": p.id,
            "name": p.name or "",
            "relation": p.relation,
            "profile_photo_path": p.profile_photo_path,
            "has_access": p.id in current_shared_ids,
        }
        for p in others
    ]
