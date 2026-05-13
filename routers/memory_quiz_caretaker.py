"""Caretaker: quiz pool + caretaker-defined fixed-length quizzes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from database import get_db
from utils.caretaker_patient_access import require_patient_for_caretaker
from routers.memory import (
    _purchased_only_generic_memories_query,
    _quiz_choice_label,
)
from routers.memory_personal import (
    _serialize,
    append_eligible_generic_memory_dicts,
)

router = APIRouter(prefix="/memory/caretaker", tags=["Caretaker Quiz Pool"])


def _patient_for_caretaker(
    db: Session, patient_id: int, caretaker_email: str
) -> models.Patient:
    return require_patient_for_caretaker(db, caretaker_email, patient_id)


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
    if not t:
        t = (d.get("related_person_name") or "").strip()
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


def _personal_memory_ids_for_patient(db: Session, patient_id: int) -> Set[int]:
    shared_ids_subq = (
        db.query(models.memory_patient_access.c.memory_id)
        .filter(models.memory_patient_access.c.patient_id == patient_id)
        .subquery()
    )
    rows = (
        db.query(models.MemoryItem.id)
        .filter(
            models.MemoryItem.library_type == "personal",
            or_(
                models.MemoryItem.patient_id == patient_id,
                models.MemoryItem.id.in_(shared_ids_subq),
            ),
        )
        .all()
    )
    return {int(r[0]) for r in rows}


def _allowed_memory_ids_for_defined_quiz(db: Session, patient_id: int) -> Set[int]:
    """Purchased generic images only + this patient's personal (and shared) memories."""
    gids = {
        int(m.id)
        for m in _purchased_only_generic_memories_query(db, patient_id)
        .order_by(models.MemoryItem.id)
        .all()
    }
    return gids | _personal_memory_ids_for_patient(db, patient_id)


def _pick_to_schema(m: models.MemoryItem) -> schemas.DefinedQuizMemoryPick:
    return schemas.DefinedQuizMemoryPick(
        id=int(m.id),
        title=_quiz_choice_label(m),
        file_path=(m.file_path or "").replace("\\", "/"),
        library_type=str(m.library_type or "personal"),
        library_topic=getattr(m, "library_topic", None),
        library_collection_slug=getattr(m, "library_collection_slug", None),
    )


@router.get(
    "/patient/{patient_id}/defined-quiz/editor",
    response_model=schemas.DefinedQuizEditorResponse,
)
def get_defined_quiz_editor(
    patient_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    _patient_for_caretaker(db, patient_id, caretaker_email)

    gen_rows = (
        _purchased_only_generic_memories_query(db, patient_id)
        .order_by(
            models.MemoryItem.library_topic,
            models.MemoryItem.library_collection_slug,
            models.MemoryItem.id,
        )
        .all()
    )
    generic_picks = [_pick_to_schema(m) for m in gen_rows]

    shared_ids_subq = (
        db.query(models.memory_patient_access.c.memory_id)
        .filter(models.memory_patient_access.c.patient_id == patient_id)
        .subquery()
    )
    pers_rows = (
        db.query(models.MemoryItem)
        .filter(
            models.MemoryItem.library_type == "personal",
            or_(
                models.MemoryItem.patient_id == patient_id,
                models.MemoryItem.id.in_(shared_ids_subq),
            ),
        )
        .order_by(models.MemoryItem.created_at.desc())
        .all()
    )
    personal_picks = [_pick_to_schema(m) for m in pers_rows]

    dq = (
        db.query(models.CaretakerDefinedQuiz)
        .filter(models.CaretakerDefinedQuiz.patient_id == patient_id)
        .first()
    )
    by_slot: Dict[int, models.CaretakerDefinedQuizQuestion] = {}
    if dq:
        for qq in (
            db.query(models.CaretakerDefinedQuizQuestion)
            .filter(models.CaretakerDefinedQuizQuestion.quiz_id == dq.id)
            .all()
        ):
            by_slot[int(qq.slot)] = qq

    slots_out: List[schemas.DefinedQuizSlotState] = []
    n = models.DEFINED_QUIZ_QUESTION_SLOTS
    for s in range(1, n + 1):
        qq = by_slot.get(s)
        if not qq:
            slots_out.append(schemas.DefinedQuizSlotState(slot=s))
            continue
        mem = (
            db.query(models.MemoryItem)
            .filter(models.MemoryItem.id == qq.memory_item_id)
            .first()
        )
        if not mem:
            slots_out.append(schemas.DefinedQuizSlotState(slot=s))
            continue
        ct = _quiz_choice_label(mem)
        four_opts = None
        cix = None
        if qq.mc_options_json:
            try:
                parsed = json.loads(qq.mc_options_json)
                if isinstance(parsed, list) and len(parsed) == 4:
                    four_opts = [str(x or "").strip() for x in parsed]
                    cix = int(qq.correct_option_index) if qq.correct_option_index is not None else None
            except (json.JSONDecodeError, TypeError, ValueError):
                four_opts = None
        slots_out.append(
            schemas.DefinedQuizSlotState(
                slot=s,
                memory_item_id=int(mem.id),
                correct_title=ct,
                file_path=(mem.file_path or "").replace("\\", "/"),
                library_type=str(mem.library_type or ""),
                library_topic=getattr(mem, "library_topic", None),
                library_collection_slug=getattr(mem, "library_collection_slug", None),
                wrong_option_1=qq.wrong_option_1,
                wrong_option_2=qq.wrong_option_2,
                wrong_option_3=qq.wrong_option_3,
                four_options=four_opts,
                correct_option_index=cix,
            ),
        )

    has_quiz = bool(dq and len(by_slot) == models.DEFINED_QUIZ_QUESTION_SLOTS)
    return schemas.DefinedQuizEditorResponse(
        has_quiz=has_quiz,
        slots=slots_out,
        generic_purchased_memories=generic_picks,
        personal_memories=personal_picks,
    )


@router.get(
    "/patient/{patient_id}/quiz-person-defaults",
    response_model=schemas.QuizPersonDefaultsResponse,
)
def get_quiz_person_defaults(
    patient_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    _patient_for_caretaker(db, patient_id, caretaker_email)
    shared_ids_subq = (
        db.query(models.memory_patient_access.c.memory_id)
        .filter(models.memory_patient_access.c.patient_id == patient_id)
        .subquery()
    )
    mem = (
        db.query(models.MemoryItem)
        .filter(
            models.MemoryItem.library_type == "personal",
            or_(
                models.MemoryItem.patient_id == patient_id,
                models.MemoryItem.id.in_(shared_ids_subq),
            ),
            models.MemoryItem.related_person_name.isnot(None),
            models.MemoryItem.related_person_name != "",
        )
        .order_by(models.MemoryItem.created_at.desc())
        .first()
    )
    if not mem or not (mem.related_person_name or "").strip():
        raise HTTPException(
            status_code=404,
            detail="Add at least one personal memory for this patient first so we know who is in the photos.",
        )
    return schemas.QuizPersonDefaultsResponse(
        related_person_name=(mem.related_person_name or "").strip(),
        related_person_relation=(mem.related_person_relation or "").strip() or None,
    )


@router.put(
    "/patient/{patient_id}/defined-quiz",
    response_model=schemas.DefinedQuizPutResponse,
)
def put_defined_quiz(
    patient_id: int,
    body: schemas.DefinedQuizPutRequest,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    patient_row = _patient_for_caretaker(db, patient_id, caretaker_email)
    primary_ce = (patient_row.caretaker_email or "").strip()
    allowed = _allowed_memory_ids_for_defined_quiz(db, patient_id)

    n = models.DEFINED_QUIZ_QUESTION_SLOTS
    if len(body.questions) != n:
        raise HTTPException(
            status_code=400,
            detail=f"Exactly {n} questions are required",
        )
    seen_slots: Set[int] = set()
    seen_memory: Set[int] = set()
    for q in body.questions:
        slot = int(q.slot)
        if slot < 1 or slot > n:
            raise HTTPException(
                status_code=400,
                detail=f"slot must be 1..{n}",
            )
        if slot in seen_slots:
            raise HTTPException(status_code=400, detail=f"Duplicate slot {slot}")
        seen_slots.add(slot)
        mid = int(q.memory_item_id)
        if mid in seen_memory:
            raise HTTPException(
                status_code=400,
                detail="Each question must use a different memory image",
            )
        seen_memory.add(mid)
        if mid not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Memory {mid} is not allowed (use purchased generic or personal only)",
            )
        mem = db.query(models.MemoryItem).filter(models.MemoryItem.id == mid).first()
        if not mem:
            raise HTTPException(status_code=400, detail=f"Memory {mid} not found")

        use_four = (
            q.four_options is not None
            and len(q.four_options) == 4
            and q.correct_option_index is not None
        )
        if use_four:
            if (mem.library_type or "").lower() != "personal":
                raise HTTPException(
                    status_code=400,
                    detail="Four-option mode is only for personal memories",
                )
            fo = [(x or "").strip() for x in q.four_options]
            if any(not x for x in fo):
                raise HTTPException(status_code=400, detail="All four options must be non-empty")
            idx = int(q.correct_option_index)
            if idx < 0 or idx > 3:
                raise HTTPException(status_code=400, detail="correct_option_index must be 0..3")
            correct_t = fo[idx].casefold()
            if len({x.casefold() for x in fo}) < 4:
                raise HTTPException(
                    status_code=400,
                    detail="The four options must all be different",
                )
            if _quiz_choice_label(mem).casefold() != correct_t:
                raise HTTPException(
                    status_code=400,
                    detail="Correct option must match the quiz answer for this memory (person name for personal photos).",
                )
        else:
            w1 = (q.wrong_option_1 or "").strip()
            w2 = (q.wrong_option_2 or "").strip()
            w3 = (q.wrong_option_3 or "").strip()
            if not w1 or not w2 or not w3:
                raise HTTPException(
                    status_code=400,
                    detail="All three wrong options must be non-empty",
                )
            correct = _quiz_choice_label(mem).casefold()
            for label, w in (("wrong_option_1", w1), ("wrong_option_2", w2), ("wrong_option_3", w3)):
                if w.casefold() == correct:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{label} must differ from the correct answer",
                    )
            ws = {w1.casefold(), w2.casefold(), w3.casefold()}
            if len(ws) < 3:
                raise HTTPException(
                    status_code=400,
                    detail="Wrong options must be three different strings",
                )

    if seen_slots != set(range(1, n + 1)):
        raise HTTPException(
            status_code=400,
            detail=f"Provide all slots 1 through {n}",
        )

    dq = (
        db.query(models.CaretakerDefinedQuiz)
        .filter(models.CaretakerDefinedQuiz.patient_id == patient_id)
        .first()
    )
    if dq:
        db.query(models.CaretakerDefinedQuizQuestion).filter(
            models.CaretakerDefinedQuizQuestion.quiz_id == dq.id,
        ).delete(synchronize_session=False)
        dq.caretaker_email = primary_ce
    else:
        dq = models.CaretakerDefinedQuiz(
            patient_id=patient_id,
            caretaker_email=primary_ce,
        )
        db.add(dq)
        db.flush()

    for q in sorted(body.questions, key=lambda x: int(x.slot)):
        use_four = (
            q.four_options is not None
            and len(q.four_options) == 4
            and q.correct_option_index is not None
        )
        if use_four:
            fo = [(x or "").strip() for x in q.four_options]
            idx = int(q.correct_option_index)
            wrongs = [fo[i] for i in range(4) if i != idx]
            mcj = json.dumps(fo)
            cidx = idx
            w1, w2, w3 = wrongs[0], wrongs[1], wrongs[2]
        else:
            mcj = None
            cidx = None
            w1 = (q.wrong_option_1 or "").strip()
            w2 = (q.wrong_option_2 or "").strip()
            w3 = (q.wrong_option_3 or "").strip()

        db.add(
            models.CaretakerDefinedQuizQuestion(
                quiz_id=dq.id,
                slot=int(q.slot),
                memory_item_id=int(q.memory_item_id),
                wrong_option_1=w1,
                wrong_option_2=w2,
                wrong_option_3=w3,
                mc_options_json=mcj,
                correct_option_index=cidx,
            )
        )
    db.commit()
    return schemas.DefinedQuizPutResponse(
        status="ok",
        question_count=models.DEFINED_QUIZ_QUESTION_SLOTS,
    )


@router.delete("/patient/{patient_id}/defined-quiz")
def delete_defined_quiz(
    patient_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    _patient_for_caretaker(db, patient_id, caretaker_email)
    dq = (
        db.query(models.CaretakerDefinedQuiz)
        .filter(models.CaretakerDefinedQuiz.patient_id == patient_id)
        .first()
    )
    if dq:
        db.delete(dq)
        db.commit()
    return {"status": "ok"}


@router.get(
    "/patient-memory-flags",
    response_model=List[schemas.PatientMemoryFlagCaretakerItem],
)
def list_patient_memory_flags(
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    """Patient comfort/safety flags (e.g. distress or PTSD-related triggers during training)."""
    el = caretaker_email.strip().casefold()
    delegated_ids = (
        db.query(models.PatientCaretakerShare.patient_id)
        .filter(func.lower(models.PatientCaretakerShare.delegate_email) == el)
    )
    rows = (
        db.query(
            models.PatientFlaggedMemory,
            models.Patient.name,
            models.MemoryItem,
        )
        .join(models.Patient, models.Patient.id == models.PatientFlaggedMemory.patient_id)
        .join(
            models.MemoryItem,
            models.MemoryItem.id == models.PatientFlaggedMemory.memory_item_id,
        )
        .filter(
            or_(
                func.lower(models.Patient.caretaker_email) == el,
                models.Patient.id.in_(delegated_ids),
            ),
        )
        .order_by(models.PatientFlaggedMemory.created_at.desc())
        .all()
    )
    out: List[schemas.PatientMemoryFlagCaretakerItem] = []
    for flag, patient_name, mem in rows:
        out.append(
            schemas.PatientMemoryFlagCaretakerItem(
                flag_id=int(flag.id),
                patient_id=int(flag.patient_id),
                patient_name=(patient_name or "").strip() or "Patient",
                memory_item_id=int(mem.id),
                file_path=(mem.file_path or "").replace("\\", "/"),
                library_type=mem.library_type,
                memory_title=(mem.title or "").strip() or "Memory",
                related_person_name=getattr(mem, "related_person_name", None),
                patient_note=flag.patient_note,
                created_at=flag.created_at or datetime.utcnow(),
            )
        )
    return out


@router.delete("/patient-memory-flags/{flag_id}")
def delete_patient_memory_flag(
    flag_id: int,
    caretaker_email: str = Query(..., alias="email"),
    db: Session = Depends(get_db),
):
    """Remove a flag after clinical review; patient may see the memory in training again."""
    el = caretaker_email.strip().casefold()
    delegated_ids = (
        db.query(models.PatientCaretakerShare.patient_id)
        .filter(func.lower(models.PatientCaretakerShare.delegate_email) == el)
    )
    row = (
        db.query(models.PatientFlaggedMemory)
        .join(models.Patient, models.Patient.id == models.PatientFlaggedMemory.patient_id)
        .filter(
            models.PatientFlaggedMemory.id == flag_id,
            or_(
                func.lower(models.Patient.caretaker_email) == el,
                models.Patient.id.in_(delegated_ids),
            ),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Flag not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}
