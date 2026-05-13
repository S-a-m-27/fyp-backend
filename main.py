import os
from pathlib import Path


def _parse_dotenv_file(path: Path) -> dict:
    """Return KEY->value from one ``.env`` file (values stripped; quotes optional)."""
    out = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def _load_env_files() -> None:
    """Merge ``.env`` files into ``os.environ``.

    Reads ``backend/backend/.env`` then ``backend/.env`` (later wins on duplicate keys).
    ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD`` always come from merged files so IDE/shell stubs
    cannot block the real credentials. Other keys only apply if not already set externally.
    """
    inner = Path(__file__).resolve().parent / ".env"
    outer = Path(__file__).resolve().parent.parent / ".env"
    merged = {}
    merged.update(_parse_dotenv_file(inner))
    merged.update(_parse_dotenv_file(outer))
    for key, val in merged.items():
        if key in ("ADMIN_EMAIL", "ADMIN_PASSWORD"):
            os.environ[key] = val
        elif key not in os.environ:
            os.environ[key] = val


_load_env_files()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

import models
from app_paths import STATIC_DIR
from database import engine
from routers import (
    memory,
    memory_catalog,
    memory_personal,
    memory_quiz_caretaker,
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

            CREATE TABLE IF NOT EXISTS authorized_users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                role VARCHAR(32) NOT NULL DEFAULT 'admin',
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_authorized_users_email ON authorized_users(email);

            CREATE TABLE IF NOT EXISTS admin_auth_sessions (
                id SERIAL PRIMARY KEY,
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                authorized_user_id INTEGER NOT NULL REFERENCES authorized_users(id) ON DELETE CASCADE,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_admin_auth_sessions_user ON admin_auth_sessions(authorized_user_id);
            CREATE INDEX IF NOT EXISTS ix_admin_auth_sessions_expires ON admin_auth_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS patient_quiz_memory_items (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                memory_item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(patient_id, memory_item_id)
            );
            CREATE INDEX IF NOT EXISTS ix_patient_quiz_memory_patient
                ON patient_quiz_memory_items(patient_id);

            CREATE TABLE IF NOT EXISTS caretaker_defined_quizzes (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
                caretaker_email VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS ix_caretaker_defined_quizzes_email
                ON caretaker_defined_quizzes(caretaker_email);

            CREATE TABLE IF NOT EXISTS caretaker_defined_quiz_questions (
                id SERIAL PRIMARY KEY,
                quiz_id INTEGER NOT NULL REFERENCES caretaker_defined_quizzes(id) ON DELETE CASCADE,
                slot INTEGER NOT NULL,
                memory_item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
                wrong_option_1 VARCHAR(500) NOT NULL,
                wrong_option_2 VARCHAR(500) NOT NULL,
                wrong_option_3 VARCHAR(500) NOT NULL,
                UNIQUE(quiz_id, slot)
            );
            CREATE INDEX IF NOT EXISTS ix_defined_quiz_questions_quiz ON caretaker_defined_quiz_questions(quiz_id);

            ALTER TABLE caretaker_defined_quiz_questions ADD COLUMN IF NOT EXISTS mc_options_json TEXT;
            ALTER TABLE caretaker_defined_quiz_questions ADD COLUMN IF NOT EXISTS correct_option_index INTEGER;

            CREATE TABLE IF NOT EXISTS patient_dismissed_library_memories (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                memory_item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(patient_id, memory_item_id)
            );
            CREATE INDEX IF NOT EXISTS ix_patient_dismissed_library_patient
                ON patient_dismissed_library_memories(patient_id);

            CREATE TABLE IF NOT EXISTS patient_quiz_attempts (
                id SERIAL PRIMARY KEY,
                patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                quiz_format VARCHAR(40) NOT NULL,
                correct_count INTEGER NOT NULL,
                wrong_count INTEGER NOT NULL DEFAULT 0,
                target_score INTEGER NOT NULL,
                passed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_patient_quiz_attempts_patient
                ON patient_quiz_attempts(patient_id);
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
app.include_router(memory_quiz_caretaker.router)
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


def _bootstrap_admin_from_env():
    from database import SessionLocal
    from data.admin_auth import sync_admin_user_from_env

    db = SessionLocal()
    try:
        sync_admin_user_from_env(db)
    finally:
        db.close()


_bootstrap_admin_from_env()


@app.get("/admin/dashboard")
def admin_dashboard_page():
    """Single-page admin console (wallet, approvals, notifications)."""
    page = STATIC_DIR / "admin" / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Admin dashboard file missing")
    return FileResponse(page, media_type="text/html; charset=utf-8")


@app.get("/")
def root():
    return {
        "message": "AI Memory Jogger Backend is Running",
        "admin_dashboard": "/admin/dashboard",
    }
