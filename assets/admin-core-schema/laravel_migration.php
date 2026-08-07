<?php

/**
 * Adminwright admin control-plane core schema — Laravel migration.
 *
 * Faithful translation of postgres.sql. Read ./README.md before adapting.
 *
 * WHAT THE SCHEMA BUILDER CANNOT EXPRESS — done here via DB::statement():
 *
 *   1. CHECK constraints. Laravel's schema builder has no CHECK support.
 *   2. Partial unique indexes. `$table->unique()` cannot carry a WHERE clause.
 *   3. Triggers. Append-only enforcement on audit_event and approval_decision,
 *      and the separation-of-duties trigger. Model events and observers are
 *      bypassed by query-builder writes, bulk operations, and other services.
 *
 * All three are included below. Do not drop them: they are the controls, not
 * decoration.
 *
 * PostgreSQL only. Adapt table names to project convention, but keep
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

        DB::statement("ALTER TABLE admin_actor ADD CONSTRAINT admin_actor_kind
            CHECK (kind IN ('human','service','agent'))");
        DB::statement("ALTER TABLE admin_actor ADD CONSTRAINT admin_actor_auth_strength
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

        DB::statement("ALTER TABLE admin_permission ADD CONSTRAINT admin_permission_risk
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
        DB::statement('CREATE INDEX impersonation_session_active
            ON impersonation_session (real_actor_id, expires_at) WHERE ended_at IS NULL');

        // -------------------------------------------------------------------
        // Audit
        // -------------------------------------------------------------------

        Schema::create('audit_event', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->uuid('event_uuid')->unique()->default(DB::raw('gen_random_uuid()'));
            $table->text('chain_id')->default('default');
            $table->bigInteger('chain_seq');

            $table->timestampTz('occurred_at');
            $table->timestampTz('recorded_at')->useCurrent();

            $table->unsignedBigInteger('actor_id')->nullable();
            $table->string('actor_kind', 16)->default('human');
            // Null unless impersonating. Never overwrite actor_id with the
            // impersonated identity: that is the most common way impersonation
            // audit is destroyed.
            $table->unsignedBigInteger('effective_actor_id')->nullable();
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

            $table->text('prev_hash')->nullable();
            $table->text('row_hash');
            $table->text('hash_algorithm')->default('sha256');

            $table->foreign('actor_id')->references('id')->on('admin_actor');
            $table->foreign('effective_actor_id')->references('id')->on('admin_actor');
            $table->foreign('impersonation_session_id')->references('id')->on('impersonation_session');

            $table->unique(['chain_id', 'chain_seq'], 'audit_event_chain');
        });

        DB::statement("ALTER TABLE audit_event ADD CONSTRAINT audit_event_actor_kind
            CHECK (actor_kind IN ('human','service','agent','system'))");
        // 'denied' lets you detect probing; 'unknown' stops a provider timeout
        // being misreported as a failure when the provider may still have acted.
        DB::statement("ALTER TABLE audit_event ADD CONSTRAINT audit_event_result
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
        DB::statement("ALTER TABLE approval_request ADD CONSTRAINT approval_request_state
            CHECK (state IN ('pending','approved','rejected','expired','executed','cancelled'))");
        DB::statement("CREATE INDEX approval_request_queue
            ON approval_request (state, expires_at) WHERE state = 'pending'");

        Schema::create('approval_decision', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('request_id');
            $table->unsignedBigInteger('approver_id');
            $table->string('decision', 16);
            $table->text('reason');
            $table->timestampTz('decided_at')->useCurrent();

            $table->foreign('request_id')->references('id')->on('approval_request')->cascadeOnDelete();
            $table->foreign('approver_id')->references('id')->on('admin_actor');
            // One approver decides once. Without this a single approver satisfies
            // a two-approval requirement by voting twice.
            $table->unique(['request_id', 'approver_id'], 'approval_decision_one_per_approver');
        });

        DB::statement("ALTER TABLE approval_decision ADD CONSTRAINT approval_decision_kind
            CHECK (decision IN ('approved','rejected'))");

        // Separation of duties: the requester may not approve their own request.
        // Enforced in the database because that is the whole point of the control.
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

        DB::statement("ALTER TABLE admin_job ADD CONSTRAINT admin_job_state
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
        // the operator which rows to retry.
        Schema::create('admin_job_failure', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->unsignedBigInteger('job_id');
            $table->text('target_type');
            $table->text('target_id');
            $table->text('error_code');
            $table->text('error_detail')->nullable();
            $table->timestampTz('failed_at')->useCurrent();

            $table->foreign('job_id')->references('id')->on('admin_job')->cascadeOnDelete();
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

        DB::statement("ALTER TABLE saved_view ADD CONSTRAINT saved_view_visibility
            CHECK (visibility IN ('private','shared'))");

        Schema::create('config_setting', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('key')->unique();
            $table->jsonb('value');
            $table->text('value_type');
            // Secret values are never displayed after write. Support rotation and
            // revocation instead.
            $table->boolean('is_secret')->default(false);
            $table->text('tenant_id')->nullable();
            $table->text('environment');
            $table->timestampTz('updated_at')->useCurrent();
        });

        Schema::create('feature_flag', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->text('key')->unique();
            $table->text('description')->default('');
            $table->boolean('is_enabled')->default(false);
            $table->jsonb('rollout')->default(DB::raw("'{}'::jsonb"));
            $table->text('environment');
            $table->timestampTz('updated_at')->useCurrent();
        });

        // Every configuration change is a privileged mutation and gets its own
        // history row in addition to its audit event.
        Schema::create('config_change', function (Blueprint $table) {
            $table->bigIncrements('id');
            $table->string('target_table', 32);
            $table->text('target_key');
            $table->jsonb('before_value')->nullable();
            $table->jsonb('after_value')->nullable();
            $table->unsignedBigInteger('changed_by');
            $table->text('reason');
            $table->timestampTz('changed_at')->useCurrent();
            $table->unsignedBigInteger('audit_event_id')->nullable();

            $table->foreign('changed_by')->references('id')->on('admin_actor');
            $table->foreign('audit_event_id')->references('id')->on('audit_event');
        });

        DB::statement("ALTER TABLE config_change ADD CONSTRAINT config_change_target
            CHECK (target_table IN ('config_setting','feature_flag'))");
        DB::statement('CREATE INDEX config_change_target_idx
            ON config_change (target_table, target_key, changed_at DESC)');

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

        // Erasure interacts with retention, legal hold and backups. legal_hold
        // blocks fulfilment; it does not silently drop the request.
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
            $table->text('resolution')->nullable();
            $table->timestampTz('resolved_at')->nullable();

            $table->foreign('assigned_to')->references('id')->on('admin_actor');
            $table->index(['state', 'due_at'], 'data_subject_request_queue');
        });

        DB::statement("ALTER TABLE data_subject_request ADD CONSTRAINT data_subject_request_kind
            CHECK (kind IN ('access','erasure','rectification','portability','restriction','objection'))");
        DB::statement("ALTER TABLE data_subject_request ADD CONSTRAINT data_subject_request_state
            CHECK (state IN ('open','in-progress','fulfilled','refused','withdrawn'))");

        // -------------------------------------------------------------------
        // Immutability
        // -------------------------------------------------------------------
        //
        // A trigger stops ordinary application paths. It does not stop a superuser
        // or anyone who can DROP it. Real tamper evidence needs the hash chain
        // anchored outside this database. See references/admin-data-model.md.
        DB::unprepared(<<<'SQL'
            CREATE OR REPLACE FUNCTION deny_audit_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit_event is append-only; % is not permitted', TG_OP;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER audit_event_no_update
                BEFORE UPDATE ON audit_event
                FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

            CREATE TRIGGER audit_event_no_delete
                BEFORE DELETE ON audit_event
                FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

            CREATE TRIGGER approval_decision_no_update
                BEFORE UPDATE ON approval_decision
                FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();
        SQL);

        // Grant shape for the application role. Adapt the role name.
        //   REVOKE UPDATE, DELETE ON audit_event FROM app_role;
        //   GRANT  INSERT, SELECT ON audit_event TO app_role;
    }

    public function down(): void
    {
        DB::unprepared('DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event');
        DB::unprepared('DROP TRIGGER IF EXISTS audit_event_no_delete ON audit_event');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_decision_no_update ON approval_decision');
        DB::unprepared('DROP TRIGGER IF EXISTS approval_decision_sod ON approval_decision');
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
