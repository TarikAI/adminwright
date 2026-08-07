# frozen_string_literal: true

# Adminwright admin control-plane core schema — Rails migration.
#
# Faithful translation of postgres.sql. Read ./README.md before adapting.
#
# WHAT RAILS EXPRESSES NATIVELY:
#   * CHECK constraints via `t.check_constraint` / `add_check_constraint`
#   * Partial unique indexes via `add_index ..., where:`
#
# WHAT NEEDS RAW SQL — included below via `execute`:
#   * Triggers: append-only enforcement on audit_event and approval_decision, and
#     the separation-of-duties trigger. ActiveRecord callbacks are bypassed by
#     `update_all`, `delete_all`, raw SQL, and other services.
#
# REQUIRED: set `config.active_record.schema_format = :sql` in application.rb.
# With the default :ruby format, `db/schema.rb` cannot represent triggers or
# functions, and they will be silently lost the next time the schema is loaded.
#
# PostgreSQL only. Adapt table names to project convention, but keep
# admin_permission.key equal to the manifest capability id.

class CreateAdminCoreSchema < ActiveRecord::Migration[7.1]
  def up
    enable_extension 'pgcrypto' unless extension_enabled?('pgcrypto')

    # -----------------------------------------------------------------------
    # Actors, roles, permissions, grants
    # -----------------------------------------------------------------------

    # The privileged identity. Drop this table and add is_privileged /
    # auth_strength / mfa_enrolled to the app's users table when operators share
    # the product's lifecycle and identity provider.
    # Credentials are never stored here; auth_subject holds the IdP subject claim.
    create_table :admin_actor do |t|
      t.uuid    :actor_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.bigint  :app_user_id
      t.text    :auth_subject, null: false
      t.text    :display_name, null: false
      t.text    :email
      # Automation and autonomous agents are first-class subjects. They never
      # inherit a human's authority.
      t.string  :kind, limit: 16, null: false, default: 'human'
      t.string  :auth_strength, limit: 32, null: false, default: 'password'
      t.boolean :mfa_enrolled, null: false, default: false
      t.boolean :is_active, null: false, default: true
      t.timestamptz :deactivated_at
      t.timestamptz :created_at, null: false, default: -> { 'now()' }

      t.index :actor_uuid, unique: true
      t.index :auth_subject, unique: true
      t.check_constraint "kind IN ('human','service','agent')", name: 'admin_actor_kind'
      t.check_constraint "auth_strength IN ('password','mfa','phishing-resistant','sso')",
                         name: 'admin_actor_auth_strength'
    end

    create_table :admin_role do |t|
      t.text    :key, null: false           # matches manifest roles[].id
      t.text    :name, null: false
      t.text    :description, null: false, default: ''
      t.boolean :requires_mfa, null: false, default: true
      t.jsonb   :max_scope, null: false, default: {}
      t.boolean :is_active, null: false, default: true

      t.index :key, unique: true
    end

    # The atom of authorization. Key it <resource>.<action> so it equals the
    # manifest capability id exactly; that equality is what makes
    # capability -> permission -> audit_event.action traceable.
    # Obligations live here, not at the call site, so a new call site cannot
    # silently skip them.
    create_table :admin_permission do |t|
      t.text    :key, null: false           # manifest capability.id
      t.text    :resource, null: false      # manifest entity id
      t.text    :action, null: false
      t.string  :risk, limit: 16, null: false, default: 'low'
      t.boolean :requires_reason, null: false, default: false
      t.boolean :requires_step_up, null: false, default: false
      t.boolean :requires_approval, null: false, default: false
      t.text    :description, null: false, default: ''

      t.index :key, unique: true
      t.check_constraint "risk IN ('low','moderate','high','critical')",
                         name: 'admin_permission_risk'
    end

    create_table :admin_role_permission, primary_key: %i[role_id permission_id] do |t|
      t.references :role, null: false, foreign_key: { to_table: :admin_role, on_delete: :cascade }
      t.references :permission, null: false,
                   foreign_key: { to_table: :admin_permission, on_delete: :cascade }
    end

    # A grant, not a join row: where it applies, when it expires, who granted it
    # and why, and how it was revoked. Revocation is a state change, never a
    # delete.
    create_table :admin_actor_role do |t|
      t.bigint  :actor_id, null: false
      t.bigint  :role_id, null: false
      t.jsonb   :scope, null: false, default: {}
      t.text    :scope_tenant_id                    # denormalized hot path
      t.timestamptz :starts_at, null: false, default: -> { 'now()' }
      t.timestamptz :expires_at                     # null = permanent
      t.bigint  :granted_by, null: false
      t.text    :granted_reason, null: false
      t.text    :ticket_ref
      t.timestamptz :granted_at, null: false, default: -> { 'now()' }
      t.timestamptz :revoked_at
      t.bigint  :revoked_by
      t.text    :revoke_reason

      t.foreign_key :admin_actor, column: :actor_id
      t.foreign_key :admin_role, column: :role_id
      t.foreign_key :admin_actor, column: :granted_by
      t.foreign_key :admin_actor, column: :revoked_by

      # No self-granting. Privilege escalation by an operator on their own
      # account is the failure this prevents.
      t.check_constraint 'granted_by <> actor_id', name: 'admin_actor_role_no_self_grant'
      t.check_constraint '(revoked_at IS NULL) = (revoked_by IS NULL)',
                         name: 'admin_actor_role_revocation_complete'
      t.check_constraint 'expires_at IS NULL OR expires_at > starts_at',
                         name: 'admin_actor_role_window'
    end

    # One live grant of a role per actor per tenant scope. Duplicate live grants
    # make revocation unreliable: revoking one leaves the other in force.
    execute <<~SQL
      CREATE UNIQUE INDEX admin_actor_role_live_unique
        ON admin_actor_role (actor_id, role_id, COALESCE(scope_tenant_id, '*'))
        WHERE revoked_at IS NULL;
    SQL
    add_index :admin_actor_role, :actor_id, where: 'revoked_at IS NULL',
              name: 'admin_actor_role_live_lookup'

    # Which rules were in force when a decision was made. audit_event
    # .policy_version points here so a past decision stays explainable after the
    # policy changes.
    create_table :admin_policy_version do |t|
      t.text :version, null: false
      t.text :source_ref, null: false   # manifest capability.authorizationPolicies[]
      t.text :checksum, null: false
      t.timestamptz :activated_at, null: false, default: -> { 'now()' }
      t.timestamptz :retired_at

      t.index :version, unique: true
    end

    # -----------------------------------------------------------------------
    # Impersonation (before audit_event for the FK)
    # -----------------------------------------------------------------------

    create_table :impersonation_session do |t|
      t.uuid   :session_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.bigint :real_actor_id, null: false
      t.text   :target_subject_id, null: false
      t.text   :target_tenant_id
      t.text   :reason, null: false
      t.text   :ticket_ref
      t.jsonb  :scope_restrictions, null: false, default: {}
      t.timestamptz :started_at, null: false, default: -> { 'now()' }
      t.timestamptz :expires_at, null: false
      t.timestamptz :ended_at
      t.text   :ended_reason
      t.bigint :revoked_by

      t.foreign_key :admin_actor, column: :real_actor_id
      t.foreign_key :admin_actor, column: :revoked_by
      t.index :session_uuid, unique: true
      t.check_constraint 'expires_at > started_at', name: 'impersonation_bounded'
    end

    add_index :impersonation_session, %i[real_actor_id expires_at],
              where: 'ended_at IS NULL', name: 'impersonation_session_active'

    # -----------------------------------------------------------------------
    # Audit
    # -----------------------------------------------------------------------

    create_table :audit_event do |t|
      t.uuid   :event_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.text   :chain_id, null: false, default: 'default'
      t.bigint :chain_seq, null: false

      t.timestamptz :occurred_at, null: false
      t.timestamptz :recorded_at, null: false, default: -> { 'now()' }

      t.bigint :actor_id
      t.string :actor_kind, limit: 16, null: false, default: 'human'
      # Null unless impersonating. Never overwrite actor_id with the impersonated
      # identity: that is the most common way impersonation audit is destroyed.
      t.bigint :effective_actor_id
      t.bigint :impersonation_session_id

      t.text   :tenant_id
      t.text   :environment, null: false
      t.inet   :source_ip
      t.text   :user_agent

      t.text   :action, null: false      # admin_permission.key, never free text
      t.text   :target_type, null: false
      t.text   :target_id

      t.text   :reason
      t.text   :ticket_ref
      t.bigint :approval_request_id

      t.text   :request_id
      t.text   :correlation_id
      t.text   :idempotency_key

      t.string :result, limit: 16, null: false
      t.text   :error_code

      t.jsonb  :before_state
      t.jsonb  :after_state
      # Hashes of the payloads; row_hash covers the hashes rather than the payload
      # text, which is what makes later redaction possible without invalidating
      # the chain.
      t.text   :before_hash
      t.text   :after_hash
      t.text   :redaction_policy, null: false, default: 'none'

      t.text   :policy_version

      t.text   :prev_hash
      t.text   :row_hash, null: false
      t.text   :hash_algorithm, null: false, default: 'sha256'

      t.foreign_key :admin_actor, column: :actor_id
      t.foreign_key :admin_actor, column: :effective_actor_id
      t.foreign_key :impersonation_session, column: :impersonation_session_id

      t.index :event_uuid, unique: true
      t.index %i[chain_id chain_seq], unique: true, name: 'audit_event_chain'

      t.check_constraint "actor_kind IN ('human','service','agent','system')",
                         name: 'audit_event_actor_kind'
      # 'denied' lets you detect probing; 'unknown' stops a provider timeout being
      # misreported as a failure when the provider may still have acted.
      t.check_constraint "result IN ('succeeded','failed','denied','partial','unknown')",
                         name: 'audit_event_result'
      t.check_constraint 'effective_actor_id IS NULL OR impersonation_session_id IS NOT NULL',
                         name: 'audit_event_effective_actor_requires_session'
    end

    # The narrow, predictable query set. Every extra index taxes the hottest
    # insert path in the system, so add nothing speculative.
    execute 'CREATE INDEX audit_event_recent ON audit_event (occurred_at DESC, id DESC)'
    execute 'CREATE INDEX audit_event_target ON audit_event (target_type, target_id, occurred_at DESC)'
    execute 'CREATE INDEX audit_event_actor ON audit_event (actor_id, occurred_at DESC)'
    execute 'CREATE INDEX audit_event_tenant ON audit_event (tenant_id, occurred_at DESC) WHERE tenant_id IS NOT NULL'
    execute 'CREATE INDEX audit_event_action ON audit_event (action, occurred_at DESC)'
    add_index :audit_event, :correlation_id, where: 'correlation_id IS NOT NULL',
              name: 'audit_event_correlation'
    add_index :audit_event, :request_id, where: 'request_id IS NOT NULL',
              name: 'audit_event_request'
    add_index :audit_event, :idempotency_key, where: 'idempotency_key IS NOT NULL',
              name: 'audit_event_idempotency'

    # Paginate by keyset on (occurred_at, id), not offset. Offset pagination on an
    # append-heavy table shifts rows under the operator and degrades linearly.

    # -----------------------------------------------------------------------
    # Approvals and separation of duties
    # -----------------------------------------------------------------------

    create_table :approval_request do |t|
      t.uuid   :request_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.text   :command_key, null: false
      t.jsonb  :payload, null: false
      t.text   :payload_hash, null: false
      t.text   :target_type, null: false
      t.text   :target_id
      t.text   :tenant_id
      t.bigint :requested_by, null: false
      t.timestamptz :requested_at, null: false, default: -> { 'now()' }
      t.text   :reason, null: false
      t.integer :required_approvals, limit: 2, null: false, default: 1
      t.string :state, limit: 16, null: false, default: 'pending'
      t.timestamptz :expires_at, null: false
      t.timestamptz :executed_at
      t.text   :execution_result

      t.foreign_key :admin_permission, column: :command_key, primary_key: :key
      t.foreign_key :admin_actor, column: :requested_by
      t.index :request_uuid, unique: true

      t.check_constraint 'required_approvals >= 1', name: 'approval_request_min_approvals'
      t.check_constraint "state IN ('pending','approved','rejected','expired','executed','cancelled')",
                         name: 'approval_request_state'
    end

    add_index :approval_request, %i[state expires_at], where: "state = 'pending'",
              name: 'approval_request_queue'

    # Immutable once written. A reversal is a new request, never an edit.
    create_table :approval_decision do |t|
      t.bigint :request_id, null: false
      t.bigint :approver_id, null: false
      t.string :decision, limit: 16, null: false
      t.text   :reason, null: false
      t.timestamptz :decided_at, null: false, default: -> { 'now()' }

      t.foreign_key :approval_request, column: :request_id, on_delete: :cascade
      t.foreign_key :admin_actor, column: :approver_id
      # One approver decides once. Without this a single approver satisfies a
      # two-approval requirement by voting twice.
      t.index %i[request_id approver_id], unique: true,
              name: 'approval_decision_one_per_approver'
      t.check_constraint "decision IN ('approved','rejected')", name: 'approval_decision_kind'
    end

    # Separation of duties: the requester may not approve their own request.
    # Enforced in the database because that is the whole point of the control.
    execute <<~SQL
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
    SQL

    # -----------------------------------------------------------------------
    # Jobs
    # -----------------------------------------------------------------------

    create_table :admin_job do |t|
      t.uuid   :job_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.text   :kind, null: false        # manifest capability.id where kind = job
      t.bigint :initiated_by, null: false
      t.text   :tenant_id
      t.text   :environment, null: false
      t.jsonb  :input_summary, null: false
      t.text   :input_hash, null: false
      t.text   :idempotency_key
      t.text   :correlation_id
      t.string :state, limit: 16, null: false, default: 'queued'
      t.integer :total_count
      t.integer :processed_count, null: false, default: 0
      t.integer :failed_count, null: false, default: 0
      t.timestamptz :queued_at, null: false, default: -> { 'now()' }
      t.timestamptz :started_at
      t.timestamptz :finished_at
      t.text   :result_artifact
      t.boolean :cancel_requested, null: false, default: false

      t.foreign_key :admin_actor, column: :initiated_by
      t.index :job_uuid, unique: true
      t.check_constraint "state IN ('queued','running','succeeded','partial','failed','cancelled','expired')",
                         name: 'admin_job_state'
      t.check_constraint 'processed_count >= 0 AND failed_count >= 0', name: 'admin_job_counts'
    end

    # Duplicate submission protection: a repeated key returns the existing job
    # rather than starting a second one.
    add_index :admin_job, %i[kind idempotency_key], unique: true,
              where: 'idempotency_key IS NOT NULL', name: 'admin_job_idempotency'
    execute <<~SQL
      CREATE INDEX admin_job_active ON admin_job (state, queued_at DESC)
        WHERE state IN ('queued','running');
    SQL

    # Per-item failures. A job that reports only an aggregate count cannot tell
    # the operator which rows to retry.
    create_table :admin_job_failure do |t|
      t.bigint :job_id, null: false
      t.text   :target_type, null: false
      t.text   :target_id, null: false
      t.text   :error_code, null: false
      t.text   :error_detail
      t.timestamptz :failed_at, null: false, default: -> { 'now()' }

      t.foreign_key :admin_job, column: :job_id, on_delete: :cascade
      t.index %i[job_id failed_at], name: 'admin_job_failure_job'
    end

    # -----------------------------------------------------------------------
    # Saved views and configuration
    # -----------------------------------------------------------------------

    # A shared saved view is a disclosure surface: it can carry one tenant's
    # filter values to another operator. Apply the same row policy when loading.
    create_table :saved_view do |t|
      t.bigint :owner_id, null: false
      t.text   :route, null: false        # manifest screens[].route
      t.text   :name, null: false
      t.jsonb  :filters, null: false, default: {}
      t.jsonb  :columns, null: false, default: []
      t.jsonb  :sort, null: false, default: []
      t.string :visibility, limit: 16, null: false, default: 'private'
      t.text   :tenant_id
      t.timestamptz :created_at, null: false, default: -> { 'now()' }
      t.timestamptz :updated_at, null: false, default: -> { 'now()' }

      t.foreign_key :admin_actor, column: :owner_id, on_delete: :cascade
      t.index %i[owner_id route name], unique: true, name: 'saved_view_name_unique'
      t.check_constraint "visibility IN ('private','shared')", name: 'saved_view_visibility'
    end

    create_table :config_setting do |t|
      t.text    :key, null: false
      t.jsonb   :value, null: false
      t.text    :value_type, null: false
      # Secret values are never displayed after write. Support rotation and
      # revocation instead.
      t.boolean :is_secret, null: false, default: false
      t.text    :tenant_id
      t.text    :environment, null: false
      t.timestamptz :updated_at, null: false, default: -> { 'now()' }

      t.index :key, unique: true
    end

    create_table :feature_flag do |t|
      t.text    :key, null: false
      t.text    :description, null: false, default: ''
      t.boolean :is_enabled, null: false, default: false
      t.jsonb   :rollout, null: false, default: {}
      t.text    :environment, null: false
      t.timestamptz :updated_at, null: false, default: -> { 'now()' }

      t.index :key, unique: true
    end

    # Every configuration change is a privileged mutation and gets its own
    # history row in addition to its audit event.
    create_table :config_change do |t|
      t.string :target_table, limit: 32, null: false
      t.text   :target_key, null: false
      t.jsonb  :before_value
      t.jsonb  :after_value
      t.bigint :changed_by, null: false
      t.text   :reason, null: false
      t.timestamptz :changed_at, null: false, default: -> { 'now()' }
      t.bigint :audit_event_id

      t.foreign_key :admin_actor, column: :changed_by
      t.foreign_key :audit_event, column: :audit_event_id
      t.check_constraint "target_table IN ('config_setting','feature_flag')",
                         name: 'config_change_target'
    end

    execute <<~SQL
      CREATE INDEX config_change_target_idx
        ON config_change (target_table, target_key, changed_at DESC);
    SQL

    # -----------------------------------------------------------------------
    # Exports and data-subject requests
    # -----------------------------------------------------------------------

    create_table :export_request do |t|
      t.uuid   :export_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.text   :resource, null: false
      t.bigint :requested_by, null: false
      t.text   :tenant_id
      t.jsonb  :filters, null: false
      # Record which policy was applied, not merely that one was. An export is
      # the easiest way for field-level policy to be silently bypassed.
      t.text   :row_policy, null: false
      t.text   :field_policy, null: false
      t.text   :reason, null: false
      t.string :state, limit: 16, null: false, default: 'queued'
      t.integer :row_count
      t.bigint :byte_size
      t.text   :artifact_ref
      t.timestamptz :requested_at, null: false, default: -> { 'now()' }
      t.timestamptz :ready_at
      t.timestamptz :expires_at, null: false
      t.integer :download_count, null: false, default: 0
      t.timestamptz :last_downloaded_at

      t.foreign_key :admin_actor, column: :requested_by
      t.index :export_uuid, unique: true
      t.index %i[state expires_at], name: 'export_request_state'
      t.check_constraint "state IN ('queued','running','ready','failed','expired','revoked')",
                         name: 'export_request_state_check'
    end

    # Erasure interacts with retention, legal hold and backups. legal_hold blocks
    # fulfilment; it does not silently drop the request.
    create_table :data_subject_request do |t|
      t.uuid   :request_uuid, null: false, default: -> { 'gen_random_uuid()' }
      t.string :kind, limit: 16, null: false
      t.text   :subject_ref, null: false
      t.text   :tenant_id
      t.timestamptz :received_at, null: false, default: -> { 'now()' }
      t.timestamptz :due_at, null: false
      t.string :state, limit: 16, null: false, default: 'open'
      t.boolean :legal_hold, null: false, default: false
      t.bigint :assigned_to
      t.text   :resolution
      t.timestamptz :resolved_at

      t.foreign_key :admin_actor, column: :assigned_to
      t.index :request_uuid, unique: true
      t.index %i[state due_at], name: 'data_subject_request_queue'
      t.check_constraint "kind IN ('access','erasure','rectification','portability','restriction','objection')",
                         name: 'data_subject_request_kind'
      t.check_constraint "state IN ('open','in-progress','fulfilled','refused','withdrawn')",
                         name: 'data_subject_request_state'
    end

    # -----------------------------------------------------------------------
    # Immutability
    # -----------------------------------------------------------------------
    #
    # A trigger stops ordinary application paths. It does not stop a superuser or
    # anyone who can DROP it. Real tamper evidence needs the hash chain anchored
    # outside this database. See references/admin-data-model.md.
    execute <<~SQL
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
    SQL

    # Grant shape for the application role. Adapt the role name.
    #   REVOKE UPDATE, DELETE ON audit_event FROM app_role;
    #   GRANT  INSERT, SELECT ON audit_event TO app_role;
  end

  def down
    execute 'DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event'
    execute 'DROP TRIGGER IF EXISTS audit_event_no_delete ON audit_event'
    execute 'DROP TRIGGER IF EXISTS approval_decision_no_update ON approval_decision'
    execute 'DROP TRIGGER IF EXISTS approval_decision_sod ON approval_decision'
    execute 'DROP FUNCTION IF EXISTS deny_audit_mutation()'
    execute 'DROP FUNCTION IF EXISTS enforce_separation_of_duties()'

    %i[data_subject_request export_request config_change feature_flag config_setting
       saved_view admin_job_failure admin_job approval_decision approval_request
       audit_event impersonation_session admin_policy_version admin_actor_role
       admin_role_permission admin_permission admin_role admin_actor].each do |table|
      drop_table table, if_exists: true
    end
  end
end
