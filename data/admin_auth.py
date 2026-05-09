"""Bootstrap admin from env; create and validate admin API sessions."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from utils.Password_Hashing import hash_password, verify_password

logger = logging.getLogger(__name__)


def sync_admin_user_from_env(db: Session) -> None:
    """Upsert ``authorized_users`` row from ``ADMIN_EMAIL`` + ``ADMIN_PASSWORD`` env (hashed)."""
    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    password_plain = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if not email or not password_plain:
        return
    hashed = hash_password(password_plain)
    u = (
        db.query(models.AuthorizedUser)
        .filter(func.lower(models.AuthorizedUser.email) == email)
        .first()
    )
    if u:
        u.password = hashed
        u.role = "admin"
        db.query(models.AdminAuthSession).filter(
            models.AdminAuthSession.authorized_user_id == u.id,
        ).delete(synchronize_session=False)
    else:
        db.add(
            models.AuthorizedUser(
                email=email,
                password=hashed,
                role="admin",
            ),
        )
    db.commit()


def create_admin_session(db: Session, authorized_user_id: int) -> str:
    """Create a new session; returns the raw bearer token (shown once to the client)."""
    raw = secrets.token_urlsafe(32)
    th = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    exp = datetime.now(timezone.utc) + timedelta(days=14)
    db.add(
        models.AdminAuthSession(
            token_hash=th,
            authorized_user_id=authorized_user_id,
            expires_at=exp,
        ),
    )
    db.commit()
    return raw


def get_authorized_admin_from_token(db: Session, token: str) -> Optional[models.AuthorizedUser]:
    """Resolve admin user from bearer token, or None if invalid/expired."""
    if not (token or "").strip():
        return None
    th = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    sess = (
        db.query(models.AdminAuthSession)
        .filter(
            models.AdminAuthSession.token_hash == th,
            models.AdminAuthSession.expires_at > func.now(),
        )
        .first()
    )
    if not sess:
        return None
    user = (
        db.query(models.AuthorizedUser)
        .filter(
            models.AuthorizedUser.id == sess.authorized_user_id,
            models.AuthorizedUser.role == "admin",
        )
        .first()
    )
    return user


def authenticate_admin_credentials(
    db: Session, email: str, password: str
) -> Optional[models.AuthorizedUser]:
    """Validate email/password against ``authorized_users`` with role ``admin``."""
    em = (email or "").strip().lower()
    pw = (password or "").strip()
    if not em or not pw:
        return None
    user = (
        db.query(models.AuthorizedUser)
        .filter(
            func.lower(models.AuthorizedUser.email) == em,
            models.AuthorizedUser.role == "admin",
        )
        .first()
    )
    if not user:
        logger.warning(
            "authenticate_admin: no authorized_users row for email=%r role=admin",
            em,
        )
        return None
    stored = (user.password or "").strip()
    if not stored:
        logger.warning(
            "authenticate_admin: empty password hash in DB for email=%r id=%s",
            em,
            user.id,
        )
        return None
    try:
        ok = verify_password(pw, stored)
    except Exception:
        logger.exception(
            "authenticate_admin: verify_password raised for email=%r id=%s",
            em,
            user.id,
        )
        return None
    if not ok:
        logger.warning(
            "authenticate_admin: password mismatch for email=%r id=%s",
            em,
            user.id,
        )
        return None
    return user
