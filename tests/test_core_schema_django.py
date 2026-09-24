# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Builds django_models.py as a real Django app against a real PostgreSQL.

Skipped unless the PostgreSQL environment of _core_schema_pg.py is available AND Django >= 5.1
and psycopg are importable (nothing in the skill itself depends on either). Also skipped, with
the reason, when the installed Django refuses the server: Django 6.1 needs PostgreSQL 15+, so
CI's PostgreSQL 14 leg installs Django 5.2 LTS and its PostgreSQL 16 leg Django 6. It is the
automated form of what the audit checked by hand: `manage.py check` is clean, `sqlmigrate` emits
every CHECK constraint and the COALESCE live-grant index and the canonical FK column names,
`migrate` applies the generated migration plus the trigger migration exactly as the models'
docstring gives it, the schema README's verification SQL passes against the result, a temporary
table cannot stand in for approval_request inside the SoD trigger, business timestamps keep the
caller's values through the ORM, and the index set equals postgres.sql's.

Standard library only for the test itself (Python 3.9+; Django needs 3.10+).
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import unittest

from _core_schema_pg import ENABLED, SCHEMA, SKIP_REASON, PgCase, canonical_check_names, readme_verification_sql


def django_ready():
    if not (importlib.util.find_spec("django") and importlib.util.find_spec("psycopg")):
        return False
    import django

    return django.VERSION >= (5, 1)


def docstring_migration():
    """The trigger migration exactly as django_models.py's docstring gives it, and whether the
    docstring tells the reader to copy django_triggers.sql next to it (audit round 2: it did not,
    and the migration then fails to load with FileNotFoundError)."""
    source = (SCHEMA / "django_models.py").read_text(encoding="utf-8")
    doc = source[source.index('"""') + 3: source.index('"""', source.index('"""') + 3)]
    section = doc[doc.index("Suggested migration"): doc.index("Then run grants.sql")]
    code = [line[4:] for line in section.splitlines() if line.startswith("    ") or not line.strip()]
    body = "\n".join(code).strip() + "\n"
    says_copy = "COPY django_triggers.sql INTO THE APP'S migrations/ DIRECTORY" in section
    return body, says_copy


def django_minimum_postgres():
    """The oldest PostgreSQL the installed Django accepts, as Django itself declares it."""
    from django.db.backends.postgresql.features import DatabaseFeatures

    return tuple(DatabaseFeatures.minimum_database_version)


def unsupported_server_reason(django_version, minimum, server):
    """Why this Django cannot run against this server, or None when it can.

    Django refuses to connect below its minimum (NotSupportedError), which would fail the test
    for a reason that says nothing about the schema.
    """
    if tuple(server) >= tuple(minimum):
        return None
    return "Django %s needs PostgreSQL %s+ but the server is %s; use Django 5.2 LTS (or 6.0) on PostgreSQL 14" % (
        django_version, ".".join(map(str, minimum)), ".".join(map(str, server)))


class TestDjangoServerPairing(unittest.TestCase):
    """No database needed: the gate that turns an impossible pairing into a skip, not a failure."""

    def test_django_61_on_postgres_14_is_a_skip_with_the_reason(self):
        reason = unsupported_server_reason("6.1.1", (15,), (14, 23))
        self.assertIn("needs PostgreSQL 15+", reason)
        self.assertIn("14.23", reason)
        self.assertIn("5.2", reason)

    def test_supported_pairings_run(self):
        self.assertIsNone(unsupported_server_reason("6.1.1", (15,), (16, 14)))
        self.assertIsNone(unsupported_server_reason("6.1.1", (15,), (15, 0)))
        self.assertIsNone(unsupported_server_reason("5.2.17", (14,), (14, 23)))


SETTINGS = """\
import os
SECRET_KEY = "not-a-secret"
INSTALLED_APPS = ["admin_core"]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": %(name)r,
    "USER": os.environ["PGUSER"],
    "PASSWORD": os.environ.get("PGPASSWORD", ""),
    "HOST": os.environ.get("PGHOST", "localhost"),
    "PORT": os.environ.get("PGPORT", "5432"),
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
"""

# Runs inside the throwaway project through the ORM, the way application code would.
ORM_PROBE = """\
import datetime
import django

django.setup()
from django.utils import timezone
from admin_core.models import (AdminActor, AdminActorRole, AdminPermission, AdminRole, ApprovalRequest,
                               DataSubjectRequest)

alice = AdminActor.objects.create(auth_subject="orm-alice", display_name="Alice")
carol = AdminActor.objects.create(auth_subject="orm-carol", display_name="Carol")
role = AdminRole.objects.create(key="orm-role", name="ORM")

# business timestamps keep what the caller gives (audit 5.5: auto_now_add overwrote them)
start = timezone.now() + datetime.timedelta(days=7)
grant = AdminActorRole.objects.create(actor=alice, role=role, granted_by=carol, granted_reason="scheduled",
                                      starts_at=start)
grant.refresh_from_db()
assert grant.starts_at == start, ("a future grant start was overwritten", grant.starts_at, start)
received = timezone.now() - datetime.timedelta(days=3)
dsr = DataSubjectRequest.objects.create(kind="erasure", subject_ref="s1", received_at=received,
                                        due_at=received + datetime.timedelta(days=30))
dsr.refresh_from_db()
assert dsr.received_at == received, ("a backdated receipt was overwritten", dsr.received_at, received)

# ... and still default to now when the caller gives nothing
role2 = AdminRole.objects.create(key="orm-role-2", name="ORM 2")
now_grant = AdminActorRole.objects.create(actor=alice, role=role2, granted_by=carol, granted_reason="now")
now_grant.refresh_from_db()
assert abs(timezone.now() - now_grant.starts_at) < datetime.timedelta(minutes=5), now_grant.starts_at

# the approval_request trigger lets Django's ordinary full save() change the lifecycle columns
AdminPermission.objects.create(key="orm.thing", resource="orm", action="thing")
request = ApprovalRequest.objects.create(command_key_id="orm.thing", payload={"amount": 10, "note": "x"},
                                         payload_hash="h", target_type="thing", requested_by=alice,
                                         reason="r", expires_at=timezone.now() + datetime.timedelta(days=1))
request = ApprovalRequest.objects.get(pk=request.pk)
request.state = "approved"
request.save()
assert ApprovalRequest.objects.get(pk=request.pk).state == "approved"
print("orm probe ok")
"""


@unittest.skipUnless(ENABLED and django_ready(), SKIP_REASON + "; needs django>=5.1 and psycopg importable")
class TestDjangoTranslationOnPostgres(PgCase):
    def dj(self, project, env, *args):
        return subprocess.run(
            [sys.executable, "-m", "django", *args],
            cwd=str(project), env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=300,
        )

    def setUp(self):
        super().setUp()
        import django

        reason = unsupported_server_reason(django.get_version(), django_minimum_postgres(), self.server_version())
        if reason:
            self.skipTest(reason)

    def test_models_check_migrate_and_pass_the_verification(self):
        db = self.new_database(apply_schema=False)
        project = self.tmp / "project"
        (project / "admin_core").mkdir(parents=True)
        (project / "admin_core" / "__init__.py").write_text("", encoding="utf-8")
        shutil.copy(str(SCHEMA / "django_models.py"), str(project / "admin_core" / "models.py"))
        (project / "aw_settings.py").write_text(SETTINGS % {"name": db}, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(project), DJANGO_SETTINGS_MODULE="aw_settings")

        checked = self.dj(project, env, "check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)  # audit 5.1: TypeError on 6.x

        made = self.dj(project, env, "makemigrations", "admin_core")
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        sql = self.dj(project, env, "sqlmigrate", "admin_core", "0001")
        self.assertEqual(sql.returncode, 0, sql.stderr)

        # every canonical CHECK is a real constraint, not just a choices= hint (audit 5.4)
        self.assertEqual(sql.stdout.count("CHECK ("), len(canonical_check_names()))
        for name in canonical_check_names():
            self.assertIn('"%s"' % name, sql.stdout)
        # the live-grant index is over the coalesced tenant (audit 5.2)
        start = sql.stdout.index("admin_actor_role_live_unique")
        self.assertIn("COALESCE", sql.stdout[start: start + 300])
        # FK columns keep their canonical names (audit 5.3), so the SoD trigger works
        for column in ("requested_by", "granted_by", "revoked_by", "initiated_by", "changed_by", "assigned_to"):
            self.assertIn('"%s" bigint' % column, sql.stdout)
            self.assertNotIn('"%s_id"' % column, sql.stdout)

        # the triggers go in the second migration the docstring gives, set up as it says
        migration, says_copy = docstring_migration()
        self.assertTrue(says_copy, "the docstring must say to copy django_triggers.sql next to the migration")
        self.assertIn("0001_initial", migration)
        migrations_dir = project / "admin_core" / "migrations"
        (migrations_dir / "0002_triggers.py").write_text(migration, encoding="utf-8")
        missing = self.dj(project, env, "showmigrations")  # what the docstring warns about
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("FileNotFoundError", missing.stderr)
        shutil.copy(str(SCHEMA / "django_triggers.sql"), str(migrations_dir / "django_triggers.sql"))
        migrated = self.dj(project, env, "migrate")
        self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
        self.assertIn("admin_core.0002_triggers", self.dj(project, env, "showmigrations", "--plan").stdout)
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM pg_trigger WHERE tgname = 'approval_decision_sod'"), "1")
        actual = set(self.scalar(
            db,
            "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE c.contype = 'c' AND n.nspname = 'public' "
            "AND t.relname NOT LIKE 'django_%'",
        ).split())
        self.assertEqual(actual, canonical_check_names())

        verified = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql("public")))
        self.assertIn("all admin_core checks passed", verified.stderr + verified.stdout)

        # django_triggers.sql names its tables unqualified: a fresh session's temporary table
        # called approval_request must not stand in for the real one inside the SoD trigger.
        self.psql(db, "-c", """
INSERT INTO admin_actor (auth_subject, display_name) VALUES ('shadow-alice', 'Alice');
INSERT INTO admin_permission (key, resource, action) VALUES ('shadow.thing', 'shadow', 'thing');
INSERT INTO approval_request (command_key, payload, payload_hash, target_type, requested_by, reason, expires_at)
SELECT 'shadow.thing', '{}', 'h', 'thing', id, 'r', now() + interval '1 day' FROM admin_actor
 WHERE auth_subject = 'shadow-alice';
""")
        shadowed = self.psql(
            db, "-c",
            "CREATE TEMP TABLE approval_request (id bigint, requested_by bigint); "
            "INSERT INTO approval_decision (request_id, approver_id, decision, reason, decided_at) "
            "SELECT r.id, r.requested_by, 'approved', 'self', now() FROM public.approval_request r "
            "WHERE r.command_key = 'shadow.thing'",
            check=False,
        )
        self.assertNotEqual(shadowed.returncode, 0, "self-approval behind a temporary approval_request was allowed")
        self.assertIn("separation of duties", shadowed.stderr)

        (project / "orm_probe.py").write_text(ORM_PROBE, encoding="utf-8")
        probe = subprocess.run([sys.executable, "orm_probe.py"], cwd=str(project), env=env, capture_output=True,
                               encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        self.assertIn("orm probe ok", probe.stdout)

        # The migrated index set is postgres.sql's (audit 5.5): no FK or *_like indexes from Django,
        # only the documented unique pair standing in for admin_role_permission's composite key.
        canon = "%s_canon" % db
        self.psql("postgres", "-c", 'DROP DATABASE IF EXISTS "%s"' % canon)
        self.psql("postgres", "-c", 'CREATE DATABASE "%s"' % canon)
        self.dbs.append(canon)
        self.psql(canon, "-f", str(SCHEMA / "postgres.sql"))
        query = ("SELECT i.relname || ':' || t.relname || ':' || x.indisunique || ':' || (x.indpred IS NOT NULL) "
                 "|| ':' || x.indnkeyatts FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid "
                 "JOIN pg_class t ON t.oid = x.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
                 "WHERE n.nspname = '%s' AND t.relname NOT LIKE 'django%%'")
        django_indexes = dict(r.split(":", 1) for r in self.scalar(db, query % "public").split())
        canonical_indexes = dict(r.split(":", 1) for r in self.scalar(canon, query % "admin_core").split())
        self.assertGreaterEqual(len(canonical_indexes), 40)
        self.assertEqual(django_indexes.pop("admin_role_permission_pk"),
                         "admin_role_permission:true:false:2")  # documented stand-in for the composite PK
        self.assertEqual(django_indexes.pop("admin_role_permission_pkey"), "admin_role_permission:true:false:1")
        self.assertEqual(canonical_indexes.pop("admin_role_permission_pkey"), "admin_role_permission:true:false:2")
        self.assertEqual(django_indexes, canonical_indexes)


if __name__ == "__main__":
    unittest.main()
