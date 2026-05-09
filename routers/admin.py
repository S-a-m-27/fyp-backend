"""Admin: platform wallet, pending bundle purchases, approve unlocks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from data.admin_auth import get_authorized_admin_from_token
from database import get_db

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
