import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

import models
from app_paths import STATIC_DIR
from database import engine
from routers import (
    memory,
    memory_catalog,
    memory_personal,
    auth_signup,
    auth_login,
    ai_training,
    ai_testing,
    ai_video,
    stats_router,
    patient_mgmt,
    dashboard,
    admin,
)

# 1. Create Database Tables
models.Base.metadata.create_all(bind=engine)

# 2. Lightweight migrations (add new columns if missing)
# PostgreSQL uses SERIAL / IDENTITY; SQLite uses AUTOINCREMENT (not valid in Postgres).
_mir_id_pk = (
    "id SERIAL PRIMARY KEY,"
    if engine.dialect.name == "postgresql"
    else "id INTEGER PRIMARY KEY AUTOINCREMENT,"
)

try:
    with engine.connect() as conn:
        conn.execute(text(
            f"""
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS age INTEGER DEFAULT 0;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS relation VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS dob VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS location VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS medical_info TEXT;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS interests TEXT;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS caretaker_email VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS qr_token VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS profession VARCHAR(255);
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS login_id VARCHAR(255);
            CREATE UNIQUE INDEX IF NOT EXISTS ix_patients_login_id ON patients (login_id);

            ALTER TABLE caretakers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();

            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS patient_id INTEGER;
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mode VARCHAR(255);
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS duration_minutes INTEGER DEFAULT 0;
            ALTER TABLE sessions ADD COLUMN IF NOT EXISTS started_at TIMESTAMP DEFAULT NOW();

            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS memory_type VARCHAR(255);
            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS year INTEGER;
            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS location VARCHAR(255);
            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS caretaker_email VARCHAR(255);
            CREATE INDEX IF NOT EXISTS ix_memory_items_caretaker_email
                ON memory_items (caretaker_email);

            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS related_person_name VARCHAR(255);
            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS related_person_relation VARCHAR(255);
            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS extra_file_paths TEXT;
            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS library_topic VARCHAR(80);
            CREATE INDEX IF NOT EXISTS ix_memory_items_library_topic
                ON memory_items (library_topic);

            ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS library_collection_slug VARCHAR(120);
            CREATE INDEX IF NOT EXISTS ix_memory_items_library_topic_collection
                ON memory_items (library_topic, library_collection_slug);

            ALTER TABLE patients ADD COLUMN IF NOT EXISTS memory_training_completed BOOLEAN DEFAULT FALSE;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS wellness_intro_completed BOOLEAN DEFAULT FALSE;
            ALTER TABLE patients ADD COLUMN IF NOT EXISTS training_sessions_completed INTEGER DEFAULT 0;

            CREATE TABLE IF NOT EXISTS memory_image_ratings (
                {_mir_id_pk}
                patient_id INTEGER NOT NULL REFERENCES patients(id),
                memory_item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
                stars INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(patient_id, memory_item_id)
            );
            CREATE INDEX IF NOT EXISTS ix_memory_image_ratings_memory
                ON memory_image_ratings(memory_item_id);

            UPDATE patients SET wellness_intro_completed = TRUE
                WHERE memory_training_completed IS TRUE
                AND (wellness_intro_completed IS NULL OR wellness_intro_completed IS NOT TRUE);
            UPDATE patients SET training_sessions_completed = 3
                WHERE memory_training_completed IS TRUE
                AND (training_sessions_completed IS NULL OR training_sessions_completed < 3);

            ALTER TABLE caretaker_bundle_purchases ADD COLUMN IF NOT EXISTS locked BOOLEAN;
            UPDATE caretaker_bundle_purchases SET locked = FALSE WHERE locked IS NULL;
            ALTER TABLE caretaker_bundle_purchases ALTER COLUMN locked SET DEFAULT TRUE;

            ALTER TABLE caretaker_bundle_purchases ADD COLUMN IF NOT EXISTS price_cents INTEGER;
            ALTER TABLE caretaker_bundle_purchases ADD COLUMN IF NOT EXISTS currency VARCHAR(8);

            CREATE TABLE IF NOT EXISTS admin_wallet_ledger (
                id SERIAL PRIMARY KEY,
                amount_cents INTEGER NOT NULL,
                currency VARCHAR(8) NOT NULL DEFAULT 'USD',
                purchase_id INTEGER REFERENCES caretaker_bundle_purchases(id) ON DELETE SET NULL,
                description VARCHAR(500),
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_admin_wallet_ledger_purchase
                ON admin_wallet_ledger(purchase_id);

            CREATE TABLE IF NOT EXISTS admin_notifications (
                id SERIAL PRIMARY KEY,
                purchase_id INTEGER REFERENCES caretaker_bundle_purchases(id) ON DELETE CASCADE,
                message TEXT NOT NULL,
                read_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_admin_notifications_read
                ON admin_notifications(read_at);
            """
        ))
        conn.commit()
        print("Database migration: All missing columns checked/added.")
except Exception as e:
    print(f"Migration note: {e}")

# 3. FastAPI App
app = FastAPI(title="AI Memory Jogger API")

# 4. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Static files (always relative to this package, not the shell cwd)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 6. Routers
app.include_router(auth_signup.router)
app.include_router(auth_login.router)
app.include_router(memory.router)
app.include_router(memory_catalog.router)
app.include_router(memory_personal.router)
app.include_router(ai_training.router)
app.include_router(ai_testing.router)
app.include_router(ai_video.router)
app.include_router(patient_mgmt.router)
app.include_router(stats_router.router, prefix="/ai")
app.include_router(dashboard.router)
app.include_router(admin.router)


# Generic library: DB rows only from on-disk images + per-bundle manifest.json (see memory.sync_disk_generic_library_to_db).
def _sync_generic_library_from_disk():
    from database import SessionLocal
    from routers.memory import sync_disk_generic_library_to_db

    db = SessionLocal()
    try:
        sync_disk_generic_library_to_db(db)
    finally:
        db.close()


_sync_generic_library_from_disk()


@app.get("/")
def root():
    return {"message": "AI Memory Jogger Backend is Running"}
