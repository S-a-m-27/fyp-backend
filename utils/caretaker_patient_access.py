"""Who may act on a patient: primary caretaker (``Patient.caretaker_email``) or delegated co-caretaker."""

from __future__ import annotations

from typing import List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

import models


def caretaker_can_access_patient(
    db: Session, caretaker_email: str, patient_id: int
) -> bool:
    e = (caretaker_email or "").strip()
    if not e:
        return False
    el = e.casefold()
    p = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not p:
        return False
    if (p.caretaker_email or "").strip().casefold() == el:
        return True
    link = (
        db.query(models.PatientCaretakerShare.id)
        .filter(
            models.PatientCaretakerShare.patient_id == patient_id,
            func.lower(models.PatientCaretakerShare.delegate_email) == el,
        )
        .first()
    )
    return link is not None


def get_patient_for_caretaker(
    db: Session, caretaker_email: str, patient_id: int
) -> Optional[models.Patient]:
    if not caretaker_can_access_patient(db, caretaker_email, patient_id):
        return None
    return db.query(models.Patient).filter(models.Patient.id == patient_id).first()


def require_patient_for_caretaker(
    db: Session, caretaker_email: str, patient_id: int
) -> models.Patient:
    p = get_patient_for_caretaker(db, caretaker_email, patient_id)
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    return p


def patient_ids_accessible_to_caretaker(db: Session, caretaker_email: str) -> List[int]:
    """Primary-owned patients plus any patient shared with this caretaker."""
    e = (caretaker_email or "").strip()
    if not e:
        return []
    owned = (
        db.query(models.Patient.id)
        .filter(func.lower(models.Patient.caretaker_email) == e.casefold())
        .all()
    )
    ids: Set[int] = {int(r[0]) for r in owned}
    delegated = (
        db.query(models.PatientCaretakerShare.patient_id)
        .filter(func.lower(models.PatientCaretakerShare.delegate_email) == e.casefold())
        .all()
    )
    for r in delegated:
        ids.add(int(r[0]))
    return list(ids)


def is_primary_caretaker_for_patient(
    db: Session, caretaker_email: str, patient_id: int
) -> bool:
    p = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not p:
        return False
    return (p.caretaker_email or "").strip().casefold() == (
        caretaker_email or ""
    ).strip().casefold()
