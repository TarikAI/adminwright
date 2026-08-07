// Adminwright admin control-plane core schema — Drizzle ORM (PostgreSQL)
//
// Faithful translation of postgres.sql. Read ./README.md before adapting.
//
// WHAT DRIZZLE CANNOT ENFORCE — add these in a hand-written migration:
//
//   1. Triggers. Append-only enforcement on auditEvent and approvalDecision, and
//      the separation-of-duties trigger on approvalDecision. Application-level
//      guards are bypassed by raw queries and other services.
//   2. Nothing else. Drizzle expresses CHECK constraints (`check()`) and partial
//      unique indexes (`.where()`) natively, and both are used below.
//
// Generate the migration, then append the trigger statements from postgres.sql:
//   npx drizzle-kit generate
//   # edit the emitted .sql and add the three triggers

import {
  pgSchema,
  bigserial,
  bigint,
  text,
  boolean,
  timestamp,
  jsonb,
  integer,
  smallint,
  uuid,
  inet,
  index,
  uniqueIndex,
  primaryKey,
  check,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

export const adminCore = pgSchema("admin_core");

// ---------------------------------------------------------------------------
// Actors, roles, permissions, grants
// ---------------------------------------------------------------------------

/**
 * The privileged identity. Drop this table and add isPrivileged / authStrength /
 * mfaEnrolled to the app's user table when operators share the product's
 * lifecycle and identity provider.
 * Credentials are never stored here; authSubject holds the IdP subject claim.
 */
export const adminActor = adminCore.table("admin_actor", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  actorUuid: uuid("actor_uuid").notNull().defaultRandom().unique(),
  appUserId: bigint("app_user_id", { mode: "bigint" }),
  authSubject: text("auth_subject").notNull().unique(),
  displayName: text("display_name").notNull(),
  email: text("email"),
  // Automation and autonomous agents are first-class subjects. They never
  // inherit a human's authority.
  kind: text("kind").notNull().default("human"),
  authStrength: text("auth_strength").notNull().default("password"),
  mfaEnrolled: boolean("mfa_enrolled").notNull().default(false),
  isActive: boolean("is_active").notNull().default(true),
  deactivatedAt: timestamp("deactivated_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
}, (t) => [
  check("admin_actor_kind", sql`${t.kind} IN ('human','service','agent')`),
  check(
    "admin_actor_auth_strength",
    sql`${t.authStrength} IN ('password','mfa','phishing-resistant','sso')`,
  ),
]);

export const adminRole = adminCore.table("admin_role", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  key: text("key").notNull().unique(), // matches manifest roles[].id
  name: text("name").notNull(),
  description: text("description").notNull().default(""),
  requiresMfa: boolean("requires_mfa").notNull().default(true),
  maxScope: jsonb("max_scope").notNull().default(sql`'{}'::jsonb`),
  isActive: boolean("is_active").notNull().default(true),
});

/**
 * The atom of authorization. Key it <resource>.<action> so it equals the manifest
 * capability id exactly; that equality is what makes
 * capability -> permission -> auditEvent.action traceable.
 * Obligations live on the permission, not at the call site, so a new call site
 * cannot silently skip them.
 */
export const adminPermission = adminCore.table("admin_permission", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  key: text("key").notNull().unique(), // manifest capability.id
  resource: text("resource").notNull(), // manifest entity id
  action: text("action").notNull(),
  risk: text("risk").notNull().default("low"),
  requiresReason: boolean("requires_reason").notNull().default(false),
  requiresStepUp: boolean("requires_step_up").notNull().default(false),
  requiresApproval: boolean("requires_approval").notNull().default(false),
  description: text("description").notNull().default(""),
}, (t) => [
  check("admin_permission_risk", sql`${t.risk} IN ('low','moderate','high','critical')`),
]);

export const adminRolePermission = adminCore.table("admin_role_permission", {
  roleId: bigint("role_id", { mode: "bigint" })
    .notNull()
    .references(() => adminRole.id, { onDelete: "cascade" }),
  permissionId: bigint("permission_id", { mode: "bigint" })
    .notNull()
    .references(() => adminPermission.id, { onDelete: "cascade" }),
}, (t) => [primaryKey({ columns: [t.roleId, t.permissionId] })]);

/**
 * A grant, not a join row: where it applies, when it expires, who granted it and
 * why, and how it was revoked. Revocation is a state change, never a delete.
 */
export const adminActorRole = adminCore.table("admin_actor_role", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  actorId: bigint("actor_id", { mode: "bigint" }).notNull().references(() => adminActor.id),
  roleId: bigint("role_id", { mode: "bigint" }).notNull().references(() => adminRole.id),
  scope: jsonb("scope").notNull().default(sql`'{}'::jsonb`),
  scopeTenantId: text("scope_tenant_id"), // denormalized hot path
  startsAt: timestamp("starts_at", { withTimezone: true }).notNull().defaultNow(),
  expiresAt: timestamp("expires_at", { withTimezone: true }), // null = permanent
  grantedBy: bigint("granted_by", { mode: "bigint" }).notNull().references(() => adminActor.id),
  grantedReason: text("granted_reason").notNull(),
  ticketRef: text("ticket_ref"),
  grantedAt: timestamp("granted_at", { withTimezone: true }).notNull().defaultNow(),
  revokedAt: timestamp("revoked_at", { withTimezone: true }),
  revokedBy: bigint("revoked_by", { mode: "bigint" }).references(() => adminActor.id),
  revokeReason: text("revoke_reason"),
}, (t) => [
  // No self-granting. Privilege escalation by an operator on their own account is
  // the failure this prevents.
  check("admin_actor_role_no_self_grant", sql`${t.grantedBy} <> ${t.actorId}`),
  check(
    "admin_actor_role_revocation_complete",
    sql`(${t.revokedAt} IS NULL) = (${t.revokedBy} IS NULL)`,
  ),
  check(
    "admin_actor_role_window",
    sql`${t.expiresAt} IS NULL OR ${t.expiresAt} > ${t.startsAt}`,
  ),
  // One live grant of a role per actor per tenant scope. Duplicate live grants
  // make revocation unreliable: revoking one leaves the other in force.
  uniqueIndex("admin_actor_role_live_unique")
    .on(t.actorId, t.roleId, sql`COALESCE(${t.scopeTenantId}, '*')`)
    .where(sql`${t.revokedAt} IS NULL`),
  index("admin_actor_role_live_lookup").on(t.actorId).where(sql`${t.revokedAt} IS NULL`),
]);

/**
 * Which rules were in force when a decision was made. auditEvent.policyVersion
 * points here so a past decision stays explainable after the policy changes.
 */
export const adminPolicyVersion = adminCore.table("admin_policy_version", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  version: text("version").notNull().unique(),
  sourceRef: text("source_ref").notNull(), // manifest capability.authorizationPolicies[]
  checksum: text("checksum").notNull(),
  activatedAt: timestamp("activated_at", { withTimezone: true }).notNull().defaultNow(),
  retiredAt: timestamp("retired_at", { withTimezone: true }),
});

// ---------------------------------------------------------------------------
// Impersonation (declared before auditEvent for the FK reference)
// ---------------------------------------------------------------------------

export const impersonationSession = adminCore.table("impersonation_session", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  sessionUuid: uuid("session_uuid").notNull().defaultRandom().unique(),
  realActorId: bigint("real_actor_id", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id),
  targetSubjectId: text("target_subject_id").notNull(),
  targetTenantId: text("target_tenant_id"),
  reason: text("reason").notNull(),
  ticketRef: text("ticket_ref"),
  scopeRestrictions: jsonb("scope_restrictions").notNull().default(sql`'{}'::jsonb`),
  startedAt: timestamp("started_at", { withTimezone: true }).notNull().defaultNow(),
  expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  endedAt: timestamp("ended_at", { withTimezone: true }),
  endedReason: text("ended_reason"),
  revokedBy: bigint("revoked_by", { mode: "bigint" }).references(() => adminActor.id),
}, (t) => [
  check("impersonation_bounded", sql`${t.expiresAt} > ${t.startedAt}`),
  index("impersonation_session_active")
    .on(t.realActorId, t.expiresAt)
    .where(sql`${t.endedAt} IS NULL`),
]);

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

/**
 * Append-only. TRIGGER REQUIRED: BEFORE UPDATE and BEFORE DELETE must raise.
 * The column set exists to answer investigation questions without joining against
 * mutable state that may since have changed.
 */
export const auditEvent = adminCore.table("audit_event", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  eventUuid: uuid("event_uuid").notNull().defaultRandom().unique(),
  chainId: text("chain_id").notNull().default("default"),
  chainSeq: bigint("chain_seq", { mode: "bigint" }).notNull(),

  occurredAt: timestamp("occurred_at", { withTimezone: true }).notNull(),
  recordedAt: timestamp("recorded_at", { withTimezone: true }).notNull().defaultNow(),

  actorId: bigint("actor_id", { mode: "bigint" }).references(() => adminActor.id),
  actorKind: text("actor_kind").notNull().default("human"),
  // Null unless impersonating. Never overwrite actorId with the impersonated
  // identity: that is the most common way impersonation audit is destroyed.
  effectiveActorId: bigint("effective_actor_id", { mode: "bigint" }).references(
    () => adminActor.id,
  ),
  impersonationSessionId: bigint("impersonation_session_id", { mode: "bigint" }).references(
    () => impersonationSession.id,
  ),

  tenantId: text("tenant_id"),
  environment: text("environment").notNull(),
  sourceIp: inet("source_ip"),
  userAgent: text("user_agent"),

  action: text("action").notNull(), // admin_permission.key, never free text
  targetType: text("target_type").notNull(),
  targetId: text("target_id"),

  reason: text("reason"),
  ticketRef: text("ticket_ref"),
  approvalRequestId: bigint("approval_request_id", { mode: "bigint" }),

  requestId: text("request_id"),
  correlationId: text("correlation_id"),
  idempotencyKey: text("idempotency_key"),

  result: text("result").notNull(),
  errorCode: text("error_code"),

  beforeState: jsonb("before_state"),
  afterState: jsonb("after_state"),
  // Hashes of the payloads; rowHash covers the hashes rather than the payload
  // text, which is what makes later redaction possible without invalidating the
  // chain.
  beforeHash: text("before_hash"),
  afterHash: text("after_hash"),
  redactionPolicy: text("redaction_policy").notNull().default("none"),

  policyVersion: text("policy_version"),

  prevHash: text("prev_hash"),
  rowHash: text("row_hash").notNull(),
  hashAlgorithm: text("hash_algorithm").notNull().default("sha256"),
}, (t) => [
  check("audit_event_actor_kind", sql`${t.actorKind} IN ('human','service','agent','system')`),
  // 'denied' lets you detect probing; 'unknown' stops a provider timeout being
  // misreported as a failure when the provider may still have acted.
  check(
    "audit_event_result",
    sql`${t.result} IN ('succeeded','failed','denied','partial','unknown')`,
  ),
  check(
    "audit_event_effective_actor_requires_session",
    sql`${t.effectiveActorId} IS NULL OR ${t.impersonationSessionId} IS NOT NULL`,
  ),
  uniqueIndex("audit_event_chain").on(t.chainId, t.chainSeq),
  // The narrow, predictable query set. Every extra index taxes the hottest insert
  // path in the system, so add nothing speculative.
  index("audit_event_recent").on(sql`${t.occurredAt} DESC`, sql`${t.id} DESC`),
  index("audit_event_target").on(t.targetType, t.targetId, sql`${t.occurredAt} DESC`),
  index("audit_event_actor").on(t.actorId, sql`${t.occurredAt} DESC`),
  index("audit_event_tenant")
    .on(t.tenantId, sql`${t.occurredAt} DESC`)
    .where(sql`${t.tenantId} IS NOT NULL`),
  index("audit_event_action").on(t.action, sql`${t.occurredAt} DESC`),
  index("audit_event_correlation").on(t.correlationId).where(sql`${t.correlationId} IS NOT NULL`),
  index("audit_event_request").on(t.requestId).where(sql`${t.requestId} IS NOT NULL`),
  index("audit_event_idempotency")
    .on(t.idempotencyKey)
    .where(sql`${t.idempotencyKey} IS NOT NULL`),
]);

// Paginate by keyset on (occurredAt, id), not offset. Offset pagination on an
// append-heavy table shifts rows under the operator and degrades linearly.

// ---------------------------------------------------------------------------
// Approvals and separation of duties
// ---------------------------------------------------------------------------

export const approvalRequest = adminCore.table("approval_request", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  requestUuid: uuid("request_uuid").notNull().defaultRandom().unique(),
  commandKey: text("command_key").notNull().references(() => adminPermission.key),
  payload: jsonb("payload").notNull(),
  payloadHash: text("payload_hash").notNull(),
  targetType: text("target_type").notNull(),
  targetId: text("target_id"),
  tenantId: text("tenant_id"),
  requestedBy: bigint("requested_by", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id),
  requestedAt: timestamp("requested_at", { withTimezone: true }).notNull().defaultNow(),
  reason: text("reason").notNull(),
  requiredApprovals: smallint("required_approvals").notNull().default(1),
  state: text("state").notNull().default("pending"),
  expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  executedAt: timestamp("executed_at", { withTimezone: true }),
  executionResult: text("execution_result"),
}, (t) => [
  check("approval_request_min_approvals", sql`${t.requiredApprovals} >= 1`),
  check(
    "approval_request_state",
    sql`${t.state} IN ('pending','approved','rejected','expired','executed','cancelled')`,
  ),
  index("approval_request_queue")
    .on(t.state, t.expiresAt)
    .where(sql`${t.state} = 'pending'`),
]);

/**
 * Immutable once written. A reversal is a new request, never an edit.
 * TRIGGER REQUIRED: the separation-of-duties check. The requester must not be able
 * to approve their own request, and enforcing that only in application code means
 * the control is absent for anything writing this table directly.
 */
export const approvalDecision = adminCore.table("approval_decision", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  requestId: bigint("request_id", { mode: "bigint" })
    .notNull()
    .references(() => approvalRequest.id, { onDelete: "cascade" }),
  approverId: bigint("approver_id", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id),
  decision: text("decision").notNull(),
  reason: text("reason").notNull(),
  decidedAt: timestamp("decided_at", { withTimezone: true }).notNull().defaultNow(),
}, (t) => [
  check("approval_decision_kind", sql`${t.decision} IN ('approved','rejected')`),
  // One approver decides once. Without this a single approver satisfies a
  // two-approval requirement by voting twice.
  uniqueIndex("approval_decision_one_per_approver").on(t.requestId, t.approverId),
]);

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export const adminJob = adminCore.table("admin_job", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  jobUuid: uuid("job_uuid").notNull().defaultRandom().unique(),
  kind: text("kind").notNull(), // manifest capability.id where kind = job
  initiatedBy: bigint("initiated_by", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id),
  tenantId: text("tenant_id"),
  environment: text("environment").notNull(),
  inputSummary: jsonb("input_summary").notNull(),
  inputHash: text("input_hash").notNull(),
  idempotencyKey: text("idempotency_key"),
  correlationId: text("correlation_id"),
  state: text("state").notNull().default("queued"),
  totalCount: integer("total_count"),
  processedCount: integer("processed_count").notNull().default(0),
  failedCount: integer("failed_count").notNull().default(0),
  queuedAt: timestamp("queued_at", { withTimezone: true }).notNull().defaultNow(),
  startedAt: timestamp("started_at", { withTimezone: true }),
  finishedAt: timestamp("finished_at", { withTimezone: true }),
  resultArtifact: text("result_artifact"),
  cancelRequested: boolean("cancel_requested").notNull().default(false),
}, (t) => [
  check(
    "admin_job_state",
    sql`${t.state} IN ('queued','running','succeeded','partial','failed','cancelled','expired')`,
  ),
  check("admin_job_counts", sql`${t.processedCount} >= 0 AND ${t.failedCount} >= 0`),
  // Duplicate submission protection: a repeated key returns the existing job
  // rather than starting a second one.
  uniqueIndex("admin_job_idempotency")
    .on(t.kind, t.idempotencyKey)
    .where(sql`${t.idempotencyKey} IS NOT NULL`),
  index("admin_job_active")
    .on(t.state, sql`${t.queuedAt} DESC`)
    .where(sql`${t.state} IN ('queued','running')`),
]);

/**
 * Per-item failures. A job that reports only an aggregate count cannot tell the
 * operator which rows to retry.
 */
export const adminJobFailure = adminCore.table("admin_job_failure", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  jobId: bigint("job_id", { mode: "bigint" })
    .notNull()
    .references(() => adminJob.id, { onDelete: "cascade" }),
  targetType: text("target_type").notNull(),
  targetId: text("target_id").notNull(),
  errorCode: text("error_code").notNull(),
  errorDetail: text("error_detail"),
  failedAt: timestamp("failed_at", { withTimezone: true }).notNull().defaultNow(),
}, (t) => [index("admin_job_failure_job").on(t.jobId, t.failedAt)]);

// ---------------------------------------------------------------------------
// Saved views and configuration
// ---------------------------------------------------------------------------

/**
 * A shared saved view is a disclosure surface: it can carry one tenant's filter
 * values to another operator. Apply the same row policy when loading it.
 */
export const savedView = adminCore.table("saved_view", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  ownerId: bigint("owner_id", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id, { onDelete: "cascade" }),
  route: text("route").notNull(), // manifest screens[].route
  name: text("name").notNull(),
  filters: jsonb("filters").notNull().default(sql`'{}'::jsonb`),
  columns: jsonb("columns").notNull().default(sql`'[]'::jsonb`),
  sort: jsonb("sort").notNull().default(sql`'[]'::jsonb`),
  visibility: text("visibility").notNull().default("private"),
  tenantId: text("tenant_id"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
}, (t) => [
  check("saved_view_visibility", sql`${t.visibility} IN ('private','shared')`),
  uniqueIndex("saved_view_name_unique").on(t.ownerId, t.route, t.name),
]);

export const configSetting = adminCore.table("config_setting", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  key: text("key").notNull().unique(),
  value: jsonb("value").notNull(),
  valueType: text("value_type").notNull(),
  // Secret values are never displayed after write. Support rotation and
  // revocation instead.
  isSecret: boolean("is_secret").notNull().default(false),
  tenantId: text("tenant_id"),
  environment: text("environment").notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

export const featureFlag = adminCore.table("feature_flag", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  key: text("key").notNull().unique(),
  description: text("description").notNull().default(""),
  isEnabled: boolean("is_enabled").notNull().default(false),
  rollout: jsonb("rollout").notNull().default(sql`'{}'::jsonb`),
  environment: text("environment").notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

/**
 * Every configuration change is a privileged mutation and gets its own history row
 * in addition to its audit event.
 */
export const configChange = adminCore.table("config_change", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  targetTable: text("target_table").notNull(),
  targetKey: text("target_key").notNull(),
  beforeValue: jsonb("before_value"),
  afterValue: jsonb("after_value"),
  changedBy: bigint("changed_by", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id),
  reason: text("reason").notNull(),
  changedAt: timestamp("changed_at", { withTimezone: true }).notNull().defaultNow(),
  auditEventId: bigint("audit_event_id", { mode: "bigint" }).references(() => auditEvent.id),
}, (t) => [
  check("config_change_target", sql`${t.targetTable} IN ('config_setting','feature_flag')`),
  index("config_change_target_idx").on(t.targetTable, t.targetKey, sql`${t.changedAt} DESC`),
]);

// ---------------------------------------------------------------------------
// Exports and data-subject requests
// ---------------------------------------------------------------------------

export const exportRequest = adminCore.table("export_request", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  exportUuid: uuid("export_uuid").notNull().defaultRandom().unique(),
  resource: text("resource").notNull(),
  requestedBy: bigint("requested_by", { mode: "bigint" })
    .notNull()
    .references(() => adminActor.id),
  tenantId: text("tenant_id"),
  filters: jsonb("filters").notNull(),
  // Record which policy was applied, not merely that one was. An export is the
  // easiest way for field-level policy to be silently bypassed.
  rowPolicy: text("row_policy").notNull(),
  fieldPolicy: text("field_policy").notNull(),
  reason: text("reason").notNull(),
  state: text("state").notNull().default("queued"),
  rowCount: integer("row_count"),
  byteSize: bigint("byte_size", { mode: "bigint" }),
  artifactRef: text("artifact_ref"),
  requestedAt: timestamp("requested_at", { withTimezone: true }).notNull().defaultNow(),
  readyAt: timestamp("ready_at", { withTimezone: true }),
  expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  downloadCount: integer("download_count").notNull().default(0),
  lastDownloadedAt: timestamp("last_downloaded_at", { withTimezone: true }),
}, (t) => [
  check(
    "export_request_state",
    sql`${t.state} IN ('queued','running','ready','failed','expired','revoked')`,
  ),
  index("export_request_state_idx").on(t.state, t.expiresAt),
]);

/**
 * Erasure interacts with retention, legal hold and backups. legalHold blocks
 * fulfilment; it does not silently drop the request.
 */
export const dataSubjectRequest = adminCore.table("data_subject_request", {
  id: bigserial("id", { mode: "bigint" }).primaryKey(),
  requestUuid: uuid("request_uuid").notNull().defaultRandom().unique(),
  kind: text("kind").notNull(),
  subjectRef: text("subject_ref").notNull(),
  tenantId: text("tenant_id"),
  receivedAt: timestamp("received_at", { withTimezone: true }).notNull().defaultNow(),
  dueAt: timestamp("due_at", { withTimezone: true }).notNull(),
  state: text("state").notNull().default("open"),
  legalHold: boolean("legal_hold").notNull().default(false),
  assignedTo: bigint("assigned_to", { mode: "bigint" }).references(() => adminActor.id),
  resolution: text("resolution"),
  resolvedAt: timestamp("resolved_at", { withTimezone: true }),
}, (t) => [
  check(
    "data_subject_request_kind",
    sql`${t.kind} IN ('access','erasure','rectification','portability','restriction','objection')`,
  ),
  check(
    "data_subject_request_state",
    sql`${t.state} IN ('open','in-progress','fulfilled','refused','withdrawn')`,
  ),
  index("data_subject_request_queue").on(t.state, t.dueAt),
]);
