"""
Migration: add hint_1, hint_2, hint_3 columns to memory_items table.

Usage (from backend/ folder):
    python migrations/run_hints_migration.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text
from database import engine


def column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(
        text("SELECT COUNT(*) FROM pragma_table_info(:t) WHERE name = :c"),
        {"t": table, "c": column},
    )
    row = result.fetchone()
    return bool(row and row[0] > 0)


def run_migration():
    with engine.connect() as conn:
        for col in ("hint_1", "hint_2", "hint_3"):
            try:
                if column_exists(conn, "memory_items", col):
                    print(f"  column '{col}' already exists — skipped")
                    continue
                conn.execute(
                    text(f"ALTER TABLE memory_items ADD COLUMN {col} VARCHAR(500)")
                )
                conn.commit()
                print(f"  ✓ added column '{col}' to memory_items")
            except Exception as exc:
                print(f"  ✗ could not add '{col}': {exc}")

    print("Migration complete.")


if __name__ == "__main__":
    run_migration()
