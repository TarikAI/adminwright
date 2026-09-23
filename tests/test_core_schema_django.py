# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Builds django_models.py as a real Django app against a real PostgreSQL.

Skipped unless the PostgreSQL environment of _core_schema_pg.py is available AND Django >= 5.1
and psycopg are importable (CI installs `django>=6` and `psycopg[binary]` for this job; nothing
in the skill itself depends on either). It is the automated form of what the audit checked by
hand: `manage.py check` is clean, `sqlmigrate` emits every CHECK constraint and the COALESCE
live-grant index and the canonical FK column names, `migrate` succeeds, django_triggers.sql
applies on top, and the schema README's verification SQL passes against the result.

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


@unittest.skipUnless(ENABLED and django_ready(), SKIP_REASON + "; needs django>=5.1 and psycopg importable")
class TestDjangoTranslationOnPostgres(PgCase):
    def dj(self, project, env, *args):
        return subprocess.run(
            [sys.executable, "-m", "django", *args],
            cwd=str(project), env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=300,
        )

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

        migrated = self.dj(project, env, "migrate")
        self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
        self.psql(db, "-f", str(SCHEMA / "django_triggers.sql"))
        actual = set(self.scalar(
            db,
            "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE c.contype = 'c' AND n.nspname = 'public' "
            "AND t.relname NOT LIKE 'django_%'",
        ).split())
        self.assertEqual(actual, canonical_check_names())

        verified = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql("public")))
        self.assertIn("all admin_core checks passed", verified.stderr + verified.stdout)


if __name__ == "__main__":
    unittest.main()
