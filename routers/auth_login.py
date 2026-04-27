from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from sqlalchemy import func
import models, schemas
from utils.Password_Hashing import verify_password

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
    # --- CARETAKER LOGIN ---
    if user_credentials.userType == "caretaker":
        user = db.query(models.Caretaker).filter(models.Caretaker.email == user_credentials.email).first()

        status = verify_password(user_credentials.password,user.password)
        print("Password verification status: ", status )

        if not status:
            raise HTTPException(status_code=401, detail="Invalid Caretaker Credentials")

        return {
            "status": "success",
            "userName": user.firstName + user.lastName,
            "UserEmail":user.email,
            "userType": "caretaker",
            "access_token": "dummy-token"
        }

    # --- PATIENT MANUAL LOGIN (Backup ke liye) ---
    elif user_credentials.userType == "patient":
        patient = db.query(models.Patient).filter(
            func.lower(models.Patient.name) == func.lower(user_credentials.email),
            models.Patient.passcode == user_credentials.password
        ).first()

        if not patient:
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

    raise HTTPException(status_code=400, detail="Invalid User Type")