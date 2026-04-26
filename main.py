import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

import models
from database import engine
from routers import (
    memory, auth_signup, auth_login, 
    ai_training, ai_testing, ai_video, 
    stats_router, patient_mgmt
)

# 1. Create Database Tables
models.Base.metadata.create_all(bind=engine)

# 2. Database Migrations (Check for missing columns)
try:
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS age INTEGER DEFAULT 0;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS relation VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS dob VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS location VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS medical_info TEXT;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS caretaker_email VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS qr_token VARCHAR(255);
        """))
        conn.commit()
        print("✓ Database migration: All missing columns checked/added.")
except Exception as e:
    print(f"⚠ Migration note: {e}")

# 3. Initialize FastAPI App
app = FastAPI(title="AI Memory Jogger API")

# 4. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Your React App URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Static Files Mounting
# This allows React to access images via http://127.0.0.1:8000/static/...
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# 6. Include Routers
app.include_router(auth_signup.router)
app.include_router(auth_login.router)
app.include_router(memory.router) # Your updated memory.py with Quiz/Training
app.include_router(ai_training.router)
app.include_router(ai_testing.router)
app.include_router(ai_video.router)
app.include_router(patient_mgmt.router)
app.include_router(stats_router.router, prefix="/ai")

@app.get("/")
def root():
    return {"message": "AI Memory Jogger Backend is Running"}