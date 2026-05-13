import os
import random
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

import numpy as np
from deepface import DeepFace
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app_paths import BACKEND_DIR, STATIC_DIR, media_path

router = APIRouter(
    prefix="/ai",
    tags=["AI Training"],
)

# Directories setup (anchored to backend package, not process cwd)
STORAGE_DIR = str(BACKEND_DIR / "memory_bank")
IMAGES_DIR = str(STATIC_DIR / "display_images")

for path in [STORAGE_DIR, IMAGES_DIR]:
    os.makedirs(path, exist_ok=True)

_GENERIC_MEDIA_ROOT = (STATIC_DIR / "memory" / "generic").resolve()


def _resolved_train_path(image_path: str) -> Optional[str]:
    if not image_path:
        return None
    resolved = image_path
    if not os.path.isabs(resolved) and not os.path.isfile(resolved):
        cand = media_path(resolved)
        if cand.is_file():
            resolved = str(cand)
    if not os.path.isfile(resolved):
        return None
    return resolved


def _path_is_under_generic_library(resolved_os_path: str) -> bool:
    """Generic catalog images are never embedded — quizzes use titles/manifest only."""
    try:
        p = Path(resolved_os_path).resolve()
        return p == _GENERIC_MEDIA_ROOT or _GENERIC_MEDIA_ROOT in p.parents
    except (OSError, ValueError):
        return False


def train_faces_from_paths(
    name: str,
    relationship: str,
    image_paths: list[str],
) -> dict:
    """Run DeepFace on existing image files, save embedding + display image.

    Used by ``POST /ai/train`` (temp files) and by **personal** memory upload
    (``static/memory/personal/``). **Never** call this for generic library paths
    under ``static/memory/generic/`` — those are quiz-only (titles from DB /
    manifest), not relative face recognition.
    """
    resolved_ok: List[str] = []
    for image_path in image_paths:
        r = _resolved_train_path(image_path)
        if r:
            resolved_ok.append(r)

    if any(_path_is_under_generic_library(r) for r in resolved_ok):
        raise HTTPException(
            status_code=400,
            detail=(
                "Face embedding training is only for personal relative photos. "
                "Generic library images are not trained (quiz uses titles / manifest)."
            ),
        )

    all_embeddings: list = []
    best_image_saved = False

    clean_name = name.strip().replace(" ", "_").lower()
    clean_rel = relationship.strip().replace(" ", "_").lower()

    for resolved in resolved_ok:
        try:
            results = DeepFace.represent(
                img_path=resolved,
                model_name="Facenet",
                enforce_detection=True,
                detector_backend="retinaface",
            )

            if results:
                all_embeddings.append(results[0]["embedding"])

                if not best_image_saved:
                    final_img_path = os.path.join(IMAGES_DIR, f"{clean_name}.jpg")
                    shutil.copyfile(resolved, final_img_path)
                    best_image_saved = True
                    print(f"Display image saved for {clean_name}")

        except Exception as e:
            print(f"Skipping {resolved}: {e}")

    if not all_embeddings:
        raise HTTPException(
            status_code=400,
            detail="AI could not detect any faces. Use clearer photos.",
        )

    avg_embedding = np.mean(all_embeddings, axis=0)
    np.save(
        os.path.join(STORAGE_DIR, f"{clean_name}_{clean_rel}.npy"),
        avg_embedding,
    )

    return {
        "status": "Success",
        "message": f"Training complete for {name}",
        "images_processed": len(all_embeddings),
    }


def predict_face_name_from_image_path(abs_image_path: str) -> tuple[Optional[str], float]:
    """Best-effort name from ``memory_bank`` embeddings (same layout as ``/ai/test``)."""
    if not abs_image_path or not os.path.isfile(abs_image_path):
        return None, 0.0
    if not os.path.isdir(STORAGE_DIR):
        return None, 0.0
    npy_files = [f for f in os.listdir(STORAGE_DIR) if f.endswith(".npy")]
    if not npy_files:
        return None, 0.0
    try:
        test_results = DeepFace.represent(
            img_path=abs_image_path,
            model_name="Facenet",
            enforce_detection=False,
            detector_backend="retinaface",
            align=True,
        )
    except Exception:
        return None, 0.0
    if not test_results:
        return None, 0.0
    face = test_results[0]
    test_embedding = np.array(face["embedding"])
    best_match: Optional[str] = None
    highest_similarity = -1.0
    for filename in npy_files:
        try:
            stored = np.load(os.path.join(STORAGE_DIR, filename))
            similarity = float(
                np.dot(test_embedding, stored)
                / (
                    np.linalg.norm(test_embedding) * np.linalg.norm(stored) + 1e-9
                ),
            )
            if similarity > highest_similarity:
                highest_similarity = similarity
                best_match = filename
        except Exception:
            continue
    if best_match is None or highest_similarity < 0.55:
        return None, float(highest_similarity)
    stem = best_match.replace(".npy", "")
    if "_" in stem:
        name_part, _rel = stem.rsplit("_", 1)
    else:
        name_part = stem
    display = name_part.replace("_", " ").strip().title()
    return display, float(highest_similarity)


@router.post("/train")
async def train_person(
    name: str = Form(...),
    relationship: str = Form(...),
    files: list[UploadFile] = File(...),
):
    temp_paths: list[str] = []
    try:
        for file in files:
            safe = f"temp_{uuid.uuid4().hex}_{file.filename or 'img'}"
            contents = await file.read()
            with open(safe, "wb") as f:
                f.write(contents)
            temp_paths.append(safe)
        return train_faces_from_paths(name, relationship, temp_paths)
    finally:
        for p in temp_paths:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


# --- Naya Route: Trained Logon ki list dekhne ke liye ---
@router.get("/list-trained")
async def list_trained():
    files = [
        f.replace(".npy", "") for f in os.listdir(STORAGE_DIR) if f.endswith(".npy")
    ]
    return {"people": files}


# 1. Simple Image Quiz Route
@router.get("/get-memory-quiz")
async def get_memory_quiz():
    if not os.path.exists(IMAGES_DIR):
        raise HTTPException(status_code=404, detail="No training data found.")

    all_images = [f for f in os.listdir(IMAGES_DIR) if f.endswith(".jpg")]
    if len(all_images) < 1:
        raise HTTPException(
            status_code=404,
            detail="Kam az kam ek person train karein.",
        )

    # Ek sahi photo select karein
    correct_img = random.choice(all_images)
    correct_name = correct_img.replace(".jpg", "").replace("_", " ").title()

    # Options banana (1 correct + 3 wrong)
    all_names = [f.replace(".jpg", "").replace("_", " ").title() for f in all_images]
    wrong_options = [n for n in all_names if n != correct_name]

    # Agar trained log kam hain toh dummy options add karein
    dummy = ["Doctor", "Padosi", "Dost", "Nurse"]
    random.shuffle(dummy)

    final_options = [correct_name] + (wrong_options + dummy)[:3]
    random.shuffle(final_options)

    return {
        "image_url": f"http://127.0.0.1:8000/static/display_images/{correct_img}",
        "correct_answer": correct_name,
        "options": final_options,
    }


# 2. Group Photo Identification (Placeholder logic)
@router.get("/get-group-quiz")
async def get_group_quiz():
    # Abhi ke liye hum static bhej rahe hain, baad mein AI box wala logic dalain ge
    return {
        "group_image": "http://127.0.0.1:8000/static/group_sample.jpg",
        "question": "Is tasveer mein Sherry kaun sa shakhs hai?",
        "options": ["Person 1 (Left)", "Person 2 (Right)", "Person 3 (Middle)"],
        "correct": "Person 1 (Left)",
    }
