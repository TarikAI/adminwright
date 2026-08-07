#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Generate the eval fixtures.

Fixtures are generated rather than hand-written so the manifests stay consistent
with the current template as the schema evolves, and so a reviewer can read the
INTENT of a fixture here instead of diffing 200 lines of JSON.

Two platforms, deliberately different in shape:

  saas-clean       a truthful multi-tenant B2B SaaS slice that must pass everywhere
  logistics-gaps   a dispatch platform with real, named defects that must be caught

Run:  python evals/build_fixtures.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = EVAL_ROOT.parent
SCRIPT = SKILL_ROOT / "scripts" / "admin_console_manifest.py"
FIXTURES = EVAL_ROOT / "fixtures"


def scaffold(name, archetype, profile):
    root = FIXTURES / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    subprocess.run(
        [sys.executable, str(SCRIPT), "init", "--project-root", str(root),
         "--name", name, "--archetype", archetype, "--profile", profile],
        capture_output=True, text=True, check=True,
    )
    return root


def write(root, rel, body):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def save(root, manifest):
    (root / ".admin-console" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def load(root):
    return json.loads((root / ".admin-console" / "manifest.json").read_text(encoding="utf-8"))


def capability(**overrides):
    base = {
        "id": "", "outcome": "", "kind": "query", "roles": [], "risk": "low",
        "status": "implemented", "rationale": "",
        "entityStates": {"from": [], "to": []}, "uiRoutes": [],
        "serverOperations": [], "authorizationPolicies": [], "auditEvents": [],
        "safeguards": [], "dataBinding": "", "tests": [], "evidence": [],
        "owner": "impl", "reviewStatus": "reviewed", "reviewedBy": "qa",
    }
    base.update(overrides)
    return base


def all_true(manifest, evidence_dir="evidence"):
    for section, values in manifest["crossCutting"].items():
        for key, value in list(values.items()):
            if isinstance(value, bool):
                values[key] = True
        if "evidence" in values:
            values["evidence"] = [evidence_dir + "/" + section + ".md"]
    manifest["crossCutting"]["authorization"]["policyTests"] = ["tests/authz.spec.ts"]


def pass_gates(manifest):
    for gate in manifest["qualityGates"]:
        gate["status"] = "passed"
        gate["evidence"] = ["evidence/" + gate["id"] + ".log"]
        gate["threshold"] = "command exits zero"


def build_saas():
    """A truthful slice. Its whole job is to prove honest work is not blocked."""
    root = scaffold("saas-clean", "b2b-saas", "regulated")

    for name in ("authentication", "authorization", "audit", "safety", "data",
                 "experience", "observability"):
        write(root, "evidence/" + name + ".md", "Recorded run output with assertions.\n")
    for name in ("build", "typecheck", "lint", "tests", "browser", "security",
                 "accessibility", "performance"):
        write(root, "evidence/" + name + ".log", "command completed, exit 0\n")
    write(root, "tests/authz.spec.ts",
          "test('role x action x scope matrix', () => {});\n")
    write(root, "tests/tenant.spec.ts",
          "test('tenant.list and tenant.suspend via /admin/tenants', () => {});\n"
          "test('/admin/tenants forbids out-of-scope operators', () => {});\n")
    write(root, "tests/subscription.spec.ts",
          "test('subscription.list and subscription.cancel via /admin/subscriptions', () => {});\n")

    m = load(root)
    m["platform"].update({
        "summary": "Multi-tenant B2B SaaS admin console slice.",
        "operationalObjectives": ["Suspend abusive tenants without engineering involvement"],
        "tenancy": "multi-tenant",
        "sourceSystems": ["PostgreSQL primary", "Stripe"],
        "regulatedData": ["billing"],
        "stack": {"frontend": "React 18", "backend": "NestJS", "database": "PostgreSQL 16",
                  "auth": "OIDC", "jobs": "BullMQ", "hosting": "Fly.io",
                  "designSystem": "in-house", "adminFramework": "none"},
        "researchSources": [{
            "topic": "Idempotent money operations",
            "url": "https://docs.stripe.com/api/idempotent_requests",
            "appliedTo": ["subscription.cancel"], "checkedOn": "2026-08-08",
        }],
        "volumes": {"entityCounts": "12k tenants", "peakConcurrentOperators": "9",
                    "retentionHorizon": "7 years"},
    })
    m["roles"] = [{
        "id": "ops", "name": "Operations", "responsibilities": ["Tenant administration"],
        "scopes": ["all tenants"], "mfaRequired": True,
        "separationOfDuties": ["tenant.suspend"],
    }]
    m["entities"] = [
        {
            "id": "tenant", "name": "Tenant", "sourceOfTruth": "postgres:tenants",
            "sensitivity": "confidential", "tenantScoped": True,
            "lifecycleStates": ["active", "suspended"],
            "lifecycleTransitions": [
                {"from": "active", "to": "suspended", "command": "tenant.suspend",
                 "actorRoles": ["ops"]},
            ],
            "retention": "7 years after closure",
            "capabilities": [
                capability(id="tenant.list", outcome="Find a tenant", roles=["ops"],
                           entityStates={"from": ["active", "suspended"], "to": []},
                           uiRoutes=["/admin/tenants"],
                           serverOperations=["TenantRepository.findForAdmin"],
                           authorizationPolicies=["policy/tenant.read"],
                           dataBinding="postgres:tenants via TenantRepository.findForAdmin",
                           tests=["tests/tenant.spec.ts"], evidence=["evidence/tests.log"]),
                capability(id="tenant.suspend", outcome="Suspend an abusive tenant",
                           kind="command", roles=["ops"], risk="high",
                           entityStates={"from": ["active"], "to": ["suspended"]},
                           uiRoutes=["/admin/tenants"],
                           serverOperations=["TenantService.suspend"],
                           authorizationPolicies=["policy/tenant.suspend"],
                           auditEvents=["tenant.suspended"],
                           safeguards=["reason capture", "typed confirmation", "step-up auth"],
                           idempotency="state-conditional; repeat on suspended tenant is a no-op",
                           concurrency="row version check",
                           recovery="tenant.restore reverses within the retention window",
                           dataBinding="postgres:tenants via TenantService.suspend",
                           tests=["tests/tenant.spec.ts"], evidence=["evidence/tests.log"]),
            ],
        },
        {
            "id": "subscription", "name": "Subscription",
            "sourceOfTruth": "stripe:subscriptions mirrored to postgres:subscriptions",
            "sensitivity": "confidential", "tenantScoped": True,
            "lifecycleStates": ["trialing", "active", "cancelled"],
            "lifecycleTransitions": [
                {"from": "active", "to": "cancelled", "command": "subscription.cancel",
                 "actorRoles": ["ops"]},
                {"from": "trialing", "to": "cancelled", "command": "subscription.cancel",
                 "actorRoles": ["ops"]},
            ],
            "retention": "7 years",
            "capabilities": [
                capability(id="subscription.list", outcome="Review a tenant's plan",
                           roles=["ops"],
                           entityStates={"from": ["trialing", "active", "cancelled"], "to": []},
                           uiRoutes=["/admin/subscriptions"],
                           serverOperations=["SubscriptionRepository.findForAdmin"],
                           authorizationPolicies=["policy/billing.read"],
                           dataBinding="postgres:subscriptions via SubscriptionRepository.findForAdmin",
                           tests=["tests/subscription.spec.ts"], evidence=["evidence/tests.log"]),
                capability(id="subscription.cancel", outcome="Cancel a subscription",
                           kind="command", roles=["ops"], risk="high",
                           entityStates={"from": ["trialing", "active"], "to": ["cancelled"]},
                           uiRoutes=["/admin/subscriptions"],
                           serverOperations=["BillingService.cancel"],
                           authorizationPolicies=["policy/billing.write"],
                           auditEvents=["subscription.cancelled"],
                           safeguards=["reason capture", "impact preview", "step-up auth"],
                           idempotency="provider idempotency key per cancellation request",
                           concurrency="provider is authoritative; reconcile on mismatch",
                           recovery="resubscribe flow; provider record retained",
                           dataBinding="stripe:subscriptions via BillingService.cancel",
                           tests=["tests/subscription.spec.ts"], evidence=["evidence/tests.log"]),
            ],
        },
    ]
    m["screens"] = [
        {"id": "tenants", "route": "/admin/tenants", "purpose": "Search and act on tenants",
         "roles": ["ops"], "dataSources": ["postgres:tenants via TenantRepository.findForAdmin"],
         "capabilities": ["tenant.list", "tenant.suspend"], "actions": ["suspend"],
         "states": ["loading", "populated", "filtered-empty", "error", "forbidden",
                    "conflict", "success"],
         "responsive": True, "accessibilityStatus": "implemented", "status": "implemented",
         "rationale": "", "tests": ["tests/tenant.spec.ts"]},
        {"id": "subscriptions", "route": "/admin/subscriptions",
         "purpose": "Review and cancel subscriptions", "roles": ["ops"],
         "dataSources": ["postgres:subscriptions via SubscriptionRepository.findForAdmin"],
         "capabilities": ["subscription.list", "subscription.cancel"], "actions": ["cancel"],
         "states": ["loading", "populated", "error", "forbidden", "success"],
         "responsive": True, "accessibilityStatus": "implemented", "status": "implemented",
         "rationale": "", "tests": ["tests/subscription.spec.ts"]},
    ]
    m["integrations"] = [{
        "id": "stripe", "direction": "bidirectional", "sourceOfTruth": "Stripe for billing state",
        "credentialBoundary": "server-side secret key, never exposed to the admin client",
        "operations": ["cancel subscription", "read invoices"],
        "failureHandling": "retry with backoff; unknown results go to the reconciliation queue",
        "reconciliation": "nightly job compares local mirror against Stripe",
        "monitoring": "alert on webhook lag over 15 minutes", "status": "implemented",
        "rationale": "", "tests": ["tests/subscription.spec.ts"],
    }]
    all_true(m)
    pass_gates(m)
    m["decisions"] = [{
        "id": "dec.profile", "decision": "Run at the regulated profile",
        "reason": "Billing data and money movement are in scope",
        "evidence": ["evidence/security.log"], "status": "confirmed",
        "appliesTo": ["subscription.cancel"],
    }]
    # Not every lifecycle state is reachable by an operator, and the reachability
    # rule is right to ask. 'active' is entered by a Stripe webhook when a trial
    # converts; there is deliberately no admin command that fakes a payment.
    # Recording it in gaps[] is how a real platform answers the question -- which
    # is the point of keeping this fixture honest rather than silencing the rule.
    m["gaps"] = [{
        "id": "gap.subscription-active",
        "severity": "low",
        "description": "No admin command moves a subscription into 'active'.",
        "status": "accepted",
        "rationale": "State 'active' is set by the Stripe webhook on successful payment. "
                     "Granting operators a way to mark a subscription active would let them "
                     "grant paid access without money moving, so its absence is deliberate.",
        "evidence": ["evidence/security.log"],
    }]
    save(root, m)
    return root


def build_logistics():
    """A dispatch platform with named, deliberate defects.

    Each defect targets one rule. If a change stops any of these firing, the
    guarantee that rule represents has silently disappeared.
    """
    root = scaffold("logistics-gaps", "logistics-mobility", "standard")

    for name in ("authentication", "authorization", "audit", "safety", "data",
                 "experience", "observability"):
        write(root, "evidence/" + name + ".md", "Recorded run output.\n")
    for name in ("build", "typecheck", "lint", "tests", "browser", "security",
                 "accessibility", "performance"):
        write(root, "evidence/" + name + ".log", "command completed, exit 0\n")
    write(root, "tests/authz.spec.ts", "test('role matrix', () => {});\n")
    write(root, "tests/job.spec.ts", "test('job.list via /admin/jobs', () => {});\n")
    # Deliberately empty: proves the non-empty evidence rule still fires.
    write(root, "evidence/empty.log", "")

    m = load(root)
    m["platform"].update({
        "summary": "Dispatch and delivery operations console.",
        "operationalObjectives": ["Reassign stuck deliveries without engineering"],
        "tenancy": "single-tenant", "sourceSystems": ["PostgreSQL primary"],
        "regulatedData": [],
        # DEFECT: stack left unfilled -> stack-incomplete
        "researchSources": [],  # DEFECT: -> research-sources-missing
        "volumes": {"entityCounts": "", "peakConcurrentOperators": "", "retentionHorizon": ""},
    })
    m["roles"] = [
        {"id": "dispatcher", "name": "Dispatcher", "responsibilities": ["Assign jobs"],
         "scopes": ["all regions"], "mfaRequired": True, "separationOfDuties": []},
        # DEFECT: declared but never used -> role-unused
        {"id": "auditor", "name": "Auditor", "responsibilities": ["Review"],
         "scopes": ["all"], "mfaRequired": True, "separationOfDuties": []},
    ]
    m["entities"] = [{
        "id": "job", "name": "Delivery job",
        "sourceOfTruth": "postgres:jobs",
        "sensitivity": "internal", "tenantScoped": False,
        # DEFECT: 'cancelled' is reachable by no command -> lifecycle-reachable
        "lifecycleStates": ["queued", "dispatched", "delivered", "cancelled"],
        "lifecycleTransitions": [
            {"from": "queued", "to": "dispatched", "command": "job.dispatch",
             "actorRoles": ["dispatcher"]},
        ],
        "retention": "3 years",
        "capabilities": [
            capability(id="job.list", outcome="Find a delivery job", roles=["dispatcher"],
                       entityStates={"from": ["queued", "dispatched", "delivered"], "to": []},
                       uiRoutes=["/admin/jobs"],
                       serverOperations=["JobRepository.findForAdmin"],
                       authorizationPolicies=["policy/job.read"],
                       dataBinding="postgres:jobs via JobRepository.findForAdmin",
                       tests=["tests/job.spec.ts"], evidence=["evidence/tests.log"]),
            capability(id="job.dispatch", outcome="Dispatch a queued job", kind="command",
                       roles=["dispatcher"], risk="high",
                       entityStates={"from": ["queued"], "to": ["dispatched"]},
                       uiRoutes=["/admin/jobs"],
                       serverOperations=["DispatchService.dispatch"],
                       authorizationPolicies=["policy/job.dispatch"],
                       auditEvents=["job.dispatched"],
                       # DEFECT: high risk with no safeguards -> high-risk-controls
                       safeguards=[],
                       dataBinding="postgres:jobs via DispatchService.dispatch",
                       tests=["tests/job.spec.ts"],
                       # DEFECT: empty evidence file -> evidence-file-empty
                       evidence=["evidence/empty.log"]),
        ],
    }]
    m["screens"] = [{
        "id": "jobs", "route": "/admin/jobs", "purpose": "Dispatch board",
        "roles": ["dispatcher"], "dataSources": ["postgres:jobs via JobRepository.findForAdmin"],
        "capabilities": ["job.list", "job.dispatch"], "actions": ["dispatch"],
        # DEFECT: has actions but no success state -> screen-state-missing
        "states": ["loading", "populated", "error", "forbidden"],
        "responsive": True, "accessibilityStatus": "implemented", "status": "implemented",
        "rationale": "", "tests": ["tests/job.spec.ts"],
    }]
    all_true(m)
    pass_gates(m)
    save(root, m)
    return root


def main():
    FIXTURES.mkdir(exist_ok=True)
    for builder in (build_saas, build_logistics):
        root = builder()
        print("built " + root.name)
    print("\nNow run: python evals/run.py --update")


if __name__ == "__main__":
    raise SystemExit(main())
