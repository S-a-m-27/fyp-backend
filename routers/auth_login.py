from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from sqlalchemy import func
import logging

import models, schemas
from data.admin_auth import authenticate_admin_credentials, create_admin_session
from utils.Password_Hashing import verify_password

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/auth",
    tags=["Login"]
)

# --- 1. QR LOGIN ENDPOINT (Sir ki New Requirement) ---
# Is line ko update karein:
@router.post("/patient-qr-login") # Pehle yahan /login-qr tha
async def login_by_qr(payload: schemas.QRLoginRequest, db: Session = Depends(get_db)):
    patient = db.query(models.Patient).filter(models.Patient.qr_token == payload.qr_token).first()

    if not patient:
        raise HTTPException(status_code=401, detail="Invalid or Expired QR Code")

    return {
        "status": "success",
        "userName": patient.name,
        "userType": "patient",
        "patientId": patient.id,
        "patientPhoto": patient.profile_photo_path,
        "memoryTrainingCompleted": bool(
            getattr(patient, "memory_training_completed", False),
        ),
        "wellnessIntroCompleted": bool(
            getattr(patient, "wellness_intro_completed", False),
        ),
        "trainingSessionsCompleted": int(
            getattr(patient, "training_sessions_completed", 0) or 0,
        ),
        "message": "QR Login Successful",
    }

# --- 2. REGULAR LOGIN (Caretaker aur Manual Patient Login) ---
@router.post("/login")
async def login(user_credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    try:
        return _login_core(user_credentials, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "POST /auth/login unexpected error userType=%r email=%r",
            getattr(user_credentials, "userType", None),
            getattr(user_credentials, "email", None),
        )
        raise HTTPException(
            status_code=500,
            detail="Login failed due to a server error. See API logs for the traceback.",
        ) from e


def _login_core(user_credentials: schemas.UserLogin, db: Session):
    # --- CARETAKER LOGIN ---
    if user_credentials.userType == "caretaker":
        email = (user_credentials.email or "").strip()
        user = (
            db.query(models.Caretaker)
            .filter(models.Caretaker.email == email)
            .first()
        )
        if not user:
            logger.warning(
                "POST /auth/login 401: caretaker not found email=%r",
                email,
            )
            raise HTTPException(status_code=401, detail="Invalid Caretaker Credentials")

        status = verify_password(user_credentials.password, user.password)
        if not status:
            logger.warning(
                "POST /auth/login 401: caretaker password mismatch email=%r",
                email,
            )
            raise HTTPException(status_code=401, detail="Invalid Caretaker Credentials")

        return {
            "status": "success",
            "userName": user.firstName + user.lastName,
            "UserEmail": user.email,
            "userType": "caretaker",
            "access_token": "dummy-token",
        }

    # --- PATIENT MANUAL LOGIN (Backup ke liye) ---
    if user_credentials.userType == "patient":
        patient = db.query(models.Patient).filter(
            func.lower(models.Patient.name) == func.lower(user_credentials.email),
            models.Patient.passcode == user_credentials.password,
        ).first()

        if not patient:
            logger.warning(
                "POST /auth/login 401: patient not found (name+passcode mismatch) name=%r",
                (user_credentials.email or "").strip(),
            )
            raise HTTPException(status_code=401, detail="Invalid Credentials")

        return {
            "status": "success",
            "userName": patient.name,
            "userType": "patient",
            "patientId": patient.id,
            "patientPhoto": patient.profile_photo_path,
            "memoryTrainingCompleted": bool(
                getattr(patient, "memory_training_completed", False),
            ),
            "wellnessIntroCompleted": bool(
                getattr(patient, "wellness_intro_completed", False),
            ),
            "trainingSessionsCompleted": int(
                getattr(patient, "training_sessions_completed", 0) or 0,
            ),
        }

    if user_credentials.userType == "admin":
        n_admins = (
            db.query(models.AuthorizedUser)
            .filter(models.AuthorizedUser.role == "admin")
            .count()
        )
        if n_admins == 0:
            logger.error(
                "POST /auth/login 503: no authorized_users with role=admin (set ADMIN_EMAIL, ADMIN_PASSWORD, restart API)",
            )
            raise HTTPException(
                status_code=503,
                detail="No admin account yet. Set ADMIN_EMAIL and ADMIN_PASSWORD on the server and restart the API.",
            )
        user = authenticate_admin_credentials(
            db,
            user_credentials.email,
            user_credentials.password,
        )
        if not user:
            logger.warning(
                "POST /auth/login 401: admin rejected email=%r (unknown email, wrong password, or bcrypt error — see auth.admin_auth logs)",
                (user_credentials.email or "").strip().lower(),
            )
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        try:
            token = create_admin_session(db, user.id)
        except Exception as e:
            logger.exception(
                "POST /auth/login: create_admin_session failed for authorized_user id=%s",
                user.id,
            )
            raise HTTPException(
                status_code=500,
                detail="Could not create admin session. See API logs.",
            ) from e
        return {
            "status": "success",
            "userName": "Administrator",
            "UserEmail": user.email,
            "userType": "admin",
            "access_token": token,
        }

    logger.warning(
        "POST /auth/login 400: invalid userType=%r",
        user_credentials.userType,
    )
    raise HTTPException(status_code=400, detail="Invalid User Type")