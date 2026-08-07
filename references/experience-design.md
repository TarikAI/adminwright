# Enterprise admin-console experience design

## Contents

1. Design scene and principles
2. Prior art to build from
3. Calibration and the default admin look
4. Information architecture
5. Dashboard versus console
6. Lists, tables, and queues
7. Data table specification
8. Record details
9. Forms and command surfaces
10. Realtime and shared data
11. System states and feedback
12. Accessibility and keyboard contracts
13. Responsive behavior
14. Internationalization
15. Visual system and motion
16. Operator enablement
17. UX acceptance checks

## Design scene and principles

Describe the real operating scene before choosing density, theme, and layout:

```text
Who uses this, where, on which device, under what time pressure,
with what expertise, and what happens if they make a mistake?
```

Optimize for earned familiarity, fast recognition, accurate decisions, safe action, and repeated work. Enterprise quality is not visual heaviness. It is predictable structure, complete states, clear consequences, low error rates, and trustworthy behavior.

The scene is an input to every later section of this file. If you cannot answer the five questions above from [discovery.md](discovery.md), you are not ready to lay out a screen.

## Prior art to build from

Consult the canonical sources for the pattern you are building. Read the source; do not reconstruct it from memory. Record what you consulted in `platform.researchSources[]`. URLs are in [resource-index.md](resource-index.md) — do not copy them into project docs.

| Source | Consult it for |
|---|---|
| PatternFly | Enterprise console shell: masthead, primary nav, page structure, toolbars, wizards, notification drawer, empty and failure states |
| IBM Carbon | The data table contract: toolbar, batch actions, selection, sorting, expansion, skeletons, density |
| Atlassian Design System | Admin conventions: nav hierarchy, inline edit, drawer versus modal, section messages, transient versus persistent messaging |
| Shopify Polaris | Admin conventions: index tables, index filters, saved views, bulk actions, page headers with primary and secondary actions |
| Dashboard Design Patterns | Dashboard information design: eight pattern groups derived from a systematic review of 144 dashboards, plus dashboard genre (analytical, operational, narrative, embedded) |
| WAI-ARIA Authoring Practices Guide (APG) | The keyboard contract for every composite widget you ship |

APG is the source for keyboard behavior. This is not optional detail. "Make it keyboard accessible" without a named pattern contract produces interfaces where arrow keys work in one table and not the next, Escape closes one drawer and submits another, and no operator can build a motor habit. Pick the APG pattern by name, implement its full keyboard interaction table, and name the pattern in the component's own documentation.

Precedence when sources disagree: the project's existing design system, then the platform's framework conventions, then the sources above. An existing design system always wins. Extend it; do not import a second vocabulary alongside it.

## Calibration and the default admin look

There is a shape that gets produced regardless of domain: left sidebar, four KPI cards across the top, one generic table below, a gradient accent, rounded cards floating on grey. It is the fallback that appears when no one consulted the operating scene. It is banned as a default.

Also banned unless the scene demands them: a metric card no one can drill into, a chart with no threshold or comparison, "Welcome back" greetings, decorative illustrations in the working area, and a nav reading Dashboard / Users / Settings for a platform that is not about users and settings.

Derive the design from the scene and state the derivation. Record it in `crossCutting.experience` with a matching `decisions[]` entry naming density, landing surface, action placement, and reason.

| Scene fact | Consequence |
|---|---|
| Operator is in the console all day | High density, keyboard-first, persistent filters, saved views, no onboarding chrome |
| Operator visits weekly or less | Text labels over icons, more inline explanation, no memorized shortcuts assumed |
| Minutes matter (fraud, outage, safety) | Land on the queue, age and SLA in the first screenful, one-action claim, no modal chains |
| Work arrives continuously | Queue-and-claim model, live counts, explicit refresh — see Realtime and shared data |
| Mistakes are reversible | Inline action with an undo window |
| Mistakes cost money, data, or legal exposure | Full-page flow, typed confirmation, reason capture, dual control |
| Records number in the thousands or more | Search-first landing, no browsable list as the entry point |
| Operators rotate in frequently | In-context definitions everywhere, handbook-first onboarding |
| Tenants or environments are confusable | Persistent environment identity, per [architecture.md](architecture.md) |
| Sensitive fields are viewed routinely | Masked by default, deliberate reveal, audited read per [security-governance.md](security-governance.md) |

State the choice in three or four lines: what density, what the operator sees first, where actions live, and which scene fact drove each. A design that cannot be traced back to a scene fact is decoration.

## Information architecture

Organize top-level navigation around bounded operational domains and operator jobs.

Common layers: overview and urgent work; domain operations and queues; customers, organizations, or accounts; financial or regulated operations; integrations and technical operations; analytics and reports; configuration and feature exposure; access, security, audit, and compliance.

Do not expose modules a role cannot use. Preserve stable URLs and deep links. Use breadcrumbs only when hierarchy is meaningful. Keep filters, tabs, and selected tenant/environment in the URL when sharing and restoration are useful.

Support common navigation chains:

```text
alert -> filtered queue -> record -> related evidence -> action -> result -> audit
search -> record -> history -> related record -> return with context preserved
```

## Dashboard versus console

The dashboard routes attention. The console does the work. Keep the split strict; a dashboard that grows action controls becomes a second, worse console.

| Belongs on the dashboard | Belongs in the console |
|---|---|
| What needs attention now, counted | Search, filter, and the result set |
| Breached SLAs, queue depth and age, unassigned work | The record and its history |
| Degraded dependencies and integration lag | Commands, forms, and confirmations |
| Exceptions and reconciliation mismatches | Bulk operations and their results |
| Threshold breaches and unusual change | Audit trail and evidence |
| Recent high-risk actions, for authorized roles | Exports and configuration |
| Role-specific next work, as a link | Everything with a side effect |

Rules:

- Every dashboard element links to the console view filtered to exactly the records it counts. A number that cannot be drilled into is decoration — remove it or make it drillable.
- No side-effecting command runs from the dashboard. Claim or assign may run there only when it moves the operator into the console with that item open.
- Do not reproduce a queue on the dashboard. Link to it. Two renderings of the same queue drift.
- If the platform has no urgent work to route, do not build a dashboard. Land on the primary queue or on search.

Every metric must expose its definition and time window, its source and as-of freshness, the comparison or threshold that makes it actionable, the responsible owner or domain, drill-down to the affected records, and the investigation or action path.

Avoid oversized vanity numbers, arbitrary card grids, decorative charts, and color-only meaning.

## Lists, tables, and queues

Choose a table when comparison across structured fields matters. Choose a list when content shape varies. Choose a queue when ownership, priority, age, SLA, and action state matter. Queues are modeled in `workQueues[]`; see [discovery.md](discovery.md) for how they are discovered and [capability-catalog.md](capability-catalog.md) for the capabilities they imply.

A queue is not a table with a filter. A queue additionally needs ownership, age against SLA, a claim model, an escalation path, and a definition of done.

## Data table specification

Build to this. Deviations need a recorded reason.

### Required behavior by volume

Size against production volume from `platform.volumes`, not against seed data. See [test-data.md](test-data.md) for generating realistic sets.

| Largest realistic result set | Required |
|---|---|
| Under ~100 rows | Render all rows; client-side sort and filter acceptable; no pagination. Authorization and field policy still enforced server-side |
| ~100 to ~10,000 rows | Server-side search, filter, sort, and pagination mandatory. Offset pagination acceptable when data changes slowly. Show total count. Column visibility and density controls |
| Above ~10,000, unbounded, or high change rate | Cursor pagination. No exact total unless it is cheap — show an approximate or a range and label it. Saved views. Export becomes a job. Virtualize only when the workflow is genuinely scroll-based |

### Column semantics

Every column declares:

- **Role:** identifier, status, ownership, time, decision field, measure, or action
- **Source:** the field or derivation, and whether it is authoritative or computed — consistent with the capability's `dataBinding`
- **Sortable:** yes only if sorting is implemented server-side over the whole result set
- **Format:** numerics right-aligned with tabular figures; timestamps absolute with explicit timezone, relative time secondary
- **Truncation and sensitivity:** where the full value is reachable, identifiers copyable, masked fields marked, and whether revealing one is an audited privileged read

The leftmost non-selection column is the identifier the operator says out loud. Status must be visible without horizontal scroll.

### Selection model

Three scopes, never conflated:

```text
page      the selected rows on the current page only
filtered  every record matching the active filter, including unloaded pages
explicit  an enumerated list of record ids
```

- Show the active scope in words with a count: "12 selected on this page" versus "All 4,318 records matching the current filter".
- The header checkbox selects the page. Extending to the filtered set is a separate, explicit control that appears only after a page selection.
- Transmit the scope as expressed: page and explicit send ids; filtered sends the filter predicate plus an as-of marker. Do not expand a filtered selection into ids in the browser.
- Record the executed scope in the audit event and restate it in the result.

Conflating these causes incidents. A "select all" that silently means the page under-applies: the operator believes the backlog is cleared and it is not. A "select all" that silently means the filtered set over-applies: the operator saw fifty rows and acted on four thousand. Re-evaluating the filter at execution time against a dataset that has moved acts on records the operator never saw and never approved.

Clear or explicitly restate the selection when the filter changes, when sort changes the membership, and always when tenant or environment changes.

### Bulk actions

Before execution, preview: command name, exact count, scope in words, a sample of affected records, irreversible effects, and what will be skipped and why — ineligible state, out of scope, claimed by another operator.

- Above a project-defined size, a bulk action becomes a job. Return a job id and a place to watch it. Mechanism is in [architecture.md](architecture.md).
- Report per item: succeeded, skipped, failed, each with a reason. Make the failure list exportable or copyable.
- Retry acts on the failed subset only, and must be idempotent.
- Never report a bulk operation as successful when any item failed. "Partially succeeded" is a real state, not a rounding error.

### Sort stability

- Every sort must be total. Append the unique id as a final tiebreaker to any non-unique key; without it, pagination repeats and skips rows.
- Sort applies server-side to the whole result set. Sorting the loaded page and calling it sorted is a defect.
- Default sort is whatever the work needs — oldest first for queues, newest first for logs — and the active sort is always visible.
- Null ordering is explicit and consistent.
- A column that cannot be sorted server-side is not sortable. Remove the affordance.

### Pagination

| Use | When |
|---|---|
| Offset / page numbers | Slowly changing data, bounded result sets, and the operator benefits from "page 7 of 42" and an exact total |
| Cursor / keyset | Data changes while paging, large or unbounded sets, queues with arriving work, exports and streams |

Cursor pagination costs jump-to-page and exact totals. Say so in the interface rather than fabricating a total.

### Saved views

A saved view is filter, sort, visible columns and order, density, and page size, under a name.

- Put filter, sort, and columns in the URL so a view can be pasted into an incident channel.
- Scope views to an owner or a role; allow one default per role.
- A shared view has an owner, and editing one is a behavior change — see Operator enablement.
- Do not ship an empty saved-view feature. Seed the two or three views the operating scene requires.

### Density and column visibility

- Provide at least comfortable and compact. Persist per operator per table.
- Persist column visibility and order per operator, with a reset control. Identifier and status columns cannot be hidden.
- A newly added column defaults to visible for existing operators; persisted preferences must not hide required new data.

### Empty, filtered-empty, forbidden-empty, error

| State | Behavior |
|---|---|
| Empty | No records exist. Explain what this table will contain and the action that creates the first record, or why the operator cannot create one |
| Filtered-empty | Records exist, the filter matched none. Restate the active filter, keep it intact, offer clear and widen-time-range |
| Forbidden-empty | Records exist outside the operator's scope. Say access is scoped. Do not imply absence and do not leak counts |
| Error | The query failed. Never render a failed query as "no results" |

### Row actions

- Label with the command verb and the target noun: "Suspend account". Icon-only actions need accessible names and are acceptable only for frequent, unambiguous operations.
- At most one or two inline actions; the rest in an overflow menu ordered by frequency, with destructive actions last and visually separated.
- A disabled action states why, in a tooltip and in an accessible description. Greyed with no explanation is a defect.
- The available action set comes from the server's authorization decision, not a client-side guess.

### Keyboard contract

Bind each element to its APG pattern and implement that pattern's full keyboard interaction table.

| Element | APG pattern |
|---|---|
| Read-only tabular data, sortable headers | Table |
| Cell navigation, inline editing, focusable cell content | Grid |
| Row and header selection | Checkbox |
| Overflow row actions | Menu Button, Menu and Menubar |
| Filter typeahead | Combobox |
| Confirmation and destructive prompts | Dialog (Modal), Alert and Message Dialogs |
| Expandable rows | Disclosure (Show/Hide) |
| Hierarchical rows | Treegrid |

Half a Grid is worse than a plain Table: pick one and finish it. Sort state is exposed on the column header. Focus after a row is removed moves to a predictable neighbor, never to the document body. No keyboard trap anywhere in the table, its toolbar, or its menus.

## Record details

A detail surface should help the operator answer:

- What is this, which tenant/environment owns it, and what state is it in and why?
- What happened, in what order, and through which source?
- Which related records or providers matter?
- What can I do, and why might an action be unavailable?
- What sensitive data am I seeing and under what policy?

Use meaningful sections, primary-detail layouts, tabs, or progressive disclosure. Avoid nested card piles. Keep identity and critical status visible while navigating long details. Show timestamps with timezone clarity and raw identifiers with copy affordances.

History should distinguish business events, user/admin changes, integration events, and technical failures without merging them into an unreadable log dump.

## Forms and command surfaces

Choose the surface from risk and task length, not from habit:

```text
single reversible field, operator stays in the list  -> inline
1-8 fields, under a minute, needs the context behind -> drawer
multi-step, review-heavy, long, or high/critical risk -> full page
one blocking decision that must be answered now      -> modal
```

- **Inline:** never for multi-field edits, irreversible effects, or commands requiring a reason.
- **Drawer:** never nest a second drawer, and never put a step sequence in one. If the operator must scroll the drawer to see the primary action, it is a page.
- **Full page:** deep-linkable, resumable, and drafts survive navigation. Use it whenever the operator will be interrupted.
- **Modal:** at most two or three fields. Never stack modals. Never use one when the answer requires reading the page behind it.

Confirmation strength follows `capability.risk`:

| Risk | Confirmation |
|---|---|
| low | None. Provide undo |
| moderate | One explicit confirm naming the command and the target |
| high | Typed confirmation of the target identifier, plus reason capture |
| critical | Typed confirmation, reason, second approver per separation of duties, step-up authentication |

See [security-governance.md](security-governance.md) for approval, reason, and step-up requirements.

Everything else about forms:

- Use field labels and help that reflect domain language. Show required, optional, immutable, derived, and sensitive fields accurately.
- Validate at appropriate times without erasing input. Keep server validation authoritative and map errors to fields or command context.
- Disable duplicate submission while showing clear progress. Explain why an action is unavailable.
- Never clear operator input on a server error.

For dangerous actions, state the consequence, affected scope, reversibility, and expected completion behavior. Confirmation language must name the actual command and target.

## Realtime and shared data

Operators share data and act on it at the same time. Design that explicitly. The transport, polling interval, and conflict detection belong in [architecture.md](architecture.md); what follows is the operator's experience of them.

**Live counts.** Queue depth, unassigned work, and breach counts update on a stated cadence with a visible as-of time. A count that updates silently and invisibly is indistinguishable from a stale one.

**Never move the ground under an action.** A live feed must not reorder, insert, or remove the row the operator has selected, opened, or is pointing at. New work arrives behind an explicit control: "7 new items — show". A row resolved by someone else is marked resolved in place and removed on the next explicit refresh.

**Presence.** Show who else has a record or queue item open, and since when. Say plainly whether presence is only a courtesy signal or an actual lock. Presence alone never prevents duplicate work.

**Claim and assignment.** Where two operators can pick up the same item, provide an explicit claim with a server-enforced holder and a visible expiry. Show claimed-by and claimed-at in the queue, allow filtering to unclaimed, warn before a claim lapses, and make release a single action.

**It changed under you.** Never silently refresh a form or a detail view. Show what changed, by whom, and when; preserve the operator's input; offer reload or compare. If the change invalidates the pending command — the record left the precondition state — disable the command and say which precondition failed.

**Rejected on stale state.** Distinguish "your view was out of date" from "you are not permitted to do this". Different cause, different message, different recovery.

**Connection loss.** Three honest states:

| State | Operator sees |
|---|---|
| Live | Updating, with cadence and as-of time |
| Degraded | Delayed or polling, with the as-of time and what is affected |
| Disconnected | Data frozen, with the time it froze and an explicit reconnect |

Never leave a frozen view looking live. Block freshness-dependent high-risk commands while disconnected and say why. On reconnect, refresh visibly and preserve pending operator input.

**Signal scope.** Realtime notifications are scoped to the operator's roles and queues. A firehose gets muted, and then the one alert that mattered is muted with it.

## System states and feedback

Cover states intentionally. Declare the covered set in `screens[].states`.

- Initial loading: structure-preserving skeletons. Empty: what the space represents and the next action. Filtered empty: preserve filters, offer reset.
- Error: safe next action, correlation ID when useful. Forbidden: distinguish absent permission from missing resource without leaking data.
- Conflict: newer state shown, recoverable input preserved. Stale/partial: freshness and affected sections identified.
- Degraded/maintenance: what read-only or delayed behavior remains. Success: the authoritative result and next state.
- Async: queued, running, progress, partial, failed, completed, with job history.

Do not use a success toast as the only evidence of a high-impact result.

## Accessibility and keyboard contracts

Target WCAG 2.2 AA unless a stricter requirement applies. Take keyboard behavior from the APG pattern, not from invention; see Prior art to build from.

- Use semantic headings, landmarks, labels, tables, buttons, links, and live regions.
- Name the APG pattern each composite component implements, and implement its whole keyboard interaction table.
- Support logical tab order and visible focus. Make all functionality keyboard-operable.
- Manage focus after navigation, dialogs, drawers, errors, and dynamic updates.
- Associate table headers and cells; provide captions or accessible names.
- Do not encode status or charts by color alone. Provide sufficient contrast and adequate target size.
- Announce validation, async progress, and results appropriately — live counts must not announce on every tick.
- Respect reduced motion and zoom/reflow.
- Test with keyboard and at least one screen-reader workflow for critical paths; record the result in `screens[].accessibilityStatus`.

## Responsive behavior

Admin consoles are usually desktop-primary, not desktop-only.

- Collapse or transform navigation structurally. Preserve essential identity, status, and actions.
- Replace wide tables with controlled horizontal scrolling, prioritized columns, or list/detail patterns. Do not silently drop important data.
- Keep filters discoverable, show active-filter count, and make touch targets usable.
- Test narrow widths, zoom, long localization, and virtual keyboards.
- Avoid forcing high-risk complex workflows onto an unsuitable viewport; communicate limitations explicitly if necessary.

## Internationalization

- Store and transmit timestamps consistently; display user/tenant timezone explicitly.
- Use locale-aware number, date, currency, percentage, address, name, and phone formatting. Define currency and rounding behavior for financial operations.
- Never concatenate translated sentence fragments. Allow text expansion and right-to-left layout where supported.
- Distinguish translated labels from invariant IDs, codes, and provider values.
- Use clear language suitable for operators with different expertise.

## Visual system and motion

- Use the existing design system and component vocabulary.
- Ensure every interactive component has default, hover, focus, active, disabled, loading, and error behavior.
- Use restrained semantic color with consistent status meanings; use typography and spacing to express hierarchy and density.
- Avoid nested cards, ornamental glass effects, gradient text, and decorative motion. Use motion only to communicate state, typically 150–250 ms.
- Keep charts accessible, labeled, and subordinate to the decision.
- Use realistic content to validate truncation, density, and scanning.

## Operator enablement

A console that only works for the person who built it is unfinished.

**In-context help.** Every non-obvious field, status, metric, and command carries its definition where it is used, not in a separate wiki. A definition states what it means, who or what changes it, and what depends on it. Status vocabularies get a legend listing every state at the point of use.

**What a new authorized operator needs**, without asking anyone: where the work arrives, how to find one record, what the states mean, which command to run, what that command does downstream, how to confirm it worked, what to do when it fails, and who to escalate to. Anything on that list that lives only in a support script, a spreadsheet, or someone's head is a gap — file it in `gaps[]`.

**Operator-facing changelog.** Announce behavior changes in the console: a command's effect, a state's meaning, a threshold, a permission, a default view, a field's source. Each entry carries the date, what changed, and what the operator should do differently. Cosmetic changes are not entries; behavior changes always are. This is separate from the audit trail and from engineering release notes.

**Generated operator handbook.** Emit it from the manifest so it cannot drift:

```text
python <skill-dir>/scripts/admin_console_manifest.py emit --manifest <path> \
  --format operator-handbook --out <docs-path>
```

Use `py -3` or `python3` if `python` is not on PATH.

The handbook is derived from roles and scopes, screens and routes, entities with lifecycle states and transitions, queues with SLA and actions, commands with preconditions and safeguards, and failure and escalation paths. Anything the handbook cannot state is a modeling defect, not a writing problem: an empty `rationale`, a missing lifecycle transition, an unnamed audit event. Fix the manifest, re-emit.

Emitting is not acceptance. [verification.md](verification.md) carries the check that it works: a new authorized operator completes the workflow using only the console and the handbook.

## UX acceptance checks

For each major role and workflow verify:

- A new authorized operator can find and complete the task using only the console and the handbook.
- A frequent operator can complete it efficiently with keyboard and saved context.
- The active tenant/environment and target are unambiguous.
- Every dashboard count drills into exactly the records it counts.
- The bulk scope executed matches the scope displayed, and partial failure is reported per item.
- No live update moved, reordered, or removed the row under the operator's action.
- Permissions and unavailable actions are understandable.
- Failure does not erase work or imply false success.
- High-risk consequences are visible before execution.
- The result and audit history can be verified afterward.
- The same component and terminology mean the same thing across modules.
- Accessibility, responsive behavior, localization, and large datasets are tested with realistic content.
- The design derivation is recorded in `crossCutting.experience` and traces to a scene fact.
