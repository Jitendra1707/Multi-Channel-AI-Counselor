-- ============================================================================
-- BusinessLayer — clear ALL data (PostgreSQL)
-- ============================================================================
-- Wipes every row from leads, sessions and tasks. The SCHEMA (tables/indexes)
-- is kept — so the app keeps working and you can re-seed immediately.
--
-- WARNING: irreversible. Removes all leads, conversation sessions, analysis,
-- and queued tasks.
--
--   psql "postgresql://USER:PASS@HOST:5432/DBNAME" -f clear_data.sql
--
-- (No FK constraints are defined, so order doesn't matter; RESTART IDENTITY is
-- harmless even though the PKs aren't sequences.)
-- ============================================================================

TRUNCATE TABLE tasks, sessions, leads RESTART IDENTITY;

-- To drop the schema ENTIRELY instead (forces the app to re-create it on next
-- boot, or re-run schema.sql), use this instead of the TRUNCATE above:
--   DROP TABLE IF EXISTS tasks, sessions, leads CASCADE;
