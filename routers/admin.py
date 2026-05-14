"""Admin: platform wallet, pending bundle purchases, approve unlocks.

Also exposes the moderation queue for anonymous generic-memory contributions
submitted via ``POST /memory/contribute``: list, approve (move file +
update manifest + create MemoryItem), and reject (delete file + audit row).
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from app_paths import STATIC_DIR
from data.admin_auth import get_authorized_admin_from_token
from data.generic_memory_library import (
    bump_generic_library_disk_cache,
    is_safe_library_segment,
)
from database import get_db
from routers.memory import (
    _contrib_build_bundle_block,
    _contrib_read_manifest,
    _contrib_write_manifest,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


def get_current_admin(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
) -> models.AuthorizedUser:
    token = (x_admin_token or "").strip()
    if not token and authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing admin session (use Authorization: Bearer <token> or X-Admin-Token)",
        )
    user = get_authorized_admin_from_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired admin session")
    return user


@router.get("/wallet", response_model=schemas.AdminWalletSummary)
def admin_wallet_summary(
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(
            models.AdminWalletLedger.currency,
            func.coalesce(func.sum(models.AdminWalletLedger.amount_cents), 0),
        )
        .group_by(models.AdminWalletLedger.currency)
        .all()
    )
    balances = [
        schemas.AdminWalletBalanceRow(currency=str(c or "USD"), balance_cents=int(s or 0))
        for c, s in rows
    ]
    return schemas.AdminWalletSummary(balances=balances)


@router.get("/wallet/ledger", response_model=List[schemas.AdminLedgerRow])
def admin_wallet_ledger(
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
):
    rows = (
        db.query(models.AdminWalletLedger)
        .order_by(models.AdminWalletLedger.id.desc())
        .limit(limit)
        .all()
    )
    return [
        schemas.AdminLedgerRow(
            id=r.id,
            amount_cents=int(r.amount_cents or 0),
            currency=str(r.currency or "USD"),
            purchase_id=r.purchase_id,
            description=r.description,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get(
    "/purchases/pending",
    response_model=List[schemas.AdminPendingPurchaseItem],
)
def list_pending_purchases(
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    q = (
        db.query(models.CaretakerBundlePurchase)
        .filter(models.CaretakerBundlePurchase.locked.is_(True))
        .order_by(models.CaretakerBundlePurchase.id.desc())
    )
    out: List[schemas.AdminPendingPurchaseItem] = []
    for r in q.all():
        pname = (
            db.query(models.Patient.name)
            .filter(models.Patient.id == r.patient_id)
            .scalar()
        )
        out.append(
            schemas.AdminPendingPurchaseItem(
                id=r.id,
                caretaker_email=r.caretaker_email,
                patient_id=r.patient_id,
                patient_name=pname,
                library_topic=r.library_topic,
                library_collection_slug=r.library_collection_slug,
                price_cents=r.price_cents,
                currency=r.currency,
                locked=bool(r.locked),
                purchased_at=r.purchased_at,
            ),
        )
    return out


@router.get(
    "/notifications",
    response_model=List[schemas.AdminNotificationItem],
)
def list_admin_notifications(
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
    unread_only: bool = Query(True),
):
    q = db.query(models.AdminNotification).order_by(models.AdminNotification.id.desc())
    if unread_only:
        q = q.filter(models.AdminNotification.read_at.is_(None))
    return [
        schemas.AdminNotificationItem(
            id=n.id,
            purchase_id=n.purchase_id,
            message=n.message,
            read_at=n.read_at,
            created_at=n.created_at,
        )
        for n in q.limit(200).all()
    ]


@router.post(
    "/purchases/{purchase_id}/approve",
    response_model=schemas.AdminApprovePurchaseResponse,
)
def approve_bundle_purchase(
    purchase_id: int,
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    row = (
        db.query(models.CaretakerBundlePurchase)
        .filter(models.CaretakerBundlePurchase.id == purchase_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Purchase not found")
    if row.locked is not True:
        return schemas.AdminApprovePurchaseResponse(
            status="ok",
            already_unlocked=True,
        )
    row.locked = False
    db.query(models.AdminNotification).filter(
        models.AdminNotification.purchase_id == purchase_id,
    ).update({"read_at": func.now()}, synchronize_session=False)
    db.commit()
    return schemas.AdminApprovePurchaseResponse(status="ok", already_unlocked=False)


@router.post("/notifications/{notification_id}/read", response_model=dict)
def mark_notification_read(
    notification_id: int,
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    n = (
        db.query(models.AdminNotification)
        .filter(models.AdminNotification.id == notification_id)
        .first()
    )
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"status": "ok"}


# ---------- Contributor moderation queue ------------------------------------
# Anonymous users hit ``POST /memory/contribute`` which writes images into
# ``static/memory/pending/`` and inserts a parent ``PendingContributionBundle``
# row plus one ``PendingContributionImage`` per file. Admins review each
# bundle here, then either approve (every image is moved into the proper
# ``static/memory/generic/<topic>/<bundle>/`` folder, the bundle's pricing is
# written to ``manifest.json`` under ``__bundle__`` together with per-image
# entries, and a ``MemoryItem`` row is created for each image) or reject (all
# uploaded files are deleted; the bundle row is kept for audit).


def _pending_bundle_or_404(
    db: Session, bundle_id: int,
) -> models.PendingContributionBundle:
    row = (
        db.query(models.PendingContributionBundle)
        .filter(models.PendingContributionBundle.id == bundle_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Contribution not found")
    return row


def _pending_images_for(
    db: Session, bundle_id: int,
) -> List[models.PendingContributionImage]:
    return (
        db.query(models.PendingContributionImage)
        .filter(models.PendingContributionImage.bundle_id == bundle_id)
        .order_by(
            models.PendingContributionImage.order_index.asc(),
            models.PendingContributionImage.id.asc(),
        )
        .all()
    )


def _pending_abs_path(rel_path: str) -> Path:
    """Resolve the absolute filesystem path for a stored static-relative path."""
    rel = (rel_path or "").lstrip("/")
    prefix = "static/"
    if rel.startswith(prefix):
        rel = rel[len(prefix):]
    return STATIC_DIR / rel


def _serialize_bundle(
    bundle: models.PendingContributionBundle,
    images: List[models.PendingContributionImage],
) -> schemas.AdminPendingContributionBundle:
    return schemas.AdminPendingContributionBundle(
        id=bundle.id,
        contributor_email=bundle.contributor_email,
        library_topic=bundle.library_topic,
        library_collection_slug=bundle.library_collection_slug,
        bundle_description=bundle.bundle_description,
        is_free=bool(bundle.is_free),
        price_cents=int(bundle.price_cents or 0),
        currency=bundle.currency or "USD",
        status=bundle.status,
        review_note=bundle.review_note,
        reviewed_by_admin_email=bundle.reviewed_by_admin_email,
        reviewed_at=bundle.reviewed_at,
        created_at=bundle.created_at,
        images=[
            schemas.AdminPendingContributionImage(
                id=img.id,
                title=img.title,
                description=img.description,
                location=img.location,
                file_path=img.file_path,
                order_index=int(img.order_index or 0),
            )
            for img in images
        ],
    )


@router.get(
    "/contributions/pending",
    response_model=List[schemas.AdminPendingContributionBundle],
)
def list_pending_contributions(
    _admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
    status_filter: str = Query("pending", alias="status"),
    limit: int = Query(100, ge=1, le=500),
):
    """List contributor bundles filtered by ``status`` (default ``pending``)."""
    q = db.query(models.PendingContributionBundle)
    valid = {"pending", "approved", "rejected", "all"}
    sf = (status_filter or "pending").strip().lower()
    if sf not in valid:
        sf = "pending"
    if sf != "all":
        q = q.filter(models.PendingContributionBundle.status == sf)
    q = q.order_by(models.PendingContributionBundle.id.desc()).limit(limit)
    bundles = q.all()
    if not bundles:
        return []

    ids = [b.id for b in bundles]
    image_rows = (
        db.query(models.PendingContributionImage)
        .filter(models.PendingContributionImage.bundle_id.in_(ids))
        .order_by(
            models.PendingContributionImage.bundle_id.asc(),
            models.PendingContributionImage.order_index.asc(),
            models.PendingContributionImage.id.asc(),
        )
        .all()
    )
    by_bundle: dict = {}
    for img in image_rows:
        by_bundle.setdefault(img.bundle_id, []).append(img)

    return [_serialize_bundle(b, by_bundle.get(b.id, [])) for b in bundles]


@router.post(
    "/contributions/{bundle_id}/approve",
    response_model=schemas.AdminApproveContributionResponse,
)
def approve_pending_contribution(
    bundle_id: int,
    admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Publish an entire pending bundle into the on-disk generic library."""
    bundle = _pending_bundle_or_404(db, bundle_id)

    if bundle.status == "approved":
        return schemas.AdminApproveContributionResponse(
            status="ok",
            pending_bundle_id=bundle.id,
            library_topic=bundle.library_topic,
            library_collection_slug=bundle.library_collection_slug,
            already_reviewed=True,
        )
    if bundle.status == "rejected":
        raise HTTPException(
            status_code=400,
            detail="This bundle was already rejected.",
        )

    topic_segment = bundle.library_topic or ""
    bundle_segment = bundle.library_collection_slug or ""
    if not is_safe_library_segment(topic_segment) or not is_safe_library_segment(
        bundle_segment,
    ):
        raise HTTPException(
            status_code=400,
            detail="Stored topic or bundle is no longer valid.",
        )

    images = _pending_images_for(db, bundle.id)
    if not images:
        raise HTTPException(
            status_code=400,
            detail="This bundle has no images to publish.",
        )

    bundle_dir = (
        STATIC_DIR / "memory" / "generic" / topic_segment / bundle_segment
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)

    manifest = _contrib_read_manifest(bundle_dir)
    bundle_block, pricing_preserved = _contrib_build_bundle_block(
        existing=manifest.get("__bundle__") if isinstance(manifest, dict) else None,
        is_free=bool(bundle.is_free),
        price_cents=int(bundle.price_cents or 0),
        currency=bundle.currency or "USD",
        description=bundle.bundle_description,
    )
    manifest["__bundle__"] = bundle_block

    # Stage 1: move files to their final location and record final relative
    # paths so we can roll back if anything goes wrong.
    moves: List[Tuple[Path, Path]] = []  # (final, src) for rollback
    final_paths_for_images: List[Tuple[models.PendingContributionImage, str, str]] = []

    try:
        for img in images:
            src = _pending_abs_path(img.file_path)
            if not src.is_file():
                raise HTTPException(
                    status_code=410,
                    detail=(
                        f"Image #{img.order_index + 1} is missing on disk; "
                        "cannot approve this bundle."
                    ),
                )
            target_name = src.name
            target_path = bundle_dir / target_name
            if target_path.exists():
                stem = src.stem
                suffix = src.suffix
                i = 1
                while True:
                    candidate = bundle_dir / f"{stem}-{i}{suffix}"
                    if not candidate.exists():
                        target_path = candidate
                        target_name = candidate.name
                        break
                    i += 1
            shutil.move(str(src), str(target_path))
            moves.append((target_path, src))

            final_rel = (
                f"static/memory/generic/{topic_segment}/{bundle_segment}/"
                f"{target_name}"
            )
            final_paths_for_images.append((img, target_name, final_rel))

            entry: dict = {"title": img.title}
            if img.location:
                entry["location"] = img.location
            if img.description:
                entry["description"] = img.description
            entry["contributor_email"] = bundle.contributor_email
            manifest[target_name] = entry

        _contrib_write_manifest(bundle_dir, manifest)
    except HTTPException:
        for final, src in moves:
            try:
                shutil.move(str(final), str(src))
            except OSError:
                pass
        raise
    except OSError as exc:
        for final, src in moves:
            try:
                shutil.move(str(final), str(src))
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail=f"Could not write manifest: {exc}",
        )

    # Stage 2: insert MemoryItem rows + update pending records.
    memory_ids: List[int] = []
    for img, _final_name, final_rel in final_paths_for_images:
        new_row = models.MemoryItem(
            patient_id=None,
            title=img.title,
            description=img.description,
            related_person_name=None,
            related_person_relation=None,
            category="image",
            library_type="generic",
            library_topic=topic_segment,
            library_collection_slug=bundle_segment,
            memory_type="general",
            year=None,
            location=img.location,
            caretaker_email=None,
            file_path=final_rel,
            extra_file_paths=None,
        )
        db.add(new_row)
        db.flush()
        memory_ids.append(int(new_row.id))
        img.file_path = final_rel

    bundle.status = "approved"
    bundle.reviewed_at = datetime.now(timezone.utc)
    bundle.reviewed_by_admin_email = admin.email
    db.commit()

    bump_generic_library_disk_cache()

    return schemas.AdminApproveContributionResponse(
        status="ok",
        pending_bundle_id=bundle.id,
        library_topic=topic_segment,
        library_collection_slug=bundle_segment,
        image_count=len(memory_ids),
        memory_ids=memory_ids,
        already_reviewed=False,
        pricing_preserved=pricing_preserved,
    )


@router.post(
    "/contributions/{bundle_id}/reject",
    response_model=schemas.AdminRejectContributionResponse,
)
def reject_pending_contribution(
    bundle_id: int,
    payload: Optional[schemas.AdminRejectContributionRequest] = None,
    admin: models.AuthorizedUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Reject a pending bundle: delete all of its files and audit the row."""
    bundle = _pending_bundle_or_404(db, bundle_id)

    if bundle.status == "rejected":
        return schemas.AdminRejectContributionResponse(
            status="ok",
            pending_bundle_id=bundle.id,
            already_reviewed=True,
        )
    if bundle.status == "approved":
        raise HTTPException(
            status_code=400,
            detail="Cannot reject — this bundle was already approved.",
        )

    images = _pending_images_for(db, bundle.id)
    for img in images:
        src = _pending_abs_path(img.file_path)
        if src.is_file():
            try:
                src.unlink()
            except OSError:
                # non-fatal; keep going so we mark the bundle as rejected anyway
                pass

    bundle.status = "rejected"
    bundle.reviewed_at = datetime.now(timezone.utc)
    bundle.reviewed_by_admin_email = admin.email
    if payload is not None and payload.note is not None:
        note = payload.note.strip()
        bundle.review_note = note or None
    db.commit()

    return schemas.AdminRejectContributionResponse(
        status="ok",
        pending_bundle_id=bundle.id,
        already_reviewed=False,
    )
