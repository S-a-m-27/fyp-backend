import os
import uuid
import random
from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile, Query
from sqlalchemy.orm import Session
from database import get_db
import models

router = APIRouter(prefix="/memory", tags=["Memory Management"])

# --- 1. UPLOAD MEMORY ---
@router.post("/upload")
async def upload_memory(
        title: str = Form(...),
        category: str = Form(...),  # image, video, audio
        library_type: str = Form(...),  # generic, personal
        patient_id: int = Form(None),
        file: UploadFile = File(...),
        db: Session = Depends(get_db)
):
    upload_dir = f"static/memory/{library_type}"
    os.makedirs(upload_dir, exist_ok=True)

    file_ext = file.filename.split(".")[-1]
    filename = f"{uuid.uuid4().hex}.{file_ext}"
    file_path = f"{upload_dir}/{filename}"

    with open(file_path, "wb") as f:
        f.write(await file.read())

    new_memory = models.MemoryItem(
        patient_id=patient_id,
        title=title,
        category=category,
        library_type=library_type,
        file_path=file_path
    )
    db.add(new_memory)
    db.commit()

    return {"status": "success", "message": "Memory added successfully!"}


# --- 2. FETCH ALL FOR TRAINING (Slideshow) ---
@router.get("/all/{patient_id}")
async def get_all_memories(patient_id: int, db: Session = Depends(get_db)):
    memories = db.query(models.MemoryItem).filter(
        (models.MemoryItem.patient_id == patient_id) |
        (models.MemoryItem.library_type == 'generic')
    ).all()

    return memories


# --- 3. GET QUIZ QUESTION (With Logic for No Repeats) ---
@router.get("/quiz/{patient_id}")
async def get_memory_quiz(
    patient_id: int,
    exclude_ids: str = Query(""), # Format: "1,2,3" from Frontend
    db: Session = Depends(get_db)
):
    """
    Selects a random item that HAS NOT been answered correctly yet.
    """
    # Convert comma-separated string to list of integers
    excluded_list = [int(i) for i in exclude_ids.split(",") if i.strip().isdigit()]

    # Get all eligible memories
    base_query = db.query(models.MemoryItem).filter(
        (models.MemoryItem.patient_id == patient_id) |
        (models.MemoryItem.library_type == 'generic')
    )

    # Filter out IDs that are already finished (correctly guessed)
    available_memories = base_query.filter(models.MemoryItem.id.not_in(excluded_list)).all()

    # If no memories left, tell the frontend the quiz is over
    if not available_memories:
        return {"status": "finished", "message": "All items completed!"}

    # Pick the target (Correct Answer) from AVAILABLE items
    correct_item = random.choice(available_memories)

    # Get distractors from ALL items (so we always have 4 options)
    all_memories = base_query.all()
    other_titles = list(set([m.title for m in all_memories if m.title != correct_item.title]))

    if len(other_titles) < 3:
        raise HTTPException(
            status_code=400,
            detail="Not enough unique memory titles to generate multiple choices."
        )

    distractors = random.sample(other_titles, 3)
    shuffled_options = distractors + [correct_item.title]
    random.shuffle(shuffled_options)

    return {
        "status": "ongoing",
        "question_item": {
            "id": correct_item.id,
            "title": correct_item.title,
            "file_path": correct_item.file_path,
            "category": correct_item.category
        },
        "shuffled_options": shuffled_options
    }