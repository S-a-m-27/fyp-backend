-- Migration: add hint_1, hint_2, hint_3 to memory_items
-- Safe to run multiple times (IF NOT EXISTS / column exists guard).
-- SQLite (development) and PostgreSQL both supported.

ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS hint_1 VARCHAR(500);
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS hint_2 VARCHAR(500);
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS hint_3 VARCHAR(500);
