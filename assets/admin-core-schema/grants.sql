-- Least-privilege grants for the application role (PostgreSQL 14+; run with psql 11+,
-- which added the :{?variable} existence test used below).
--
-- The regulated profile requires this: the triggers in postgres.sql stop ordinary
-- application paths, and this file removes the privilege to attempt the mutation at
-- all. It is shared by every variant (Prisma, Drizzle, Django, Laravel, Rails): run it
-- once after the migration that creates the tables.
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v app_role=admin_app [-v audit_admin_role=audit_admin] [-v schema=admin_core] \
--        -f grants.sql
--
-- app_role         the role the console connects as. Must already exist, must not be a
--                  superuser, must not own (or be a member of the owner of) the tables
--                  or functions (an owner can disable triggers and grant itself anything
--                  back), and must not be able to CREATE in the schema. The script
--                  checks all three and stops if any holds.
-- audit_admin_role optional; the only role that may call scrub_audit_payload().
-- schema           where the tables live; admin_core for postgres.sql and Drizzle,
--                  usually public for Prisma, Django, Laravel and Rails.
--
-- What it covers: the append-only tables (read and insert only), the three tables whose
-- rows the triggers keep as written (approval_request, admin_actor_role,
-- impersonation_session: read, insert, and UPDATE of their lifecycle columns only, no
-- DELETE), and SELECT on admin_actor, which the audit trigger reads. INSERT is granted
-- column by column and never on id, so a writer cannot pick an id ahead of the sequence
-- and make every later insert collide. If you add columns to these tables, add them
-- here. Grants on the remaining tables are project-specific.
--
-- A missing app_role is an error (psql exits 3), never a silent success: a deploy
-- script must not report grants that were not applied. The script stops at its first
-- error even when the caller forgot -v ON_ERROR_STOP=1, and applies nothing then.

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

-- The application role must not be able to switch the controls off.
SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = :'schema'
       AND c.relname IN ('audit_event', 'approval_decision', 'approval_request', 'admin_actor',
                         'admin_actor_role', 'impersonation_session', 'config_change', 'admin_job_failure')
       AND pg_catalog.pg_has_role(:'app_role', c.relowner, 'MEMBER')
    UNION ALL
    SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = :'schema'
       AND p.proname IN ('audit_event_hash_input', 'audit_event_chain_link', 'verify_audit_chain',
                         'scrub_audit_payload', 'enforce_separation_of_duties',
                         'approval_request_guard_update', 'approval_request_guard_state',
                         'deny_audit_mutation', 'audit_event_guard_update', 'admin_actor_role_guard',
                         'impersonation_session_guard_update')
       AND pg_catalog.pg_has_role(:'app_role', p.proowner, 'MEMBER')
) AS aw_app_owns \gset
\if :aw_app_owns
    \echo grants.sql: :app_role is a superuser, or owns (or is a member of the owner of) the tables or functions in :schema
    DO $$ BEGIN RAISE EXCEPTION 'grants.sql: the application role can disable the triggers; no grants were applied'; END $$;
\endif

REVOKE CREATE ON SCHEMA :"schema" FROM :"app_role";
SELECT pg_catalog.has_schema_privilege(:'app_role', :'schema', 'CREATE') AS aw_app_creates \gset
\if :aw_app_creates
    \echo grants.sql: :app_role can still CREATE in schema :schema (through PUBLIC or a role it belongs to); run REVOKE CREATE ON SCHEMA :schema FROM PUBLIC (and from that role)
    DO $$ BEGIN RAISE EXCEPTION 'grants.sql: the application role can create objects in the schema; no grants were applied'; END $$;
\endif

GRANT USAGE ON SCHEMA :"schema" TO :"app_role";

REVOKE ALL ON
    :"schema".audit_event,
    :"schema".approval_decision,
    :"schema".config_change,
    :"schema".admin_job_failure,
    :"schema".approval_request,
    :"schema".admin_actor_role,
    :"schema".impersonation_session
FROM PUBLIC, :"app_role";

-- Append-only: the application inserts and reads, and can never rewrite, delete or
-- truncate. audit_event and approval_decision are also protected by triggers;
-- config_change and admin_job_failure are immutable by rule (see the model doc), and
-- admin_job_failure's job key is ON DELETE RESTRICT, so deleting a job cannot take
-- its failures with it. The chain columns, recorded_at, actor_subject, the payload
-- hashes and decided_at are in the lists because ORMs send every column; the triggers
-- overwrite whatever is sent.
GRANT SELECT ON
    :"schema".audit_event,
    :"schema".approval_decision,
    :"schema".config_change,
    :"schema".admin_job_failure,
    :"schema".approval_request,
    :"schema".admin_actor_role,
    :"schema".impersonation_session,
    :"schema".admin_actor
TO :"app_role";

GRANT INSERT (event_uuid, chain_id, chain_seq, occurred_at, recorded_at, actor_id, actor_kind,
              actor_subject, effective_actor_id, impersonation_session_id, tenant_id, environment,
              source_ip, user_agent, action, target_type, target_id, reason, ticket_ref,
              approval_request_id, request_id, correlation_id, idempotency_key, result, error_code,
              before_state, after_state, before_hash, after_hash, redaction_policy, policy_version,
              prev_hash, row_hash, hash_algorithm)
    ON :"schema".audit_event TO :"app_role";
GRANT INSERT (request_id, approver_id, decision, reason, decided_at)
    ON :"schema".approval_decision TO :"app_role";
GRANT INSERT (target_table, target_key, environment, tenant_id, before_value, after_value, changed_by,
              reason, changed_at, audit_event_id)
    ON :"schema".config_change TO :"app_role";
GRANT INSERT (job_id, target_type, target_id, error_code, error_detail, failed_at)
    ON :"schema".admin_job_failure TO :"app_role";

-- Kept as written by triggers; only the lifecycle columns are updatable, nothing is deleted.
GRANT INSERT (request_uuid, command_key, payload, payload_hash, target_type, target_id, tenant_id,
              requested_by, requested_at, reason, required_approvals, state, expires_at, executed_at,
              execution_result)
    ON :"schema".approval_request TO :"app_role";
GRANT UPDATE (state, executed_at, execution_result) ON :"schema".approval_request TO :"app_role";
GRANT INSERT (actor_id, role_id, scope, scope_tenant_id, starts_at, expires_at, granted_by,
              granted_reason, ticket_ref, granted_at, revoked_at, revoked_by, revoke_reason)
    ON :"schema".admin_actor_role TO :"app_role";
GRANT UPDATE (revoked_at, revoked_by, revoke_reason) ON :"schema".admin_actor_role TO :"app_role";
GRANT INSERT (session_uuid, real_actor_id, target_subject_id, target_tenant_id, reason, ticket_ref,
              read_only, scope_restrictions, started_at, expires_at, ended_at, ended_reason, revoked_by)
    ON :"schema".impersonation_session TO :"app_role";
GRANT UPDATE (ended_at, ended_reason, revoked_by) ON :"schema".impersonation_session TO :"app_role";

-- bigserial ids need the sequences (the column default draws the id; the writer never names it).
GRANT USAGE ON SEQUENCE
    :"schema".audit_event_id_seq,
    :"schema".approval_decision_id_seq,
    :"schema".config_change_id_seq,
    :"schema".admin_job_failure_id_seq,
    :"schema".approval_request_id_seq,
    :"schema".admin_actor_role_id_seq,
    :"schema".impersonation_session_id_seq
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
