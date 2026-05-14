from fastapi import APIRouter, UploadFile, File, HTTPException
import cv2
import os
import numpy as np
from deepface import DeepFace

router = APIRouter(prefix="/ai", tags=["Video Recognition"])
STORAGE_DIR = "memory_bank"


@router.post("/test-video")
async def test_video(file: UploadFile = File(...)):
    video_path = f"temp_{file.filename}"
    with open(video_path, "wb") as f:
        f.write(await file.read())

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    # Har 0.5 second ka frame check karein taake koi miss na ho
    step = int(fps / 2) if fps > 0 else 15

    unique_people = set()
    frame_idx = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret: break

        if frame_idx % step == 0:
            try:
                # Save frame to temporary file for DeepFace
                temp_frame_path = f"temp_frame_{frame_idx}.jpg"
                cv2.imwrite(temp_frame_path, frame)
                
                # 1. retinaface use karein (Niqab/Blur mein behtar hai)
                results = DeepFace.represent(
                    img_path=temp_frame_path,
                    model_name='Facenet',
                    enforce_detection=False,
                    detector_backend='retinaface',  # Accuracy barhane ke liye
                    align=True
                )
                
                # Clean up temporary frame file
                if os.path.exists(temp_frame_path):
                    os.remove(temp_frame_path)

                for face in results:
                    test_embedding = np.array(face["embedding"])

                    for filename in os.listdir(STORAGE_DIR):
                        if filename.endswith(".npy"):
                            stored_embedding = np.load(os.path.join(STORAGE_DIR, filename))
                            similarity = np.dot(test_embedding, stored_embedding) / (
                                    np.linalg.norm(test_embedding) * np.linalg.norm(stored_embedding)
                            )

                            # 2. Video ke liye threshold 0.50-0.55 rakhein
                            if similarity > 0.60:
                                stem = filename.replace(".npy", "")
                                name = stem.replace("_", " ").strip().title()
                                unique_people.add(name)
                                print(f"Found {name} in video with {similarity} score")
            except Exception as e:
                print(f"Frame skip: {e}")
                continue
        frame_idx += 1

    cap.release()
    if os.path.exists(video_path): os.remove(video_path)

    return {"summary": list(unique_people)}