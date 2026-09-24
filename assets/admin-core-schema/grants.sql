-- Least-privilege grants for the application role (PostgreSQL 14+; run with psql 11+,
-- which added the :{?variable} existence test used below).
--
-- The regulated profile requires this: the append-only triggers in postgres.sql
-- stop ordinary application paths, and this file removes the privilege to attempt
-- the mutation at all. It is shared by every variant (Prisma, Drizzle, Django,
-- Laravel, Rails): run it once after the migration that creates the tables.
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v app_role=admin_app [-v audit_admin_role=audit_admin] [-v schema=admin_core] \
--        -f grants.sql
--
-- app_role         the role the console connects as. Must already exist and must
--                  NOT own the tables (an owner can disable triggers and grant itself
--                  anything back).
-- audit_admin_role optional; the only role that may call scrub_audit_payload().
-- schema           where the tables live; admin_core for postgres.sql and Drizzle,
--                  usually public for Prisma, Django, Laravel and Rails.
--
-- This grants only what the append-only tables need. Grants on the remaining
-- tables (SELECT/INSERT/UPDATE on the mutable ones) are project-specific.
--
-- A missing app_role is an error (psql exits 3), never a silent success: a deploy
-- script must not report grants that were not applied. The script stops at its first
-- error even when the caller forgot -v ON_ERROR_STOP=1.

\set ON_ERROR_STOP on
\if :{?app_role}
\else
    DO $$ BEGIN RAISE EXCEPTION 'grants.sql: pass -v app_role=<role>; no grants were applied'; END $$;
\endif
\if :{?schema}
\else
    \set schema admin_core
\endif

BEGIN;

GRANT USAGE ON SCHEMA :"schema" TO :"app_role";

-- Append-only: the application inserts and reads, and can never rewrite, delete or
-- truncate. audit_event and approval_decision are also protected by triggers;
-- config_change and admin_job_failure are immutable by rule (see the model doc).
REVOKE ALL ON
    :"schema".audit_event,
    :"schema".approval_decision,
    :"schema".config_change,
    :"schema".admin_job_failure
FROM PUBLIC, :"app_role";

GRANT SELECT, INSERT ON
    :"schema".audit_event,
    :"schema".approval_decision,
    :"schema".config_change,
    :"schema".admin_job_failure
TO :"app_role";

-- bigserial ids need the sequences.
GRANT USAGE ON SEQUENCE
    :"schema".audit_event_id_seq,
    :"schema".approval_decision_id_seq,
    :"schema".config_change_id_seq,
    :"schema".admin_job_failure_id_seq
TO :"app_role";

-- The integrity checker is read-only and safe for the application role.
GRANT EXECUTE ON FUNCTION :"schema".verify_audit_chain(text) TO :"app_role";

-- Payload scrub is a redaction act, not an application path: only the named role.
REVOKE ALL ON FUNCTION :"schema".scrub_audit_payload(bigint, text, text, bigint)
    FROM PUBLIC, :"app_role";

\if :{?audit_admin_role}
    GRANT USAGE ON SCHEMA :"schema" TO :"audit_admin_role";
    GRANT EXECUTE ON FUNCTION :"schema".scrub_audit_payload(bigint, text, text, bigint)
        TO :"audit_admin_role";
\endif

COMMIT;
