# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Install the adminwright agents into any harness.

The agent files in <skill-dir>/agents/ are written once, harness-neutral, with the
token ${CLAUDE_PLUGIN_ROOT} standing for the skill directory. Claude Code fills it
in for plugin installs; every other harness receives copies written in the shape
and at the location that harness discovers agents today. Standard library only.

Usage:

    python <skill-dir>/scripts/install_agents.py --harness <name> --project-root <dir>
        [--skill-dir <dir>] [--append-pointer]

Harnesses. Native agent locations and required fields were verified against each
harness's official documentation on 2026-09-24; the URLs sit beside the HARNESSES
entries. A harness with no documented agents concept gets only the harness-neutral
copy and the pointer block; no location is invented for it.

    claude-code   .claude/agents/<name>.md             verbatim source files
    opencode      .opencode/agents/<name>.md           description + mode: subagent
    codex         .codex/agents/<name>.toml            name, description, developer_instructions
                  + .adminwright/agents/ + pointer block for AGENTS.md
    antigravity   .agents/agents/<name>/agent.md       name, description
                  + .adminwright/agents/ + pointer block for AGENTS.md
    gemini        .gemini/agents/<name>.md             name, description, max_turns, timeout_mins
                  + .adminwright/agents/ + pointer block for GEMINI.md
    cursor        .cursor/agents/<name>.md             name, description
                  + .adminwright/agents/ + rule .cursor/rules/adminwright.mdc
    pi            .adminwright/agents/ + pointer block for AGENTS.md
                  (Pi has no built-in subagents)
    generic       .adminwright/agents/ + pointer block printed only

.adminwright/agents/ is the harness-neutral fallback: the source files, verbatim
apart from the skill path. Native files are rebuilt from each source's `name`,
`description` and body only, so Claude-specific frontmatter (`model`,
`disallowedTools`) never reaches a harness that would misread or reject it.

No file the installer writes for a project to commit carries a machine's absolute
path. A skill inside the project is named relative to the project root. A skill
outside it (a plugin cache, ~/.claude/skills, a clone elsewhere) is named by the
placeholder <skill>, and this machine's absolute path goes to
.adminwright/skill-dir, which the installer adds to the project's .gitignore.
Every agent's "Locate the skill" section reads that file before its search list.

The pointer block tells the main agent where the passes are and in what order;
where a harness cannot spawn subagents it runs the passes sequentially with
distinct agent ids, per the single-agent degradation rules in
references/multi-agent.md. Codex, Antigravity and Pi all read AGENTS.md, so the
block carries one marked section per harness installed into that file, and a
reinstall in any order keeps every section. `--append-pointer` writes the block
into the instruction file and replaces it in place on a reinstall; without the
flag the block is printed for manual paste. Cursor's rule file is a dedicated
document and is rewritten on every install.

Exit codes: 0 success, 2 usage or IO failure.
"""

import argparse
import json
import os
import re
import sys
from collections import namedtuple
from pathlib import Path

TOKEN = "${CLAUDE_PLUGIN_ROOT}"
# What installed copies say where the source says TOKEN, when the skill lives
# outside the project: the placeholder every agent's "Locate the skill" section
# defines and uses for the skill directory. It is deliberately not a ${...}
# variable: outside a Claude Code plugin no harness expands TOKEN, and Gemini CLI
# reads ${name} in a subagent body as a template input and refuses to start a
# subagent it has no input for.
PLACEHOLDER = "<skill>"
# This machine's absolute skill path, for agents whose copies name PLACEHOLDER.
# It differs per machine, so the installer git-ignores it.
MACHINE_PATH_FILE = ".adminwright/skill-dir"
GITIGNORE_ENTRY = "/" + MACHINE_PATH_FILE
GITIGNORE_COMMENT = "# adminwright: this machine's skill path, written by install_agents.py"

MARKER_START = "<!-- adminwright-agents:start -->"
MARKER_END = "<!-- adminwright-agents:end -->"
# Opens one harness's section inside the pointer block, so a later install into
# the same instruction file knows which harnesses already share it.
SECTION_MARK = "<!-- adminwright-harness: {} -->"

# Harness-neutral fallback copy, written for every harness that has no complete
# native story of its own.
NEUTRAL_DIR = ".adminwright/agents"

CHECKED = "2026-09-24"

# native_dir          project-relative directory the harness discovers agents in
#                     (None: the harness documents no agents concept)
# native_format       how the source files are rendered for it (see render_native)
# instruction_file    file that carries the pointer block (None: none written)
# neutral_copy        also write the verbatim copy to NEUTRAL_DIR
# docs / checked      the official pages verified against, and the date
Harness = namedtuple(
    "Harness", "native_dir native_format instruction_file neutral_copy docs checked")

HARNESSES = {
    # Subagents are Markdown + YAML frontmatter in .claude/agents/; `name` and
    # `description` are required, `model` and `disallowedTools` optional. The
    # source files already have exactly this shape, so they are copied verbatim.
    # Claude Code substitutes ${CLAUDE_PLUGIN_ROOT} in plugin agent content only,
    # not in .claude/agents/, so these copies name the skill like any other.
    "claude-code": Harness(
        ".claude/agents", "claude", None, False,
        ("https://code.claude.com/docs/en/sub-agents",
         "https://code.claude.com/docs/en/plugins-reference#environment-variables"), CHECKED),
    # opencode reads .opencode/agents (plural; its loader also accepts the
    # singular directory, which the docs do not mention). `description` is
    # required, `mode` defaults to `all` so it is set to `subagent`. `name` is the
    # file name and any unknown frontmatter key is forwarded to the model provider
    # as an option, so `name`, `tools` (deprecated there), `model` (needs
    # provider/model-id) and `disallowedTools` must not be carried over.
    "opencode": Harness(
        ".opencode/agents", "opencode", None, False,
        ("https://opencode.ai/docs/agents/",), CHECKED),
    # Custom agents are standalone TOML files in .codex/agents/ (project) or
    # ~/.codex/agents/; `name`, `description` and `developer_instructions` are
    # required. Current Codex releases spawn them when asked, including by an
    # AGENTS.md instruction, and AGENTS.md is Codex's project instruction file.
    # The docs moved to learn.chatgpt.com; the developers.openai.com pages still
    # serve the same text with these as their canonical links.
    "codex": Harness(
        ".codex/agents", "codex", "AGENTS.md", True,
        ("https://learn.chatgpt.com/docs/agent-configuration/subagents",
         "https://learn.chatgpt.com/docs/agent-configuration/agents-md"), CHECKED),
    # Custom subagents are `.agents/agents/<name>.md` or
    # `.agents/agents/<name>/agent.md` (workspace) with required `name` and
    # `description`; the folder form is the template the /agents panel prints.
    # `tools` is an allowlist, and an unknown name can hang the subagent (a
    # known issue the docs state), so only the required fields are written.
    # Antigravity reads AGENTS.md (and GEMINI.md) at the workspace root.
    "antigravity": Harness(
        ".agents/agents", "antigravity", "AGENTS.md", True,
        ("https://antigravity.google/docs/subagents",
         "https://antigravity.google/docs/cli/commands/agents",
         "https://antigravity.google/docs/rules"), CHECKED),
    # Custom subagents are Markdown in .gemini/agents/ (project) or
    # ~/.gemini/agents/; `name` (lowercase slug) and `description` are required,
    # an omitted `tools` inherits the parent's, and the frontmatter schema is
    # strict (an unknown key fails the load). `max_turns` and `timeout_mins`
    # default to 30 and 10 (see GEMINI_RUN_LIMITS). The body is the system prompt
    # and is templated (see GEMINI_TEMPLATE). GEMINI.md is the default context
    # file; AGENTS.md is read only if context.fileName lists it.
    "gemini": Harness(
        ".gemini/agents", "gemini", "GEMINI.md", True,
        ("https://geminicli.com/docs/core/subagents/",
         "https://geminicli.com/docs/cli/gemini-md/"), CHECKED),
    # Subagents are Markdown in .cursor/agents/ (Cursor also reads .claude/agents
    # and .codex/agents; .cursor/ wins on a name clash); `name` and `description`
    # are optional but written. Project rules are .mdc files in .cursor/rules/;
    # plain .md files there are ignored. The deprecated .cursorrules is not used.
    # The pointer is a dedicated always-applied rule, not a section of a shared file.
    "cursor": Harness(
        ".cursor/agents", "cursor", ".cursor/rules/adminwright.mdc", True,
        ("https://cursor.com/docs/context/subagents",
         "https://cursor.com/docs/context/rules"), CHECKED),
    # Pi has no built-in subagents ("build your own with extensions") and no
    # documented agents directory, so nothing native is written. It loads
    # AGENTS.md at startup, which is where the pointer goes.
    "pi": Harness(
        None, None, "AGENTS.md", True,
        ("https://pi.dev/",), CHECKED),
    # Any other harness: the neutral copy and a printed pointer block. Nothing
    # to verify, so no docs.
    "generic": Harness(None, None, None, True, (), CHECKED),
}

# How the pointer block names a harness that discovers the prompts natively.
LABELS = {
    "codex": "Codex",
    "antigravity": "Antigravity",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
}

# Instruction files that are a whole rule document rather than a shared file to
# merge a section into. These are written wholesale and rewritten on reinstall.
DEDICATED_RULE_FILES = {".cursor/rules/adminwright.mdc"}

# Frontmatter for harnesses whose rule files require it.
MDC_FRONTMATTER = (
    "---\n"
    "description: Adminwright admin-console delivery rules and role agents\n"
    "alwaysApply: true\n"
    "---\n\n"
)

# Dispatch order matters and the pointer block states it.
AGENT_ORDER = (
    "adminwright-architect",
    "adminwright-implementer",
    "adminwright-ux-reviewer",
    "adminwright-security",
    "adminwright-qa",
    "adminwright-harvester",
)

NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")

# Gemini CLI fills ${name} placeholders in a subagent's system prompt from its
# inputs and fails the dispatch on any it lacks; a Markdown subagent has only
# `query`.
GEMINI_TEMPLATE = re.compile(r"\$\{(\w+)\}")
GEMINI_INPUTS = frozenset(("query",))
# Gemini CLI stops a subagent after 30 turns or 10 minutes unless its
# frontmatter says otherwise, which cuts an architect's discovery or an
# implementer's slice short. Both keys take any positive integer; at the limit
# the subagent gets one grace turn to report. 200 turns of model and tool time
# (builds, tests) fit well inside 120 minutes, so the turn limit binds first.
GEMINI_RUN_LIMITS = (("max_turns", "200"), ("timeout_mins", "120"))


class InstallError(Exception):
    """A problem the user can act on; reported as ERROR: ..., exit 2."""


def fail(message):
    print("ERROR: " + message, file=sys.stderr)
    return 2


def configure_console():
    """Never let a print crash the install.

    Piped stdout on Windows is the ANSI code page (cp1252), which cannot encode
    most project names or paths. Force UTF-8 and replace what still cannot be
    written; a failed print must not follow a completed write.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def portable(path):
    """Absolute path with forward slashes, valid on every platform Python runs on."""
    return Path(path).resolve().as_posix()


def relative_inside(path, root):
    """`path` relative to `root` as a posix string ("." for the root itself), or
    None when it is not inside. Symlinks are tried both resolved and as written
    so a skill linked into the project counts as inside it."""
    pairs = (
        (Path(path).resolve(), Path(root).resolve()),
        (Path(os.path.abspath(str(path))), Path(os.path.abspath(str(root)))),
    )
    for candidate, base in pairs:
        try:
            return candidate.relative_to(base).as_posix()
        except ValueError:
            continue
    return None


def skill_reference(skill_dir, project_root):
    """How committed files name the skill: (text, project-relative path or None).

    Inside the project: the project-relative path, which is right on every
    machine. Outside it: PLACEHOLDER, never the absolute path; that goes to the
    git-ignored MACHINE_PATH_FILE instead.
    """
    rel = relative_inside(skill_dir, project_root)
    if rel is not None:
        return rel, rel
    return PLACEHOLDER, None


# --- source agent files ---------------------------------------------------------

FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
KEY_LINE = re.compile(r"([A-Za-z_][\w-]*):[ \t]*(.*)\Z")

Agent = namedtuple("Agent", "stem name description body text")


def decode_scalar(parts):
    """Decode the YAML scalar of one top-level frontmatter key.

    Handles plain, single- and double-quoted (including folded across lines) and
    block scalars; anything else (a list, a mapping) decodes to its raw text,
    which the installer never uses.
    """
    first = parts[0].strip()
    rest = [line.strip() for line in parts[1:]]
    while rest and not rest[-1]:
        rest.pop()
    if first[:1] in ("|", ">"):
        return ("\n" if first[0] == "|" else " ").join(line for line in rest if line or first[0] == "|")
    text = " ".join(piece for piece in [first] + rest if piece)
    if text[:1] == '"' and text[-1:] == '"' and len(text) >= 2:
        try:
            return json.loads(text, strict=False)
        except ValueError:
            return text[1:-1]
    if text[:1] == "'" and text[-1:] == "'" and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    return text


def frontmatter_fields(block):
    entries = []
    for line in block.split("\n"):
        match = KEY_LINE.match(line)
        if match:
            entries.append((match.group(1), [match.group(2)]))
        elif entries and not line.lstrip().startswith("#"):
            entries[-1][1].append(line)
    return {key: decode_scalar(parts) for key, parts in entries}


def load_agent(path, skill_text):
    """Read one source agent file, name the skill, split it into parts."""
    text = path.read_text(encoding="utf-8")  # universal newlines: always "\n" here
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace(TOKEN, skill_text)
    match = FRONTMATTER.match(text)
    if not match:
        raise InstallError(path.name + " has no YAML frontmatter")
    fields = frontmatter_fields(match.group(1))
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not NAME_PATTERN.match(name) or name != path.stem:
        raise InstallError(path.name + ": frontmatter `name` must equal the file name "
                           "and be a lowercase slug, got " + repr(name))
    if not description:
        raise InstallError(path.name + ": frontmatter `description` is required")
    body = text[match.end():].lstrip("\n").rstrip("\n") + "\n"
    return Agent(path.stem, name, description, body, text)


# --- native renderers -----------------------------------------------------------

def _escape_scalar(encoded):
    # JSON string syntax is a subset of both YAML double-quoted and TOML basic
    # strings. Escape the few characters JSON leaves raw that either format
    # treats as a line break or forbids.
    return (encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
            .replace("\x85", "\\u0085").replace("\x7f", "\\u007f"))


def quote(value):
    """A double-quoted scalar valid as YAML and as TOML."""
    return _escape_scalar(json.dumps(value, ensure_ascii=False))


def toml_multiline(text):
    """A TOML multi-line string holding `text` exactly."""
    body = text if text.endswith("\n") else text + "\n"
    controls = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
    if "'''" not in body and not controls.search(body):
        return "'''\n" + body + "'''"
    escaped = body.replace("\\", "\\\\").replace('"""', '""\\"')
    escaped = controls.sub(lambda m: "\\u%04x" % ord(m.group()), escaped)
    return '"""\n' + escaped + '"""'


def markdown_agent(fields, body):
    front = "".join(key + ": " + value + "\n" for key, value in fields)
    return "---\n" + front + "---\n\n" + body


def check_gemini_body(agent):
    """Refuse a body Gemini CLI would fail to template at dispatch time."""
    unknown = sorted(set(GEMINI_TEMPLATE.findall(agent.body)) - GEMINI_INPUTS)
    if unknown:
        raise InstallError(
            agent.stem + ".md: Gemini CLI reads ${" + unknown[0] + "} in a subagent body as a "
            "template input and refuses to start the subagent without it; write it another "
            "way (for example $" + unknown[0] + " or <" + unknown[0] + ">) and rerun")


def render_native(native_format, agent):
    """(path relative to the native directory, file text) for one agent."""
    if native_format == "claude":
        return agent.stem + ".md", agent.text
    if native_format == "opencode":
        return agent.name + ".md", markdown_agent(
            [("description", quote(agent.description)), ("mode", "subagent")], agent.body)
    if native_format == "gemini":
        check_gemini_body(agent)
        return agent.name + ".md", markdown_agent(
            [("name", agent.name), ("description", quote(agent.description))]
            + list(GEMINI_RUN_LIMITS), agent.body)
    if native_format == "cursor":
        return agent.name + ".md", markdown_agent(
            [("name", agent.name), ("description", quote(agent.description))], agent.body)
    if native_format == "antigravity":
        return agent.name + "/agent.md", markdown_agent(
            [("name", agent.name), ("description", quote(agent.description))], agent.body)
    if native_format == "codex":
        return agent.name + ".toml", (
            "name = " + quote(agent.name) + "\n"
            "description = " + quote(agent.description) + "\n"
            "developer_instructions = " + toml_multiline(agent.body) + "\n")
    raise InstallError("unknown native format: " + repr(native_format))


def write_text(path, text):
    """UTF-8 with LF line endings on every platform, never the OS default."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# --- this machine's skill path --------------------------------------------------

def is_skill_dir(path):
    """A directory an agent can run the skill from."""
    return (path / "SKILL.md").is_file() and (path / "scripts" / "admin_console_manifest.py").is_file()


def durable_skill_dir(skill_dir):
    """(directory to record for this machine, message or None).

    Claude Code copies a marketplace plugin to
    <plugins>/cache/<marketplace>/<plugin>/<version>/ and deletes that directory
    about 14 days after the next update, while the marketplace's clone at
    <plugins>/marketplaces/<marketplace>/ stays. A skill found in the cache is
    recorded as the clone when the clone holds it too; otherwise the message is
    a warning that the recorded path will go stale.
    """
    resolved = Path(skill_dir).resolve()
    for ancestor in resolved.parents:
        if ancestor.name.lower() != "cache" or ancestor.parent.name.lower() != "plugins":
            continue
        inside = resolved.relative_to(ancestor).parts  # marketplace, plugin, version, ...
        if len(inside) < 3:
            break
        clone = ancestor.parent / "marketplaces" / inside[0]
        for candidate in (clone.joinpath(*inside[3:]), clone):
            if is_skill_dir(candidate):
                return candidate, (
                    "The skill is in Claude Code's versioned plugin cache (" + portable(resolved)
                    + "), which is deleted about 14 days after the plugin updates; recording the "
                    "marketplace clone " + portable(candidate) + " for this machine instead")
        return resolved, (
            "WARNING: " + portable(resolved) + " is in Claude Code's versioned plugin cache, which "
            "is deleted about 14 days after the plugin updates, and no marketplace clone holds "
            "the skill; rerun this installer after each update, or pass --skill-dir with a "
            "directory that stays")
    return resolved, None


def record_machine_path(project_root, skill_dir):
    """Write this machine's absolute skill path to MACHINE_PATH_FILE.

    One line, forward slashes, so an agent (or a shell) can read it as is.
    Returns True when the file changed.
    """
    path = project_root / MACHINE_PATH_FILE
    text = portable(skill_dir) + "\n"
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except UnicodeDecodeError:
            pass
    write_text(path, text)
    return True


def ignore_machine_path(project_root):
    """Make the project's .gitignore ignore MACHINE_PATH_FILE.

    Returns "created", "added", "present", or "unreadable" for a .gitignore that
    is not UTF-8, which is left untouched for the caller to warn about. A file
    that is CRLF throughout stays CRLF; everything already in it is kept.
    """
    path = project_root / ".gitignore"
    if not path.exists():
        write_text(path, GITIGNORE_COMMENT + "\n" + GITIGNORE_ENTRY + "\n")
        return "created"
    try:
        existing, newline = read_instruction_file(path)
    except InstallError:
        return "unreadable"
    entries = {line.strip() for line in existing.split("\n")}
    if GITIGNORE_ENTRY in entries or MACHINE_PATH_FILE in entries:
        return "present"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing.strip():
        existing += "\n"
    merged = existing + GITIGNORE_COMMENT + "\n" + GITIGNORE_ENTRY + "\n"
    with open(str(path), "w", encoding="utf-8", newline="") as handle:
        handle.write(merged.replace("\n", newline) if newline != "\n" else merged)
    return "added"


def report_machine_path(project_root, skill_dir, needed):
    """Keep MACHINE_PATH_FILE current and git-ignored.

    Written whenever the committed files name PLACEHOLDER (`needed`); refreshed,
    never left stale, when an earlier install wrote it and the skill now lives
    inside the project.
    """
    path = project_root / MACHINE_PATH_FILE
    if not needed and not path.exists():
        return
    recorded, message = durable_skill_dir(skill_dir)
    if message:
        print(message, file=sys.stderr if message.startswith("WARNING") else sys.stdout)
    if record_machine_path(project_root, recorded):
        if needed:
            print("Wrote this machine's skill path to " + str(path) + "; the agent files name "
                  "the skill " + PLACEHOLDER + " and read the path from there")
        else:
            print("Refreshed this machine's skill path in " + str(path))
    status = ignore_machine_path(project_root)
    gitignore = project_root / ".gitignore"
    if status == "created":
        print("Created " + str(gitignore) + " ignoring " + GITIGNORE_ENTRY
              + " (a machine's path must not be committed)")
    elif status == "added":
        print("Added " + GITIGNORE_ENTRY + " to " + str(gitignore)
              + " (a machine's path must not be committed)")
    elif status == "unreadable":
        print("WARNING: " + str(gitignore) + " is not valid UTF-8, so it was not modified; add "
              + GITIGNORE_ENTRY + " to it by hand so this machine's path is never committed",
              file=sys.stderr)


# --- pointer block --------------------------------------------------------------

# Extra dispatch guidance per harness, inside that harness's section of the
# pointer block. Each entry exists because that harness's default behavior fought
# the skill in the field; keep entries short and behavioral. Every entry opens
# with "<Name> note", which is how blocks written before section marks existed
# are recognized.
HARNESS_NOTES = {
    "antigravity": [
        "Antigravity note — respect existing plans: this harness drafts its own",
        "implementation plan before acting. When a plan already exists — the user supplied",
        "one, or the manifest's decisions[] records the build order — that IS the plan.",
        "Do not author a competing plan. If the harness requires a plan artifact, mirror",
        "the existing plan into it verbatim, then start executing at the first unfinished",
        "step. Re-planning burns budget and drifts from the manifest, which is the plan of",
        "record.",
    ],
    "codex": [
        "Codex note: Codex spawns subagents when these instructions ask. Run each pass as the",
        "custom agent of the same name (for example `adminwright-architect`), and let a pass",
        "finish, with its exit condition checked, before starting one the order above puts",
        "after it. Subagents inherit this session's sandbox, so a read-only sandbox stops",
        "every pass (each one writes the manifest). Where subagents are off (`[agents]",
        "enabled = false`), run the passes sequentially in this session instead and announce",
        "each switch in one line (role, agent id, exit condition), so the transcript shows",
        "where one role's authority ended.",
    ],
    "gemini": [
        "Gemini note: dispatch each pass to the subagent of the same name; subagents cannot",
        "call each other, so this session orchestrates them. Whether a pass runs as a",
        "subagent or here, re-read the manifest from disk at each pass switch — do not carry",
        "a cached copy across passes.",
    ],
    "cursor": [
        "Cursor note: keep this file loaded as a rule. Dispatch each pass to the subagent of",
        "the same name; when you run a pass yourself instead, adopt one role file at a time",
        "and make the current role explicit in each response so review independence is",
        "visible.",
    ],
    "pi": [
        "Pi note: run the passes sequentially, one role file at a time, with a distinct",
        "agent id per pass; coordination state lives in the manifest, not the session.",
    ],
}


def harness_section(name):
    """One harness's lines inside the pointer block, opened by its section mark;
    empty for a harness with nothing to say."""
    lines = []
    native_dir = HARNESSES[name].native_dir
    if native_dir and name in LABELS:
        lines.append(LABELS[name] + " discovers these prompts natively in `" + native_dir + "/`.")
    lines += HARNESS_NOTES.get(name, [])
    return [SECTION_MARK.format(name)] + lines if lines else []


def pointer_block(agent_dir, skill_rel, harnesses=()):
    """The marker-delimited block that points a harness at the skill and agents.

    `skill_rel` is the skill's path relative to the project root, or None when
    the skill lives outside the project; the block then sends the reader to the
    git-ignored MACHINE_PATH_FILE, so it never holds a machine's path. Each of
    `harnesses` gets its own marked section, in HARNESSES order, whatever order
    they were installed in.
    """
    lines = [MARKER_START, "## Adminwright agent passes", ""]
    if skill_rel is not None:
        lines += [
            "This project builds or audits its admin console under the adminwright skill at",
            "`" + skill_rel + "` (start from its `SKILL.md`). Paths in this block are relative",
            "to the project root. The role prompts in",
        ]
    else:
        lines += [
            "This project builds or audits its admin console under the adminwright skill (start",
            "from its `SKILL.md`). Its directory differs per machine, so it is not written here:",
            "read it from the first line of `" + MACHINE_PATH_FILE + "`, which the installer",
            "writes and git-ignores, or resolve it with the \"Locate the skill\" section of any",
            "role file. Paths in this block are relative to the project root. The role prompts in",
        ]
    lines += [
        "`" + agent_dir + "/` define six passes. If this harness can spawn subagents, dispatch",
        "each file as one. If not, run them yourself sequentially, adopting one file at a",
        "time as your instructions, with a distinct agent id per pass — the single-agent",
        "degradation rules in the skill's `references/multi-agent.md` apply.",
        "",
        "Order: architect (always first; plan-validate must exit 0), implementer for the",
        "auth/authz/tenancy/audit spine (serialized), implementers per entity slice,",
        "ux-reviewer and security over each finished domain, qa for the adversarial pass",
        "and release gate, harvester last to bank what the run taught.",
        "",
        "If the user supplies a plan at any point, it is the plan of record: record it in",
        "the manifest's decisions[], build from it, and raise conflicts as gaps — never",
        "regenerate it as a fresh plan of your own.",
        "",
        "The implementer of a capability never reviews it. Coordination goes through",
        "`.admin-console/manifest.json` via the skill's script, never through chat memory.",
        "",
        "When the final pass completes, close with the skill's completion report: one",
        "plain-language paragraph on what operators can now do, then profile, domains and",
        "workflows controlled, roles, integrations, safeguards and audit coverage,",
        "verification performed, validate/coverage exit codes, lessons recorded, and what",
        "remains deferred or blocked with the reason. Never claim 'production-ready'",
        "without those exit codes at 0.",
    ]
    wanted = set(harnesses)
    for name in HARNESSES:
        if name in wanted:
            section = harness_section(name)
            if section:
                lines += [""] + section
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


BLOCK_RE = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END) + r"\n?", re.DOTALL)
# The heading the block has always opened with; an unmarked copy of it (a block
# pasted without its markers) is migrated rather than duplicated.
LEGACY_HEADING = re.compile(r"^## Adminwright agent passes[ \t]*$", re.MULTILINE)
NEXT_HEADING = re.compile(r"^#{1,2}[ \t]+\S", re.MULTILINE)
SECTION_RE = re.compile(r"<!-- adminwright-harness: ([a-z0-9_-]+) -->")
# The first words of every HARNESS_NOTES entry since the first release.
LEGACY_NOTE = re.compile(r"^(Antigravity|Codex|Gemini|Cursor|Pi) note\b", re.MULTILINE)


def adminwright_text(existing):
    """The adminwright part of an instruction file: every marked block, or else
    an unmarked section under the block's heading; "" when there is none."""
    blocks = [match.group(0) for match in BLOCK_RE.finditer(existing)]
    if blocks:
        return "".join(blocks)
    legacy = LEGACY_HEADING.search(existing)
    if legacy:
        following = NEXT_HEADING.search(existing, legacy.end())
        return existing[legacy.start():following.start() if following else len(existing)]
    return ""


def harnesses_in(block_text):
    """Harnesses with a section in an existing block: its section marks, plus the
    notes that identified each harness in blocks written before the marks."""
    names = set(SECTION_RE.findall(block_text))
    names.update(label.lower() for label in LEGACY_NOTE.findall(block_text))
    return names


def pointer_harnesses(harness, instruction_file, existing):
    """The harnesses the block in `instruction_file` should carry after this
    install: `harness` plus every harness already there that uses the same file."""
    kept = {name for name in harnesses_in(adminwright_text(existing))
            if name in HARNESSES and HARNESSES[name].instruction_file == instruction_file}
    kept.add(harness)
    return kept


def merge_pointer(existing, block):
    """Merge the pointer block into instruction-file text (LF newlines).

    Returns (new_text, status), status one of appended, updated, migrated,
    unchanged. A marked block is replaced in place, never skipped, so a reinstall
    after the skill moved rewrites the path. Unbalanced markers raise InstallError
    rather than guess which of the user's text to overwrite.
    """
    starts = existing.count(MARKER_START)
    ends = existing.count(MARKER_END)
    if starts or ends:
        matches = list(BLOCK_RE.finditer(existing))
        if starts != ends or len(matches) != starts:
            raise InstallError(
                "the adminwright pointer markers are unbalanced or out of order; fix or remove "
                + MARKER_START + " / " + MARKER_END + " by hand and rerun")
        pieces = []
        last = 0
        for index, match in enumerate(matches):
            pieces.append(existing[last:match.start()])
            if index == 0:
                pieces.append(block)
            last = match.end()
        pieces.append(existing[last:])
        merged = "".join(pieces)
        return merged, ("unchanged" if merged == existing else "updated")
    legacy = LEGACY_HEADING.search(existing)
    if legacy:
        following = NEXT_HEADING.search(existing, legacy.end())
        stop = following.start() if following else len(existing)
        tail = ("\n" if following else "") + existing[stop:]
        return existing[:legacy.start()] + block + tail, "migrated"
    if not existing:
        return block, "appended"
    if existing.endswith("\n\n"):
        joiner = ""
    else:
        joiner = "\n" if existing.endswith("\n") else "\n\n"
    return existing + joiner + block, "appended"


def read_instruction_file(path):
    """(text with LF newlines, newline style to write back) for an existing file.

    A file that is CRLF throughout stays CRLF so the block does not leave it
    mixed; every other file, including mixed ones, is left byte-for-byte alone
    outside the block.
    """
    try:
        with open(str(path), "r", encoding="utf-8", newline="") as handle:
            raw = handle.read()
    except UnicodeDecodeError:
        raise InstallError(str(path) + " is not valid UTF-8; it was not modified")
    if "\r\n" in raw and "\n" not in raw.replace("\r\n", ""):
        return raw.replace("\r\n", "\n"), "\r\n"
    return raw, "\n"


def plan_pointer(spec, harness, project_root, skill_rel):
    """Everything about the pointer decided before any file is written, so a
    refusal (unbalanced markers, an undecodable file) leaves the project as it
    was. None for a harness with no instruction file.

    A dedicated rule file (Cursor's .mdc) is the whole document, written
    wholesale on every install. A shared file (AGENTS.md, GEMINI.md) keeps
    everything outside the marked block untouched, and the block keeps the
    section of every other harness installed into the same file.
    """
    if not spec.instruction_file:
        return None
    path = project_root / spec.instruction_file
    dedicated = spec.instruction_file in DEDICATED_RULE_FILES
    existing, newline = ("", "\n")
    if not dedicated and path.exists():
        existing, newline = read_instruction_file(path)
    names = {harness} if dedicated else pointer_harnesses(
        harness, spec.instruction_file, existing)
    block = pointer_block(NEUTRAL_DIR, skill_rel, names)
    plan = {"path": path, "name": spec.instruction_file, "block": block,
            "dedicated": dedicated, "has_block": False}
    if not dedicated:
        merged, status = merge_pointer(existing, block)
        plan.update(merged=merged, status=status, newline=newline,
                    has_block=bool(adminwright_text(existing)))
    return plan


def write_pointer(plan):
    """Carry out a planned --append-pointer (or a dedicated rule); returns the status."""
    if plan["dedicated"]:
        write_text(plan["path"], MDC_FRONTMATTER + plan["block"])
        return "appended"
    if plan["status"] != "unchanged":
        merged, newline = plan["merged"], plan["newline"]
        plan["path"].parent.mkdir(parents=True, exist_ok=True)
        with open(str(plan["path"]), "w", encoding="utf-8", newline="") as handle:
            handle.write(merged.replace("\n", newline) if newline != "\n" else merged)
    return plan["status"]


# --- main -----------------------------------------------------------------------

def run_install(args):
    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    project_root = Path(args.project_root).resolve()
    source_dir = skill_dir / "agents"

    if not (skill_dir / "SKILL.md").exists():
        return fail("no SKILL.md in " + str(skill_dir) + "; pass --skill-dir explicitly")
    if not project_root.is_dir():
        return fail("project root does not exist: " + str(project_root))

    sources = [source_dir / (name + ".md") for name in AGENT_ORDER]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        return fail("agent files missing: " + ", ".join(missing))

    spec = HARNESSES[args.harness]
    skill_text, skill_rel = skill_reference(skill_dir, project_root)
    agents = [load_agent(source, skill_text) for source in sources]
    native = [render_native(spec.native_format, agent) for agent in agents] if spec.native_format else []
    pointer = plan_pointer(spec, args.harness, project_root, skill_rel)

    if spec.native_format:
        native_dir = project_root / spec.native_dir
        for relative, text in native:
            write_text(native_dir / relative, text)
        print("Installed " + str(len(native)) + " agents to " + str(native_dir))
        for relative, _text in native:
            print("  " + relative)

    neutral_dir = project_root / NEUTRAL_DIR
    if spec.neutral_copy:
        for agent in agents:
            write_text(neutral_dir / (agent.stem + ".md"), agent.text)
        label = "harness-neutral copies" if spec.native_format else "agents"
        print("Installed " + str(len(agents)) + " " + label + " to " + str(neutral_dir))
        if not spec.native_format:
            for agent in agents:
                print("  " + agent.stem + ".md")

    report_machine_path(project_root, skill_dir, skill_rel is None)

    if pointer is not None:
        where = str(pointer["path"])
        if args.append_pointer or pointer["dedicated"]:
            # A dedicated rule file is ours alone, so writing it is not the
            # intrusion that changing someone's AGENTS.md would be.
            status = write_pointer(pointer)
            if status == "unchanged":
                print("Pointer block already present in " + where + " and current; unchanged")
            elif status == "updated":
                print("Updated pointer block in " + where)
            elif status == "migrated":
                print("Replaced an older unmarked adminwright section in " + where
                      + " with the marked pointer block")
            else:
                print("Wrote pointer block to " + where)
        elif pointer["has_block"]:
            if pointer["status"] == "unchanged":
                print("Pointer block already present in " + where + " and current; unchanged")
            else:
                print(where + " has an out-of-date adminwright pointer block; "
                      "rerun with --append-pointer to replace it in place, or paste this one:\n")
                print(pointer["block"])
        else:
            print("\nAdd this to " + pointer["name"] + " (or rerun with --append-pointer):\n")
            print(pointer["block"])
    elif args.harness == "generic":
        print("\nPoint your harness at the installed files; dispatch order is in each name:")
        print(pointer_block(NEUTRAL_DIR, skill_rel))
    return 0


def main(argv=None):
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", required=True, choices=sorted(HARNESSES))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--skill-dir", default=None,
                        help="defaults to this script's parent skill directory")
    parser.add_argument("--append-pointer", action="store_true",
                        help="write the pointer block into the harness's instruction file "
                             "(replaces an existing block in place)")
    args = parser.parse_args(argv)
    try:
        return run_install(args)
    except InstallError as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail("could not read or write " + str(getattr(exc, "filename", None) or "a file")
                    + ": " + str(exc))


if __name__ == "__main__":
    sys.exit(main())
