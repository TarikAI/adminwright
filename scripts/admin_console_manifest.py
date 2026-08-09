#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Model, validate, and audit admin-console capability manifests (manifest v2).

Standard library only. Runs on Windows and POSIX.

Commands: init, migrate, validate, report, coverage, emit, add, set, claim,
release-claim, lesson.

Exit codes: 0 ok, 1 findings at error severity, 2 usage or IO failure,
3 capability claim conflict.

`add` and `set` refuse a write that would introduce new errors, and exit 2 for
it. That is deliberate: the manifest was not modified, so the caller's request
could not be carried out -- the same class of outcome as a bad path or malformed
JSON. Exit 1 is reserved for "the manifest was read and it has findings", which
is what a caller polling validate needs to distinguish. Pass --allow-invalid to
write anyway; it reports everything it waved through.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
AGENT_ROLES = ("architect", "implementer", "ux-reviewer", "qa", "security")
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
PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:mock(?:s|ed|ing)?|fake(?:s|d)?|stub(?:s|bed|bing)?|dumm(?:y|ies)"
    r"|placeholder(?:s)?|sample(?:s)?|lorem|random(?:ly)?|demo-only"
    r"|todo(?:s)?|tbd|fixme|coming-soon|wip|xxx+)\b",
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
        "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
        "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
        "\u03a1": "P", "\u03a4": "T", "\u03a7": "X", "\u03bf": "o", "\u03b1": "a",
        "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M",
        "\u041d": "H", "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T",
        "\u0425": "X", "\u0406": "I", "\uff4f": "o", "\uff41": "a",
    }
)

# Tokens dangerous enough that a substring match is worth the occasional false
# positive. Word boundaries miss 'gmockRepositoryImpl' and 'usemockdata' because
# there is no case transition to split on, and those are exactly the names that
# get written when someone is wiring a temporary shim.
# A false positive is renamed or registered in declaredStatic[] in seconds; a
# false negative ships mock data to production.
SUBSTRING_TOKENS = (
    "mock", "fake", "stub", "dummy", "placeholder", "lorem",
    "todo", "fixme", "tbd", "notimplemented", "comingsoon",
)
SUBSTRING_PATTERN = re.compile("|".join(SUBSTRING_TOKENS), re.I)

# Ordinary English words that happen to contain a banned token. Stripped before
# the substring pass so 'stubbornRetry' and 'hammock' stay legal identifiers.
# Keep this list short: every entry is a hole someone could name a shim after.
SUBSTRING_EXCEPTIONS = re.compile(r"stubborn|hammock|smock|mockingbird", re.I)


def substring_hit(squashed):
    return SUBSTRING_PATTERN.search(SUBSTRING_EXCEPTIONS.sub("", squashed))


def fold_confusables(text):
    return text.translate(CONFUSABLES)
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
    "screen-responsive": E3,
    "screen-accessibility": E3,
    "gate-missing": E3,
    "gate-not-passed": E3,
    "gate-evidence-missing": E3,
    "gate-rationale-missing": E3,
    "gap-unresolved": E3,
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
    }
)

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


def load_json(path):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ManifestError("File not found: " + str(path))
    except UnicodeDecodeError:
        # A UTF-16 manifest saved by a Windows editor is a realistic accident,
        # not an exotic attack. Fail as a usage error, not a traceback.
        raise ManifestError(
            str(path) + " is not valid UTF-8. Re-save it as UTF-8 without a BOM."
        )
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(str(tmp), str(path))


def write_json(path, data):
    write_text_file(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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


def stderr(message):
    print(message, file=sys.stderr)


class FileLock:
    """Exclusive lock on a single path. Breaks locks older than the stale window."""

    def __init__(self, path, stale=LOCK_STALE_SECONDS, timeout=LOCK_TIMEOUT_SECONDS):
        self.path = Path(path)
        self.stale = stale
        self.timeout = timeout
        self.held = False

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
                if age is not None and age > self.stale:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.time() >= deadline:
                    raise LockError(
                        "Manifest lock is held by another process: " + str(self.path)
                    )
                time.sleep(0.2)
                continue
            except OSError as exc:
                raise LockError("Cannot create lock " + str(self.path) + ": " + str(exc))
            os.write(handle, ("pid=" + str(os.getpid()) + "\n").encode("utf-8"))
            os.close(handle)
            self.held = True
            return self

    def __exit__(self, exc_type, exc, tb):
        if self.held:
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
    file_part = str(raw).split("#", 1)[0].strip()
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


def path_state(candidate):
    """Return 'missing', 'directory', 'empty', 'placeholder', or 'ok'.

    A directory is never evidence: any folder holding one unrelated file used to
    satisfy the gate. Whitespace-only files have nonzero size, so byte count
    alone is not an emptiness test. And the file's CONTENT is scanned, because
    an evidence file reading "TODO: replace with real results" passed every
    check when only its path string was examined.
    """
    try:
        if candidate.is_dir():
            return "directory"
        if not candidate.exists():
            return "missing"
        if candidate.stat().st_size == 0:
            return "empty"
        body = read_head(candidate)
        if not body.strip():
            return "empty"
        folded = fold_confusables(body)
        squashed = folded.replace(" ", "").replace("-", "").replace("_", "")
        if PLACEHOLDER_PATTERN.search(split_identifiers(folded)) or substring_hit(squashed):
            return "placeholder"
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


def check_evidence_list(
    values,
    field_path,
    findings,
    project_root,
    required,
    missing_rule="evidence-path-missing",
):
    """Validate one evidence/test list. Returns the resolvable local paths."""
    resolved = []
    entries = [value for value in as_list(values) if isinstance(value, str)]
    for index, raw in enumerate(entries):
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
        if state == "placeholder":
            findings.add(
                "placeholder-scan",
                entry_path,
                "evidence file contains placeholder text: " + raw.strip(),
            )
            continue
        resolved.append(candidate)
    if required and entries and not resolved:
        findings.add(
            "evidence-not-resolvable",
            field_path,
            "no entry resolves to a non-empty local path; "
            "URL and manual entries are supplemental only",
        )
    return resolved


def capability_tokens(capability):
    tokens = set()
    cid = text_of(capability.get("id"))
    if cid:
        tokens.add(cid)
        for separator in ("-", "_", " ", "/"):
            tokens.add(cid.replace(".", separator))
    for key in ("serverOperations", "uiRoutes"):
        for value in as_list(capability.get(key)):
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


def static_index(manifest, findings):
    """Map declaredStatic paths that are allowed to escape the placeholder scan."""
    allowed = set()
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


def run_placeholder_scan(manifest, findings, release):
    targets = scan_targets(manifest, release)
    allowed = static_index(manifest, findings)
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
            match = PLACEHOLDER_PATTERN.search(scannable) or substring_hit(
                folded.replace(" ", "").replace("-", "").replace("_", "")
            )
            if match:
                findings.add(
                    "placeholder-scan",
                    entry_path,
                    "placeholder token '"
                    + match.group(0)
                    + "' in '"
                    + text
                    + "'; replace it with the real value or register it in declaredStatic[]",
                )
    for target in sorted(allowed):
        if target not in known_paths:
            findings.add(
                "declared-static-unused",
                "declaredStatic",
                "path '" + target + "' does not match any scanned manifest field",
            )


# --------------------------------------------------------------------------
# shared shape checks
# --------------------------------------------------------------------------


def check_id(value, path, findings):
    if not isinstance(value, str) or not ID_PATTERN.match(value):
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
                findings.add("id-duplicate", key, "duplicate id '" + value + "'")
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
    escape_text = collect_escape_text(manifest)
    for eid, entity in iter_entities(manifest):
        path = "entities[" + eid + "]"
        entity_caps = [
            cap for cap in as_list(entity.get("capabilities")) if isinstance(cap, dict)
        ]
        kinds = {cap.get("kind") for cap in entity_caps}
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
            if state not in observed and state.lower() not in escape_text:
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
                and state.lower() not in escape_text
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


def collect_escape_text(manifest):
    """Lowercased text of gap and declaredStatic rationales, used as the observability escape."""
    chunks = []
    for _path, gap in iter_named(manifest, "gaps"):
        chunks.append(text_of(gap.get("description")))
        chunks.append(text_of(gap.get("rationale")))
    for _path, entry in iter_named(manifest, "declaredStatic"):
        chunks.append(text_of(entry.get("path")))
        chunks.append(text_of(entry.get("reason")))
    return " ".join(chunks).lower()


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate_manifest(manifest, phase, project_root, profile):
    findings = Findings(profile, phase)
    release = phase == "release"
    check_required(manifest, TOP_LEVEL_KEYS, "manifest", findings)

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
        if not roles:
            findings.add("required-field-empty", "roles", "release requires at least one modeled role")
        if not as_list(manifest.get("entities")):
            findings.add("required-field-empty", "entities", "release requires at least one managed entity")
        if not as_list(manifest.get("screens")):
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
        if status == "implemented":
            for key in (
                "outcome",
                "roles",
                "uiRoutes",
                "serverOperations",
                "authorizationPolicies",
                "dataBinding",
                "tests",
                "evidence",
            ):
                if not nonempty(capability.get(key)):
                    findings.add("required-field-empty", path + "." + key, "must not be empty for an implemented capability")
            if release and capability.get("reviewStatus") != "reviewed":
                findings.add(
                    "capability-unreviewed",
                    path + ".reviewStatus",
                    "an implemented capability must be reviewed by an agent other than its implementer",
                )
            check_reviewer_identity(capability, path, findings)


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
        check_nonempty(screen, ("route", "purpose", "roles", "dataSources", "states"), path, findings)
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
            check_evidence_list(values.get("evidence"), path, findings, project_root, True)

    authorization = as_dict(cross.get("authorization"))
    policy_path = "crossCutting.authorization.policyTests"
    if not nonempty(authorization.get("policyTests")):
        findings.add(
            "authorization-policy-tests",
            policy_path,
            "record the authorization test files that prove default-deny and scope checks",
        )
    else:
        check_evidence_list(authorization.get("policyTests"), policy_path, findings, project_root, True)

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
            if isinstance(value, str) and value not in known
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


def check_reviewer_identity(capability, path, findings):
    """Verify the reviewer is not the implementer.

    `capability-unreviewed` claimed review was done "by an agent other than its
    implementer", but nothing recorded who reviewed, so the same agent could set
    reviewStatus on its own work. reviewedBy makes the claim checkable.
    """
    if capability.get("reviewStatus") != "reviewed":
        return
    reviewer = text_of(capability.get("reviewedBy"))
    owner = text_of(capability.get("owner"))
    if not reviewer:
        findings.add(
            "reviewer-identity",
            path + ".reviewedBy",
            "reviewStatus is 'reviewed' but no reviewer is recorded; "
            "an unattributed review cannot be distinguished from self-review",
        )
        return
    if owner and reviewer == owner:
        findings.add(
            "reviewer-identity",
            path + ".reviewedBy",
            "reviewer '" + reviewer + "' is the implementer; review must be a separate pass "
            "by a different agent, or an explicitly declared pass that re-reads the code",
        )


def validate_evidence(manifest, findings, project_root):
    """Release-phase file checks for capability, screen, and queue evidence."""
    for path, _entity, capability in iter_capabilities(manifest):
        if capability.get("status") != "implemented":
            continue
        test_paths = check_evidence_list(
            capability.get("tests"), path + ".tests", findings, project_root, True
        )
        check_evidence_list(
            capability.get("evidence"), path + ".evidence", findings, project_root, True
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
                "no listed test file mentions the capability id, a server operation, or a UI route; "
                "the link between manifest and test is unproven",
            )
    for path, screen in iter_named(manifest, "screens"):
        if screen.get("status") != "implemented":
            continue
        screen_tests = check_evidence_list(
            screen.get("tests"), path + ".tests", findings, project_root, True
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
                check_evidence_list(item.get("tests"), path + ".tests", findings, project_root, True)
    for path, gap in iter_named(manifest, "gaps"):
        if gap.get("status") == "fixed":
            check_evidence_list(gap.get("evidence"), path + ".evidence", findings, project_root, True)


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
        for role_id in as_list(capability.get("roles")):
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
        for role_id in as_list(capability.get("roles")):
            caps_by_role.setdefault(role_id, []).append((entity, capability))
    screens_by_role = {}
    for _path, screen in iter_named(manifest, "screens"):
        for role_id in as_list(screen.get("roles")):
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
            for key in ("dataBinding", "idempotency", "concurrency", "recovery", "owner"):
                ensure_key(capability, key, "", cap_path, changes)
            ensure_key(capability, "reviewStatus", "unreviewed", cap_path, changes)

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

    for key in ("declaredStatic", "feedback", "agents"):
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
        "reviewStatus": "unreviewed",
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
        "proposedChange": "",
        "evidence": [],
        "status": "open",
    },
    "agent": {
        "id": "",
        "role": "implementer",
        "ownsCapabilities": [],
        "ownsScreens": [],
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
    return merged


def parse_json_argument(raw):
    text = raw
    if raw.startswith("@"):
        path = Path(raw[1:])
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError("Cannot read --json file: " + str(exc))
    elif raw.strip() == "-":
        text = sys.stdin.read()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError("--json is not valid JSON: " + str(exc))
    if not isinstance(value, dict):
        raise ManifestError("--json must be a JSON object")
    return value


def new_error_signatures(before, after):
    """Return errors introduced by an edit, so partial writes cannot corrupt the manifest."""
    profile = active_profile(after, None)[0]
    before_errors = {
        (item["rule"], item["path"], item["message"])
        for item in validate_manifest(before, "plan", None, profile).errors
    }
    after_errors = [
        item for item in validate_manifest(after, "plan", None, profile).errors
    ]
    return [
        item
        for item in after_errors
        if (item["rule"], item["path"], item["message"]) not in before_errors
    ]


def parse_path_expression(expression):
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


def resolve_path_parent(manifest, segments):
    current = manifest
    for key, selector in segments[:-1]:
        if not isinstance(current, dict) or key not in current:
            raise ManifestError("--path does not resolve: missing '" + key + "'")
        current = current[key]
        if selector is None:
            continue
        if not isinstance(current, list):
            raise ManifestError("--path selector on '" + key + "' requires an array")
        found = None
        for item in current:
            if isinstance(item, dict) and text_of(item.get("id")) == selector:
                found = item
                break
        if found is None:
            raise ManifestError("--path selector '" + selector + "' matches no item in '" + key + "'")
        current = found
    key, selector = segments[-1]
    if selector is not None:
        raise ManifestError("--path must end at a field, not a selector")
    if not isinstance(current, dict):
        raise ManifestError("--path does not resolve to an object field")
    return current, key


def coerce_value(raw, current):
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered == "null":
        return None
    if isinstance(current, bool):
        raise ManifestError("field is boolean; --value must be true or false")
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(raw)
        except ValueError:
            raise ManifestError("field is numeric; --value must be an integer")
    if isinstance(current, list):
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return parsed
        return [part.strip() for part in raw.split(",") if part.strip()]
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
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"), "<secret>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<hash>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    # Hostnames including every leading label: db-prod-eu1.acme.internal must not
    # survive as db-prod-eu1.<host>, which still names the machine.
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:internal|local|corp|lan|test|intranet)\b", re.I), "<host>"),
    (re.compile(r"\b[\w-]+(?:\.[\w-]+)+\.(?:com|net|org|io|dev|ai|co|app|cloud|sh)\b", re.I), "<domain>"),
    (re.compile(r"\b[\w-]+\.(?:com|net|org|io|dev|ai|co|app|cloud|sh)\b", re.I), "<domain>"),
    # Windows and POSIX paths, and bare Windows drive roots.
    (re.compile(r"\b[A-Za-z]:[\\/][^\s,;]*"), "<path>"),
    (re.compile(r"(?<!\w)~?/(?:[\w.-]+/)+[\w.-]*"), "<path>"),
    (re.compile(r"\b(?:[\w.-]+\\)+[\w.-]+"), "<path>"),
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


def project_fingerprint(name):
    """Stable pseudonym for a project, so recurrence is countable without naming it."""
    return "p_" + hashlib.sha256(text_of(name).encode("utf-8")).hexdigest()[:12]


def global_store_dir(override=None):
    """Where observations from every project accumulate.

    Deliberately outside any project. The promotion bar asks whether a lesson was
    seen on two or more distinct projects, and that question is unanswerable while
    each project's copy of the skill only knows about itself.
    """
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("ADMINWRIGHT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".adminwright"


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


def merge_observation_files(*paths):
    """Union of observation records by id, order-stable.

    observations.jsonl is append-only, so a sync conflict is never a real
    disagreement -- it is two devices having appended different lines. Merging
    by id union means neither device loses work, which git's default
    line-oriented merge cannot guarantee.
    """
    merged = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = text_of(record.get("id")) or observation_fingerprint(record)
            merged.setdefault(key, record)
    return list(merged.values())


def observation_fingerprint(record):
    """Group observations that say the same thing in different words."""
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
    for line in path.read_text(encoding="utf-8").splitlines():
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
        with open(str(path), "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def next_lesson_number(lessons_dir):
    highest = 0
    if lessons_dir.exists():
        for path in lessons_dir.glob("*.md"):
            match = re.match(r"^(\d{4})-", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def yaml_list(values):
    if not values:
        return "[]"
    return "[" + ", ".join('"' + str(value).replace('"', "'") + '"' for value in values) + "]"


LESSON_INDEX_HEADER = (
    "# Lessons index\n"
    "\n"
    "| id | title | category | scope | status | confidence | date |\n"
    "|---|---|---|---|---|---|---|\n"
)


def read_lesson_frontmatter(path):
    fields = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return fields
    if not text.startswith("---"):
        return fields
    body = text.split("---", 2)
    if len(body) < 3:
        return fields
    for line in body[1].splitlines():
        if ":" not in line:
            continue
        key, _sep, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def resolve_manifest_path(value):
    path = Path(value).resolve()
    if not path.exists():
        raise ManifestError("Manifest not found: " + str(path))
    return path


def cmd_init(args):
    project_root = Path(args.project_root).resolve()
    target_dir = project_root / ".admin-console"
    target = target_dir / "manifest.json"
    if target.exists() and not args.force:
        stderr("Manifest already exists: " + str(target))
        return 2
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_json(TEMPLATE_PATH)
    manifest["profile"] = args.profile
    manifest["platform"]["name"] = args.name
    manifest["platform"]["archetypes"] = list(dict.fromkeys(args.archetype or []))
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
    write_json(target, manifest)
    schema_copy = target_dir / "admin-console.manifest.schema.json"
    shutil.copyfile(str(SCHEMA_PATH), str(schema_copy))
    print("Created " + str(target))
    print("Created " + str(schema_copy))
    print("Profile: " + args.profile)
    return 0


def cmd_migrate(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    original_version = manifest.get("manifestVersion")
    migrated, changes = migrate_manifest(manifest)
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
    with FileLock(manifest_path.parent / ".lock"):
        write_json(manifest_path, migrated)
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
        + " warning(s)"
    )


def cmd_validate(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    profile, source = active_profile(manifest, args.profile)
    project_root = (
        Path(args.project_root).resolve() if args.project_root else manifest_path.parent.parent
    )
    findings = validate_manifest(manifest, args.phase, project_root, profile)
    if args.json:
        print(
            json.dumps(
                {
                    "profile": profile,
                    "profileSource": source,
                    "phase": args.phase,
                    "manifest": str(manifest_path),
                    "projectRoot": str(project_root),
                    "errors": len(findings.errors),
                    "warnings": len(findings.warnings),
                    "findings": findings.sorted_items(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print_findings(findings, args.phase, source)
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
        "capabilityStatus": dict(Counter(cap.get("status", "unknown") for cap in capabilities)),
        "capabilityKind": dict(Counter(cap.get("kind", "unknown") for cap in capabilities)),
        "capabilityRisk": dict(Counter(cap.get("risk", "unknown") for cap in capabilities)),
        "reviewStatus": dict(Counter(cap.get("reviewStatus", "unreviewed") for cap in capabilities)),
        "qualityGates": dict(
            Counter(
                gate.get("status", "unknown")
                for _p, gate in iter_named(manifest, "qualityGates")
            )
        ),
        "gaps": dict(
            Counter(gap.get("status", "unknown") for _p, gap in iter_named(manifest, "gaps"))
        ),
        "decisions": dict(
            Counter(item.get("status", "unknown") for _p, item in iter_named(manifest, "decisions"))
        ),
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
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    profile = active_profile(manifest, None)[0]
    text = EMITTERS[args.format](manifest, profile)
    if args.out:
        out_path = Path(args.out).resolve()
        write_text_file(out_path, text)
        print("Wrote " + str(out_path))
    else:
        sys.stdout.write(text)
    return 0


def cmd_add(args):
    manifest_path = resolve_manifest_path(args.manifest)
    payload = parse_json_argument(args.json)
    kind = args.kind
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        entry = merge_defaults(kind, payload)
        entry_id = text_of(entry.get("id"))
        if not entry_id:
            raise ManifestError("the added " + kind + " needs an id")
        if not ID_PATTERN.match(entry_id):
            raise ManifestError("id '" + entry_id + "' must be lowercase alphanumeric with dots or hyphens")
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
            if any(text_of(cap.get("id")) == entry_id for cap in collection if isinstance(cap, dict)):
                raise ManifestError("capability '" + entry_id + "' already exists on entity '" + args.entity + "'")
        else:
            key = KIND_TARGETS[kind]
            collection = manifest.setdefault(key, [])
            if not isinstance(collection, list):
                raise ManifestError("manifest." + key + " is not an array")
            if any(text_of(item.get("id")) == entry_id for item in collection if isinstance(item, dict)):
                raise ManifestError(kind + " '" + entry_id + "' already exists")
        collection.append(entry)
        introduced = new_error_signatures(before, manifest)
        if introduced and not args.allow_invalid:
            stderr("Refusing to write: the new " + kind + " introduces validation errors.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            return 2
        write_json(manifest_path, manifest)
    print("Added " + kind + " '" + entry_id + "' to " + str(manifest_path))
    report_overridden(introduced, args.allow_invalid)
    return 0


def report_overridden(introduced, allow_invalid):
    """Never let --allow-invalid hide what it waved through.

    A silent override turns the escape hatch into a way to smuggle mock data past
    the scanner, which is the one thing this tool exists to prevent.
    """
    if not (introduced and allow_invalid):
        return
    stderr("WARNING: --allow-invalid wrote " + str(len(introduced)) + " unresolved error(s):")
    for item in introduced:
        stderr("  [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
    stderr("These still fail `validate --phase release`. Resolve them before any release claim.")


def cmd_set(args):
    manifest_path = resolve_manifest_path(args.manifest)
    segments = parse_path_expression(args.path)
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        before = json.loads(json.dumps(manifest))
        container, key = resolve_path_parent(manifest, segments)
        previous = container.get(key)
        value = coerce_value(args.value, previous)
        container[key] = value
        introduced = new_error_signatures(before, manifest)
        if introduced and not args.allow_invalid:
            stderr("Refusing to write: the change introduces validation errors.")
            for item in introduced:
                stderr("  ERROR: [" + item["rule"] + "] " + item["path"] + ": " + item["message"])
            return 2
        write_json(manifest_path, manifest)
    print(args.path + ": " + json.dumps(previous) + " -> " + json.dumps(value))
    report_overridden(introduced, args.allow_invalid)
    return 0


def lock_file_name(capability_id):
    return re.sub(r"[^A-Za-z0-9._-]", "_", capability_id) + ".lock"


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
        caps = capability_index(manifest)
        unknown = [cid for cid in args.capability if cid not in caps]
        if unknown:
            stderr("Unknown capability id(s): " + ", ".join(unknown))
            return 2
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
        record = None
        for item in agents:
            if isinstance(item, dict) and text_of(item.get("id")) == args.agent:
                record = item
                break
        if record is None:
            record = merge_defaults("agent", {"id": args.agent, "role": args.role})
            agents.append(record)
        record["role"] = args.role
        record["status"] = "active"
        owned = [value for value in as_list(record.get("ownsCapabilities")) if isinstance(value, str)]
        for cid in args.capability:
            if cid not in owned:
                owned.append(cid)
            caps[cid][2]["owner"] = args.agent
        record["ownsCapabilities"] = owned
        if stolen:
            note = text_of(record.get("notes"))
            steal_note = "; ".join(
                "stole " + cid + " from " + (owner or "unknown") + ": " + text_of(args.reason)
                for cid, owner in stolen
            )
            record["notes"] = (note + " | " if note else "") + steal_note
        write_json(manifest_path, manifest)
    for cid, owner in stolen:
        print("Stole claim on " + cid + " from " + (owner or "unknown"))
    print("Agent " + args.agent + " (" + args.role + ") claims: " + ", ".join(args.capability))
    return 0


def cmd_release_claim(args):
    manifest_path = resolve_manifest_path(args.manifest)
    lock_dir = manifest_path.parent / "locks"
    released = []
    skipped = []
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        caps = capability_index(manifest)
        agents = as_list(manifest.get("agents"))
        record = None
        for item in agents:
            if isinstance(item, dict) and text_of(item.get("id")) == args.agent:
                record = item
                break
        owned = as_list(record.get("ownsCapabilities")) if record else []
        targets = args.capability if args.capability else list(owned)
        for cid in targets:
            lock_path = lock_dir / lock_file_name(cid)
            owner = ""
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
            released.append(cid)
            entry = caps.get(cid)
            if entry and text_of(entry[2].get("owner")) == args.agent:
                entry[2]["owner"] = ""
        if record is not None:
            remaining = [cid for cid in owned if cid not in released]
            record["ownsCapabilities"] = remaining
            if not remaining:
                record["status"] = "done"
        write_json(manifest_path, manifest)
    for cid, owner in skipped:
        stderr("Skipped " + cid + ": lock is held by '" + owner + "'")
    print("Released: " + (", ".join(released) if released else "nothing"))
    return 3 if skipped else 0


def cmd_harvest(args):
    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_json(manifest_path)
    store = global_store_dir(args.store)
    platform = as_dict(manifest.get("platform"))
    project = text_of(args.project) or text_of(platform.get("name")) or manifest_path.parent.parent.name
    stack = as_dict(platform.get("stack"))
    stack_summary = ", ".join(
        text_of(stack.get(key)) for key in ("frontend", "backend", "database") if text_of(stack.get(key))
    )
    known = {record.get("id") for record in read_observations(store)}
    harvested = []
    with FileLock(manifest_path.parent / ".lock"):
        manifest = load_json(manifest_path)
        for _path, item in iter_named(manifest, "feedback"):
            if item.get("status") != "open":
                continue
            record = {
                "project": project,
                "archetypes": [text_of(a) for a in as_list(platform.get("archetypes"))],
                "stack": stack_summary,
                "category": text_of(item.get("category")),
                "observation": text_of(item.get("observation")),
                "proposedChange": text_of(item.get("proposedChange")),
                "scope": text_of(item.get("scope")) or "unassigned",
                "evidence": [text_of(e) for e in as_list(item.get("evidence"))],
                "date": text_of(args.date) or "unknown",
            }
            record["fingerprint"] = observation_fingerprint(record)
            record["id"] = project + ":" + text_of(item.get("id"))
            if record["id"] in known:
                continue
            harvested.append(record)
            item["status"] = "promoted"
        if harvested:
            write_json(manifest_path, manifest)
    if harvested:
        append_observations(store, harvested)
    print("Harvested " + str(len(harvested)) + " observation(s) from project '" + project + "'")
    print("Store: " + str(store))
    if not text_of(args.date):
        stderr("WARN: no --date supplied; observations recorded as 'unknown'")
    return 0


def cmd_store(args):
    store = global_store_dir(args.store)
    action = args.action

    if action == "status":
        records = read_observations(store)
        projects = {text_of(r.get("project")) for r in records if text_of(r.get("project"))}
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
        store.mkdir(parents=True, exist_ok=True)
        observations = store / "observations.jsonl"
        if not observations.exists():
            observations.write_text("", encoding="utf-8")
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
            gitignore.write_text(".lock\nlocks/\n", encoding="utf-8")
        readme = store / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Adminwright observation store\n\n"
                "Observations harvested from every project on this machine.\n"
                "Private by default. Nothing here leaves the machine unless you run\n"
                "`promote --export` and choose to share the result.\n",
                encoding="utf-8",
            )
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
        if not (store / ".git").exists():
            raise ManifestError("store is not a git repository; run `store init --remote <url>` first")
        if not run_git(store, "config", "user.email", check=False).stdout.strip():
            run_git(store, "config", "user.name", "adminwright", check=False)
            run_git(store, "config", "user.email", "adminwright@localhost", check=False)
        has_remote = run_git(store, "remote", "get-url", "origin", check=False).returncode == 0
        run_git(store, "add", "-A")
        status = run_git(store, "status", "--porcelain", check=False)
        if status.stdout.strip():
            message = "observations: " + (text_of(args.date) or "sync")
            run_git(store, "-c", "user.name=adminwright", "-c",
                    "user.email=adminwright@localhost", "commit", "-q", "-m", message)
            print("Committed local observations.")
        else:
            print("Nothing new to commit.")
        if not has_remote:
            print("No remote configured; nothing to push.")
            return 0
        fetched = run_git(store, "fetch", "-q", "origin", check=False)
        if fetched.returncode != 0:
            raise ManifestError("could not reach the remote:\n" + fetched.stderr.strip())
        remote_ref = "origin/" + args.branch
        exists = run_git(store, "rev-parse", "--verify", "-q", remote_ref, check=False)
        if exists.returncode == 0:
            local_path = store / "observations.jsonl"
            theirs = run_git(store, "show", remote_ref + ":observations.jsonl", check=False)
            if theirs.returncode == 0:
                incoming = store / ".incoming.jsonl"
                incoming.write_text(theirs.stdout, encoding="utf-8")
                merged = merge_observation_files(local_path, incoming)
                incoming.unlink()
                with open(str(local_path), "w", encoding="utf-8") as handle:
                    for record in merged:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                print("Merged " + str(len(merged)) + " observation(s) by id union.")
                # Commit the union BEFORE joining histories. git merge rewrites
                # the working tree from the merge result, so an uncommitted union
                # is silently discarded -- which loses exactly the remote lines
                # this step exists to keep.
                run_git(store, "add", "-A")
                if run_git(store, "status", "--porcelain", check=False).stdout.strip():
                    run_git(store, "-c", "user.name=adminwright", "-c",
                            "user.email=adminwright@localhost", "commit", "-q", "-m",
                            "merge remote observations")
            # Join histories with our committed union winning any conflict.
            # --allow-unrelated-histories is required, not optional: each device
            # runs `store init` independently, so two stores pointed at the same
            # remote genuinely have no common ancestor the first time they meet.
            joined = run_git(
                store, "merge", "-q", "--no-edit", "--allow-unrelated-histories",
                "-X", "ours", remote_ref, check=False,
            )
            if joined.returncode != 0:
                run_git(store, "merge", "--abort", check=False)
                raise ManifestError(
                    "could not join the remote history:\n"
                    + (joined.stderr or joined.stdout).strip()
                    + "\nThe local store is unchanged. Resolve by hand in " + str(store)
                )
            # git resolves the file line by line and happily keeps both sides of
            # a non-conflicting hunk, which duplicates records our id-union had
            # already collapsed. Normalise the file itself as the last word.
            local_path = store / "observations.jsonl"
            deduped = merge_observation_files(local_path)
            before = local_path.read_text(encoding="utf-8") if local_path.exists() else ""
            after = "".join(
                json.dumps(record, ensure_ascii=False) + "\n" for record in deduped
            )
            if after != before:
                local_path.write_text(after, encoding="utf-8")
                run_git(store, "add", "-A")
                if run_git(store, "status", "--porcelain", check=False).stdout.strip():
                    run_git(store, "commit", "-q", "-m", "normalise observations")
                print("Deduplicated to " + str(len(deduped)) + " observation(s).")
        pushed = run_git(store, "push", "-q", "origin", "HEAD:" + args.branch, check=False)
        if pushed.returncode != 0:
            raise ManifestError("push failed:\n" + (pushed.stderr or pushed.stdout).strip())
        print("Synced with " + args.branch + ".")
        return 0

    raise ManifestError("unknown store action: " + str(action))


def sanitise_candidate(candidate):
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
        "projects": [project_fingerprint(name) for name in candidate["projects"]],
        # Evidence paths are dropped entirely: they name files in someone's
        # private repository and prove nothing to a reader who cannot open them.
    }


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
    groups = {}
    for record in records:
        groups.setdefault(record.get("fingerprint"), []).append(record)

    candidates = []
    for fingerprint, items in groups.items():
        projects = sorted({text_of(item.get("project")) for item in items if text_of(item.get("project"))})
        archetypes = sorted({a for item in items for a in as_list(item.get("archetypes"))})
        stacks = sorted({text_of(item.get("stack")) for item in items if text_of(item.get("stack"))})
        category = text_of(items[0].get("category"))
        # A correction of wrong guidance promotes on first sighting; everything
        # else waits for corroboration from a second, distinct project.
        immediate = category == "incorrect-guidance"
        if len(projects) >= args.min_projects or immediate:
            candidates.append(
                {
                    "fingerprint": fingerprint,
                    "projects": projects,
                    "projectCount": len(projects),
                    "archetypes": archetypes,
                    "stacks": stacks,
                    "category": category,
                    "scope": text_of(items[0].get("scope")),
                    "proposedChange": text_of(items[0].get("proposedChange")),
                    "observation": text_of(items[0].get("observation")),
                    "evidence": sorted({e for item in items for e in as_list(item.get("evidence"))}),
                    "reason": "correction" if immediate else "seen on " + str(len(projects)) + " projects",
                    "communityEvidence": sum(1 for item in items if item.get("community")),
                }
            )
    candidates.sort(key=lambda c: (-c["projectCount"], c["scope"]))

    if getattr(args, "export", None):
        bundle = {
            "bundleVersion": "1",
            "generatedFor": "adminwright community observations",
            "note": "Sanitised. Project names are one-way fingerprints; paths, hosts, "
                    "emails, URLs and evidence references are removed.",
            "observations": [sanitise_candidate(c) for c in candidates],
        }
        out_path = Path(args.export).resolve()
        write_text_file(out_path, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")
        print("Wrote " + str(len(bundle["observations"])) + " sanitised candidate(s) to " + str(out_path))
        print()
        print("READ THE FILE BEFORE SHARING IT.")
        print("Patterns removed emails, URLs, paths, IP addresses, hostnames, domains,")
        print("tokens and hashes, replaced project names with one-way fingerprints, and")
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
        print()
    print("Record each accepted candidate with `lesson add`, then edit the reference it")
    print("names and open a pull request. Promotion is a judgement call: read")
    print("references/skill-evolution.md before adopting anything here.")
    return 0


def cmd_lesson_add(args):
    lessons_dir = Path(args.lessons_dir).resolve() if args.lessons_dir else SKILL_ROOT / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    number = next_lesson_number(lessons_dir)
    lesson_id = "%04d" % number
    slug = slugify(args.title)
    path = lessons_dir / (lesson_id + "-" + slug + ".md")
    date = text_of(args.date) or "unknown"
    if date == "unknown":
        stderr("WARN: no --date supplied; recorded as 'unknown'")
    front = [
        "---",
        "id: " + lesson_id,
        'title: "' + args.title.replace('"', "'") + '"',
        "date: " + date,
        "category: " + args.category,
        'scope: "' + args.scope.replace('"', "'") + '"',
        "status: " + args.status,
        "confidence: " + args.confidence,
        "platforms: " + yaml_list(args.platform or []),
        'trigger: "' + args.trigger.replace('"', "'") + '"',
        'rule: "' + args.rule.replace('"', "'") + '"',
        "evidence: " + yaml_list(args.evidence or []),
        "---",
        "",
        "# " + args.title,
        "",
        "## Trigger",
        "",
        args.trigger,
        "",
        "## Rule",
        "",
        args.rule,
        "",
        "## Evidence",
        "",
    ]
    if args.evidence:
        front.extend("- " + item for item in args.evidence)
    else:
        front.append("- none recorded")
    front.extend(
        [
            "",
            "## Promotion",
            "",
            "Status is `" + args.status + "`. Promote into a reference file only under the promotion bar,",
            "and state what the adoption replaces or trims.",
            "",
        ]
    )
    write_text_file(path, "\n".join(front))

    index_path = lessons_dir / "index.md"
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8").rstrip("\n") + "\n"
    else:
        existing = LESSON_INDEX_HEADER
    row = (
        "| "
        + " | ".join(
            [
                lesson_id,
                cell(args.title),
                cell(args.category),
                cell(args.scope),
                args.status,
                args.confidence,
                date,
            ]
        )
        + " |\n"
    )
    write_text_file(index_path, existing + row)
    print("Wrote " + str(path))
    print("Updated " + str(index_path))
    return 0


def cmd_lesson_list(args):
    lessons_dir = Path(args.lessons_dir).resolve() if args.lessons_dir else SKILL_ROOT / "lessons"
    if not lessons_dir.exists():
        stderr("No lessons directory at " + str(lessons_dir))
        return 2
    rows = []
    for path in sorted(lessons_dir.glob("*.md")):
        if not re.match(r"^\d{4}-", path.name):
            continue
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
    return cmd_lesson_list(args)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="Model, validate, and audit admin-console capability manifests"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create .admin-console/manifest.json")
    init_parser.add_argument("--project-root", default=".")
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
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(func=cmd_validate)

    report_parser = subparsers.add_parser("report", help="Print a coverage summary")
    report_parser.add_argument("--manifest", required=True)
    report_parser.add_argument("--json", action="store_true")
    report_parser.set_defaults(func=cmd_report)

    coverage_parser = subparsers.add_parser("coverage", help="Audit structural gaps only")
    coverage_parser.add_argument("--manifest", required=True)
    coverage_parser.add_argument("--project-root")
    coverage_parser.add_argument("--profile", choices=PROFILES)
    coverage_parser.set_defaults(func=cmd_coverage)

    emit_parser = subparsers.add_parser("emit", help="Generate a working document from the manifest")
    emit_parser.add_argument("--manifest", required=True)
    emit_parser.add_argument("--format", required=True, choices=sorted(EMITTERS))
    emit_parser.add_argument("--out")
    emit_parser.set_defaults(func=cmd_emit)

    add_parser = subparsers.add_parser("add", help="Append one object to a manifest collection")
    add_parser.add_argument("--manifest", required=True)
    add_parser.add_argument("--kind", required=True, choices=sorted(KIND_TARGETS))
    add_parser.add_argument("--json", required=True, help="JSON object, @file, or - for stdin")
    add_parser.add_argument("--entity", help="entity id, required when --kind capability")
    add_parser.add_argument("--allow-invalid", action="store_true")
    add_parser.set_defaults(func=cmd_add)

    set_parser = subparsers.add_parser("set", help="Set one field by path expression")
    set_parser.add_argument("--manifest", required=True)
    set_parser.add_argument("--path", required=True)
    set_parser.add_argument("--value", required=True)
    set_parser.add_argument("--allow-invalid", action="store_true")
    set_parser.set_defaults(func=cmd_set)

    claim_parser = subparsers.add_parser("claim", help="Take exclusive ownership of capabilities")
    claim_parser.add_argument("--manifest", required=True)
    claim_parser.add_argument("--agent", required=True)
    claim_parser.add_argument("--role", required=True, choices=AGENT_ROLES)
    claim_parser.add_argument("--capability", action="append", required=True)
    claim_parser.add_argument("--force-steal", action="store_true")
    claim_parser.add_argument("--reason")
    claim_parser.set_defaults(func=cmd_claim)

    release_parser = subparsers.add_parser("release-claim", help="Release capability ownership")
    release_parser.add_argument("--manifest", required=True)
    release_parser.add_argument("--agent", required=True)
    release_parser.add_argument("--capability", action="append", default=[])
    release_parser.set_defaults(func=cmd_release_claim)

    lesson_parser = subparsers.add_parser("lesson", help="Record or list skill lessons")
    lesson_sub = lesson_parser.add_subparsers(dest="lesson_command", required=True)
    lesson_add = lesson_sub.add_parser("add", help="Write lessons/NNNN-<slug>.md")
    lesson_add.add_argument("--title", required=True)
    lesson_add.add_argument("--category", required=True)
    lesson_add.add_argument("--scope", required=True)
    lesson_add.add_argument("--trigger", required=True)
    lesson_add.add_argument("--rule", required=True)
    lesson_add.add_argument("--evidence", action="append", default=[])
    lesson_add.add_argument("--platform", action="append", default=[])
    lesson_add.add_argument("--date", help="caller-supplied date; the script never reads the clock")
    lesson_add.add_argument("--status", choices=LESSON_STATUSES, default="proposed")
    lesson_add.add_argument("--confidence", choices=("low", "medium", "high"), default="low")
    lesson_add.add_argument("--lessons-dir", help="override the skill lessons directory")
    lesson_add.set_defaults(func=cmd_lesson)
    harvest = subparsers.add_parser(
        "harvest", help="Move a project's feedback[] into the cross-project store"
    )
    harvest.add_argument("--manifest", required=True)
    harvest.add_argument("--project", help="defaults to platform.name")
    harvest.add_argument("--store", help="defaults to $ADMINWRIGHT_HOME or ~/.adminwright")
    harvest.add_argument("--date", help="caller-supplied date; the script never reads the clock")
    harvest.set_defaults(func=cmd_harvest)

    promote = subparsers.add_parser(
        "promote", help="List observations that clear the promotion bar across projects"
    )
    promote.add_argument("--store", help="defaults to $ADMINWRIGHT_HOME or ~/.adminwright")
    promote.add_argument("--min-projects", type=int, default=2)
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

    lesson_list = lesson_sub.add_parser("list", help="Print the lessons index")
    lesson_list.add_argument("--status", choices=LESSON_STATUSES)
    lesson_list.add_argument("--lessons-dir")
    lesson_list.set_defaults(func=cmd_lesson)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ManifestError as exc:
        stderr("ERROR: " + str(exc))
        return 2
    except KeyboardInterrupt:
        stderr("Interrupted")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
