# Stack adapters

## Contents

1. Read the stack before writing code
2. Identifying the stack
3. The seven seams
4. Next.js App Router
5. Remix and React Router
6. Laravel
7. Django
8. Ruby on Rails
9. NestJS and Express
10. Supabase
11. Go
12. .NET
13. Commerce and CMS backends
14. When the stack is not listed

## Read the stack before writing code

The control plane described in [architecture.md](architecture.md) is stack-neutral. Its
landing points are not. Every framework has an idiomatic place to enforce authorization, run
background work, and emit audit events, and putting them anywhere else produces a console
that works in development and leaks in production.

Required order:

1. Identify the stack and the pinned versions from the repository.
2. Read that stack's current documentation for the seams below. Entry points are in
   [resource-index.md](resource-index.md).
3. Record what you consulted in `platform.researchSources[]` and the stack itself in
   `platform.stack`.
4. Only then write code.

Guessing at a framework's auth, policy, or job idioms is a defect, not a shortcut. The
validator reports empty `platform.stack` and missing `researchSources` at release.

## Identifying the stack

| Evidence | Indicates |
|---|---|
| `package.json` + `next.config.*` + `app/` directory | Next.js App Router |
| `package.json` + `vite.config.*` + `app/routes/` | Remix or React Router framework mode |
| `composer.json` + `artisan` | Laravel; check `app/Filament/` for Filament |
| `manage.py` + `settings.py` | Django; check `INSTALLED_APPS` for `django.contrib.admin` |
| `Gemfile` + `config/routes.rb` | Rails; check for admin engines |
| `nest-cli.json` or `@nestjs/*` deps | NestJS |
| `go.mod` + `sqlc.yaml` or `migrations/` | Go |
| `*.csproj` + `Program.cs` | ASP.NET Core |
| `supabase/config.toml` or `@supabase/*` deps | Supabase |
| `medusa-config.*`, `payload.config.*`, `directus` deps | Commerce or CMS backend |

Also read the lockfile for actual versions, the ORM schema or migrations for the data layer,
and any existing middleware, policy, or guard directory. An admin console is a privileged
client of what is already there; find it before adding a parallel system.

## The seven seams

Every adapter below resolves the same seven questions. So must any stack not listed.

| Seam | The question |
|---|---|
| Admin gateway | Where do privileged requests enter, separated from customer routes? |
| Authorization point | Where is the server-side decision made, on every query and command? |
| Command modeling | Where does a named domain command live, with its invariants? |
| Job execution | How does long, retryable, or provider-dependent work run off the request? |
| Audit emission | Where is the audit event written, in the same transaction where possible? |
| Tenant scope | How does tenant and environment identity propagate through every layer? |
| List querying | How are server-side search, filter, sort, and pagination expressed? |

## Next.js App Router

| Seam | Landing point |
|---|---|
| Admin gateway | A dedicated `app/admin/` route group with its own layout and middleware matcher |
| Authorization | In the Server Action or Route Handler body, and in every data-access function. Never in `middleware.ts` alone |
| Commands | Server Actions delegating to a domain service; the action is transport, not logic |
| Jobs | An external queue (BullMQ, Inngest, QStash). Serverless request timeouts make in-request work unsafe |
| Audit | Inside the domain service, in the same database transaction as the mutation |
| Tenant scope | Resolved from the session server-side, never from a client-supplied parameter |
| Lists | Server Components calling repository functions with `searchParams` for filter state |

Traps:

- `middleware.ts` runs before the request reaches the handler but is easy to bypass through
  matcher gaps, and it cannot see the row being requested. It is a routing convenience, not
  the authorization point.
- Server Actions are public HTTP endpoints. An action that is only rendered for admins is
  still callable by anyone who has the action id. Authorize inside every action body.
- `"use client"` boundaries silently pull data into the browser bundle. Any field excluded by
  policy must be dropped server-side before it crosses the boundary, not hidden in the
  component.
- Aggressive route caching can serve one tenant's rendered data to another. Mark privileged
  routes dynamic and key any cache by tenant and role.

## Remix and React Router

| Seam | Landing point |
|---|---|
| Admin gateway | A `routes/admin.*` segment with a parent loader that establishes the admin session |
| Authorization | In every `loader` and `action`; the parent loader does not protect child routes |
| Commands | `action` functions delegating to domain services |
| Jobs | External queue; the request/response model is not a job runner |
| Audit | In the domain service, transactionally |
| Tenant scope | From the session in the loader, threaded into the repository call |
| Lists | Loader reads the URL search params, which keeps filter state shareable by default |

Trap: a parent route's loader running does not mean a child route's loader is protected —
child loaders are addressable directly. Authorize in each.

## Laravel

| Seam | Landing point |
|---|---|
| Admin gateway | A route group with `auth`, `verified`, and an admin middleware stack |
| Authorization | Policies and Gates, invoked per action and per model instance |
| Commands | Form Requests for validation plus Action or Service classes for the operation |
| Jobs | Queued Jobs with `ShouldQueue`, retry and backoff configured explicitly |
| Audit | Model events or an explicit audit service inside a `DB::transaction` |
| Tenant scope | Global scopes plus explicit tenant binding; verify scopes are not bypassed by `withoutGlobalScopes` |
| Lists | Eloquent with pagination; watch for N+1 on relationship columns |

Traps:

- `$this->authorize()` in a controller covers that action only. Anything reachable through
  another controller, an API route, a console command, or a queued job needs its own check.
- Filament is a strong accelerator, but its resource-level access checks must be paired with
  model policies. A Filament page that hides an action does not stop the underlying request.
- Global scopes are the usual tenancy mechanism and the usual tenancy bug. Test cross-tenant
  access explicitly rather than trusting the scope is always applied.

## Django

| Seam | Landing point |
|---|---|
| Admin gateway | `django.contrib.admin`, or a separate app for custom operator workflows |
| Authorization | Permission classes and object-level checks; `has_object_permission` for rows |
| Commands | Service functions; keep business rules out of `ModelAdmin` |
| Jobs | Celery or equivalent |
| Audit | `LogEntry` for admin actions, plus a domain audit table for business commands |
| Tenant scope | Explicit queryset filtering in `get_queryset`, per view and per admin class |
| Lists | `list_display`, `list_filter`, `search_fields`, with `select_related` to avoid N+1 |

Django admin's real limits, stated plainly: it is a model editor, not a workflow tool. It is
excellent for internal CRUD by trusted staff and poor at lifecycle commands with
preconditions, approvals, reason capture, and per-row scope. Model-level permissions
(add/change/delete/view) do not express "may refund up to this amount in this region". When
the domain needs transitions rather than edits, build the operator surfaces as their own app
and keep Django admin for the genuinely CRUD-shaped tail. See
[buy-vs-build.md](buy-vs-build.md).

## Ruby on Rails

| Seam | Landing point |
|---|---|
| Admin gateway | An `Admin::` controller namespace, or a mounted engine |
| Authorization | Pundit policies or CanCanCan abilities, invoked per action and per record |
| Commands | Service or interactor objects; keep transitions out of controllers and callbacks |
| Jobs | Active Job with a durable backend |
| Audit | An explicit audit model written in the same transaction |
| Tenant scope | Explicit scoping in the controller or a current-tenant object; avoid implicit thread-local surprises in background jobs |
| Lists | Scopes plus a pagination gem; `includes` to avoid N+1 |

Trap: `verify_authorized`-style callbacks catch missing checks in controllers only.
Background jobs and rake tasks that perform the same mutation bypass them entirely.

## NestJS and Express

| Seam | Landing point |
|---|---|
| Admin gateway | A dedicated admin module or router with its own middleware chain |
| Authorization | Guards in Nest; explicit middleware plus per-handler checks in Express |
| Commands | Providers or command handlers; controllers stay thin |
| Jobs | BullMQ or an equivalent durable queue |
| Audit | In the service, transactionally with the mutation |
| Tenant scope | Request-scoped context, propagated explicitly into repositories and jobs |
| Lists | Repository or query-builder methods with server-side filter, sort, and cursor pagination |

Trap: a global guard protects registered routes. Anything mounted outside the framework's
router, or any handler added later without the decorator, is unprotected by default. Prefer
deny-by-default with explicit opt-in over allow-by-default with opt-out.

## Supabase

| Seam | Landing point |
|---|---|
| Admin gateway | A server-side API you control; never the anon client for privileged operations |
| Authorization | Row Level Security as the backstop, plus explicit server-side policy for admin actions |
| Commands | Postgres functions or a server-side service; not client calls |
| Jobs | Edge functions with an external scheduler, or a separate worker |
| Audit | A trigger or an explicit insert in the same transaction |
| Tenant scope | RLS predicates on tenant columns, verified by test |
| Lists | PostgREST filters server-side, with the policy applied by the database |

The critical warning: RLS is a data-layer backstop, not an admin authorization layer. It
answers "may this JWT read this row", not "may this operator perform this command under
these conditions with this obligation". Admin operations frequently need the service role
key, which bypasses RLS entirely — at that moment every guarantee you had is gone and the
only remaining control is the server-side policy you wrote. Never put the service role key in
anything the browser can reach.

## Go

| Seam | Landing point |
|---|---|
| Admin gateway | A separate router subtree or a separate binary for the admin API |
| Authorization | Explicit middleware plus an in-handler check against the loaded row |
| Commands | Package-level service functions taking an explicit actor and context |
| Jobs | A durable queue; goroutines are not a job system and do not survive a deploy |
| Audit | Written in the same `sql.Tx` as the mutation |
| Tenant scope | Carried in `context.Context` and applied in every query, never implicit |
| Lists | sqlc or hand-written SQL with keyset pagination |

Trap: a goroutine started from a request is cancelled with the request context and lost on
restart. Long admin operations need durable jobs with recorded state.

## .NET

| Seam | Landing point |
|---|---|
| Admin gateway | A separate area or minimal-API group with its own auth policy |
| Authorization | Policy-based authorization with requirements and handlers; resource-based checks for rows |
| Commands | MediatR handlers or services |
| Jobs | Hosted services with a durable queue, or Hangfire |
| Audit | `SaveChanges` interception or an explicit write in the same transaction |
| Tenant scope | A scoped tenant provider plus EF global query filters, verified by test |
| Lists | `IQueryable` composed server-side, materialized with explicit projections |

Trap: EF global query filters are easy to disable with `IgnoreQueryFilters()`. Every use is a
potential cross-tenant leak and deserves a test.

## Commerce and CMS backends

Medusa, Payload, Directus, and Strapi ship an admin. Each gives real leverage for the
entities it models and hits a ceiling at custom lifecycle commands and scoped authorization.

| Seam | Landing point |
|---|---|
| Admin gateway | The platform's admin API, extended with your own routes |
| Authorization | The platform's role system, extended where scope conditions exceed it |
| Commands | Custom endpoints or plugins calling your domain services |
| Jobs | The platform's subscriber or job system, or an external queue |
| Audit | Usually your own; verify what the platform records before assuming coverage |
| Tenant scope | Frequently absent or single-tenant by design; check before promising multi-tenancy |
| Lists | The platform's admin list APIs |

Verify what the platform's audit actually captures. "It has an activity log" and "it records
actor, target, reason, before and after values, and result" are different claims.

## When the stack is not listed

Resolve the same seven seams by reading the code, in this order:

1. Find the routing table. Identify how a privileged route differs from a public one.
2. Find one existing authorization check. Determine whether it is per-route or per-object,
   and whether anything enforces its presence on new handlers.
3. Find one existing write path. Follow it to the transaction boundary and see what else is
   written there.
4. Find the background work mechanism, and confirm it survives a restart.
5. Find how the current tenant is resolved, and whether jobs and events carry it.
6. Find one existing list endpoint and see whether filtering happens in the database or in
   memory.
7. Find the existing audit or activity log and read what it actually stores.

Then record the answers in `platform.stack` and the documentation you read in
`platform.researchSources[]`. If a seam genuinely does not exist yet, that is a finding for
`gaps[]` and part of the walking skeleton in [build-order.md](build-order.md), not something
to invent locally inside one screen.
