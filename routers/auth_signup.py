from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import exists
from database import get_db
import models
import uuid
import os

router = APIRouter(
    prefix="/auth",
    tags=["Signup"]
)


@router.post("/signup-patient")
async def signup_patient(
        name: str = Form(...),
        relation: str = Form(...),
        dob: str = Form(None),
        location: str = Form(None),
        passcode: str = Form(...),
        medicalInfo: str = Form(None),
        interests: str = Form(None),  # Frontend se JSON string aa rahi hai
        caretaker_email: str = Form(...),
        profile_photo: UploadFile = File(None),
        db: Session = Depends(get_db)
):
    # 1. Duplicate Check (query only id to avoid loading columns that might not exist)
    patient_id = db.query(models.Patient.id).filter(models.Patient.name == name).first()
    if patient_id:
        raise HTTPException(status_code=400, detail="Patient already exists")

    # 2. QR Secret Generate karein
    qr_secret = f"PAT_LOGIN_{uuid.uuid4().hex}"

    # 3. Photo Storage Logic
    photo_path = "static/default.jpg"
    if profile_photo and profile_photo.filename:
        os.makedirs("static/patients", exist_ok=True)
        file_ext = profile_photo.filename.split(".")[-1]
        filename = f"{uuid.uuid4().hex}.{file_ext}"
        photo_path = f"static/patients/{filename}"

        contents = await profile_photo.read()
        with open(photo_path, "wb") as f:
            f.write(contents)

    # 4. Database Save
    try:
        new_patient = models.Patient(
            name=name,
            relation=relation,
            dob=dob,
            location=location,
            passcode=passcode,
            medical_info=medicalInfo,
            caretaker_email=caretaker_email,
            qr_token=qr_secret,
            profile_photo_path=photo_path
            # Agar models.py mein 'interests' column hai toh yahan add karein
        )

        db.add(new_patient)
        db.commit()
        db.refresh(new_patient)

        return {
            "status": "success",
            "message": "Patient registered successfully!",
            "qr_token": qr_secret,
            "patientName": name
        }

    except Exception as e:
        db.rollback()
        print(f"Final DB Error: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")