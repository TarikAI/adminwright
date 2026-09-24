-- Admin control-plane core schema (PostgreSQL 14+)
--
-- Reference implementation of the model in references/admin-data-model.md.
-- Adapt names to the project's conventions. An existing equivalent in the project
-- always wins over adding a parallel system.
--
-- Scope: the tables the admin console needs about ITSELF. Domain tables (users,
-- orders, payouts) are not here and are not replaced by these.
--
-- Read references/admin-data-model.md before adapting. The comments below explain
-- constraints whose reason is not obvious from the DDL alone.
--
-- Every CHECK is named <table>_<column>_check (or a descriptive name for the
-- multi-column ones) so the other five variants can carry the same names and a
-- drift test can compare them. grants.sql (same directory) is the least-privilege
-- shape for the application role; the regulated profile requires it.

BEGIN;

CREATE SCHEMA IF NOT EXISTS admin_core;
SET LOCAL search_path = admin_core, public;

-- admin_actor.email is citext (case-insensitive). Installed into public so that
-- roles with a different search_path still resolve its comparison operators.
CREATE EXTENSION IF NOT EXISTS citext WITH SCHEMA public;

-- ---------------------------------------------------------------------------
-- Actors, roles, permissions, grants
-- ---------------------------------------------------------------------------

-- Separate admin identity. If operator accounts share the product's lifecycle and
-- identity provider, drop this table and add is_privileged / auth_strength /
-- mfa_enrolled to the application user table instead.
-- Credentials are never stored here; auth_subject holds the IdP subject claim.
CREATE TABLE admin_actor (
    id              bigserial PRIMARY KEY,
    actor_uuid      uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    app_user_id     bigint      NULL,
    auth_subject    text        NOT NULL UNIQUE,
    display_name    text        NOT NULL,
    email           citext      NULL,
    kind            text        NOT NULL DEFAULT 'human'
                    CONSTRAINT admin_actor_kind_check
                    CHECK (kind IN ('human', 'service', 'agent')),
    auth_strength   text        NOT NULL DEFAULT 'password'
                    CONSTRAINT admin_actor_auth_strength_check
                    CHECK (auth_strength IN ('password', 'mfa', 'phishing-resistant', 'sso')),
    mfa_enrolled    boolean     NOT NULL DEFAULT false,
    is_active       boolean     NOT NULL DEFAULT true,
    deactivated_at  timestamptz NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN admin_actor.kind IS
    'Automation and autonomous agents are first-class subjects. They never inherit a human''s authority.';

CREATE TABLE admin_role (
    id            bigserial PRIMARY KEY,
    key           text NOT NULL UNIQUE,          -- matches manifest roles[].id
    name          text NOT NULL,
    description   text NOT NULL DEFAULT '',
    requires_mfa  boolean NOT NULL DEFAULT true, -- manifest roles[].mfaRequired
    max_scope     jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active     boolean NOT NULL DEFAULT true
);

-- The atom of authorization. Key it <resource>.<action> so it equals the manifest
-- capability id exactly; that equality is what makes capability -> permission ->
-- audit_event.action traceable.
-- Obligations live on the permission, not at the call site, so a new call site
-- cannot silently skip them.
CREATE TABLE admin_permission (
    id                bigserial PRIMARY KEY,
    key               text NOT NULL UNIQUE,      -- manifest capability.id
    resource          text NOT NULL,             -- manifest entity id
    action            text NOT NULL,
    risk              text NOT NULL DEFAULT 'low'
                      CONSTRAINT admin_permission_risk_check
                      CHECK (risk IN ('low', 'moderate', 'high', 'critical')),
    requires_reason   boolean NOT NULL DEFAULT false,
    requires_step_up  boolean NOT NULL DEFAULT false,
    requires_approval boolean NOT NULL DEFAULT false,
    description       text NOT NULL DEFAULT ''
);

CREATE TABLE admin_role_permission (
    role_id       bigint NOT NULL REFERENCES admin_role(id) ON DELETE CASCADE,
    permission_id bigint NOT NULL REFERENCES admin_permission(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- A grant, not a join row: it carries where it applies, when it expires, who
-- granted it and why, and how it was revoked.
CREATE TABLE admin_actor_role (
    id               bigserial PRIMARY KEY,
    actor_id         bigint      NOT NULL REFERENCES admin_actor(id),
    role_id          bigint      NOT NULL REFERENCES admin_role(id),
    scope            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    scope_tenant_id  text        NULL,           -- denormalized hot path
    starts_at        timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NULL,           -- NULL = permanent
    granted_by       bigint      NOT NULL REFERENCES admin_actor(id),
    granted_reason   text        NOT NULL,
    ticket_ref       text        NULL,
    granted_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz NULL,
    revoked_by       bigint      NULL REFERENCES admin_actor(id),
    revoke_reason    text        NULL,
    -- No self-granting. Privilege escalation by an operator on its own account is
    -- the failure this prevents.
    CONSTRAINT admin_actor_role_no_self_grant CHECK (granted_by <> actor_id),
    CONSTRAINT admin_actor_role_revocation_complete
        CHECK ((revoked_at IS NULL) = (revoked_by IS NULL)),
    CONSTRAINT admin_actor_role_window CHECK (expires_at IS NULL OR expires_at > starts_at)
);

-- One live grant of a role per actor per tenant scope. Duplicate live grants make
-- revocation unreliable: revoking one leaves the other in force. COALESCE makes a
-- global grant (NULL tenant) unique too; a plain unique index treats NULLs as distinct.
CREATE UNIQUE INDEX admin_actor_role_live_unique
    ON admin_actor_role (actor_id, role_id, COALESCE(scope_tenant_id, '*'))
    WHERE revoked_at IS NULL;

CREATE INDEX admin_actor_role_live_lookup
    ON admin_actor_role (actor_id)
    WHERE revoked_at IS NULL;

-- Which rules were in force when a decision was made. audit_event.policy_version
-- points here so a past decision stays explainable after the policy changes.
CREATE TABLE admin_policy_version (
    id          bigserial PRIMARY KEY,
    version     text NOT NULL UNIQUE,
    source_ref  text NOT NULL,                   -- manifest capability.authorizationPolicies[]
    checksum    text NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT now(),
    retired_at   timestamptz NULL
);

-- ---------------------------------------------------------------------------
-- Audit
-- ---------------------------------------------------------------------------

-- Append-only. See the Integrity section below for enforcement.
-- The database owns the chain columns: writers leave chain_seq, prev_hash and
-- row_hash out of the INSERT (the audit_event_chain_link trigger assigns them),
-- and before_hash / after_hash are derived from before_state / after_state.
CREATE TABLE audit_event (
    id                      bigserial PRIMARY KEY,
    event_uuid              uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    chain_id                text        NOT NULL DEFAULT 'default',
    chain_seq               bigint      NOT NULL,   -- assigned by trigger

    occurred_at             timestamptz NOT NULL,
    recorded_at             timestamptz NOT NULL DEFAULT now(),

    actor_id                bigint      NULL REFERENCES admin_actor(id),
    actor_kind              text        NOT NULL DEFAULT 'human'
                            CONSTRAINT audit_event_actor_kind_check
                            CHECK (actor_kind IN ('human', 'service', 'agent', 'system')),
    -- NULL unless impersonating. Never overwrite actor_id with the impersonated
    -- identity: that is the most common way impersonation audit is destroyed.
    -- The acted-as subject is usually a customer, not an admin_actor, so this is
    -- text in the same identity space as impersonation_session.target_subject_id,
    -- deliberately with no foreign key.
    effective_actor_id      text        NULL,
    impersonation_session_id bigint     NULL,

    tenant_id               text        NULL,
    environment             text        NOT NULL,
    source_ip               inet        NULL,
    user_agent              text        NULL,

    action                  text        NOT NULL,  -- admin_permission.key, never free text
    target_type             text        NOT NULL,
    target_id               text        NULL,

    reason                  text        NULL,
    ticket_ref              text        NULL,
    approval_request_id     bigint      NULL,

    request_id              text        NULL,
    correlation_id          text        NULL,
    idempotency_key         text        NULL,

    -- 'denied' lets you detect probing; 'unknown' stops a provider timeout being
    -- misreported as a failure when the provider may still have acted.
    result                  text        NOT NULL
                            CONSTRAINT audit_event_result_check
                            CHECK (result IN ('succeeded', 'failed', 'denied', 'partial', 'unknown')),
    error_code              text        NULL,

    before_state            jsonb       NULL,
    after_state             jsonb       NULL,
    before_hash             text        NULL,
    after_hash              text        NULL,
    redaction_policy        text        NOT NULL DEFAULT 'none',

    policy_version          text        NULL,

    prev_hash               text        NULL,      -- assigned by trigger
    row_hash                text        NOT NULL,  -- assigned by trigger
    hash_algorithm          text        NOT NULL DEFAULT 'sha256',

    CONSTRAINT audit_event_effective_actor_requires_session
        CHECK (effective_actor_id IS NULL OR impersonation_session_id IS NOT NULL)
);

-- Chain position must be unique and gap-detectable.
CREATE UNIQUE INDEX audit_event_chain ON audit_event (chain_id, chain_seq);

-- The narrow, predictable query set. Every extra index taxes the hottest insert
-- path in the system, so add nothing speculative here.
CREATE INDEX audit_event_recent      ON audit_event (occurred_at DESC, id DESC);
CREATE INDEX audit_event_target      ON audit_event (target_type, target_id, occurred_at DESC);
CREATE INDEX audit_event_actor       ON audit_event (actor_id, occurred_at DESC);
CREATE INDEX audit_event_tenant      ON audit_event (tenant_id, occurred_at DESC) WHERE tenant_id IS NOT NULL;
CREATE INDEX audit_event_action      ON audit_event (action, occurred_at DESC);
CREATE INDEX audit_event_correlation ON audit_event (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX audit_event_request     ON audit_event (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX audit_event_idempotency ON audit_event (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Paginate by keyset on (occurred_at, id). Offset pagination on an append-heavy
-- table shifts rows under the operator and degrades linearly.

-- ---------------------------------------------------------------------------
-- Impersonation
-- ---------------------------------------------------------------------------

CREATE TABLE impersonation_session (
    id                 bigserial PRIMARY KEY,
    session_uuid       uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    real_actor_id      bigint      NOT NULL REFERENCES admin_actor(id),
    target_subject_id  text        NOT NULL,
    target_tenant_id   text        NULL,
    reason             text        NOT NULL,
    ticket_ref         text        NULL,
    -- Opting into write impersonation is a deliberate, separately permissioned act.
    read_only          boolean     NOT NULL DEFAULT true,
    scope_restrictions jsonb       NOT NULL DEFAULT '{}'::jsonb,
    started_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    ended_at           timestamptz NULL,
    ended_reason       text        NULL,
    revoked_by         bigint      NULL REFERENCES admin_actor(id),
    CONSTRAINT impersonation_bounded CHECK (expires_at > started_at),
    -- Bounded maximum duration; 8 hours here, tighten to the project's policy.
    CONSTRAINT impersonation_max_duration
        CHECK (expires_at - started_at <= interval '8 hours'),
    -- A session that stops without a recorded reason is indistinguishable from one
    -- that was never closed, so ending it requires one of the four reasons.
    CONSTRAINT impersonation_end_state CHECK (
        (ended_at IS NULL AND ended_reason IS NULL)
        OR (ended_at IS NOT NULL AND ended_reason IS NOT NULL
            AND ended_reason IN ('operator_exit', 'expired', 'revoked', 'session_lost'))
    )
);

-- At most one live session per real actor. A sweeper must end expired sessions
-- (ended_reason = 'expired'); until it does, the actor cannot start another.
CREATE UNIQUE INDEX impersonation_session_one_live
    ON impersonation_session (real_actor_id)
    WHERE ended_at IS NULL;

CREATE INDEX impersonation_session_active
    ON impersonation_session (real_actor_id, expires_at)
    WHERE ended_at IS NULL;

ALTER TABLE audit_event
    ADD CONSTRAINT audit_event_impersonation_fk
    FOREIGN KEY (impersonation_session_id) REFERENCES impersonation_session(id);

-- ---------------------------------------------------------------------------
-- Approvals and separation of duties
-- ---------------------------------------------------------------------------

CREATE TABLE approval_request (
    id              bigserial PRIMARY KEY,
    request_uuid    uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    command_key     text        NOT NULL REFERENCES admin_permission(key),
    payload         jsonb       NOT NULL,
    payload_hash    text        NOT NULL,
    target_type     text        NOT NULL,
    target_id       text        NULL,
    tenant_id       text        NULL,
    requested_by    bigint      NOT NULL REFERENCES admin_actor(id),
    requested_at    timestamptz NOT NULL DEFAULT now(),
    reason          text        NOT NULL,
    required_approvals smallint NOT NULL DEFAULT 1
                    CONSTRAINT approval_request_min_approvals CHECK (required_approvals >= 1),
    state           text        NOT NULL DEFAULT 'pending'
                    CONSTRAINT approval_request_state_check
                    CHECK (state IN ('pending', 'approved', 'rejected', 'expired', 'executed',
                                     'execution_failed', 'cancelled')),
    expires_at      timestamptz NOT NULL,
    executed_at     timestamptz NULL,
    execution_result text       NULL
);

CREATE INDEX approval_request_queue
    ON approval_request (state, expires_at)
    WHERE state = 'pending';

-- Append-only, like audit_event: RESTRICT (not CASCADE) on the request, plus the
-- UPDATE / DELETE / TRUNCATE deny triggers in the Integrity section. Cascading
-- would let a rejection be deleted with its request and re-entered as an approval.
CREATE TABLE approval_decision (
    id           bigserial PRIMARY KEY,
    request_id   bigint      NOT NULL REFERENCES approval_request(id) ON DELETE RESTRICT,
    approver_id  bigint      NOT NULL REFERENCES admin_actor(id),
    decision     text        NOT NULL
                 CONSTRAINT approval_decision_kind_check
                 CHECK (decision IN ('approved', 'rejected')),
    reason       text        NOT NULL,
    decided_at   timestamptz NOT NULL DEFAULT now(),
    -- One approver decides once. Without this, a single approver satisfies a
    -- two-approval requirement by voting twice.
    CONSTRAINT approval_decision_one_per_approver UNIQUE (request_id, approver_id)
);

-- The separation-of-duties trigger and the append-only triggers for this table are in
-- the Integrity section at the end.

-- ---------------------------------------------------------------------------
-- Jobs
-- ---------------------------------------------------------------------------

CREATE TABLE admin_job (
    id               bigserial PRIMARY KEY,
    job_uuid         uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    kind             text        NOT NULL,       -- manifest capability.id where kind = job
    initiated_by     bigint      NOT NULL REFERENCES admin_actor(id),
    tenant_id        text        NULL,
    environment      text        NOT NULL,
    input_summary    jsonb       NOT NULL,
    input_hash       text        NOT NULL,
    idempotency_key  text        NULL,
    correlation_id   text        NULL,
    state            text        NOT NULL DEFAULT 'queued'
                     CONSTRAINT admin_job_state_check
                     CHECK (state IN ('queued','running','succeeded','partial','failed','cancelled','expired')),
    total_count      integer     NULL,
    processed_count  integer     NOT NULL DEFAULT 0,
    failed_count     integer     NOT NULL DEFAULT 0,
    queued_at        timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz NULL,
    finished_at      timestamptz NULL,
    result_artifact  text        NULL,
    cancel_requested boolean     NOT NULL DEFAULT false,
    CONSTRAINT admin_job_counts CHECK (processed_count >= 0 AND failed_count >= 0)
);

-- Duplicate submission protection. A repeated key returns the existing job rather
-- than starting a second one.
CREATE UNIQUE INDEX admin_job_idempotency
    ON admin_job (kind, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX admin_job_active
    ON admin_job (state, queued_at DESC)
    WHERE state IN ('queued', 'running');

-- Per-item failures. A job that reports only an aggregate count cannot tell the
-- operator which rows to retry. Immutable history (grants.sql): RESTRICT, not
-- CASCADE, or deleting the job would erase its failures with it.
CREATE TABLE admin_job_failure (
    id          bigserial PRIMARY KEY,
    job_id      bigint      NOT NULL REFERENCES admin_job(id) ON DELETE RESTRICT,
    target_type text        NOT NULL,
    target_id   text        NOT NULL,
    error_code  text        NOT NULL,
    error_detail text       NULL,
    failed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX admin_job_failure_job ON admin_job_failure (job_id, failed_at);

-- ---------------------------------------------------------------------------
-- Saved views and configuration
-- ---------------------------------------------------------------------------

CREATE TABLE saved_view (
    id          bigserial PRIMARY KEY,
    owner_id    bigint      NOT NULL REFERENCES admin_actor(id) ON DELETE CASCADE,
    route       text        NOT NULL,            -- manifest screens[].route
    name        text        NOT NULL,
    filters     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    columns     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    sort        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    visibility  text        NOT NULL DEFAULT 'private'
                CONSTRAINT saved_view_visibility_check
                CHECK (visibility IN ('private', 'shared')),
    tenant_id   text        NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT saved_view_name_unique UNIQUE (owner_id, route, name)
);

-- A shared saved view is a disclosure surface: it can carry one tenant's filter
-- values to another operator. Apply the same row policy when loading it.

-- The same key legitimately differs per environment (production vs staging) and
-- per tenant, so uniqueness is (key, environment, tenant); COALESCE makes the
-- global row (NULL tenant) unique too.
CREATE TABLE config_setting (
    id          bigserial PRIMARY KEY,
    key         text        NOT NULL,
    value       jsonb       NOT NULL,
    value_type  text        NOT NULL,
    is_secret   boolean     NOT NULL DEFAULT false,
    tenant_id   text        NULL,
    environment text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- A secret is only ever held as a reference to a secret manager entry, so the
    -- flag and the value_type must agree. There is no column for a secret value.
    CONSTRAINT config_setting_secret_ref CHECK (is_secret = (value_type = 'secret_ref'))
);

CREATE UNIQUE INDEX config_setting_key_scope_unique
    ON config_setting (key, environment, COALESCE(tenant_id, '*'));

COMMENT ON COLUMN config_setting.is_secret IS
    'Set for value_type = secret_ref rows: value is a pointer, never the secret. Secrets are never displayed; support rotation and revocation instead.';

CREATE TABLE feature_flag (
    id            bigserial PRIMARY KEY,
    key           text        NOT NULL,
    description   text        NOT NULL DEFAULT '',
    is_enabled    boolean     NOT NULL DEFAULT false,
    rollout       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    tenant_id     text        NULL,
    environment   text        NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX feature_flag_key_scope_unique
    ON feature_flag (key, environment, COALESCE(tenant_id, '*'));

-- Every configuration change is a privileged mutation and gets its own history row
-- in addition to its audit_event. Keys repeat across environments and tenants, so
-- the history row carries the same scope as the row it changed.
CREATE TABLE config_change (
    id           bigserial PRIMARY KEY,
    target_table text        NOT NULL
                 CONSTRAINT config_change_target_check
                 CHECK (target_table IN ('config_setting', 'feature_flag')),
    target_key   text        NOT NULL,
    environment  text        NOT NULL,
    tenant_id    text        NULL,
    before_value jsonb       NULL,
    after_value  jsonb       NULL,
    changed_by   bigint      NOT NULL REFERENCES admin_actor(id),
    reason       text        NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now(),
    audit_event_id bigint    NULL REFERENCES audit_event(id)
);

CREATE INDEX config_change_target
    ON config_change (target_table, target_key, environment, changed_at DESC);

-- ---------------------------------------------------------------------------
-- Exports and data-subject requests
-- ---------------------------------------------------------------------------

CREATE TABLE export_request (
    id             bigserial PRIMARY KEY,
    export_uuid    uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    resource       text        NOT NULL,
    requested_by   bigint      NOT NULL REFERENCES admin_actor(id),
    tenant_id      text        NULL,
    filters        jsonb       NOT NULL,
    -- Record which policy was applied, not merely that one was. An export is the
    -- easiest way for field-level policy to be silently bypassed.
    row_policy     text        NOT NULL,
    field_policy   text        NOT NULL,
    reason         text        NOT NULL,
    state          text        NOT NULL DEFAULT 'queued'
                   CONSTRAINT export_request_state_check
                   CHECK (state IN ('queued','running','ready','failed','expired','revoked')),
    row_count      integer     NULL,
    byte_size      bigint      NULL,
    artifact_ref   text        NULL,
    requested_at   timestamptz NOT NULL DEFAULT now(),
    ready_at       timestamptz NULL,
    expires_at     timestamptz NOT NULL,
    download_count integer     NOT NULL DEFAULT 0,
    last_downloaded_at timestamptz NULL
);

CREATE INDEX export_request_state ON export_request (state, expires_at);

-- Erasure interacts with retention, legal hold and backups. A request under legal hold
-- cannot be marked fulfilled: it stays open (or is refused) while the hold lasts, and the
-- hold never silently drops it. Access and portability requests link the export_request
-- that answered them; erasure links the admin_job that carried it out, because erasure
-- across a real system is a long-running job with partial failures.
CREATE TABLE data_subject_request (
    id            bigserial PRIMARY KEY,
    request_uuid  uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    kind          text        NOT NULL
                  CONSTRAINT data_subject_request_kind_check
                  CHECK (kind IN ('access','erasure','rectification','portability','restriction','objection')),
    subject_ref   text        NOT NULL,
    tenant_id     text        NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    due_at        timestamptz NOT NULL,
    state         text        NOT NULL DEFAULT 'open'
                  CONSTRAINT data_subject_request_state_check
                  CHECK (state IN ('open','in-progress','fulfilled','refused','withdrawn')),
    legal_hold    boolean     NOT NULL DEFAULT false,
    assigned_to   bigint      NULL REFERENCES admin_actor(id),
    export_request_id bigint  NULL REFERENCES export_request(id),
    admin_job_id  bigint      NULL REFERENCES admin_job(id),
    resolution    text        NULL,
    resolved_at   timestamptz NULL,
    CONSTRAINT data_subject_request_hold_blocks_fulfilment
        CHECK (NOT (legal_hold AND state = 'fulfilled'))
);

CREATE INDEX data_subject_request_queue ON data_subject_request (state, due_at);

-- ---------------------------------------------------------------------------
-- Integrity: hash chain, payload scrub, append-only enforcement
-- ---------------------------------------------------------------------------

-- Caveats, stated plainly:
--   * A trigger stops ordinary application paths. It does not stop a superuser,
--     anyone who can DROP the trigger, or direct file access.
--   * Real tamper evidence needs the hash chain anchored somewhere outside this
--     database (an append-only log, a WORM bucket, periodic external attestation).
--     See the hash-chaining section of references/admin-data-model.md for what a
--     chain does and does not prove.
--   * At regulated profile, pair this with grants.sql: revoked UPDATE / DELETE /
--     TRUNCATE for a separate application role.

-- Canonical hash input for one audit_event row: the JSON array (jsonb canonical
-- text form) of the values below, in this order; timestamps as UTC ISO-8601 with
-- microseconds, the address without a netmask, SQL NULL as JSON null. Excluded on
-- purpose: id (storage detail), row_hash (the output), and before_state /
-- after_state / redaction_policy (a scrub changes them; before_hash / after_hash
-- stand in for the payloads, which is what lets a payload be scrubbed without
-- breaking the chain). row_hash = hex(sha256(utf8(this text))).
CREATE OR REPLACE FUNCTION admin_core.audit_event_hash_input(e admin_core.audit_event)
RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_array(
        e.event_uuid, e.chain_id, e.chain_seq,
        to_char(e.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        to_char(e.recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        e.actor_id, e.actor_kind, e.effective_actor_id, e.impersonation_session_id,
        e.tenant_id, e.environment, host(e.source_ip), e.user_agent,
        e.action, e.target_type, e.target_id,
        e.reason, e.ticket_ref, e.approval_request_id,
        e.request_id, e.correlation_id, e.idempotency_key,
        e.result, e.error_code, e.before_hash, e.after_hash,
        e.policy_version, e.prev_hash, e.hash_algorithm
    )::text
$$;

-- Assigns chain_seq, prev_hash, the payload hashes and row_hash. The advisory lock
-- serializes writers within one chain_id (released at commit), which is what makes
-- chain_seq gap-free; use several chain_ids to shard write throughput. Under
-- REPEATABLE READ or SERIALIZABLE a stale snapshot can pick an already-used
-- chain_seq: the unique index then rejects the insert and the writer retries.
CREATE OR REPLACE FUNCTION admin_core.audit_event_chain_link()
RETURNS trigger AS $$
DECLARE
    last_seq  bigint;
    last_hash text;
BEGIN
    IF NEW.hash_algorithm IS DISTINCT FROM 'sha256' THEN
        RAISE EXCEPTION 'audit_event: only sha256 is implemented, got %', NEW.hash_algorithm;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('admin_core.audit_event:' || NEW.chain_id, 0));
    SELECT a.chain_seq, a.row_hash INTO last_seq, last_hash
      FROM admin_core.audit_event a
     WHERE a.chain_id = NEW.chain_id
     ORDER BY a.chain_seq DESC
     LIMIT 1;
    NEW.chain_seq := COALESCE(last_seq, 0) + 1;
    NEW.prev_hash := last_hash;
    IF NEW.before_state IS NOT NULL THEN
        NEW.before_hash := encode(sha256(convert_to(NEW.before_state::text, 'UTF8')), 'hex');
    END IF;
    IF NEW.after_state IS NOT NULL THEN
        NEW.after_hash := encode(sha256(convert_to(NEW.after_state::text, 'UTF8')), 'hex');
    END IF;
    NEW.row_hash := encode(
        sha256(convert_to(admin_core.audit_event_hash_input(NEW), 'UTF8')), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_chain_link
    BEFORE INSERT ON admin_core.audit_event
    FOR EACH ROW EXECUTE FUNCTION admin_core.audit_event_chain_link();

-- Re-computes every row of the chain and returns one row per problem found; an
-- empty result means the stored chain is internally consistent. A segment that
-- starts above chain_seq 1 (earlier segments archived) is checked from its first
-- row; link it to the archived tail hash separately. Compare the newest row_hash
-- with the externally anchored head to detect truncation or a rewritten chain.
CREATE OR REPLACE FUNCTION admin_core.verify_audit_chain(p_chain_id text DEFAULT NULL)
RETURNS TABLE (chain_id text, chain_seq bigint, problem text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r         admin_core.audit_event%ROWTYPE;
    cur_chain text;
    prev_seq  bigint;
    prev_hash text;
BEGIN
    FOR r IN
        SELECT * FROM admin_core.audit_event a
         WHERE p_chain_id IS NULL OR a.chain_id = p_chain_id
         ORDER BY a.chain_id, a.chain_seq
    LOOP
        IF cur_chain IS DISTINCT FROM r.chain_id THEN
            cur_chain := r.chain_id;
            prev_seq := NULL;
            prev_hash := NULL;
        END IF;
        chain_id := r.chain_id;
        chain_seq := r.chain_seq;
        IF prev_seq IS NOT NULL AND r.chain_seq <> prev_seq + 1 THEN
            problem := 'gap: previous chain_seq is ' || prev_seq;
            RETURN NEXT;
        END IF;
        IF (prev_seq IS NOT NULL OR r.chain_seq = 1)
           AND r.prev_hash IS DISTINCT FROM prev_hash THEN
            problem := 'prev_hash does not match the previous row_hash';
            RETURN NEXT;
        END IF;
        IF r.row_hash IS DISTINCT FROM encode(
               sha256(convert_to(admin_core.audit_event_hash_input(r), 'UTF8')), 'hex') THEN
            problem := 'row_hash does not match the row content';
            RETURN NEXT;
        END IF;
        IF r.before_state IS NOT NULL AND r.before_hash IS DISTINCT FROM
               encode(sha256(convert_to(r.before_state::text, 'UTF8')), 'hex') THEN
            problem := 'before_hash does not match before_state';
            RETURN NEXT;
        END IF;
        IF r.after_state IS NOT NULL AND r.after_hash IS DISTINCT FROM
               encode(sha256(convert_to(r.after_state::text, 'UTF8')), 'hex') THEN
            problem := 'after_hash does not match after_state';
            RETURN NEXT;
        END IF;
        prev_seq := r.chain_seq;
        prev_hash := r.row_hash;
    END LOOP;
END;
$$;

-- The single permitted UPDATE of an audit_event: drop the payload and name the
-- redaction policy. Hashes, and therefore the chain, are untouched. Runs as the
-- function owner so the application role needs no UPDATE grant; the scrub is
-- itself recorded as an audit_event. Register 'audit.scrub_payload' as a
-- permission and grant EXECUTE only to the role that may redact.
CREATE OR REPLACE FUNCTION admin_core.scrub_audit_payload(
    p_event_id bigint, p_policy text, p_reason text, p_actor_id bigint DEFAULT NULL)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = admin_core, pg_temp AS $$
DECLARE
    ev admin_core.audit_event%ROWTYPE;
BEGIN
    IF p_policy IS NULL OR p_policy IN ('', 'none') THEN
        RAISE EXCEPTION 'scrub_audit_payload needs a named redaction policy';
    END IF;
    IF p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'scrub_audit_payload needs a reason';
    END IF;
    PERFORM set_config('admin_core.scrub', 'on', true);
    UPDATE admin_core.audit_event
       SET before_state = NULL, after_state = NULL, redaction_policy = p_policy
     WHERE id = p_event_id
    RETURNING * INTO ev;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'audit_event % does not exist', p_event_id;
    END IF;
    PERFORM set_config('admin_core.scrub', 'off', true);
    -- actor_kind is 'system': the database performed the write; actor_id is who asked.
    INSERT INTO admin_core.audit_event
        (occurred_at, actor_id, actor_kind, tenant_id, environment,
         action, target_type, target_id, reason, result)
    VALUES
        (now(), p_actor_id, 'system', ev.tenant_id, ev.environment,
         'audit.scrub_payload', 'audit_event', ev.id::text, p_reason, 'succeeded');
END;
$$;

REVOKE ALL ON FUNCTION admin_core.scrub_audit_payload(bigint, text, text, bigint) FROM PUBLIC;

-- Separation of duties: the requester may not approve their own request. Enforced
-- here rather than in application code because it is the whole point of the control.
CREATE OR REPLACE FUNCTION admin_core.enforce_separation_of_duties()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM admin_core.approval_request r
        WHERE r.id = NEW.request_id AND r.requested_by = NEW.approver_id
    ) THEN
        RAISE EXCEPTION 'separation of duties: requester % may not approve request %',
            NEW.approver_id, NEW.request_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER approval_decision_sod
    BEFORE INSERT ON admin_core.approval_decision
    FOR EACH ROW EXECUTE FUNCTION admin_core.enforce_separation_of_duties();

-- Append-only enforcement.
CREATE OR REPLACE FUNCTION admin_core.deny_audit_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

-- UPDATE of audit_event is refused except inside scrub_audit_payload, and even
-- there nothing the row hash covers may change.
CREATE OR REPLACE FUNCTION admin_core.audit_event_guard_update()
RETURNS trigger AS $$
BEGIN
    IF current_setting('admin_core.scrub', true) IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'audit_event is append-only; UPDATE is not permitted (scrub payloads with scrub_audit_payload)';
    END IF;
    IF NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL
       OR (to_jsonb(NEW) - 'before_state' - 'after_state' - 'redaction_policy')
          IS DISTINCT FROM (to_jsonb(OLD) - 'before_state' - 'after_state' - 'redaction_policy') THEN
        RAISE EXCEPTION 'audit_event scrub may only drop before_state / after_state and set redaction_policy';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_no_update
    BEFORE UPDATE ON admin_core.audit_event
    FOR EACH ROW EXECUTE FUNCTION admin_core.audit_event_guard_update();

CREATE TRIGGER audit_event_no_delete
    BEFORE DELETE ON admin_core.audit_event
    FOR EACH ROW EXECUTE FUNCTION admin_core.deny_audit_mutation();

CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON admin_core.audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION admin_core.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_update
    BEFORE UPDATE ON admin_core.approval_decision
    FOR EACH ROW EXECUTE FUNCTION admin_core.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_delete
    BEFORE DELETE ON admin_core.approval_decision
    FOR EACH ROW EXECUTE FUNCTION admin_core.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_truncate
    BEFORE TRUNCATE ON admin_core.approval_decision
    FOR EACH STATEMENT EXECUTE FUNCTION admin_core.deny_audit_mutation();

-- Grant shape for the application role: see grants.sql.

COMMIT;
