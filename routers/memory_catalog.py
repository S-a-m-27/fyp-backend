"""Browse generic memory library by topic → bundle → images; rate & purchase."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
import schemas
from app_paths import STATIC_DIR
from data.generic_memory_library import (
    bundle_is_free_on_disk,
    discover_generic_topic_cards,
    load_bundle_manifest_dict,
    list_bundle_slugs_on_disk,
    parse_bundle_pricing,
    topic_exists_on_disk,
)
from database import get_db
from routers.library_relevance import (
    bundle_match_score,
    manifest_gender_penalty,
    parse_json_string_list,
    profession_haystack_tokens,
    topic_match_score,
)

router = APIRouter(prefix="/memory/catalog", tags=["Memory catalog"])

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def generic_topic_image_counts(db: Session) -> dict[str, int]:
    """Count generic ``MemoryItem`` rows per ``library_topic`` (for catalog UI)."""
    rows = (
        db.query(
            models.MemoryItem.library_topic,
            func.count(models.MemoryItem.id),
        )
        .filter(
            models.MemoryItem.library_type == "generic",
            models.MemoryItem.library_topic.isnot(None),
        )
        .group_by(models.MemoryItem.library_topic)
        .all()
    )
    return {str(r[0]): int(r[1]) for r in rows if r[0] is not None}


def _patient_for_caretaker_catalog(
    db: Session, caretaker_email: str, patient_id: int
) -> models.Patient:
    _caretaker_or_404(db, caretaker_email)
    p = (
        db.query(models.Patient)
        .filter(
            models.Patient.id == patient_id,
            models.Patient.caretaker_email == caretaker_email,
        )
        .first()
    )
    if not p:
        raise HTTPException(
            status_code=404,
            detail="Patient not found for this caretaker",
        )
    return p


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


def _count_disk_images_with_manifest(bundle_dir: Path, manifest: dict) -> int:
    n = 0
    if not bundle_dir.is_dir():
        return 0
    for fp in bundle_dir.iterdir():
        if not fp.is_file() or fp.suffix.lower() not in _IMAGE_EXTS:
            continue
        meta = manifest.get(fp.name)
        if not isinstance(meta, dict):
            continue
        title = str(meta.get("title") or "").strip()
        if title:
            n += 1
    return n


def _bundle_dir(topic_slug: str, bundle_slug: str) -> Path:
    return (STATIC_DIR / "memory" / "generic" / topic_slug / bundle_slug).resolve()


def _bundle_label(slug: str) -> str:
    if not slug:
        return "Bundle"
    return slug.replace("_", " ").strip().title()


@router.get("/topics", response_model=List[schemas.GenericTopicInfo])
def catalog_topics(
    db: Session = Depends(get_db),
    email: Optional[str] = Query(
        None,
        description="Caretaker email (with patient_id: auth + patient-scoped topic ranking)",
    ),
    patient_id: Optional[int] = Query(None),
):
    """Topics from on-disk folders; ``approx_count`` = generic images in DB per topic.

    When ``email`` and ``patient_id`` are set, topics are sorted by relevance to the
    patient's interests/sub-interests and caretaker+patient profession keywords.
    """
    counts = generic_topic_image_counts(db)
    cards = discover_generic_topic_cards()

    rank_patient: Optional[models.Patient] = None
    if email and patient_id is not None:
        rank_patient = _patient_for_caretaker_catalog(db, email, patient_id)

    out: List[schemas.GenericTopicInfo] = []
    for t in cards:
        slug = t["slug"]
        label = t["label"]
        default_bs = t.get("default_bundle_slug") or ""
        ms = 0.0
        if rank_patient is not None:
            interests = parse_json_string_list(rank_patient.interests)
            sub_i = parse_json_string_list(rank_patient.sub_interests)
            prof_toks = profession_haystack_tokens(rank_patient.profession)
            ms = topic_match_score(slug, label, interests, sub_i, prof_toks)
        out.append(
            schemas.GenericTopicInfo(
                slug=slug,
                label=label,
                blurb=t.get("blurb") or "",
                approx_count=counts.get(slug, 0),
                default_bundle_slug=default_bs,
                match_score=round(ms, 4),
            ),
        )

    if rank_patient is not None:
        out.sort(key=lambda x: (-x.match_score, x.slug))
    return out


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
    if not topic_exists_on_disk(topic_slug):
        raise HTTPException(status_code=404, detail="Unknown topic")

    db_rows = (
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
        .all()
    )
    db_counts = {coll: int(cnt or 0) for coll, cnt in db_rows if coll}

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
                models.CaretakerBundlePurchase.locked.is_(False),
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

    caretaker_bundle_state: dict[str, dict] = {}
    if email and patient_id and not patient_mode:
        for rp in (
            db.query(models.CaretakerBundlePurchase)
            .filter(
                models.CaretakerBundlePurchase.caretaker_email == email,
                models.CaretakerBundlePurchase.patient_id == patient_id,
                models.CaretakerBundlePurchase.library_topic == topic_slug,
            )
            .all()
        ):
            lr = getattr(rp, "locked", None)
            locked_val = bool(lr) if lr is not None else False
            caretaker_bundle_state[rp.library_collection_slug] = {"locked": locked_val}

    rank_ctx: Optional[tuple] = None
    if email and patient_id is not None and not patient_mode:
        rp = _patient_for_caretaker_catalog(db, email, patient_id)
        rank_ctx = (
            parse_json_string_list(rp.interests),
            parse_json_string_list(rp.sub_interests),
            (rp.gender or "").strip() or None,
        )

    out: List[schemas.CatalogBundleDetail] = []
    for coll in list_bundle_slugs_on_disk(topic_slug):
        bdir = _bundle_dir(topic_slug, coll)
        manifest = load_bundle_manifest_dict(bdir)
        pricing = parse_bundle_pricing(manifest)
        is_free = bool(pricing["is_free"])
        cnt = int(db_counts.get(coll, 0))
        if cnt == 0:
            cnt = _count_disk_images_with_manifest(bdir, manifest)

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
        purchase_pending_admin = False
        if email and patient_id and not patient_mode:
            st = caretaker_bundle_state.get(coll)
            if st is not None:
                is_purchased = not st["locked"]
                purchase_pending_admin = bool(st["locked"])
        elif patient_mode:
            is_purchased = (topic_slug, coll) in purchase_pairs

        if patient_mode:
            if not is_free and (topic_slug, coll) not in purchase_pairs:
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

        bundle_display = _bundle_label(coll)
        bb = manifest.get("__bundle__")
        if isinstance(bb, dict):
            for key in ("title", "display_name", "name"):
                tv = bb.get(key)
                if isinstance(tv, str) and tv.strip():
                    bundle_display = tv.strip()
                    break

        ms = 0.0
        if rank_ctx is not None:
            interests_l, sub_l, gend = rank_ctx
            base = bundle_match_score(
                coll, bundle_display, interests_l, sub_l, manifest
            )
            ms = float(base) * float(manifest_gender_penalty(manifest, gend))

        out.append(
            schemas.CatalogBundleDetail(
                topic_slug=topic_slug,
                bundle_slug=coll,
                display_name=bundle_display,
                image_count=cnt,
                average_rating=round(avg_f, 2),
                rating_count=n_ratings,
                is_purchased=is_purchased,
                purchase_pending_admin=purchase_pending_admin,
                cover_file_path=cover_path,
                is_free=is_free,
                price_cents=int(pricing.get("price_cents") or 0),
                currency=str(pricing.get("currency") or "USD"),
                match_score=round(ms, 4),
            ),
        )
    if rank_ctx is not None:
        out.sort(key=lambda b: (-b.match_score, b.bundle_slug))
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
    if not topic_exists_on_disk(topic_slug):
        raise HTTPException(status_code=404, detail="Unknown topic")

    if patient_id is not None and not (passcode or qr_token):
        raise HTTPException(
            status_code=400,
            detail="patient_id requires passcode or qr_token",
        )

    if patient_id and (passcode or qr_token):
        p = _patient_auth(db, patient_id, passcode, qr_token)
        allowed = bundle_is_free_on_disk(topic_slug, bundle_slug) or (
            db.query(models.CaretakerBundlePurchase.id)
            .filter(
                models.CaretakerBundlePurchase.patient_id == p.id,
                models.CaretakerBundlePurchase.caretaker_email == p.caretaker_email,
                models.CaretakerBundlePurchase.library_topic == topic_slug,
                models.CaretakerBundlePurchase.library_collection_slug == bundle_slug,
                models.CaretakerBundlePurchase.locked.is_(False),
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
    if not topic_exists_on_disk(payload.topic_slug):
        raise HTTPException(status_code=404, detail="Unknown topic")
    if not payload.bundle_slug:
        raise HTTPException(status_code=400, detail="bundle_slug required")
    if payload.stars < 1 or payload.stars > 5:
        raise HTTPException(status_code=400, detail="stars must be 1–5")

    p = _patient_auth(db, payload.patient_id, payload.passcode, payload.qr_token)

    unlocked = bundle_is_free_on_disk(payload.topic_slug, payload.bundle_slug) or (
        db.query(models.CaretakerBundlePurchase.id)
        .filter(
            models.CaretakerBundlePurchase.patient_id == p.id,
            models.CaretakerBundlePurchase.caretaker_email == p.caretaker_email,
            models.CaretakerBundlePurchase.library_topic == payload.topic_slug,
            models.CaretakerBundlePurchase.library_collection_slug == payload.bundle_slug,
            models.CaretakerBundlePurchase.locked.is_(False),
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
    if not topic_exists_on_disk(payload.topic_slug):
        raise HTTPException(status_code=404, detail="Unknown topic")
    if not payload.bundle_slug:
        raise HTTPException(status_code=400, detail="bundle_slug required")
    if bundle_is_free_on_disk(payload.topic_slug, payload.bundle_slug):
        raise HTTPException(
            status_code=400,
            detail="This bundle is free for all patients and does not require purchase.",
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

    existing = (
        db.query(models.CaretakerBundlePurchase)
        .filter(
            models.CaretakerBundlePurchase.caretaker_email == payload.caretaker_email,
            models.CaretakerBundlePurchase.patient_id == payload.patient_id,
            models.CaretakerBundlePurchase.library_topic == payload.topic_slug,
            models.CaretakerBundlePurchase.library_collection_slug == payload.bundle_slug,
        )
        .first()
    )
    if existing:
        lr = getattr(existing, "locked", None)
        locked_out = bool(lr) if lr is not None else False
        return schemas.BundlePurchaseResponse(
            status="ok",
            already_owned=True,
            locked=locked_out,
            purchase_id=existing.id,
        )

    bdir = _bundle_dir(payload.topic_slug, payload.bundle_slug)
    manifest = load_bundle_manifest_dict(bdir)
    pricing = parse_bundle_pricing(manifest)
    price_cents = int(pricing.get("price_cents") or 0)
    currency = str(pricing.get("currency") or "USD")

    row = models.CaretakerBundlePurchase(
        caretaker_email=payload.caretaker_email,
        patient_id=payload.patient_id,
        library_topic=payload.topic_slug,
        library_collection_slug=payload.bundle_slug,
        locked=True,
        price_cents=price_cents,
        currency=currency,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        dup = (
            db.query(models.CaretakerBundlePurchase)
            .filter(
                models.CaretakerBundlePurchase.caretaker_email == payload.caretaker_email,
                models.CaretakerBundlePurchase.patient_id == payload.patient_id,
                models.CaretakerBundlePurchase.library_topic == payload.topic_slug,
                models.CaretakerBundlePurchase.library_collection_slug == payload.bundle_slug,
            )
            .first()
        )
        lr = getattr(dup, "locked", None) if dup else None
        locked_out = bool(lr) if lr is not None else False
        return schemas.BundlePurchaseResponse(
            status="ok",
            already_owned=True,
            locked=locked_out,
            purchase_id=dup.id if dup else None,
        )

    patient_label = (patient.name or "").strip() or f"Patient #{patient.id}"
    amount_line = f"{(price_cents / 100.0):.2f} {currency}"
    msg = (
        f"Payment pending: {payload.caretaker_email} requested unlock of "
        f"'{payload.topic_slug}/{payload.bundle_slug}' for {patient_label} "
        f"({amount_line}). Approve in admin when payment is confirmed."
    )
    db.add(
        models.AdminWalletLedger(
            amount_cents=price_cents,
            currency=currency,
            purchase_id=row.id,
            description=f"Bundle purchase (pending admin): {payload.topic_slug}/{payload.bundle_slug}",
        ),
    )
    db.add(
        models.AdminNotification(
            purchase_id=row.id,
            message=msg,
        ),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        dup = (
            db.query(models.CaretakerBundlePurchase)
            .filter(
                models.CaretakerBundlePurchase.caretaker_email == payload.caretaker_email,
                models.CaretakerBundlePurchase.patient_id == payload.patient_id,
                models.CaretakerBundlePurchase.library_topic == payload.topic_slug,
                models.CaretakerBundlePurchase.library_collection_slug == payload.bundle_slug,
            )
            .first()
        )
        lr = getattr(dup, "locked", None) if dup else None
        locked_out = bool(lr) if lr is not None else False
        return schemas.BundlePurchaseResponse(
            status="ok",
            already_owned=True,
            locked=locked_out,
            purchase_id=dup.id if dup else None,
        )
    return schemas.BundlePurchaseResponse(
        status="ok",
        already_owned=False,
        locked=True,
        purchase_id=row.id,
    )
