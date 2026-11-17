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

def function_with(name, *replacements):
    """The canonical CREATE FUNCTION statement for admin_core.<name>, with each (old, new) applied:
    a control put back the way an earlier version (or an attacker's edit) had it."""
    sql = (SCHEMA / "postgres.sql").read_text(encoding="utf-8")
    start = sql.index("CREATE OR REPLACE FUNCTION admin_core.%s(" % name)
    end = sql.index("$$ LANGUAGE plpgsql;", start) + len("$$ LANGUAGE plpgsql;")
    text = sql[start:end]
    for old, new in replacements:
        assert old in text, "%s no longer contains %r" % (name, old)
        text = text.replace(old, new)
    return text + "\n"


# The chain-link trigger as it was at d6c64a8: it hashed a payload only when one was present and
# otherwise kept whatever before_hash / after_hash / redaction_policy the writer supplied.
PRE_FIX_CHAIN_LINK = function_with(
    "audit_event_chain_link",
    ("NEW.before_hash := CASE WHEN NEW.before_state IS NULL THEN NULL", "NEW.before_hash := CASE WHEN NEW.before_state IS NULL THEN NEW.before_hash"),
    ("NEW.after_hash := CASE WHEN NEW.after_state IS NULL THEN NULL", "NEW.after_hash := CASE WHEN NEW.after_state IS NULL THEN NEW.after_hash"),
    ("NEW.redaction_policy := 'none';", "NULL;"),
)

# Audit 2026-11-17: the chain link kept a writer's recorded_at (T-05), had no identity snapshot
# (T-04), and let any actor name any impersonation session (T-04, I4).
WRITER_RECORDED_AT = function_with("audit_event_chain_link", ("NEW.recorded_at := pg_catalog.clock_timestamp();", ""))
NO_ACTOR_SNAPSHOT = function_with(
    "audit_event_chain_link",
    ("    NEW.actor_subject := NULL;\n", ""),
    ("NEW.actor_subject := (SELECT a.auth_subject FROM admin_core.admin_actor a WHERE a.id = NEW.actor_id);", "NULL;"),
)
ANY_SESSION = function_with("audit_event_chain_link", ("IF FOUND AND (sess.real_actor_id", "IF FALSE AND (sess.real_actor_id"))
# T-03: a decision accepted whatever the request's state.
DECISION_IN_ANY_STATE = function_with(
    "enforce_separation_of_duties",
    ("IF req.state <> 'pending' OR req.expires_at <= pg_catalog.clock_timestamp() THEN", "IF FALSE THEN"))

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
INSERT INTO admin_core.admin_actor (auth_subject, display_name) VALUES ('scrub-requester', 'Requester');
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
SELECT id AS asker FROM admin_core.admin_actor WHERE auth_subject = 'scrub-requester' \gset

BEGIN;
SAVEPOINT nested;
SET LOCAL ROLE "%(red)s";
SELECT admin_core.scrub_audit_payload(:e1, 'pii-v1', 'retention window elapsed', :asker);
RELEASE SAVEPOINT nested;
SELECT set_config('aw.e2', :'e2', true);
SELECT set_config('aw.asker', :'asker', true);
DO $t$
BEGIN
    BEGIN
        PERFORM admin_core.scrub_audit_payload(current_setting('aw.e2')::bigint, 'pii-v1', 'retention window elapsed',
                                               current_setting('aw.asker')::bigint);
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
    IF (SELECT count(*) FROM admin_core.audit_event) <> 1 THEN  -- the first insert
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'the application role cannot read what it wrote';
    END IF;
    IF EXISTS (SELECT 1 FROM admin_core.verify_audit_chain()) THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'verify_audit_chain reported a problem';
    END IF;
    -- T-05: a writer may send recorded_at (ORMs send every column), but the database sets it
    INSERT INTO admin_core.audit_event (occurred_at, recorded_at, environment, action, target_type, result)
    VALUES (now(), '2020-01-01', 'production', 'grants.check', 'x', 'succeeded') RETURNING id INTO new_id;
    IF (SELECT recorded_at FROM admin_core.audit_event WHERE id = new_id) < now() THEN
        RAISE EXCEPTION USING ERRCODE = 'XX000', MESSAGE = 'a writer-supplied recorded_at was kept';
    END IF;
    FOREACH stmt IN ARRAY ARRAY[
        'UPDATE admin_core.audit_event SET reason = ''x''',
        'DELETE FROM admin_core.audit_event',
        'TRUNCATE admin_core.audit_event',
        'DELETE FROM admin_core.approval_decision',
        'UPDATE admin_core.approval_decision SET reason = ''x''',
        'TRUNCATE admin_core.approval_decision',
        'DELETE FROM admin_core.config_change',
        'SELECT admin_core.scrub_audit_payload(' || new_id || ', ''p'', ''r'', 1)',
        -- audit 2026-11-17 T-06: an id ahead of the sequence blocked every later insert
        'INSERT INTO admin_core.audit_event (id, occurred_at, environment, action, target_type, result) VALUES (999999, now(), ''p'', ''x'', ''x'', ''succeeded'')',
        'INSERT INTO admin_core.approval_decision (id, request_id, approver_id, decision, reason) VALUES (999999, 1, 2, ''approved'', ''x'')',
        -- T-03 / T-07: only the lifecycle columns of the guarded tables change, nothing is deleted
        'DELETE FROM admin_core.approval_request',
        'UPDATE admin_core.approval_request SET payload = ''{}''',
        'UPDATE admin_core.admin_actor_role SET role_id = 1',
        'DELETE FROM admin_core.admin_actor_role',
        'UPDATE admin_core.impersonation_session SET target_subject_id = ''x''',
        'TRUNCATE admin_core.approval_request CASCADE'
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
DECLARE ev bigint; asker bigint;
BEGIN
    INSERT INTO admin_core.admin_actor (auth_subject, display_name) VALUES ('grants-asker', 'A') RETURNING id INTO asker;
    INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, result, before_state)
    VALUES (now(), 'production', 'grants.check', 'x', 'succeeded', '{"a":1}') RETURNING id INTO ev;
    SET LOCAL ROLE "%(audit)s";
    PERFORM admin_core.scrub_audit_payload(ev, 'policy-1', 'retention', asker);
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

    # --- audit 2026-11-17: each new control, removed, makes the README verification fail -------

    def test_verification_is_not_vacuous_approval_state_machine(self):
        # T-03: a requester's side set its own request approved with no decisions
        self.assert_verification_fails_without(
            "DROP TRIGGER approval_request_state ON admin_core.approval_request", "approved with no approvals was allowed")

    def test_verification_is_not_vacuous_decision_after_the_request_closed(self):
        self.assert_verification_fails_without(DECISION_IN_ANY_STATE, "decision on a rejected request was allowed")

    def test_verification_is_not_vacuous_request_delete(self):
        self.assert_verification_fails_without(
            "DROP TRIGGER approval_request_no_delete ON admin_core.approval_request",
            "DELETE a pending approval_request was allowed")

    def test_verification_is_not_vacuous_actor_snapshot(self):
        # T-04: rewriting admin_actor re-attributed history without breaking the chain
        self.assert_verification_fails_without(NO_ACTOR_SNAPSHOT, "actor_subject is not the acting actor's auth_subject")

    def test_verification_is_not_vacuous_impersonation_session_owner(self):
        self.assert_verification_fails_without(ANY_SESSION, "another actor's impersonation session was allowed")

    def test_verification_is_not_vacuous_impersonation_session_guard(self):
        self.assert_verification_fails_without(
            "DROP TRIGGER impersonation_session_immutable ON admin_core.impersonation_session",
            "retargeting a session was allowed")

    def test_verification_is_not_vacuous_recorded_at(self):
        # T-05: writers chose the database-witnessed time
        self.assert_verification_fails_without(WRITER_RECORDED_AT, "a writer-supplied recorded_at was kept")

    def test_verification_is_not_vacuous_grant_guard(self):
        # T-07 / T-08: a grant's role could be swapped, a revocation undone, scope and tenant disagree
        self.assert_verification_fails_without(
            "DROP TRIGGER admin_actor_role_guard ON admin_core.admin_actor_role",
            "tenant scope with a global scope_tenant_id was allowed")

    def test_verification_is_not_vacuous_enable_always(self):
        # T-09: an origin-mode trigger is off under session_replication_role = replica
        self.assert_verification_fails_without(
            "ALTER TABLE admin_core.audit_event ENABLE TRIGGER audit_event_no_delete", "are not ENABLE ALWAYS")

    def test_verification_is_not_vacuous_chain_assigned_check(self):
        # T-13: a row the chain trigger never saw inserted silently (Prisma's DB defaults)
        self.assert_verification_fails_without(
            "ALTER TABLE admin_core.audit_event DROP CONSTRAINT audit_event_chain_assigned", "unchained audit row was allowed")

    def test_triggers_fire_under_replica_role(self):
        # T-09: as a superuser with session_replication_role = replica, every guard still refuses.
        db = self.new_database()
        self.psql(db, "-c", """
INSERT INTO admin_core.admin_actor (auth_subject, display_name) VALUES ('r-alice', 'Alice');
INSERT INTO admin_core.admin_permission (key, resource, action) VALUES ('r.thing', 'r', 'thing');
INSERT INTO admin_core.approval_request (command_key, payload, payload_hash, target_type, requested_by, reason, expires_at)
SELECT 'r.thing', '{}', 'h', 'thing', id, 'r', now() + interval '1 day' FROM admin_core.admin_actor;
INSERT INTO admin_core.audit_event (occurred_at, environment, action, target_type, result)
VALUES (now(), 'production', 'x.y', 'x', 'succeeded'), (now(), 'production', 'x.z', 'x', 'succeeded');
""")
        attacks = {
            "DELETE of the chain tail": ("DELETE FROM admin_core.audit_event WHERE chain_seq = 2", "append-only"),
            "UPDATE of an audit row": ("UPDATE admin_core.audit_event SET action = 'rewritten' WHERE chain_seq = 1",
                                       "append-only"),
            "self-approval": ("INSERT INTO admin_core.approval_decision (request_id, approver_id, decision, reason) "
                              "SELECT id, requested_by, 'approved', 'self' FROM admin_core.approval_request",
                              "separation of duties"),
            "approval with no decisions": ("UPDATE admin_core.approval_request SET state = 'approved'", "approvals it requires"),
        }
        for label, (sql, expected) in attacks.items():
            with self.subTest(attack=label):
                result = self.psql(db, "-c", "SET session_replication_role = replica; " + sql, check=False)
                self.assertNotEqual(result.returncode, 0, "%s was allowed under replica" % label)
                self.assertIn(expected, result.stderr)
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM admin_core.audit_event"), "2")
        # and the chain trigger still owns the chain columns: forged values are replaced, not kept
        self.psql(db, "-c", "SET session_replication_role = replica; INSERT INTO admin_core.audit_event "
                          "(chain_seq, occurred_at, environment, action, target_type, result, row_hash) "
                          "VALUES (2, now(), 'production', 'f', 'x', 'succeeded', repeat('0', 64))")
        self.assertEqual(self.scalar(db, "SELECT chain_seq || ':' || (row_hash = repeat('0', 64)) FROM admin_core.audit_event "
                                         "WHERE action = 'f'"), "3:false")
        self.assertEqual(self.scalar(db, "SELECT count(*) FROM admin_core.verify_audit_chain()"), "0")

    def test_function_overloads_cannot_stand_in_for_built_ins(self):
        # T-01 / T-02: a role that can CREATE in public (every role on a database that started on
        # PostgreSQL 14) created exact-type overloads that beat pg_catalog's: to_jsonb(approval_request)
        # let it re-attribute a request and approve it, to_jsonb(audit_event) let it rewrite audit
        # rows, and convert_to(text,text) / sha256(text) blinded verify_audit_chain(). Canonical and
        # carrier (public) form, each attack from a fresh session of a non-owner role.
        for schema in ("admin_core", "public"):
            with self.subTest(schema=schema):
                db = "aw_core_%s_%s" % (self.tag[:40], schema)
                self.psql("postgres", "-c", 'DROP DATABASE IF EXISTS "%s"' % db)
                self.psql("postgres", "-c", 'CREATE DATABASE "%s"' % db)
                self.dbs.append(db)
                if schema == "admin_core":
                    self.psql(db, "-f", str(SCHEMA / "postgres.sql"))
                else:
                    self.psql(db, "-f", self.sql_file("public.sql", unqualified_schema_sql()))
                app = "aw_shadow_%s_%s" % (self.tag[:30], schema[:2])
                if app not in self.roles:
                    self.roles.append(app)
                    self.psql("postgres", "-c", "CREATE ROLE \"%s\" LOGIN PASSWORD 'pw-%s'" % (app, self.tag))
                self.psql(db, "-c", """
GRANT CREATE ON SCHEMA public TO PUBLIC;
GRANT USAGE ON SCHEMA %(s)s TO "%(app)s";
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA %(s)s TO "%(app)s";
GRANT USAGE ON ALL SEQUENCES IN SCHEMA %(s)s TO "%(app)s";
INSERT INTO %(s)s.admin_actor (auth_subject, display_name) VALUES ('alice', 'Alice'), ('bob', 'Bob');
INSERT INTO %(s)s.admin_permission (key, resource, action) VALUES ('payout.release', 'payout', 'release');
INSERT INTO %(s)s.approval_request (command_key, payload, payload_hash, target_type, requested_by, reason,
                                   expires_at, required_approvals)
SELECT 'payout.release', '{"amount":100}', 'h', 'payout', id, 'r', now() + interval '1 day', 2
  FROM %(s)s.admin_actor WHERE auth_subject = 'alice';
INSERT INTO %(s)s.audit_event (occurred_at, environment, action, target_type, target_id, result, before_state)
VALUES (now(), 'production', 'user.delete', 'user', 'u1', 'succeeded', '{"email":"a@example.com"}');
""" % {"s": schema, "app": app})
                attacks = {
                    "request rewrite behind a to_jsonb overload": (
                        "CREATE FUNCTION public.to_jsonb(%(s)s.approval_request) RETURNS jsonb LANGUAGE sql IMMUTABLE "
                        "AS $$ SELECT '{}'::jsonb $$; "
                        "UPDATE %(s)s.approval_request SET requested_by = (SELECT id FROM %(s)s.admin_actor "
                        "WHERE auth_subject = 'bob'), payload = '{\"amount\":99999999}', required_approvals = 1",
                        "immutable except state"),
                    "audit rewrite behind a to_jsonb overload": (
                        "CREATE FUNCTION public.to_jsonb(%(s)s.audit_event) RETURNS jsonb LANGUAGE sql IMMUTABLE "
                        "AS $$ SELECT '{}'::jsonb $$; "
                        "INSERT INTO %(s)s.audit_event (occurred_at, environment, action, target_type, target_id, result) "
                        "SELECT now(), 'production', 'audit.scrub_payload', 'audit_event', min(id)::text, 'succeeded' "
                        "FROM %(s)s.audit_event; "
                        "SELECT set_config('admin_core.scrub', (SELECT max(id) FROM %(s)s.audit_event)::text, false); "
                        "UPDATE %(s)s.audit_event SET action = 'user.view', target_id = 'u2', before_state = NULL, "
                        "redaction_policy = 'p' WHERE id = (SELECT min(id) FROM %(s)s.audit_event)",
                        "may only drop"),
                }
                for label, (sql, expected) in attacks.items():
                    result = subprocess.run(
                        [PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", app, "-d", db, "-c", sql % {"s": schema}],
                        capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                        env=dict(os.environ, PGPASSWORD="pw-%s" % self.tag),
                    )
                    self.assertNotEqual(result.returncode, 0, "%s was allowed (%s)" % (label, schema))
                    self.assertIn(expected, result.stderr, label)
                self.assertEqual(self.scalar(db, "SELECT required_approvals FROM %s.approval_request" % schema), "2")
                self.assertEqual(self.scalar(db, "SELECT action FROM %s.audit_event ORDER BY id LIMIT 1" % schema),
                                 "user.delete")
                # the role leaves convert_to / sha256 overloads behind, then someone who can disable the
                # trigger edits a row: the owner's verify_audit_chain() must still see it
                shadows = subprocess.run(
                    [PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", app, "-d", db, "-c",
                     "CREATE FUNCTION public.convert_to(text, text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT $1 $$; "
                     "CREATE FUNCTION public.sha256(text) RETURNS bytea LANGUAGE sql IMMUTABLE AS $$ SELECT '\\x00'::bytea $$"],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                    env=dict(os.environ, PGPASSWORD="pw-%s" % self.tag),
                )
                self.assertEqual(shadows.returncode, 0, shadows.stderr)
                self.psql(db, "-c", """
ALTER TABLE %(s)s.audit_event DISABLE TRIGGER audit_event_no_update;
UPDATE %(s)s.audit_event SET action = 'tampered' WHERE id = (SELECT min(id) FROM %(s)s.audit_event);
ALTER TABLE %(s)s.audit_event ENABLE ALWAYS TRIGGER audit_event_no_update;
""" % {"s": schema})
                self.assertIn("row_hash does not match", self.scalar(
                    db, "SELECT string_agg(problem, '; ') FROM %s.verify_audit_chain()" % schema))

    def test_grants_sql_refuses_an_app_role_that_can_switch_the_controls_off(self):
        # T-01: grants.sql said the application role must not own the tables, and nothing checked;
        # a role that can CREATE in the schema is refused too. Nothing is granted when it stops.
        app = "aw_app_%s" % self.tag
        self.roles.append(app)
        self.psql("postgres", "-c", 'CREATE ROLE "%s" NOLOGIN' % app)
        for label, setup, expected in (
            ("owns a table", 'ALTER TABLE admin_core.audit_event OWNER TO "%s"' % app, "can disable the triggers"),
            ("can create in the schema", "GRANT CREATE ON SCHEMA admin_core TO PUBLIC", "can create objects"),
        ):
            with self.subTest(case=label):
                db = self.new_database()
                self.psql(db, "-c", setup)
                result = subprocess.run(
                    [PSQL, "-X", "-q", "-d", db, "-v", "app_role=" + app, "-f", str(SCHEMA / "grants.sql")],
                    capture_output=True, encoding="utf-8", errors="replace", timeout=300,
                )
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertEqual(self.scalar(
                    db, "SELECT has_table_privilege('%s', 'admin_core.approval_decision', 'INSERT')" % app), "f")
                self.dbs.remove(db)
                self.psql("postgres", "-c", 'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % db)

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
