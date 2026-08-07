# Authoritative resource index

## Contents

1. How to use this index
2. Discovery and user needs
3. Enterprise console patterns
4. Dashboard information design
5. Tables and high-density interfaces
6. Accessibility
7. Security and authorization
8. Audit, compliance, and privacy
9. Money movement and idempotency
10. Observability
11. Implementation accelerators
12. Stack documentation
13. Recording what you consulted

## How to use this index

This is a starting point, not a substitute for the project's own documentation. Every entry
below is a general reference. None of them knows your stack's version, your framework's
current API, or your organization's controls.

Two rules:

- Reach for an entry when you are about to make a decision it covers, not as background
  reading. Each entry names the question it answers.
- Before implementing against any framework, read that framework's current documentation at
  the version the project actually pins. These links change; the project's lockfile does not
  lie. See [stack-adapters.md](stack-adapters.md) for how to identify the stack first.

Verified reachable 2026-08-07. A dead link is a defect worth fixing, not a reason to guess.

## Discovery and user needs

| Question | Source |
|---|---|
| How do I learn what operators actually need instead of assuming? | [GOV.UK Service Manual: user research](https://www.gov.uk/service-manual/user-research) |

Reach for this when the platform has real operators you can observe and you are tempted to
design from the database schema instead. It covers interviewing, observing current
processes, and turning needs into stories. Most of what an admin console must do is already
happening somewhere in a spreadsheet, a support macro, or a manual SQL snippet.

## Enterprise console patterns

| Question | Source |
|---|---|
| What are the established patterns for a dense operational console? | [PatternFly](https://www.patternfly.org/) |

PatternFly is built for enterprise consoles specifically, not marketing sites. Reach for it
when deciding navigation structure, filter and toolbar layout, bulk-action affordances,
alerting, and status presentation. Treat it as a vocabulary, not a mandate — the project's
own design system always wins where one exists.

## Dashboard information design

| Question | Source |
|---|---|
| What belongs on a dashboard, and how should it be laid out? | [Dashboard Design Patterns](https://dashboarddesignpatterns.github.io/) |

A research-backed pattern collection covering data, layout, interaction, drill-down,
multi-page structure, screen space, and visualization choice. Reach for it when designing
the landing surface. It is the antidote to the card wall: it forces the question of what
decision each element supports.

## Tables and high-density interfaces

| Question | Source |
|---|---|
| What does a complete data table owe the operator? | [IBM Carbon data table](https://carbondesignsystem.com/components/data-table/usage/) |
| What is the required keyboard behavior for a grid? | [WAI-ARIA APG: grid pattern](https://www.w3.org/WAI/ARIA/apg/patterns/grid/) |
| Which interaction pattern applies to this component? | [WAI-ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/patterns/) |
| How do I mark up a complex table? | [W3C tables tutorial](https://www.w3.org/WAI/tutorials/tables/) |

Carbon covers search, sorting, expansion, filtering, batch actions, pagination, loading, and
accessibility as one connected contract. The ARIA APG is the source for keyboard behavior:
"make it keyboard accessible" without the pattern contract produces interfaces that are
technically operable and practically unlearnable, because every table behaves differently.
Read the pattern before building the component, not after the accessibility audit.

## Accessibility

| Question | Source |
|---|---|
| What is the conformance target? | [WCAG 2.2](https://www.w3.org/TR/WCAG22/) |
| What does this success criterion require in practice? | [WCAG 2.2 quick reference](https://www.w3.org/WAI/WCAG22/quickref/) |

Target WCAG 2.2 AA unless a stricter requirement applies. The quickref is the working
document; the specification is the authority when they appear to disagree. Automated
checking finds a minority of real barriers — the keyboard and screen-reader passes required
in [verification.md](verification.md) are not optional extras.

## Security and authorization

| Question | Source |
|---|---|
| What security requirements apply at this assurance level? | [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) |
| What are the most common classes of failure? | [OWASP Top 10](https://owasp.org/www-project-top-ten/) |
| How should authorization be designed and enforced? | [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) |
| What should be logged, and what must never be? | [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) |
| Which second factor is appropriate for privileged roles? | [OWASP MFA Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html) |

ASVS is the checklist to select a level against and verify. The Authorization cheat sheet is
the one to read before designing the policy layer: least privilege, deny by default,
validation on every request, and automated authorization tests are all stated there, and all
four are requirements in [security-governance.md](security-governance.md).

High-risk or regulated platforms need qualified human security review. This index does not
replace it.

## Audit, compliance, and privacy

| Question | Source |
|---|---|
| What access and audit controls are expected? | [NIST SP 800-53 Rev. 5](https://csrc.nist.gov/pubs/sp/800/53/r5/upd1/final) |
| What must a subject access request return? | [GDPR Article 15](https://gdpr-info.eu/art-15-gdpr/) |
| What does erasure actually require? | [GDPR Article 17](https://gdpr-info.eu/art-17-gdpr/) |
| What processing records must exist? | [GDPR Article 30](https://gdpr-info.eu/art-30-gdpr/) |

In NIST SP 800-53, the AC (access control) and AU (audit and accountability) families are the
two that map directly onto this skill's authorization and audit requirements. Use them to
justify why a control exists, not to claim compliance.

Mapping a control is not passing an audit. Article 17 in particular does not mean a delete
button: it interacts with retention, legal hold, and backups, which is why
[security-governance.md](security-governance.md) requires you to distinguish archive,
deactivate, redact, anonymize, soft delete, and hard delete rather than collapse them.

## Access control and log management models

| Question | Source |
|---|---|
| How do I model attributes, scope, and conditions? | [NIST SP 800-162 (ABAC)](https://csrc.nist.gov/pubs/sp/800/162/upd2/final) |
| What is an obligation, formally? | [XACML 3.0](http://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html) |
| How long do logs live, and how are they protected? | [NIST SP 800-92](https://csrc.nist.gov/pubs/sp/800/92/final) |
| How do I anchor a hash chain externally? | [RFC 3161 timestamping](https://www.rfc-editor.org/rfc/rfc3161) |
| How does row-level security actually behave? | [PostgreSQL row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) |
| How do I partition a large audit table? | [PostgreSQL partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html) |

Reach for these when designing the control-plane data model in
[admin-data-model.md](admin-data-model.md). SP 800-162 is the vocabulary behind the
subject/resource/action/scope/condition/obligation model this skill uses; XACML is where
"obligation" is defined precisely enough to implement.

## Money movement and idempotency

| Question | Source |
|---|---|
| How should a retryable money command behave? | [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) |
| What states does a refund actually pass through? | [Stripe refunds](https://docs.stripe.com/refunds) |

Read these as prior art even when the project uses a different provider. They are the
clearest public statement of the semantics every financial admin command needs: a client-
supplied key, a durable result for a repeated key, and an explicit unknown-result path. An
admin refund button without these is a duplicate-payment incident waiting for a slow network.

## Observability

| Question | Source |
|---|---|
| How should admin operations be instrumented? | [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/) |

Reach for this when naming spans, metrics, and attributes for admin queries, commands, jobs,
and integration calls. Consistent naming is what makes the correlation IDs required by
[architecture.md](architecture.md) actually usable during an incident.

## Implementation accelerators

Read these as prior art and as candidates in the buy-versus-build decision. A framework may
satisfy a requirement; it may never waive one. See [buy-vs-build.md](buy-vs-build.md).

| Tool | Reach for it when |
|---|---|
| [Refine](https://refine.dev/docs/) | React CRUD with routing, auth, access control, audit-log and realtime hooks already modeled |
| [react-admin](https://marmelab.com/react-admin/documentation.html) | React admin with a mature data-provider abstraction over an existing API |
| [AdminJS](https://adminjs.co/) | Node/TypeScript autogenerated admin over existing ORM models |
| [Filament](https://filamentphp.com/docs) | Laravel; the most complete first-party-feeling admin in any PHP stack |
| [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) | Django; excellent for internal CRUD, weak for custom lifecycle workflows |
| [Directus](https://directus.com/docs) | A database-first admin and API over an existing schema |
| [Payload](https://payloadcms.com/docs) | Content-shaped domains where the admin is part of the product |
| [TanStack Table](https://tanstack.com/table/latest) | You are hand-building the data grid and need the headless table primitives |

## Stack documentation

Always read the version the project pins. These are the entry points, not the answers.

| Stack | Entry point |
|---|---|
| Next.js App Router | [nextjs.org/docs/app](https://nextjs.org/docs/app) |
| Laravel | [laravel.com/docs](https://laravel.com/docs) |
| Django admin | [docs.djangoproject.com](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) |
| Ruby on Rails | [guides.rubyonrails.org](https://guides.rubyonrails.org/) |
| NestJS | [docs.nestjs.com](https://docs.nestjs.com/) |
| ASP.NET Core authorization | [learn.microsoft.com](https://learn.microsoft.com/en-us/aspnet/core/security/authorization/introduction) |
| Supabase row level security | [supabase.com/docs](https://supabase.com/docs/guides/database/postgres/row-level-security) |

## Recording what you consulted

Every source that changed an implementation decision goes into `platform.researchSources[]`:

```text
{ "topic": "Laravel policy registration",
  "url": "https://laravel.com/docs/...",
  "appliedTo": ["user.suspend", "payout.approve"],
  "checkedOn": "2026-08-07" }
```

The validator reports a missing `researchSources` at release. The point is not bookkeeping:
it is that a later agent auditing this console can tell the difference between a decision
grounded in the framework's documented behavior and one that was guessed and happened to work.
