# routers/patient_mgmt.py
from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
import models
import os
import shutil

router = APIRouter(prefix="/patients", tags=["Patient Management"])

UPLOAD_DIR = "static/profiles"
os.makedirs(UPLOAD_DIR, exist_ok=True)


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
        photo_path = os.path.join(UPLOAD_DIR, f"{name}_{profile_photo.filename}")
        with open(photo_path, "wb") as buffer:
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