from sqlalchemy import Column, Integer, String, Text, Date, ForeignKey, DateTime
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    firstName = Column(String)
    lastName = Column(String)
    age = Column(Integer)
    email = Column(String, unique=True, index=True)
    password = Column(String)

class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    age = Column(Integer, default=0)
    relation = Column(String)
    dob = Column(String)
    location = Column(String)
    passcode = Column(String)
    medical_info = Column(String)
    qr_token = Column(String)
    profile_photo_path = Column(String)
    caretaker_email = Column(String)

# --- Memory Library Table (Generic & Personal) ---
class MemoryItem(Base):
    __tablename__ = "memory_items"
    id = Column(Integer, primary_key=True, index=True)
    # Foreign Key jo Patient se link hai (Generic ke liye nullable=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    title = Column(String)
    description = Column(String, nullable=True)
    category = Column(String)  # "image", "video", "audio"
    library_type = Column(String)  # "generic" ya "personal"
    file_path = Column(String)
    created_at = Column(DateTime, default=func.now())