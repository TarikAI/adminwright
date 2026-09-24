# Admin core schema

Runnable reference schema for the control-plane model in
[../../references/admin-data-model.md](../../references/admin-data-model.md).

`postgres.sql` is the canonical definition. The other files are translations of it, each
carrying a header comment naming what that stack cannot enforce and must delegate to the
database. Read the model reference before adapting — the DDL comments explain constraints
whose reason is not obvious, but not the reasoning behind the model.

| File | Stack | Expresses natively | Needs raw SQL |
|---|---|---|---|
| `postgres.sql` | PostgreSQL 14+ | everything | — |
| `prisma.schema` + `prisma_raw.sql` | Prisma | tables, enums, FKs, plain and composite indexes | CHECK constraints, partial and expression indexes, triggers and functions |
| `drizzle.ts` | Drizzle | tables, CHECK, partial and expression indexes | triggers and functions |
| `django_models.py` + `django_triggers.sql` | Django | tables, CHECK, partial and expression indexes | triggers and functions |
| `laravel_migration.php` | Laravel | tables, indexes | CHECK constraints, partial and expression indexes, triggers and functions |
| `rails_migration.rb` | Rails | tables, CHECK, partial indexes | expression indexes, triggers and functions |
| `grants.sql` | every stack | — | least-privilege grants for the application role (psql) |

The raw statements are written out in full: Prisma and Django keep them in the companion
`.sql` file, Laravel and Rails carry them inline, and Drizzle needs the Integrity section of
`postgres.sql` appended to its generated migration. Do not drop them when adapting.

`tests/test_core_schema.py` parses all six files and compares, without a database: the tables;
the columns and their type families; the CHECK constraints (names, enum value sets, and the
expressions, by logical equivalence); the indexes and named unique constraints (names, UNIQUE,
the ordered key columns and expressions, the `WHERE` predicate); foreign-key `ON DELETE`
actions; and the function and trigger text. A change to `postgres.sql` that a variant misses in
any of those fails CI. It cannot see what a tool adds or changes at migration time, so CI also
builds the Django, Prisma and Drizzle variants on a real PostgreSQL and runs the verification
SQL at the end of this file against each, and against `postgres.sql` itself. Laravel and Rails
are checked by the static comparison only.

## What this is and is not

These are the tables the admin console needs **about itself**: who may operate it, what they
may do, what they did, what is awaiting approval, and what work is running.

They are not your domain tables. Users, orders, payouts, and content stay where they are.
Nothing here replaces them.

An existing equivalent in the project always wins. If the application already has an audit
table, extend it to the column set in the model reference rather than adding a second one.
Two audit logs is worse than one imperfect audit log, because now neither is complete.

## Applying it

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f postgres.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v app_role=admin_app [-v audit_admin_role=audit_admin] -f grants.sql
```

Requires PostgreSQL 14+. The script runs `CREATE EXTENSION IF NOT EXISTS citext` (into
`public`) for the actor email column; drop that line and change the column to `text` with a
lowercase unique index if you would rather not add the extension. UUID defaults use the
built-in `gen_random_uuid()` and the hash chain uses the built-in `sha256()`.

The Django translation needs Django 5.1+. Django 6.1 requires PostgreSQL 15+, so on
PostgreSQL 14 use Django 5.2 LTS (or 6.0). CI runs it with Django 5.2 on PostgreSQL 14 and with
Django 6 on PostgreSQL 16 and 18.

The script creates an `admin_core` schema. Rename it, or drop the schema statement and let the
tables land in `public`, to match project convention (then pass `-v schema=public` to
`grants.sql`).

The database owns the audit hash chain: leave `chain_seq`, `prev_hash`, `row_hash` and the
payload hashes out of your `INSERT`s and read the doc's hash-chain section for the encoding.
`SELECT * FROM admin_core.verify_audit_chain()` returns one row per problem and nothing when
the stored chain is consistent; `admin_core.scrub_audit_payload(...)` is the only way to drop
an audit payload.

## Adapting it

| Decision | Guidance |
|---|---|
| Naming | Rename freely to match the project. Keep `admin_permission.key` equal to the manifest `capability.id` and `audit_event.action` equal to that key — the traceability the manifest checks depends on this equality |
| Separate or shared admin identity | Drop `admin_actor` and add `is_privileged`, `auth_strength`, `mfa_enrolled` to the app's user table when operators share the product's lifecycle and identity provider. Keep it separate when operator accounts have a different lifecycle, a stricter auth policy, or must survive a compromise of customer-facing auth |
| Tenancy | `tenant_id` columns are `text` so they fit any tenant key shape (and `config_setting`, `feature_flag` and `config_change` carry one, so a setting can be per tenant). On a single-tenant platform, leave them `NULL` rather than removing them — retrofitting tenancy later is far more expensive than carrying an unused column |
| Scope encoding | The `jsonb` scope plus the denormalized `scope_tenant_id` is the workable default. Move to a policy engine only when the signals in the model reference's trade-off table point that way |
| Audit volume | Partition `audit_event` by month once inserts justify it. Partition before the table is large, not after, and read the partitioning notes in the model reference first: they change the primary key and unique indexes |
| Hash chain | Keep the columns even if you do not anchor externally at first. Adding them later means the chain cannot cover historic rows |
| Impersonation limits | The 8-hour maximum in `impersonation_max_duration` is a placeholder; set it to the project's policy |

## Profile adaptation

| Table | internal | standard | regulated |
|---|---|---|---|
| `admin_actor`, `admin_role`, `admin_permission`, `admin_role_permission` | required | required | required |
| `admin_actor_role` with scope and `expires_at` | scope may collapse to a single tenant | required | required; no permanent high-risk grants |
| `audit_event` | required; a reduced column set is acceptable | required | required; full column set |
| Append-only triggers | optional | recommended | required, plus `grants.sql` (no UPDATE / DELETE / TRUNCATE for the application role) |
| `prev_hash` / `row_hash` chain | optional | recommended | required, with external anchoring |
| `admin_policy_version` | optional | recommended | required |
| `impersonation_session` | required if impersonation exists at all | required | required, with restricted scope |
| `approval_request` / `approval_decision` | drop if nothing needs dual control | required for high-risk commands | required, with the separation-of-duties trigger |
| `admin_job` / `admin_job_failure` | required if any bulk or async work exists | required | required |
| `saved_view` | optional | optional | optional |
| `config_setting`, `feature_flag`, `config_change` | drop `config_change` only if nothing is configurable | required | required |
| `export_request` | drop if nothing is exportable | required if anything is exportable | required |
| `data_subject_request` | drop if no privacy obligations apply | required if personal data is processed | required |

Dropping a table at `internal` is a recorded decision, not a silent omission. Put it in
`decisions[]` with the reason, so a later audit can tell a deliberate scope choice from a gap.

## Translation rules for other stacks

Four things in `postgres.sql` cannot be expressed by most ORMs and must stay in the
database regardless of which stack owns the migrations:

1. **Append-only enforcement** on `audit_event` and `approval_decision`: `UPDATE`, `DELETE`
   and `TRUNCATE` all raise. ORM-level hooks are bypassed by raw queries, console sessions,
   and other services.
2. **The hash chain and payload scrub**: the `BEFORE INSERT` trigger that assigns
   `chain_seq`, `prev_hash` and `row_hash`, `verify_audit_chain()`, and
   `scrub_audit_payload()`, the single permitted `UPDATE`.
3. **The separation-of-duties trigger** on `approval_decision`. Enforcing it only in
   application code means the control is absent for anything that writes the table directly.
4. **Partial and expression unique indexes** — the live-grant uniqueness on
   `admin_actor_role`, the idempotency uniqueness on `admin_job`, the one-live-session
   index, and the `(key, environment, tenant)` uniqueness on `config_setting` and
   `feature_flag`. Most ORMs cannot express a filtered or `COALESCE` unique index in their
   model DSL; write it as a raw statement inside the migration.

Per stack:

| Stack | Approach |
|---|---|
| Prisma | Model the tables in `prisma.schema`; append `prisma_raw.sql` to a migration created with `prisma migrate dev --create-only`. Prisma does not model CHECK constraints, and Prisma 7 takes the connection URL from `prisma.config.ts`, not the schema |
| Drizzle | Tables, CHECK, and partial and expression indexes translate directly (`.where()`, a `sql` template inside `.on()`). Functions and triggers go in a hand-written migration alongside: append the Integrity section of `postgres.sql` |
| Django | Models plus `constraints` in `Meta` for CHECK and `UniqueConstraint(condition=...)` for partial uniqueness; `db_default` for database defaults. Functions and triggers go in a `RunSQL` migration that reads `django_triggers.sql`. Django 6 spells the CHECK keyword `condition=` (`check=` was removed) |
| Laravel | Schema builder for tables and indexes; `DB::statement()` / `DB::unprepared()` inside the migration for CHECK constraints, expression indexes, functions and triggers |
| Rails | `create_table` with `check_constraint`, `add_index` with `where:` for partial uniqueness, and `execute` for expression indexes, functions and triggers. Set `schema_format = :sql` so triggers survive schema dumps |
| Go, .NET | Apply `postgres.sql` directly through the project's migration tool. Neither ecosystem gains from re-expressing it in a model DSL |

Whatever the stack, run `grants.sql` for the regulated profile and apply the same test after
migrating: attempt an `UPDATE` and a `DELETE` on `audit_event` and a `DELETE` on
`approval_decision` and confirm each raises. A schema that describes immutability without
enforcing it gives an auditor a false answer.

## Verifying the install

The block below is the test. It builds its own fixtures, asserts the row counts before and
after every refused statement (so an empty table cannot make it pass vacuously), fails with
`VERIFY FAILED: ...` and a non-zero exit when any control is missing or refuses for the wrong
reason, and rolls everything back. Run it with
`psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f verify.sql` after saving it, replacing the
`search_path` line with the schema your tables live in (`public` for most variants). CI runs
it against a real PostgreSQL on every change.

```sql
BEGIN;
SET LOCAL search_path = admin_core, public;
-- Django (and some other ORMs) declare foreign keys DEFERRABLE INITIALLY DEFERRED; make
-- them immediate so each refusal below is raised by the statement that caused it.
SET CONSTRAINTS ALL IMMEDIATE;

-- Runs one statement that must be refused with one of the given SQLSTATEs ('23503', or
-- '23503|23001' where versions differ). Any other outcome (allowed, or refused for a
-- different reason) aborts with XX000.
CREATE FUNCTION pg_temp.expect_refused(label text, stmt text, expected text)
RETURNS void LANGUAGE plpgsql AS $f$
BEGIN
    BEGIN
        EXECUTE stmt;
    EXCEPTION WHEN OTHERS THEN
        IF SQLSTATE = ANY (string_to_array(expected, '|')) THEN
            RAISE NOTICE 'ok: % refused (%)', label, SQLSTATE;
            RETURN;
        END IF;
        RAISE EXCEPTION 'VERIFY FAILED: % raised % (%), expected %', label, SQLSTATE, SQLERRM, expected
            USING ERRCODE = 'XX000';
    END;
    RAISE EXCEPTION 'VERIFY FAILED: % was allowed', label USING ERRCODE = 'XX000';
END $f$;

DO $verify$
DECLARE
    alice bigint; bob bigint; carol bigint; ev1 bigint; ev2 bigint;
    req bigint; role bigint; sess bigint; n bigint;
BEGIN
    INSERT INTO admin_actor (auth_subject, display_name) VALUES ('verify-alice', 'Alice') RETURNING id INTO alice;
    INSERT INTO admin_actor (auth_subject, display_name) VALUES ('verify-bob', 'Bob') RETURNING id INTO bob;
    INSERT INTO admin_actor (auth_subject, display_name) VALUES ('verify-carol', 'Carol') RETURNING id INTO carol;

    -- 1. audit chain: the database assigns chain_seq / prev_hash / row_hash.
    INSERT INTO audit_event (chain_id, occurred_at, actor_id, environment, action, target_type,
                             target_id, result, before_state, after_state)
    VALUES ('verify', now(), alice, 'production', 'user.suspend', 'user', 'u1', 'succeeded',
            '{"status":"active"}', '{"status":"suspended"}') RETURNING id INTO ev1;
    INSERT INTO audit_event (chain_id, occurred_at, actor_id, environment, action, target_type,
                             target_id, result)
    VALUES ('verify', now(), alice, 'production', 'user.restore', 'user', 'u1', 'succeeded')
    RETURNING id INTO ev2;
    SELECT count(*) INTO n FROM audit_event WHERE chain_id = 'verify';
    IF n <> 2 THEN RAISE EXCEPTION 'VERIFY FAILED: expected 2 audit rows, found %', n USING ERRCODE = 'XX000'; END IF;
    IF (SELECT chain_seq FROM audit_event WHERE id = ev2) <> 2
       OR (SELECT prev_hash FROM audit_event WHERE id = ev2) IS DISTINCT FROM
          (SELECT row_hash FROM audit_event WHERE id = ev1) THEN
        RAISE EXCEPTION 'VERIFY FAILED: chain_seq / prev_hash not linked' USING ERRCODE = 'XX000';
    END IF;
    IF EXISTS (SELECT 1 FROM verify_audit_chain('verify')) THEN
        RAISE EXCEPTION 'VERIFY FAILED: a healthy chain reported problems' USING ERRCODE = 'XX000';
    END IF;

    -- 2. audit_event is append-only, and the rows are still there afterwards.
    PERFORM pg_temp.expect_refused('UPDATE audit_event', 'UPDATE audit_event SET reason = ''x'' WHERE id = ' || ev1, 'P0001');
    PERFORM pg_temp.expect_refused('DELETE audit_event', 'DELETE FROM audit_event WHERE id = ' || ev1, 'P0001');
    PERFORM pg_temp.expect_refused('TRUNCATE audit_event CASCADE', 'TRUNCATE audit_event CASCADE', 'P0001');
    SELECT count(*) INTO n FROM audit_event WHERE chain_id = 'verify';
    IF n <> 2 THEN RAISE EXCEPTION 'VERIFY FAILED: a refused mutation changed the row count (%)', n USING ERRCODE = 'XX000'; END IF;

    -- 3. the one permitted UPDATE: scrub the payload, keep the hashes and the chain.
    PERFORM scrub_audit_payload(ev1, 'pii-v1', 'retention window elapsed', alice);
    IF (SELECT before_state FROM audit_event WHERE id = ev1) IS NOT NULL
       OR (SELECT before_hash FROM audit_event WHERE id = ev1) IS NULL
       OR NOT EXISTS (SELECT 1 FROM audit_event WHERE action = 'audit.scrub_payload' AND target_id = ev1::text)
       OR EXISTS (SELECT 1 FROM verify_audit_chain('verify')) THEN
        RAISE EXCEPTION 'VERIFY FAILED: scrub dropped the hashes, broke the chain, or was not audited' USING ERRCODE = 'XX000';
    END IF;
    PERFORM pg_temp.expect_refused('direct payload UPDATE', 'UPDATE audit_event SET after_state = NULL WHERE id = ' || ev1, 'P0001');

    -- 4. tampering by someone who can disable the trigger is detected by the verifier.
    ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_update;
    UPDATE audit_event SET action = 'tampered' WHERE id = ev2;
    ALTER TABLE audit_event ENABLE TRIGGER audit_event_no_update;
    IF NOT EXISTS (SELECT 1 FROM verify_audit_chain('verify') WHERE problem LIKE 'row_hash%') THEN
        RAISE EXCEPTION 'VERIFY FAILED: verify_audit_chain missed an edited row' USING ERRCODE = 'XX000';
    END IF;

    -- 5. approvals: separation of duties, and approval_decision is append-only.
    INSERT INTO admin_permission (key, resource, action) VALUES ('verify.thing', 'verify', 'thing');
    INSERT INTO approval_request (command_key, payload, payload_hash, target_type, requested_by, reason, expires_at)
    VALUES ('verify.thing', '{}', 'h', 'thing', alice, 'r', now() + interval '1 day') RETURNING id INTO req;
    PERFORM pg_temp.expect_refused('self-approval', format('INSERT INTO approval_decision (request_id, approver_id, decision, reason) VALUES (%s, %s, ''approved'', ''r'')', req, alice), 'P0001');
    INSERT INTO approval_decision (request_id, approver_id, decision, reason) VALUES (req, bob, 'rejected', 'r');
    PERFORM pg_temp.expect_refused('second vote by one approver', format('INSERT INTO approval_decision (request_id, approver_id, decision, reason) VALUES (%s, %s, ''approved'', ''r'')', req, bob), '23505');
    PERFORM pg_temp.expect_refused('UPDATE approval_decision', 'UPDATE approval_decision SET decision = ''approved'' WHERE request_id = ' || req, 'P0001');
    PERFORM pg_temp.expect_refused('DELETE approval_decision', 'DELETE FROM approval_decision WHERE request_id = ' || req, 'P0001');
    PERFORM pg_temp.expect_refused('TRUNCATE approval_decision CASCADE', 'TRUNCATE approval_decision CASCADE', 'P0001');
    -- PostgreSQL 18 reports an ON DELETE RESTRICT refusal as 23001 (restrict_violation);
    -- older versions, and NO ACTION keys such as Django's, report 23503.
    PERFORM pg_temp.expect_refused('DELETE approval_request with decisions', 'DELETE FROM approval_request WHERE id = ' || req, '23503|23001');
    SELECT count(*) INTO n FROM approval_decision WHERE request_id = req;
    IF n <> 1 THEN RAISE EXCEPTION 'VERIFY FAILED: the rejection did not survive (% rows)', n USING ERRCODE = 'XX000'; END IF;

    -- 6. live-grant uniqueness, including the global (NULL tenant) grant.
    INSERT INTO admin_role (key, name) VALUES ('verify-role', 'Verify') RETURNING id INTO role;
    INSERT INTO admin_actor_role (actor_id, role_id, granted_by, granted_reason) VALUES (alice, role, carol, 'r');
    PERFORM pg_temp.expect_refused('duplicate live global grant', format('INSERT INTO admin_actor_role (actor_id, role_id, granted_by, granted_reason) VALUES (%s, %s, %s, ''r'')', alice, role, carol), '23505');
    INSERT INTO admin_actor_role (actor_id, role_id, scope_tenant_id, granted_by, granted_reason) VALUES (alice, role, 't1', carol, 'r');
    PERFORM pg_temp.expect_refused('duplicate live tenant grant', format('INSERT INTO admin_actor_role (actor_id, role_id, scope_tenant_id, granted_by, granted_reason) VALUES (%s, %s, ''t1'', %s, ''r'')', alice, role, carol), '23505');
    PERFORM pg_temp.expect_refused('self-grant', format('INSERT INTO admin_actor_role (actor_id, role_id, granted_by, granted_reason) VALUES (%s, %s, %s, ''r'')', bob, role, bob), '23514');

    -- 7. configuration keys are unique per (key, environment, tenant), not per key.
    INSERT INTO feature_flag (key, environment) VALUES ('verify.flag', 'production'), ('verify.flag', 'staging');
    INSERT INTO feature_flag (key, environment, tenant_id) VALUES ('verify.flag', 'production', 't1');
    SELECT count(*) INTO n FROM feature_flag WHERE key = 'verify.flag';
    IF n <> 3 THEN RAISE EXCEPTION 'VERIFY FAILED: expected 3 flag rows, found %', n USING ERRCODE = 'XX000'; END IF;
    PERFORM pg_temp.expect_refused('duplicate flag scope', 'INSERT INTO feature_flag (key, environment) VALUES (''verify.flag'', ''production'')', '23505');
    PERFORM pg_temp.expect_refused('secret value stored inline', 'INSERT INTO config_setting (key, value, value_type, is_secret, environment) VALUES (''k'', ''"s3cret"'', ''string'', true, ''production'')', '23514');

    -- 8. impersonation: one live session, bounded duration, an end reason.
    INSERT INTO impersonation_session (real_actor_id, target_subject_id, reason, expires_at)
    VALUES (alice, 'customer-1', 'support ticket', now() + interval '1 hour') RETURNING id INTO sess;
    IF NOT (SELECT read_only FROM impersonation_session WHERE id = sess) THEN
        RAISE EXCEPTION 'VERIFY FAILED: read_only must default to true' USING ERRCODE = 'XX000';
    END IF;
    PERFORM pg_temp.expect_refused('second live session', format('INSERT INTO impersonation_session (real_actor_id, target_subject_id, reason, expires_at) VALUES (%s, ''c2'', ''r'', now() + interval ''1 hour'')', alice), '23505');
    PERFORM pg_temp.expect_refused('nine-hour session', format('INSERT INTO impersonation_session (real_actor_id, target_subject_id, reason, expires_at) VALUES (%s, ''c2'', ''r'', now() + interval ''9 hours'')', bob), '23514');
    PERFORM pg_temp.expect_refused('ending without a reason', 'UPDATE impersonation_session SET ended_at = now() WHERE id = ' || sess, '23514');
    UPDATE impersonation_session SET ended_at = now(), ended_reason = 'operator_exit' WHERE id = sess;
    PERFORM pg_temp.expect_refused('acted-as subject without a session', format('INSERT INTO audit_event (occurred_at, actor_id, effective_actor_id, environment, action, target_type, result) VALUES (now(), %s, ''customer-1'', ''production'', ''x'', ''x'', ''succeeded'')', alice), '23514');

    RAISE NOTICE 'all admin_core checks passed';
END
$verify$;

ROLLBACK;
```

Put the same checks in the project's test suite, referenced from the manifest's
`crossCutting.audit.evidence` and `crossCutting.safety.evidence`. A constraint nobody tests is
a constraint that quietly disappears in a future migration.
