# routers/stats_router.py
from fastapi import APIRouter
import os

router = APIRouter()

# Memory bank ka path (Apne folder structure ke hisab se set karein)
STORAGE_DIR = "memory_bank"


@router.get("/stats")  # React mein ye "/ai/stats" ban jayega agar prefix "/ai" hua
async def get_real_stats():
    # 1. Check karein agar folder exist karta hai
    if not os.path.exists(STORAGE_DIR):
        return {"memories": 0, "patients": 0, "active": 0, "sessions": 0}

    # 2. Asli files scan karein (.npy files count)
    files = [f for f in os.listdir(STORAGE_DIR) if f.endswith(".npy")]

    # 3. Unique patients nikalain (File name: "Ubaid_1.npy" -> split karke "Ubaid" nikalega)
    unique_patients = set([f.split('_')[0] for f in files])

    return {
        "memories": len(files),
        "patients": len(unique_patients),
        "active": 0,  # Baad mein database se history la sakte hain
        "sessions": 0  # Baad mein database se history la sakte hain
    }