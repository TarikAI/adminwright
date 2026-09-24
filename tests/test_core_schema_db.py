# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Runs the canonical admin core schema (postgres.sql) on a real PostgreSQL.

Skipped unless ADMINWRIGHT_PG_TEST=1 and psql plus a PostgreSQL superuser are available (see
_core_schema_pg.py for how CI and a local Docker container provide them).

What it proves that the static drift test cannot: postgres.sql applies (PostgreSQL 14+), the
verification SQL in the schema README passes AND fails when a control is removed (so it is not
vacuous), grants.sql really takes UPDATE / DELETE / TRUNCATE away from the application role, and
concurrent writers keep the audit hash chain gap-free.

Standard library only (Python 3.9+).
"""

import subprocess
import unittest

from _core_schema_pg import ENABLED, PSQL, SCHEMA, SKIP_REASON, PgCase, readme_verification_sql


@unittest.skipUnless(ENABLED, SKIP_REASON)
class TestCoreSchemaOnPostgres(PgCase):
    def test_postgres_sql_applies_and_the_readme_verification_passes(self):
        db = self.new_database()
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM pg_tables WHERE schemaname = 'admin_core'"), "18")
        result = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql()))
        self.assertIn("all admin_core checks passed", result.stderr + result.stdout)
        # ROLLBACK at the end: the verification leaves nothing behind
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM admin_core.audit_event"), "0")
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM admin_core.admin_actor"), "0")

    def assert_verification_fails_without(self, db_statement, expected_message):
        db = self.new_database()
        self.psql(db, "-c", db_statement)
        result = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql()), check=False)
        self.assertNotEqual(result.returncode, 0, "the verification passed with a control removed: " + db_statement)
        self.assertIn(expected_message, result.stderr + result.stdout)

    def test_verification_is_not_vacuous_deletes_of_decisions(self):
        self.assert_verification_fails_without(
            "DROP TRIGGER approval_decision_no_delete ON admin_core.approval_decision",
            "DELETE approval_decision was allowed",
        )

    def test_verification_is_not_vacuous_audit_updates(self):
        self.assert_verification_fails_without(
            "DROP TRIGGER audit_event_no_update ON admin_core.audit_event",
            "UPDATE audit_event was allowed",
        )

    def test_verification_is_not_vacuous_global_grant_uniqueness(self):
        self.assert_verification_fails_without(
            "DROP INDEX admin_core.admin_actor_role_live_unique",
            "duplicate live global grant was allowed",
        )

    def test_verification_is_not_vacuous_feature_flag_scope(self):
        self.assert_verification_fails_without(
            "DROP INDEX admin_core.feature_flag_key_scope_unique; "
            "CREATE UNIQUE INDEX feature_flag_key_only ON admin_core.feature_flag (key)",
            "feature_flag_key_only",
        )

    def test_verification_is_not_vacuous_request_delete_cascade(self):
        # the pre-fix schema: deleting a request cascaded into (and past) its decisions
        self.assert_verification_fails_without(
            "ALTER TABLE admin_core.approval_decision DROP CONSTRAINT approval_decision_request_id_fkey; "
            "ALTER TABLE admin_core.approval_decision ADD CONSTRAINT approval_decision_request_id_fkey "
            "FOREIGN KEY (request_id) REFERENCES admin_core.approval_request(id) ON DELETE CASCADE",
            "DELETE approval_request with decisions",
        )

    def test_verification_is_not_vacuous_job_failure_cascade(self):
        # the pre-fix schema: deleting a job cascaded into its immutable failure rows
        self.assert_verification_fails_without(
            "ALTER TABLE admin_core.admin_job_failure DROP CONSTRAINT admin_job_failure_job_id_fkey; "
            "ALTER TABLE admin_core.admin_job_failure ADD CONSTRAINT admin_job_failure_job_id_fkey "
            "FOREIGN KEY (job_id) REFERENCES admin_core.admin_job(id) ON DELETE CASCADE",
            "DELETE admin_job with failures was allowed",
        )

    def test_grants_sql_takes_mutation_away_from_the_application_role(self):
        db = self.new_database()
        app, audit = "aw_app_%s" % self.tag, "aw_audit_%s" % self.tag
        self.roles.extend([app, audit])
        for role in (app, audit):
            self.psql("postgres", "-c", 'CREATE ROLE "%s" NOLOGIN' % role)
        self.psql(db, "-v", "app_role=" + app, "-v", "audit_admin_role=" + audit, "-f", str(SCHEMA / "grants.sql"))
        script = self.sql_file("as_app.sql", """
BEGIN;
SET LOCAL ROLE "%(app)s";
DO $t$
DECLARE stmt text; new_id bigint;
BEGIN
    INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, result)
    VALUES (now(), 'production', 'grants.check', 'x', 'succeeded') RETURNING id INTO new_id;
    IF (SELECT count(*) FROM admin_core.audit_event) <> 1 THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'the application role cannot read what it wrote';
    END IF;
    IF EXISTS (SELECT 1 FROM admin_core.verify_audit_chain()) THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'verify_audit_chain reported a problem';
    END IF;
    FOREACH stmt IN ARRAY ARRAY[
        'UPDATE admin_core.audit_event SET reason = ''x''',
        'DELETE FROM admin_core.audit_event',
        'TRUNCATE admin_core.audit_event',
        'DELETE FROM admin_core.approval_decision',
        'UPDATE admin_core.approval_decision SET reason = ''x''',
        'TRUNCATE admin_core.approval_decision',
        'DELETE FROM admin_core.config_change',
        'SELECT admin_core.scrub_audit_payload(' || new_id || ', ''p'', ''r'')'
    ] LOOP
        BEGIN
            EXECUTE stmt;
            RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'the application role was allowed: ' || stmt;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'ok: denied %%', stmt;
        END;
    END LOOP;
END $t$;
ROLLBACK;

BEGIN;
DO $t$
DECLARE ev bigint;
BEGIN
    INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, result, before_state)
    VALUES (now(), 'production', 'grants.check', 'x', 'succeeded', '{"a":1}') RETURNING id INTO ev;
    SET LOCAL ROLE "%(audit)s";
    PERFORM admin_core.scrub_audit_payload(ev, 'policy-1', 'retention');
    RESET ROLE;
    IF (SELECT before_state FROM admin_core.audit_event WHERE id = ev) IS NOT NULL
       OR NOT EXISTS (SELECT 1 FROM admin_core.audit_event WHERE action = 'audit.scrub_payload') THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'the audit admin role could not scrub';
    END IF;
END $t$;
ROLLBACK;
""" % {"app": app, "audit": audit})
        result = self.psql(db, "-f", script)
        self.assertIn("ok: denied", result.stderr + result.stdout)

    def test_grants_sql_without_an_app_role_fails_loudly(self):
        # A deploy script that forgets -v app_role must not report success with nothing granted,
        # whether or not the caller passed -v ON_ERROR_STOP=1.
        db = self.new_database()
        for caller_flags in ([], ["-v", "ON_ERROR_STOP=1"]):
            result = subprocess.run(
                [PSQL, "-X", "-q", *caller_flags, "-d", db, "-f", str(SCHEMA / "grants.sql")],
                capture_output=True, encoding="utf-8", errors="replace", timeout=300,
            )
            with self.subTest(caller_flags=caller_flags):
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn("pass -v app_role", result.stderr)
        self.assertEqual(self.scalar(db, "SELECT relacl IS NULL FROM pg_class WHERE oid = 'admin_core.audit_event'::regclass"),
                         "t")

    def test_concurrent_writers_produce_a_gap_free_chain(self):
        db = self.new_database()
        insert = ("INSERT INTO admin_core.audit_event (chain_id, occurred_at, environment, action, target_type, result) "
                  "VALUES ('conc', now(), 'production', 'x.y', 'x', 'succeeded');\n")
        scripts = [self.sql_file("writer%d.sql" % n, insert * 40) for n in range(3)]
        procs = [
            subprocess.Popen([PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db, "-f", s],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
            for s in scripts
        ]
        for p in procs:
            out, err = p.communicate(timeout=300)
            self.assertEqual(p.returncode, 0, out + err)
        self.assertEqual(
            self.scalar(
                db,
                "SELECT count(*) || ',' || max(chain_seq) || ',' || count(DISTINCT chain_seq) FROM admin_core.audit_event "
                "WHERE chain_id = 'conc'",
            ),
            "120,120,120",
        )
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM admin_core.verify_audit_chain('conc')"), "0")


if __name__ == "__main__":
    unittest.main()
