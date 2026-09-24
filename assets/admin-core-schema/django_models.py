"""Adminwright admin control-plane core schema — Django.

Translation of postgres.sql. Read ./README.md before adapting.

VERSIONS. Needs Django 5.1+; checked on Django 6.1 with PostgreSQL 16 and on Django 5.2
LTS with PostgreSQL 14. Django 6.1 requires PostgreSQL 15+ (it refuses to connect to 14),
so on PostgreSQL 14 stay on Django 5.2 LTS or 6.0. CheckConstraint(condition=...) is the
5.1+ keyword: Django 5.0 and older spell it check=..., and 6.0 removed check= entirely
(a TypeError at import). db_default (used for every column that has a database default
in postgres.sql) needs Django 5.0+.

WHAT DJANGO CANNOT ENFORCE IN MODELS — add these in a migration:

  1. Triggers and functions. Append-only enforcement on AuditEvent and
     ApprovalDecision, the audit hash chain (chain_seq / prev_hash / row_hash are
     assigned by a BEFORE INSERT trigger), verify_audit_chain(), the payload
     scrub function, the separation-of-duties trigger, and the trigger that keeps
     an ApprovalRequest as written (only state, executed_at and execution_result
     change; prefer save(update_fields=[...]) for those, since a full save()
     rewrites every column and is refused if a value does not round-trip exactly).
     They live in ./django_triggers.sql (unqualified names, canonical column
     names). Model-level guards (save() overrides, signals) are bypassed by bulk
     operations, raw SQL and other services.
  2. Nothing else. Every CHECK in postgres.sql (including the enum-like ones, which
     are CheckConstraints below and not just choices=) and every partial /
     expression unique index is expressed natively via Meta.constraints.

Suggested migration: a second one, after the migration makemigrations generates.
COPY django_triggers.sql INTO THE APP'S migrations/ DIRECTORY, next to this file:
the migration reads it from there when Django loads migrations, so without the
copy every manage.py command that loads them stops with FileNotFoundError. (Or
paste the file's contents into the migration as the sql= string instead.)

    # admin_core/migrations/0002_triggers.py, with django_triggers.sql beside it
    from pathlib import Path
    from django.db import migrations

    class Migration(migrations.Migration):
        dependencies = [("admin_core", "0001_initial")]
        operations = [
            migrations.RunSQL(
                sql=Path(__file__).with_name("django_triggers.sql").read_text(encoding="utf-8"),
                reverse_sql=migrations.RunSQL.noop,
            ),
        ]

Then run grants.sql (psql, see its header) for the regulated profile.

DIFFERENCES FROM postgres.sql that Django's ORM imposes (all deliberate):
  * FK columns are named <field>_id by default. Where the canonical column has no
    _id suffix (requested_by, granted_by, revoked_by, initiated_by, changed_by,
    assigned_to) the field carries db_column= so raw SQL, the triggers and every
    other variant see the same name.
  * Django creates an index per ForeignKey and a *_like index per unique text
    column. Both are switched off (db_index=False, UniqueConstraint instead of
    unique=True) so the indexes match postgres.sql exactly; audit_event is the hot
    insert path. Add FK indexes on read-heavy tables if your queries need them.
  * admin_role_permission has a surrogate id (Django's default primary key);
    postgres.sql uses the composite (role_id, permission_id) primary key. The
    unique constraint below keeps the pair unique either way.
  * admin_actor.email is an EmailField (varchar) rather than citext, and Django's
    Now() is STATEMENT_TIMESTAMP() where postgres.sql uses now() (transaction start).
  * Record-keeping timestamps (created_at, recorded_at, decided_at, changed_at) use
    auto_now_add: the ORM stamps the current time whatever a caller passes.
    Business timestamps (starts_at, granted_at, activated_at, started_at,
    requested_at, queued_at, failed_at, received_at) use default=timezone.now, so a
    caller can schedule a grant to start later or record a request received by post
    on the day it arrived, as postgres.sql's plain defaults allow.
  * The id columns assume DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
    (startproject's default), matching postgres.sql's bigserial keys.
  * Django FKs are DEFERRABLE INITIALLY DEFERRED, and on_delete is enforced by the
    ORM: PROTECT is NO ACTION in the database (which also blocks the delete, at
    commit rather than at the statement) and CASCADE is emulated by the ORM, so a
    raw DELETE of a parent row does not cascade the way postgres.sql does. Django 6.1's
    database-level DB_CASCADE is not used: it cannot be mixed with the PROTECT
    references elsewhere in this schema (system check fields.E323).
  * AuditEvent.chain_seq and row_hash carry placeholder defaults (0 and "") because
    the ORM always sends a value; the trigger overwrites them.

App label assumed to be "admin_core". Adjust db_table names to project convention;
keep AdminPermission.key equal to the manifest capability id.
"""

import uuid
from datetime import timedelta

from django.db import models
from django.db.models import F, Func, Q, Value
from django.db.models.functions import Coalesce, Now
from django.utils import timezone


class GenRandomUUID(Func):
    """gen_random_uuid(): the database default for the *_uuid columns."""

    function = "gen_random_uuid"
    output_field = models.UUIDField()


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

    actor_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    app_user_id = models.BigIntegerField(null=True, blank=True)
    auth_subject = models.TextField()  # unique: see Meta.constraints
    display_name = models.TextField()
    email = models.EmailField(null=True, blank=True)
    # Automation and autonomous agents are first-class subjects. They never
    # inherit a human's authority.
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.HUMAN, db_default="human"
    )
    auth_strength = models.CharField(
        max_length=32,
        choices=AuthStrength.choices,
        default=AuthStrength.PASSWORD,
        db_default="password",
    )
    mfa_enrolled = models.BooleanField(default=False, db_default=False)
    is_active = models.BooleanField(default=True, db_default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_default=Now())

    class Meta:
        db_table = "admin_actor"
        constraints = [
            models.UniqueConstraint(fields=["auth_subject"], name="admin_actor_auth_subject_key"),
            models.CheckConstraint(
                condition=Q(kind__in=["human", "service", "agent"]),
                name="admin_actor_kind_check",
            ),
            models.CheckConstraint(
                condition=Q(auth_strength__in=["password", "mfa", "phishing-resistant", "sso"]),
                name="admin_actor_auth_strength_check",
            ),
        ]


class AdminRole(models.Model):
    key = models.TextField()  # matches manifest roles[].id; unique: see Meta.constraints
    name = models.TextField()
    description = models.TextField(default="", db_default="", blank=True)
    requires_mfa = models.BooleanField(default=True, db_default=True)  # manifest roles[].mfaRequired
    max_scope = models.JSONField(default=dict, db_default={})
    is_active = models.BooleanField(default=True, db_default=True)

    permissions = models.ManyToManyField(
        "AdminPermission", through="AdminRolePermission", related_name="roles"
    )

    class Meta:
        db_table = "admin_role"
        constraints = [
            models.UniqueConstraint(fields=["key"], name="admin_role_key_key"),
        ]


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

    key = models.TextField()  # manifest capability.id; unique: see Meta.constraints
    resource = models.TextField()  # manifest entity id
    action = models.TextField()
    risk = models.CharField(
        max_length=16, choices=Risk.choices, default=Risk.LOW, db_default="low"
    )
    requires_reason = models.BooleanField(default=False, db_default=False)
    requires_step_up = models.BooleanField(default=False, db_default=False)
    requires_approval = models.BooleanField(default=False, db_default=False)
    description = models.TextField(default="", db_default="", blank=True)

    class Meta:
        db_table = "admin_permission"
        constraints = [
            models.UniqueConstraint(fields=["key"], name="admin_permission_key_key"),
            models.CheckConstraint(
                condition=Q(risk__in=["low", "moderate", "high", "critical"]),
                name="admin_permission_risk_check",
            ),
        ]


class AdminRolePermission(models.Model):
    role = models.ForeignKey(AdminRole, on_delete=models.CASCADE, db_index=False)
    permission = models.ForeignKey(AdminPermission, on_delete=models.CASCADE, db_index=False)

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

    actor = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="grants_held", db_index=False
    )
    role = models.ForeignKey(
        AdminRole, on_delete=models.PROTECT, related_name="grants", db_index=False
    )
    scope = models.JSONField(default=dict, db_default={})
    scope_tenant_id = models.TextField(null=True, blank=True)  # denormalized hot path
    starts_at = models.DateTimeField(default=timezone.now, db_default=Now())
    expires_at = models.DateTimeField(null=True, blank=True)  # null = permanent
    granted_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        related_name="grants_issued",
        db_column="granted_by",
        db_index=False,
    )
    granted_reason = models.TextField()
    ticket_ref = models.TextField(null=True, blank=True)
    granted_at = models.DateTimeField(default=timezone.now, db_default=Now())
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="grants_revoked",
        db_column="revoked_by",
        db_index=False,
    )
    revoke_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "admin_actor_role"
        constraints = [
            # No self-granting. Privilege escalation by an operator on their own
            # account is the failure this prevents.
            models.CheckConstraint(
                condition=~Q(granted_by=F("actor")), name="admin_actor_role_no_self_grant"
            ),
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True, revoked_by__isnull=True)
                | Q(revoked_at__isnull=False, revoked_by__isnull=False),
                name="admin_actor_role_revocation_complete",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__isnull=True) | Q(expires_at__gt=F("starts_at")),
                name="admin_actor_role_window",
            ),
            # One live grant of a role per actor per tenant scope. Duplicate live
            # grants make revocation unreliable: revoking one leaves the other in
            # force. Coalesce makes a global grant (NULL tenant) unique too; a plain
            # unique index treats NULLs as distinct. (On PostgreSQL 15+,
            # nulls_distinct=False on the fields form is an equivalent spelling.)
            models.UniqueConstraint(
                F("actor"),
                F("role"),
                Coalesce("scope_tenant_id", Value("*")),
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

    version = models.TextField()  # unique: see Meta.constraints
    source_ref = models.TextField()  # manifest capability.authorizationPolicies[]
    checksum = models.TextField()
    activated_at = models.DateTimeField(default=timezone.now, db_default=Now())
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "admin_policy_version"
        constraints = [
            models.UniqueConstraint(fields=["version"], name="admin_policy_version_version_key"),
        ]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEvent(models.Model):
    """Append-only. Enforce with triggers (django_triggers.sql), not save() overrides.

    The database owns chain_seq, prev_hash, row_hash and, when a payload is given,
    before_hash / after_hash. Writers leave them out; the ORM's placeholder values
    for chain_seq and row_hash are overwritten by the BEFORE INSERT trigger.
    """

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

    event_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    chain_id = models.TextField(default="default", db_default="default")
    chain_seq = models.BigIntegerField(default=0)  # assigned by trigger

    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True, db_default=Now())

    actor = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_acted",
        db_index=False,
    )
    actor_kind = models.CharField(
        max_length=16, choices=ActorKind.choices, default=ActorKind.HUMAN, db_default="human"
    )
    # Null unless impersonating. Never overwrite actor with the impersonated
    # identity: that is the most common way impersonation audit is destroyed. The
    # acted-as subject is usually a customer, not an AdminActor, so this is text in
    # the same identity space as ImpersonationSession.target_subject_id — deliberately
    # not a foreign key.
    effective_actor_id = models.TextField(null=True, blank=True)
    impersonation_session = models.ForeignKey(
        "ImpersonationSession",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_index=False,
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
    # Plain column, as in postgres.sql: audit rows carry no foreign key to the
    # approval that authorised them.
    approval_request_id = models.BigIntegerField(null=True, blank=True)

    request_id = models.TextField(null=True, blank=True)
    correlation_id = models.TextField(null=True, blank=True)
    idempotency_key = models.TextField(null=True, blank=True)

    result = models.CharField(max_length=16, choices=Result.choices)
    error_code = models.TextField(null=True, blank=True)

    before_state = models.JSONField(null=True, blank=True)
    after_state = models.JSONField(null=True, blank=True)
    # Hashes of the payloads (set by the trigger); row_hash covers the hashes rather
    # than the payload text, which is what makes a later scrub possible without
    # invalidating the chain.
    before_hash = models.TextField(null=True, blank=True)
    after_hash = models.TextField(null=True, blank=True)
    redaction_policy = models.TextField(default="none", db_default="none")

    policy_version = models.TextField(null=True, blank=True)

    prev_hash = models.TextField(null=True, blank=True)  # assigned by trigger
    row_hash = models.TextField(default="")  # assigned by trigger
    hash_algorithm = models.TextField(default="sha256", db_default="sha256")

    class Meta:
        db_table = "audit_event"
        constraints = [
            models.UniqueConstraint(
                fields=["chain_id", "chain_seq"], name="audit_event_chain"
            ),
            models.CheckConstraint(
                condition=Q(actor_kind__in=["human", "service", "agent", "system"]),
                name="audit_event_actor_kind_check",
            ),
            models.CheckConstraint(
                condition=Q(result__in=["succeeded", "failed", "denied", "partial", "unknown"]),
                name="audit_event_result_check",
            ),
            models.CheckConstraint(
                condition=Q(effective_actor_id__isnull=True)
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
    class EndedReason(models.TextChoices):
        OPERATOR_EXIT = "operator_exit"
        EXPIRED = "expired"
        REVOKED = "revoked"
        SESSION_LOST = "session_lost"

    session_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    real_actor = models.ForeignKey(
        AdminActor, on_delete=models.PROTECT, related_name="impersonations", db_index=False
    )
    target_subject_id = models.TextField()
    target_tenant_id = models.TextField(null=True, blank=True)
    reason = models.TextField()
    ticket_ref = models.TextField(null=True, blank=True)
    # Opting into write impersonation is a deliberate, separately permissioned act.
    read_only = models.BooleanField(default=True, db_default=True)
    scope_restrictions = models.JSONField(default=dict, db_default={})
    started_at = models.DateTimeField(default=timezone.now, db_default=Now())
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.TextField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="impersonation_revocations",
        db_column="revoked_by",
        db_index=False,
    )

    class Meta:
        db_table = "impersonation_session"
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("started_at")), name="impersonation_bounded"
            ),
            # Bounded maximum duration; 8 hours here, tighten to the project's policy.
            models.CheckConstraint(
                condition=Q(expires_at__lte=F("started_at") + timedelta(hours=8)),
                name="impersonation_max_duration",
            ),
            # Ending a session requires one of the four reasons; a session that
            # simply stops is indistinguishable from one never closed.
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True, ended_reason__isnull=True)
                | Q(
                    ended_at__isnull=False,
                    ended_reason__isnull=False,
                    ended_reason__in=["operator_exit", "expired", "revoked", "session_lost"],
                ),
                name="impersonation_end_state",
            ),
            # At most one live session per real actor. A sweeper must end expired
            # sessions (ended_reason = 'expired'), or the actor cannot start another.
            models.UniqueConstraint(
                fields=["real_actor"],
                condition=Q(ended_at__isnull=True),
                name="impersonation_session_one_live",
            ),
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
        EXECUTION_FAILED = "execution_failed"
        CANCELLED = "cancelled"

    request_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    command_key = models.ForeignKey(
        AdminPermission,
        to_field="key",
        db_column="command_key",
        on_delete=models.PROTECT,
        db_index=False,
    )
    payload = models.JSONField()
    payload_hash = models.TextField()
    target_type = models.TextField()
    target_id = models.TextField(null=True, blank=True)
    tenant_id = models.TextField(null=True, blank=True)
    requested_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        related_name="approvals_requested",
        db_column="requested_by",
        db_index=False,
    )
    requested_at = models.DateTimeField(default=timezone.now, db_default=Now())
    reason = models.TextField()
    required_approvals = models.SmallIntegerField(default=1, db_default=1)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING, db_default="pending"
    )
    expires_at = models.DateTimeField()
    executed_at = models.DateTimeField(null=True, blank=True)
    execution_result = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "approval_request"
        constraints = [
            models.CheckConstraint(
                condition=Q(required_approvals__gte=1), name="approval_request_min_approvals"
            ),
            models.CheckConstraint(
                condition=Q(
                    state__in=[
                        "pending",
                        "approved",
                        "rejected",
                        "expired",
                        "executed",
                        "execution_failed",
                        "cancelled",
                    ]
                ),
                name="approval_request_state_check",
            ),
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

    Append-only like AuditEvent: PROTECT (never CASCADE) on the request, plus the
    UPDATE / DELETE / TRUNCATE deny triggers in django_triggers.sql. Cascading would
    let a rejection be deleted with its request and re-entered as an approval.
    The separation-of-duties rule (requester may not approve their own request)
    lives in a database trigger, not here. Enforcing it only in application code
    means the control is absent for anything writing this table directly.
    """

    class Decision(models.TextChoices):
        APPROVED = "approved"
        REJECTED = "rejected"

    request = models.ForeignKey(
        ApprovalRequest, on_delete=models.PROTECT, related_name="decisions", db_index=False
    )
    approver = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        related_name="approval_decisions",
        db_index=False,
    )
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reason = models.TextField()
    decided_at = models.DateTimeField(auto_now_add=True, db_default=Now())

    class Meta:
        db_table = "approval_decision"
        constraints = [
            # One approver decides once. Without this a single approver satisfies
            # a two-approval requirement by voting twice.
            models.UniqueConstraint(
                fields=["request", "approver"], name="approval_decision_one_per_approver"
            ),
            models.CheckConstraint(
                condition=Q(decision__in=["approved", "rejected"]),
                name="approval_decision_kind_check",
            ),
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

    job_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    kind = models.TextField()  # manifest capability.id where kind = job
    initiated_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        related_name="jobs",
        db_column="initiated_by",
        db_index=False,
    )
    tenant_id = models.TextField(null=True, blank=True)
    environment = models.TextField()
    input_summary = models.JSONField()
    input_hash = models.TextField()
    idempotency_key = models.TextField(null=True, blank=True)
    correlation_id = models.TextField(null=True, blank=True)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.QUEUED, db_default="queued"
    )
    total_count = models.IntegerField(null=True, blank=True)
    processed_count = models.IntegerField(default=0, db_default=0)
    failed_count = models.IntegerField(default=0, db_default=0)
    queued_at = models.DateTimeField(default=timezone.now, db_default=Now())
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result_artifact = models.TextField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False, db_default=False)

    class Meta:
        db_table = "admin_job"
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    state__in=[
                        "queued",
                        "running",
                        "succeeded",
                        "partial",
                        "failed",
                        "cancelled",
                        "expired",
                    ]
                ),
                name="admin_job_state_check",
            ),
            models.CheckConstraint(
                condition=Q(processed_count__gte=0) & Q(failed_count__gte=0),
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
    to retry. PROTECT, not CASCADE: deleting the job must not erase its failure
    history.
    """

    job = models.ForeignKey(
        AdminJob, on_delete=models.PROTECT, related_name="failures", db_index=False
    )
    target_type = models.TextField()
    target_id = models.TextField()
    error_code = models.TextField()
    error_detail = models.TextField(null=True, blank=True)
    failed_at = models.DateTimeField(default=timezone.now, db_default=Now())

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

    owner = models.ForeignKey(
        AdminActor, on_delete=models.CASCADE, related_name="saved_views", db_index=False
    )
    route = models.TextField()  # manifest screens[].route
    name = models.TextField()
    filters = models.JSONField(default=dict, db_default={})
    columns = models.JSONField(default=list, db_default=[])
    sort = models.JSONField(default=list, db_default=[])
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        db_default="private",
    )
    tenant_id = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_default=Now())
    updated_at = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        db_table = "saved_view"
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "route", "name"], name="saved_view_name_unique"
            ),
            models.CheckConstraint(
                condition=Q(visibility__in=["private", "shared"]),
                name="saved_view_visibility_check",
            ),
        ]


class ConfigSetting(models.Model):
    """The same key differs per environment and tenant, so it is unique per scope."""

    key = models.TextField()
    value = models.JSONField()
    value_type = models.TextField()
    # A secret is only ever held as a reference to a secret manager entry, so the
    # flag and value_type = 'secret_ref' must agree. There is no secret-value column;
    # secrets are never displayed, support rotation and revocation instead.
    is_secret = models.BooleanField(default=False, db_default=False)
    tenant_id = models.TextField(null=True, blank=True)
    environment = models.TextField()
    updated_at = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        db_table = "config_setting"
        constraints = [
            models.CheckConstraint(
                condition=Q(is_secret=True, value_type="secret_ref")
                | Q(is_secret=False) & ~Q(value_type="secret_ref"),
                name="config_setting_secret_ref",
            ),
            models.UniqueConstraint(
                F("key"),
                F("environment"),
                Coalesce("tenant_id", Value("*")),
                name="config_setting_key_scope_unique",
            ),
        ]


class FeatureFlag(models.Model):
    key = models.TextField()
    description = models.TextField(default="", db_default="", blank=True)
    is_enabled = models.BooleanField(default=False, db_default=False)
    rollout = models.JSONField(default=dict, db_default={})
    tenant_id = models.TextField(null=True, blank=True)
    environment = models.TextField()
    updated_at = models.DateTimeField(auto_now=True, db_default=Now())

    class Meta:
        db_table = "feature_flag"
        constraints = [
            # (key, environment, tenant): production and staging are different
            # facts. Coalesce makes the global row (NULL tenant) unique too.
            models.UniqueConstraint(
                F("key"),
                F("environment"),
                Coalesce("tenant_id", Value("*")),
                name="feature_flag_key_scope_unique",
            ),
        ]


class ConfigChange(models.Model):
    """Every configuration change is a privileged mutation.

    It gets its own history row in addition to its audit event, and carries the same
    environment / tenant scope as the row it changed (keys repeat across scopes).
    """

    class Target(models.TextChoices):
        CONFIG_SETTING = "config_setting"
        FEATURE_FLAG = "feature_flag"

    target_table = models.CharField(max_length=32, choices=Target.choices)
    target_key = models.TextField()
    environment = models.TextField()
    tenant_id = models.TextField(null=True, blank=True)
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    changed_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        related_name="config_changes",
        db_column="changed_by",
        db_index=False,
    )
    reason = models.TextField()
    changed_at = models.DateTimeField(auto_now_add=True, db_default=Now())
    audit_event = models.ForeignKey(
        AuditEvent, on_delete=models.PROTECT, null=True, blank=True, db_index=False
    )

    class Meta:
        db_table = "config_change"
        constraints = [
            models.CheckConstraint(
                condition=Q(target_table__in=["config_setting", "feature_flag"]),
                name="config_change_target_check",
            ),
        ]
        indexes = [
            models.Index(
                fields=["target_table", "target_key", "environment", "-changed_at"],
                name="config_change_target",
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

    export_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    resource = models.TextField()
    requested_by = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        related_name="exports",
        db_column="requested_by",
        db_index=False,
    )
    tenant_id = models.TextField(null=True, blank=True)
    filters = models.JSONField()
    # Record which policy was applied, not merely that one was. An export is the
    # easiest way for field-level policy to be silently bypassed.
    row_policy = models.TextField()
    field_policy = models.TextField()
    reason = models.TextField()
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.QUEUED, db_default="queued"
    )
    row_count = models.IntegerField(null=True, blank=True)
    byte_size = models.BigIntegerField(null=True, blank=True)
    artifact_ref = models.TextField(null=True, blank=True)
    requested_at = models.DateTimeField(default=timezone.now, db_default=Now())
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    download_count = models.IntegerField(default=0, db_default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "export_request"
        constraints = [
            models.CheckConstraint(
                condition=Q(state__in=["queued", "running", "ready", "failed", "expired", "revoked"]),
                name="export_request_state_check",
            ),
        ]
        indexes = [models.Index(fields=["state", "expires_at"], name="export_request_state")]


class DataSubjectRequest(models.Model):
    """Erasure interacts with retention, legal hold and backups.

    A request under legal hold cannot be marked fulfilled (a CHECK below); the hold
    never silently drops it. Access and portability requests link their
    ExportRequest, erasure its AdminJob.
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

    request_uuid = models.UUIDField(
        default=uuid.uuid4, db_default=GenRandomUUID(), unique=True, editable=False
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    subject_ref = models.TextField()
    tenant_id = models.TextField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now, db_default=Now())
    due_at = models.DateTimeField()
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.OPEN, db_default="open"
    )
    legal_hold = models.BooleanField(default=False, db_default=False)
    assigned_to = models.ForeignKey(
        AdminActor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="dsr_assignments",
        db_column="assigned_to",
        db_index=False,
    )
    export_request = models.ForeignKey(
        ExportRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="data_subject_requests",
        db_index=False,
    )
    admin_job = models.ForeignKey(
        AdminJob,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="data_subject_requests",
        db_index=False,
    )
    resolution = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "data_subject_request"
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    kind__in=[
                        "access",
                        "erasure",
                        "rectification",
                        "portability",
                        "restriction",
                        "objection",
                    ]
                ),
                name="data_subject_request_kind_check",
            ),
            models.CheckConstraint(
                condition=Q(state__in=["open", "in-progress", "fulfilled", "refused", "withdrawn"]),
                name="data_subject_request_state_check",
            ),
            models.CheckConstraint(
                condition=~Q(legal_hold=True, state="fulfilled"),
                name="data_subject_request_hold_blocks_fulfilment",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "due_at"], name="data_subject_request_queue")
        ]
