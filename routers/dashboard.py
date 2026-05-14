"""
Caretaker Dashboard API.

Single-file router that powers the Caretaker dashboard screen:
  - Profile info (name, email, member-since year)
  - 4 stat cards   (total patients / memories / sessions this week / active today)
  - Recent sessions list
  - Patients list linked to a caretaker
  - Logging a new session

All endpoints scope data to a caretaker by `email` (passed as query param).
The `/overview` endpoint is the one the dashboard screen should call - it
returns everything in a single round-trip.
"""

from datetime import datetime, timedelta, date, time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

import models
import schemas
from database import get_db
from utils.caretaker_patient_access import (
    caretaker_can_access_patient,
    is_primary_caretaker_for_patient,
    patient_ids_accessible_to_caretaker,
)

router = APIRouter(
    prefix="/dashboard",
    tags=["Caretaker Dashboard"],
)


# --------------------------- helpers ---------------------------

def _get_caretaker_or_404(db: DBSession, email: str) -> models.Caretaker:
    caretaker = (
        db.query(models.Caretaker)
        .filter(models.Caretaker.email == email)
        .first()
    )
    if not caretaker:
        raise HTTPException(status_code=404, detail="Caretaker not found")
    return caretaker


def _patient_ids_for_caretaker(db: DBSession, email: str) -> List[int]:
    return patient_ids_accessible_to_caretaker(db, email)


def _build_stats(db: DBSession, email: str) -> schemas.DashboardStats:
    patient_ids = _patient_ids_for_caretaker(db, email)

    if not patient_ids:
        return schemas.DashboardStats(
            totalPatients=0,
            totalMemories=0,
            sessionsThisWeek=0,
            activeToday=0,
        )

    total_patients = len(patient_ids)

    total_memories = (
        db.query(func.count(models.MemoryItem.id))
        .filter(models.MemoryItem.patient_id.in_(patient_ids))
        .scalar()
        or 0
    )

    week_ago = datetime.utcnow() - timedelta(days=7)
    sessions_this_week = (
        db.query(func.count(models.Session.session_id))
        .filter(
            models.Session.patient_id.in_(patient_ids),
            models.Session.started_at >= week_ago,
        )
        .scalar()
        or 0
    )

    start_of_day = datetime.combine(date.today(), time.min)
    end_of_day = datetime.combine(date.today(), time.max)
    active_today = (
        db.query(func.count(func.distinct(models.Session.patient_id)))
        .filter(
            models.Session.patient_id.in_(patient_ids),
            models.Session.started_at >= start_of_day,
            models.Session.started_at <= end_of_day,
        )
        .scalar()
        or 0
    )

    return schemas.DashboardStats(
        totalPatients=total_patients,
        totalMemories=total_memories,
        sessionsThisWeek=sessions_this_week,
        activeToday=active_today,
    )


def _build_profile(caretaker: models.Caretaker) -> schemas.CaretakerProfile:
    full_name = f"{caretaker.firstName or ''} {caretaker.lastName or ''}".strip() or "Caretaker"
    member_since = (
        caretaker.created_at.year
        if caretaker.created_at
        else datetime.utcnow().year
    )
    return schemas.CaretakerProfile(
        userName=full_name,
        userEmail=caretaker.email,
        memberSince=str(member_since),
    )


def _build_recent_sessions(
    db: DBSession,
    email: str,
    limit: int,
) -> List[schemas.RecentSession]:
    patient_ids = _patient_ids_for_caretaker(db, email)
    if not patient_ids:
        return []

    rows = (
        db.query(models.Session)
        .filter(models.Session.patient_id.in_(patient_ids))
        .order_by(models.Session.started_at.desc())
        .limit(limit)
        .all()
    )

    return [
        schemas.RecentSession(
            id=s.session_id,
            patientId=s.patient_id,
            patientName=s.patient_name or "",
            mode=s.mode or "Session",
            minutes=int(s.duration_minutes or 0),
            startedAt=s.started_at or datetime.utcnow(),
        )
        for s in rows
    ]


# --------------------------- endpoints ---------------------------

@router.get("/overview", response_model=schemas.DashboardOverview)
def get_dashboard_overview(
    email: str = Query(..., description="Logged-in caretaker's email"),
    recent_limit: int = Query(5, ge=1, le=20),
    db: DBSession = Depends(get_db),
):
    """One-shot endpoint for the dashboard screen."""
    caretaker = _get_caretaker_or_404(db, email)
    return schemas.DashboardOverview(
        profile=_build_profile(caretaker),
        stats=_build_stats(db, email),
        recentSessions=_build_recent_sessions(db, email, recent_limit),
    )


@router.get("/profile", response_model=schemas.CaretakerProfile)
def get_caretaker_profile(
    email: str = Query(...),
    db: DBSession = Depends(get_db),
):
    caretaker = _get_caretaker_or_404(db, email)
    return _build_profile(caretaker)


@router.get("/stats", response_model=schemas.DashboardStats)
def get_dashboard_stats(
    email: str = Query(...),
    db: DBSession = Depends(get_db),
):
    _get_caretaker_or_404(db, email)
    return _build_stats(db, email)


@router.get("/recent-sessions", response_model=List[schemas.RecentSession])
def get_recent_sessions(
    email: str = Query(...),
    limit: int = Query(5, ge=1, le=50),
    db: DBSession = Depends(get_db),
):
    _get_caretaker_or_404(db, email)
    return _build_recent_sessions(db, email, limit)


@router.get("/patients", response_model=List[schemas.PatientSchema])
def get_caretaker_patients(
    email: str = Query(...),
    db: DBSession = Depends(get_db),
):
    """Patients the caretaker may manage: owned + shared by other caretakers."""
    _get_caretaker_or_404(db, email)
    el = email.strip().casefold()

    owned = (
        db.query(models.Patient)
        .filter(func.lower(models.Patient.caretaker_email) == el)
        .order_by(models.Patient.id.desc())
        .all()
    )
    owned_ids = {p.id for p in owned}

    delegated_ids = [
        int(r[0])
        for r in (
            db.query(models.PatientCaretakerShare.patient_id)
            .filter(func.lower(models.PatientCaretakerShare.delegate_email) == el)
            .all()
        )
        if int(r[0]) not in owned_ids
    ]
    delegated: List[models.Patient] = []
    if delegated_ids:
        delegated = (
            db.query(models.Patient)
            .filter(models.Patient.id.in_(delegated_ids))
            .order_by(models.Patient.id.desc())
            .all()
        )

    out: List[schemas.PatientSchema] = []
    for p in owned:
        base = schemas.PatientSchema.model_validate(p)
        out.append(base.model_copy(update={"delegate_access": False}))
    for p in delegated:
        base = schemas.PatientSchema.model_validate(p)
        out.append(base.model_copy(update={"delegate_access": True}))
    return out


@router.get("/patients/{patient_id}", response_model=schemas.PatientSchema)
def get_caretaker_patient(
    patient_id: int,
    email: str = Query(...),
    db: DBSession = Depends(get_db),
):
    """One patient record if the caretaker may access them (primary or delegate)."""
    _get_caretaker_or_404(db, email)
    if not caretaker_can_access_patient(db, email, patient_id):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to view this patient",
        )
    p = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    primary = (p.caretaker_email or "").strip().casefold()
    is_delegate = primary != email.strip().casefold()
    base = schemas.PatientSchema.model_validate(p)
    return base.model_copy(update={"delegate_access": is_delegate})


@router.patch("/patients/{patient_id}", response_model=schemas.PatientSchema)
def update_caretaker_patient(
    patient_id: int,
    payload: schemas.PatientUpdate,
    email: str = Query(...),
    db: DBSession = Depends(get_db),
):
    """Update patient demographics / notes for anyone who can access this patient."""
    _get_caretaker_or_404(db, email)
    if not caretaker_can_access_patient(db, email, patient_id):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to update this patient",
        )
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    data = payload.model_dump(exclude_unset=True)
    if "login_id" in data:
        lid = (data.pop("login_id") or "").strip() or None
        if lid:
            clash = (
                db.query(models.Patient.id)
                .filter(
                    models.Patient.login_id == lid,
                    models.Patient.id != patient_id,
                )
                .first()
            )
            if clash:
                raise HTTPException(
                    status_code=400,
                    detail="That reference ID is already used by another patient",
                )
        patient.login_id = lid
    for key, val in data.items():
        if not hasattr(patient, key):
            continue
        setattr(patient, key, val)

    db.commit()
    db.refresh(patient)
    primary = (patient.caretaker_email or "").strip().casefold()
    is_delegate = primary != email.strip().casefold()
    base = schemas.PatientSchema.model_validate(patient)
    return base.model_copy(update={"delegate_access": is_delegate})


@router.get("/patient-delegations", response_model=List[schemas.PatientDelegateInfo])
def list_patient_delegations(
    email: str = Query(..., description="Primary caretaker email"),
    db: DBSession = Depends(get_db),
):
    """Co-caretaker assignments for all patients owned by this caretaker."""
    _get_caretaker_or_404(db, email)
    el = email.strip().casefold()
    rows = (
        db.query(models.PatientCaretakerShare)
        .join(
            models.Patient,
            models.Patient.id == models.PatientCaretakerShare.patient_id,
        )
        .filter(func.lower(models.Patient.caretaker_email) == el)
        .order_by(models.PatientCaretakerShare.id.desc())
        .all()
    )
    return [
        schemas.PatientDelegateInfo(
            share_id=r.id,
            patient_id=r.patient_id,
            delegate_email=r.delegate_email,
        )
        for r in rows
    ]


@router.post("/patient-delegations", response_model=schemas.PatientDelegateInfo)
def add_patient_delegation(
    payload: schemas.AssignPatientDelegatePayload,
    email: str = Query(..., description="Primary caretaker email"),
    db: DBSession = Depends(get_db),
):
    """Grant another registered caretaker the same patient access as you (memories, quiz, library purchases)."""
    _get_caretaker_or_404(db, email)
    if not is_primary_caretaker_for_patient(db, email, payload.patient_id):
        raise HTTPException(
            status_code=403,
            detail="Only the patient's primary caretaker can assign co-caretakers",
        )
    raw = (payload.delegate_email or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="delegate_email required")
    del_lower = raw.casefold()
    if del_lower == email.strip().casefold():
        raise HTTPException(status_code=400, detail="Cannot assign yourself")
    other = (
        db.query(models.Caretaker)
        .filter(func.lower(models.Caretaker.email) == del_lower)
        .first()
    )
    if not other:
        raise HTTPException(
            status_code=404,
            detail="No caretaker account exists with that email",
        )
    canonical = (other.email or "").strip()
    exists = (
        db.query(models.PatientCaretakerShare.id)
        .filter(
            models.PatientCaretakerShare.patient_id == payload.patient_id,
            func.lower(models.PatientCaretakerShare.delegate_email) == del_lower,
        )
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=400,
            detail="That caretaker is already assigned to this patient",
        )
    row = models.PatientCaretakerShare(
        patient_id=payload.patient_id,
        delegate_email=canonical,
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not save assignment (duplicate?)",
        )
    return schemas.PatientDelegateInfo(
        share_id=row.id,
        patient_id=row.patient_id,
        delegate_email=row.delegate_email,
    )


@router.delete("/patient-delegations/{share_id}", status_code=204)
def remove_patient_delegation(
    share_id: int,
    email: str = Query(..., description="Primary caretaker email"),
    db: DBSession = Depends(get_db),
):
    _get_caretaker_or_404(db, email)
    el = email.strip().casefold()
    row = (
        db.query(models.PatientCaretakerShare)
        .join(
            models.Patient,
            models.Patient.id == models.PatientCaretakerShare.patient_id,
        )
        .filter(
            models.PatientCaretakerShare.id == share_id,
            func.lower(models.Patient.caretaker_email) == el,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/sessions", status_code=201, response_model=schemas.RecentSession)
def log_session(
    payload: schemas.SessionCreate,
    caretaker_email: Optional[str] = Query(
        None,
        description="If set, caller must have access to this patient (primary or co-caretaker)",
    ),
    db: DBSession = Depends(get_db),
):
    """Log a new session for a patient (called when a quiz/training ends)."""
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == payload.patient_id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if caretaker_email and not caretaker_can_access_patient(
        db, caretaker_email, payload.patient_id
    ):
        raise HTTPException(status_code=404, detail="Patient not found")

    new_session = models.Session(
        patient_id=patient.id,
        patient_name=patient.name,
        mode=payload.mode,
        duration_minutes=payload.duration_minutes,
        started_at=datetime.utcnow(),
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return schemas.RecentSession(
        id=new_session.session_id,
        patientId=new_session.patient_id,
        patientName=new_session.patient_name or "",
        mode=new_session.mode or "Session",
        minutes=int(new_session.duration_minutes or 0),
        startedAt=new_session.started_at,
    )
