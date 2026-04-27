# routers/patient_mgmt.py
import io
import shutil

import qrcode
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

import models
from app_paths import STATIC_DIR, media_path
from database import get_db

router = APIRouter(prefix="/patients", tags=["Patient Management"])

PROFILE_UPLOAD_DIR = STATIC_DIR / "profiles"
PROFILE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/{patient_id}/qr.png")
def get_patient_qr_image(patient_id: int, db: Session = Depends(get_db)):
    """Render the patient's QR code as a PNG image.

    Encodes `Patient.qr_token` (the same value `/auth/patient-qr-login` checks
    against) so the patient can sign in by scanning. Caretakers embed this URL
    directly via <Image source={{ uri: '.../patients/{id}/qr.png' }} />.
    """
    patient = (
        db.query(models.Patient.qr_token)
        .filter(models.Patient.id == patient_id)
        .first()
    )
    if not patient or not patient.qr_token:
        raise HTTPException(status_code=404, detail="Patient or QR token not found")

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(patient.qr_token)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.post("/signup")
async def signup_patient(
        name: str = Form(...),
        relation: str = Form(...),
        dob: str = Form(None),
        location: str = Form(None),
        passcode: str = Form(...),
        medicalInfo: str = Form(None),
        interests: str = Form(None),
        profile_photo: UploadFile = File(None),
        db: Session = Depends(get_db)
):
    # 1. Photo save karein agar upload hui hai
    photo_path = None
    if profile_photo:
        safe_name = f"{name}_{profile_photo.filename}".replace("\\", "_").replace(
            "/", "_",
        )
        photo_path = f"static/profiles/{safe_name}".replace("\\", "/")
        abs_p = media_path(photo_path)
        with open(abs_p, "wb") as buffer:
            shutil.copyfileobj(profile_photo.file, buffer)

    # 2. Database mein save karein
    new_patient = models.Patient(
        name=name,
        relation=relation,
        dob=dob,
        location=location,
        passcode=passcode,
        medical_info=medicalInfo,
        interests=interests,
        profile_photo_path=photo_path
    )

    db.add(new_patient)
    db.commit()
    db.refresh(new_patient)

    return {"message": "Patient Created Successfully", "id": new_patient.id}