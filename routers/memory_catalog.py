"""Browse generic memory library by topic → bundle → images; rate & purchase."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
import schemas
from data.generic_memory_library import (
    DEFAULT_GENERIC_BUNDLE_SLUG,
    GENERIC_TOPIC_CATALOG,
)
from database import get_db

router = APIRouter(prefix="/memory/catalog", tags=["Memory catalog"])

_TOPIC_SLUGS = {t["slug"] for t in GENERIC_TOPIC_CATALOG}


def _caretaker_or_404(db: Session, email: str) -> None:
    c = (
        db.query(models.Caretaker.id)
        .filter(models.Caretaker.email == email)
        .first()
    )
    if not c:
        raise HTTPException(status_code=404, detail="Caretaker not found")


def _patient_auth(
    db: Session, patient_id: int, passcode: Optional[str], qr_token: Optional[str]
) -> models.Patient:
    p = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    qt = (qr_token or "").strip()
    pt = (passcode or "").strip()
    ok = (qt and (p.qr_token or "").strip() == qt) or (pt and p.passcode == pt)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid patient credentials")
    return p


def _bundle_label(slug: str) -> str:
    if not slug:
        return "Bundle"
    return slug.replace("_", " ").strip().title()


@router.get("/topics", response_model=List[schemas.GenericTopicInfo])
def catalog_topics():
    """Same eight topic cards as /memory/generic/topics (for RN catalog home)."""
    return [
        schemas.GenericTopicInfo(
            slug=t["slug"],
            label=t["label"],
            blurb=t["blurb"],
            approx_count=10,
            default_bundle_slug=DEFAULT_GENERIC_BUNDLE_SLUG,
        )
        for t in GENERIC_TOPIC_CATALOG
    ]


@router.get("/topics/{topic_slug}/bundles", response_model=List[schemas.CatalogBundleDetail])
def list_bundles_for_topic(
    topic_slug: str,
    email: Optional[str] = Query(None, description="Caretaker email (browse all bundles)"),
    patient_id: Optional[int] = Query(
        None, description="With passcode or qr_token: patient-visible bundles only",
    ),
    passcode: Optional[str] = Query(None),
    qr_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if topic_slug not in _TOPIC_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown topic")

    rows = (
        db.query(
            models.MemoryItem.library_collection_slug,
            func.count(models.MemoryItem.id),
        )
        .filter(
            models.MemoryItem.library_type == "generic",
            models.MemoryItem.library_topic == topic_slug,
            models.MemoryItem.library_collection_slug.isnot(None),
        )
        .group_by(models.MemoryItem.library_collection_slug)
        .order_by(models.MemoryItem.library_collection_slug)
        .all()
    )

    purchase_pairs: set[tuple[str, str]] = set()
    patient_mode = bool(patient_id and (passcode or qr_token))
    if patient_mode:
        p = _patient_auth(db, patient_id, passcode, qr_token)
        prs = (
            db.query(
                models.CaretakerBundlePurchase.library_topic,
                models.CaretakerBundlePurchase.library_collection_slug,
            )
            .filter(
                models.CaretakerBundlePurchase.patient_id == p.id,
                models.CaretakerBundlePurchase.caretaker_email == p.caretaker_email,
            )
            .all()
        )
        purchase_pairs = {(a, b) for a, b in prs if a and b is not None}
    elif email:
        _caretaker_or_404(db, email)
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide caretaker email or patient_id with passcode/qr_token",
        )

    out: List[schemas.CatalogBundleDetail] = []
    for coll, cnt in rows:
        if not coll:
            continue
        avg, rcount = (
            db.query(
                func.coalesce(func.avg(models.BundleRating.stars), 0),
                func.count(models.BundleRating.id),
            )
            .filter(
                models.BundleRating.library_topic == topic_slug,
                models.BundleRating.library_collection_slug == coll,
            )
            .first()
        )
        avg_f = float(avg or 0)
        n_ratings = int(rcount or 0)
        if n_ratings == 0:
            avg_f = 0.0

        tr_avg, tr_cnt = (
            db.query(
                func.coalesce(func.avg(models.MemoryImageRating.stars), 0),
                func.count(models.MemoryImageRating.id),
            )
            .join(
                models.MemoryItem,
                models.MemoryItem.id == models.MemoryImageRating.memory_item_id,
            )
            .filter(
                models.MemoryItem.library_type == "generic",
                models.MemoryItem.library_topic == topic_slug,
                models.MemoryItem.library_collection_slug == coll,
            )
            .first()
        )
        tr_n = int(tr_cnt or 0)
        if tr_n > 0:
            avg_f = float(tr_avg or 0)
            n_ratings = tr_n

        is_purchased = False
        if email and patient_id:
            hit = (
                db.query(models.CaretakerBundlePurchase.id)
                .filter(
                    models.CaretakerBundlePurchase.caretaker_email == email,
                    models.CaretakerBundlePurchase.patient_id == patient_id,
                    models.CaretakerBundlePurchase.library_topic == topic_slug,
                    models.CaretakerBundlePurchase.library_collection_slug == coll,
                )
                .first()
            )
            is_purchased = bool(hit)
        elif patient_mode:
            is_purchased = (topic_slug, coll) in purchase_pairs or (
                coll == DEFAULT_GENERIC_BUNDLE_SLUG
            )

        if patient_mode:
            visible = (coll == DEFAULT_GENERIC_BUNDLE_SLUG) or (
                (topic_slug, coll) in purchase_pairs
            )
            if not visible:
                continue

        cover_row = (
            db.query(models.MemoryItem.file_path)
            .filter(
                models.MemoryItem.library_type == "generic",
                models.MemoryItem.library_topic == topic_slug,
                models.MemoryItem.library_collection_slug == coll,
            )
            .order_by(models.MemoryItem.id)
            .first()
        )
        cover_path = cover_row[0] if cover_row else None

        out.append(
            schemas.CatalogBundleDetail(
                topic_slug=topic_slug,
                bundle_slug=coll,
                display_name=_bundle_label(coll),
                image_count=int(cnt or 0),
                average_rating=round(avg_f, 2),
                rating_count=n_ratings,
                is_purchased=is_purchased,
                cover_file_path=cover_path,
            ),
        )
    return out


@router.get(
    "/topics/{topic_slug}/bundles/{bundle_slug}/memories",
    response_model=List[schemas.MemoryGalleryItem],
)
def list_memories_in_bundle(
    topic_slug: str,
    bundle_slug: str,
    patient_id: Optional[int] = Query(None),
    passcode: Optional[str] = Query(None),
    qr_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    if topic_slug not in _TOPIC_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown topic")

    if patient_id is not None and not (passcode or qr_token):
        raise HTTPException(
            status_code=400,
            detail="patient_id requires passcode or qr_token",
        )

    if patient_id and (passcode or qr_token):
        p = _patient_auth(db, patient_id, passcode, qr_token)
        allowed = bundle_slug == DEFAULT_GENERIC_BUNDLE_SLUG or (
            db.query(models.CaretakerBundlePurchase.id)
            .filter(
                models.CaretakerBundlePurchase.patient_id == p.id,
                models.CaretakerBundlePurchase.caretaker_email == p.caretaker_email,
                models.CaretakerBundlePurchase.library_topic == topic_slug,
                models.CaretakerBundlePurchase.library_collection_slug == bundle_slug,
            )
            .first()
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Bundle not unlocked for this patient")

    rows = (
        db.query(models.MemoryItem)
        .filter(
            models.MemoryItem.library_type == "generic",
            models.MemoryItem.library_topic == topic_slug,
            models.MemoryItem.library_collection_slug == bundle_slug,
        )
        .order_by(models.MemoryItem.id)
        .all()
    )
    return [
        schemas.MemoryGalleryItem(
            id=m.id,
            title=m.title,
            location=m.location,
            description=m.description,
            file_path=m.file_path,
            category=m.category,
            library_type=m.library_type,
            library_topic=getattr(m, "library_topic", None),
            library_collection_slug=getattr(m, "library_collection_slug", None),
            memory_type=m.memory_type,
            patient_id=m.patient_id,
            related_person_name=getattr(m, "related_person_name", None),
            related_person_relation=getattr(m, "related_person_relation", None),
        )
        for m in rows
    ]


@router.post("/bundles/rate", response_model=schemas.BundleRateResponse)
def rate_bundle(payload: schemas.BundleRatePayload, db: Session = Depends(get_db)):
    if payload.topic_slug not in _TOPIC_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown topic")
    if not payload.bundle_slug:
        raise HTTPException(status_code=400, detail="bundle_slug required")
    if payload.stars < 1 or payload.stars > 5:
        raise HTTPException(status_code=400, detail="stars must be 1–5")

    p = _patient_auth(db, payload.patient_id, payload.passcode, payload.qr_token)

    unlocked = payload.bundle_slug == DEFAULT_GENERIC_BUNDLE_SLUG or (
        db.query(models.CaretakerBundlePurchase.id)
        .filter(
            models.CaretakerBundlePurchase.patient_id == p.id,
            models.CaretakerBundlePurchase.caretaker_email == p.caretaker_email,
            models.CaretakerBundlePurchase.library_topic == payload.topic_slug,
            models.CaretakerBundlePurchase.library_collection_slug == payload.bundle_slug,
        )
        .first()
    )
    if not unlocked:
        raise HTTPException(
            status_code=403,
            detail="You can only rate bundles you have access to",
        )

    existing = (
        db.query(models.BundleRating)
        .filter(
            models.BundleRating.patient_id == p.id,
            models.BundleRating.library_topic == payload.topic_slug,
            models.BundleRating.library_collection_slug == payload.bundle_slug,
        )
        .first()
    )
    if existing:
        existing.stars = payload.stars
    else:
        db.add(
            models.BundleRating(
                patient_id=p.id,
                library_topic=payload.topic_slug,
                library_collection_slug=payload.bundle_slug,
                stars=payload.stars,
            )
        )
    db.commit()

    avg, rcount = (
        db.query(
            func.coalesce(func.avg(models.BundleRating.stars), 0),
            func.count(models.BundleRating.id),
        )
        .filter(
            models.BundleRating.library_topic == payload.topic_slug,
            models.BundleRating.library_collection_slug == payload.bundle_slug,
        )
        .first()
    )
    return schemas.BundleRateResponse(
        status="ok",
        average_rating=round(float(avg or 0), 2),
        rating_count=int(rcount or 0),
    )


@router.post("/bundles/purchase", response_model=schemas.BundlePurchaseResponse)
def purchase_bundle(
    payload: schemas.BundlePurchasePayload,
    db: Session = Depends(get_db),
):
    _caretaker_or_404(db, payload.caretaker_email)
    if payload.topic_slug not in _TOPIC_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown topic")
    if not payload.bundle_slug:
        raise HTTPException(status_code=400, detail="bundle_slug required")
    if payload.bundle_slug == DEFAULT_GENERIC_BUNDLE_SLUG:
        raise HTTPException(
            status_code=400,
            detail="The starter (included) bundle is already available to all patients.",
        )

    patient = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == payload.patient_id,
            models.Patient.caretaker_email == payload.caretaker_email,
        )
        .first()
    )
    if not patient:
        raise HTTPException(
            status_code=400,
            detail="Patient not found for this caretaker",
        )

    row = models.CaretakerBundlePurchase(
        caretaker_email=payload.caretaker_email,
        patient_id=payload.patient_id,
        library_topic=payload.topic_slug,
        library_collection_slug=payload.bundle_slug,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return schemas.BundlePurchaseResponse(status="ok", already_owned=True)
    return schemas.BundlePurchaseResponse(status="ok", already_owned=False)
