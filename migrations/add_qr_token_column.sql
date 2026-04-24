-- Migration script to add qr_token column to patients table
-- Run this script in your PostgreSQL database

ALTER TABLE patients ADD COLUMN IF NOT EXISTS qr_token VARCHAR(255);

-- Make it nullable initially if needed, or add a default value
-- ALTER TABLE patients ALTER COLUMN qr_token SET DEFAULT '';
