# Database Migration Instructions

## Problem
The `patients` table is missing the `qr_token` column that is required by the application.

## Solution

### Option 1: Run the Python Migration Script (Recommended)

Make sure you're in the backend directory and your virtual environment is activated, then run:

```bash
python migrations/run_migration.py
```

### Option 2: Run SQL Directly in PostgreSQL

Connect to your PostgreSQL database and run:

```sql
ALTER TABLE patients ADD COLUMN IF NOT EXISTS qr_token VARCHAR(255);
```

You can run this using:
- psql command line: `psql -U postgres -d fyp -c "ALTER TABLE patients ADD COLUMN IF NOT EXISTS qr_token VARCHAR(255);"`
- pgAdmin (right-click on patients table → Scripts → CREATE Script)
- Any PostgreSQL client

### Option 3: Automatic Migration on Startup

The application will attempt to add the column automatically when it starts (if the code is updated).
