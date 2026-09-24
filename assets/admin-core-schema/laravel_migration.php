<?php

/**
 * Adminwright admin control-plane core schema — Laravel migration.
 *
 * Translation of postgres.sql, kept in step with it by tests/test_core_schema.py.
 * Read ./README.md before adapting.
 *
 * WHAT THE SCHEMA BUILDER CANNOT EXPRESS — done here via DB::statement():
 *
 *   1. CHECK constraints. Laravel's schema builder has no CHECK support.
 *   2. Partial and expression unique indexes. `$table->unique()` cannot carry a
 *      WHERE clause or COALESCE().
 *   3. Triggers and functions. Append-only enforcement on audit_event and
 *      approval_decision, the audit hash chain (chain_seq / prev_hash / row_hash
 *      are assigned by a BEFORE INSERT trigger; leave them out of your inserts),
 *      verify_audit_chain(), scrub_audit_payload(), the separation-of-duties
 *      trigger, and the trigger that keeps an approval_request as written (only
 *      state, executed_at and execution_result change). Model events and
 *      observers are bypassed by query-builder writes, bulk operations, and other
 *      services.
 *
 * All three are included below. Do not drop them: they are the controls, not
 * decoration. Then run grants.sql (least-privilege grants for the application
 * role; the regulated profile requires it) with psql.
 *
 * PostgreSQL only; tables land in the default (public) schema and admin_actor.email
 * is text rather than citext. Adapt table names to project convention, but keep
 * admin_permission.key equal to the manifest capability id.
 */

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        // -------------------------------------------------------------------
        // Actors, roles, permissions, grants
        // -------------------------------------------------------------------

        // The privileged identity. Drop this table and add is_privileged /
        // auth_strength / mfa_enrolled to the app's users table when operators
        // share the product's lifecycle and identity provider.
        // Credentials are never stored here; auth_subject holds the IdP subject.
        Schema::create('admin_actor', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('actor_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->unsignedBigInteger('app_user_id')->nullable();
            $table->text('auth_subject')->unique();
            $table->text('display_name');
            $table->text('email')->nullable();
            // Automation and autonomous agents are first-class subjects. They
            // never inherit a human's authority.
            $table->string('kind', 16)->default('human');
            $table->string('auth_strength', 32)->default('password');
            $table->boolean('mfa_enrolled')->default(false);
            $table->boolean('is_active')->default(true);
            $table->timestampTz('deactivated_at')->nullable();
            $table->timestampTz('created_at')->useCurrent();
        });

        DB::statement("ALTER TABLE admin_actor ADD CONSTRAINT admin_actor_kind_check
            CHECK (kind IN ('human','service','agent'))");
        DB::statement("ALTER TABLE admin_actor ADD CONSTRAINT admin_actor_auth_strength_check
            CHECK (auth_strength IN ('password','mfa','phishing-resistant','sso'))");

        Schema::create('admin_role', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('key')->unique();          // matches manifest roles[].id
            $table->text('name');
            $table->text('description')->default('');
            $table->boolean('requires_mfa')->default(true);
            $table->jsonb('max_scope')->default(DB::raw("'{}'::jsonb"));
            $table->boolean('is_active')->default(true);
        });

        // The atom of authorization. Key it <resource>.<action> so it equals the
        // manifest capability id exactly; that equality is what makes
        // capability -> permission -> audit_event.action traceable.
        // Obligations live here, not at the call site, so a new call site cannot
        // silently skip them.
        Schema::create('admin_permission', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('key')->unique();          // manifest capability.id
            $table->text('resource');               // manifest entity id
            $table->text('action');
            $table->string('risk', 16)->default('low');
            $table->boolean('requires_reason')->default(false);
            $table->boolean('requires_step_up')->default(false);
            $table->boolean('requires_approval')->default(false);
            $table->text('description')->default('');
        });

        DB::statement("ALTER TABLE admin_permission ADD CONSTRAINT admin_permission_risk_check
            CHECK (risk IN ('low','moderate','high','critical'))");

        Schema::create('admin_role_permission', function (Blueprint $table) {
            $table->unsignedBigInteger('role_id');
            $table->unsignedBigInteger('permission_id');
            $table->primary(['role_id', 'permission_id']);
            $table->foreign('role_id')->references('id')->on('admin_role')->cascadeOnDelete();
            $table->foreign('permission_id')->references('id')->on('admin_permission')->cascadeOnDelete();
        });

        // A grant, not a join row: where it applies, when it expires, who granted
        // it and why, and how it was revoked. Revocation is a state change.
        Schema::create('admin_actor_role', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('actor_id');
            $table->unsignedBigInteger('role_id');
            $table->jsonb('scope')->default(DB::raw("'{}'::jsonb"));
            $table->text('scope_tenant_id')->nullable();   // denormalized hot path
            $table->timestampTz('starts_at')->useCurrent();
            $table->timestampTz('expires_at')->nullable(); // null = permanent
            $table->unsignedBigInteger('granted_by');
            $table->text('granted_reason');
            $table->text('ticket_ref')->nullable();
            $table->timestampTz('granted_at')->useCurrent();
            $table->timestampTz('revoked_at')->nullable();
            $table->unsignedBigInteger('revoked_by')->nullable();
            $table->text('revoke_reason')->nullable();

            $table->foreign('actor_id')->references('id')->on('admin_actor');
            $table->foreign('role_id')->references('id')->on('admin_role');
            $table->foreign('granted_by')->references('id')->on('admin_actor');
            $table->foreign('revoked_by')->references('id')->on('admin_actor');
        });

        // No self-granting. Privilege escalation by an operator on their own
        // account is the failure this prevents.
        DB::statement('ALTER TABLE admin_actor_role ADD CONSTRAINT admin_actor_role_no_self_grant
            CHECK (granted_by <> actor_id)');
        DB::statement('ALTER TABLE admin_actor_role ADD CONSTRAINT admin_actor_role_revocation_complete
            CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))');
        DB::statement('ALTER TABLE admin_actor_role ADD CONSTRAINT admin_actor_role_window
            CHECK (expires_at IS NULL OR expires_at > starts_at)');
        // One live grant of a role per actor per tenant scope. Duplicate live
        // grants make revocation unreliable: revoking one leaves the other live.
        DB::statement("CREATE UNIQUE INDEX admin_actor_role_live_unique
            ON admin_actor_role (actor_id, role_id, COALESCE(scope_tenant_id, '*'))
            WHERE revoked_at IS NULL");
        DB::statement('CREATE INDEX admin_actor_role_live_lookup
            ON admin_actor_role (actor_id) WHERE revoked_at IS NULL');

        // Which rules were in force when a decision was made. audit_event
        // .policy_version points here so a past decision stays explainable.
        Schema::create('admin_policy_version', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('version')->unique();
            $table->text('source_ref');   // manifest capability.authorizationPolicies[]
            $table->text('checksum');
            $table->timestampTz('activated_at')->useCurrent();
            $table->timestampTz('retired_at')->nullable();
        });

        // -------------------------------------------------------------------
        // Impersonation (before audit_event for the FK)
        // -------------------------------------------------------------------

        Schema::create('impersonation_session', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('session_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->unsignedBigInteger('real_actor_id');
            $table->text('target_subject_id');
            $table->text('target_tenant_id')->nullable();
            $table->text('reason');
            $table->text('ticket_ref')->nullable();
            // Opting into write impersonation is a deliberate, separately
            // permissioned act.
            $table->boolean('read_only')->default(true);
            $table->jsonb('scope_restrictions')->default(DB::raw("'{}'::jsonb"));
            $table->timestampTz('started_at')->useCurrent();
            $table->timestampTz('expires_at');
            $table->timestampTz('ended_at')->nullable();
            $table->text('ended_reason')->nullable();
            $table->unsignedBigInteger('revoked_by')->nullable();

            $table->foreign('real_actor_id')->references('id')->on('admin_actor');
            $table->foreign('revoked_by')->references('id')->on('admin_actor');
        });

        DB::statement('ALTER TABLE impersonation_session ADD CONSTRAINT impersonation_bounded
            CHECK (expires_at > started_at)');
        // Bounded maximum duration; 8 hours here, tighten to the project's policy.
        DB::statement("ALTER TABLE impersonation_session ADD CONSTRAINT impersonation_max_duration
            CHECK (expires_at - started_at <= interval '8 hours')");
        // A session that stops without a recorded reason is indistinguishable from
        // one that was never closed, so ending it requires one of the four reasons.
        DB::statement("ALTER TABLE impersonation_session ADD CONSTRAINT impersonation_end_state
            CHECK ((ended_at IS NULL AND ended_reason IS NULL)
                OR (ended_at IS NOT NULL AND ended_reason IS NOT NULL
                    AND ended_reason IN ('operator_exit', 'expired', 'revoked', 'session_lost')))");
        // At most one live session per real actor. A sweeper must end expired
        // sessions (ended_reason = 'expired'), or the actor cannot start another.
        DB::statement('CREATE UNIQUE INDEX impersonation_session_one_live
            ON impersonation_session (real_actor_id) WHERE ended_at IS NULL');
        DB::statement('CREATE INDEX impersonation_session_active
            ON impersonation_session (real_actor_id, expires_at) WHERE ended_at IS NULL');

        // -------------------------------------------------------------------
        // Audit
        // -------------------------------------------------------------------

        Schema::create('audit_event', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('event_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->text('chain_id')->default('default');
            $table->bigInteger('chain_seq');   // assigned by trigger

            $table->timestampTz('occurred_at');
            $table->timestampTz('recorded_at')->useCurrent();

            $table->unsignedBigInteger('actor_id')->nullable();
            $table->string('actor_kind', 16)->default('human');
            // Null unless impersonating. Never overwrite actor_id with the
            // impersonated identity: that is the most common way impersonation
            // audit is destroyed. The acted-as subject is usually a customer, not
            // an admin_actor, so this is text in the same identity space as
            // impersonation_session.target_subject_id, deliberately with no FK.
            $table->text('effective_actor_id')->nullable();
            $table->unsignedBigInteger('impersonation_session_id')->nullable();

            $table->text('tenant_id')->nullable();
            $table->text('environment');
            $table->ipAddress('source_ip')->nullable();
            $table->text('user_agent')->nullable();

            $table->text('action');          // admin_permission.key, never free text
            $table->text('target_type');
            $table->text('target_id')->nullable();

            $table->text('reason')->nullable();
            $table->text('ticket_ref')->nullable();
            $table->unsignedBigInteger('approval_request_id')->nullable();

            $table->text('request_id')->nullable();
            $table->text('correlation_id')->nullable();
            $table->text('idempotency_key')->nullable();

            $table->string('result', 16);
            $table->text('error_code')->nullable();

            $table->jsonb('before_state')->nullable();
            $table->jsonb('after_state')->nullable();
            // Hashes of the payloads; row_hash covers the hashes rather than the
            // payload text, which is what makes later redaction possible without
            // invalidating the chain.
            $table->text('before_hash')->nullable();
            $table->text('after_hash')->nullable();
            $table->text('redaction_policy')->default('none');

            $table->text('policy_version')->nullable();

            $table->text('prev_hash')->nullable();   // assigned by trigger
            $table->text('row_hash');                // assigned by trigger
            $table->text('hash_algorithm')->default('sha256');

            $table->foreign('actor_id')->references('id')->on('admin_actor');
            $table->foreign('impersonation_session_id')->references('id')->on('impersonation_session');

            $table->unique(['chain_id', 'chain_seq'], 'audit_event_chain');
        });

        DB::statement("ALTER TABLE audit_event ADD CONSTRAINT audit_event_actor_kind_check
            CHECK (actor_kind IN ('human','service','agent','system'))");
        // 'denied' lets you detect probing; 'unknown' stops a provider timeout
        // being misreported as a failure when the provider may still have acted.
        DB::statement("ALTER TABLE audit_event ADD CONSTRAINT audit_event_result_check
            CHECK (result IN ('succeeded','failed','denied','partial','unknown'))");
        DB::statement('ALTER TABLE audit_event ADD CONSTRAINT audit_event_effective_actor_requires_session
            CHECK (effective_actor_id IS NULL OR impersonation_session_id IS NOT NULL)');

        // The narrow, predictable query set. Every extra index taxes the hottest
        // insert path in the system, so add nothing speculative.
        DB::statement('CREATE INDEX audit_event_recent ON audit_event (occurred_at DESC, id DESC)');
        DB::statement('CREATE INDEX audit_event_target ON audit_event (target_type, target_id, occurred_at DESC)');
        DB::statement('CREATE INDEX audit_event_actor ON audit_event (actor_id, occurred_at DESC)');
        DB::statement('CREATE INDEX audit_event_tenant ON audit_event (tenant_id, occurred_at DESC) WHERE tenant_id IS NOT NULL');
        DB::statement('CREATE INDEX audit_event_action ON audit_event (action, occurred_at DESC)');
        DB::statement('CREATE INDEX audit_event_correlation ON audit_event (correlation_id) WHERE correlation_id IS NOT NULL');
        DB::statement('CREATE INDEX audit_event_request ON audit_event (request_id) WHERE request_id IS NOT NULL');
        DB::statement('CREATE INDEX audit_event_idempotency ON audit_event (idempotency_key) WHERE idempotency_key IS NOT NULL');

        // Paginate by keyset on (occurred_at, id), not offset. Offset pagination
        // on an append-heavy table shifts rows under the operator.

        // -------------------------------------------------------------------
        // Approvals and separation of duties
        // -------------------------------------------------------------------

        Schema::create('approval_request', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('request_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->text('command_key');
            $table->jsonb('payload');
            $table->text('payload_hash');
            $table->text('target_type');
            $table->text('target_id')->nullable();
            $table->text('tenant_id')->nullable();
            $table->unsignedBigInteger('requested_by');
            $table->timestampTz('requested_at')->useCurrent();
            $table->text('reason');
            $table->smallInteger('required_approvals')->default(1);
            $table->string('state', 16)->default('pending');
            $table->timestampTz('expires_at');
            $table->timestampTz('executed_at')->nullable();
            $table->text('execution_result')->nullable();

            $table->foreign('command_key')->references('key')->on('admin_permission');
            $table->foreign('requested_by')->references('id')->on('admin_actor');
        });

        DB::statement('ALTER TABLE approval_request ADD CONSTRAINT approval_request_min_approvals
            CHECK (required_approvals >= 1)');
        DB::statement("ALTER TABLE approval_request ADD CONSTRAINT approval_request_state_check
            CHECK (state IN ('pending','approved','rejected','expired','executed','execution_failed','cancelled'))");
        DB::statement("CREATE INDEX approval_request_queue
            ON approval_request (state, expires_at) WHERE state = 'pending'");

        Schema::create('approval_decision', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('request_id');
            $table->unsignedBigInteger('approver_id');
            $table->string('decision', 16);
            $table->text('reason');
            $table->timestampTz('decided_at')->useCurrent();

            // RESTRICT, not CASCADE: cascading would let a rejection be deleted with its
            // request and re-entered as an approval.
            $table->foreign('request_id')->references('id')->on('approval_request')->restrictOnDelete();
            $table->foreign('approver_id')->references('id')->on('admin_actor');
            // One approver decides once. Without this a single approver satisfies
            // a two-approval requirement by voting twice.
            $table->unique(['request_id', 'approver_id'], 'approval_decision_one_per_approver');
        });

        DB::statement("ALTER TABLE approval_decision ADD CONSTRAINT approval_decision_kind_check
            CHECK (decision IN ('approved','rejected'))");

        // Separation of duties: the requester may not approve their own request.
        // Enforced in the database because that is the whole point of the control.
        // The request itself is fixed once written (only state, executed_at and
        // execution_result change), so it cannot be re-attributed to get past it.
        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION enforce_separation_of_duties()
            RETURNS trigger AS $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM approval_request r
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
                FOR EACH ROW EXECUTE FUNCTION enforce_separation_of_duties();

            CREATE OR REPLACE FUNCTION approval_request_guard_update()
            RETURNS trigger AS $$
            BEGIN
                IF (to_jsonb(NEW) - 'state' - 'executed_at' - 'execution_result')
                   IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'executed_at' - 'execution_result') THEN
                    RAISE EXCEPTION 'approval_request % is immutable except state, executed_at and execution_result', OLD.id;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER approval_request_immutable
                BEFORE UPDATE ON approval_request
                FOR EACH ROW EXECUTE FUNCTION approval_request_guard_update();
        SQL);

        // -------------------------------------------------------------------
        // Jobs
        // -------------------------------------------------------------------

        Schema::create('admin_job', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('job_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->text('kind');   // manifest capability.id where kind = job
            $table->unsignedBigInteger('initiated_by');
            $table->text('tenant_id')->nullable();
            $table->text('environment');
            $table->jsonb('input_summary');
            $table->text('input_hash');
            $table->text('idempotency_key')->nullable();
            $table->text('correlation_id')->nullable();
            $table->string('state', 16)->default('queued');
            $table->integer('total_count')->nullable();
            $table->integer('processed_count')->default(0);
            $table->integer('failed_count')->default(0);
            $table->timestampTz('queued_at')->useCurrent();
            $table->timestampTz('started_at')->nullable();
            $table->timestampTz('finished_at')->nullable();
            $table->text('result_artifact')->nullable();
            $table->boolean('cancel_requested')->default(false);

            $table->foreign('initiated_by')->references('id')->on('admin_actor');
        });

        DB::statement("ALTER TABLE admin_job ADD CONSTRAINT admin_job_state_check
            CHECK (state IN ('queued','running','succeeded','partial','failed','cancelled','expired'))");
        DB::statement('ALTER TABLE admin_job ADD CONSTRAINT admin_job_counts
            CHECK (processed_count >= 0 AND failed_count >= 0)');
        // Duplicate submission protection: a repeated key returns the existing
        // job rather than starting a second one.
        DB::statement('CREATE UNIQUE INDEX admin_job_idempotency
            ON admin_job (kind, idempotency_key) WHERE idempotency_key IS NOT NULL');
        DB::statement("CREATE INDEX admin_job_active
            ON admin_job (state, queued_at DESC) WHERE state IN ('queued','running')");

        // Per-item failures. A job reporting only an aggregate count cannot tell
        // the operator which rows to retry. RESTRICT, not CASCADE: deleting the job
        // must not erase its failure history.
        Schema::create('admin_job_failure', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('job_id');
            $table->text('target_type');
            $table->text('target_id');
            $table->text('error_code');
            $table->text('error_detail')->nullable();
            $table->timestampTz('failed_at')->useCurrent();

            $table->foreign('job_id')->references('id')->on('admin_job')->restrictOnDelete();
            $table->index(['job_id', 'failed_at'], 'admin_job_failure_job');
        });

        // -------------------------------------------------------------------
        // Saved views and configuration
        // -------------------------------------------------------------------

        // A shared saved view is a disclosure surface: it can carry one tenant's
        // filter values to another operator. Apply the same row policy on load.
        Schema::create('saved_view', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('owner_id');
            $table->text('route');   // manifest screens[].route
            $table->text('name');
            $table->jsonb('filters')->default(DB::raw("'{}'::jsonb"));
            $table->jsonb('columns')->default(DB::raw("'[]'::jsonb"));
            $table->jsonb('sort')->default(DB::raw("'[]'::jsonb"));
            $table->string('visibility', 16)->default('private');
            $table->text('tenant_id')->nullable();
            $table->timestampTz('created_at')->useCurrent();
            $table->timestampTz('updated_at')->useCurrent();

            $table->foreign('owner_id')->references('id')->on('admin_actor')->cascadeOnDelete();
            $table->unique(['owner_id', 'route', 'name'], 'saved_view_name_unique');
        });

        DB::statement("ALTER TABLE saved_view ADD CONSTRAINT saved_view_visibility_check
            CHECK (visibility IN ('private','shared'))");

        // The same key legitimately differs per environment (production vs staging)
        // and per tenant, so uniqueness is (key, environment, tenant); COALESCE makes
        // the global row (NULL tenant) unique too.
        Schema::create('config_setting', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('key');
            $table->jsonb('value');
            $table->text('value_type');
            $table->boolean('is_secret')->default(false);
            $table->text('tenant_id')->nullable();
            $table->text('environment');
            $table->timestampTz('updated_at')->useCurrent();
        });

        // A secret is only ever held as a reference to a secret manager entry, so
        // the flag and the value_type must agree. There is no column for a secret
        // value; secrets are never displayed, support rotation and revocation.
        DB::statement("ALTER TABLE config_setting ADD CONSTRAINT config_setting_secret_ref
            CHECK (is_secret = (value_type = 'secret_ref'))");
        DB::statement("CREATE UNIQUE INDEX config_setting_key_scope_unique
            ON config_setting (key, environment, COALESCE(tenant_id, '*'))");

        Schema::create('feature_flag', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('key');
            $table->text('description')->default('');
            $table->boolean('is_enabled')->default(false);
            $table->jsonb('rollout')->default(DB::raw("'{}'::jsonb"));
            $table->text('tenant_id')->nullable();
            $table->text('environment');
            $table->timestampTz('updated_at')->useCurrent();
        });

        DB::statement("CREATE UNIQUE INDEX feature_flag_key_scope_unique
            ON feature_flag (key, environment, COALESCE(tenant_id, '*'))");

        // Every configuration change is a privileged mutation and gets its own
        // history row in addition to its audit event. Keys repeat across
        // environments and tenants, so the row carries the same scope.
        Schema::create('config_change', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->string('target_table', 32);
            $table->text('target_key');
            $table->text('environment');
            $table->text('tenant_id')->nullable();
            $table->jsonb('before_value')->nullable();
            $table->jsonb('after_value')->nullable();
            $table->unsignedBigInteger('changed_by');
            $table->text('reason');
            $table->timestampTz('changed_at')->useCurrent();
            $table->unsignedBigInteger('audit_event_id')->nullable();

            $table->foreign('changed_by')->references('id')->on('admin_actor');
            $table->foreign('audit_event_id')->references('id')->on('audit_event');
        });

        DB::statement("ALTER TABLE config_change ADD CONSTRAINT config_change_target_check
            CHECK (target_table IN ('config_setting','feature_flag'))");
        DB::statement('CREATE INDEX config_change_target
            ON config_change (target_table, target_key, environment, changed_at DESC)');

        // -------------------------------------------------------------------
        // Exports and data-subject requests
        // -------------------------------------------------------------------

        Schema::create('export_request', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('export_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->text('resource');
            $table->unsignedBigInteger('requested_by');
            $table->text('tenant_id')->nullable();
            $table->jsonb('filters');
            // Record which policy was applied, not merely that one was. An export
            // is the easiest way for field-level policy to be silently bypassed.
            $table->text('row_policy');
            $table->text('field_policy');
            $table->text('reason');
            $table->string('state', 16)->default('queued');
            $table->integer('row_count')->nullable();
            $table->bigInteger('byte_size')->nullable();
            $table->text('artifact_ref')->nullable();
            $table->timestampTz('requested_at')->useCurrent();
            $table->timestampTz('ready_at')->nullable();
            $table->timestampTz('expires_at');
            $table->integer('download_count')->default(0);
            $table->timestampTz('last_downloaded_at')->nullable();

            $table->foreign('requested_by')->references('id')->on('admin_actor');
            $table->index(['state', 'expires_at'], 'export_request_state');
        });

        DB::statement("ALTER TABLE export_request ADD CONSTRAINT export_request_state_check
            CHECK (state IN ('queued','running','ready','failed','expired','revoked'))");

        // Erasure interacts with retention, legal hold and backups. A request under
        // legal hold cannot be marked fulfilled; the hold never silently drops it.
        // Access and portability requests link their export_request, erasure its
        // admin_job.
        Schema::create('data_subject_request', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('request_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->string('kind', 16);
            $table->text('subject_ref');
            $table->text('tenant_id')->nullable();
            $table->timestampTz('received_at')->useCurrent();
            $table->timestampTz('due_at');
            $table->string('state', 16)->default('open');
            $table->boolean('legal_hold')->default(false);
            $table->unsignedBigInteger('assigned_to')->nullable();
            $table->unsignedBigInteger('export_request_id')->nullable();
            $table->unsignedBigInteger('admin_job_id')->nullable();
            $table->text('resolution')->nullable();
            $table->timestampTz('resolved_at')->nullable();

            $table->foreign('assigned_to')->references('id')->on('admin_actor');
            $table->foreign('export_request_id')->references('id')->on('export_request');
            $table->foreign('admin_job_id')->references('id')->on('admin_job');
            $table->index(['state', 'due_at'], 'data_subject_request_queue');
        });

        DB::statement("ALTER TABLE data_subject_request ADD CONSTRAINT data_subject_request_kind_check
            CHECK (kind IN ('access','erasure','rectification','portability','restriction','objection'))");
        DB::statement("ALTER TABLE data_subject_request ADD CONSTRAINT data_subject_request_state_check
            CHECK (state IN ('open','in-progress','fulfilled','refused','withdrawn'))");
        DB::statement("ALTER TABLE data_subject_request ADD CONSTRAINT data_subject_request_hold_blocks_fulfilment
            CHECK (NOT (legal_hold AND state = 'fulfilled'))");

        // -------------------------------------------------------------------
        // Integrity: hash chain, payload scrub, append-only enforcement
        // -------------------------------------------------------------------
        //
        // A trigger stops ordinary application paths. It does not stop a superuser
        // or anyone who can DROP it. Real tamper evidence needs the hash chain
        // anchored outside this database. See references/admin-data-model.md.
        //
        // The database owns chain_seq, prev_hash, row_hash and the payload hashes
        // (audit_event_chain_link). verify_audit_chain() re-computes the chain;
        // scrub_audit_payload() is the single permitted UPDATE of an audit_event.
        // Copied verbatim from postgres.sql (a test keeps them in step).
        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION audit_event_hash_input(e audit_event)
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

            CREATE OR REPLACE FUNCTION audit_event_chain_link()
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
                  FROM audit_event a
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
                    sha256(convert_to(audit_event_hash_input(NEW), 'UTF8')), 'hex');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER audit_event_chain_link
                BEFORE INSERT ON audit_event
                FOR EACH ROW EXECUTE FUNCTION audit_event_chain_link();

            CREATE OR REPLACE FUNCTION verify_audit_chain(p_chain_id text DEFAULT NULL)
            RETURNS TABLE (chain_id text, chain_seq bigint, problem text)
            LANGUAGE plpgsql STABLE AS $$
            DECLARE
                r         audit_event%ROWTYPE;
                cur_chain text;
                prev_seq  bigint;
                prev_hash text;
            BEGIN
                FOR r IN
                    SELECT * FROM audit_event a
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
                           sha256(convert_to(audit_event_hash_input(r), 'UTF8')), 'hex') THEN
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

            CREATE OR REPLACE FUNCTION scrub_audit_payload(
                p_event_id bigint, p_policy text, p_reason text, p_actor_id bigint DEFAULT NULL)
            RETURNS void
            LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
            DECLARE
                ev audit_event%ROWTYPE;
            BEGIN
                IF p_policy IS NULL OR p_policy IN ('', 'none') THEN
                    RAISE EXCEPTION 'scrub_audit_payload needs a named redaction policy';
                END IF;
                IF p_reason IS NULL OR btrim(p_reason) = '' THEN
                    RAISE EXCEPTION 'scrub_audit_payload needs a reason';
                END IF;
                PERFORM set_config('admin_core.scrub', 'on', true);
                UPDATE audit_event
                   SET before_state = NULL, after_state = NULL, redaction_policy = p_policy
                 WHERE id = p_event_id
                RETURNING * INTO ev;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'audit_event % does not exist', p_event_id;
                END IF;
                PERFORM set_config('admin_core.scrub', 'off', true);

                INSERT INTO audit_event
                    (occurred_at, actor_id, actor_kind, tenant_id, environment,
                     action, target_type, target_id, reason, result)
                VALUES
                    (now(), p_actor_id, 'system', ev.tenant_id, ev.environment,
                     'audit.scrub_payload', 'audit_event', ev.id::text, p_reason, 'succeeded');
            END;
            $$;

            REVOKE ALL ON FUNCTION scrub_audit_payload(bigint, text, text, bigint) FROM PUBLIC;

            CREATE OR REPLACE FUNCTION deny_audit_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION audit_event_guard_update()
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
                BEFORE UPDATE ON audit_event
                FOR EACH ROW EXECUTE FUNCTION audit_event_guard_update();

            CREATE TRIGGER audit_event_no_delete
                BEFORE DELETE ON audit_event
                FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

            CREATE TRIGGER audit_event_no_truncate
                BEFORE TRUNCATE ON audit_event
                FOR EACH STATEMENT EXECUTE FUNCTION deny_audit_mutation();

            CREATE TRIGGER approval_decision_no_update
                BEFORE UPDATE ON approval_decision
                FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

            CREATE TRIGGER approval_decision_no_delete
                BEFORE DELETE ON approval_decision
                FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

            CREATE TRIGGER approval_decision_no_truncate
                BEFORE TRUNCATE ON approval_decision
                FOR EACH STATEMENT EXECUTE FUNCTION deny_audit_mutation();
        SQL);

        // Least-privilege grants for the application role (required at regulated):
        // run grants.sql once after this migration, with psql, e.g.
        //   psql "$DATABASE_URL" -v app_role=admin_app -v schema=public -f grants.sql
    }

    public function down(): void
    {
        DB::unprepared('DROP TRIGGER IF EXISTS audit_event_chain_link ON audit_event');
        DB::unprepared('DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event');
        DB::unprepared('DROP TRIGGER IF EXISTS audit_event_no_delete ON audit_event');
        DB::unprepared('DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_decision_no_update ON approval_decision');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_decision_no_delete ON approval_decision');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_decision_no_truncate ON approval_decision');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_decision_sod ON approval_decision');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_request_immutable ON approval_request');
        DB::unprepared('DROP FUNCTION IF EXISTS approval_request_guard_update()');
        DB::unprepared('DROP FUNCTION IF EXISTS scrub_audit_payload(bigint, text, text, bigint)');
        DB::unprepared('DROP FUNCTION IF EXISTS verify_audit_chain(text)');
        DB::unprepared('DROP FUNCTION IF EXISTS audit_event_guard_update()');
        DB::unprepared('DROP FUNCTION IF EXISTS audit_event_chain_link()');
        DB::unprepared('DROP FUNCTION IF EXISTS audit_event_hash_input(audit_event)');
        DB::unprepared('DROP FUNCTION IF EXISTS deny_audit_mutation()');
        DB::unprepared('DROP FUNCTION IF EXISTS enforce_separation_of_duties()');

        Schema::dropIfExists('data_subject_request');
        Schema::dropIfExists('export_request');
        Schema::dropIfExists('config_change');
        Schema::dropIfExists('feature_flag');
        Schema::dropIfExists('config_setting');
        Schema::dropIfExists('saved_view');
        Schema::dropIfExists('admin_job_failure');
        Schema::dropIfExists('admin_job');
        Schema::dropIfExists('approval_decision');
        Schema::dropIfExists('approval_request');
        Schema::dropIfExists('audit_event');
        Schema::dropIfExists('impersonation_session');
        Schema::dropIfExists('admin_policy_version');
        Schema::dropIfExists('admin_actor_role');
        Schema::dropIfExists('admin_role_permission');
        Schema::dropIfExists('admin_permission');
        Schema::dropIfExists('admin_role');
        Schema::dropIfExists('admin_actor');
    }
};
