-- ============================================================================
-- BusinessLayer — manual lead seed (PostgreSQL)
-- ============================================================================
-- The application creates the SCHEMA automatically on boot (SQLAlchemy
-- create_all from the SQLModel models). This script only inserts DATA.
--
-- Run it AFTER the service has started at least once (so the `leads` table
-- exists), e.g.:
--     psql "postgresql://USER:PASS@HOST:5432/aegis" -f seed_leads.sql
--   or, inside the k8s pod:
--     kubectl exec -it <postgres-pod> -- psql -U <user> -d aegis -f /tmp/seed_leads.sql
--
-- For a clean reset: run clear_data.sql first, then this.
--
-- Idempotent: ON CONFLICT (lead_id) DO NOTHING — safe to re-run; existing
-- leads (and any analysis already folded into them) are left untouched.
--
-- We set the columns the app reads as non-null (facts/open_concerns/sent_items,
-- the integer counters, timestamps) explicitly, since SQLModel's defaults are
-- applied in Python (ORM inserts), not as DB-level defaults. Edit / add rows as
-- needed — only lead_id, full_name, phone_e164 are essential; consent_call /
-- consent_whatsapp gate outbound calls / WhatsApp.
-- ============================================================================

INSERT INTO leads (
    lead_id, full_name, email, phone_e164, source, language_preference,
    consent_call, consent_whatsapp, status,
    facts, open_concerns, sent_items,
    interest, confidence, call_attempts, version,
    created_at, updated_at
) VALUES
    ('test-lead-1', 'Ayush',        'ayush.agarwal@centific.com',   '+917220054290', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-2', 'Jitendra',     'jitendra.yadav1@centific.com', '+919610373417', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-3', 'Tirupati Rao', 'tirupati.rao@centific.com',    '+917219134567', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-4', 'Ramesh',       'ramesh@centific.com',          '+916301857629', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-5', 'Vaishnavi',    'vaishnavi@centific.com',       '+917993617356', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-6', 'Ravi',         'ravi@centific.com',            '+918939803458', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-7', 'Venkat',       'venkat@gmail.com',             '+919642300099', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc')),

    ('test-lead-8', 'Ganesh',       'ganesh@gmail.com',             '+919962539018', 'site_form', 'en',
        true, true, 'new', '{}'::json, '[]'::json, '[]'::json, 0, 0, 0, 0,
        (now() at time zone 'utc'), (now() at time zone 'utc'))
ON CONFLICT (lead_id) DO NOTHING;
