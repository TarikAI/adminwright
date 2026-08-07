"""Adminwright admin control-plane core schema — Django.

Faithful translation of postgres.sql. Read ./README.md before adapting.

WHAT DJANGO CANNOT ENFORCE IN MODELS — add these in a migration:

  1. Triggers. Append-only enforcement on AuditEvent and ApprovalDecision, and the
     separation-of-duties trigger on ApprovalDecision. Use migrations.RunSQL with
     the statements from postgres.sql. Model-level guards (save() overrides,
     signals) are bypassed by bulk operations, raw SQL, and other services.
  2. Nothing else. Django expresses CHECK constraints and partial unique indexes
     natively via Meta.constraints, and both are used below.

Suggested migration tail:

    operations = [
        migrations.RunSQL(
            sql=open("assets/admin-core-schema/triggers.sql").read(),
            reverse_sql="DROP TRIGGER IF EXISTS audit_event_no_update ON audit_event;",
        ),
    ]

App label assumed to be "admin_core". Adjust db_table names to project convention;
keep AdminPermission.key equal to the manifest capability id.
"""

import uuid

from django.db import models
from django.db.models import Q, F


# ---------------------------------------------------------------------------
# Actors, roles, permissions, grants
# ---------------------------------------------------------------------------


class AdminActor(models.Model):
    """The privileged identity.

    Drop this model and add is_privileged / auth_strength / mfa_enrolled to the
    project's user model when operators share the product's lifecycle and identity
    provider. Credentials are never stored here; auth_subject holds the IdP
    subject claim.
    """

    class Kind(models.TextChoices):
        HUMAN = "human"
        SERVICE = "service"
        AGENT = "agent"

    class AuthStrength(models.TextChoices):
        PASSWORD = "password"
        MFA = "mfa"
        PHISHING_RESISTANT = "phishing-resistant"
        SSO = "sso"

    actor_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    app_user_id = models.BigIntegerField(null=True, blank=True)
    auth_subject = models.TextField(unique=True)
    display_name = models.TextField()
    email = models.EmailField(null=True, blank=True)
    # Automation and autonomous agents are first-class subjects. They never
    # inherit a human's authority.
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.HUMAN)
    auth_strength = models.CharField(
        max_length=32, choices=AuthStrength.choices, default=AuthStrength.PASSWORD
    )
    mfa_enrolled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "admin_actor"


class AdminRole(models.Model):
    key = models.TextField(unique=True)  # matches manifest roles[].id
    name = models.TextField()
    description = models.TextField(default="", blank=True)
    requires_mfa = models.BooleanField(default=True)  # manifest roles[].mfaRequired
    max_scope = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    permissions = models.ManyToManyField(
        "AdminPermission", through="AdminRolePermission", related_name="roles"
    )

    class Meta:
        db_table = "admin_role"


class AdminPermission(models.Model):
    """The atom of authorization.

    Key it <resource>.<action> so it equals the manifest capability id exactly;
    that equality makes capability -> permission -> audit_event.action traceable.
    Obligations live here rather than at the call site so a new call site cannot
    silently skip them.
    """

    class Risk(models.TextChoices):
        LOW = "low"
        MODERATE = "moderate"
        HIGH = "high"
        CRITICAL = "critical"

    key = models.TextField(unique=True)  # manifest capability.id
    resource = models.TextField()  # manifest entity id
    action = models.TextField()
    risk = models.CharField(max_length=16, choices=Risk.choices, default=Risk.LOW)
    requires_reason = models.BooleanField(default=False)
    requires_step_up = models.BooleanField(default=False)
    requires_approval = models.BooleanField(default=False)
    description = models.TextField(default="", blank=True)

    class Meta:
        db_table = "admin_permission"


class AdminRolePermission(models.Model):
    role = models.ForeignKey(AdminRole, on_delete=models.CASCADE)
    permission = models.ForeignKey(AdminPermission, on_delete=models.CASCADE)

    class Meta:
        db_table = "admin_role_permission"
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"], name="admin_role_permission_pk"
            )
        ]


class AdminActorRole(models.Model):
    """A grant, not a join row.

    Carries where it applies, when it expires, who granted it and why, and how it
    was revoked. Revocation is a state change, never a delete.
    """

    actor = models.ForeignKey(AdminActor, on_delete=models.PROTECT, related_name="grants_held")
    role = models.ForeignKey(AdminRole, on_delete=models.PROTECT, related_name="grants")
    scope = models.JSONField(default=dict)
    scope_tenant_id = models.TextField(null=True, blank=True)  # denormalized hot path
    starts_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = permanent
    granted_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="grants_issued"
    )
    granted_reason = models.TextField()
    ticket_ref = models.TextField(null=True, blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="grants_revoked",
    )
    revoke_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "admin_actor_role"
        constraints = [
            # No self-granting. Privilege escalation by an operator on their own
            # account is the failure this prevents.
            models.CheckConstraint(
                check=~Q(granted_by=F("actor")), name="admin_actor_role_no_self_grant"
            ),
            models.CheckConstraint(
                check=Q(revoked_at__isnull=True, revoked_by__isnull=True)
                | Q(revoked_at__isnull=False, revoked_by__isnull=False),
                name="admin_actor_role_revocation_complete",
            ),
            models.CheckConstraint(
                check=Q(expires_at__isnull=True) | Q(expires_at__gt=F("starts_at")),
                name="admin_actor_role_window",
            ),
            # One live grant of a role per actor per tenant scope. Duplicate live
            # grants make revocation unreliable: revoking one leaves the other in
            # force.
            models.UniqueConstraint(
                fields=["actor", "role", "scope_tenant_id"],
                condition=Q(revoked_at__isnull=True),
                name="admin_actor_role_live_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["actor"],
                condition=Q(revoked_at__isnull=True),
                name="admin_actor_role_live_lookup",
            )
        ]


class AdminPolicyVersion(models.Model):
    """Which rules were in force when a decision was made.

    AuditEvent.policy_version points here so a past decision stays explainable
    after the policy changes.
    """

    version = models.TextField(unique=True)
    source_ref = models.TextField()  # manifest capability.authorizationPolicies[]
    checksum = models.TextField()
    activated_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "admin_policy_version"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEvent(models.Model):
    """Append-only. Enforce with triggers, not with save() overrides."""

    class ActorKind(models.TextChoices):
        HUMAN = "human"
        SERVICE = "service"
        AGENT = "agent"
        SYSTEM = "system"

    class Result(models.TextChoices):
        # 'denied' lets you detect probing; 'unknown' stops a provider timeout
        # being misreported as a failure when the provider may still have acted.
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        DENIED = "denied"
        PARTIAL = "partial"
        UNKNOWN = "unknown"

    event_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    chain_id = models.TextField(default="default")
    chain_seq = models.BigIntegerField()

    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    actor = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_acted"
    )
    actor_kind = models.CharField(
        max_length=16, choices=ActorKind.choices, default=ActorKind.HUMAN
    )
    # Null unless impersonating. Never overwrite actor with the impersonated
    # identity: that is the most common way impersonation audit is destroyed.
    effective_actor = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="audit_effective",
    )
    impersonation_session = models.ForeignKey(
        "ImpersonationSession", on_delete=models.PROTECT, null=True, blank=True
    )

    tenant_id = models.TextField(null=True, blank=True)
    environment = models.TextField()
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    action = models.TextField()  # admin_permission.key, never free text
    target_type = models.TextField()
    target_id = models.TextField(null=True, blank=True)

    reason = models.TextField(null=True, blank=True)
    ticket_ref = models.TextField(null=True, blank=True)
    approval_request = models.ForeignKey(
        "ApprovalRequest", on_delete=models.PROTECT, null=True, blank=True
    )

    request_id = models.TextField(null=True, blank=True)
    correlation_id = models.TextField(null=True, blank=True)
    idempotency_key = models.TextField(null=True, blank=True)

    result = models.CharField(max_length=16, choices=Result.choices)
    error_code = models.TextField(null=True, blank=True)

    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)
    # Hashes of the payloads; row_hash covers the hashes rather than the payload
    # text, which is what makes later redaction possible without invalidating the
    # chain.
    before_hash = models.TextField(null=True, blank=True)
    after_hash = models.TextField(null=True, blank=True)
    redaction_policy = models.TextField(default="none")

    policy_version = models.TextField(null=True, blank=True)

    prev_hash = models.TextField(null=True, blank=True)
    row_hash = models.TextField()
    hash_algorithm = models.TextField(default="sha256")

    class Meta:
        db_table = "audit_event"
        constraints = [
            models.UniqueConstraint(
                fields=["chain_id", "chain_seq"], name="audit_event_chain"
            ),
            models.CheckConstraint(
                check=Q(effective_actor__isnull=True)
                | Q(impersonation_session__isnull=False),
                name="audit_event_effective_actor_requires_session",
            ),
        ]
        # The narrow, predictable query set. Every extra index taxes the hottest
        # insert path in the system, so add nothing speculative.
        indexes = [
            models.Index(fields=["-occurred_at", "-id"], name="audit_event_recent"),
            models.Index(
                fields=["target_type", "target_id", "-occurred_at"], name="audit_event_target"
            ),
            models.Index(fields=["actor", "-occurred_at"], name="audit_event_actor"),
            models.Index(
                fields=["tenant_id", "-occurred_at"],
                condition=Q(tenant_id__isnull=False),
                name="audit_event_tenant",
            ),
            models.Index(fields=["action", "-occurred_at"], name="audit_event_action"),
            models.Index(
                fields=["correlation_id"],
                condition=Q(correlation_id__isnull=False),
                name="audit_event_correlation",
            ),
            models.Index(
                fields=["request_id"],
                condition=Q(request_id__isnull=False),
                name="audit_event_request",
            ),
            models.Index(
                fields=["idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="audit_event_idempotency",
            ),
        ]

    # Paginate by keyset on (occurred_at, id), not offset. Offset pagination on an
    # append-heavy table shifts rows under the operator and degrades linearly.


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


class ImpersonationSession(models.Model):
    session_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    real_actor = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="impersonations"
    )
    target_subject_id = models.TextField()
    target_tenant_id = models.TextField(null=True, blank=True)
    reason = models.TextField()
    ticket_ref = models.TextField(null=True, blank=True)
    scope_restrictions = models.JSONField(default=dict)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.TextField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="impersonation_revocations",
    )

    class Meta:
        db_table = "impersonation_session"
        constraints = [
            models.CheckConstraint(
                check=Q(expires_at__gt=F("started_at")), name="impersonation_bounded"
            )
        ]
        indexes = [
            models.Index(
                fields=["real_actor", "expires_at"],
                condition=Q(ended_at__isnull=True),
                name="impersonation_session_active",
            )
        ]


# ---------------------------------------------------------------------------
# Approvals and separation of duties
# ---------------------------------------------------------------------------


class ApprovalRequest(models.Model):
    class State(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        EXPIRED = "expired"
        EXECUTED = "executed"
        CANCELLED = "cancelled"

    request_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    command_key = models.ForeignKey(
        AdminPermission, to_field="key", db_column="command_key", on_delete=models.PROTECT
    )
    payload = models.JSONField()
    payload_hash = models.TextField()
    target_type = models.TextField()
    target_id = models.TextField(null=True, blank=True)
    tenant_id = models.TextField(null=True, blank=True)
    requested_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="approvals_requested"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    required_approvals = models.SmallIntegerField(default=1)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    expires_at = models.DateTimeField()
    executed_at = models.DateTimeField(null=True, blank=True)
    execution_result = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "approval_request"
        constraints = [
            models.CheckConstraint(
                check=Q(required_approvals__gte=1), name="approval_request_min_approvals"
            )
        ]
        indexes = [
            models.Index(
                fields=["state", "expires_at"],
                condition=Q(state="pending"),
                name="approval_request_queue",
            )
        ]


class ApprovalDecision(models.Model):
    """Immutable once written. A reversal is a new request, never an edit.

    The separation-of-duties rule (requester may not approve their own request)
    lives in a database trigger, not here. Enforcing it only in application code
    means the control is absent for anything writing this table directly.
    """

    class Decision(models.TextChoices):
        APPROVED = "approved"
        REJECTED = "rejected"

    request = models.ForeignKey(
        ApprovalRequest, on_delete=models.CASCADE, related_name="decisions"
    )
    approver = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="approval_decisions"
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reason = models.TextField()
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approval_decision"
        constraints = [
            # One approver decides once. Without this a single approver satisfies
            # a two-approval requirement by voting twice.
            models.UniqueConstraint(
                fields=["request", "approver"], name="approval_decision_one_per_approver"
            )
        ]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class AdminJob(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        PARTIAL = "partial"
        FAILED = "failed"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    job_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.TextField()  # manifest capability.id where kind = job
    initiated_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="jobs"
    )
    tenant_id = models.TextField(null=True, blank=True)
    environment = models.TextField()
    input_summary = models.JSONField()
    input_hash = models.TextField()
    idempotency_key = models.TextField(null=True, blank=True)
    correlation_id = models.TextField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    total_count = models.IntegerField(null=True, blank=True)
    processed_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result_artifact = models.TextField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)

    class Meta:
        db_table = "admin_job"
        constraints = [
            models.CheckConstraint(
                check=Q(processed_count__gte=0) & Q(failed_count__gte=0),
                name="admin_job_counts",
            ),
            # Duplicate submission protection: a repeated key returns the existing
            # job rather than starting a second one.
            models.UniqueConstraint(
                fields=["kind", "idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="admin_job_idempotency",
            ),
        ]
        indexes = [
            models.Index(
                fields=["state", "-queued_at"],
                condition=Q(state__in=["queued", "running"]),
                name="admin_job_active",
            )
        ]


class AdminJobFailure(models.Model):
    """Per-item failures.

    A job that reports only an aggregate count cannot tell the operator which rows
    to retry.
    """

    job = models.ForeignKey(AdminJob, on_delete=models.CASCADE, related_name="failures")
    target_type = models.TextField()
    target_id = models.TextField()
    error_code = models.TextField()
    error_detail = models.TextField(null=True, blank=True)
    failed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "admin_job_failure"
        indexes = [models.Index(fields=["job", "failed_at"], name="admin_job_failure_job")]


# ---------------------------------------------------------------------------
# Saved views and configuration
# ---------------------------------------------------------------------------


class SavedView(models.Model):
    """A shared saved view is a disclosure surface.

    It can carry one tenant's filter values to another operator. Apply the same row
    policy when loading it.
    """

    class Visibility(models.TextChoices):
        PRIVATE = "private"
        SHARED = "shared"

    owner = models.ForeignKey(AdminActor, on_delete=models.CASCADE, related_name="saved_views")
    route = models.TextField()  # manifest screens[].route
    name = models.TextField()
    filters = models.JSONField(default=dict)
    columns = models.JSONField(default=list)
    sort = models.JSONField(default=list)
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE
    )
    tenant_id = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saved_view"
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "route", "name"], name="saved_view_name_unique"
            )
        ]


class ConfigSetting(models.Model):
    key = models.TextField(unique=True)
    value = models.JSONField()
    value_type = models.TextField()
    # Secret values are never displayed after write. Support rotation and
    # revocation instead.
    is_secret = models.BooleanField(default=False)
    tenant_id = models.TextField(null=True, blank=True)
    environment = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "config_setting"


class FeatureFlag(models.Model):
    key = models.TextField(unique=True)
    description = models.TextField(default="", blank=True)
    is_enabled = models.BooleanField(default=False)
    rollout = models.JSONField(default=dict)
    environment = models.TextField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "feature_flag"


class ConfigChange(models.Model):
    """Every configuration change is a privileged mutation.

    It gets its own history row in addition to its audit event.
    """

    class Target(models.TextChoices):
        CONFIG_SETTING = "config_setting"
        FEATURE_FLAG = "feature_flag"

    target_table = models.CharField(max_length=32, choices=Target.choices)
    target_key = models.TextField()
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    changed_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="config_changes"
    )
    reason = models.TextField()
    changed_at = models.DateTimeField(auto_now_add=True)
    audit_event = models.ForeignKey(
        AuditEvent, on_delete=models.PROTECT, null=True, blank=True
    )

    class Meta:
        db_table = "config_change"
        indexes = [
            models.Index(
                fields=["target_table", "target_key", "-changed_at"], name="config_change_target"
            )
        ]


# ---------------------------------------------------------------------------
# Exports and data-subject requests
# ---------------------------------------------------------------------------


class ExportRequest(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        READY = "ready"
        FAILED = "failed"
        EXPIRED = "expired"
        REVOKED = "revoked"

    export_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    resource = models.TextField()
    requested_by = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="exports"
    )
    tenant_id = models.TextField(null=True, blank=True)
    filters = models.JSONField()
    # Record which policy was applied, not merely that one was. An export is the
    # easiest way for field-level policy to be silently bypassed.
    row_policy = models.TextField()
    field_policy = models.TextField()
    reason = models.TextField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    row_count = models.IntegerField(null=True, blank=True)
    byte_size = models.BigIntegerField(null=True, blank=True)
    artifact_ref = models.TextField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    download_count = models.IntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "export_request"
        indexes = [models.Index(fields=["state", "expires_at"], name="export_request_state")]


class DataSubjectRequest(models.Model):
    """Erasure interacts with retention, legal hold and backups.

    legal_hold blocks fulfilment; it does not silently drop the request.
    """

    class Kind(models.TextChoices):
        ACCESS = "access"
        ERASURE = "erasure"
        RECTIFICATION = "rectification"
        PORTABILITY = "portability"
        RESTRICTION = "restriction"
        OBJECTION = "objection"

    class State(models.TextChoices):
        OPEN = "open"
        IN_PROGRESS = "in-progress"
        FULFILLED = "fulfilled"
        REFUSED = "refused"
        WITHDRAWN = "withdrawn"

    request_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    subject_ref = models.TextField()
    tenant_id = models.TextField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.OPEN)
    legal_hold = models.BooleanField(default=False)
    assigned_to = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="dsr_assignments",
    )
    resolution = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "data_subject_request"
        indexes = [
            models.Index(fields=["state", "due_at"], name="data_subject_request_queue")
        ]
