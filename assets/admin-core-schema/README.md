# Admin core schema

Runnable reference schema for the control-plane model in
[../../references/admin-data-model.md](../../references/admin-data-model.md).

`postgres.sql` is the canonical definition. The other files are faithful translations of it,
each carrying a header comment naming what that stack cannot enforce and must delegate to the
database. Read the model reference before adapting — the DDL comments explain constraints
whose reason is not obvious, but not the reasoning behind the model.

| File | Stack | Expresses natively | Needs raw SQL |
|---|---|---|---|
| `postgres.sql` | PostgreSQL 14+ | everything | — |
| `prisma.schema` | Prisma | tables, indexes, composite uniques | CHECK constraints, partial unique indexes, triggers |
| `drizzle.ts` | Drizzle | tables, CHECK, partial indexes | triggers |
| `django_models.py` | Django | tables, CHECK, partial indexes | triggers |
| `laravel_migration.php` | Laravel | tables, indexes | CHECK constraints, partial unique indexes, triggers |
| `rails_migration.rb` | Rails | tables, CHECK, partial indexes | triggers |

Prisma and Laravel carry the most delegated work. In both, the raw statements are already
written out in the file — do not drop them when adapting.

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
psql "$DATABASE_URL" -f postgres.sql
```

Requires PostgreSQL 14+, `pgcrypto` or `pg_catalog.gen_random_uuid()` for UUID defaults, and
the `citext` extension for the actor email column. Drop the `citext` dependency by changing
that column to `text` with a lowercase unique index if you would rather not add the extension.

The script creates an `admin_core` schema. Rename it, or drop the schema statement and let the
tables land in `public`, to match project convention.

## Adapting it

| Decision | Guidance |
|---|---|
| Naming | Rename freely to match the project. Keep `admin_permission.key` equal to the manifest `capability.id` and `audit_event.action` equal to that key — the traceability the manifest checks depends on this equality |
| Separate or shared admin identity | Drop `admin_actor` and add `is_privileged`, `auth_strength`, `mfa_enrolled` to the app's user table when operators share the product's lifecycle and identity provider. Keep it separate when operator accounts have a different lifecycle, a stricter auth policy, or must survive a compromise of customer-facing auth |
| Tenancy | `tenant_id` columns are `text` so they fit any tenant key shape. On a single-tenant platform, leave them `NULL` rather than removing them — retrofitting tenancy later is far more expensive than carrying an unused column |
| Scope encoding | The `jsonb` scope plus the denormalized `scope_tenant_id` is the workable default. Move to a policy engine only when the signals in the model reference's trade-off table point that way |
| Audit volume | Partition `audit_event` by month once inserts justify it. Partition before the table is large, not after |
| Hash chain | Keep the columns even if you do not anchor externally at first. Adding them later means the chain cannot cover historic rows |

## Profile adaptation

| Table | internal | standard | regulated |
|---|---|---|---|
| `admin_actor`, `admin_role`, `admin_permission`, `admin_role_permission` | required | required | required |
| `admin_actor_role` with scope and `expires_at` | scope may collapse to a single tenant | required | required; no permanent high-risk grants |
| `audit_event` | required; a reduced column set is acceptable | required | required; full column set |
| Append-only triggers | optional | recommended | required, plus revoked UPDATE/DELETE grants |
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

Three constraints in `postgres.sql` cannot be expressed by most ORMs and must stay in the
database regardless of which stack owns the migrations:

1. **Append-only enforcement** on `audit_event` and `approval_decision`. ORM-level hooks are
   bypassed by raw queries, console sessions, and other services.
2. **The separation-of-duties trigger** on `approval_decision`. Enforcing it only in
   application code means the control is absent for anything that writes the table directly.
3. **Partial unique indexes** — the live-grant uniqueness on `admin_actor_role` and the
   idempotency uniqueness on `admin_job`. Most ORMs cannot express a filtered unique index in
   their model DSL; write it as a raw statement inside the migration.

Per stack:

| Stack | Approach |
|---|---|
| Prisma | Model the tables in `schema.prisma`; put triggers, partial unique indexes, and CHECK constraints in a migration created with `prisma migrate dev --create-only` and edited by hand. Prisma does not model CHECK constraints |
| Drizzle | Tables and indexes translate directly, including partial indexes via `.where()`. Triggers go in a hand-written migration alongside |
| Django | Models plus `constraints` in `Meta` for CHECK and `UniqueConstraint(condition=...)` for partial uniqueness. Triggers go in a `RunSQL` migration operation |
| Laravel | Schema builder for tables and indexes; `DB::statement()` inside the migration for triggers and CHECK constraints |
| Rails | `create_table` with `check_constraint`, `add_index` with `where:` for partial uniqueness, and `execute` for triggers. Set `schema_format = :sql` so triggers survive schema dumps |
| Go, .NET | Apply `postgres.sql` directly through the project's migration tool. Neither ecosystem gains from re-expressing it in a model DSL |

Whatever the stack, apply the same test: after migrating, attempt an `UPDATE` on
`audit_event` and confirm it raises. A schema that describes immutability without enforcing it
gives an auditor a false answer.

## Verifying the install

```sql
-- append-only holds
UPDATE admin_core.audit_event SET reason = 'x' WHERE id = 1;   -- must raise

-- separation of duties holds
--   insert an approval_request, then an approval_decision by the same actor -- must raise

-- live-grant uniqueness holds
--   grant the same role twice to one actor in one tenant scope -- second must fail
```

These three checks belong in the project's test suite, referenced from the manifest's
`crossCutting.audit.evidence` and `crossCutting.safety.evidence`. A constraint nobody tests is
a constraint that quietly disappears in a future migration.
