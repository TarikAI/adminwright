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

import os
import subprocess
import unittest

from _core_schema_pg import (ENABLED, PSQL, SCHEMA, SKIP_REASON, PgCase, readme_verification_sql,
                             unqualified_schema_sql)

# The chain-link trigger as it was at d6c64a8: it hashed a payload only when one was present and
# otherwise kept whatever before_hash / after_hash / redaction_policy the writer supplied.
PRE_FIX_CHAIN_LINK = r"""
CREATE OR REPLACE FUNCTION admin_core.audit_event_chain_link()
RETURNS trigger AS $$
DECLARE
    last_seq  bigint;
    last_hash text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('admin_core.audit_event:' || NEW.chain_id, 0));
    SELECT a.chain_seq, a.row_hash INTO last_seq, last_hash
      FROM admin_core.audit_event a WHERE a.chain_id = NEW.chain_id ORDER BY a.chain_seq DESC LIMIT 1;
    NEW.chain_seq := COALESCE(last_seq, 0) + 1;
    NEW.prev_hash := last_hash;
    IF NEW.before_state IS NOT NULL THEN
        NEW.before_hash := encode(sha256(convert_to(NEW.before_state::text, 'UTF8')), 'hex');
    END IF;
    IF NEW.after_state IS NOT NULL THEN
        NEW.after_hash := encode(sha256(convert_to(NEW.after_state::text, 'UTF8')), 'hex');
    END IF;
    NEW.row_hash := encode(sha256(convert_to(admin_core.audit_event_hash_input(NEW), 'UTF8')), 'hex');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# The update guard as it worked at d6c64a8, a user-settable flag and nothing else (made to accept
# the id the current scrub_audit_payload() sets, so the rest of the verification still runs).
FLAG_ONLY_GUARD = r"""
CREATE OR REPLACE FUNCTION admin_core.audit_event_guard_update()
RETURNS trigger AS $$
BEGIN
    IF coalesce(current_setting('admin_core.scrub', true), '') IN ('', 'off') THEN
        RAISE EXCEPTION 'audit_event is append-only';
    END IF;
    IF NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL
       OR (to_jsonb(NEW) - 'before_state' - 'after_state' - 'redaction_policy')
          IS DISTINCT FROM (to_jsonb(OLD) - 'before_state' - 'after_state' - 'redaction_policy') THEN
        RAISE EXCEPTION 'audit_event scrub may only drop the payload';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# A guard that checks the setting names a scrub event, but not that the event is for this row.
ANY_EVENT_GUARD = r"""
CREATE OR REPLACE FUNCTION admin_core.audit_event_guard_update()
RETURNS trigger AS $$
DECLARE
    scrub_event text := current_setting('admin_core.scrub', true);
BEGIN
    IF scrub_event IS NULL OR scrub_event !~ '^[0-9]{1,18}$' THEN
        RAISE EXCEPTION 'audit_event is append-only';
    END IF;
    IF NEW.before_state IS NOT NULL OR NEW.after_state IS NOT NULL THEN
        RAISE EXCEPTION 'audit_event scrub may only drop the payload';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM admin_core.audit_event s
                    WHERE s.id = scrub_event::bigint AND s.action = 'audit.scrub_payload') THEN
        RAISE EXCEPTION 'no scrub event';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# A verifier that does everything except notice a payload dropped without its scrub event.
BLIND_VERIFIER = r"""
ALTER FUNCTION admin_core.verify_audit_chain(text) RENAME TO verify_audit_chain_full;
CREATE FUNCTION admin_core.verify_audit_chain(p_chain_id text DEFAULT NULL)
RETURNS TABLE (chain_id text, chain_seq bigint, problem text)
LANGUAGE sql STABLE AS $$
    SELECT * FROM admin_core.verify_audit_chain_full(p_chain_id) f WHERE f.problem NOT LIKE 'payload dropped%'
$$;
"""


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

    def test_verification_is_not_vacuous_flag_only_scrub_guard(self):
        # audit finding 5.11 (round 2): SET LOCAL admin_core.scrub = 'on' was enough to drop a payload
        self.assert_verification_fails_without(FLAG_ONLY_GUARD, "GUC-only payload drop was allowed")

    def test_verification_is_not_vacuous_scrub_event_of_another_row(self):
        self.assert_verification_fails_without(
            ANY_EVENT_GUARD, "payload drop under another row's scrub event was allowed")

    def test_verification_is_not_vacuous_unaudited_payload_drop(self):
        self.assert_verification_fails_without(
            BLIND_VERIFIER, "verify_audit_chain missed a payload dropped without a scrub event")

    def test_payload_drops_always_leave_a_scrub_event(self):
        # The finding's own scenario: a role that holds UPDATE on audit_event without owning it
        # (the standard profile, where grants.sql is not applied). Its only way to drop a payload
        # writes an audit.scrub_payload event naming the row; the sanctioned function works from
        # a savepoint and from a PL/pgSQL exception block (ORMs nest transactions that way).
        db = self.new_database()
        app, redactor = "aw_app_%s" % self.tag, "aw_red_%s" % self.tag
        self.roles.extend([app, redactor])
        for role in (app, redactor):
            self.psql("postgres", "-c", 'CREATE ROLE "%s" NOLOGIN' % role)
        self.psql(db, "-c", """
GRANT USAGE ON SCHEMA admin_core TO "%(app)s", "%(red)s";
GRANT SELECT, INSERT, UPDATE ON admin_core.audit_event TO "%(app)s";
GRANT USAGE ON SEQUENCE admin_core.audit_event_id_seq TO "%(app)s";
GRANT EXECUTE ON FUNCTION admin_core.scrub_audit_payload(bigint, text, text, bigint) TO "%(red)s";
""" % {"app": app, "red": redactor})
        script = self.sql_file("scrub_paths.sql", r"""
BEGIN;
SET LOCAL ROLE "%(app)s";
DO $t$
DECLARE target bigint; mine bigint; before_n bigint;
BEGIN
    INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, target_id, result, before_state)
    VALUES (now(), 'production', 'user.update', 'user', 'u1', 'succeeded', '{"email":"x@example.com"}')
    RETURNING id INTO target;
    SELECT count(*) INTO before_n FROM admin_core.audit_event WHERE action = 'audit.scrub_payload';

    -- the two statements from the finding
    PERFORM set_config('admin_core.scrub', 'on', true);
    BEGIN
        UPDATE admin_core.audit_event SET before_state = NULL, redaction_policy = 'silent' WHERE id = target;
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'GUC-only payload drop was allowed';
    EXCEPTION WHEN raise_exception THEN
        RAISE NOTICE 'ok: refused %%', SQLERRM;
    END;

    -- writing the scrub event itself is the audit record, and then the drop goes through
    INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, target_id, reason, result)
    VALUES (now(), 'production', 'audit.scrub_payload', 'audit_event', target::text, 'manual', 'succeeded')
    RETURNING id INTO mine;
    PERFORM set_config('admin_core.scrub', mine::text, true);
    UPDATE admin_core.audit_event SET before_state = NULL, redaction_policy = 'manual-v1' WHERE id = target;
    IF (SELECT before_state FROM admin_core.audit_event WHERE id = target) IS NOT NULL
       OR (SELECT count(*) FROM admin_core.audit_event WHERE action = 'audit.scrub_payload') <> before_n + 1 THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'an audited drop did not apply, or was not the only new scrub event';
    END IF;

    -- a payload is dropped once: the same event cannot relabel the row afterwards
    BEGIN
        UPDATE admin_core.audit_event SET redaction_policy = 'other' WHERE id = target;
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'a second payload drop was allowed';
    EXCEPTION WHEN raise_exception THEN
        RAISE NOTICE 'ok: refused %%', SQLERRM;
    END;
    PERFORM set_config('admin_core.scrub', '', true);
END $t$;
COMMIT;

INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, target_id, result, after_state)
VALUES (now(), 'production', 'user.update', 'user', 'u2', 'succeeded', '{"phone":"1"}') RETURNING id AS e1 \gset
INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, target_id, result, after_state)
VALUES (now(), 'production', 'user.update', 'user', 'u3', 'succeeded', '{"phone":"2"}') RETURNING id AS e2 \gset

BEGIN;
SAVEPOINT nested;
SET LOCAL ROLE "%(red)s";
SELECT admin_core.scrub_audit_payload(:e1, 'pii-v1', 'retention window elapsed');
RELEASE SAVEPOINT nested;
SELECT set_config('aw.e2', :'e2', true);
DO $t$
BEGIN
    BEGIN
        PERFORM admin_core.scrub_audit_payload(current_setting('aw.e2')::bigint, 'pii-v1', 'retention window elapsed');
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'scrub failed inside a subtransaction: ' || SQLERRM;
    END;
END $t$;
RESET ROLE;
COMMIT;

DO $t$
BEGIN
    IF EXISTS (SELECT 1 FROM admin_core.audit_event WHERE target_type = 'user' AND (before_state IS NOT NULL OR after_state IS NOT NULL)) THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'a sanctioned scrub left a payload behind';
    END IF;
    IF (SELECT count(*) FROM admin_core.audit_event WHERE action = 'audit.scrub_payload') <> 3 THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'expected one scrub event per dropped payload';
    END IF;
    IF EXISTS (SELECT 1 FROM admin_core.verify_audit_chain()) THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'verify_audit_chain reported a problem after audited drops';
    END IF;
    RAISE NOTICE 'scrub paths ok';
END $t$;
""" % {"app": app, "red": redactor})
        result = self.psql(db, "-f", script)
        self.assertIn("scrub paths ok", result.stderr + result.stdout)
        self.assertEqual((result.stderr + result.stdout).count("ok: refused"), 2)

    def test_temporary_tables_cannot_stand_in_for_the_real_ones(self):
        # round-2 finding: in the unqualified carriers a trigger ran with the caller's search_path,
        # where the caller's own temporary tables come first, so a fresh session could create
        # TEMP TABLE approval_request and approve its own request (and fake a scrub event the
        # same way). The same install must also pass the README verification in public.
        db = self.new_database(apply_schema=False)
        self.psql(db, "-f", self.sql_file("unqualified.sql", unqualified_schema_sql()))
        verified = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql("public")))
        self.assertIn("all admin_core checks passed", verified.stderr + verified.stdout)
        app = "aw_app_%s" % self.tag
        self.roles.append(app)
        self.psql("postgres", "-c", "CREATE ROLE \"%s\" LOGIN PASSWORD 'pw-%s'" % (app, self.tag))
        self.psql(db, "-c", """
GRANT USAGE ON SCHEMA public TO "%(app)s";
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "%(app)s";
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "%(app)s";
INSERT INTO admin_actor (auth_subject, display_name) VALUES ('alice', 'Alice');
INSERT INTO admin_permission (key, resource, action) VALUES ('probe.thing', 'probe', 'thing');
INSERT INTO approval_request (command_key, payload, payload_hash, target_type, requested_by, reason, expires_at)
SELECT 'probe.thing', '{}', 'h', 'thing', id, 'r', now() + interval '1 day' FROM admin_actor;
INSERT INTO audit_event (occurred_at, environment, action, target_type, target_id, result, before_state)
VALUES (now(), 'production', 'user.update', 'user', 'u1', 'succeeded', '{"email":"x@example.com"}');
""" % {"app": app})
        attacks = {
            "self-approval behind a temporary approval_request": (
                "CREATE TEMP TABLE approval_request (id bigint, requested_by bigint); "
                "INSERT INTO approval_decision (request_id, approver_id, decision, reason) "
                "SELECT r.id, r.requested_by, 'approved', 'self' FROM public.approval_request r",
                "separation of duties"),
            "payload drop behind a temporary audit_event": (
                "CREATE TEMP TABLE audit_event (id bigint, action text, target_type text, target_id text); "
                "INSERT INTO audit_event SELECT 999999, 'audit.scrub_payload', 'audit_event', e.id::text "
                "FROM public.audit_event e; "
                "SET admin_core.scrub = '999999'; "
                "UPDATE public.audit_event SET before_state = NULL, redaction_policy = 'silent'",
                "audit.scrub_payload event"),
        }
        for label, (sql, expected) in attacks.items():
            with self.subTest(attack=label):
                # a fresh session for each: the attack depends on the temporary table existing
                # before the trigger function is first planned in that session
                result = subprocess.run(
                    [PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", app, "-d", db, "-c", sql],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                    env=dict(os.environ, PGPASSWORD="pw-%s" % self.tag),
                )
                self.assertNotEqual(result.returncode, 0, "%s was allowed" % label)
                self.assertIn(expected, result.stderr)
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM approval_decision"), "0")
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM audit_event WHERE before_state IS NOT NULL"), "1")

    def test_verification_is_not_vacuous_forged_payload_hash(self):
        # round-2 finding: an INSERT could set before_hash with no payload, so the row looked scrubbed
        self.assert_verification_fails_without(
            PRE_FIX_CHAIN_LINK,
            "an insert kept writer-supplied payload hashes",
        )

    def test_verification_is_not_vacuous_request_rewrite(self):
        # without the trigger, re-attributing a request gets the requester past the SoD check
        self.assert_verification_fails_without(
            "DROP TRIGGER approval_request_immutable ON admin_core.approval_request",
            "re-attributing a request was allowed",
        )

    def test_verification_is_not_vacuous_job_idempotency(self):
        self.assert_verification_fails_without(
            "DROP INDEX admin_core.admin_job_idempotency", "duplicate job idempotency key was allowed")

    def test_verification_is_not_vacuous_config_setting_scope(self):
        self.assert_verification_fails_without(
            "DROP INDEX admin_core.config_setting_key_scope_unique", "duplicate config_setting scope was allowed")

    def test_verification_is_not_vacuous_enum_check(self):
        self.assert_verification_fails_without(
            "ALTER TABLE admin_core.audit_event DROP CONSTRAINT audit_event_result_check",
            "unknown audit result was allowed")

    def test_verification_is_not_vacuous_legal_hold(self):
        self.assert_verification_fails_without(
            "ALTER TABLE admin_core.data_subject_request DROP CONSTRAINT data_subject_request_hold_blocks_fulfilment",
            "fulfilled under legal hold was allowed",
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
