import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

import models
from app_paths import STATIC_DIR, media_path
from database import get_db
from schemas import CaretakerCreate
from utils.Password_Hashing import hash_password

router = APIRouter(
    prefix="/auth",
    tags=["Signup"],
)


# ---------- helpers ----------

def _save_profile_photo(profile_photo: UploadFile) -> str:
    """Persist an uploaded photo to /static/patients and return the path.
    If no file was uploaded, return an empty string (no placeholder file on disk)."""
    if not (profile_photo and profile_photo.filename):
        return ""

    (STATIC_DIR / "patients").mkdir(parents=True, exist_ok=True)
    file_ext = profile_photo.filename.rsplit(".", 1)[-1].lower() or "jpg"
    filename = f"{uuid.uuid4().hex}.{file_ext}"
    return f"static/patients/{filename}"


# ---------- Patient signup ----------

@router.post("/signup-patient")
async def signup_patient(
    name: str = Form(...),
    relation: str = Form(...),
    age: int = Form(None),
    profession: str = Form(None),
    dob: str = Form(None),
    location: str = Form(None),
    login_id: str = Form(None),     # caretaker-chosen username for the patient
    passcode: str = Form(...),
    medicalInfo: str = Form(None),
    interests: str = Form(None),    # JSON string from the frontend
    caretaker_email: str = Form(...),
    profile_photo: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    # 1. Caretaker must exist
    caretaker = (
        db.query(models.Caretaker.id)
        .filter(models.Caretaker.email == caretaker_email)
        .first()
    )
    if not caretaker:
        raise HTTPException(status_code=404, detail="Caretaker not found")

    # 2. Duplicate-name check (scoped to this caretaker)
    duplicate_name = (
        db.query(models.Patient.id)
        .filter(
            models.Patient.name == name,
            models.Patient.caretaker_email == caretaker_email,
        )
        .first()
    )
    if duplicate_name:
        raise HTTPException(
            status_code=400,
            detail="A patient with this name already exists for this caretaker",
        )

    # 3. login_id must be globally unique (it's how the patient logs in)
    if login_id:
        login_id = login_id.strip()
        existing_login = (
            db.query(models.Patient.id)
            .filter(models.Patient.login_id == login_id)
            .first()
        )
        if existing_login:
            raise HTTPException(
                status_code=400,
                detail="This Patient ID is already taken. Choose a different one.",
            )

    # 4. Photo storage (write only on success path)
    photo_path = _save_profile_photo(profile_photo)
    if profile_photo and profile_photo.filename and photo_path:
        contents = await profile_photo.read()
        with open(media_path(photo_path), "wb") as f:
            f.write(contents)

    # 5. QR secret for the patient's quick-login
    qr_secret = f"PAT_LOGIN_{uuid.uuid4().hex}"

    # 6. Persist
    try:
        new_patient = models.Patient(
            name=name,
            relation=relation,
            age=age or 0,
            profession=profession,
            dob=dob,
            location=location,
            login_id=login_id or None,
            passcode=passcode,
            medical_info=medicalInfo,
            interests=interests,
            caretaker_email=caretaker_email,
            qr_token=qr_secret,
            profile_photo_path=photo_path,
        )
        db.add(new_patient)
        db.commit()
        db.refresh(new_patient)

        return {
            "status": "success",
            "message": "Patient registered successfully!",
            "patient": {
                "id": new_patient.id,
                "name": new_patient.name,
                "login_id": new_patient.login_id,
                "qr_token": new_patient.qr_token,
                "profile_photo_path": new_patient.profile_photo_path,
            },
        }

    except Exception as e:
        db.rollback()
        print(f"Final DB Error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


# ---------- Caretaker signup ----------

@router.post("/signup")
async def signup_caretaker(user_data: CaretakerCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(models.Caretaker)
        .filter(models.Caretaker.email == user_data.email)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Caretaker email already exists")

    new_caretaker = models.Caretaker(
        firstName=user_data.firstName,
        lastName=user_data.lastName,
        email=user_data.email,
        password=hash_password(user_data.password),
        age=user_data.age,
    )

    try:
        db.add(new_caretaker)
        db.commit()
        db.refresh(new_caretaker)
        return {"status": "success", "message": "Caretaker registered successfully!"}
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database insertion failed")
