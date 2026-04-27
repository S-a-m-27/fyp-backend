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
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

import models
import schemas
from database import get_db

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
    rows = (
        db.query(models.Patient.id)
        .filter(models.Patient.caretaker_email == email)
        .all()
    )
    return [r[0] for r in rows]


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
    """Patients linked to a caretaker (used by the Patients screen)."""
    _get_caretaker_or_404(db, email)
    patients = (
        db.query(models.Patient)
        .filter(models.Patient.caretaker_email == email)
        .order_by(models.Patient.id.desc())
        .all()
    )
    return patients


@router.post("/sessions", status_code=201, response_model=schemas.RecentSession)
def log_session(
    payload: schemas.SessionCreate,
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
