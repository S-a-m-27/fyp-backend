"""
Migration script to add missing columns to patients table
Run this script once to update your database schema

Usage: python -m migrations.run_migration
Or: cd backend && python migrations/run_migration.py
"""
import sys
import os

# Add parent directory to path to import database module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database import engine

def run_migration():
    try:
        with engine.connect() as conn:
            # Add qr_token column if it doesn't exist
            conn.execute(text("""
                ALTER TABLE patients 
                ADD COLUMN IF NOT EXISTS qr_token VARCHAR(255);
            """))
            conn.commit()
            print("✓ Successfully added qr_token column to patients table")
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    run_migration()
