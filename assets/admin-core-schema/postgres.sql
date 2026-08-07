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

BEGIN;

CREATE SCHEMA IF NOT EXISTS admin_core;
SET LOCAL search_path = admin_core, public;

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
                    CHECK (kind IN ('human', 'service', 'agent')),
    auth_strength   text        NOT NULL DEFAULT 'password'
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
-- revocation unreliable: revoking one leaves the other in force.
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

-- Append-only. See the immutability section below for enforcement.
CREATE TABLE audit_event (
    id                      bigserial PRIMARY KEY,
    event_uuid              uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    chain_id                text        NOT NULL DEFAULT 'default',
    chain_seq               bigint      NOT NULL,

    occurred_at             timestamptz NOT NULL,
    recorded_at             timestamptz NOT NULL DEFAULT now(),

    actor_id                bigint      NULL REFERENCES admin_actor(id),
    actor_kind              text        NOT NULL DEFAULT 'human'
                            CHECK (actor_kind IN ('human', 'service', 'agent', 'system')),
    -- NULL unless impersonating. Never overwrite actor_id with the impersonated
    -- identity: that is the most common way impersonation audit is destroyed.
    effective_actor_id      bigint      NULL REFERENCES admin_actor(id),
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
                            CHECK (result IN ('succeeded', 'failed', 'denied', 'partial', 'unknown')),
    error_code              text        NULL,

    before_state            jsonb       NULL,
    after_state             jsonb       NULL,
    before_hash             text        NULL,
    after_hash              text        NULL,
    redaction_policy        text        NOT NULL DEFAULT 'none',

    policy_version          text        NULL,

    prev_hash               text        NULL,
    row_hash                text        NOT NULL,
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
    scope_restrictions jsonb       NOT NULL DEFAULT '{}'::jsonb,
    started_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    ended_at           timestamptz NULL,
    ended_reason       text        NULL,
    revoked_by         bigint      NULL REFERENCES admin_actor(id),
    CONSTRAINT impersonation_bounded CHECK (expires_at > started_at)
);

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
    required_approvals smallint NOT NULL DEFAULT 1 CHECK (required_approvals >= 1),
    state           text        NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending', 'approved', 'rejected', 'expired', 'executed', 'cancelled')),
    expires_at      timestamptz NOT NULL,
    executed_at     timestamptz NULL,
    execution_result text       NULL
);

CREATE INDEX approval_request_queue
    ON approval_request (state, expires_at)
    WHERE state = 'pending';

CREATE TABLE approval_decision (
    id           bigserial PRIMARY KEY,
    request_id   bigint      NOT NULL REFERENCES approval_request(id) ON DELETE CASCADE,
    approver_id  bigint      NOT NULL REFERENCES admin_actor(id),
    decision     text        NOT NULL CHECK (decision IN ('approved', 'rejected')),
    reason       text        NOT NULL,
    decided_at   timestamptz NOT NULL DEFAULT now(),
    -- One approver decides once. Without this, a single approver satisfies a
    -- two-approval requirement by voting twice.
    CONSTRAINT approval_decision_one_per_approver UNIQUE (request_id, approver_id)
);

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
    BEFORE INSERT ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION admin_core.enforce_separation_of_duties();

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
-- operator which rows to retry.
CREATE TABLE admin_job_failure (
    id          bigserial PRIMARY KEY,
    job_id      bigint      NOT NULL REFERENCES admin_job(id) ON DELETE CASCADE,
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
                CHECK (visibility IN ('private', 'shared')),
    tenant_id   text        NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT saved_view_name_unique UNIQUE (owner_id, route, name)
);

-- A shared saved view is a disclosure surface: it can carry one tenant's filter
-- values to another operator. Apply the same row policy when loading it.

CREATE TABLE config_setting (
    id          bigserial PRIMARY KEY,
    key         text        NOT NULL UNIQUE,
    value       jsonb       NOT NULL,
    value_type  text        NOT NULL,
    is_secret   boolean     NOT NULL DEFAULT false,
    tenant_id   text        NULL,
    environment text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN config_setting.is_secret IS
    'Secret values are never displayed after write. Support rotation and revocation instead.';

CREATE TABLE feature_flag (
    id            bigserial PRIMARY KEY,
    key           text        NOT NULL UNIQUE,
    description   text        NOT NULL DEFAULT '',
    is_enabled    boolean     NOT NULL DEFAULT false,
    rollout       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    environment   text        NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Every configuration change is a privileged mutation and gets its own history row
-- in addition to its audit_event.
CREATE TABLE config_change (
    id           bigserial PRIMARY KEY,
    target_table text        NOT NULL CHECK (target_table IN ('config_setting', 'feature_flag')),
    target_key   text        NOT NULL,
    before_value jsonb       NULL,
    after_value  jsonb       NULL,
    changed_by   bigint      NOT NULL REFERENCES admin_actor(id),
    reason       text        NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now(),
    audit_event_id bigint    NULL REFERENCES audit_event(id)
);

CREATE INDEX config_change_target ON config_change (target_table, target_key, changed_at DESC);

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

CREATE TABLE data_subject_request (
    id            bigserial PRIMARY KEY,
    request_uuid  uuid        NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    kind          text        NOT NULL
                  CHECK (kind IN ('access','erasure','rectification','portability','restriction','objection')),
    subject_ref   text        NOT NULL,
    tenant_id     text        NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    due_at        timestamptz NOT NULL,
    state         text        NOT NULL DEFAULT 'open'
                  CHECK (state IN ('open','in-progress','fulfilled','refused','withdrawn')),
    legal_hold    boolean     NOT NULL DEFAULT false,
    assigned_to   bigint      NULL REFERENCES admin_actor(id),
    resolution    text        NULL,
    resolved_at   timestamptz NULL
);

CREATE INDEX data_subject_request_queue ON data_subject_request (state, due_at);

-- Erasure interacts with retention, legal hold and backups. legal_hold blocks
-- fulfilment; it does not silently drop the request.

-- ---------------------------------------------------------------------------
-- Immutability
-- ---------------------------------------------------------------------------

-- Append-only enforcement for audit_event.
--
-- Caveats, stated plainly:
--   * A trigger stops ordinary application paths. It does not stop a superuser,
--     anyone who can DROP the trigger, or direct file access.
--   * Real tamper evidence needs the hash chain anchored somewhere outside this
--     database (an append-only log, a WORM bucket, periodic external attestation).
--     See the hash-chaining section of references/admin-data-model.md for what a
--     chain does and does not prove.
--   * At regulated profile, pair this with revoked UPDATE/DELETE grants and a
--     separate role for the application.
CREATE OR REPLACE FUNCTION admin_core.deny_audit_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only; % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_no_update
    BEFORE UPDATE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION admin_core.deny_audit_mutation();

CREATE TRIGGER audit_event_no_delete
    BEFORE DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION admin_core.deny_audit_mutation();

CREATE TRIGGER approval_decision_no_update
    BEFORE UPDATE ON approval_decision
    FOR EACH ROW EXECUTE FUNCTION admin_core.deny_audit_mutation();

-- Grant shape for the application role. Adapt the role name.
--   REVOKE UPDATE, DELETE ON admin_core.audit_event FROM app_role;
--   GRANT  INSERT, SELECT ON admin_core.audit_event TO app_role;

COMMIT;
