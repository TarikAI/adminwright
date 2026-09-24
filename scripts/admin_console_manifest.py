#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Model, validate, and audit admin-console capability manifests (manifest v2).

Standard library only. Runs on Windows and POSIX.

Commands: init, migrate, validate, report, coverage, emit, add, set, remove, rename,
claim, release-claim, harvest, promote, store, lesson.

Exit codes: 0 ok, 1 findings at error severity, 2 usage or IO failure,
3 capability claim conflict.

`add` and `set` refuse a write that would introduce new errors, and exit 2 for
it. That is deliberate: the manifest was not modified, so the caller's request
could not be carried out -- the same class of outcome as a bad path or malformed
JSON. Exit 1 is reserved for "the manifest was read and it has findings", which
is what a caller polling validate needs to distinguish. Pass --allow-invalid to
write anyway; it reports everything it waved through.

`add` accepts one JSON object or an array of them. An array is atomic: every
entry is validated before anything is appended, so one bad id or duplicate
refuses the whole batch and the manifest is left untouched -- a caller that
persists several objects in one call can never end up half-written.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = SKILL_ROOT / "assets" / "admin-console.manifest.template.json"
SCHEMA_PATH = SKILL_ROOT / "assets" / "admin-console.manifest.schema.json"

MANIFEST_VERSION = "2.0"
LEGACY_VERSIONS = ("1.0",)

PROFILES = ("internal", "standard", "regulated")
DEFAULT_PROFILE = "standard"

STATUSES = (
    "discovered",
    "planned",
    "in-progress",
    "implemented",
    "blocked",
    "deferred",
    "not-applicable",
)
RESOLVED_STATUSES = ("implemented", "not-applicable")
EXPLAINED_STATUSES = ("blocked", "deferred", "not-applicable")
GATE_STATUSES = ("pending", "passed", "failed", "not-applicable")
CAPABILITY_KINDS = ("query", "command", "job", "export", "subscription")
MUTATING_KINDS = ("command", "job", "export")
RISKS = ("low", "moderate", "high", "critical")
SENSITIVITIES = ("public", "internal", "confidential", "restricted")
TENANCIES = ("single-tenant", "multi-tenant", "hybrid", "unknown")
DIRECTIONS = ("inbound", "outbound", "bidirectional")
DECISION_STATUSES = ("assumed", "confirmed", "superseded")
GAP_SEVERITIES = ("critical", "high", "medium", "low")
GAP_STATUSES = ("open", "fixed", "accepted", "blocked")
FEEDBACK_CATEGORIES = ("gap", "friction", "incorrect-guidance", "new-pattern", "tooling")
FEEDBACK_STATUSES = ("open", "promoted", "rejected")
# The pass order: security reviews before qa closes the release gate.
AGENT_ROLES = ("architect", "implementer", "ux-reviewer", "security", "qa")
AGENT_STATUSES = ("active", "done", "handed-off")
REVIEW_STATUSES = ("unreviewed", "reviewed", "contested")
LESSON_STATUSES = ("proposed", "adopted", "rejected", "superseded")

REQUIRED_GATES = (
    "build",
    "typecheck",
    "lint",
    "tests",
    "browser",
    "accessibility",
    "security",
    "performance",
)
NONFUNCTIONAL_GATES = ("accessibility", "performance")
REQUIRED_SCREEN_STATES = ("loading", "populated", "error", "forbidden")
CROSS_SECTIONS = (
    "authentication",
    "authorization",
    "audit",
    "safety",
    "data",
    "experience",
    "observability",
)

TOP_LEVEL_KEYS = (
    "manifestVersion",
    "profile",
    "platform",
    "roles",
    "entities",
    "workQueues",
    "screens",
    "integrations",
    "crossCutting",
    "qualityGates",
    "decisions",
    "gaps",
    "declaredStatic",
    "feedback",
    "agents",
)
COLLECTION_KEYS = (
    "roles",
    "entities",
    "workQueues",
    "screens",
    "integrations",
    "qualityGates",
    "decisions",
    "gaps",
    "declaredStatic",
    "feedback",
    "agents",
)

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
# The one file or area of the skill a feedback entry (or a lesson) would change.
# Clustering buckets observations by it, so an entry without one lands in
# "unassigned" and can never group with anything. This is only the SHAPE -- a
# relative path with '/' and no '.', '..' or hidden segment; skill_scope_problem
# also requires it to exist in the skill.
FEEDBACK_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
_PLACEHOLDER_WORDS = (
    r"mock(?:s|ed|ing)?|fake(?:s|d)?|stub(?:s|bed|bing)?|dumm(?:y|ies)"
    r"|placeholder(?:s)?|lorem|demo-only|todo(?:s)?|tbd|fixme|coming-soon|wip|xxx+"
    r"|not[\s_-]?implemented"
)
# Manifest fields are scanned for every word above plus 'sample' and 'random'.
# Evidence FILES are scanned without those two: "1200 samples" and "random seed"
# are ordinary words in a real run log, and dropping them costs nothing in a
# field that names an implementation.
PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:" + _PLACEHOLDER_WORDS + r"|sample(?:s)?|random(?:ly)?)\b", re.I
)
FILE_PLACEHOLDER_PATTERN = re.compile(r"\b(?:" + _PLACEHOLDER_WORDS + r")\b", re.I)
# Framework idioms that contain a banned word and mean nothing by it.
FRAMEWORK_CALLS = re.compile(
    r"jest\.mock\(|vi\.mock\(|sinon\.stub\(|unittest\.mock|getByPlaceholderText"
    r"|NotImplementedError|\bplaceholder\s*=",
    re.I,
)
HARDCODE_PATTERN = re.compile(r"\bhard[- ]?cod(?:e|ed|ing|es)\b", re.I)

# Cyrillic and Greek letters that render identically to Latin ones. Without
# folding these, 'm<Cyrillic o>ckRepository' reads as a normal identifier to the
# scanner while reading as "mock" to every human reviewing the diff.
CONFUSABLES = str.maketrans(
    {
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
        "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0458": "j", "\u04bb": "h",
        "\u043a": "k", "\u0455": "s", "\u0501": "d", "\u051b": "q", "\u051d": "w",
        "\u0261": "g", "\u0131": "i", "\u03bd": "v", "\u03c1": "p",
        "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
        "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
        "\u03a1": "P", "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X", "\u03bf": "o",
        "\u03b1": "a",
        "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
        "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
        "\u0425": "X", "\u0406": "I", "\u0405": "S", "\uff4f": "o", "\uff41": "a",
    }
)

# Tokens dangerous enough that a substring match is worth the occasional false
# positive. Word boundaries miss 'gmockRepositoryImpl' and 'usemockdata' because
# there is no case transition to split on, and those are exactly the names that
# get written when someone is wiring a temporary shim.
# A false positive is renamed or registered in declaredStatic[] in seconds; a
# false negative ships mock data to production.
SUBSTRING_TOKENS = (
    "mock", "fake", "stub", "dummy", r"place[-_]?holder", "lorem",
    "todo", "fixme", "tbd", r"not[-_]?implemented", r"coming[-_]?soon",
)
SUBSTRING_PATTERN = re.compile("|".join(SUBSTRING_TOKENS), re.I)

# Ordinary English words that happen to contain a banned token. Stripped before
# the substring pass so 'stubbornRetry' and 'hammock' stay legal identifiers.
# Keep this list short: every entry is a hole someone could name a shim after.
SUBSTRING_EXCEPTIONS = re.compile(
    r"stubborn|stubble|stubby|hammock|smock|mockingbird|mastodon|autodoc|photodoc|fakir", re.I
)


def substring_hit(token):
    """Search ONE whitespace-delimited token, never text joined across spaces.

    Squashing the spaces out of a sentence before matching made ordinary prose a
    placeholder error at every profile: "Rotation of a key" contains `fake`,
    "nothing left to do" contains `todo`, "scenarios that BDD covers" contains
    `tbd`. Words are judged one at a time; within a token a hyphen or underscore
    may sit inside a compound ('not_implemented', 'coming-soon').
    """
    return SUBSTRING_PATTERN.search(SUBSTRING_EXCEPTIONS.sub("", token))


def fold_confusables(text):
    """Reduce a string to what a human reads, so 'mo<ZWSP>ck' cannot hide a mock.

    Format characters (zero-width space and joiner, BOM, soft hyphen, bidi marks)
    are invisible; NFKD folds fullwidth and mathematical letters to ASCII and
    splits accents off; the table then maps the Cyrillic and Greek look-alikes.
    """
    kept = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    decomposed = unicodedata.normalize("NFKD", kept)
    plain = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return plain.translate(CONFUSABLES)


URL_PREFIXES = ("http://", "https://")
NOT_REQUIRED_PATTERN = re.compile(r"^not-required:\s*\S+", re.I)

LOCK_STALE_SECONDS = 120.0
LOCK_TIMEOUT_SECONDS = 30.0
EVIDENCE_READ_LIMIT = 512 * 1024

OFF, WARN, ERROR = "off", "warn", "error"
E3 = (ERROR, ERROR, ERROR)
W3 = (WARN, WARN, WARN)
QUALITY = (WARN, ERROR, ERROR)
LATE = (WARN, WARN, ERROR)
STRICT_ONLY = (OFF, WARN, ERROR)
TIERED = (OFF, ERROR, ERROR)

# rule id -> severity by profile (internal, standard, regulated)
RULES = {
    # structural facts: wrong at every tier
    "manifest-version": E3,
    "manifest-key-missing": E3,
    "manifest-type": E3,
    "profile-invalid": E3,
    "id-invalid": E3,
    "id-duplicate": E3,
    "enum-invalid": E3,
    "required-field-empty": E3,
    "unknown-role-reference": E3,
    "unknown-capability-reference": E3,
    "capability-route-unknown": E3,
    "capability-screen-link": E3,
    "capability-no-role": E3,
    "transition-command-unknown": E3,
    "rationale-required": E3,
    "release-status-unresolved": E3,
    "screen-state-missing": E3,
    "screen-incomplete": E3,
    "screen-responsive": E3,
    "screen-accessibility": E3,
    "gate-missing": E3,
    "gate-not-passed": E3,
    "gate-evidence-missing": E3,
    "gate-rationale-missing": E3,
    "gap-unresolved": E3,
    "feedback-scope": W3,
    "unknown-field": QUALITY,
    "gap-rationale-missing": E3,
    "placeholder-scan": E3,
    "unregistered-static": E3,
    "declared-static-incomplete": E3,
    "evidence-path-missing": E3,
    "integration-controls": E3,
    "high-risk-controls": E3,
    "idempotency-missing": E3,
    "audit-events-missing": E3,
    "crosscutting-section-missing": E3,
    "crosscutting-flag": E3,
    "decision-reason-missing": E3,
    "decision-applies-to-unknown": E3,
    # quality tier: honest internal teams may carry these as warnings
    "crosscutting-evidence": QUALITY,
    "authorization-policy-tests": QUALITY,
    "gate-evidence-path": QUALITY,
    "evidence-file-empty": QUALITY,
    "evidence-not-resolvable": QUALITY,
    "test-asserts-nothing": QUALITY,
    "evidence-uninformative": QUALITY,
    "lifecycle-observable": QUALITY,
    "lifecycle-reachable": QUALITY,
    "role-unused": QUALITY,
    "entity-query-missing": QUALITY,
    "screen-no-capability": QUALITY,
    "stack-incomplete": QUALITY,
    "research-sources-missing": QUALITY,
    # late tier: enforced only where the regulator cares
    "manual-evidence": LATE,
    "separation-of-duties": LATE,
    "capability-unreviewed": LATE,
    # strict tier
    "evidence-token-match": STRICT_ONLY,
    "decision-assumed": STRICT_ONLY,
    "nonfunctional-gate-required": TIERED,
    "privileged-read-audit": TIERED,
    # advisory
    "plan-note": W3,
    "declared-static-unused": W3,
    "research-source-incomplete": QUALITY,
    "reviewer-identity": LATE,
    "archetype-coverage": W3,
    # evidence integrity: what a gate is allowed to point at
    "declared-static-forbidden-path": E3,
    "declared-static-self-approved": STRICT_ONLY,
    "evidence-inside-manifest-dir": E3,
    "evidence-is-test-file": QUALITY,
    "evidence-shared": STRICT_ONLY,
    "gate-evidence-unrelated": STRICT_ONLY,
    "test-file-shape": QUALITY,
    "test-file-shared": STRICT_ONLY,
    "implementer-unrecorded": LATE,
    "not-applicable-undecided": STRICT_ONLY,
    "contested-needs-gap": LATE,
    # learning loop (round 2)
    "platform-id-missing": W3,
}

# Rules that describe an UNFINISHED model rather than a malformed one.
#
# A half-built manifest legitimately has unused roles, entities without queries,
# and lifecycle states nothing reaches yet — that is what "in progress" looks
# like. Firing these as errors before release makes incremental modeling
# impossible: a role is unused until its first capability exists, and a
# capability cannot reference a role that has not been added yet.
#
# They stay errors at release, where an unfinished model IS the defect.
COMPLETENESS_RULES = frozenset(
    {
        "role-unused",
        "entity-query-missing",
        "screen-no-capability",
        "capability-no-role",
        "capability-screen-link",
        "capability-route-unknown",
        "screen-incomplete",
        "lifecycle-observable",
        "lifecycle-reachable",
        "transition-command-unknown",
        "integration-controls",
        "high-risk-controls",
        "idempotency-missing",
        "audit-events-missing",
        "stack-incomplete",
        "research-sources-missing",
        "separation-of-duties",
        "capability-unreviewed",
        "implementer-unrecorded",
        "not-applicable-undecided",
        "contested-needs-gap",
    }
)

def ruleset_version():
    """Short hash of the rule table, so a transcript says which rules judged it.

    An auto-updated install can change what `validate` accepts between two runs.
    This is not a version number anyone maintains: it changes exactly when a
    severity, a rule, or the completeness set changes.
    """
    payload = json.dumps(
        {
            "rules": {name: list(tiers) for name, tiers in sorted(RULES.items())},
            "completeness": sorted(COMPLETENESS_RULES),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


_SKILL_VERSION = []


def skill_version():
    """The installed commit: HEAD in a checkout of the skill, else the plugin cache's sha dir.

    Only a checkout whose work-tree top IS the skill root reports HEAD. A copy
    vendored into a project repository (<project>/.claude/skills/adminwright) or a
    plugin cache under a git-tracked home sits inside SOMEONE ELSE'S repository,
    whose HEAD says nothing about the skill and changes with every project commit.
    """
    if _SKILL_VERSION:
        return _SKILL_VERSION[0]
    version = "unknown"
    if skill_is_checkout():
        done = skill_git("rev-parse", "--short", "HEAD")
        if done is not None and done.returncode == 0 and done.stdout.strip():
            version = done.stdout.strip()
    if version == "unknown" and re.fullmatch(r"[0-9a-f]{7,40}", SKILL_ROOT.name):
        version = SKILL_ROOT.name[:12]
    _SKILL_VERSION.append(version)
    return version


# Expected operational domains per archetype, as lowercase keyword sets.
# Used only to ask "did this build actually engage the domain it claims?" — a
# manifest tagged logistics-mobility that models nothing but generic orders is
# the "collapses into the same console" failure the catalog exists to prevent.
# Advisory by design: the catalog is a hypothesis generator, and a real platform
# may legitimately skip a domain. It must say so, not omit it silently.
ARCHETYPE_DOMAINS = {
    "b2b-saas": ("tenant", "organization", "workspace", "subscription", "plan", "entitlement",
                 "invoice", "usage", "quota", "seat", "invitation", "sso", "api key"),
    "commerce": ("order", "catalog", "product", "inventory", "fulfilment", "fulfillment",
                 "shipment", "refund", "return", "payment", "cart", "promotion", "tax"),
    "marketplace": ("seller", "provider", "listing", "payout", "commission", "dispute",
                    "verification", "onboarding", "moderation", "escrow", "reserve"),
    "fintech": ("ledger", "transaction", "settlement", "reversal", "kyc", "kyb", "sanction",
                "reconciliation", "hold", "limit", "dispute", "chargeback"),
    "content-media": ("content", "publish", "draft", "revision", "moderation", "report",
                      "appeal", "taxonomy", "media", "schedule", "takedown"),
    "logistics-mobility": ("dispatch", "route", "driver", "vehicle", "asset", "delivery",
                           "pickup", "tracking", "eta", "proof", "incident", "reassign",
                           "service area", "job"),
    "healthcare": ("patient", "consent", "eligibility", "encounter", "provider", "disclosure",
                   "legal hold", "break-glass", "record"),
    "education": ("course", "enrollment", "cohort", "instructor", "learner", "grade",
                  "assessment", "attendance", "certificate", "term"),
    "infrastructure": ("project", "environment", "resource", "deployment", "credential",
                       "token", "quota", "pipeline", "incident", "rollback", "region"),
    "ai-platform": ("model", "prompt", "evaluation", "dataset", "deployment", "inference",
                    "feedback", "safety", "cost", "provider", "version"),
    # Field-tested: platforms whose product IS managing LLM and third-party APIs
    # kept receiving consoles with no way to add a key, a base URL, or a user.
    # The domains here are the controls that were missing every time.
    "llm-gateway": ("provider", "credential", "api key", "base url", "model", "routing",
                    "fallback", "rate limit", "quota", "usage", "cost", "rotation",
                    "invitation", "user", "spend cap", "budget", "request log", "payload",
                    "prompt", "pii", "redact", "retention", "ssrf", "allowlist",
                    "health check", "revoke"),
    "agent-operated": ("agent", "tool", "scope", "approval", "provenance", "kill switch",
                       "pause", "replay", "spend", "cap"),
}

# Common spellings that should resolve to the keys above.
ARCHETYPE_ALIASES = {
    "saas": "b2b-saas", "b2b": "b2b-saas", "multi-tenant": "b2b-saas",
    "b2b-saas-multi-tenant": "b2b-saas",
    "ecommerce": "commerce", "e-commerce": "commerce", "retail": "commerce",
    "gig": "marketplace", "two-sided": "marketplace",
    "financial-services": "fintech", "banking": "fintech", "payments": "fintech",
    "content": "content-media", "media": "content-media", "community": "content-media",
    "logistics": "logistics-mobility", "mobility": "logistics-mobility",
    "field-operations": "logistics-mobility", "field-ops": "logistics-mobility",
    "health": "healthcare", "regulated-care": "healthcare",
    "learning": "education", "edtech": "education",
    "developer-platform": "infrastructure", "iot": "infrastructure",
    "data-platform": "infrastructure", "devtools": "infrastructure",
    "ai": "ai-platform", "ml": "ai-platform", "llm": "ai-platform",
    "model-gateway": "llm-gateway", "ai-gateway": "llm-gateway",
    "inference-gateway": "llm-gateway", "llm-api": "llm-gateway",
    "llm-apis": "llm-gateway", "api-management": "llm-gateway",
    "llm-ops": "llm-gateway", "llmops": "llm-gateway",
    "model-router": "llm-gateway", "llm-proxy": "llm-gateway",
    "agentic": "agent-operated", "autonomous-agents": "agent-operated",
    # Field-tested: a trading platform typed --archetype financial and the
    # coverage check silently never ran. Common money words must resolve.
    "financial": "fintech", "finance": "fintech", "trading": "fintech",
    "crypto": "fintech", "investing": "fintech",
}


def resolve_archetype(raw):
    key = text_of(raw).strip().lower().replace("_", "-").replace(" ", "-")
    if key in ARCHETYPE_DOMAINS:
        return key
    if key in ARCHETYPE_ALIASES:
        return ARCHETYPE_ALIASES[key]
    for known in ARCHETYPE_DOMAINS:
        if known in key or key in known:
            return known
    return None


def manifest_vocabulary(manifest):
    """Everything the build actually named, lowercased."""
    words = []
    for _eid, entity in iter_entities(manifest):
        words.append(text_of(entity.get("id")))
        words.append(text_of(entity.get("name")))
        for state in as_list(entity.get("lifecycleStates")):
            words.append(text_of(state))
    for _path, _entity, capability in iter_capabilities(manifest):
        words.append(text_of(capability.get("id")))
        words.append(text_of(capability.get("outcome")))
    for _path, queue in iter_named(manifest, "workQueues"):
        words.append(text_of(queue.get("id")))
        words.append(text_of(queue.get("purpose")))
    for _path, screen in iter_named(manifest, "screens"):
        words.append(text_of(screen.get("id")))
        words.append(text_of(screen.get("purpose")))
    return " ".join(words).lower()


def check_archetype_coverage(manifest, findings):
    platform = as_dict(manifest.get("platform"))
    entities = list(iter_entities(manifest))
    capabilities = list(iter_capabilities(manifest))
    # Too small to judge. A three-capability internal tool legitimately engages
    # almost none of any archetype, and warning about it is noise that trains
    # operators to ignore the rule that matters on a real build.
    if len(entities) < 3 and len(capabilities) < 8:
        return
    vocabulary = manifest_vocabulary(manifest)
    excused = collect_escape_text(manifest)
    for raw in as_list(platform.get("archetypes")):
        key = resolve_archetype(raw)
        if not key:
            continue
        missing = [
            domain
            for domain in ARCHETYPE_DOMAINS[key]
            if domain not in vocabulary and domain not in excused
        ]
        # Most archetypes list 10-14 domains and no real platform needs all of
        # them. Only flag when the build engaged almost none of the archetype —
        # the signal for "tagged logistics, built generic CRUD".
        if len(missing) > len(ARCHETYPE_DOMAINS[key]) - 2:
            findings.add(
                "archetype-coverage",
                "platform.archetypes",
                "archetype '" + str(raw) + "' is declared but the model engages almost none "
                "of its expected domains (" + ", ".join(missing[:6]) + "); either model them "
                "or record why they do not apply in gaps[]",
            )


SURFACES = ("ui", "api", "cli", "job", "chat")


def surface_of(capability):
    """Where operators reach a capability. Absent or empty means the admin UI."""
    values = [v for v in as_list(capability.get("surface")) if isinstance(v, str) and v]
    return values or ["ui"]


SCANNED_CAPABILITY_FIELDS = (
    "dataBinding",
    "serverOperations",
    "authorizationPolicies",
    "auditEvents",
    "safeguards",
    "tests",
    "evidence",
)


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


class ManifestError(Exception):
    """Usage or IO failure. Maps to exit code 2."""


class LockError(ManifestError):
    pass


class Findings:
    def __init__(self, profile, phase="release"):
        if profile not in PROFILES:
            raise ManifestError("profile must be one of: " + ", ".join(PROFILES))
        self.profile = profile
        self.phase = phase
        self._index = PROFILES.index(profile)
        self.items = []
        self._seen = set()
        # Every resolved evidence file, tagged with what cited it, so the
        # sharing rules can ask "is one README standing in for every gate?"
        self.evidence_index = []
        self.manifest_dir = None
        self.static_allowed = frozenset()

    def severity_for(self, rule):
        severity = RULES.get(rule, E3)[self._index]
        # Before release, an incomplete model is expected, not broken. Report the
        # gap without blocking the edit that is on its way to closing it.
        if severity == ERROR and self.phase != "release" and rule in COMPLETENESS_RULES:
            return WARN
        return severity

    def add(self, rule, path, message):
        severity = self.severity_for(rule)
        if severity == OFF:
            return
        key = (rule, path, message)
        # The schema walker and the field checks can legitimately notice the
        # same wrong type; say it once.
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append(
            {"rule": rule, "severity": severity, "path": path, "message": message}
        )

    def extend(self, other):
        self.items.extend(other.items)

    @property
    def errors(self):
        return [item for item in self.items if item["severity"] == ERROR]

    @property
    def warnings(self):
        return [item for item in self.items if item["severity"] == WARN]

    def sorted_items(self):
        order = {ERROR: 0, WARN: 1}
        return sorted(
            self.items,
            key=lambda item: (order.get(item["severity"], 2), item["path"], item["rule"]),
        )


# --------------------------------------------------------------------------
# io helpers
# --------------------------------------------------------------------------


def decode_text_bytes(data, source, allow_utf16=False):
    """Decode a JSON file the way Windows tools actually write it.

    PowerShell 5.1's `Set-Content -Encoding utf8` and most editors prefix a UTF-8
    byte-order mark, which strict `utf-8` decoding rejects as "Unexpected UTF-8
    BOM"; `Out-File` and `>` can produce UTF-16. A BOM is accepted silently here
    and never written back (write_text_file emits none).
    """
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8")
    if allow_utf16 and data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # A UTF-16 file saved by a Windows editor is a realistic accident, not an
        # exotic attack. Fail as a usage error, not a traceback.
        raise ManifestError(
            str(source) + " is not valid UTF-8 (a UTF-16 or legacy code-page file?). "
            "Re-save it as UTF-8; a byte-order mark is fine."
        )


def load_json(path):
    try:
        raw = decode_text_bytes(path.read_bytes(), path)
    except FileNotFoundError:
        raise ManifestError("File not found: " + str(path))
    except OSError as exc:
        raise ManifestError("Cannot read " + str(path) + ": " + str(exc))
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError("Invalid JSON in " + str(path) + ": " + str(exc))
    except RecursionError:
        raise ManifestError(
            "JSON in " + str(path) + " is nested too deeply to parse safely."
        )
    if not isinstance(value, dict):
        raise ManifestError("Expected a JSON object in " + str(path))
    return value


def write_text_file(path, text):
    """Atomic write: a full temp file, then one rename over the target.

    The temp name carries the pid so two writers can never share it, and the
    rename is retried because Windows refuses os.replace with Access Denied
    while any other process has the destination open — an unlocked reader
    (another agent's validate, or an editor) is enough, and the write lock
    cannot prevent it. Field-tested: eight concurrent records against one
    manifest lost one to WinError 5.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    try:
        with open(str(tmp), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            # The rename below is atomic, but not durable: without an fsync a crash
            # can leave the renamed file empty.
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        for attempt in range(10):
            try:
                os.replace(str(tmp), str(path))
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_json(path, data):
    write_text_file(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def commit_manifest(path, manifest):
    """Write a manifest, stamping which rule set and skill commit last touched it.

    An auto-updated install can change what `validate` accepts between two runs;
    the stamp lets anyone reading the manifest tell which rules its last edit
    was checked against.
    """
    manifest["validatedWith"] = {"ruleset": ruleset_version(), "skill": skill_version()}
    write_json(path, manifest)


def nonempty(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def as_list(value):
    return value if isinstance(value, list) else []


def as_dict(value):
    return value if isinstance(value, dict) else {}


def text_of(value):
    return value.strip() if isinstance(value, str) else ""


def label(value, default="unknown"):
    """A hashable, printable stand-in for a value that may have the wrong type."""
    if value is None:
        return default
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


def str_items(value):
    """The string members of a list; anything else is skipped rather than crashed on."""
    return [item for item in as_list(value) if isinstance(item, str)]


def stderr(message):
    print(message, file=sys.stderr)


def pid_alive(pid):
    """Best-effort: is this process still running on this machine?"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                # 5 = access denied: the process exists but is not ours.
                return ctypes.windll.kernel32.GetLastError() == 5
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class FileLock:
    """Exclusive lock on a single path. Breaks locks older than the stale window.

    The lock file records the holder's pid and a random token. Two things depend
    on that. A stale lock is taken over by atomically RENAMING it away (only one
    contender's rename can succeed), and it is released only if the file still
    carries our token -- with a bare unlink, two waiters that both judged the lock
    stale each "took" it, and the first to finish deleted the second's live lock.
    And when a caller does time out, the message names the holder, the lock's
    age, and what to do about it, instead of "held by another process".
    """

    def __init__(self, path, stale=LOCK_STALE_SECONDS, timeout=LOCK_TIMEOUT_SECONDS):
        self.path = Path(path)
        self.stale = stale
        self.timeout = timeout
        self.held = False
        self.token = "%d-%s" % (os.getpid(), os.urandom(6).hex())

    def read_holder(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return None, ""
        pid = 0
        token = ""
        for line in text.splitlines():
            if line.startswith("pid="):
                try:
                    pid = int(line[4:].strip())
                except ValueError:
                    pid = 0
            elif line.startswith("token="):
                token = line[6:].strip()
        return pid, token

    def describe_holder(self):
        pid, _token = self.read_holder()
        try:
            age = int(time.time() - self.path.stat().st_mtime)
        except OSError:
            age = None
        text = "held by pid " + str(pid) if pid else "held by an unknown process"
        if age is not None:
            text += " for " + str(age) + "s"
        return text

    def take_over_stale(self):
        """Remove a dead or stale lock; True if this process did it.

        A bare rename-and-delete is not safe: two waiters both judge the lock stale,
        the first removes it and takes a fresh one, and the second then removes THAT
        one. So removal happens under a short-lived guard file only one process can
        create, and the staleness is re-checked under it.
        """
        guard = self.path.with_name(self.path.name + ".takeover")
        try:
            handle = os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError:
            try:
                if time.time() - guard.stat().st_mtime > 10.0:
                    guard.unlink()  # a guard left by a crash between create and unlink
            except OSError:
                pass
            return False
        try:
            os.close(handle)
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                return True  # already gone
            holder_pid, _token = self.read_holder()
            dead = age > 2.0 and holder_pid and not pid_alive(holder_pid)
            if age > self.stale or dead:
                try:
                    self.path.unlink()
                except OSError:
                    return False
                return True
            return False
        finally:
            try:
                guard.unlink()
            except OSError:
                pass

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                handle = os.open(
                    str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
            except FileExistsError:
                age = None
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = None
                holder_pid, _token = self.read_holder()
                dead = (
                    age is not None and age > 2.0 and holder_pid and not pid_alive(holder_pid)
                )
                if age is not None and (age > self.stale or dead):
                    self.take_over_stale()
                    continue
                if time.time() >= deadline:
                    raise LockError(
                        "Manifest lock " + self.path.name + " is " + self.describe_holder()
                        + ". If that process is gone, delete " + str(self.path) + " and retry."
                    )
                time.sleep(0.2)
                continue
            except PermissionError as exc:
                # Windows raises Access Denied, not FileExistsError, while the
                # previous holder's unlink is still pending — a contention
                # state, not a permission problem, so retry to the deadline
                # like any other held lock. Field-tested: eight concurrent
                # records, one killed outright by [Errno 13] on the lock.
                if time.time() >= deadline:
                    raise LockError(
                        "Cannot create lock " + str(self.path) + " after "
                        + str(self.timeout) + "s: " + str(exc)
                    )
                time.sleep(0.05)
                continue
            except OSError as exc:
                raise LockError("Cannot create lock " + str(self.path) + ": " + str(exc))
            os.write(
                handle, ("pid=" + str(os.getpid()) + "\ntoken=" + self.token + "\n").encode("utf-8")
            )
            os.close(handle)
            self.held = True
            return self

    def __exit__(self, exc_type, exc, tb):
        if self.held:
            _pid, token = self.read_holder()
            # Only remove a lock that is still ours: if it was taken over while we
            # ran, the file now belongs to someone else.
            if token == self.token or (_pid is None):
                try:
                    self.path.unlink()
                except OSError:
                    pass
            self.held = False
        return False


# --------------------------------------------------------------------------
# manifest traversal
# --------------------------------------------------------------------------


def entity_key(entity, index):
    value = text_of(entity.get("id"))
    return value if value else "#" + str(index)


def iter_entities(manifest):
    for index, entity in enumerate(as_list(manifest.get("entities"))):
        if isinstance(entity, dict):
            yield entity_key(entity, index), entity


def iter_capabilities(manifest):
    """Yield (path, entity, capability) for every modeled capability."""
    for eid, entity in iter_entities(manifest):
        for index, capability in enumerate(as_list(entity.get("capabilities"))):
            if not isinstance(capability, dict):
                continue
            cid = text_of(capability.get("id")) or "#" + str(index)
            yield "entities[" + eid + "].capabilities[" + cid + "]", entity, capability


def iter_named(manifest, key):
    for index, item in enumerate(as_list(manifest.get(key))):
        if isinstance(item, dict):
            iid = text_of(item.get("id")) or "#" + str(index)
            yield key + "[" + iid + "]", item


def capability_index(manifest):
    index = {}
    for path, entity, capability in iter_capabilities(manifest):
        cid = text_of(capability.get("id"))
        if cid:
            index[cid] = (path, entity, capability)
    return index


def active_profile(manifest, override):
    if override:
        if override not in PROFILES:
            raise ManifestError("--profile must be one of: " + ", ".join(PROFILES))
        return override, "override"
    declared = text_of(manifest.get("profile"))
    if not declared:
        return DEFAULT_PROFILE, "default"
    if declared not in PROFILES:
        return DEFAULT_PROFILE, "invalid value '" + declared + "', using default"
    return declared, "manifest"


# --------------------------------------------------------------------------
# evidence resolution
# --------------------------------------------------------------------------


def evidence_kind(raw):
    text = str(raw).strip()
    lowered = text.lower()
    if lowered.startswith(URL_PREFIXES):
        return "url"
    if lowered.startswith("manual:"):
        return "manual"
    if lowered.startswith("command:"):
        return "command"
    return "path"


def evidence_path(raw, project_root):
    file_part = str(raw).split("#", 1)[0].strip().replace("\\", "/")
    if not file_part:
        return None
    candidate = Path(file_part)
    if not candidate.is_absolute():
        if project_root is None:
            return None
        candidate = project_root / candidate
    return candidate


def escapes_root(candidate, project_root):
    """True when evidence resolves outside the project.

    '../../elsewhere/real.txt' resolves to a real, non-empty file and would
    otherwise satisfy the gate while pointing at something no reviewer of this
    repository can see.
    """
    if project_root is None:
        return False
    try:
        resolved = candidate.resolve()
        root = project_root.resolve()
    except OSError:
        return False
    return root != resolved and root not in resolved.parents


def inside_manifest_dir(candidate, manifest_dir):
    """True when evidence lives in the manifest's own working directory.

    `.admin-console/manifest.json` is real, non-empty JSON. Citing it as a test
    or as gate evidence proves nothing but that the manifest exists, and the
    manifest is exactly what the evidence is supposed to be checked against.
    """
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    if ".admin-console" in resolved.parts:
        return True
    if manifest_dir is not None:
        try:
            base = Path(manifest_dir).resolve()
        except OSError:
            return False
        return resolved == base or base in resolved.parents
    return False


def path_state(candidate):
    """Return 'missing', 'directory', 'empty', or 'ok'.

    A directory is never evidence: any folder holding one unrelated file used to
    satisfy the gate. Whitespace-only files have nonzero size, so byte count
    alone is not an emptiness test. Content is judged separately, by role:
    a test file is checked for assertions, an evidence file for placeholder text.
    """
    try:
        if candidate.is_dir():
            return "directory"
        if not candidate.exists():
            return "missing"
        if candidate.stat().st_size == 0:
            return "empty"
        if not read_head(candidate).strip():
            return "empty"
        return "ok"
    except OSError:
        return "missing"


def read_head(candidate):
    try:
        if candidate.is_dir():
            chunks = []
            for child in sorted(candidate.rglob("*")):
                if child.is_file():
                    chunks.append(read_head(child))
                if sum(len(c) for c in chunks) > EVIDENCE_READ_LIMIT:
                    break
            return "\n".join(chunks)
        with open(str(candidate), "rb") as handle:
            data = handle.read(EVIDENCE_READ_LIMIT)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


# An assertion in any of the languages this skill targets. A test file listed
# as proof of a capability must actually claim something about it: `test('...',
# () => {})` compiles, runs, passes, and proves nothing.
#
# Every alternative is anchored: an unanchored `assert` matched
# `import { assert } from 'chai'`, a comment reading "add assertions later", and
# `jwt.verify(...)`, so a file with no test in it satisfied the check.
ASSERTION_PATTERN = re.compile(
    r"\bassert\w*\s*[(!]"                       # assert(x), assertEqual(..), assert_eq!(..)
    r"|\bassert\.\w+"                           # assert.equal, testify assert.Equal, xUnit
    r"|^\s*assert\s+\S"                         # Python: assert x == y
    r"|\bexpect\s*\("
    r"|\.should\b|\bshould\s*\("
    r"|\.to(?:be|equal|match|contain|have|throw)\w*"
    r"|(?<![\w.])verify\s*\("                   # Mockito verify(..), never jwt.verify
    r"|\bxctassert\w*\s*\("
    r"|\brequire\.(?:no)?error\s*\("
    r"|(?<![\w.])ok\s*\("
    r"|(?<![\w.])check\s*\(",
    re.I | re.M,
)

# Comments are stripped before assertion matching so a TODO that mentions
# assertions is not counted as one.
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(
    r"(?m)(?:^[ \t]*(?://|#(?!\[))|(?<![:\w\"'/])//|[ \t]#[ \t]).*$"
)

# Something a test runner would actually run. A file named like a test passes on
# its name alone; anything else has to contain a runner call.
TEST_NAME_PATTERN = re.compile(
    r"(?:\.(?:spec|test)\.[A-Za-z0-9]+$|_test\.(?:py|go|rb|exs?)$|(?:^|/)test_[^/]*\.py$"
    r"|\.cs$|\.kt$|\.swift$|\.java$)"
)
TEST_RUNNER_PATTERN = re.compile(
    r"(?<![\w.])(?:test|it|describe|suite)\s*(?:\.\w+\s*)?\(|\bdef\s+test_|\bfunc\s+Test"
    r"|#\[test\]|\[(?:Fact|Theory|Test|TestMethod)\]|@Test\b|@pytest\.|\bRSpec\.describe\b"
)

# Evidence that says a thing happened without saying what. A real run leaves
# counts, names, or routes behind; "command completed, exit 0" is an
# attestation wearing a log's clothes.
VAGUE_EVIDENCE_WORDS = re.compile(
    r"\b(?:completed?|finished|done|passed|pass|ok|success(?:ful)?|recorded|verified"
    r"|checked|reviewed|tested|no issues|all good|looks good|working)\b",
    re.I,
)


def strip_comments(body):
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", body))


def assertion_count(body):
    return len(ASSERTION_PATTERN.findall(strip_comments(body)))


def has_assertion(body):
    return assertion_count(body) > 0


def looks_like_test_file(candidate, body):
    name = str(candidate).replace("\\", "/")
    if TEST_NAME_PATTERN.search(name):
        return True
    if name.endswith(".rs"):
        return "#[test]" in body
    return bool(TEST_RUNNER_PATTERN.search(strip_comments(body)))


def is_uninformative(body):
    """A single short line whose only content is a claim of success."""
    stripped = body.strip()
    if not stripped or "\n" in stripped or len(stripped) > 100:
        return False
    return bool(VAGUE_EVIDENCE_WORDS.search(stripped))


def check_evidence_list(
    values,
    field_path,
    findings,
    project_root,
    required,
    missing_rule="evidence-path-missing",
    expect_assertions=False,
    role="evidence",
    owner="",
):
    """Validate one evidence/test list. Returns the resolvable local paths.

    `expect_assertions` marks a list of test files rather than run output: the
    file must be shaped like a test and contain an assertion, because an empty
    test body satisfies every path-and-content check while proving nothing about
    the capability it is listed under.

    `role` and `owner` say what is citing the file, so the sharing rules can
    later ask whether one file is standing in for many claims.
    """
    resolved = []
    if values is None:
        return resolved
    if not isinstance(values, list):
        # `"evidence": true` and `"tests": "tests/user.spec.ts"` are non-empty,
        # so every caller's nonempty() gate let them through with no path, size,
        # assertion or token check at all.
        findings.add(
            "manifest-type",
            field_path,
            "must be an array of strings, not " + type(values).__name__,
        )
        return resolved
    entries = []
    for index, raw in enumerate(values):
        if isinstance(raw, str):
            entries.append((index, raw))
        else:
            findings.add(
                "manifest-type",
                field_path + "[" + str(index) + "]",
                "must be a string path, not " + type(raw).__name__,
            )
    for index, raw in entries:
        entry_path = field_path + "[" + str(index) + "]"
        kind = evidence_kind(raw)
        if kind == "manual":
            findings.add(
                "manual-evidence",
                entry_path,
                "manual attestation '" + raw.strip() + "' is not verifiable evidence",
            )
            continue
        if kind in ("url", "command"):
            continue
        candidate = evidence_path(raw, project_root)
        if candidate is None:
            continue
        if escapes_root(candidate, project_root):
            findings.add(
                missing_rule,
                entry_path,
                "evidence path resolves outside the project: " + raw.strip(),
            )
            continue
        if inside_manifest_dir(candidate, findings.manifest_dir):
            findings.add(
                "evidence-inside-manifest-dir",
                entry_path,
                "'" + raw.strip() + "' is inside .admin-console/; the manifest and its "
                "working files are the claim, not evidence for it",
            )
            continue
        state = path_state(candidate)
        if state == "missing":
            findings.add(
                missing_rule, entry_path, "evidence path does not exist: " + raw.strip()
            )
            continue
        if state == "directory":
            findings.add(
                missing_rule,
                entry_path,
                "evidence must name a file, not a directory: " + raw.strip(),
            )
            continue
        if state == "empty":
            findings.add(
                "evidence-file-empty",
                entry_path,
                "evidence path is empty or whitespace only: " + raw.strip(),
            )
            continue
        body = read_head(candidate)
        if expect_assertions:
            # A test file is not placeholder-scanned: fabricated fixtures, mocks
            # and stubs are what tests are made of. The boundary is enforced by
            # where the path appears (references/test-data.md), not by content.
            if not looks_like_test_file(candidate, body):
                findings.add(
                    "test-file-shape",
                    entry_path,
                    "'" + raw.strip() + "' does not look like a test: it has no test-file "
                    "name (*.spec.*, *.test.*, test_*.py, *_test.go ...) and no test-runner call",
                )
                continue
            if not has_assertion(body):
                findings.add(
                    "test-asserts-nothing",
                    entry_path,
                    "test file contains no assertion: " + raw.strip()
                    + " — an empty or assertion-free test passes without proving anything",
                )
                continue
        else:
            hit = scan_file_placeholder(body)
            if hit and (entry_path + "#content") not in findings.static_allowed:
                token, line = hit
                findings.add(
                    "placeholder-scan",
                    entry_path,
                    "evidence file " + raw.strip() + " contains placeholder token '"
                    + token + "' at line " + str(line)
                    + "; replace it with real output, or register the file's content in "
                    "declaredStatic[] with path '" + entry_path + "#content'",
                )
                continue
            if is_uninformative(body):
                findings.add(
                    "evidence-uninformative",
                    entry_path,
                    "evidence states success without stating what ran: " + raw.strip()
                    + " — record the command's real output, with counts, names, or routes",
                )
                continue
        resolved.append(candidate)
        findings.evidence_index.append(
            {
                "role": role,
                "owner": owner,
                "field": entry_path,
                "path": str(candidate.resolve()),
                "raw": raw.strip(),
            }
        )
    if required and entries and not resolved:
        findings.add(
            "evidence-not-resolvable",
            field_path,
            "no entry resolves to a non-empty local path; "
            "URL and manual entries are supplemental only",
        )
    return resolved


def capability_tokens(capability):
    """Words a test for this capability must be able to mention.

    UI routes are deliberately absent: every capability on a screen shares the
    screen's route, so one route-smoke loop token-matched all of them.
    """
    tokens = set()
    cid = text_of(capability.get("id"))
    if cid:
        tokens.add(cid)
        for separator in ("-", "_", " ", "/"):
            tokens.add(cid.replace(".", separator))
    for value in as_list(capability.get("serverOperations")):
        if isinstance(value, str) and value.strip():
            tokens.add(value.strip())
    long_tokens = {token for token in tokens if len(token) >= 4}
    # Falling back rather than returning an empty set: a short id like 'a.b'
    # produced no tokens at all, which silently skipped the whole
    # evidence-token-match check instead of tightening it.
    return long_tokens or {token for token in tokens if token}


# --------------------------------------------------------------------------
# placeholder scanning
# --------------------------------------------------------------------------


def scan_targets(manifest, release):
    """Collect (field_path, value) pairs for every placeholder-scanned field.

    Scanned at every phase and every status, not only once implemented. An empty
    field never matches the pattern, so nothing is lost by scanning a half-built
    model — and the rule the skill actually states is "leave implementation fields
    empty rather than filling them with placeholder text". Deferring the scan until
    a capability flips to implemented is what lets `mockUserRepository.findAll` sit
    in the manifest for the whole build and surface only at the release gate.
    """
    targets = []
    for eid, entity in iter_entities(manifest):
        targets.append(("entities[" + eid + "].sourceOfTruth", entity.get("sourceOfTruth")))
    for path, _entity, capability in iter_capabilities(manifest):
        for key in SCANNED_CAPABILITY_FIELDS:
            targets.append((path + "." + key, capability.get(key)))
    for path, screen in iter_named(manifest, "screens"):
        targets.append((path + ".dataSources", screen.get("dataSources")))
        targets.append((path + ".tests", screen.get("tests")))
    for path, integration in iter_named(manifest, "integrations"):
        targets.append((path + ".sourceOfTruth", integration.get("sourceOfTruth")))
    for path, gate in iter_named(manifest, "qualityGates"):
        targets.append((path + ".evidence", gate.get("evidence")))
    cross = as_dict(manifest.get("crossCutting"))
    for section in CROSS_SECTIONS:
        values = as_dict(cross.get(section))
        targets.append(("crossCutting." + section + ".evidence", values.get("evidence")))
    authorization = as_dict(cross.get("authorization"))
    targets.append(("crossCutting.authorization.policyTests", authorization.get("policyTests")))
    return targets


# Fields that hold a claim about the implementation, not a value. A declaredStatic
# entry may exempt a value (a fixed tax rate, a hard-coded support address) but
# never the place a capability says where its data comes from or how it is proven:
# registering `dataBinding` as "static" turned the sanctioned exception into a
# way to ship a mock.
FORBIDDEN_STATIC_FIELDS = frozenset(
    (
        "dataBinding", "sourceOfTruth", "serverOperations", "authorizationPolicies",
        "auditEvents", "tests", "evidence", "dataSources", "policyTests", "uiRoutes",
    )
)


def final_field(path):
    """Last field name in a manifest path: 'a[x.y].tests[0]' -> 'tests'."""
    depth = 0
    segments = [""]
    for char in path:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        if char == "." and depth == 0:
            segments.append("")
        else:
            segments[-1] += char
    return re.sub(r"(?:\[[^\]]*\])+$", "", segments[-1])


def static_index(manifest, findings):
    """Map declaredStatic paths that are allowed to escape the placeholder scan.

    A path ending `#content` exempts the CONTENT of an evidence file (a captured
    log that legitimately quotes a mock), never the manifest string that names it.
    """
    allowed = set()
    caps = capability_index(manifest)
    agent_ids = {
        text_of(agent.get("id")).lower() for _p, agent in iter_named(manifest, "agents")
    }
    for path, entry in iter_named(manifest, "declaredStatic"):
        target = text_of(entry.get("path"))
        if not target:
            findings.add("required-field-empty", path + ".path", "declaredStatic entry needs the manifest path it covers")
            continue
        missing = [
            key
            for key in ("value", "reason", "approvedBy")
            if not text_of(entry.get(key))
        ]
        if missing:
            # value matters as much as reason: a registration that does not say
            # WHAT is static exempts a field from the placeholder scan without
            # recording what was exempted.
            findings.add(
                "declared-static-incomplete",
                path,
                "declaredStatic entry for '" + target + "' is missing " + ", ".join(missing)
                + "; an incomplete registration is not a valid escape",
            )
            continue
        if not target.endswith("#content") and final_field(target) in FORBIDDEN_STATIC_FIELDS:
            findings.add(
                "declared-static-forbidden-path",
                path + ".path",
                "'" + target + "' names a " + final_field(target) + " field; that field states "
                "where data comes from or how it is proven, so it can never be declared static",
            )
            continue
        approver = text_of(entry.get("approvedBy")).lower()
        interested = set(agent_ids)
        covered = re.search(r"capabilities\[([^\]]+)\]", target)
        if covered and covered.group(1) in caps:
            capability = caps[covered.group(1)][2]
            for key in ("owner", "implementedBy", "reviewedBy"):
                interested.add(text_of(capability.get(key)).lower())
        interested.discard("")
        if approver in interested:
            findings.add(
                "declared-static-self-approved",
                path + ".approvedBy",
                "'" + text_of(entry.get("approvedBy")) + "' is an agent or the capability's own "
                "implementer or reviewer; a static exemption needs an approver outside the build",
            )
        allowed.add(target)
    return allowed


CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
IDENT_SEPARATORS = re.compile(r"[_./\\:>|]+")


def split_identifiers(text):
    """Expose placeholder tokens buried inside identifiers.

    A word-boundary scan alone misses the naming style agents actually use:
    'mockSuspendUser', 'stub_payment_gateway' and 'FakeRepo' all read as one word
    to \\b. Splitting camelCase humps and identifier separators makes each token
    scannable without loosening the pattern into substring matching, which would
    fire on 'randomize' or 'stubborn'.
    """
    return IDENT_SEPARATORS.sub(" ", CAMEL_BOUNDARY.sub(" ", text))


def flatten_strings(prefix, value):
    """Yield (path, text) for every string at any depth inside a list."""
    if isinstance(value, str):
        yield (prefix, value)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            for pair in flatten_strings(prefix + "[" + str(index) + "]", item):
                yield pair


def placeholder_match(text):
    """Return the placeholder token found in a manifest string, or None."""
    folded = fold_confusables(text)
    hit = PLACEHOLDER_PATTERN.search(split_identifiers(folded))
    if hit:
        return hit.group(0)
    for token in folded.split():
        hit = substring_hit(token)
        if hit:
            return hit.group(0)
    return None


def scan_file_placeholder(body):
    """Return (token, line_number) for the first placeholder in an evidence file, else None.

    Word-boundary matching only, and framework idioms are skipped: a captured log
    that says `jest.mock('nodemailer')` or `1200 samples` is not a placeholder.
    """
    for number, line in enumerate(body.splitlines(), 1):
        cleaned = FRAMEWORK_CALLS.sub(" ", fold_confusables(line))
        hit = FILE_PLACEHOLDER_PATTERN.search(split_identifiers(cleaned))
        if hit:
            return hit.group(0), number
    return None


def run_placeholder_scan(manifest, findings, release):
    targets = scan_targets(manifest, release)
    allowed = findings.static_allowed
    known_paths = set()
    for field_path, value in targets:
        known_paths.add(field_path)
        if isinstance(value, str):
            entries = [(field_path, value)]
        elif isinstance(value, list):
            # Recurse: a nested list is not a valid shape here, but skipping it
            # silently lets ["real op", ["mockHandler"]] hide a mock from the scan.
            entries = list(flatten_strings(field_path, value))
        else:
            continue
        for entry_path, text in entries:
            known_paths.add(entry_path)
            if entry_path in allowed or field_path in allowed:
                continue
            folded = fold_confusables(text)
            scannable = split_identifiers(folded)
            match = HARDCODE_PATTERN.search(scannable)
            if match:
                findings.add(
                    "unregistered-static",
                    entry_path,
                    "hard-coded marker '"
                    + match.group(0)
                    + "' in '"
                    + text
                    + "'; register it in declaredStatic[] with a reason and approver or replace it",
                )
            token = placeholder_match(text)
            if token:
                findings.add(
                    "placeholder-scan",
                    entry_path,
                    "placeholder token '"
                    + token
                    + "' in '"
                    + text
                    + "'; replace it with the real value or register it in declaredStatic[]",
                )
    for target in sorted(allowed):
        base = target[: -len("#content")] if target.endswith("#content") else target
        if base not in known_paths:
            findings.add(
                "declared-static-unused",
                "declaredStatic",
                "path '" + target + "' does not match any scanned manifest field",
            )


# --------------------------------------------------------------------------
# shared shape checks
# --------------------------------------------------------------------------


def check_id(value, path, findings):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        findings.add(
            "id-invalid",
            path,
            "id must be lowercase alphanumeric separated by dots or hyphens",
        )


def check_unique_ids(manifest, key, findings):
    items = manifest.get(key)
    if not isinstance(items, list):
        findings.add("manifest-type", key, "must be an array")
        return
    seen = set()
    for index, item in enumerate(items):
        path = key + "[" + str(index) + "]"
        if not isinstance(item, dict):
            findings.add("manifest-type", path, "must be an object")
            continue
        value = item.get("id")
        check_id(value, path + ".id", findings)
        if isinstance(value, str):
            if value in seen:
                # The position is part of the message: identical findings are
                # de-duplicated, and a second duplicate of the same id must still
                # count as new to the write guard.
                findings.add(
                    "id-duplicate", key, "duplicate id '" + value + "' at position " + str(index)
                )
            seen.add(value)


def check_required(obj, keys, path, findings):
    if not isinstance(obj, dict):
        findings.add("manifest-type", path, "must be an object")
        return False
    for key in keys:
        if key not in obj:
            findings.add("manifest-key-missing", path + "." + key, "is required")
    return True


def check_nonempty(obj, keys, path, findings):
    for key in keys:
        if not nonempty(obj.get(key)):
            findings.add("required-field-empty", path + "." + key, "must not be empty")


def check_enum(value, allowed, path, findings, label):
    if value not in allowed:
        findings.add(
            "enum-invalid",
            path,
            label + " must be one of: " + ", ".join(allowed) + " (found " + repr(value) + ")",
        )


def check_roles(values, role_ids, path, findings):
    unknown = sorted(
        value
        for value in as_list(values)
        if isinstance(value, str) and value not in role_ids
    )
    if unknown:
        findings.add(
            "unknown-role-reference",
            path + ".roles",
            "references undeclared roles: " + ", ".join(unknown),
        )


# --------------------------------------------------------------------------
# schema-driven type checks
# --------------------------------------------------------------------------

_SCHEMA_CACHE = []


def load_schema():
    if not _SCHEMA_CACHE:
        try:
            _SCHEMA_CACHE.append(json.loads(SCHEMA_PATH.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as exc:
            raise ManifestError(
                "Cannot read the manifest schema at " + str(SCHEMA_PATH) + ": " + str(exc)
            )
    return _SCHEMA_CACHE[0]


def resolve_schema(schema, root):
    """Follow a local `$ref` (the only kind this schema uses)."""
    seen = 0
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str) and seen < 10:
        seen += 1
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            break
        node = root
        for part in ref[2:].split("/"):
            node = as_dict(node).get(part, {})
        merged = dict(as_dict(node))
        merged.update({key: val for key, val in schema.items() if key != "$ref"})
        schema = merged
    return schema if isinstance(schema, dict) else {}


def schema_item_key(item, index, item_schema):
    """Name an array element the way every other finding path does: by id."""
    if isinstance(item, dict) and "id" in as_dict(item_schema.get("properties")):
        return text_of(item.get("id")) or "#" + str(index)
    return str(index)


def check_schema_types(value, schema, root, path, findings, unknown_field=False):
    """Type-check the manifest against the schema's `type`, `items` and `properties`.

    The schema types `tests`, `evidence`, `policyTests` and every other list of
    strings, and until this walker existed nothing enforced it: `"tests":
    "tests/user.spec.ts"` and `"evidence": true` are non-empty, so every check
    that began `if nonempty(...)` waved them through with no path, size, assertion
    or token check. Types are E3 at every profile; enums, patterns and required
    keys stay with the rule-specific checks.
    """
    schema = resolve_schema(schema, root)
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            findings.add("manifest-type", path, "must be an object")
            return
        properties = as_dict(schema.get("properties"))
        for key, sub in properties.items():
            if key in value:
                child = key if not path else path + "." + key
                check_schema_types(value[key], sub, root, child, findings, unknown_field)
        if unknown_field and properties:
            for key in value:
                if key not in properties:
                    findings.add(
                        "unknown-field",
                        (key if not path else path + "." + key),
                        "is not a field the manifest schema declares; check the spelling, or "
                        "record the extra information in a field that exists",
                    )
        return
    if expected == "array":
        if not isinstance(value, list):
            findings.add("manifest-type", path, "must be an array")
            return
        items = resolve_schema(schema.get("items"), root)
        for index, item in enumerate(value):
            child = path + "[" + schema_item_key(item, index, items) + "]"
            if items:
                check_schema_types(item, items, root, child, findings, unknown_field)
        return
    if expected == "string" and not isinstance(value, str):
        findings.add("manifest-type", path, "must be a string, not " + type(value).__name__)
    elif expected == "boolean" and not isinstance(value, bool):
        findings.add("manifest-type", path, "must be a boolean")
    elif "enum" in schema and isinstance(value, (list, dict)):
        findings.add(
            "manifest-type", path, "must be a single string, not " + type(value).__name__
        )


# --------------------------------------------------------------------------
# structural completeness (the coverage audit)
# --------------------------------------------------------------------------


def structural_findings(manifest, findings, release):
    roles = [item for item in as_list(manifest.get("roles")) if isinstance(item, dict)]
    role_ids = {text_of(role.get("id")) for role in roles if text_of(role.get("id"))}
    screens = [item for item in as_list(manifest.get("screens")) if isinstance(item, dict)]
    caps = capability_index(manifest)

    screen_routes = {}
    for screen in screens:
        route = text_of(screen.get("route"))
        if route:
            screen_routes.setdefault(route, []).append(screen)

    roles_on_screens = set()
    roles_on_caps = set()
    linked_capabilities = set()
    for screen in screens:
        roles_on_screens.update(
            value for value in as_list(screen.get("roles")) if isinstance(value, str)
        )
        linked_capabilities.update(
            value for value in as_list(screen.get("capabilities")) if isinstance(value, str)
        )
    for _path, _entity, capability in iter_capabilities(manifest):
        roles_on_caps.update(
            value for value in as_list(capability.get("roles")) if isinstance(value, str)
        )

    for index, role in enumerate(roles):
        rid = text_of(role.get("id")) or "#" + str(index)
        path = "roles[" + rid + "]"
        if rid not in roles_on_screens:
            findings.add("role-unused", path, "role is not granted access to any screen")
        if rid not in roles_on_caps:
            findings.add("role-unused", path, "role is not the actor on any capability")

    # screens -> capabilities
    for path, screen in iter_named(manifest, "screens"):
        listed = as_list(screen.get("capabilities"))
        if not listed:
            findings.add(
                "screen-no-capability",
                path + ".capabilities",
                "screen exposes no capability; either link one or delete the screen",
            )
        for value in listed:
            if isinstance(value, str) and value not in caps:
                findings.add(
                    "unknown-capability-reference",
                    path + ".capabilities",
                    "references unknown capability '" + value + "'",
                )

    # capabilities -> screens, roles, routes
    for path, _entity, capability in iter_capabilities(manifest):
        cid = text_of(capability.get("id"))
        if capability.get("status") == "not-applicable":
            # A rejected catalog candidate has no actor, screen or route by
            # definition; only its rationale matters (rationale-required).
            continue
        if not as_list(capability.get("roles")):
            findings.add(
                "capability-no-role",
                path + ".roles",
                "capability has no actor role",
            )
        routes = [value for value in as_list(capability.get("uiRoutes")) if isinstance(value, str)]
        for route in routes:
            if route not in screen_routes:
                findings.add(
                    "capability-route-unknown",
                    path + ".uiRoutes",
                    "route '" + route + "' has no matching screen",
                )
        if capability.get("status") != "implemented":
            continue
        if "ui" not in surface_of(capability):
            continue
        anchored = False
        listed_anywhere = False
        for screen in screens:
            if cid not in as_list(screen.get("capabilities")):
                continue
            listed_anywhere = True
            if text_of(screen.get("route")) in routes:
                anchored = True
                break
        if not anchored:
            if listed_anywhere:
                detail = (
                    "is listed on a screen whose route is absent from its uiRoutes; "
                    "the link must be route-anchored"
                )
            else:
                detail = "is not reachable from any screen"
            findings.add("capability-screen-link", path, "implemented capability " + detail)

    # entity observability and lifecycle reachability
    for eid, entity in iter_entities(manifest):
        path = "entities[" + eid + "]"
        entity_caps = [
            cap for cap in as_list(entity.get("capabilities")) if isinstance(cap, dict)
        ]
        kinds = {cap.get("kind") for cap in entity_caps if isinstance(cap.get("kind"), str)}
        if "query" not in kinds:
            findings.add(
                "entity-query-missing",
                path + ".capabilities",
                "entity has no query capability, so operators cannot observe it",
            )
        states = [
            value for value in as_list(entity.get("lifecycleStates")) if isinstance(value, str)
        ]
        reached = set()
        observed = set()
        for cap in entity_caps:
            entity_states = as_dict(cap.get("entityStates"))
            from_states = [v for v in as_list(entity_states.get("from")) if isinstance(v, str)]
            to_states = [v for v in as_list(entity_states.get("to")) if isinstance(v, str)]
            observed.update(from_states)
            observed.update(to_states)
            if cap.get("kind") in ("command", "job"):
                reached.update(to_states)
        transition_targets = set()
        for index, transition in enumerate(as_list(entity.get("lifecycleTransitions"))):
            if not isinstance(transition, dict):
                findings.add("manifest-type", path + ".lifecycleTransitions[" + str(index) + "]", "must be an object")
                continue
            tpath = path + ".lifecycleTransitions[" + str(index) + "]"
            command = text_of(transition.get("command"))
            if not command:
                findings.add("required-field-empty", tpath + ".command", "transition needs the capability that performs it")
            elif command not in caps:
                findings.add(
                    "transition-command-unknown",
                    tpath + ".command",
                    "command '" + command + "' is not a declared capability id",
                )
            else:
                transition_targets.add(text_of(transition.get("to")))
            unknown = sorted(
                value
                for value in as_list(transition.get("actorRoles"))
                if isinstance(value, str) and value not in role_ids
            )
            if unknown:
                findings.add(
                    "unknown-role-reference",
                    tpath + ".actorRoles",
                    "references undeclared roles: " + ", ".join(unknown),
                )
            for key in ("from", "to"):
                value = text_of(transition.get(key))
                if value and states and value not in states:
                    findings.add(
                        "enum-invalid",
                        tpath + "." + key,
                        "'" + value + "' is not one of the declared lifecycleStates",
                    )
        for position, state in enumerate(states):
            if state not in observed and not lifecycle_escaped(manifest, entity, state):
                findings.add(
                    "lifecycle-observable",
                    path + ".lifecycleStates",
                    "state '" + state + "' appears in no capability entityStates, so it is invisible to operators",
                )
            if position == 0:
                continue
            # Same escape as observability: a state entered only by an external
            # system -- a payment webhook, a device callback -- is legitimately
            # unreachable by any operator command. Recording why in gaps[] is the
            # honest answer, and it must count here too, not only above.
            if (
                state not in reached
                and state not in transition_targets
                and not lifecycle_escaped(manifest, entity, state)
            ):
                findings.add(
                    "lifecycle-reachable",
                    path + ".lifecycleStates",
                    "state '" + state + "' is the target of no command capability, so operators cannot put a record into it",
                )

    # integrations and queues
    for path, integration in iter_named(manifest, "integrations"):
        check_roles(integration.get("roles"), role_ids, path, findings)
        if release and integration.get("status") == "implemented":
            for key in ("monitoring", "reconciliation", "failureHandling", "credentialBoundary"):
                if not nonempty(integration.get(key)):
                    findings.add(
                        "integration-controls",
                        path + "." + key,
                        "an implemented integration must declare " + key,
                    )
    for path, queue in iter_named(manifest, "workQueues"):
        check_roles(queue.get("roles"), role_ids, path, findings)

    # command safety
    for path, _entity, capability in iter_capabilities(manifest):
        status = capability.get("status")
        kind = capability.get("kind")
        risk = capability.get("risk")
        if status != "implemented":
            continue
        if risk in ("high", "critical"):
            for key in ("safeguards", "recovery"):
                if not nonempty(capability.get(key)):
                    findings.add(
                        "high-risk-controls",
                        path + "." + key,
                        "a " + str(risk) + "-risk capability must declare " + key,
                    )
        if release and kind in MUTATING_KINDS:
            value = text_of(capability.get("idempotency"))
            if not value:
                findings.add(
                    "idempotency-missing",
                    path + ".idempotency",
                    "a " + str(kind) + " must state its idempotency key or 'not-required: <reason>'",
                )
            elif value.lower().startswith("not-required") and not NOT_REQUIRED_PATTERN.match(value):
                findings.add(
                    "idempotency-missing",
                    path + ".idempotency",
                    "'not-required' must be written as 'not-required: <reason>'",
                )
        if kind in MUTATING_KINDS and not nonempty(capability.get("auditEvents")):
            findings.add(
                "audit-events-missing",
                path + ".auditEvents",
                "a " + str(kind) + " must emit at least one audit event",
            )
        if risk == "critical" and kind == "command":
            declared = False
            for role in roles:
                if text_of(role.get("id")) in as_list(capability.get("roles")):
                    if nonempty(role.get("separationOfDuties")):
                        declared = True
            if not declared:
                findings.add(
                    "separation-of-duties",
                    path,
                    "no actor role declares separationOfDuties for this critical command",
                )


def lifecycle_escaped(manifest, entity, state):
    """Whether a recorded gap explains why a lifecycle state has no operator path.

    A state entered only by an external system (a payment webhook, a device
    callback) is legitimately unreachable by any operator command, and recording
    why in gaps[] is the honest answer. But the escape used to be a substring
    match over ALL gap text, so any gap that happened to mention 'quarantined' --
    including findings the OCR bridge files automatically -- silenced the check for
    every entity. It now takes an open or accepted gap, not filed by the OCR
    bridge, that names the state AND either names this entity or carries an
    explicit `entity` (or `capability`) field pointing at it.
    """
    entity_id = text_of(entity.get("id")).lower()
    capability_ids = {
        text_of(cap.get("id"))
        for cap in as_list(entity.get("capabilities"))
        if isinstance(cap, dict)
    }
    wanted = re.compile(r"(?<![a-z0-9])" + re.escape(state.lower()) + r"(?![a-z0-9])")
    for _path, gap in iter_named(manifest, "gaps"):
        if gap.get("status") not in ("open", "accepted"):
            continue
        if text_of(gap.get("source")).lower() == "ocr":
            continue
        text = (text_of(gap.get("description")) + " " + text_of(gap.get("rationale"))).lower()
        if not wanted.search(text):
            continue
        gap_entity = text_of(gap.get("entity")).lower()
        gap_capability = text_of(gap.get("capability"))
        explicit = bool(
            (gap_entity and gap_entity == entity_id)
            or (gap_capability and gap_capability in capability_ids)
        )
        if explicit or (entity_id and entity_id in text):
            return True
    return False


def collect_escape_text(manifest):
    """Lowercased text of gap and declaredStatic rationales, used as the archetype-coverage escape."""
    chunks = []
    for _path, gap in iter_named(manifest, "gaps"):
        if text_of(gap.get("source")).lower() == "ocr":
            continue
        chunks.append(text_of(gap.get("description")))
        chunks.append(text_of(gap.get("rationale")))
    for _path, entry in iter_named(manifest, "declaredStatic"):
        chunks.append(text_of(entry.get("path")))
        chunks.append(text_of(entry.get("reason")))
    return " ".join(chunks).lower()


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate_manifest(manifest, phase, project_root, profile, manifest_dir=None):
    findings = Findings(profile, phase)
    findings.manifest_dir = manifest_dir
    release = phase == "release"
    check_required(manifest, TOP_LEVEL_KEYS, "manifest", findings)
    if isinstance(manifest, dict):
        schema = load_schema()
        check_schema_types(manifest, schema, schema, "", findings, unknown_field=True)
        findings.static_allowed = frozenset(static_index(manifest, findings))

    version = manifest.get("manifestVersion")
    if version != MANIFEST_VERSION:
        if version in LEGACY_VERSIONS:
            findings.add(
                "manifest-version",
                "manifest.manifestVersion",
                "is '" + str(version) + "'; run `migrate --write` to reach " + MANIFEST_VERSION,
            )
        else:
            findings.add(
                "manifest-version",
                "manifest.manifestVersion",
                "must be '" + MANIFEST_VERSION + "'",
            )
    declared_profile = manifest.get("profile")
    if declared_profile is not None and declared_profile not in PROFILES:
        findings.add(
            "profile-invalid", "manifest.profile", "must be one of: " + ", ".join(PROFILES)
        )

    validate_platform(manifest, findings, release)
    for key in COLLECTION_KEYS:
        if key in manifest:
            check_unique_ids(manifest, key, findings)

    roles = [item for item in as_list(manifest.get("roles")) if isinstance(item, dict)]
    role_ids = {text_of(role.get("id")) for role in roles if text_of(role.get("id"))}
    validate_roles(manifest, findings)
    validate_entities(manifest, findings, release, role_ids)
    validate_screens(manifest, findings, release, role_ids)
    validate_queues_and_integrations(manifest, findings, release)
    validate_cross_cutting(manifest, findings, release, project_root)
    validate_gates(manifest, findings, release, project_root)
    validate_decisions(manifest, findings, release)
    validate_gaps(manifest, findings, release)
    validate_agents(manifest, findings)

    run_placeholder_scan(manifest, findings, release)
    check_archetype_coverage(manifest, findings)
    structural_findings(manifest, findings, release)
    if release:
        validate_evidence(manifest, findings, project_root)
        check_evidence_sharing(manifest, findings)
        if not roles:
            findings.add("required-field-empty", "roles", "release requires at least one modeled role")
        if not as_list(manifest.get("entities")):
            findings.add("required-field-empty", "entities", "release requires at least one managed entity")
        ui_capabilities = [
            cap for _p, _e, cap in iter_capabilities(manifest) if "ui" in surface_of(cap)
        ]
        has_capabilities = any(True for _ in iter_capabilities(manifest))
        if not as_list(manifest.get("screens")) and (ui_capabilities or not has_capabilities):
            findings.add("required-field-empty", "screens", "release requires at least one connected screen")
    else:
        for key in ("roles", "entities", "screens"):
            if not as_list(manifest.get(key)):
                findings.add("plan-note", key, "not modeled yet")
    return findings


def validate_platform(manifest, findings, release):
    platform = manifest.get("platform")
    keys = (
        "name",
        "summary",
        "archetypes",
        "tenancy",
        "regulatedData",
        "sourceSystems",
        "operationalObjectives",
        "stack",
        "researchSources",
        "volumes",
    )
    if not check_required(platform, keys, "platform", findings):
        return
    check_nonempty(platform, ("name",), "platform", findings)
    for key in ("summary", "archetypes", "operationalObjectives"):
        if nonempty(platform.get(key)):
            continue
        if release:
            findings.add("required-field-empty", "platform." + key, "must not be empty for release")
        else:
            findings.add("plan-note", "platform." + key, "is not modeled yet")
    tenancy = platform.get("tenancy")
    check_enum(tenancy, TENANCIES, "platform.tenancy", findings, "tenancy")
    if release and tenancy == "unknown":
        findings.add("required-field-empty", "platform.tenancy", "cannot remain unknown for release")
    stack = as_dict(platform.get("stack"))
    if release:
        for key in ("frontend", "backend", "database", "auth"):
            if not nonempty(stack.get(key)):
                findings.add(
                    "stack-incomplete",
                    "platform.stack." + key,
                    "record the actual technology so stack-specific idioms can be verified",
                )
        if not as_list(platform.get("researchSources")):
            findings.add(
                "research-sources-missing",
                "platform.researchSources",
                "record the authoritative documentation consulted for this stack",
            )
    for index, source in enumerate(as_list(platform.get("researchSources"))):
        path = "platform.researchSources[" + str(index) + "]"
        if not isinstance(source, dict):
            findings.add("manifest-type", path, "must be an object")
            continue
        check_nonempty(source, ("topic", "url"), path, findings)
        # The schema requires appliedTo and the validator did not check it, so a
        # source could be recorded without saying what it actually informed --
        # which is the only part that makes it auditable later.
        if not as_list(source.get("appliedTo")):
            findings.add(
                "research-source-incomplete",
                path + ".appliedTo",
                "name the capabilities or decisions this source informed; "
                "an unattributed link is a citation, not evidence of research",
            )


def validate_roles(manifest, findings):
    for path, role in iter_named(manifest, "roles"):
        check_required(
            role,
            ("id", "name", "responsibilities", "scopes", "mfaRequired"),
            path,
            findings,
        )
        check_nonempty(role, ("name", "responsibilities", "scopes"), path, findings)
        if "mfaRequired" in role and not isinstance(role.get("mfaRequired"), bool):
            findings.add("manifest-type", path + ".mfaRequired", "must be a boolean")


def validate_entities(manifest, findings, release, role_ids):
    seen_capability_ids = set()
    confirmed_targets = set()
    for _dpath, decision in iter_named(manifest, "decisions"):
        if decision.get("status") == "confirmed":
            confirmed_targets.update(
                value for value in as_list(decision.get("appliesTo")) if isinstance(value, str)
            )
    for eid, entity in iter_entities(manifest):
        path = "entities[" + eid + "]"
        check_required(
            entity,
            (
                "id",
                "name",
                "sourceOfTruth",
                "sensitivity",
                "tenantScoped",
                "lifecycleStates",
                "retention",
                "capabilities",
            ),
            path,
            findings,
        )
        check_nonempty(entity, ("name", "sourceOfTruth", "retention"), path, findings)
        check_enum(entity.get("sensitivity"), SENSITIVITIES, path + ".sensitivity", findings, "sensitivity")
        capabilities = entity.get("capabilities")
        if not isinstance(capabilities, list):
            findings.add("manifest-type", path + ".capabilities", "must be an array")
            continue
        if release and not capabilities:
            findings.add("required-field-empty", path + ".capabilities", "entity has no modeled capabilities")

    for path, entity, capability in iter_capabilities(manifest):
        check_required(
            capability,
            (
                "id",
                "outcome",
                "kind",
                "roles",
                "risk",
                "status",
                "rationale",
                "entityStates",
                "uiRoutes",
                "serverOperations",
                "authorizationPolicies",
                "auditEvents",
                "safeguards",
                "dataBinding",
                "tests",
                "evidence",
            ),
            path,
            findings,
        )
        cid = text_of(capability.get("id"))
        check_id(capability.get("id"), path + ".id", findings)
        if cid:
            if cid in seen_capability_ids:
                findings.add("id-duplicate", path + ".id", "capability id '" + cid + "' is used by another entity")
            seen_capability_ids.add(cid)
        status = capability.get("status")
        check_enum(status, STATUSES, path + ".status", findings, "status")
        check_enum(capability.get("kind"), CAPABILITY_KINDS, path + ".kind", findings, "kind")
        check_enum(capability.get("risk"), RISKS, path + ".risk", findings, "risk")
        if "reviewStatus" in capability:
            check_enum(
                capability.get("reviewStatus"),
                REVIEW_STATUSES,
                path + ".reviewStatus",
                findings,
                "reviewStatus",
            )
        check_roles(capability.get("roles"), role_ids, path, findings)
        if status in EXPLAINED_STATUSES and not nonempty(capability.get("rationale")):
            findings.add(
                "rationale-required",
                path + ".rationale",
                "status '" + str(status) + "' requires a recorded rationale",
            )
        if release and status not in RESOLVED_STATUSES:
            findings.add(
                "release-status-unresolved",
                path + ".status",
                "is '" + str(status) + "'; release requires implemented or not-applicable",
            )
        for value in as_list(capability.get("surface")):
            check_enum(value, SURFACES, path + ".surface", findings, "surface")
        if status == "implemented":
            surface = surface_of(capability)
            required_keys = [
                "outcome", "roles", "serverOperations", "authorizationPolicies",
                "dataBinding", "tests", "evidence",
            ]
            if "ui" in surface:
                required_keys.insert(2, "uiRoutes")
            if any(value != "ui" for value in surface):
                # No screen exists to carry the capability, so the way in and the
                # proof of who may use it have to be named on the capability itself.
                required_keys.extend(["entrypoints", "policyTests"])
            for key in required_keys:
                if not nonempty(capability.get(key)):
                    findings.add("required-field-empty", path + "." + key, "must not be empty for an implemented capability")
            if release and capability.get("reviewStatus") != "reviewed":
                findings.add(
                    "capability-unreviewed",
                    path + ".reviewStatus",
                    "an implemented capability must be reviewed by an agent other than its implementer",
                )
            check_reviewer_identity(manifest, capability, path, findings)
            if not text_of(capability.get("implementedBy")):
                findings.add(
                    "implementer-unrecorded",
                    path + ".implementedBy",
                    "an implemented capability must record who implemented it "
                    "(`claim --role implementer` stamps it), or review independence cannot be checked",
                )
        if capability.get("reviewStatus") == "contested" and not contested_gap_exists(manifest, cid):
            findings.add(
                "contested-needs-gap",
                path + ".reviewStatus",
                "'contested' must be paired with a gaps[] entry that names '" + cid
                + "' (its `capability` field, or its id in the description), so the implementer "
                "knows what to fix",
            )
        if (
            status == "not-applicable"
            and capability.get("kind") in ("command", "job", "export")
            and cid not in confirmed_targets
        ):
            findings.add(
                "not-applicable-undecided",
                path + ".status",
                "a not-applicable " + str(capability.get("kind")) + " needs a confirmed decision "
                "whose appliesTo names '" + cid + "'; without one it is a deferred capability "
                "relabelled so that release accepts it",
            )


def validate_screens(manifest, findings, release, role_ids):
    for path, screen in iter_named(manifest, "screens"):
        check_required(
            screen,
            (
                "id",
                "route",
                "purpose",
                "roles",
                "dataSources",
                "capabilities",
                "actions",
                "states",
                "responsive",
                "accessibilityStatus",
                "status",
                "rationale",
                "tests",
            ),
            path,
            findings,
        )
        check_nonempty(screen, ("route", "purpose", "roles"), path, findings)
        # An architect adds the screen; the ux-reviewer adds its states; a screen
        # is legitimately without either until then. Blocking the add refused the
        # documented split of work, so these warn until release.
        for key in ("dataSources", "states"):
            if not nonempty(screen.get(key)):
                findings.add(
                    "screen-incomplete",
                    path + "." + key,
                    "screen has no " + key + " yet; release requires it",
                )
        check_roles(screen.get("roles"), role_ids, path, findings)
        status = screen.get("status")
        check_enum(status, STATUSES, path + ".status", findings, "status")
        check_enum(
            screen.get("accessibilityStatus"),
            STATUSES,
            path + ".accessibilityStatus",
            findings,
            "accessibilityStatus",
        )
        if status in EXPLAINED_STATUSES and not nonempty(screen.get("rationale")):
            findings.add("rationale-required", path + ".rationale", "status '" + str(status) + "' requires a recorded rationale")
        states = {value for value in as_list(screen.get("states")) if isinstance(value, str)}
        if not release:
            continue
        if status != "implemented":
            findings.add("release-status-unresolved", path + ".status", "must be implemented for release")
        missing = [state for state in REQUIRED_SCREEN_STATES if state not in states]
        if missing:
            findings.add("screen-state-missing", path + ".states", "is missing: " + ", ".join(missing))
        if as_list(screen.get("actions")) and "success" not in states:
            findings.add("screen-state-missing", path + ".states", "must include success because the screen has actions")
        if screen.get("responsive") is not True:
            findings.add("screen-responsive", path + ".responsive", "must be true for release")
        if screen.get("accessibilityStatus") != "implemented":
            findings.add("screen-accessibility", path + ".accessibilityStatus", "must be implemented for release")
        if not nonempty(screen.get("tests")):
            findings.add("required-field-empty", path + ".tests", "must not be empty for release")


QUEUE_REQUIRED = ("id", "purpose", "roles", "source", "priorityRule", "sla", "actions", "status", "rationale")
INTEGRATION_REQUIRED = (
    "id",
    "direction",
    "sourceOfTruth",
    "operations",
    "failureHandling",
    "reconciliation",
    "monitoring",
    "status",
    "rationale",
)
QUEUE_CONTENT = ("purpose", "source", "priorityRule", "sla", "actions")
INTEGRATION_CONTENT = ("sourceOfTruth", "operations", "failureHandling")


def validate_queues_and_integrations(manifest, findings, release):
    for key in ("workQueues", "integrations"):
        required = QUEUE_REQUIRED if key == "workQueues" else INTEGRATION_REQUIRED
        content = QUEUE_CONTENT if key == "workQueues" else INTEGRATION_CONTENT
        for path, item in iter_named(manifest, key):
            # Every other collection enforces its schema-required fields. Without
            # this, {"id","status","rationale"} is a legal queue that clears
            # release with no source, no SLA and no action.
            check_required(item, required, path, findings)
            if release:
                check_nonempty(item, content, path, findings)
            status = item.get("status")
            check_enum(status, STATUSES, path + ".status", findings, "status")
            if status in EXPLAINED_STATUSES and not nonempty(item.get("rationale")):
                findings.add("rationale-required", path + ".rationale", "status '" + str(status) + "' requires a recorded rationale")
            if key == "integrations":
                check_enum(item.get("direction"), DIRECTIONS, path + ".direction", findings, "direction")
            if not release:
                continue
            if status not in RESOLVED_STATUSES:
                findings.add(
                    "release-status-unresolved",
                    path + ".status",
                    "is '" + str(status) + "'; release requires implemented or not-applicable",
                )
            if status == "implemented" and not nonempty(item.get("tests")):
                findings.add("required-field-empty", path + ".tests", "must not be empty for release")


def validate_cross_cutting(manifest, findings, release, project_root):
    cross = manifest.get("crossCutting")
    if not isinstance(cross, dict):
        findings.add("manifest-type", "crossCutting", "must be an object")
        return
    for section in CROSS_SECTIONS:
        if section not in cross:
            findings.add("crosscutting-section-missing", "crossCutting." + section, "is required")
    if not release:
        return

    platform = as_dict(manifest.get("platform"))
    capabilities = [cap for _p, _e, cap in iter_capabilities(manifest)]
    implemented = [cap for cap in capabilities if cap.get("status") == "implemented"]
    required_true = {
        "authentication": ["implemented", "mfaForPrivilegedRoles", "sessionRevocation"],
        "authorization": ["serverEnforced", "defaultDeny", "objectAndScopeChecks"],
        "audit": ["privilegedMutations", "capturesActorTargetReasonResult", "tamperProtected"],
        "safety": ["riskClassifiedActions", "recoveryDefined"],
        "data": ["releaseUsesRealSources", "sensitiveFieldsClassified", "retentionDefined"],
        "experience": [
            "keyboardCriticalFlowsTested",
            "responsiveCriticalFlowsTested",
            "localizationRequirementsHandled",
        ],
        "observability": [
            "adminApiInstrumented",
            "highRiskActionsDetectable",
            "correlationIdsExposedSafely",
        ],
    }
    if platform.get("tenancy") in ("multi-tenant", "hybrid"):
        required_true["data"].append("tenantIsolationTested")
    if any(cap.get("risk") in ("high", "critical") for cap in implemented):
        required_true["authentication"].append("stepUpForSensitiveActions")
    if any(cap.get("kind") in MUTATING_KINDS for cap in implemented):
        required_true["audit"].append("capturesSafeBeforeAfter")
    if as_list(manifest.get("workQueues")) or as_list(manifest.get("integrations")) or any(
        cap.get("kind") == "job" for cap in implemented
    ):
        required_true["observability"].append("jobsAndIntegrationsMonitored")

    for section, keys in required_true.items():
        values = cross.get(section)
        if not isinstance(values, dict):
            findings.add("manifest-type", "crossCutting." + section, "must be an object")
            continue
        for key in keys:
            if values.get(key) is not True:
                findings.add(
                    "crosscutting-flag",
                    "crossCutting." + section + "." + key,
                    "must be true for release",
                )
        path = "crossCutting." + section + ".evidence"
        if not nonempty(values.get("evidence")):
            findings.add(
                "crosscutting-evidence",
                path,
                "a boolean is not evidence; record at least one artifact that proves this section",
            )
        else:
            check_evidence_list(
                values.get("evidence"), path, findings, project_root, True,
                role="cross", owner=section,
            )

    authorization = as_dict(cross.get("authorization"))
    policy_path = "crossCutting.authorization.policyTests"
    if not nonempty(authorization.get("policyTests")):
        findings.add(
            "authorization-policy-tests",
            policy_path,
            "record the authorization test files that prove default-deny and scope checks",
        )
    else:
        check_evidence_list(
            authorization.get("policyTests"), policy_path, findings, project_root, True,
            expect_assertions=True, role="policy-test", owner="crossCutting.authorization",
        )

    audit = as_dict(cross.get("audit"))
    regulated = bool(as_list(platform.get("regulatedData")))
    if findings.profile == "regulated" or (findings.profile == "standard" and regulated):
        if audit.get("privilegedReadsWhenRequired") is not True:
            findings.add(
                "privileged-read-audit",
                "crossCutting.audit.privilegedReadsWhenRequired",
                "privileged reads of regulated data must be audited",
            )


def validate_gates(manifest, findings, release, project_root):
    gates = {}
    for path, gate in iter_named(manifest, "qualityGates"):
        gid = text_of(gate.get("id"))
        if gid:
            gates[gid] = (path, gate)
        check_required(gate, ("id", "status", "threshold", "evidence", "rationale"), path, findings)
        check_enum(gate.get("status"), GATE_STATUSES, path + ".status", findings, "status")
        status = gate.get("status")
        if status == "passed":
            if not nonempty(gate.get("evidence")):
                findings.add("gate-evidence-missing", path + ".evidence", "gate is passed without evidence")
            else:
                check_evidence_list(
                    gate.get("evidence"),
                    path + ".evidence",
                    findings,
                    project_root,
                    True,
                    missing_rule="gate-evidence-path",
                    role="gate",
                    owner=gid,
                )
            if not nonempty(gate.get("threshold")):
                findings.add("required-field-empty", path + ".threshold", "a passed gate needs a measurable threshold")
        if status == "not-applicable" and not nonempty(gate.get("rationale")):
            findings.add("gate-rationale-missing", path + ".rationale", "gate is not-applicable without rationale")
        if release and status not in ("passed", "not-applicable"):
            findings.add("gate-not-passed", path + ".status", "is '" + str(status) + "', not passed")
    for gid in REQUIRED_GATES:
        if gid not in gates:
            findings.add("gate-missing", "qualityGates", "missing required gate '" + gid + "'")
    if release:
        for gid in NONFUNCTIONAL_GATES:
            entry = gates.get(gid)
            if entry and entry[1].get("status") != "passed":
                findings.add(
                    "nonfunctional-gate-required",
                    entry[0] + ".status",
                    "the " + gid + " gate must pass at this profile; not-applicable is only allowed at profile internal",
                )


def validate_decisions(manifest, findings, release):
    caps = capability_index(manifest)
    known = set(caps)
    for key in ("screens", "entities", "workQueues", "integrations", "roles", "gaps"):
        for _path, item in iter_named(manifest, key):
            value = text_of(item.get("id"))
            if value:
                known.add(value)
    for path, decision in iter_named(manifest, "decisions"):
        check_required(decision, ("id", "decision", "reason", "evidence", "status"), path, findings)
        check_enum(decision.get("status"), DECISION_STATUSES, path + ".status", findings, "status")
        if not nonempty(decision.get("reason")):
            findings.add("decision-reason-missing", path + ".reason", "every decision must record why it was taken")
        unknown = sorted(
            value
            for value in as_list(decision.get("appliesTo"))
            if isinstance(value, str) and value != "*" and value not in known
        )
        if unknown:
            findings.add(
                "decision-applies-to-unknown",
                path + ".appliesTo",
                "references unknown ids: " + ", ".join(unknown),
            )
        if release and decision.get("status") == "assumed":
            findings.add(
                "decision-assumed",
                path + ".status",
                "is still assumed at release; confirm it with the owner or record it as a gap",
            )


def validate_gaps(manifest, findings, release):
    for path, gap in iter_named(manifest, "gaps"):
        check_required(gap, ("id", "severity", "description", "status", "rationale", "evidence"), path, findings)
        check_enum(gap.get("severity"), GAP_SEVERITIES, path + ".severity", findings, "severity")
        check_enum(gap.get("status"), GAP_STATUSES, path + ".status", findings, "status")
        if gap.get("status") == "accepted" and not nonempty(gap.get("rationale")):
            findings.add("gap-rationale-missing", path + ".rationale", "an accepted gap needs the accepted-risk rationale")
        if release and gap.get("severity") in ("critical", "high") and gap.get("status") not in ("fixed", "accepted"):
            findings.add(
                "gap-unresolved",
                path + ".status",
                "unresolved " + str(gap.get("severity")) + " gap blocks release",
            )
    for path, item in iter_named(manifest, "feedback"):
        check_required(item, ("id", "observation", "category", "proposedChange", "evidence", "status"), path, findings)
        check_enum(item.get("category"), FEEDBACK_CATEGORIES, path + ".category", findings, "category")
        check_enum(item.get("status"), FEEDBACK_STATUSES, path + ".status", findings, "status")
        scope_problem = skill_scope_problem(item.get("scope")) if item.get("status") == "open" else None
        if scope_problem:
            findings.add(
                "feedback-scope",
                path + ".scope",
                scope_problem + "; " + SKILL_SCOPE_HINT + ". An entry without a valid scope "
                "cannot be grouped with the same finding from another project",
            )
    # With the feedback checks because only `harvest` reads it: it is how the
    # promotion bar tells one project from another.
    check_platform_id(manifest, findings)


def validate_agents(manifest, findings):
    caps = capability_index(manifest)
    screen_ids = {text_of(screen.get("id")) for _p, screen in iter_named(manifest, "screens")}
    for path, agent in iter_named(manifest, "agents"):
        check_required(agent, ("id", "role", "ownsCapabilities", "ownsScreens", "status"), path, findings)
        check_enum(agent.get("role"), AGENT_ROLES, path + ".role", findings, "role")
        check_enum(agent.get("status"), AGENT_STATUSES, path + ".status", findings, "status")
        unknown = sorted(
            value for value in as_list(agent.get("ownsCapabilities")) if isinstance(value, str) and value not in caps
        )
        if unknown:
            findings.add("unknown-capability-reference", path + ".ownsCapabilities", "unknown capabilities: " + ", ".join(unknown))
        unknown_screens = sorted(
            value for value in as_list(agent.get("ownsScreens")) if isinstance(value, str) and value not in screen_ids
        )
        if unknown_screens:
            findings.add("unknown-capability-reference", path + ".ownsScreens", "unknown screens: " + ", ".join(unknown_screens))


def contested_gap_exists(manifest, capability_id):
    """An open or blocked gap that names the capability a reviewer contested."""
    for _path, gap in iter_named(manifest, "gaps"):
        if gap.get("status") not in ("open", "blocked"):
            continue
        if text_of(gap.get("capability")) == capability_id:
            return True
        if capability_id and capability_id in (
            text_of(gap.get("description")) + " " + text_of(gap.get("rationale"))
        ):
            return True
    return False


def implementer_ids(manifest, capability):
    """Every id that built or held this capability, lowercased.

    `owner` alone is not enough: release-claim (step 5 of every implementer's
    exit protocol) blanks it, after which "who implemented this" was recorded
    nowhere and the implementer could review its own work.
    """
    cid = text_of(capability.get("id"))
    names = {text_of(capability.get("owner")), text_of(capability.get("implementedBy"))}
    for _path, agent in iter_named(manifest, "agents"):
        if text_of(agent.get("role")) != "implementer":
            continue
        held = as_list(agent.get("ownsCapabilities")) + as_list(agent.get("previouslyOwned"))
        if cid and cid in held:
            names.add(text_of(agent.get("id")))
    return {name.lower() for name in names if name}


def check_reviewer_identity(manifest, capability, path, findings):
    """Verify the reviewer is not the implementer.

    `capability-unreviewed` claimed review was done "by an agent other than its
    implementer", but nothing recorded who reviewed, so the same agent could set
    reviewStatus on its own work. reviewedBy makes the claim checkable.
    """
    if capability.get("reviewStatus") != "reviewed":
        return
    reviewer = text_of(capability.get("reviewedBy"))
    if not reviewer:
        findings.add(
            "reviewer-identity",
            path + ".reviewedBy",
            "reviewStatus is 'reviewed' but no reviewer is recorded; "
            "an unattributed review cannot be distinguished from self-review",
        )
        return
    if reviewer.lower() in implementer_ids(manifest, capability):
        findings.add(
            "reviewer-identity",
            path + ".reviewedBy",
            "reviewer '" + reviewer + "' is the implementer (owner, implementedBy, or an "
            "implementer that held this capability); review must be a separate pass "
            "by a different agent",
        )


def validate_evidence(manifest, findings, project_root):
    """Release-phase file checks for capability, screen, and queue evidence."""
    for path, _entity, capability in iter_capabilities(manifest):
        if capability.get("status") != "implemented":
            continue
        cid = text_of(capability.get("id"))
        check_evidence_list(
            capability.get("policyTests"), path + ".policyTests", findings, project_root, True,
            expect_assertions=True, role="policy-test", owner=cid,
        )
        test_paths = check_evidence_list(
            capability.get("tests"), path + ".tests", findings, project_root, True,
            expect_assertions=True, role="capability-test", owner=cid,
        )
        check_evidence_list(
            capability.get("evidence"), path + ".evidence", findings, project_root, True,
            role="capability-evidence", owner=cid,
        )
        if findings.severity_for("evidence-token-match") == OFF:
            continue
        tokens = capability_tokens(capability)
        if not tokens or not test_paths:
            continue
        matched = False
        for candidate in test_paths:
            body = read_head(candidate).lower()
            if any(token.lower() in body for token in tokens):
                matched = True
                break
        if not matched:
            findings.add(
                "evidence-token-match",
                path + ".tests",
                "no listed test file mentions the capability id or one of its server operations; "
                "the link between manifest and test is unproven",
            )
    for path, screen in iter_named(manifest, "screens"):
        if screen.get("status") != "implemented":
            continue
        screen_tests = check_evidence_list(
            screen.get("tests"), path + ".tests", findings, project_root, True,
            expect_assertions=True, role="screen-test", owner=text_of(screen.get("id")),
        )
        # Same asymmetry the capability check closes: a screen could cite a real
        # but unrelated test file forever.
        if findings.severity_for("evidence-token-match") == OFF:
            continue
        screen_tokens = {
            token
            for token in (text_of(screen.get("id")), text_of(screen.get("route")))
            if token
        }
        if not screen_tokens or not screen_tests:
            continue
        if not any(
            token.lower() in read_head(candidate).lower()
            for candidate in screen_tests
            for token in screen_tokens
        ):
            findings.add(
                "evidence-token-match",
                path + ".tests",
                "no listed test file mentions the screen id or its route; "
                "the link between manifest and test is unproven",
            )
    for key in ("workQueues", "integrations"):
        for path, item in iter_named(manifest, key):
            if item.get("status") == "implemented":
                check_evidence_list(
                    item.get("tests"), path + ".tests", findings, project_root, True,
                    expect_assertions=True, role="item-test", owner=text_of(item.get("id")),
                )
    for path, gap in iter_named(manifest, "gaps"):
        if gap.get("status") == "fixed":
            entries = gap.get("evidence")
            if text_of(gap.get("source")).lower() == "ocr" and isinstance(entries, list):
                # The OCR bridge cites its own review record (kept beside the
                # manifest, and git-ignored) as evidence of the FINDING. That is a
                # provenance note, not proof of a fix, so it is neither required to
                # resolve nor accepted as proof; anything else it lists still is.
                entries = [
                    entry for entry in entries
                    if not (
                        isinstance(entry, str)
                        and evidence_kind(entry) == "path"
                        and evidence_path(entry, project_root) is not None
                        and inside_manifest_dir(evidence_path(entry, project_root), findings.manifest_dir)
                    )
                ]
            check_evidence_list(
                entries, path + ".evidence", findings, project_root, True,
                role="gap-evidence", owner=text_of(gap.get("id")),
            )


GENERIC_COMMAND_WORDS = frozenset(
    "npm npx run yarn pnpm exec node python python3 py bun sh bash make the and".split()
)


def gate_tokens(gate):
    tokens = {text_of(gate.get("id")).lower()}
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", text_of(gate.get("command"))):
        if word.lower() not in GENERIC_COMMAND_WORDS:
            tokens.add(word.lower())
    tokens.discard("")
    return tokens


def check_evidence_sharing(manifest, findings):
    """One file standing in for many claims.

    A six-line package.json cited for six quality gates, a README.md for every
    crossCutting section, or one route-smoke spec cited by every capability all
    satisfied the per-file checks: each file exists, is non-empty, and asserts
    something. Whether a file is being asked to prove too much is a property of
    the whole manifest, so it is judged here, after every list has been resolved.
    """
    by_path = {}
    for entry in findings.evidence_index:
        by_path.setdefault(entry["path"], []).append(entry)
    test_roles = ("capability-test", "screen-test", "item-test", "policy-test")
    for _path, uses in sorted(by_path.items()):
        roles = {use["role"] for use in uses}
        label = uses[0]["raw"]
        evidence_uses = [use for use in uses if use["role"] in ("gate", "cross")]
        if evidence_uses and roles.intersection(test_roles):
            findings.add(
                "evidence-is-test-file",
                evidence_uses[0]["field"],
                "'" + label + "' is cited as gate or crossCutting evidence and as a test; "
                "evidence is the recorded output of running something, not the test's source",
            )
        gate_owners = sorted({use["owner"] for use in uses if use["role"] == "gate"})
        cross_owners = sorted({use["owner"] for use in uses if use["role"] == "cross"})
        if len(gate_owners) > 2 or (gate_owners and cross_owners):
            first = evidence_uses[0]
            findings.add(
                "evidence-shared",
                first["field"],
                "'" + label + "' is the evidence for " + ", ".join(gate_owners + cross_owners)
                + "; one file cannot prove that many independent claims",
            )
        cap_uses = [use for use in uses if use["role"] == "capability-test"]
        cap_owners = sorted({use["owner"] for use in cap_uses})
        screen_owners = {use["owner"] for use in uses if use["role"] == "screen-test"}
        if len(cap_owners) > 3 or (cap_owners and screen_owners and "policy-test" in roles):
            findings.add(
                "test-file-shared",
                cap_uses[0]["field"],
                "'" + label + "' is the test for " + str(len(cap_owners)) + " capabilities"
                + (", screens and authorization policy" if screen_owners and "policy-test" in roles else "")
                + "; a route-smoke loop is not a test of each of them",
            )
        if cap_owners and findings.profile == "regulated":
            asserted = assertion_count(read_head(Path(uses[0]["path"])))
            if asserted < len(cap_owners):
                findings.add(
                    "test-asserts-nothing",
                    cap_uses[0]["field"],
                    "'" + label + "' holds " + str(asserted) + " assertion(s) for "
                    + str(len(cap_owners)) + " capabilities; each needs at least one",
                )
    for path, gate in iter_named(manifest, "qualityGates"):
        gid = text_of(gate.get("id"))
        owned = [
            entry for entry in findings.evidence_index
            if entry["role"] == "gate" and entry["owner"] == gid
        ]
        if not owned:
            continue
        tokens = gate_tokens(gate)
        related = False
        for entry in owned:
            body = read_head(Path(entry["path"])).lower()
            if any(token in body for token in tokens):
                related = True
                break
        if not related:
            findings.add(
                "gate-evidence-unrelated",
                path + ".evidence",
                "no evidence file for gate '" + gid + "' mentions '" + gid + "'"
                + (" or its command" if text_of(gate.get("command")) else "")
                + "; record the gate's own output (the `command` field names what ran)",
            )


# --------------------------------------------------------------------------
# coverage audit
# --------------------------------------------------------------------------


def coverage_findings(manifest, profile):
    findings = Findings(profile)
    structural_findings(manifest, findings, True)
    return findings


# --------------------------------------------------------------------------
# markdown emitters
# --------------------------------------------------------------------------


def cell(value):
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    text = str(value if value is not None else "").replace("|", "\\|")
    return " ".join(text.split()) or "-"


def emit_authz_matrix(manifest):
    roles = {text_of(role.get("id")): role for _p, role in iter_named(manifest, "roles")}
    lines = ["# Authorization matrix", ""]
    lines.append("| Role | Scopes | Capability | Entity | Kind | Risk | Policies | Obligations |")
    lines.append("|---|---|---|---|---|---|---|---|")
    rows = 0
    for _path, entity, capability in iter_capabilities(manifest):
        obligations = []
        obligations.extend(as_list(capability.get("auditEvents")))
        obligations.extend(as_list(capability.get("safeguards")))
        if capability.get("risk") in ("high", "critical"):
            obligations.append("step-up or approval required by risk")
        for role_id in str_items(capability.get("roles")):
            role = as_dict(roles.get(role_id))
            lines.append(
                "| "
                + " | ".join(
                    [
                        cell(role_id),
                        cell(role.get("scopes")),
                        cell(capability.get("id")),
                        cell(entity.get("id")),
                        cell(capability.get("kind")),
                        cell(capability.get("risk")),
                        cell(capability.get("authorizationPolicies")),
                        cell(obligations),
                    ]
                )
                + " |"
            )
            rows += 1
    if not rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    orphans = [
        text_of(capability.get("id"))
        for _p, _e, capability in iter_capabilities(manifest)
        if not as_list(capability.get("roles"))
    ]
    lines.extend(["", "## Capabilities with no actor role", ""])
    if orphans:
        lines.extend("- " + item for item in orphans)
    else:
        lines.append("- none")
    lines.extend(["", "## Denied-by-default expectation", ""])
    lines.append("Any role absent from a capability row must be rejected by the server, not hidden in the UI.")
    return "\n".join(lines) + "\n"


def emit_test_plan(manifest):
    role_ids = [text_of(role.get("id")) for _p, role in iter_named(manifest, "roles")]
    lines = ["# Test plan", ""]
    lines.append("Every unchecked box is missing coverage. Negative tests are not optional.")
    for eid, entity in iter_entities(manifest):
        lines.extend(["", "## entity: " + eid, ""])
        states = as_list(entity.get("lifecycleStates"))
        if states:
            lines.append("- [ ] fixtures exist for every lifecycle state: " + ", ".join(str(s) for s in states))
        if entity.get("tenantScoped") is True:
            lines.append("- [ ] cross-tenant read and write of " + eid + " is rejected server-side")
        capabilities = [cap for cap in as_list(entity.get("capabilities")) if isinstance(cap, dict)]
        if not capabilities:
            lines.append("- [ ] no capability modeled; entity is unmanaged")
        for capability in capabilities:
            cid = text_of(capability.get("id"))
            allowed = [value for value in as_list(capability.get("roles")) if isinstance(value, str)]
            lines.extend(
                [
                    "",
                    "### " + cid + " (" + str(capability.get("kind")) + ", risk " + str(capability.get("risk")) + ")",
                    "",
                ]
            )
            for role_id in allowed:
                lines.append("- [ ] positive: `" + role_id + "` achieves \"" + str(capability.get("outcome")) + "\"")
            for role_id in role_ids:
                if role_id and role_id not in allowed:
                    lines.append("- [ ] negative: `" + role_id + "` is rejected by the server for " + cid)
            lines.append("- [ ] negative: an unauthenticated request to " + cell(capability.get("serverOperations")) + " is rejected")
            if capability.get("kind") in MUTATING_KINDS:
                lines.append("- [ ] audit: " + cell(capability.get("auditEvents")) + " records actor, target, reason, result")
                lines.append("- [ ] idempotency: repeating the operation does not double-apply (" + cell(capability.get("idempotency")) + ")")
            if capability.get("risk") in ("high", "critical"):
                lines.append("- [ ] safeguard: " + cell(capability.get("safeguards")) + " blocks the unconfirmed path")
                lines.append("- [ ] recovery: " + cell(capability.get("recovery")) + " restores the prior state")
            entity_states = as_dict(capability.get("entityStates"))
            if as_list(entity_states.get("from")):
                lines.append("- [ ] precondition: the operation is rejected from states outside " + cell(entity_states.get("from")))
            if nonempty(capability.get("concurrency")):
                lines.append("- [ ] concurrency: " + cell(capability.get("concurrency")))
    lines.extend(["", "## screens", ""])
    for _path, screen in iter_named(manifest, "screens"):
        route = text_of(screen.get("route"))
        declared = [value for value in as_list(screen.get("states")) if isinstance(value, str)]
        required = list(REQUIRED_SCREEN_STATES)
        if as_list(screen.get("actions")):
            required.append("success")
        for state in required:
            mark = "x" if state in declared else " "
            lines.append("- [" + mark + "] " + route + " renders the " + state + " state")
        for state in declared:
            if state not in required:
                lines.append("- [ ] " + route + " renders the " + state + " state")
        lines.append("- [ ] " + route + " is operable by keyboard only")
        lines.append("- [ ] " + route + " is usable at the project's smallest supported viewport")
    return "\n".join(lines) + "\n"


def screen_domain(screen):
    route = text_of(screen.get("route")).strip("/")
    parts = [
        part
        for part in route.split("/")
        if part and not part.startswith((":", "{", "[", "*", "$"))
    ]
    generic = ("admin", "admin-console", "backoffice", "back-office", "console", "dashboard", "ops", "internal")
    while parts and parts[0].lower() in generic and len(parts) > 1:
        parts = parts[1:]
    return parts[0] if parts else "root"


def emit_nav_map(manifest):
    lines = ["# Navigation map", ""]
    grouped = {}
    for _path, screen in iter_named(manifest, "screens"):
        grouped.setdefault(screen_domain(screen), []).append(screen)
    if not grouped:
        lines.append("- no screens modeled")
        return "\n".join(lines) + "\n"
    for domain in sorted(grouped):
        lines.append("- **" + domain + "**")
        for screen in sorted(grouped[domain], key=lambda item: text_of(item.get("route"))):
            lines.append(
                "  - `"
                + text_of(screen.get("route"))
                + "` "
                + text_of(screen.get("id"))
                + " [roles: "
                + cell(screen.get("roles"))
                + "] ("
                + str(screen.get("status"))
                + ")"
            )
            lines.append("    - purpose: " + cell(screen.get("purpose")))
            lines.append("    - capabilities: " + cell(screen.get("capabilities")))
            lines.append("    - states: " + cell(screen.get("states")))
    return "\n".join(lines) + "\n"


SEED_CASES = (
    ("empty", "no records at all, so the empty state is exercised"),
    ("large", "enough records to force pagination, virtualization, and the performance budget"),
    ("long-text", "maximum-length and non-Latin text in every displayed field"),
    ("missing-optional", "records with every optional field null or absent"),
    ("conflict", "two operators editing the same record, producing a version conflict"),
    ("partial-failure", "a bulk or job run where some items succeed and some fail"),
)


def emit_seed_plan(manifest):
    lines = ["# Seed and fixture plan", ""]
    lines.append("Build these fixtures before verification. Fixtures are real records in a real store, not literals in the UI.")
    lines.extend(["", "## Role accounts", "", "| Role | Scopes | MFA | Purpose |", "|---|---|---|---|"])
    rows = 0
    for _path, role in iter_named(manifest, "roles"):
        lines.append(
            "| "
            + " | ".join(
                [
                    cell(role.get("id")),
                    cell(role.get("scopes")),
                    cell(role.get("mfaRequired")),
                    cell(role.get("responsibilities")),
                ]
            )
            + " |"
        )
        rows += 1
    if not rows:
        lines.append("| - | - | - | - |")
    for eid, entity in iter_entities(manifest):
        lines.extend(["", "## entity: " + eid, "", "| Fixture | Requirement |", "|---|---|"])
        for state in as_list(entity.get("lifecycleStates")):
            lines.append("| state:" + cell(state) + " | at least one record resting in this state |")
        for name, requirement in SEED_CASES:
            lines.append("| " + name + " | " + requirement + " |")
        if entity.get("tenantScoped") is True:
            lines.append("| cross-tenant | records owned by a second tenant that must never be visible |")
        if entity.get("sensitivity") in ("confidential", "restricted"):
            lines.append("| sensitive | records carrying the fields that must be redacted or access-logged |")
    integrations = list(iter_named(manifest, "integrations"))
    if integrations:
        lines.extend(["", "## Integrations", "", "| Integration | Fixture |", "|---|---|"])
        for _path, integration in integrations:
            lines.append("| " + cell(integration.get("id")) + " | provider timeout, duplicate delivery, and reconciliation mismatch |")
    queues = list(iter_named(manifest, "workQueues"))
    if queues:
        lines.extend(["", "## Work queues", "", "| Queue | Fixture |", "|---|---|"])
        for _path, queue in queues:
            lines.append("| " + cell(queue.get("id")) + " | backlog past its SLA, an empty queue, and one item claimed by another operator |")
    return "\n".join(lines) + "\n"


def emit_operator_handbook(manifest):
    lines = ["# Operator handbook", ""]
    caps_by_role = {}
    for _path, entity, capability in iter_capabilities(manifest):
        for role_id in str_items(capability.get("roles")):
            caps_by_role.setdefault(role_id, []).append((entity, capability))
    screens_by_role = {}
    for _path, screen in iter_named(manifest, "screens"):
        for role_id in str_items(screen.get("roles")):
            screens_by_role.setdefault(role_id, []).append(screen)
    roles = list(iter_named(manifest, "roles"))
    if not roles:
        lines.append("No roles modeled.")
        return "\n".join(lines) + "\n"
    for _path, role in roles:
        rid = text_of(role.get("id"))
        lines.extend(["", "## " + cell(role.get("name")) + " (`" + rid + "`)", ""])
        lines.append("- Responsibilities: " + cell(role.get("responsibilities")))
        lines.append("- Scopes: " + cell(role.get("scopes")))
        lines.append("- MFA required: " + cell(role.get("mfaRequired")))
        if nonempty(role.get("separationOfDuties")):
            lines.append("- Separation of duties: " + cell(role.get("separationOfDuties")))
        lines.extend(["", "### Where they work", ""])
        assigned = screens_by_role.get(rid, [])
        if assigned:
            for screen in sorted(assigned, key=lambda item: text_of(item.get("route"))):
                lines.append("- `" + text_of(screen.get("route")) + "` " + cell(screen.get("purpose")))
        else:
            lines.append("- no screen grants this role access")
        lines.extend(["", "### What they can do", "", "| Capability | Entity | Kind | Risk | Safeguards | Recovery |", "|---|---|---|---|---|---|"])
        entries = caps_by_role.get(rid, [])
        if not entries:
            lines.append("| - | - | - | - | - | - |")
        for entity, capability in entries:
            lines.append(
                "| "
                + " | ".join(
                    [
                        cell(capability.get("id")),
                        cell(entity.get("id")),
                        cell(capability.get("kind")),
                        cell(capability.get("risk")),
                        cell(capability.get("safeguards")),
                        cell(capability.get("recovery")),
                    ]
                )
                + " |"
            )
        escalations = [
            capability
            for _entity, capability in entries
            if capability.get("risk") in ("high", "critical")
        ]
        if escalations:
            lines.extend(["", "### Before a high-risk action", ""])
            for capability in escalations:
                lines.append(
                    "- "
                    + cell(capability.get("id"))
                    + ": confirm the target, capture a reason, expect "
                    + cell(capability.get("auditEvents"))
                    + "; recover with "
                    + cell(capability.get("recovery"))
                )
    return "\n".join(lines) + "\n"


def emit_gap_report(manifest, profile):
    lines = ["# Gap report", "", "Profile: " + profile, ""]
    lines.append("## Recorded gaps")
    lines.append("")
    recorded = list(iter_named(manifest, "gaps"))
    if not recorded:
        lines.append("- none recorded")
    for severity in GAP_SEVERITIES:
        rows = [gap for _p, gap in recorded if gap.get("severity") == severity]
        if not rows:
            continue
        lines.extend(["", "### " + severity, "", "| Gap | Status | Description | Rationale |", "|---|---|---|---|"])
        for gap in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        cell(gap.get("id")),
                        cell(gap.get("status")),
                        cell(gap.get("description")),
                        cell(gap.get("rationale")),
                    ]
                )
                + " |"
            )
    lines.extend(["", "## Declared static", ""])
    entries = list(iter_named(manifest, "declaredStatic"))
    if not entries:
        lines.append("- none declared")
    else:
        lines.extend(["| Path | Value | Reason | Approved by |", "|---|---|---|---|"])
        for _path, entry in entries:
            lines.append(
                "| "
                + " | ".join(
                    [
                        cell(entry.get("path")),
                        cell(entry.get("value")),
                        cell(entry.get("reason")),
                        cell(entry.get("approvedBy")),
                    ]
                )
                + " |"
            )
    findings = coverage_findings(manifest, profile)
    lines.extend(["", "## Structural findings", ""])
    if not findings.items:
        lines.append("- none")
    for severity in (ERROR, WARN):
        rows = [item for item in findings.sorted_items() if item["severity"] == severity]
        if not rows:
            continue
        lines.extend(["", "### " + severity, "", "| Rule | Path | Finding |", "|---|---|---|"])
        for item in rows:
            lines.append("| " + cell(item["rule"]) + " | " + cell(item["path"]) + " | " + cell(item["message"]) + " |")
    return "\n".join(lines) + "\n"


def emit_rule_matrix():
    """RULES as a markdown table, so the published matrix cannot drift from the code."""
    lines = [
        "| Rule | internal | standard | regulated |",
        "|---|---|---|---|",
    ]
    for name in sorted(RULES):
        tiers = RULES[name]
        mark = "*" if name in COMPLETENESS_RULES else ""
        lines.append(
            "| `" + name + "`" + mark + " | " + " | ".join(tiers) + " |"
        )
    return "\n".join(lines) + "\n"


EMITTERS = {
    "authz-matrix": lambda manifest, profile: emit_authz_matrix(manifest),
    "test-plan": lambda manifest, profile: emit_test_plan(manifest),
    "nav-map": lambda manifest, profile: emit_nav_map(manifest),
    "seed-plan": lambda manifest, profile: emit_seed_plan(manifest),
    "operator-handbook": lambda manifest, profile: emit_operator_handbook(manifest),
    "gap-report": emit_gap_report,
}


# --------------------------------------------------------------------------
# migration
# --------------------------------------------------------------------------


def ensure_key(obj, key, default, path, changes):
    if key not in obj:
        obj[key] = default
        changes.append(path + "." + key)
        return True
    return False


def migrate_manifest(manifest):
    changes = []
    version = manifest.get("manifestVersion")
    if version == MANIFEST_VERSION:
        return manifest, changes
    if version not in LEGACY_VERSIONS:
        raise ManifestError(
            "manifestVersion '" + str(version) + "' cannot be migrated; expected one of: "
            + ", ".join(LEGACY_VERSIONS)
        )
    manifest["manifestVersion"] = MANIFEST_VERSION
    changes.append("manifestVersion -> " + MANIFEST_VERSION)
    ensure_key(manifest, "profile", DEFAULT_PROFILE, "manifest", changes)

    platform = manifest.setdefault("platform", {})
    if not isinstance(platform, dict):
        raise ManifestError("cannot migrate: platform must be an object")
    if not isinstance(manifest.get("crossCutting", {}), dict):
        raise ManifestError("cannot migrate: crossCutting must be an object")
    ensure_key(
        platform,
        "stack",
        {
            "frontend": "",
            "backend": "",
            "database": "",
            "auth": "",
            "jobs": "",
            "hosting": "",
            "designSystem": "",
            "adminFramework": "",
        },
        "platform",
        changes,
    )
    ensure_key(platform, "researchSources", [], "platform", changes)
    ensure_key(
        platform,
        "volumes",
        {"entityCounts": "", "peakConcurrentOperators": "", "retentionHorizon": ""},
        "platform",
        changes,
    )

    for index, role in enumerate(as_list(manifest.get("roles"))):
        if isinstance(role, dict):
            path = "roles[" + str(index) + "]"
            ensure_key(role, "authenticationStrength", "", path, changes)
            ensure_key(role, "separationOfDuties", [], path, changes)

    for index, entity in enumerate(as_list(manifest.get("entities"))):
        if not isinstance(entity, dict):
            continue
        path = "entities[" + str(index) + "]"
        ensure_key(entity, "lifecycleTransitions", [], path, changes)
        for cap_index, capability in enumerate(as_list(entity.get("capabilities"))):
            if not isinstance(capability, dict):
                continue
            cap_path = path + ".capabilities[" + str(cap_index) + "]"
            ensure_key(capability, "entityStates", {"from": [], "to": []}, cap_path, changes)
            for key in (
                "dataBinding", "idempotency", "concurrency", "recovery", "owner", "implementedBy",
            ):
                ensure_key(capability, key, "", cap_path, changes)
            ensure_key(capability, "reviewStatus", "unreviewed", cap_path, changes)
            ensure_key(capability, "surface", ["ui"], cap_path, changes)
            ensure_key(capability, "entrypoints", [], cap_path, changes)
            ensure_key(capability, "policyTests", [], cap_path, changes)

    for index, queue in enumerate(as_list(manifest.get("workQueues"))):
        if isinstance(queue, dict):
            ensure_key(queue, "tests", [], "workQueues[" + str(index) + "]", changes)

    for index, integration in enumerate(as_list(manifest.get("integrations"))):
        if isinstance(integration, dict):
            path = "integrations[" + str(index) + "]"
            ensure_key(integration, "credentialBoundary", "", path, changes)
            ensure_key(integration, "roles", [], path, changes)
            ensure_key(integration, "tests", [], path, changes)

    cross = manifest.setdefault("crossCutting", {})
    for section in CROSS_SECTIONS:
        values = cross.setdefault(section, {})
        if isinstance(values, dict):
            ensure_key(values, "evidence", [], "crossCutting." + section, changes)
    authorization = cross.get("authorization")
    if isinstance(authorization, dict):
        ensure_key(authorization, "policyTests", [], "crossCutting.authorization", changes)

    for index, decision in enumerate(as_list(manifest.get("decisions"))):
        if isinstance(decision, dict):
            ensure_key(decision, "appliesTo", [], "decisions[" + str(index) + "]", changes)

    # Every collection the 2.0 schema requires, not only the three 2.0 introduced: a
    # 1.0 manifest without `decisions` migrated to something the schema rejects.
    for key in COLLECTION_KEYS:
        ensure_key(manifest, key, [], "manifest", changes)

    ordered = {}
    for key in TOP_LEVEL_KEYS:
        if key in manifest:
            ordered[key] = manifest[key]
    for key in manifest:
        if key not in ordered:
            ordered[key] = manifest[key]
    if "$schema" in manifest:
        reordered = {"$schema": manifest["$schema"]}
        for key, value in ordered.items():
            if key != "$schema":
                reordered[key] = value
        ordered = reordered
    return ordered, changes


# --------------------------------------------------------------------------
# add / set
# --------------------------------------------------------------------------


KIND_TARGETS = {
    "role": "roles",
    "entity": "entities",
    "capability": "capabilities",
    "screen": "screens",
    "queue": "workQueues",
    "integration": "integrations",
    "decision": "decisions",
    "gap": "gaps",
    "static": "declaredStatic",
    "feedback": "feedback",
    "agent": "agents",
}

DEFAULTS = {
    "role": {
        "id": "",
        "name": "",
        "responsibilities": [],
        "scopes": [],
        "mfaRequired": False,
        "authenticationStrength": "",
        "separationOfDuties": [],
    },
    "entity": {
        "id": "",
        "name": "",
        "sourceOfTruth": "",
        "sensitivity": "internal",
        "tenantScoped": False,
        "lifecycleStates": [],
        "lifecycleTransitions": [],
        "retention": "",
        "capabilities": [],
    },
    "capability": {
        "id": "",
        "outcome": "",
        "kind": "query",
        "roles": [],
        "risk": "low",
        "status": "discovered",
        "rationale": "",
        "entityStates": {"from": [], "to": []},
        "uiRoutes": [],
        "serverOperations": [],
        "authorizationPolicies": [],
        "auditEvents": [],
        "safeguards": [],
        "dataBinding": "",
        "idempotency": "",
        "concurrency": "",
        "recovery": "",
        "tests": [],
        "evidence": [],
        "owner": "",
        "implementedBy": "",
        "reviewStatus": "unreviewed",
        "surface": ["ui"],
        "entrypoints": [],
        "policyTests": [],
    },
    "screen": {
        "id": "",
        "route": "",
        "purpose": "",
        "roles": [],
        "dataSources": [],
        "capabilities": [],
        "actions": [],
        "states": [],
        "responsive": False,
        "accessibilityStatus": "planned",
        "status": "planned",
        "rationale": "",
        "tests": [],
    },
    "queue": {
        "id": "",
        "purpose": "",
        "roles": [],
        "source": "",
        "priorityRule": "",
        "sla": "",
        "actions": [],
        "status": "planned",
        "rationale": "",
        "tests": [],
    },
    "integration": {
        "id": "",
        "direction": "outbound",
        "sourceOfTruth": "",
        "credentialBoundary": "",
        "roles": [],
        "operations": [],
        "failureHandling": "",
        "reconciliation": "",
        "monitoring": "",
        "status": "planned",
        "rationale": "",
        "tests": [],
    },
    "decision": {
        "id": "",
        "decision": "",
        "reason": "",
        "evidence": [],
        "status": "assumed",
        "appliesTo": [],
    },
    "gap": {
        "id": "",
        "severity": "medium",
        "description": "",
        "status": "open",
        "rationale": "",
        "evidence": [],
    },
    "static": {"id": "", "path": "", "value": "", "reason": "", "approvedBy": "", "evidence": []},
    "feedback": {
        "id": "",
        "observation": "",
        "category": "gap",
        "scope": "",
        "proposedChange": "",
        "evidence": [],
        "status": "open",
    },
    "agent": {
        "id": "",
        "role": "implementer",
        "ownsCapabilities": [],
        "ownsScreens": [],
        "previouslyOwned": [],
        "status": "active",
        "notes": "",
    },
}


def merge_defaults(kind, obj):
    defaults = DEFAULTS[kind]
    merged = {}
    for key, value in defaults.items():
        merged[key] = obj[key] if key in obj else (list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value)
    for key, value in obj.items():
        if key not in merged:
            merged[key] = value
    if kind == "entity" and isinstance(merged.get("capabilities"), list):
        # An entity added with its capabilities inline: each one gets the same
        # defaults `add --kind capability` would give it. Without this, four
        # undocumented required keys refused the whole entity.
        merged["capabilities"] = [
            merge_defaults("capability", item) if isinstance(item, dict) else item
            for item in merged["capabilities"]
        ]
    return merged


POWERSHELL_HINT = (
    "In PowerShell, double quotes inside single-quoted JSON are stripped before Python "
    "sees them. Write the JSON to a file and pass --json-file <path> (a UTF-8 byte-order "
    "mark is accepted), or --json '@<path>' with the @ argument quoted."
)


PATH_LIST_FIELDS = frozenset(("tests", "evidence", "policyTests"))


def normalise_path_list(values):
    """Rewrite `\\` as `/` in repository-path entries; return (new_values, changed)."""
    fixed = []
    changed = 0
    for value in values:
        if isinstance(value, str) and "\\" in value and evidence_kind(value) == "path":
            value = value.replace("\\", "/")
            changed += 1
        fixed.append(value)
    return fixed, changed


def normalise_paths(node, label):
    """Store evidence paths with forward slashes, so a manifest written on Windows
    validates the same on every machine. Prints one note when it changes anything."""
    total = 0
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in list(current.items()):
                if key in PATH_LIST_FIELDS and isinstance(value, list):
                    current[key], changed = normalise_path_list(value)
                    total += changed
                elif isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(item for item in current if isinstance(item, (dict, list)))
    if total:
        stderr(
            "note: rewrote backslashes as '/' in " + str(total) + " evidence path(s) of "
            + label + "; manifests store forward slashes"
        )


def parse_json_value(raw):
    text = raw
    if raw.startswith("@"):
        path = Path(raw[1:])
        try:
            text = decode_text_bytes(path.read_bytes(), path, allow_utf16=True)
        except OSError as exc:
            raise ManifestError("Cannot read --json file: " + str(exc))
    elif raw.strip() == "-":
        text = decode_text_bytes(sys.stdin.buffer.read(), "standard input", allow_utf16=True)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError("--json is not valid JSON: " + str(exc) + ". " + POWERSHELL_HINT)


def parse_json_argument(raw):
    value = parse_json_value(raw)
    if not isinstance(value, dict):
        raise ManifestError("--json must be a JSON object")
    return value


def parse_json_entries(raw):
    """Like parse_json_argument, but one object or an array of objects."""
    value = parse_json_value(raw)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return value
    raise ManifestError("--json must be a JSON object or a non-empty array of objects")


def new_findings(before, after, project_root, manifest_dir, renamed=None):
    """Errors and warnings an edit introduced, judged at plan phase.

    The guard MUST validate with the project root: without it a relative evidence
    path resolves to nothing, every passed gate raised `evidence-not-resolvable`,
    and a gate could never be `set` to `passed` from anywhere.

    Findings are compared as a multiset, not a set: with a set, once one duplicate
    id existed the next duplicate produced an identical signature and slipped
    through. `renamed=(old, new)` maps the before-findings of a renamed id onto
    the new one, so renaming an item that already carries errors is not blamed for
    them.
    """
    profile = active_profile(after, None)[0]
    seen = validate_manifest(before, "plan", project_root, profile, manifest_dir)
    now = validate_manifest(after, "plan", project_root, profile, manifest_dir)
    known = Counter()
    for item in seen.items:
        path, message = item["path"], item["message"]
        if renamed:
            old, new = renamed
            path = path.replace("[" + old + "]", "[" + new + "]")
            message = message.replace("'" + old + "'", "'" + new + "'")
        known[(item["rule"], path, message)] += 1
    fresh = []
    for item in now.items:
        key = (item["rule"], item["path"], item["message"])
        if known[key] > 0:
            known[key] -= 1
        else:
            fresh.append(item)
    return (
        [i for i in fresh if i["severity"] == ERROR],
        # Completeness rules describe an unfinished model, which every incremental
        # add produces (a new role is unused until its first capability exists);
        # echoing them after each write teaches agents to skip the warnings that
        # matter, so only real findings such as reviewer-identity are printed.
        [
            i for i in fresh
            if i["severity"] == WARN and i["rule"] != "plan-note" and i["rule"] not in COMPLETENESS_RULES
        ],
    )


def new_error_signatures(before, after, project_root=None, manifest_dir=None):
    """Return errors introduced by an edit, so partial writes cannot corrupt the manifest."""
    return new_findings(before, after, project_root, manifest_dir)[0]


MAX_WARNINGS_PRINTED = 8


def report_new_warnings(warnings):
    """Show what a successful write introduced, so a warning-tier problem is not silent."""
    for item in warnings[:MAX_WARNINGS_PRINTED]:
        stderr("WARN: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
    if len(warnings) > MAX_WARNINGS_PRINTED:
        stderr("WARN: ... and " + str(len(warnings) - MAX_WARNINGS_PRINTED) + " more; run `validate`")


def parse_path_expression(expression):
    # `validate` prints paths such as manifest.roles[ops].name; a finding's path
    # must be pasteable into `set --path` as it stands.
    if expression.startswith("manifest."):
        expression = expression[len("manifest."):]
    segments = []
    buffer = ""
    depth = 0
    for char in expression:
        if char == "[":
            depth += 1
            buffer += char
        elif char == "]":
            depth -= 1
            buffer += char
        elif char == "." and depth == 0:
            segments.append(buffer)
            buffer = ""
        else:
            buffer += char
    segments.append(buffer)
    parsed = []
    for segment in segments:
        if not segment:
            raise ManifestError("Empty segment in --path '" + expression + "'")
        match = re.match(r"^([A-Za-z0-9_]+)\[([^\]]+)\]$", segment)
        if match:
            parsed.append((match.group(1), match.group(2)))
            continue
        if not re.match(r"^[A-Za-z0-9_]+$", segment):
            raise ManifestError("Unsupported segment '" + segment + "' in --path")
        parsed.append((segment, None))
    return parsed


def select_item(items, selector, key):
    """An array element by id, or by index as the paths `validate` prints use."""
    for item in items:
        if isinstance(item, dict) and text_of(item.get("id")) == selector:
            return item
    index = selector[1:] if selector.startswith("#") else selector
    if index.isdigit() and int(index) < len(items) and isinstance(items[int(index)], dict):
        return items[int(index)]
    raise ManifestError("--path selector '" + selector + "' matches no item in '" + key + "'")


def resolve_path_parent(manifest, segments):
    """Walk to the container of the last segment.

    A selector is an item's id, or its array index (`[0]`, or `[#0]` for an item
    with no id yet): `validate` prints indexed paths and they must be pasteable.
    A last segment with a numeric selector names one element of a list of strings
    (`evidence[0]`); the container returned is then that list and the key its index.
    """
    current = manifest
    for key, selector in segments[:-1]:
        if not isinstance(current, dict) or key not in current:
            raise ManifestError("--path does not resolve: missing '" + key + "'")
        current = current[key]
        if selector is None:
            continue
        if not isinstance(current, list):
            raise ManifestError("--path selector on '" + key + "' requires an array")
        current = select_item(current, selector, key)
    key, selector = segments[-1]
    if not isinstance(current, dict):
        raise ManifestError("--path does not resolve to an object field")
    if selector is None:
        return current, key
    values = current.get(key)
    index = selector[1:] if selector.startswith("#") else selector
    if not isinstance(values, list) or not index.isdigit() or int(index) >= len(values):
        raise ManifestError("--path must end at a field or at an existing list element")
    return values, int(index)


def coerce_value(raw, current):
    """Turn --value into the type the field already has.

    Strings are stored as written: `platform.name` set to "true" became the boolean
    `true` and validated clean, and "null" erased a field. `true`, `false` and
    `null` are converted only where the field is already a boolean, or unset. A
    value that starts with `{` or `[` for a list, object or unset field must be
    valid JSON -- an object used to be stored as a string and a malformed list
    silently fell back to comma-splitting -- and is refused otherwise.
    """
    stripped = raw.strip()
    if isinstance(current, str):
        return raw
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(stripped)
        except ValueError as exc:
            raise ManifestError(
                "--value starts with '" + stripped[:1] + "', so it must be valid JSON: " + str(exc)
            )
    lowered = stripped.lower()
    if isinstance(current, bool):
        if lowered in ("true", "false"):
            return lowered == "true"
        raise ManifestError("field is boolean; --value must be true or false")
    if isinstance(current, dict):
        raise ManifestError(
            "field is an object; pass it as JSON, e.g. --value '{\"from\": [\"a\"], \"to\": []}'"
        )
    if isinstance(current, int):
        try:
            return int(raw)
        except ValueError:
            raise ManifestError("field is numeric; --value must be an integer")
    if isinstance(current, list):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if current is None:
        if lowered in ("true", "false"):
            return lowered == "true"
        if lowered == "null":
            return None
    return raw


# --------------------------------------------------------------------------
# lessons
# --------------------------------------------------------------------------


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug[:60].rstrip("-")) or "lesson"


# Patterns scrubbed before an observation can leave the machine. Deliberately
# aggressive: a false scrub costs a little readability, a missed one leaks a
# customer name or an internal hostname into a public pull request.
SCRUB_PATTERNS = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    (re.compile(r"\bhttps?://\S+"), "<url>"),
    # Credential shapes, before the generic path and domain patterns can eat half
    # of them: cloud, source-host and JWT tokens.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"), "<secret>"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "<secret>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "<secret>"),
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"), "<secret>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<hash>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    # Hostnames including every leading label: db-prod-eu1.acme.internal must not
    # survive as db-prod-eu1.<host>, which still names the machine.
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:internal|local|corp|lan|test|intranet)\b", re.I), "<host>"),
    # Windows and POSIX paths, bare Windows drive roots, and relative paths such as
    # src/acme/billing.ts, which name a project's layout as surely as an absolute one.
    (re.compile(r"\b[A-Za-z]:[\\/][^\s,;]*"), "<path>"),
    (re.compile(r"(?<!\w)~?/(?:[\w.-]+/)+[\w.-]*"), "<path>"),
    (re.compile(r"\b(?:[\w.-]+\\)+[\w.-]+"), "<path>"),
    (re.compile(r"(?<![\w/.-])[\w.-]+/[\w./-]+"), "<path>"),
    # Any dotted name that ends in a two-or-more-letter label reads as a hostname
    # whatever the top-level domain is (acme.dev, acme.xyz, billing.acme.co.uk).
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I), "<domain>"),
)

# Stacks coarsened to a family, so "PostgreSQL 16.2 on prod-eu-1" becomes "postgres".
STACK_FAMILIES = (
    "next.js", "remix", "react", "vue", "svelte", "angular",
    "laravel", "django", "rails", "nestjs", "express", "fastapi", "spring",
    "postgres", "mysql", "sqlite", "mongodb", "supabase", "dynamodb",
    "go", "dotnet", ".net", "php", "python", "node", "typescript", "java", "ruby",
)


def scrub_text(value):
    """Remove anything that could identify a project, person, or host."""
    text = text_of(value)
    for pattern, replacement in SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return " ".join(text.split())


def coarsen_stack(value):
    """Reduce a stack string to recognised family names only."""
    lowered = text_of(value).lower()
    return sorted({family for family in STACK_FAMILIES if family in lowered})


def store_salt(store, create=True):
    """The store's private random salt for project fingerprints.

    A bare SHA-256 of a project name can be reversed by anyone who can guess the
    name, and project names are guessable. The salt never leaves the store, so a
    fingerprint in a published bundle cannot be checked against a list of names.
    """
    path = Path(store) / "salt"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    if not create:
        return ""
    value = os.urandom(16).hex()
    try:
        Path(store).mkdir(parents=True, exist_ok=True)
        with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value + "\n")
    except OSError as exc:
        raise ManifestError("Cannot write the store salt at " + str(path) + ": " + str(exc))
    return value


def project_fingerprint(name, salt=""):
    """Stable pseudonym for a project, so recurrence is countable without naming it."""
    basis = (salt + "|" if salt else "") + text_of(name)
    return "p_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def global_store_dir(override=None):
    """Where observations from every project accumulate.

    Deliberately outside any project. The promotion bar asks whether a lesson was
    seen on two or more distinct projects, and that question is unanswerable while
    each project's copy of the skill only knows about itself.
    """
    if override:
        path = Path(override).expanduser().resolve()
    else:
        env = os.environ.get("ADMINWRIGHT_HOME")
        path = Path(env).expanduser().resolve() if env else Path.home() / ".adminwright"
    if path.exists() and not path.is_dir():
        raise ManifestError(
            "the observation store path " + str(path) + " exists and is not a directory; "
            "check ADMINWRIGHT_HOME"
        )
    return path


def run_git(store, *args, check=True):
    """Run git inside the store. Only the store subcommand needs git."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(store)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        raise ManifestError(
            "git is not on PATH. Store sync needs git; every other command does not. "
            "Alternatively point ADMINWRIGHT_HOME at a folder your file-sync tool already "
            "mirrors between devices."
        )
    if check and completed.returncode != 0:
        raise ManifestError(
            "git " + " ".join(args) + " failed:\n" + (completed.stderr or completed.stdout).strip()
        )
    return completed


def observation_key(record):
    return text_of(record.get("id")) or observation_fingerprint(record)


def merge_observation_files(*paths):
    """Union of observation records by id, in canonical (sorted) order.

    observations.jsonl is append-only, so a sync conflict is never a real
    disagreement -- it is two devices having appended different lines. Merging
    by id union means neither device loses work, which git's default
    line-oriented merge cannot guarantee. Sorting by id makes the result a pure
    function of the SET of records: two devices holding the same records write
    the same bytes, so a later merge never has anything to disagree about.
    """
    merged = {}
    for path in paths:
        if not path.exists():
            continue
        for line in decode_text_bytes(path.read_bytes(), path).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            merged.setdefault(observation_key(record), record)
    return [merged[key] for key in sorted(merged)]


def write_observations(path, records):
    write_text_file(
        path,
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
    )


STOPWORDS = frozenset("""
a an and are as at be been but by for from had has have in into is it its of on or so than
that the then there these they this to was were will with without would should must can not
no nor own too very s t just don now
when which what why how all each every any some also only more most other same like even
still yet via per after before during while where because onto upon over under about
against between through does did doing done them their our your
""".split())

# The skill's own vocabulary of complaint. Nearly every observation says that a
# manifest failed validation, an agent's build broke, or something is missing,
# so sharing these words is no evidence of sharing a finding: they count toward
# a note's length but never toward the shared-word minimum. Reviewer probe:
# "Manifest validation failed on the audit event" and "...failed on screens"
# scored 0.75 and merged on exactly these three words, although they name
# different defects. The OBJECTS of a complaint (audit, screens, roles, export)
# are never listed here.
GENERIC_WORDS = frozenset("""
manifest manifests validation validate validates validated validating validator validators
skill skills agent agents build builds session sessions project projects run runs ran
fail fails failed failing failure failures error errors reject rejects rejected missing
""".split())

# Two agents describing one problem rarely choose the same words, so exact
# matching can only ever group a record with itself. Clustering compares the
# content words of the observation and the proposed change instead.
SIMILARITY_THRESHOLD = 0.45
# Denominator floor for the overlap coefficient: see observation_similarity.
SIMILARITY_FLOOR = 4
# A match needs at least this many shared words outside GENERIC_WORDS. Two
# short notes that share a bigram ("audit log export missing filters" / "audit
# log retention too short") scored 0.5 and merged.
MIN_SHARED_WORDS = 3
# ...and the shared words must be at least this share of the LONGER note, or a
# three-word note is absorbed by any paragraph that happens to contain its words
# (reviewer probe: "validator rejected screens" scored 0.75 against an
# unrelated paragraph about build order).
MIN_LONGER_SHARE = 0.2


def content_words(*texts):
    """Lowercased content words, minus stopwords, digits, and short noise."""
    words = set()
    for text in texts:
        for word in re.findall(r"[a-z][a-z-]{2,}", text_of(text).lower()):
            word = word.strip("-")
            if word and word not in STOPWORDS:
                words.add(word)
    return words


def observation_text(record):
    """An observation's whole wording, normalised for an exact comparison."""
    return " ".join(
        (text_of(record.get("observation")) + " " + text_of(record.get("proposedChange"))).lower().split()
    )


def observation_similarity(left, right):
    """Overlap of two observations' content words, 0.0 to 1.0.

    Overlap rather than Jaccard: one agent writes a sentence and another writes
    a paragraph about the same defect, and Jaccard punishes that difference in
    length even when the shorter text is wholly contained in the longer. The
    denominator has a floor, though: without one, two-word notes sharing a single
    word scored 0.5 and merged with anything that mentioned it. Identical wording
    scores 1.0 however short it is.
    """
    if observation_text(left) and observation_text(left) == observation_text(right):
        return 1.0
    a = content_words(left.get("observation"), left.get("proposedChange"))
    b = content_words(right.get("observation"), right.get("proposedChange"))
    if not a or not b:
        return 0.0
    return len(a & b) / float(max(min(len(a), len(b)), SIMILARITY_FLOOR))


def observations_match(left, right, threshold=SIMILARITY_THRESHOLD):
    """Whether two observations may be one finding.

    Identical wording always is. Otherwise all three must hold: the overlap
    reaches the threshold, at least MIN_SHARED_WORDS of the shared words are not
    GENERIC_WORDS, and the shared words are at least MIN_LONGER_SHARE of the
    longer note. A false merge fabricates the second project the promotion bar
    asks for; a false split only waits for another sighting, and both reports
    stay in the store.
    """
    if observation_text(left) and observation_text(left) == observation_text(right):
        return True
    a = content_words(left.get("observation"), left.get("proposedChange"))
    b = content_words(right.get("observation"), right.get("proposedChange"))
    if not a or not b:
        return False
    shared = a & b
    if len(shared - GENERIC_WORDS) < MIN_SHARED_WORDS:
        return False
    if len(shared) < MIN_LONGER_SHARE * max(len(a), len(b)):
        return False
    return len(shared) / float(max(min(len(a), len(b)), SIMILARITY_FLOOR)) >= threshold


def members_needed(cluster):
    """How many members a record must match to join: all of a small cluster, else a majority."""
    return len(cluster) if len(cluster) <= 3 else len(cluster) // 2 + 1


def cluster_observations(records, threshold=SIMILARITY_THRESHOLD):
    """Group observations that say the same thing in different words.

    Scope and category must match exactly -- they are the coordinates of what
    the rule would change, and a friction note about SKILL.md is not the same
    finding as a tooling bug in the validator however similar the wording.
    Within a bucket, a record joins a cluster only if it MATCHES (see
    observations_match) every member of a cluster of up to three, or a majority
    of a larger one; among the clusters it may join it takes the one it
    resembles most on average.

    Single-link let one shared word chain unrelated findings into one cluster:
    A resembled B, B resembled C, so A and C were "the same finding" although
    they shared nothing. Average-link still admitted a strong bridge: a note
    that repeats two unrelated findings scores 1.0 against each, so the mean
    (0.0 + 1.0) / 2 let the second finding in. Requiring a match with the
    members themselves closes both.

    Field-tested: two projects reported "modelled entities and screens but no
    roles or capabilities" in different words. Exact fingerprint matching filed
    them as two unrelated ideas, so the two-project promotion bar could never
    be met and the most-repeated finding in the store stayed invisible.
    """
    buckets = {}
    for record in records:
        key = (
            text_of(record.get("scope")).lower().strip(),
            text_of(record.get("category")).lower().strip(),
        )
        buckets.setdefault(key, []).append(record)

    clusters = []
    for _key, members in sorted(buckets.items()):
        # Deterministic order so the same store always clusters the same way.
        members = sorted(
            members,
            key=lambda r: (text_of(r.get("date")), text_of(r.get("project")),
                           text_of(r.get("fingerprint")), observation_key(r)),
        )
        local = []
        for record in members:
            best = None
            for cluster in local:
                matched = sum(1 for other in cluster if observations_match(record, other, threshold))
                if matched < members_needed(cluster):
                    continue
                mean = sum(observation_similarity(record, other) for other in cluster) / float(len(cluster))
                if best is None or mean > best[0]:
                    best = (mean, cluster)
            if best is not None:
                best[1].append(record)
            else:
                local.append([record])
        clusters.extend(local)
    return clusters


def observation_fingerprint(record):
    """Stable identity for one observation, used to de-duplicate re-harvests.

    This is exact-match by construction and cannot group differently worded
    reports of one problem; `cluster_observations` does that.
    """
    basis = (
        text_of(record.get("scope")).lower().strip()
        + "|"
        + text_of(record.get("category")).lower().strip()
        + "|"
        + re.sub(r"[^a-z0-9 ]+", "", text_of(record.get("proposedChange")).lower())[:160]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def read_community_observations(skill_root=None):
    """Contributed bundles from the skill checkout.

    Untrusted by construction: these arrived through a pull request from someone
    else's machine. They may corroborate a local observation across the promotion
    bar; they can never adopt guidance on their own, and they are always labelled
    in output so a reviewer can weigh them differently.
    """
    base = Path(skill_root) if skill_root else SKILL_ROOT
    directory = base / "community" / "observations"
    records = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for index, record in enumerate(as_list(payload.get("observations"))):
            if not isinstance(record, dict):
                continue
            record["community"] = True
            # Recompute rather than trust the bundle's own fingerprint. A stale
            # or hand-edited value would either fail to group with matching local
            # observations, or group with unrelated ones -- and grouping is what
            # decides whether something clears the bar.
            record["fingerprint"] = observation_fingerprint(record)
            # One contributor may report the same idea from several projects;
            # keep them distinct so projectCount stays meaningful.
            contributors = as_list(record.get("projects"))
            record["project"] = (
                text_of(contributors[0]) if contributors
                else "community:" + path.stem + ":" + str(index)
            )
            records.append(record)
    return records


def read_observations(store):
    path = store / "observations.jsonl"
    records = []
    if not path.exists():
        return records
    for line in decode_text_bytes(path.read_bytes(), path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def append_observations(store, records):
    store.mkdir(parents=True, exist_ok=True)
    path = store / "observations.jsonl"
    with FileLock(store / ".lock"):
        with open(str(path), "a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


LESSON_CONFIDENCES = ("observed-once", "repeated", "confirmed")
LESSON_FILE_PATTERN = re.compile(r"^(\d{4})-.+\.md$")


def next_lesson_number(lessons_dir):
    highest = 0
    if lessons_dir.exists():
        for path in lessons_dir.glob("*.md"):
            match = re.match(r"^(\d{4})-", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def yaml_quote(value):
    """A YAML double-quoted scalar. JSON string syntax is a subset of it."""
    return json.dumps(" ".join(str(value).split()), ensure_ascii=False)


def yaml_list(values):
    return "[" + ", ".join(yaml_quote(value) for value in values) + "]"


def parse_frontmatter_value(raw):
    raw = raw.strip()
    if raw.startswith("[") or raw.startswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    if raw.startswith("["):
        inner = raw.strip("[]")
        return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
    return raw.strip("'\"")


def read_lesson_frontmatter(path):
    """The fields between a first line '---' and the next line that is exactly '---'.

    Line by line, never by splitting the text on '---': a title such as
    "Use A --- B split" is valid quoted YAML, and splitting on it cut the
    frontmatter short, dropping the lesson's status from the index and from the
    guidance level.
    """
    try:
        text = decode_text_bytes(path.read_bytes(), path)
    except (OSError, ManifestError):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return {}
    fields = {}
    for line in lines[1:]:
        if line.rstrip() == "---":
            return fields
        if ":" not in line:
            continue
        key, _sep, value = line.partition(":")
        fields[key.strip()] = parse_frontmatter_value(value)
    return {}  # never closed: not frontmatter


def lesson_files(lessons_dir):
    if not lessons_dir.exists():
        return []
    return sorted(p for p in lessons_dir.glob("*.md") if LESSON_FILE_PATTERN.match(p.name))


LESSON_INDEX_PREAMBLE = (
    "One row per lesson, ordered by id. `lesson add` rebuilds the table from the lesson\n"
    "files, so the index cannot drift from them. Field meanings, statuses, and the review\n"
    "procedure are in [README.md](README.md). The promotion bar is in\n"
    "[../references/skill-evolution.md](../references/skill-evolution.md).\n"
    "\n"
    "`scope` is skill-root-relative and names the one file or area the rule lands in.\n"
)
# `platforms` carries the promotion bar: "seen on two or more distinct
# projects" is unreadable from an index that does not name the projects.
LESSON_INDEX_COLUMNS = "| id | title | category | scope | status | confidence | platforms | date |"


def rebuild_lesson_index(lessons_dir):
    """Regenerate index.md from the lesson files; return the guidance level.

    The index used to be appended to by hand and drifted: its `Guidance level`
    read 0001 while four later lessons were adopted, and its rows named lessons
    without linking them. It is now a pure function of the files.
    """
    index_path = lessons_dir / "index.md"
    consolidation = "unknown"
    if index_path.exists():
        found = re.search(r"^Last consolidation:\s*(.+)$", index_path.read_text(encoding="utf-8"), re.M)
        if found:
            consolidation = found.group(1).strip()
    rows = []
    adopted = []
    for path in lesson_files(lessons_dir):
        fields = read_lesson_frontmatter(path)
        lesson_id = fields.get("id") or path.name[:4]
        if fields.get("status") == "adopted":
            adopted.append(lesson_id)
        platforms = fields.get("platforms")
        if isinstance(platforms, list):
            platforms = "; ".join(str(item) for item in platforms)
        rows.append(
            "| "
            + " | ".join(
                [
                    "[" + lesson_id + "](" + path.name + ")",
                    cell(fields.get("title", "")),
                    cell(fields.get("category", "")),
                    cell(fields.get("scope", "")),
                    cell(fields.get("status", "")),
                    cell(fields.get("confidence", "")),
                    cell(platforms or ""),
                    cell(fields.get("date", "")),
                ]
            )
            + " |"
        )
    level = max(adopted) if adopted else "none"
    text = (
        "# Lesson index\n\nLast consolidation: " + consolidation + "\nGuidance level: " + level
        + "\n\n" + LESSON_INDEX_PREAMBLE + "\n" + LESSON_INDEX_COLUMNS + "\n"
        "|---|---|---|---|---|---|---|---|\n" + "".join(row + "\n" for row in rows)
    )
    write_text_file(index_path, text)
    return level


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def resolve_manifest_path(value):
    path = Path(value).resolve()
    if not path.exists():
        raise ManifestError("Manifest not found: " + str(path))
    return path


def project_root_of(manifest_path, manifest=None):
    """The repository root a manifest's relative evidence paths resolve against.

    Every command that reads evidence must agree on this, or `validate` from the
    project root and `set` from anywhere else judge the same manifest differently.
    A manifest may record `projectRoot` (relative to the folder holding the
    manifest file); otherwise it is the parent of that folder, which is where
    `init` puts `.admin-console/manifest.json`.
    """
    base = Path(manifest_path).resolve().parent
    declared = text_of(as_dict(manifest).get("projectRoot")) if manifest else ""
    if declared:
        return (base / declared).resolve()
    return base.parent


ADMIN_CONSOLE_GITIGNORE = (
    "# Working state of the admin-console tooling. Commit the manifest, its schema copy,\n"
    "# the worklogs and the emitted reports; never these.\n"
    "locks/\n"
    ".lock\n"
    "*.tmp\n"
    "*.stale.*\n"
    "ocr-bundle*.json\n"
    "ocr-record-*.json\n"
    "ocr-diff-*.json\n"
    "ocr-scan-*.json\n"
)


def cmd_init(args):
    project_root = Path(args.project_root).resolve()
    if args.manifest:
        target = Path(args.manifest).resolve()
        target_dir = target.parent
    else:
        target_dir = project_root / ".admin-console"
        target = target_dir / "manifest.json"
    if target.exists() and not args.force:
        stderr("Manifest already exists: " + str(target))
        stderr(
            "init is for a new console. To continue work on this one: read the manifest, read "
            "every worklog whose agents[].status is handed-off, run `validate --phase plan`, and "
            "resume at the first phase whose exit condition fails. Pass --force only to discard "
            "the manifest and start over."
        )
        return 2
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_json(TEMPLATE_PATH)
    manifest["profile"] = args.profile
    try:
        relative_root = os.path.relpath(str(project_root), str(target_dir)).replace("\\", "/")
    except ValueError:  # another drive: no relative path exists
        relative_root = str(project_root).replace("\\", "/")
    manifest["projectRoot"] = relative_root
    manifest["platform"]["name"] = args.name
    # A stable handle for the cross-project store: the promotion bar counts
    # distinct projects, and a display name is not one.
    manifest["platform"]["id"] = (
        slugify(args.name)[:32].rstrip("-") + "-" + os.urandom(3).hex()
    )
    archetypes = []
    for raw in args.archetype or []:
        # --archetype a,b used to store the single string "a,b", which resolved to
        # one archetype (or none).
        archetypes.extend(part.strip() for part in raw.split(",") if part.strip())
    manifest["platform"]["archetypes"] = list(dict.fromkeys(archetypes))
    # An unrecognised archetype silently disables coverage checking, and the
    # first field tests proved nobody notices. Warn at the moment of typing,
    # when the fix costs one word.
    for raw in manifest["platform"]["archetypes"]:
        if resolve_archetype(raw) is None:
            stderr(
                "WARN: archetype '" + str(raw) + "' is not a recognised key; "
                "archetype-coverage checking will skip it. Known keys: "
                + ", ".join(sorted(ARCHETYPE_DOMAINS))
            )
    stack = manifest["platform"].setdefault("stack", {})
    for key, value in (
        ("frontend", args.stack_frontend),
        ("backend", args.stack_backend),
        ("database", args.stack_database),
        ("auth", args.stack_auth),
        ("jobs", args.stack_jobs),
        ("hosting", args.stack_hosting),
        ("designSystem", args.stack_design_system),
        ("adminFramework", args.stack_admin_framework),
    ):
        if value:
            stack[key] = value
    # Choosing a profile is a recorded decision, not a default. init makes it an
    # ASSUMED one, so an unconfirmed profile fails the release gate at regulated
    # (decision-assumed) until someone with authority confirms it.
    manifest["decisions"] = [{
        "id": "dec.profile",
        "decision": "Run at the " + args.profile + " profile",
        "reason": "chosen when the manifest was created; not yet confirmed with the owner",
        "evidence": [],
        "status": "assumed",
        "appliesTo": ["*"],
    }]
    commit_manifest(target, manifest)
    schema_copy = target_dir / "admin-console.manifest.schema.json"
    shutil.copyfile(str(SCHEMA_PATH), str(schema_copy))
    ignore_path = target_dir / ".gitignore"
    if not ignore_path.exists():
        write_text_file(ignore_path, ADMIN_CONSOLE_GITIGNORE)
    print("Created " + str(target))
    print("Created " + str(schema_copy))
    print("Profile: " + args.profile + " (recorded as the assumed decision dec.profile; confirm it)")
    print("Project root: " + relative_root + " (relative to the manifest's folder)")
    print("adminwright " + skill_version() + ", ruleset " + ruleset_version())
    return 0


def migrate_safely(manifest):
    try:
        return migrate_manifest(manifest)
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        raise ManifestError(
            "cannot migrate: this 1.0 manifest is malformed (" + type(exc).__name__ + ": "
            + str(exc) + "). Fix the shape it names, or start from `init`."
        )


def cmd_migrate(args):
    manifest_path = resolve_manifest_path(args.manifest)
    # Read, migrate and write under one lock, so a concurrent add cannot be
    # overwritten by a migration of the copy read before it.
    lock = FileLock(manifest_path.parent / ".lock") if args.write else None
    if lock is not None:
        lock.__enter__()
    try:
        manifest = load_json(manifest_path)
        original_version = manifest.get("manifestVersion")
        migrated, changes = migrate_safely(manifest)
        if original_version == MANIFEST_VERSION:
            print("Manifest is already at version " + MANIFEST_VERSION + "; nothing to migrate.")
            return 0
        print("Migration " + str(original_version) + " -> " + MANIFEST_VERSION)
        print("Added or changed " + str(len(changes)) + " key(s):")
        for change in changes:
            print("  + " + change)
        if not args.write:
            print("Dry run. Re-run with --write to persist.")
            return 0
        commit_manifest(manifest_path, migrated)
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)
    print("Wrote " + str(manifest_path))
    return 0


def print_findings(findings, phase, profile_source):
    print("Profile: " + findings.profile + " (" + profile_source + ")")
    print("Phase: " + phase)
    for item in findings.sorted_items():
        print(
            item["severity"].upper()
            + ": ["
            + item["rule"]
            + "] "
            + item["path"]
            + ": "
            + item["message"]
        )
    print(
        "Validation complete: "
        + str(len(findings.errors))
        + " error(s), "
        + str(len(findings.warnings))
        + " warning(s) [profile "
        + findings.profile
        + ", phase "
        + phase
        + ", adminwright "
        + skill_version()
        + ", ruleset "
        + ruleset_version()
        + "]"
    )


def profile_rank(name):
    return PROFILES.index(name) if name in PROFILES else PROFILES.index(DEFAULT_PROFILE)


def cmd_validate(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    profile, source = active_profile(manifest, args.profile)
    declared = active_profile(manifest, None)[0]
    lowered = bool(args.profile) and profile_rank(profile) < profile_rank(declared)
    warning = None
    if lowered and args.phase == "release":
        # `--profile` exists to preview a stricter tier, or to inspect a manifest
        # as another profile would judge it. At release it must never be a way to
        # certify below the profile the manifest itself declares.
        if not args.allow_lower_profile:
            stderr(
                "ERROR: --profile " + profile + " is below this manifest's declared profile '"
                + declared + "'. A release is judged at the declared profile; pass "
                "--allow-lower-profile to evaluate it lower anyway (the result will say so)."
            )
            return 2
        warning = "evaluated at " + profile + "; manifest profile is " + declared
        stderr("WARNING: " + warning)
    project_root = (
        Path(args.project_root).resolve() if args.project_root else project_root_of(manifest_path, manifest)
    )
    findings = validate_manifest(
        manifest, args.phase, project_root, profile, manifest_dir=manifest_path.parent
    )
    if args.json:
        print(
            json.dumps(
                {
                    "profile": profile,
                    "profileSource": source,
                    "manifestProfile": declared,
                    "allowLowerProfile": bool(args.allow_lower_profile),
                    "warning": warning,
                    "skillVersion": skill_version(),
                    "rulesetVersion": ruleset_version(),
                    "phase": args.phase,
                    "manifest": str(manifest_path),
                    "projectRoot": str(project_root),
                    "errors": len(findings.errors),
                    "warnings": len(findings.warnings),
                    "notApplicable": summarize(manifest)["notApplicable"],
                    "findings": findings.sorted_items(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print_findings(findings, args.phase, source)
        if args.phase == "release":
            print("not-applicable: " + str(summarize(manifest)["notApplicable"]) + " capability(ies)")
    return 1 if findings.errors else 0


def summarize(manifest):
    capabilities = [cap for _p, _e, cap in iter_capabilities(manifest)]
    platform = as_dict(manifest.get("platform"))
    return {
        "platform": text_of(platform.get("name")) or "unnamed platform",
        "profile": active_profile(manifest, None)[0],
        "archetypes": as_list(platform.get("archetypes")),
        "tenancy": platform.get("tenancy"),
        "counts": {
            "roles": len(as_list(manifest.get("roles"))),
            "entities": len(as_list(manifest.get("entities"))),
            "screens": len(as_list(manifest.get("screens"))),
            "workQueues": len(as_list(manifest.get("workQueues"))),
            "integrations": len(as_list(manifest.get("integrations"))),
            "capabilities": len(capabilities),
            "declaredStatic": len(as_list(manifest.get("declaredStatic"))),
            "feedback": len(as_list(manifest.get("feedback"))),
            "agents": len(as_list(manifest.get("agents"))),
        },
        "capabilityStatus": dict(Counter(label(cap.get("status")) for cap in capabilities)),
        "capabilityKind": dict(Counter(label(cap.get("kind")) for cap in capabilities)),
        "capabilityRisk": dict(Counter(label(cap.get("risk")) for cap in capabilities)),
        "reviewStatus": dict(
            Counter(label(cap.get("reviewStatus"), "unreviewed") for cap in capabilities)
        ),
        "qualityGates": dict(
            Counter(label(gate.get("status")) for _p, gate in iter_named(manifest, "qualityGates"))
        ),
        "gaps": dict(
            Counter(label(gap.get("status")) for _p, gap in iter_named(manifest, "gaps"))
        ),
        "decisions": dict(
            Counter(label(item.get("status")) for _p, item in iter_named(manifest, "decisions"))
        ),
        "notApplicable": sum(1 for cap in capabilities if cap.get("status") == "not-applicable"),
        "declaredStatic": [
            {
                "path": text_of(entry.get("path")),
                "reason": text_of(entry.get("reason")),
                "approvedBy": text_of(entry.get("approvedBy")),
            }
            for _p, entry in iter_named(manifest, "declaredStatic")
        ],
    }


def cmd_report(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    data = summarize(manifest)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    counts = data["counts"]
    print("# Admin console coverage: " + data["platform"])
    print()
    print("- Profile: " + data["profile"])
    print("- Archetypes: " + (", ".join(data["archetypes"]) or "not classified"))
    print("- Tenancy: " + str(data["tenancy"]))
    for key in ("roles", "entities", "screens", "workQueues", "integrations", "capabilities"):
        print("- " + key + ": " + str(counts[key]))
    for title, key in (
        ("Capability status", "capabilityStatus"),
        ("Capability kind", "capabilityKind"),
        ("Capability risk", "capabilityRisk"),
        ("Review status", "reviewStatus"),
        ("Quality gates", "qualityGates"),
        ("Decisions", "decisions"),
        ("Gaps", "gaps"),
    ):
        print()
        print("## " + title)
        values = data[key]
        if not values:
            print("- none recorded")
            continue
        for name in sorted(values):
            print("- " + str(name) + ": " + str(values[name]))
    print()
    print("## Declared static")
    if not data["declaredStatic"]:
        print("- none declared")
    for entry in data["declaredStatic"]:
        print("- " + entry["path"] + ": " + entry["reason"] + " (approved by " + entry["approvedBy"] + ")")
    return 0


def cmd_coverage(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    profile, source = active_profile(manifest, getattr(args, "profile", None))
    findings = coverage_findings(manifest, profile)
    print("# Structural coverage audit")
    print()
    print("Profile: " + profile + " (" + source + ")")
    print()
    if not findings.items:
        print("No unresolved structural gaps.")
        return 0
    print("| Severity | Rule | Path | Finding |")
    print("|---|---|---|---|")
    for item in findings.sorted_items():
        print(
            "| "
            + cell(item["severity"])
            + " | "
            + cell(item["rule"])
            + " | "
            + cell(item["path"])
            + " | "
            + cell(item["message"])
            + " |"
        )
    print()
    print(
        str(len(findings.errors))
        + " error(s), "
        + str(len(findings.warnings))
        + " warning(s)"
    )
    # Exit on errors only, matching validate. Failing on warnings would block an
    # internal-profile team for findings that profile explicitly says may warn,
    # which is the dishonest-tiering trap the profiles exist to avoid.
    return 1 if findings.errors else 0


def cmd_emit(args):
    if args.format == "rule-matrix":
        text = emit_rule_matrix()
        if args.out:
            out_path = Path(args.out).resolve()
            write_text_file(out_path, text)
            print("Wrote " + str(out_path))
        else:
            sys.stdout.write(text)
        return 0
    if not args.manifest:
        raise ManifestError("--manifest is required for --format " + args.format)
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    profile = active_profile(manifest, None)[0]
    # Surface manifest health on every emit. Field-tested: an audit session
    # hand-wrote the manifest, ran only emit, and shipped a gap report while 83
    # validation errors sat invisible -- emit is where agents actually engage,
    # so emit is where health has to show. Advisory only; exit code unchanged.
    # A gap report is judged the way a release is, so its health line and its
    # table use the same severities; the other documents are working notes.
    health_phase = "release" if args.format == "gap-report" else "plan"
    health = validate_manifest(
        manifest, health_phase, project_root_of(manifest_path, manifest), profile,
        manifest_path.parent,
    )
    if health.errors:
        stderr(
            "WARN: this manifest has " + str(len(health.errors))
            + " validation error(s) at " + health_phase + " phase. The report below may rest on "
            "malformed data. Run `validate --phase " + health_phase + "` for the list."
        )
    if not as_list(manifest.get("feedback")):
        stderr(
            "note: feedback[] is empty. If this skill misled or slowed you, record it "
            "(add --kind feedback) and run `harvest` -- that is how the skill learns."
        )
    text = EMITTERS[args.format](manifest, profile)
    if args.format == "gap-report" and (health.errors or health.warnings):
        caveat = (
            " A gap report from a manifest that does not validate is a sketch, not a record."
            if health.errors
            else ""
        )
        text = text.replace(
            "# Gap report\n",
            "# Gap report\n\nManifest health: " + str(len(health.errors))
            + " error(s), " + str(len(health.warnings))
            + " warning(s) at release validation." + caveat + "\n",
            1,
        )
    if args.out:
        out_path = Path(args.out).resolve()
        write_text_file(out_path, text)
        print("Wrote " + str(out_path))
    else:
        sys.stdout.write(text)
    return 0


def cmd_add(args):
    manifest_path = resolve_manifest_path(args.manifest)
    raw_json = args.json if args.json is not None else "@" + args.json_file
    entries = [merge_defaults(args.kind, item) for item in parse_json_entries(raw_json)]
    for entry in entries:
        normalise_paths(entry, args.kind + " '" + text_of(entry.get("id")) + "'")
    kind = args.kind
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        if kind == "capability":
            if not args.entity:
                raise ManifestError("--entity is required when adding a capability")
            target = None
            for _eid, entity in iter_entities(manifest):
                if text_of(entity.get("id")) == args.entity:
                    target = entity
                    break
            if target is None:
                raise ManifestError("entity '" + args.entity + "' is not in the manifest")
            collection = target.setdefault("capabilities", [])
            existing = [
                text_of(cap.get("id")) for cap in collection if isinstance(cap, dict)
            ]
            duplicate_where = " on entity '" + args.entity + "'"
        else:
            key = KIND_TARGETS[kind]
            collection = manifest.setdefault(key, [])
            if not isinstance(collection, list):
                raise ManifestError("manifest." + key + " is not an array")
            existing = [
                text_of(item.get("id")) for item in collection if isinstance(item, dict)
            ]
            duplicate_where = ""
        # The whole batch is validated before anything is appended, so an array
        # add is atomic: one bad entry refuses the batch and nothing is written.
        batch_ids = set()
        for entry in entries:
            entry_id = text_of(entry.get("id"))
            if not entry_id:
                raise ManifestError("the added " + kind + " needs an id")
            if not ID_PATTERN.fullmatch(entry_id):
                raise ManifestError("id '" + entry_id + "' must be lowercase alphanumeric with dots or hyphens")
            if entry_id in existing:
                raise ManifestError(kind + " '" + entry_id + "' already exists" + duplicate_where)
            if entry_id in batch_ids:
                raise ManifestError(kind + " '" + entry_id + "' appears twice in this batch")
            if kind == "feedback" and skill_scope_problem(entry.get("scope")):
                raise ManifestError(
                    "feedback '" + entry_id + "' needs a scope: its scope "
                    + json.dumps(entry.get("scope")) + " " + skill_scope_problem(entry.get("scope"))
                    + "; " + SKILL_SCOPE_HINT
                )
            batch_ids.add(entry_id)
        collection.extend(entries)
        introduced, warned = new_findings(
            before, manifest, project_root_of(manifest_path, manifest), manifest_path.parent
        )
        if introduced and not args.allow_invalid:
            stderr("Refusing to write: the new " + kind + " introduces validation errors.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            return 2
        commit_manifest(manifest_path, manifest)
    for entry in entries:
        print("Added " + kind + " '" + text_of(entry.get("id")) + "' to " + str(manifest_path))
    report_new_warnings(warned)
    report_overridden(introduced, args.allow_invalid, manifest, manifest_path)
    return 0


def report_overridden(introduced, allow_invalid, manifest, manifest_path):
    """Never let --allow-invalid hide what it waved through.

    A silent override turns the escape hatch into a way to smuggle mock data past
    the scanner, which is the one thing this tool exists to prevent. The closing
    line states what release will actually do, by asking the release phase.
    """
    if not (introduced and allow_invalid):
        return
    stderr("WARNING: --allow-invalid wrote " + str(len(introduced)) + " unresolved error(s):")
    for item in introduced:
        stderr("  [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
    profile = active_profile(manifest, None)[0]
    release = validate_manifest(
        manifest, "release", project_root_of(manifest_path, manifest), profile,
        manifest_path.parent,
    )
    still = {(i["rule"], i["path"]) for i in release.errors}
    persisting = [i for i in introduced if (i["rule"], i["path"]) in still]
    if persisting:
        stderr(
            str(len(persisting)) + " of them still fail `validate --phase release`. "
            "Resolve them before any release declaration."
        )
    else:
        stderr("None of them fail `validate --phase release`; they were plan-phase findings.")


def cmd_set(args):
    manifest_path = resolve_manifest_path(args.manifest)
    segments = parse_path_expression(args.path)
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        container, key = resolve_path_parent(manifest, segments)
        previous = container[key] if isinstance(container, list) else container.get(key)
        value = coerce_value(args.value, previous)
        if key in PATH_LIST_FIELDS and isinstance(value, list):
            value, changed = normalise_path_list(value)
            if changed:
                stderr("note: rewrote backslashes as '/' in " + str(changed) + " path(s) of " + args.path)
        container[key] = value
        renamed = None
        if key == "id" and isinstance(previous, str) and previous and isinstance(value, str) and previous != value:
            renamed = (previous, value)
        introduced, warned = new_findings(
            before, manifest, project_root_of(manifest_path, manifest), manifest_path.parent,
            renamed=renamed,
        )
        if introduced and not args.allow_invalid:
            stderr("Refusing to write: the change introduces validation errors.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            return 2
        commit_manifest(manifest_path, manifest)
    print(args.path + ": " + json.dumps(previous) + " -> " + json.dumps(value))
    report_new_warnings(warned)
    report_overridden(introduced, args.allow_invalid, manifest, manifest_path)
    return 0


def find_item(manifest, kind, item_id, entity_id=None):
    """Return (collection, index) for an item, or raise ManifestError."""
    if kind == "capability":
        for _eid, entity in iter_entities(manifest):
            if entity_id and text_of(entity.get("id")) != entity_id:
                continue
            collection = entity.get("capabilities")
            if not isinstance(collection, list):
                continue
            for index, item in enumerate(collection):
                if isinstance(item, dict) and text_of(item.get("id")) == item_id:
                    return collection, index
        raise ManifestError("capability '" + item_id + "' is not in the manifest")
    key = KIND_TARGETS[kind]
    collection = manifest.get(key)
    if isinstance(collection, list):
        for index, item in enumerate(collection):
            if isinstance(item, dict) and text_of(item.get("id")) == item_id:
                return collection, index
    raise ManifestError(kind + " '" + item_id + "' is not in the manifest")


def cmd_remove(args):
    """Delete one item. The write guard refuses it if anything still points at it.

    SKILL.md forbids hand-editing the manifest, and there was no command that could
    delete an entry, so a wrong `add` could only be undone by breaking the rule.
    """
    manifest_path = resolve_manifest_path(args.manifest)
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        collection, index = find_item(manifest, args.kind, args.id, args.entity)
        collection.pop(index)
        introduced, warned = new_findings(
            before, manifest, project_root_of(manifest_path, manifest), manifest_path.parent
        )
        if introduced and not args.allow_invalid:
            stderr("Refusing to remove: something still depends on " + args.kind + " '" + args.id + "'.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            stderr("Remove or update those first, or pass --allow-invalid.")
            return 2
        commit_manifest(manifest_path, manifest)
    print("Removed " + args.kind + " '" + args.id + "' from " + str(manifest_path))
    report_new_warnings(warned)
    report_overridden(introduced, args.allow_invalid, manifest, manifest_path)
    return 0


def swap_in_list(container, key, old, new):
    changed = 0
    values = container.get(key) if isinstance(container, dict) else None
    if isinstance(values, list):
        for index, value in enumerate(values):
            if value == old:
                values[index] = new
                changed += 1
    return changed


def swap_scalar(container, key, old, new):
    if isinstance(container, dict) and container.get(key) == old:
        container[key] = new
        return 1
    return 0


def rewrite_references(manifest, kind, old, new):
    """Point every reference to `old` at `new`; return how many were changed."""
    changed = 0
    capabilities = [cap for _path, _entity, cap in iter_capabilities(manifest)]
    if kind == "role":
        for cap in capabilities:
            changed += swap_in_list(cap, "roles", old, new)
        for _path, screen in iter_named(manifest, "screens"):
            changed += swap_in_list(screen, "roles", old, new)
        for key in ("workQueues", "integrations"):
            for _path, item in iter_named(manifest, key):
                changed += swap_in_list(item, "roles", old, new)
        for _eid, entity in iter_entities(manifest):
            for transition in as_list(entity.get("lifecycleTransitions")):
                changed += swap_in_list(transition, "actorRoles", old, new)
    if kind == "capability":
        for _path, screen in iter_named(manifest, "screens"):
            changed += swap_in_list(screen, "capabilities", old, new)
        for _eid, entity in iter_entities(manifest):
            for transition in as_list(entity.get("lifecycleTransitions")):
                changed += swap_scalar(transition, "command", old, new)
        for _path, agent in iter_named(manifest, "agents"):
            changed += swap_in_list(agent, "ownsCapabilities", old, new)
            changed += swap_in_list(agent, "previouslyOwned", old, new)
        for _path, gap in iter_named(manifest, "gaps"):
            changed += swap_scalar(gap, "capability", old, new)
        for source in as_list(as_dict(manifest.get("platform")).get("researchSources")):
            changed += swap_in_list(source, "appliedTo", old, new)
    if kind == "screen":
        for _path, agent in iter_named(manifest, "agents"):
            changed += swap_in_list(agent, "ownsScreens", old, new)
    if kind == "entity":
        for _path, gap in iter_named(manifest, "gaps"):
            changed += swap_scalar(gap, "entity", old, new)
    if kind == "agent":
        for cap in capabilities:
            for key in ("owner", "implementedBy", "reviewedBy"):
                changed += swap_scalar(cap, key, old, new)
        for _path, entry in iter_named(manifest, "declaredStatic"):
            changed += swap_scalar(entry, "approvedBy", old, new)
    if kind in ("role", "capability", "screen", "entity", "queue", "integration", "gap"):
        for _path, decision in iter_named(manifest, "decisions"):
            changed += swap_in_list(decision, "appliesTo", old, new)
    if kind in ("role", "capability", "screen", "entity", "queue", "integration"):
        # declaredStatic paths name items by id: entities[old].capabilities[x].dataBinding
        for _path, entry in iter_named(manifest, "declaredStatic"):
            target = text_of(entry.get("path"))
            if "[" + old + "]" in target:
                entry["path"] = target.replace("[" + old + "]", "[" + new + "]")
                changed += 1
    return changed


def cmd_rename(args):
    """Change an item's id and every reference to it, atomically.

    A capability id is repeated in screens, transitions, agents, decisions and gaps;
    renaming it by hand is exactly the multi-file edit that goes wrong.
    """
    manifest_path = resolve_manifest_path(args.manifest)
    new_id = args.to
    if not ID_PATTERN.fullmatch(new_id):
        raise ManifestError("id '" + new_id + "' must be lowercase alphanumeric with dots or hyphens")
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        collection, index = find_item(manifest, args.kind, args.id, args.entity)
        for other in collection:
            if isinstance(other, dict) and text_of(other.get("id")) == new_id:
                raise ManifestError(args.kind + " '" + new_id + "' already exists")
        if args.kind == "capability" and new_id in capability_index(manifest):
            raise ManifestError("capability id '" + new_id + "' is used by another entity")
        collection[index]["id"] = new_id
        changed = rewrite_references(manifest, args.kind, args.id, new_id)
        introduced, warned = new_findings(
            before, manifest, project_root_of(manifest_path, manifest), manifest_path.parent,
            renamed=(args.id, new_id),
        )
        if introduced and not args.allow_invalid:
            stderr("Refusing to rename: the result would introduce validation errors.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            return 2
        commit_manifest(manifest_path, manifest)
        if args.kind == "capability":
            old_lock = manifest_path.parent / "locks" / lock_file_name(args.id)
            if old_lock.exists():
                try:
                    old_lock.replace(manifest_path.parent / "locks" / lock_file_name(new_id))
                except OSError:
                    pass
    print(
        "Renamed " + args.kind + " '" + args.id + "' to '" + new_id + "' and updated "
        + str(changed) + " reference(s) in " + str(manifest_path)
    )
    report_new_warnings(warned)
    report_overridden(introduced, args.allow_invalid, manifest, manifest_path)
    return 0


def lock_file_name(capability_id):
    return re.sub(r"[^A-Za-z0-9._-]", "_", capability_id) + ".lock"


IMPLEMENTER_ROLES = ("implementer", "architect")
REVIEWER_ROLES = ("ux-reviewer", "qa", "security")


def find_agent(manifest, agent_id):
    for item in as_list(manifest.get("agents")):
        if isinstance(item, dict) and text_of(item.get("id")) == agent_id:
            return item
    return None


def manifest_holder(manifest, capability, requester):
    """Another ACTIVE agent that the manifest says holds this capability, or None.

    The lock files are untracked, so on a fresh clone -- or after `locks/` was
    cleaned -- a claim decided from them alone let any agent take a capability
    another agent visibly owned.
    """
    cid = text_of(capability.get("id"))
    for _path, agent in iter_named(manifest, "agents"):
        agent_id = text_of(agent.get("id"))
        if agent_id == requester or text_of(agent.get("status")) != "active":
            continue
        if cid in as_list(agent.get("ownsCapabilities")):
            return agent_id
    owner = text_of(capability.get("owner"))
    if owner and owner != requester:
        record = find_agent(manifest, owner)
        if record is not None and text_of(record.get("status")) == "active":
            return owner
    return None


def cmd_claim(args):
    manifest_path = resolve_manifest_path(args.manifest)
    if args.force_steal and not text_of(args.reason):
        stderr("--force-steal requires --reason")
        return 2
    lock_dir = manifest_path.parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    acquired = []
    stolen = []
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        caps = capability_index(manifest)
        unknown = [cid for cid in args.capability if cid not in caps]
        if unknown:
            stderr("Unknown capability id(s): " + ", ".join(unknown))
            return 2
        existing = find_agent(manifest, args.agent)
        if (
            existing is not None
            and args.role in REVIEWER_ROLES
            and text_of(existing.get("role")) in IMPLEMENTER_ROLES
            and (as_list(existing.get("ownsCapabilities")) or as_list(existing.get("previouslyOwned")))
        ):
            # Re-registering under a reviewer role would erase the only record that
            # this id built something, and let it sign off its own work.
            stderr(
                "Agent '" + args.agent + "' is registered as " + text_of(existing.get("role"))
                + " and has held capabilities; it cannot claim as " + args.role
                + ". Use a different agent id for the review."
            )
            return 2
        for cid in args.capability:
            holder = manifest_holder(manifest, caps[cid][2], args.agent)
            if holder and not args.force_steal:
                stderr(
                    "Capability '" + cid + "' is held by agent '" + holder + "' according to the "
                    "manifest. Coordinate with it, wait for its release-claim, or re-run with "
                    "--force-steal --reason '<why>'."
                )
                return 3
        for cid in args.capability:
            lock_path = lock_dir / lock_file_name(cid)
            owner = None
            try:
                handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.write(handle, (args.agent + "\n").encode("utf-8"))
                os.close(handle)
                acquired.append(lock_path)
                continue
            except FileExistsError:
                try:
                    owner = lock_path.read_text(encoding="utf-8").strip()
                except OSError:
                    owner = ""
            except OSError as exc:
                stderr("Cannot create capability lock: " + str(exc))
                return 2
            if owner == args.agent:
                continue
            if args.force_steal:
                write_text_file(lock_path, args.agent + "\n")
                stolen.append((cid, owner))
                continue
            for path in acquired:
                try:
                    path.unlink()
                except OSError:
                    pass
            stderr(
                "Capability '"
                + cid
                + "' is already claimed by agent '"
                + (owner or "unknown")
                + "'. Coordinate through the manifest or re-run with --force-steal --reason '<why>'."
            )
            return 3

        agents = manifest.setdefault("agents", [])
        record = existing
        if record is None:
            record = merge_defaults("agent", {"id": args.agent, "role": args.role})
            agents.append(record)
        record["role"] = args.role
        record["status"] = "active"
        owned = [value for value in as_list(record.get("ownsCapabilities")) if isinstance(value, str)]
        for cid in args.capability:
            if cid not in owned:
                owned.append(cid)
            capability = caps[cid][2]
            # Only the roles that BUILD a capability become its owner and
            # implementer. A reviewer takes the lock and is recorded as holding
            # the capability, but overwriting `owner` made the honest reviewer
            # look like the implementer and blocked their own sign-off.
            if args.role in IMPLEMENTER_ROLES:
                capability["owner"] = args.agent
                recorded = text_of(capability.get("implementedBy"))
                recorded_agent = find_agent(manifest, recorded) if recorded else None
                superseded = (
                    args.role == "implementer"
                    and recorded_agent is not None
                    and text_of(recorded_agent.get("role")) == "architect"
                )
                if not recorded or superseded:
                    capability["implementedBy"] = args.agent
        record["ownsCapabilities"] = owned
        for cid, victim in stolen:
            # The victim must stop believing it holds the capability, or its own
            # release-claim can never reach `done`.
            victim_record = find_agent(manifest, text_of(victim)) if victim else None
            if victim_record is not None:
                victim_record["ownsCapabilities"] = [
                    v for v in as_list(victim_record.get("ownsCapabilities")) if v != cid
                ]
                history = [v for v in as_list(victim_record.get("previouslyOwned")) if isinstance(v, str)]
                if cid not in history:
                    history.append(cid)
                victim_record["previouslyOwned"] = history
        if stolen:
            note = text_of(record.get("notes"))
            steal_note = "; ".join(
                "stole " + cid + " from " + (owner or "unknown") + ": " + text_of(args.reason)
                for cid, owner in stolen
            )
            record["notes"] = (note + " | " if note else "") + steal_note
            taken = [v for v in as_list(record.get("stolenFrom")) if isinstance(v, str)]
            taken.extend(
                cid + " from " + (owner or "unknown") + ": " + text_of(args.reason)
                for cid, owner in stolen
            )
            record["stolenFrom"] = taken
        # The claim writes the manifest, so it goes through the same guard as add
        # and set: a claim must not be the way to smuggle in an invalid record.
        introduced, _warned = new_findings(
            before, manifest, project_root_of(manifest_path, manifest), manifest_path.parent
        )
        if introduced:
            for path in acquired:
                try:
                    path.unlink()
                except OSError:
                    pass
            stderr("Refusing to claim: the claim would introduce validation errors.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            return 2
        commit_manifest(manifest_path, manifest)
    for cid, owner in stolen:
        print("Stole claim on " + cid + " from " + (owner or "unknown"))
    print("Agent " + args.agent + " (" + args.role + ") claims: " + ", ".join(args.capability))
    return 0


def cmd_release_claim(args):
    manifest_path = resolve_manifest_path(args.manifest)
    lock_dir = manifest_path.parent / "locks"
    released = []
    skipped = []
    not_held = []
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        caps = capability_index(manifest)
        record = find_agent(manifest, args.agent)
        owned = as_list(record.get("ownsCapabilities")) if record else []
        targets = args.capability if args.capability else list(owned)
        for cid in targets:
            lock_path = lock_dir / lock_file_name(cid)
            owner = ""
            entry = caps.get(cid)
            held_in_manifest = cid in owned or (
                entry is not None and text_of(entry[2].get("owner")) == args.agent
            )
            if lock_path.exists():
                try:
                    owner = lock_path.read_text(encoding="utf-8").strip()
                except OSError:
                    owner = ""
                if owner and owner != args.agent:
                    skipped.append((cid, owner))
                    continue
                try:
                    lock_path.unlink()
                except OSError:
                    pass
            elif not held_in_manifest:
                # Releasing something this agent never held used to report
                # "Released: <it>" and exit 0.
                not_held.append(cid)
                continue
            released.append(cid)
            # `owner` says who holds it now and is blanked; `implementedBy` says who
            # built it and never is. Releasing used to erase the only record of the
            # implementer, after which the same agent could review its own work.
            if entry and text_of(entry[2].get("owner")) == args.agent:
                entry[2]["owner"] = ""
        if record is not None:
            remaining = [cid for cid in owned if cid not in released]
            record["ownsCapabilities"] = remaining
            history = [v for v in as_list(record.get("previouslyOwned")) if isinstance(v, str)]
            for cid in released:
                if cid not in history:
                    history.append(cid)
            record["previouslyOwned"] = history
            if not remaining:
                record["status"] = "done"
        commit_manifest(manifest_path, manifest)
    for cid, owner in skipped:
        stderr("Skipped " + cid + ": lock is held by '" + owner + "'")
    for cid in not_held:
        stderr("Skipped " + cid + ": agent '" + args.agent + "' does not hold it")
    print("Released: " + (", ".join(released) if released else "nothing"))
    return 3 if (skipped or not_held) else 0


def feedback_content_hash(item):
    """Identity of what an agent actually wrote, so an edit is noticed."""
    basis = "|".join(
        text_of(item.get(key)) for key in ("category", "scope", "observation", "proposedChange")
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def project_key_of(manifest, manifest_path):
    """The identity a project is counted by: `platform.id`, else its name.

    A bare display name is a poor key -- two unrelated projects both called
    'admin' were one, and one project renamed was two. `platform.id` is the
    stable handle; the name is only the fallback for manifests that predate it.
    `harvest --project` is deliberately NOT an input here: it renames the
    project in the output, and letting it re-key the project meant one codebase
    harvested once with the flag and once without counted as two projects.
    """
    platform = as_dict(manifest.get("platform"))
    return (
        text_of(platform.get("id"))
        or text_of(platform.get("name"))
        or project_root_of(manifest_path, manifest).name
    )


PLATFORM_ID_HINT = (
    "add a stable id once and never change it: `set --manifest <path> --path platform.id "
    "--value <name>-<6 random hex digits>` (the form `init` writes, e.g. acme-portal-3f9a2c)"
)


def check_platform_id(manifest, findings):
    """The learning loop's identity check: without platform.id a project is its name."""
    platform = manifest.get("platform")
    if isinstance(platform, dict) and not text_of(platform.get("id")):
        findings.add(
            "platform-id-missing",
            "platform.id",
            "is missing, so `harvest` counts this console by its display name: renaming it "
            "makes it a second project, and two consoles that share a name count as one; "
            + PLATFORM_ID_HINT,
        )


def cmd_harvest(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    store = global_store_dir(args.store)
    platform = as_dict(manifest.get("platform"))
    project = (
        text_of(args.project) or text_of(platform.get("name"))
        or project_root_of(manifest_path, manifest).name
    )
    key = project_key_of(manifest, manifest_path)
    if not text_of(platform.get("id")):
        stderr(
            "WARN: platform.id is missing, so this project is counted by its name '" + key
            + "': a rename will count as a new project. To fix it, " + PLATFORM_ID_HINT
        )
    stack = as_dict(platform.get("stack"))
    stack_summary = ", ".join(
        text_of(stack.get(name)) for name in ("frontend", "backend", "database") if text_of(stack.get(name))
    )
    try:
        known = {record.get("id") for record in read_observations(store)}
    except OSError as exc:
        raise ManifestError("Cannot read the observation store at " + str(store) + ": " + str(exc))
    harvested = []
    flipped = []
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        for _path, item in iter_named(manifest, "feedback"):
            chash = feedback_content_hash(item)
            status = item.get("status")
            edited = (
                status == "promoted"
                and text_of(item.get("promotedHash"))
                and text_of(item.get("promotedHash")) != chash
            )
            if status != "open" and not edited:
                continue
            record = {
                "project": project,
                "projectKey": key,
                "archetypes": [text_of(a) for a in as_list(platform.get("archetypes"))],
                "stack": stack_summary,
                "category": text_of(item.get("category")),
                "observation": text_of(item.get("observation")),
                "proposedChange": text_of(item.get("proposedChange")),
                "scope": text_of(item.get("scope")) or "unassigned",
                "evidence": [text_of(e) for e in as_list(item.get("evidence"))],
                "date": text_of(args.date) or "unknown",
                "contentHash": chash,
            }
            record["fingerprint"] = observation_fingerprint(record)
            # The content hash is part of the id: an entry edited after it was
            # promoted is a NEW observation, not a duplicate of the old one.
            record["id"] = key + ":" + text_of(item.get("id")) + ":" + chash
            flipped.append((item, chash))
            if record["id"] not in known:
                harvested.append(record)
        # Store first, manifest second. Marking feedback `promoted` before the
        # store write meant a mis-set ADMINWRIGHT_HOME stamped everything
        # promoted, crashed, and the entries were skipped forever.
        if harvested:
            try:
                append_observations(store, harvested)
            except OSError as exc:
                raise ManifestError(
                    "Cannot write the observation store at " + str(store) + " (" + str(exc)
                    + "); no feedback was marked promoted. Check ADMINWRIGHT_HOME."
                )
        for item, chash in flipped:
            item["status"] = "promoted"
            item["promotedHash"] = chash
        if flipped:
            commit_manifest(manifest_path, manifest)
    print("Harvested " + str(len(harvested)) + " observation(s) from project '" + project + "'")
    print("Store: " + str(store))
    if not text_of(args.date):
        stderr("WARN: no --date supplied; observations recorded as 'unknown'")
    return 0


STORE_FILES = ("observations.jsonl", "README.md", ".gitignore")
STORE_README = (
    "# Adminwright observation store\n\n"
    "Observations harvested from every project on this machine.\n"
    "Private by default. Nothing here leaves this machine except (a) `store sync`, which\n"
    "pushes these files to the private git remote you configured, and (b) `promote --export`,\n"
    "which writes a sanitised bundle that is shared only if you open a pull request with it.\n"
    "Observations quote your engineering notes: use a PRIVATE remote.\n"
)


def commit_store(store, message):
    run_git(store, "add", "--", *[name for name in STORE_FILES if (store / name).exists()])
    if run_git(store, "status", "--porcelain", "--", *STORE_FILES, check=False).stdout.strip():
        run_git(store, "-c", "user.name=adminwright", "-c", "user.email=adminwright@localhost",
                "commit", "-q", "-m", message)
        return True
    return False


def sync_store(store, args):
    if not (store / ".git").exists():
        raise ManifestError("store is not a git repository; run `store init --remote <url>` first")
    if not run_git(store, "config", "user.email", check=False).stdout.strip():
        run_git(store, "config", "user.name", "adminwright", check=False)
        run_git(store, "config", "user.email", "adminwright@localhost", check=False)
    has_remote = run_git(store, "remote", "get-url", "origin", check=False).returncode == 0
    local_path = store / "observations.jsonl"
    # The whole sync runs under the store lock: append_observations takes it, so
    # a harvest can no longer write between our read and our rewrite.
    with FileLock(store / ".lock"):
        # Normalise first, so what we commit is already the canonical union.
        write_observations(local_path, merge_observation_files(local_path))
        print(
            "Committed local observations."
            if commit_store(store, "observations: " + (text_of(args.date) or "sync"))
            else "Nothing new to commit."
        )
        if not has_remote:
            print("No remote configured; nothing to push.")
            return 0
        fetched = run_git(store, "fetch", "-q", "origin", check=False)
        if fetched.returncode != 0:
            raise ManifestError("could not reach the remote:\n" + fetched.stderr.strip())
        remote_ref = "origin/" + args.branch
        if run_git(store, "rev-parse", "--verify", "-q", remote_ref, check=False).returncode == 0:
            theirs = run_git(store, "show", remote_ref + ":observations.jsonl", check=False)
            if theirs.returncode == 0:
                incoming = store / ".incoming.jsonl"
                try:
                    write_text_file(incoming, theirs.stdout)
                    merged = merge_observation_files(local_path, incoming)
                finally:
                    if incoming.exists():
                        incoming.unlink()
                write_observations(local_path, merged)
                print("Merged " + str(len(merged)) + " observation(s) by id union.")
                # Commit the union BEFORE joining histories, so the tree the merge
                # keeps already holds every record from both sides.
                commit_store(store, "merge remote observations")
            # Join histories keeping OUR tree outright (`-s ours`). `-X ours` still
            # merges line by line and, when two devices held the same records in a
            # different order, re-applied the reorder as a deletion. Our tree is the
            # canonical union of both sides, so nothing on the remote can be lost by
            # ignoring it. --allow-unrelated-histories: each device runs `store init`
            # itself, so two stores share no ancestor the first time they meet.
            joined = run_git(
                store, "-c", "user.name=adminwright", "-c", "user.email=adminwright@localhost",
                "merge", "-q", "--no-edit", "--allow-unrelated-histories", "-s", "ours",
                remote_ref, check=False,
            )
            if joined.returncode != 0:
                run_git(store, "merge", "--abort", check=False)
                raise ManifestError(
                    "could not join the remote history:\n"
                    + (joined.stderr or joined.stdout).strip()
                    + "\nThe local store is unchanged. Resolve by hand in " + str(store)
                )
        pushed = run_git(store, "push", "-q", "origin", "HEAD:" + args.branch, check=False)
        if pushed.returncode != 0:
            raise ManifestError("push failed:\n" + (pushed.stderr or pushed.stdout).strip())
    print("Synced with " + args.branch + ".")
    return 0


def cmd_store(args):
    store = global_store_dir(args.store)
    action = args.action

    if action == "status":
        records = read_observations(store)
        projects = {
            text_of(r.get("projectKey")) or text_of(r.get("project"))
            for r in records
            if text_of(r.get("projectKey")) or text_of(r.get("project"))
        }
        print("Store: " + str(store))
        print("Exists: " + ("yes" if store.exists() else "no"))
        print("Observations: " + str(len(records)) + " across " + str(len(projects)) + " project(s)")
        if (store / ".git").exists():
            remote = run_git(store, "remote", "get-url", "origin", check=False)
            url = remote.stdout.strip() if remote.returncode == 0 else "(none)"
            head = run_git(store, "log", "-1", "--format=%h %ci", check=False)
            print("Git remote: " + url)
            print("Last commit: " + (head.stdout.strip() or "(none)"))
        else:
            print("Git: not initialised. Run `store init` to sync across devices.")
        return 0

    if action == "init":
        try:
            store.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ManifestError("Cannot create the store at " + str(store) + ": " + str(exc))
        observations = store / "observations.jsonl"
        if not observations.exists():
            write_text_file(observations, "")
        store_salt(store)
        if not (store / ".git").exists():
            run_git(store, "init", "-q")
            run_git(store, "checkout", "-q", "-B", "main", check=False)
        # Repo-local identity so merges and commits work without touching the
        # user's global git config. A merge commit needs this as much as a
        # regular commit does, which is why it is set once here rather than
        # passed per-command.
        run_git(store, "config", "user.name", "adminwright", check=False)
        run_git(store, "config", "user.email", "adminwright@localhost", check=False)
        gitignore = store / ".gitignore"
        if not gitignore.exists():
            # The salt is deliberately not synced: it exists so a published
            # fingerprint cannot be reversed, and a synced copy on every device
            # (and on the remote) would widen who can reverse one.
            write_text_file(gitignore, ".lock\nlocks/\n.incoming.jsonl\nsalt\noutbox/\n")
        readme = store / "README.md"
        if not readme.exists():
            write_text_file(readme, STORE_README)
        if args.remote:
            existing = run_git(store, "remote", "get-url", "origin", check=False)
            if existing.returncode == 0:
                run_git(store, "remote", "set-url", "origin", args.remote)
            else:
                run_git(store, "remote", "add", "origin", args.remote)
        print("Store ready at " + str(store))
        if args.remote:
            print("Remote: " + args.remote)
            print("Run `store sync` to push. Use a PRIVATE repository: observations quote your code.")
        else:
            print("No remote set. Add one with --remote <git-url> to sync across devices.")
        return 0

    if action == "sync":
        return sync_store(store, args)

    raise ManifestError("unknown store action: " + str(action))


def sanitise_candidate(candidate, salt=""):
    """Strip a promotion candidate down to what is safe to publish."""
    return {
        "fingerprint": candidate["fingerprint"],
        "category": candidate["category"],
        "scope": candidate["scope"],
        "observation": scrub_text(candidate["observation"]),
        "proposedChange": scrub_text(candidate["proposedChange"]),
        "archetypes": sorted({scrub_text(a) for a in candidate["archetypes"] if a}),
        "stacks": sorted({family for stack in candidate["stacks"] for family in coarsen_stack(stack)}),
        "projectCount": candidate["projectCount"],
        "projects": [project_fingerprint(name, salt) for name in candidate["projectKeys"]],
        # Evidence paths are dropped entirely: they name files in someone's
        # private repository and prove nothing to a reader who cannot open them.
    }


def record_project_key(record):
    """What counts as 'one project' for the promotion bar."""
    return text_of(record.get("projectKey")) or text_of(record.get("project"))


def cmd_promote(args):
    store = global_store_dir(args.store)
    records = read_observations(store)
    community_count = 0
    if getattr(args, "include_community", False):
        community = read_community_observations(getattr(args, "skill_root", None))
        community_count = len(community)
        records = records + community
    if not records:
        print("No observations in " + str(store) + ". Run `harvest` after a build.")
        return 0
    threshold = getattr(args, "similarity", None)
    if threshold is None:
        threshold = SIMILARITY_THRESHOLD
    groups = {}
    for cluster in cluster_observations(records, threshold):
        # The lowest member fingerprint is a stable cluster id: it does not
        # change when a third report joins an existing pair.
        key = min(text_of(item.get("fingerprint")) for item in cluster)
        groups.setdefault(key, []).extend(cluster)

    candidates = []
    for fingerprint, items in groups.items():
        # The bar counts distinct projects, not distinct name strings: a project
        # is its stable key (platform.id, else name), so a renamed project is
        # still one and two unrelated projects that share a name are still two.
        keys = sorted({record_project_key(item) for item in items if record_project_key(item)})
        names = sorted({text_of(item.get("project")) for item in items if text_of(item.get("project"))})
        archetypes = sorted({a for item in items for a in as_list(item.get("archetypes"))})
        stacks = sorted({text_of(item.get("stack")) for item in items if text_of(item.get("stack"))})
        category = text_of(items[0].get("category"))
        # A correction of wrong guidance promotes on first sighting; everything
        # else waits for corroboration from a second, distinct project.
        immediate = category == "incorrect-guidance"
        if len(keys) >= args.min_projects or immediate:
            candidates.append(
                {
                    "fingerprint": fingerprint,
                    "projects": names,
                    "projectKeys": keys,
                    "projectCount": len(keys),
                    "archetypes": archetypes,
                    "stacks": stacks,
                    "category": category,
                    "scope": text_of(items[0].get("scope")),
                    "proposedChange": text_of(items[0].get("proposedChange")),
                    "observation": text_of(items[0].get("observation")),
                    "evidence": sorted({e for item in items for e in as_list(item.get("evidence"))}),
                    "reason": "correction" if immediate else "seen on " + str(len(keys)) + " projects",
                    "communityEvidence": sum(1 for item in items if item.get("community")),
                    # Who was merged into this cluster, so a bad merge is visible.
                    "members": [
                        {
                            "id": observation_key(item),
                            "project": text_of(item.get("project")),
                            "date": text_of(item.get("date")),
                            "observation": text_of(item.get("observation"))[:160],
                        }
                        for item in items
                    ],
                }
            )
    candidates.sort(key=lambda c: (-c["projectCount"], c["scope"], c["fingerprint"]))

    if getattr(args, "export", None):
        salt = store_salt(store)
        bundle = {
            "bundleVersion": "1",
            "generatedFor": "adminwright community observations",
            "note": "Sanitised. Project names are salted one-way fingerprints; paths, hosts, "
                    "emails, URLs, credentials and evidence references are removed.",
            "observations": [sanitise_candidate(c, salt) for c in candidates],
        }
        out_path = Path(args.export).resolve()
        write_text_file(out_path, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
        print("Wrote " + str(len(bundle["observations"])) + " sanitised candidate(s) to " + str(out_path))
        print()
        print("READ THE FILE BEFORE SHARING IT.")
        print("Patterns removed emails, URLs, paths, IP addresses, hostnames, domains,")
        print("tokens and hashes, replaced project names with salted one-way fingerprints, and")
        print("dropped evidence references entirely.")
        print()
        print("What no pattern can catch: a company, product, customer or person's name")
        print("written as an ordinary word. Only you can see those. Read every line.")
        print()
        print("To contribute, open a pull request adding this file under")
        print("community/observations/ in the skill repository. Contribution is opt-in,")
        print("and anything contributed can be withdrawn by a pull request that deletes it.")
        return 0

    if args.json:
        print(json.dumps(
            {"store": str(store), "communityRecords": community_count, "candidates": candidates},
            indent=2, ensure_ascii=False))
        return 0

    print("# Promotion candidates")
    print()
    print("Store: " + str(store))
    print("Observations: " + str(len(records)) + " across " + str(len(groups)) + " distinct ideas")
    print("Bar: " + str(args.min_projects) + "+ distinct projects, or category incorrect-guidance")
    if community_count:
        print("Community records included: " + str(community_count)
              + " (corroborating only; they never adopt guidance on their own)")
    print()
    if not candidates:
        print("Nothing clears the bar yet. Keep harvesting.")
        return 0
    for candidate in candidates:
        print("## " + candidate["scope"] + " (" + candidate["reason"] + ")")
        print()
        print("- Category: " + candidate["category"])
        print("- Projects: " + ", ".join(candidate["projects"]))
        if candidate.get("communityEvidence"):
            print("- Community-sourced records: " + str(candidate["communityEvidence"])
                  + " (weigh separately; unverified)")
        if candidate["archetypes"]:
            print("- Archetypes: " + ", ".join(candidate["archetypes"]))
        if candidate["stacks"]:
            print("- Stacks: " + "; ".join(candidate["stacks"]))
        print("- Observation: " + candidate["observation"])
        print("- Proposed: " + candidate["proposedChange"])
        if candidate["evidence"]:
            print("- Evidence: " + ", ".join(candidate["evidence"]))
        print("- Merged from " + str(len(candidate["members"])) + " observation(s); if two of "
              "these are not the same finding, do not adopt them as one:")
        for member in candidate["members"]:
            print("    - " + member["id"] + " (" + (member["date"] or "undated") + "): "
                  + member["observation"])
        print()
    print("Record each accepted candidate with `lesson add`, then edit the reference it")
    print("names and open a pull request. Promotion is a judgement call: read")
    print("references/skill-evolution.md before adopting anything here.")
    return 0


def in_plugin_cache(path):
    parts = [part.lower() for part in Path(path).parts]
    return any(parts[i] == "plugins" and parts[i + 1] == "cache" for i in range(len(parts) - 1))


# Variables that point git at a repository other than the one around its -C
# directory. A validate run from a project's git hook inherits GIT_DIR, and the
# skill must not read the PROJECT's repository as its own.
GIT_LOCATION_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX",
)


def skill_git(*args):
    """Run git in the skill root; None when git is missing or does not answer."""
    environment = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARIABLES}
    try:
        return subprocess.run(
            ["git", "-C", str(SKILL_ROOT)] + list(args),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def same_directory(left, right):
    """True when two spellings name one directory (case, separators, links)."""
    def canonical(value):
        return os.path.normcase(os.path.realpath(str(value)))
    if canonical(left) == canonical(right):
        return True
    try:
        return os.path.samefile(str(left), str(right))
    except OSError:
        return False


_SKILL_CHECKOUT = []


def skill_is_checkout():
    """True only when the skill root is the top of its own git work tree.

    A marketplace install lives under ~/.claude/plugins/cache/<marketplace>/
    <plugin>/<sha>/ and is replaced on update, so anything written into its
    lessons/ or references/ is silently discarded. A copy vendored into a
    project (<project>/.claude/skills/adminwright, a documented install) is
    overwritten by the next copy. Both can sit inside SOME git repository -- a
    git-tracked home, the project itself -- so "a .git in any ancestor" is not
    the test: the work tree's top must be the skill root itself.
    """
    if _SKILL_CHECKOUT:
        return _SKILL_CHECKOUT[0]
    result = False
    if not in_plugin_cache(SKILL_ROOT) and any(
        (candidate / ".git").exists() for candidate in [SKILL_ROOT] + list(SKILL_ROOT.parents)
    ):
        done = skill_git("rev-parse", "--show-toplevel")
        if done is not None and done.returncode == 0 and done.stdout.strip():
            result = same_directory(done.stdout.strip(), SKILL_ROOT)
    _SKILL_CHECKOUT.append(result)
    return result


def clean_block(text):
    """Keep a multi-line argument from forging a heading the format forbids."""
    return "\n".join(
        ("\\" + line if line.lstrip().startswith("#") else line) for line in text.strip().splitlines()
    )


SKILL_SCOPE_HINT = (
    "name the one file or area of the skill it would change, as a path from the skill root "
    "that exists, e.g. SKILL.md, references/architecture.md, scripts, agents, "
    "assets/admin-core-schema, lessons or INTEGRATIONS.md"
)


def skill_path_exists(root, relative):
    """Whether root/relative exists, matching every segment's case exactly.

    Exact case on every platform: 'skill.md' exists on Windows and not on Linux,
    and a scope must bucket the same way wherever the store was harvested.
    """
    current = Path(root)
    for segment in relative.split("/"):
        try:
            if segment not in os.listdir(str(current)):
                return False
        except OSError:
            return False
        current = current / segment
    return True


def skill_scope_problem(scope, roots=None):
    """None when `scope` names an existing file or area of the skill, else what is wrong.

    The one validator for feedback[].scope (add refuses, validate warns) and
    `lesson add --scope`: they used to disagree -- feedback accepted a
    nonexistent references/nope.md and 'scripts/../../etc/passwd' but refused
    assets/, INTEGRATIONS.md and lessons/, while lesson add refused the bare
    'scripts' and 'agents' scopes that feedback and promote use.
    """
    value = scope.strip() if isinstance(scope, str) else ""
    if not value:
        return "is missing"
    if "\\" in value:
        return "uses '\\'; write the path with '/'"
    if not FEEDBACK_SCOPE_PATTERN.match(value):
        return "is not a relative path inside the skill (no leading '/', and no '.', '..' or hidden segments)"
    if value == "references":
        return "names the whole references/ folder; name the one reference file"
    if any(skill_path_exists(root, value) for root in (roots or (SKILL_ROOT,))):
        return None
    return "names nothing that exists in the skill"


def build_lesson_text(lesson_id, args, date):
    evidence = [clean_block(item) for item in (args.evidence or [])]
    lines = [
        "---",
        "id: " + yaml_quote(lesson_id),
        "title: " + yaml_quote(args.title),
        "date: " + yaml_quote(date),
        "category: " + args.category,
        "scope: " + yaml_quote(args.scope),
        "status: " + args.status,
        "confidence: " + args.confidence,
        "platforms: " + yaml_list(args.platform or []),
        "---",
        "",
        "## Trigger",
        "",
        clean_block(args.trigger),
        "",
        "## Rule",
        "",
        clean_block(args.rule),
        "",
        "## Evidence",
        "",
    ]
    lines.extend(("- " + item) for item in evidence) if evidence else lines.append("- none recorded")
    lines.extend(["", "## Proposed edit", "", "## Review notes", ""])
    return "\n".join(lines)


def lesson_date(raw):
    """A caller-supplied YYYY-MM-DD, checked against the calendar (the clock is never read).

    Written unvalidated, `--date $'2026-09-23\\nstatus: adopted\\n---'` closed the
    frontmatter early and opened the lesson as adopted, past the refusal below.
    """
    import datetime

    value = raw.strip() if isinstance(raw, str) else ""
    if value == "":
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            datetime.date(int(value[:4]), int(value[5:7]), int(value[8:]))
            return value
        except ValueError:
            pass
    raise ManifestError("--date must be a calendar date written YYYY-MM-DD, e.g. 2026-09-23; got "
                        + json.dumps(value))


def cmd_lesson_add(args):
    if args.status == "adopted":
        raise ManifestError(
            "a lesson is opened as 'proposed'; adoption is a separate reviewed edit "
            "(lessons/README.md, section 7), never part of opening it"
        )
    date = lesson_date(args.date) or "unknown"
    if date == "unknown":
        stderr("WARN: no --date supplied; recorded as 'unknown'")
    outbox = False
    if args.lessons_dir:
        lessons_dir = Path(args.lessons_dir).resolve()
    elif skill_is_checkout():
        lessons_dir = SKILL_ROOT / "lessons"
    else:
        outbox = True
        lessons_dir = global_store_dir() / "outbox" / "lessons"
    # An explicit --lessons-dir may sit in another clone of the skill: accept a
    # scope that exists there too.
    roots = (lessons_dir.parent, SKILL_ROOT) if args.lessons_dir else (SKILL_ROOT,)
    problem = skill_scope_problem(args.scope, roots)
    if problem:
        raise ManifestError("--scope " + json.dumps(args.scope) + " " + problem + "; " + SKILL_SCOPE_HINT)
    try:
        lessons_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ManifestError("Cannot create " + str(lessons_dir) + ": " + str(exc))
    slug = slugify(args.title)
    if outbox:
        # Not a checkout: numbering and the index belong to the repository, and a
        # plugin cache is replaced on update. Park the lesson where it survives.
        path = lessons_dir / ((date if date != "unknown" else "undated") + "-" + slug + ".md")
        write_text_file(path, build_lesson_text("pending", args, date))
        print("Wrote " + str(path))
        print(
            "This copy of adminwright (" + str(SKILL_ROOT) + ") is not a git checkout of the "
            "skill repository: a plugin cache is replaced on update, and a copy vendored into a "
            "project is overwritten by the next copy. So the lesson was written to your outbox "
            "instead of lessons/.\n"
            "To adopt it: clone github.com/TarikAI/adminwright, copy the file into lessons/ under "
            "the next id, run `lesson index`, edit the reference it names, and open a pull request."
        )
        return 0
    with FileLock(lessons_dir / ".lock"):
        lesson_id = "%04d" % next_lesson_number(lessons_dir)
        path = lessons_dir / (lesson_id + "-" + slug + ".md")
        write_text_file(path, build_lesson_text(lesson_id, args, date))
        rebuild_lesson_index(lessons_dir)
    print("Wrote " + str(path))
    print("Updated " + str(lessons_dir / "index.md"))
    return 0


def cmd_lesson_index(args):
    lessons_dir = Path(args.lessons_dir).resolve() if args.lessons_dir else SKILL_ROOT / "lessons"
    if not lessons_dir.exists():
        stderr("No lessons directory at " + str(lessons_dir))
        return 2
    with FileLock(lessons_dir / ".lock"):
        level = rebuild_lesson_index(lessons_dir)
    print("Rebuilt " + str(lessons_dir / "index.md") + "; guidance level " + level)
    return 0


def cmd_lesson_list(args):
    lessons_dir = Path(args.lessons_dir).resolve() if args.lessons_dir else SKILL_ROOT / "lessons"
    if not lessons_dir.exists():
        stderr("No lessons directory at " + str(lessons_dir))
        return 2
    rows = []
    for path in lesson_files(lessons_dir):
        fields = read_lesson_frontmatter(path)
        if args.status and fields.get("status") != args.status:
            continue
        rows.append(fields)
    print("| id | title | category | scope | status | confidence | date |")
    print("|---|---|---|---|---|---|---|")
    for fields in rows:
        print(
            "| "
            + " | ".join(
                cell(fields.get(key, ""))
                for key in ("id", "title", "category", "scope", "status", "confidence", "date")
            )
            + " |"
        )
    return 0


def cmd_lesson(args):
    if args.lesson_command == "add":
        return cmd_lesson_add(args)
    if args.lesson_command == "index":
        return cmd_lesson_index(args)
    return cmd_lesson_list(args)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


class VersionAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print("adminwright " + skill_version() + " (ruleset " + ruleset_version() + ")")
        parser.exit(0)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Model, validate, and audit admin-console capability manifests",
        epilog=(
            "exit codes: 0 ok; 1 the manifest was read and has findings at error severity; "
            "2 usage or IO failure, including an add/set/claim refused by the write guard "
            "(the manifest was left unmodified); 3 capability claim conflict"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action=VersionAction,
        help="print the installed commit and the rule-set hash, then exit",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create .admin-console/manifest.json")
    init_parser.add_argument(
        "--project-root", default=".",
        help="the repository the console administers; relative evidence paths resolve against it",
    )
    init_parser.add_argument(
        "--manifest", metavar="PATH",
        help="where to write the manifest (default <project-root>/.admin-console/manifest.json); "
             "use one per console in a monorepo",
    )
    init_parser.add_argument("--name", required=True)
    init_parser.add_argument("--archetype", action="append", default=[])
    init_parser.add_argument("--profile", choices=PROFILES, default=DEFAULT_PROFILE)
    init_parser.add_argument("--stack-frontend")
    init_parser.add_argument("--stack-backend")
    init_parser.add_argument("--stack-database")
    init_parser.add_argument("--stack-auth")
    init_parser.add_argument("--stack-jobs")
    init_parser.add_argument("--stack-hosting")
    init_parser.add_argument("--stack-design-system")
    init_parser.add_argument("--stack-admin-framework")
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=cmd_init)

    migrate_parser = subparsers.add_parser("migrate", help="Upgrade a 1.0 manifest to 2.0")
    migrate_parser.add_argument("--manifest", required=True)
    migrate_parser.add_argument("--write", action="store_true")
    migrate_parser.set_defaults(func=cmd_migrate)

    validate_parser = subparsers.add_parser("validate", help="Validate structure, evidence, and coverage")
    validate_parser.add_argument("--manifest", required=True)
    validate_parser.add_argument("--project-root")
    validate_parser.add_argument("--phase", choices=("plan", "release"), default="plan")
    validate_parser.add_argument("--profile", choices=PROFILES)
    validate_parser.add_argument(
        "--allow-lower-profile", action="store_true",
        help="at --phase release, permit --profile below the manifest's declared profile",
    )
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(func=cmd_validate)

    report_parser = subparsers.add_parser("report", help="Print a coverage summary")
    report_parser.add_argument("--manifest", required=True)
    report_parser.add_argument("--json", action="store_true")
    report_parser.set_defaults(func=cmd_report)

    coverage_parser = subparsers.add_parser("coverage", help="Audit structural gaps only")
    coverage_parser.add_argument("--manifest", required=True)
    coverage_parser.add_argument(
        "--project-root",
        help="accepted so validate and coverage can share arguments; coverage is structural "
             "and reads no files, so it is not used",
    )
    coverage_parser.add_argument("--profile", choices=PROFILES)
    coverage_parser.set_defaults(func=cmd_coverage)

    emit_parser = subparsers.add_parser("emit", help="Generate a working document from the manifest")
    emit_parser.add_argument(
        "--manifest", help="the manifest to render (not needed for --format rule-matrix)"
    )
    emit_parser.add_argument("--format", required=True, choices=sorted(EMITTERS) + ["rule-matrix"])
    emit_parser.add_argument("--out")
    emit_parser.set_defaults(func=cmd_emit)

    add_parser = subparsers.add_parser("add", help="Append one object to a manifest collection")
    add_parser.add_argument("--manifest", required=True)
    add_parser.add_argument("--kind", required=True, choices=sorted(KIND_TARGETS))
    payload = add_parser.add_mutually_exclusive_group(required=True)
    payload.add_argument(
        "--json", help="JSON object, array of objects, @file, or - for stdin"
    )
    payload.add_argument(
        "--json-file", metavar="PATH",
        help="read the JSON object or array from a file (PowerShell-safe; a BOM is accepted)",
    )
    add_parser.add_argument("--entity", help="entity id, required when --kind capability")
    add_parser.add_argument("--allow-invalid", action="store_true")
    add_parser.set_defaults(func=cmd_add)

    set_parser = subparsers.add_parser("set", help="Set one field by path expression")
    set_parser.add_argument("--manifest", required=True, help="path to manifest.json")
    set_parser.add_argument(
        "--path", required=True,
        help="field to set, e.g. 'entities[user].capabilities[user.suspend].status' or a path "
             "copied from `validate` output (manifest.roles[0].name, ...evidence[0])",
    )
    set_parser.add_argument(
        "--value", required=True,
        help="new value, typed by the field's current type. Lists take comma-separated text or "
             "JSON; objects take JSON; strings are stored as written",
    )
    set_parser.add_argument(
        "--allow-invalid", action="store_true",
        help="write even if the change introduces validation errors (they are listed)",
    )
    set_parser.set_defaults(func=cmd_set)

    remove_parser = subparsers.add_parser(
        "remove", help="Delete one item; refused if anything still depends on it"
    )
    remove_parser.add_argument("--manifest", required=True, help="path to manifest.json")
    remove_parser.add_argument("--kind", required=True, choices=sorted(KIND_TARGETS))
    remove_parser.add_argument("--id", required=True, help="id of the item to delete")
    remove_parser.add_argument("--entity", help="entity id, to disambiguate when --kind capability")
    remove_parser.add_argument(
        "--allow-invalid", action="store_true",
        help="delete even if that leaves dangling references (they are listed)",
    )
    remove_parser.set_defaults(func=cmd_remove)

    rename_parser = subparsers.add_parser(
        "rename", help="Change an item's id and every reference to it"
    )
    rename_parser.add_argument("--manifest", required=True, help="path to manifest.json")
    rename_parser.add_argument("--kind", required=True, choices=sorted(KIND_TARGETS))
    rename_parser.add_argument("--id", required=True, help="current id")
    rename_parser.add_argument("--to", required=True, help="new id")
    rename_parser.add_argument("--entity", help="entity id, to disambiguate when --kind capability")
    rename_parser.add_argument(
        "--allow-invalid", action="store_true",
        help="rename even if the result introduces validation errors (they are listed)",
    )
    rename_parser.set_defaults(func=cmd_rename)

    claim_parser = subparsers.add_parser("claim", help="Take exclusive ownership of capabilities")
    claim_parser.add_argument("--manifest", required=True, help="path to manifest.json")
    claim_parser.add_argument("--agent", required=True, help="your agent id (registered on first claim)")
    claim_parser.add_argument("--role", required=True, choices=AGENT_ROLES, help="the pass you are running")
    claim_parser.add_argument(
        "--capability", action="append", required=True,
        help="capability id to claim; repeat for several",
    )
    claim_parser.add_argument(
        "--force-steal", action="store_true",
        help="take a capability another agent holds; requires --reason and is recorded on both agents",
    )
    claim_parser.add_argument("--reason", help="why the claim is being stolen (required with --force-steal)")
    claim_parser.set_defaults(func=cmd_claim)

    release_parser = subparsers.add_parser("release-claim", help="Release capability ownership")
    release_parser.add_argument("--manifest", required=True, help="path to manifest.json")
    release_parser.add_argument("--agent", required=True, help="the agent releasing its claims")
    release_parser.add_argument(
        "--capability", action="append", default=[],
        help="capability to release; repeat for several; omit to release everything this agent holds",
    )
    release_parser.set_defaults(func=cmd_release_claim)

    lesson_parser = subparsers.add_parser("lesson", help="Record or list skill lessons")
    lesson_sub = lesson_parser.add_subparsers(dest="lesson_command", required=True)
    lesson_add = lesson_sub.add_parser("add", help="Write lessons/NNNN-<slug>.md")
    lesson_add.add_argument("--title", required=True)
    lesson_add.add_argument("--category", required=True, choices=FEEDBACK_CATEGORIES)
    lesson_add.add_argument("--scope", required=True)
    lesson_add.add_argument("--trigger", required=True)
    lesson_add.add_argument("--rule", required=True)
    lesson_add.add_argument("--evidence", action="append", default=[])
    lesson_add.add_argument("--platform", action="append", default=[])
    lesson_add.add_argument("--date", help="caller-supplied date; the script never reads the clock")
    lesson_add.add_argument("--status", choices=LESSON_STATUSES, default="proposed")
    lesson_add.add_argument("--confidence", choices=LESSON_CONFIDENCES, default="observed-once")
    lesson_add.add_argument("--lessons-dir", help="override the skill lessons directory")
    lesson_add.set_defaults(func=cmd_lesson)
    harvest = subparsers.add_parser(
        "harvest", help="Move a project's feedback[] into the cross-project store"
    )
    harvest.add_argument("--manifest", required=True)
    harvest.add_argument(
        "--project",
        help="the name shown for this project in the store and in promote output (default "
             "platform.name); it never changes how the project is counted, which is by "
             "platform.id",
    )
    harvest.add_argument("--store", help="defaults to $ADMINWRIGHT_HOME or ~/.adminwright")
    harvest.add_argument("--date", help="caller-supplied date; the script never reads the clock")
    harvest.set_defaults(func=cmd_harvest)

    promote = subparsers.add_parser(
        "promote", help="List observations that clear the promotion bar across projects"
    )
    promote.add_argument("--store", help="defaults to $ADMINWRIGHT_HOME or ~/.adminwright")
    promote.add_argument("--min-projects", type=int, default=2)
    promote.add_argument(
        "--similarity",
        type=float,
        default=SIMILARITY_THRESHOLD,
        help="content-word overlap needed to treat two observations as one "
             "finding (0.0-1.0; lower groups more aggressively). Three shared "
             "non-generic words and a match with the group's members are always required",
    )
    promote.add_argument("--json", action="store_true")
    promote.add_argument(
        "--include-community",
        action="store_true",
        help="also weigh contributed observations from community/observations/",
    )
    promote.add_argument("--skill-root", help="override where community/ is read from")
    promote.add_argument(
        "--export",
        metavar="FILE",
        help="write sanitised candidates as a shareable bundle instead of printing",
    )
    promote.set_defaults(func=cmd_promote)

    store_parser = subparsers.add_parser(
        "store", help="Manage the cross-project observation store (multi-device sync)"
    )
    store_parser.add_argument("action", choices=("init", "sync", "status"))
    store_parser.add_argument("--store", help="defaults to $ADMINWRIGHT_HOME or ~/.adminwright")
    store_parser.add_argument("--remote", help="git URL to sync with; use a PRIVATE repository")
    store_parser.add_argument("--branch", default="main")
    store_parser.add_argument("--date", help="caller-supplied date; the script never reads the clock")
    store_parser.set_defaults(func=cmd_store)

    lesson_index = lesson_sub.add_parser("index", help="Rebuild lessons/index.md from the lesson files")
    lesson_index.add_argument("--lessons-dir")
    lesson_index.set_defaults(func=cmd_lesson)
    lesson_list = lesson_sub.add_parser("list", help="Print the lessons index")
    lesson_list.add_argument("--status", choices=LESSON_STATUSES)
    lesson_list.add_argument("--lessons-dir")
    lesson_list.set_defaults(func=cmd_lesson)
    return parser


def configure_console():
    """Speak UTF-8 no matter what the console or pipe says it is.

    With stdout a pipe -- which is how every agent harness runs this -- Python on
    Windows picks cp1252, and any character outside it (an emoji in a platform name,
    a CJK role) turned `validate`, `report`, `emit`, `coverage`, `harvest`, `promote`
    and `lesson list` into a traceback with exit 1. `errors="replace"` means output
    can never fail on encoding, whatever the reader's code page.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main():
    configure_console()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ManifestError as exc:
        stderr("ERROR: " + str(exc))
        return 2
    except BrokenPipeError:
        # `... | head`: the reader left early. Not a failure of this command.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 0
    except KeyboardInterrupt:
        stderr("Interrupted")
        return 2
    except OSError as exc:
        # A read-only file, a full disk, a path that is a file: usage or IO
        # failures, never a traceback.
        stderr("ERROR: " + type(exc).__name__ + ": " + str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
