"""Caretaker: choose which patient-visible memories are included in quiz mode."""

from __future__ import annotations

from typing import List, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from database import get_db
from routers.memory_personal import (
    _serialize,
    append_eligible_generic_memory_dicts,
)
from sqlalchemy import or_

router = APIRouter(prefix="/memory/caretaker", tags=["Caretaker Quiz Pool"])


def _patient_for_caretaker(
    db: Session, patient_id: int, caretaker_email: str
) -> models.Patient:
    p = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == patient_id,
            models.Patient.caretaker_email == caretaker_email,
        )
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    return p


def _quiz_candidate_dicts(db: Session, patient_id: int, caretaker_email: str) -> List[dict]:
    """Personal + eligible generic memories for this patient (caretaker-scoped)."""
    _patient_for_caretaker(db, patient_id, caretaker_email)

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
    memories = q.order_by(models.MemoryItem.created_at.desc()).all()
    out: List[dict] = [_serialize(m) for m in memories]
    append_eligible_generic_memory_dicts(db, patient_id, out)
    return out


def _memory_item_schema_from_dict(c: dict) -> schemas.MemoryItemSchema:
    d = dict(c)
    t = (d.get("title") or "").strip()
    d["title"] = t or "Untitled"
    if not d.get("category"):
        d["category"] = "image"
    return schemas.MemoryItemSchema(**d)


@router.get(
    "/patient/{patient_id}/quiz-pool",
    response_model=schemas.QuizPoolStateResponse,
)
def get_quiz_pool(
    patient_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    _patient_for_caretaker(db, patient_id, caretaker_email)
    candidates = _quiz_candidate_dicts(db, patient_id, caretaker_email)
    allowed: Set[int] = {int(c["id"]) for c in candidates}

    rows = (
        db.query(models.PatientQuizMemoryItem.memory_item_id)
        .filter(models.PatientQuizMemoryItem.patient_id == patient_id)
        .all()
    )
    pool_ids = [int(r[0]) for r in rows if int(r[0]) in allowed]
    return schemas.QuizPoolStateResponse(
        pool_memory_ids=sorted(set(pool_ids)),
        candidates=[_memory_item_schema_from_dict(c) for c in candidates],
    )


@router.put(
    "/patient/{patient_id}/quiz-pool",
    response_model=schemas.QuizPoolPutResponse,
)
def put_quiz_pool(
    patient_id: int,
    body: schemas.QuizPoolPutRequest,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    _patient_for_caretaker(db, patient_id, caretaker_email)
    candidates = _quiz_candidate_dicts(db, patient_id, caretaker_email)
    allowed: Set[int] = {int(c["id"]) for c in candidates}

    seen: Set[int] = set()
    clean_ids: List[int] = []
    for mid in body.memory_ids:
        i = int(mid)
        if i in seen:
            continue
        seen.add(i)
        if i not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Memory id {i} is not available for this patient",
            )
        clean_ids.append(i)

    db.query(models.PatientQuizMemoryItem).filter(
        models.PatientQuizMemoryItem.patient_id == patient_id,
    ).delete(synchronize_session=False)

    for mid in clean_ids:
        db.add(
            models.PatientQuizMemoryItem(
                patient_id=patient_id,
                memory_item_id=mid,
            )
        )
    db.commit()
    return schemas.QuizPoolPutResponse(status="ok", count=len(clean_ids))
