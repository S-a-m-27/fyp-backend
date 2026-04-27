from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


# Many-to-many: a personal memory can be made accessible to multiple patients
# under the same caretaker (e.g. siblings sharing childhood photos).
memory_patient_access = Table(
    "memory_patient_access",
    Base.metadata,
    Column(
        "memory_id",
        Integer,
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "patient_id",
        Integer,
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    firstName = Column(String)
    lastName = Column(String)
    age = Column(Integer)
    email = Column(String, unique=True, index=True)
    password = Column(String)


class Caretaker(Base):
    __tablename__ = "caretakers"

    id = Column(Integer, primary_key=True, index=True)
    firstName = Column(String)
    lastName = Column(String)
    age = Column(Integer)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    created_at = Column(DateTime, default=func.now())

    patients = relationship(
        "Patient",
        back_populates="caretaker",
        primaryjoin="Caretaker.email == foreign(Patient.caretaker_email)",
        viewonly=True,
    )


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    age = Column(Integer, default=0)
    relation = Column(String)
    profession = Column(String, nullable=True)
    dob = Column(String)
    location = Column(String)
    # Caretaker-chosen reference / display ID for the patient (NOT used for login -
    # patients always sign in via their QR code -> see `qr_token`).
    login_id = Column(String, unique=True, index=True, nullable=True)
    passcode = Column(String)
    medical_info = Column(String)
    interests = Column(Text, nullable=True)
    qr_token = Column(String)
    profile_photo_path = Column(String)
    caretaker_email = Column(String, index=True)
    # Patient must complete gentle memory training (generic + personal) before home.
    memory_training_completed = Column(Boolean, default=False, nullable=False)
    # Short wellness / orientation quiz after QR login (one-time per patient).
    wellness_intro_completed = Column(Boolean, default=False, nullable=False)
    # Guided training sessions (0–3); quiz mode unlocks at 3.
    training_sessions_completed = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now())

    caretaker = relationship(
        "Caretaker",
        back_populates="patients",
        primaryjoin="Caretaker.email == foreign(Patient.caretaker_email)",
        viewonly=True,
    )
    sessions = relationship("Session", back_populates="patient", cascade="all, delete")
    memories = relationship("MemoryItem", back_populates="patient")
    shared_memories = relationship(
        "MemoryItem",
        secondary=memory_patient_access,
        back_populates="shared_with",
    )


class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    patient_name = Column(String)
    mode = Column(String)  # e.g. "Quiz Mode", "Training Mode"
    duration_minutes = Column(Integer, default=0)
    started_at = Column(DateTime, default=datetime.utcnow, index=True)

    patient = relationship("Patient", back_populates="sessions")


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id = Column(Integer, primary_key=True, index=True)
    # Primary owner of the memory (the patient it was originally uploaded for).
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    title = Column(String)
    description = Column(String, nullable=True)
    # Person shown in the media (for personal memories / face-recognition metadata).
    related_person_name = Column(String, nullable=True)
    # How that person relates to the patient (e.g. Grandson, Sister).
    related_person_relation = Column(String, nullable=True)
    category = Column(String)        # "image" | "video" | "audio"
    library_type = Column(String)    # "generic" | "personal"
    # Generic library shelf (war_history, cricket, music_singers, …). Null for personal.
    library_topic = Column(String, nullable=True, index=True)
    # Generic sub-folder / purchasable bundle under that topic (e.g. ww2_memorials). Null = legacy flat file.
    library_collection_slug = Column(String, nullable=True, index=True)
    # Sub-type shown in the UI for personal memories.
    memory_type = Column(String, nullable=True)  # "specific" | "general"
    year = Column(Integer, nullable=True)
    location = Column(String, nullable=True)
    # Caretaker who owns this memory (used to scope sharing to their patients).
    caretaker_email = Column(String, nullable=True, index=True)
    file_path = Column(String)
    # JSON array of extra image paths (same memory, multiple photos of the person).
    extra_file_paths = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    patient = relationship("Patient", back_populates="memories")
    # Patients (besides the primary owner) who also have access to this memory.
    shared_with = relationship(
        "Patient",
        secondary=memory_patient_access,
        back_populates="shared_memories",
    )


class MemoryImageRating(Base):
    """Per-memory star rating from a patient during training (feeds bundle averages)."""

    __tablename__ = "memory_image_ratings"
    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "memory_item_id",
            name="uq_memory_image_rating_patient_memory",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    memory_item_id = Column(
        Integer,
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class BundleRating(Base):
    """Patient rating (1–5 stars) for one generic bundle (topic + collection)."""

    __tablename__ = "bundle_ratings"
    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "library_topic",
            "library_collection_slug",
            name="uq_bundle_rating_patient_topic_collection",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    library_topic = Column(String(80), nullable=False, index=True)
    library_collection_slug = Column(String(120), nullable=False, index=True)
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class CaretakerBundlePurchase(Base):
    """Caretaker unlocks a generic bundle for one patient (same caretaker_email)."""

    __tablename__ = "caretaker_bundle_purchases"
    __table_args__ = (
        UniqueConstraint(
            "caretaker_email",
            "patient_id",
            "library_topic",
            "library_collection_slug",
            name="uq_caretaker_bundle_purchase_once",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    caretaker_email = Column(String, nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    library_topic = Column(String(80), nullable=False)
    library_collection_slug = Column(String(120), nullable=False)
    purchased_at = Column(DateTime, default=func.now())
