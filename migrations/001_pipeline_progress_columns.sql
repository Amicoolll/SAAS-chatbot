-- Run once on existing databases (new installs get columns via SQLAlchemy create_all on fresh DB only).
-- Postgres / SQLite compatible:

ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS drive_sync_progress_json TEXT;
ALTER TABLE pipeline_state ADD COLUMN IF NOT EXISTS index_progress_json TEXT;

-- SQLite before 3.35.0 has no IF NOT EXISTS on ADD COLUMN; then run:
-- ALTER TABLE pipeline_state ADD COLUMN drive_sync_progress_json TEXT;
-- ALTER TABLE pipeline_state ADD COLUMN index_progress_json TEXT;
