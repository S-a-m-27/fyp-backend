from fastapi import APIRouter, UploadFile, File, HTTPException
from deepface import DeepFace
import numpy as np
import os
import cv2
import tempfile

# Router definition (Hamesha imports ke foran baad aur routes se pehle)
router = APIRouter(prefix="/ai", tags=["AI Testing"])

STORAGE_DIR = "memory_bank"


@router.post("/test")
async def test_group_photo(file: UploadFile = File(...)):
    temp_path = None
    try:
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return {"error": "Invalid image format", "people": []}

        # Save image to temporary file for DeepFace
        temp_path = f"temp_{file.filename}"
        cv2.imwrite(temp_path, img)

        # 1. Faces detect karein
        try:
            test_results = DeepFace.represent(
                img_path=temp_path,
                model_name='Facenet',
                enforce_detection=False,
                detector_backend='retinaface',
                align=True
            )
        except Exception as e:
            return {"error": f"Face detection failed: {str(e)}", "people": []}

        # Agar koi face detect nahi hua
        if not test_results or len(test_results) == 0:
            return {"error": "Photo mein koi chehra detect nahi hua. Clear photo upload karein.", "people": []}

        # 2. SABSE ZAROORI STEP: Left-to-Right Sorting
        # facial_area['x'] ki value jiski sabse kam hogi wo sabse left par hoga
        test_results = sorted(test_results, key=lambda x: x["facial_area"]["x"])

        final_identities = []
        assigned_faces = set()

        # Check if memory bank exists and has files
        if not os.path.exists(STORAGE_DIR):
            return {"error": "Memory bank folder not found.", "people": []}
        
        npy_files = [f for f in os.listdir(STORAGE_DIR) if f.endswith(".npy")]
        if not npy_files:
            return {"error": "Memory bank empty hai. Pehle AI training karein.", "people": []}

        # Ab loop sorted faces par chalay ga
        for idx, face in enumerate(test_results):
            # Face confidence check - agar face detect nahi hua to skip
            face_confidence = face.get("face_confidence", 1.0)
            if face_confidence < 0.5:
                continue

            test_embedding = np.array(face["embedding"])
            best_match = None
            highest_similarity = -1

            # Memory bank se sab embeddings compare karein
            for filename in os.listdir(STORAGE_DIR):
                if filename.endswith(".npy"):
                    try:
                        stored_embedding = np.load(os.path.join(STORAGE_DIR, filename))
                        
                        # Cosine similarity calculate karein
                        similarity = np.dot(test_embedding, stored_embedding) / (
                                np.linalg.norm(test_embedding) * np.linalg.norm(stored_embedding)
                        )

                        if similarity > highest_similarity:
                            highest_similarity = similarity
                            best_match = filename
                    except Exception as e:
                        print(f"Error loading {filename}: {e}")
                        continue

            # Threshold logic - 0.60 se zyada similarity chahiye
            if highest_similarity > 0.60 and best_match:
                parts = best_match.replace(".npy", "").split("_")
                name = parts[0].capitalize()
                relationship = parts[1].capitalize() if len(parts) > 1 else "Relative"

                # Duplicate check - agar same person pehle se identify ho chuka hai
                if name not in assigned_faces:
                    final_identities.append({
                        "label": f"Person {idx + 1}",
                        "name": name,
                        "relationship": relationship,
                        "confidence": round(float(highest_similarity) * 100, 2)
                    })
                    assigned_faces.add(name)
                else:
                    # Agar duplicate hai to bhi add karein but "Unknown Person" ke taur par
                    final_identities.append({
                        "label": f"Person {idx + 1}",
                        "name": "Unknown",
                        "relationship": "N/A",
                        "confidence": 0
                    })
            else:
                # Memory bank mein nahi mila - Unknown Person
                final_identities.append({
                    "label": f"Person {idx + 1}",
                    "name": "Unknown",
                    "relationship": "N/A",
                    "confidence": 0
                })

        return {"people": final_identities}

    except Exception as e:
        return {"error": str(e), "people": []}
    finally:
        # Clean up temporary file
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)