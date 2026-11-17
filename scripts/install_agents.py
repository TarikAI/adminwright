# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Install the adminwright agents into any harness.

The agent files in <skill-dir>/agents/ are written once, harness-neutral, with the
token ${CLAUDE_PLUGIN_ROOT} standing for the skill directory. Claude Code fills it
in for plugin installs; every other harness receives copies written in the shape
and at the location that harness discovers agents today. Standard library only.

Usage:

    python <skill-dir>/scripts/install_agents.py --harness <name> --project-root <dir>
        [--skill-dir <dir>] [--append-pointer] [--dry-run] [--force]

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
flag the block is printed for manual paste, and an instruction file the
installer cannot merge (not UTF-8, damaged markers) only earns a warning.
Cursor's rule file is a dedicated document and is rewritten on every install.

Re-running is safe on a project someone edited. Every write is planned and
checked before the first one happens: a target reached through a symlink or
junction that leads out of the project root is refused, and so is a target
that cannot be written; either way nothing is written. The agent files and
Cursor's rule are recorded in .adminwright/installed.json (path -> sha256 of
the LF text written; commit it with them). A reinstall overwrites such a file
only while it still holds what the installer wrote, so a local edit (a Codex
`model`, a note in a prompt) is refused, naming the file, unless --force.
--dry-run prints every file a run would create, update, keep or refuse, and
writes nothing. The installer never deletes: a recorded file this version no
longer writes is named in a note. Codex loads .codex/agents/ only in a trusted
project, which the install output says.

Exit codes: 0 success, 2 usage or IO failure, or a refused write (nothing written;
--dry-run exits 2 when the real run would refuse).
"""

import argparse
import hashlib
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
    try:
        text = path.read_text(encoding="utf-8")  # universal newlines: always "\n" here
    except UnicodeDecodeError as exc:
        raise InstallError(str(path) + " is not valid UTF-8 (byte " + str(exc.start)
                           + "); re-save it as UTF-8 and rerun")
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


# --- planned writes -------------------------------------------------------------
#
# Every file the installer writes is planned first, then checked (no link out of
# the project, no file a user edited, every target writable), and only then
# written. A refusal therefore changes nothing, and --dry-run prints the plan the
# real run would carry out.

# One planned file: its project-relative path (forward slashes), absolute path,
# the exact bytes to write, whether the install record tracks it (the agent
# files and Cursor's rule, which the installer owns outright), what writing it
# means (create, update or unchanged), and the record hash of what is on disk.
Write = namedtuple("Write", "rel path data tracked action current")


def _real(path):
    return os.path.normcase(os.path.realpath(str(path)))


def _within(path, root):
    """Both from _real: True when path is root or lies below it."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # another drive
        return False


def safe_target(project_root, rel):
    """project_root/rel, or InstallError when writing there would leave the project.

    A checked-out repository can hold a symlink or a Windows junction where the
    installer writes (`.claude/agents`, `.gitignore`, `.adminwright/skill-dir`);
    writing through one would truncate or plant files anywhere the repository's
    author chose. Each existing component is compared with where it really
    leads; a link that stays inside the project is followed, one that leaves it
    is refused by name.
    """
    root = _real(project_root)
    current = Path(project_root)
    for part in rel.split("/"):
        parent = os.path.realpath(str(current))
        current = current / part
        if not os.path.lexists(str(current)):
            break
        leads_to = os.path.realpath(str(current))
        if os.path.normcase(leads_to) != os.path.normcase(os.path.join(parent, part)) \
                and not _within(os.path.normcase(leads_to), root):
            raise InstallError(
                "refusing to write " + rel + ": " + str(current) + " is a link (symlink or "
                "junction) to " + leads_to + ", outside the project root " + str(project_root)
                + "; nothing was written. Remove the link, or install into a project you trust")
    path = Path(project_root).joinpath(*rel.split("/"))
    if not _within(_real(path), root):
        raise InstallError("refusing to write " + rel + ": it resolves to " + os.path.realpath(str(path))
                           + ", outside the project root " + str(project_root) + "; nothing was written")
    return path


def record_hash(data):
    """What the install record keeps for a file: sha256 of its bytes with LF line
    endings, so a checkout that turned them into CRLF (core.autocrlf) still matches."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def plan_write(project_root, rel, text, tracked, newline="\n"):
    """A Write for `text` (LF newlines) at rel, written with `newline` line endings."""
    path = safe_target(project_root, rel)
    data = (text.replace("\n", newline) if newline != "\n" else text).encode("utf-8")
    if not os.path.lexists(str(path)):
        return Write(rel, path, data, tracked, "create", None)
    if path.is_dir():
        raise InstallError(str(path) + " is a directory, where the installer writes a file; "
                           "nothing was written")
    current = path.read_bytes()
    if current == data or (tracked and record_hash(current) == record_hash(data)):
        action = "unchanged"
    else:
        action = "update"
    return Write(rel, path, data, tracked, action, record_hash(current))


def check_writable(write):
    """InstallError now, before anything is written, if `write` would fail later."""
    path = write.path
    if path.exists():
        try:
            with open(str(path), "r+b"):
                pass
        except OSError as exc:
            raise InstallError("cannot write " + str(path) + " (" + exc.__class__.__name__ + ": "
                               + str(exc.strerror or exc) + "); nothing was written")
        return
    ancestor = path.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        raise InstallError("cannot write " + str(path) + ": " + str(ancestor)
                           + " is a file, not a directory; nothing was written")
    if not os.access(str(ancestor), os.W_OK):
        raise InstallError("cannot write " + str(path) + ": " + str(ancestor)
                           + " is not writable; nothing was written")


def execute(writes):
    """Write every planned file that changes. A failure part-way (a disk filling
    up after the checks passed) names what was already written."""
    done = []
    for write in writes:
        if write.action == "unchanged":
            continue
        try:
            write.path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(write.path), "wb") as handle:
                handle.write(write.data)
        except OSError as exc:
            listing = "".join("\n  " + str(path) for path in done) or " none"
            raise InstallError("could not write " + str(write.path) + ": " + str(exc)
                               + "\nAlready written before the failure:" + listing
                               + "\nRerun once the cause is fixed; files already written are "
                               "recognised as the installer's own.")
        done.append(write.path)
    return done


# --- the install record ---------------------------------------------------------
#
# .adminwright/installed.json keeps, for every agent file and rule the installer
# wrote, the hash of what it wrote. A reinstall overwrites a file only while it
# still holds exactly that; a file someone edited (a Codex `model`, a local note)
# is refused unless --force. The record is committed with the agent files, so a
# teammate's reinstall knows them too. It holds no machine path.

RECORD_FILE = ".adminwright/installed.json"
RECORD_NOTE = ("Written by adminwright's install_agents.py: the sha256 (of the LF text) of each "
               "file it last wrote. A reinstall leaves a file that no longer matches alone unless "
               "--force is given. Commit this file with the agent files.")


def read_record(project_root):
    """{relative path: hash} from the install record; {} when there is none."""
    path = project_root / RECORD_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = data.get("files") if isinstance(data, dict) else None
    except (OSError, ValueError, RecursionError):
        files = None
    if not isinstance(files, dict):
        print("WARNING: " + str(path) + " is not a readable install record; every installed "
              "file that differs from this version is treated as edited", file=sys.stderr)
        return {}
    return {str(key): str(value) for key, value in files.items()}


def record_text(files):
    return json.dumps({"note": RECORD_NOTE, "files": dict(sorted(files.items()))},
                      indent=2, ensure_ascii=False) + "\n"


def conflicts(writes, record):
    """Tracked files that would be overwritten although they are not what the
    installer last wrote: (write, why) pairs."""
    found = []
    for write in writes:
        if not write.tracked or write.action != "update":
            continue
        recorded = record.get(write.rel)
        if recorded == write.current:
            continue
        found.append((write, "edited since the installer wrote it" if recorded else
                      "not in the install record (edited, or written before " + RECORD_FILE
                      + " existed)"))
    return found


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


def plan_machine_path(project_root, skill_dir, needed):
    """(writes, messages) that keep MACHINE_PATH_FILE current and git-ignored.

    Written whenever the committed files name PLACEHOLDER (`needed`); refreshed,
    never left stale, when an earlier install wrote it and the skill now lives
    inside the project. One line, forward slashes, so an agent (or a shell) can
    read it as is. Each message is (stream, text, the Write it reports or None),
    printed only when that write happens. The .gitignore gains the entry once;
    one that is CRLF throughout stays CRLF, everything already in it is kept,
    and one that is not UTF-8 is left alone with a warning.
    """
    path = project_root / MACHINE_PATH_FILE
    if not needed and not os.path.lexists(str(path)):
        return [], []
    writes, messages = [], []
    recorded, message = durable_skill_dir(skill_dir)
    if message:
        messages.append((sys.stderr if message.startswith("WARNING") else sys.stdout, message, None))
    machine = plan_write(project_root, MACHINE_PATH_FILE, portable(recorded) + "\n", False)
    writes.append(machine)
    if needed:
        messages.append((sys.stdout, "Wrote this machine's skill path to " + str(path) + "; the "
                         "agent files name the skill " + PLACEHOLDER + " and read the path from "
                         "there", machine))
    else:
        messages.append((sys.stdout, "Refreshed this machine's skill path in " + str(path), machine))

    gitignore = project_root / ".gitignore"
    if not os.path.lexists(str(gitignore)):
        write = plan_write(project_root, ".gitignore", GITIGNORE_COMMENT + "\n" + GITIGNORE_ENTRY + "\n",
                           False)
        writes.append(write)
        messages.append((sys.stdout, "Created " + str(gitignore) + " ignoring " + GITIGNORE_ENTRY
                         + " (a machine's path must not be committed)", write))
        return writes, messages
    safe_target(project_root, ".gitignore")
    try:
        existing, newline = read_instruction_file(gitignore)
    except InstallError:
        messages.append((sys.stderr, "WARNING: " + str(gitignore) + " is not valid UTF-8, so it was "
                         "not modified; add " + GITIGNORE_ENTRY + " to it by hand so this "
                         "machine's path is never committed", None))
        return writes, messages
    entries = {line.strip() for line in existing.split("\n")}
    if GITIGNORE_ENTRY in entries or MACHINE_PATH_FILE in entries:
        return writes, messages
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing.strip():
        existing += "\n"
    write = plan_write(project_root, ".gitignore",
                       existing + GITIGNORE_COMMENT + "\n" + GITIGNORE_ENTRY + "\n", False, newline)
    writes.append(write)
    messages.append((sys.stdout, "Added " + GITIGNORE_ENTRY + " to " + str(gitignore)
                     + " (a machine's path must not be committed)", write))
    return writes, messages


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
        "enabled = false`), or the custom agents are missing because the project is not",
        "trusted (Codex loads project `.codex/` layers only for a trusted project), run the",
        "passes sequentially in this session instead and announce each switch in one line",
        "(role, agent id, exit condition), so the transcript shows where one role's authority",
        "ended.",
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


def plan_pointer(spec, harness, project_root, skill_rel, strict=True):
    """Everything about the pointer decided before any file is written, so a
    refusal (unbalanced markers, an undecodable file) leaves the project as it
    was. None for a harness with no instruction file.

    A dedicated rule file (Cursor's .mdc) is the whole document, written
    wholesale on every install. A shared file (AGENTS.md, GEMINI.md) keeps
    everything outside the marked block untouched, and the block keeps the
    section of every other harness installed into the same file.

    `strict` is --append-pointer: only then is a shared file written, so only
    then does a file the installer cannot merge (not UTF-8, damaged markers)
    refuse the install. Otherwise the plan carries `unmergeable`, the reason,
    and the block is printed for pasting by hand.
    """
    if not spec.instruction_file:
        return None
    path = project_root / spec.instruction_file
    dedicated = spec.instruction_file in DEDICATED_RULE_FILES
    existing, newline = ("", "\n")
    try:
        if not dedicated and path.exists():
            existing, newline = read_instruction_file(path)
        names = {harness} if dedicated else pointer_harnesses(
            harness, spec.instruction_file, existing)
        block = pointer_block(NEUTRAL_DIR, skill_rel, names)
        plan = {"path": path, "name": spec.instruction_file, "block": block,
                "dedicated": dedicated, "has_block": False, "unmergeable": None}
        if not dedicated:
            merged, status = merge_pointer(existing, block)
            plan.update(merged=merged, status=status, newline=newline,
                        has_block=bool(adminwright_text(existing)))
    except InstallError as exc:
        if strict:
            raise
        plan = {"path": path, "name": spec.instruction_file, "dedicated": False,
                "block": pointer_block(NEUTRAL_DIR, skill_rel, [harness]),
                "has_block": False, "unmergeable": str(exc)}
    return plan


def plan_pointer_write(plan, project_root, append_pointer):
    """(Write or None, status) for a planned pointer: the dedicated rule file
    always, a shared instruction file only with --append-pointer."""
    if plan is None or plan["unmergeable"]:
        return None, None
    if plan["dedicated"]:
        return plan_write(project_root, plan["name"], MDC_FRONTMATTER + plan["block"], True), "appended"
    if not append_pointer:
        return None, None
    if plan["status"] == "unchanged":
        return None, "unchanged"
    return plan_write(project_root, plan["name"], plan["merged"], False, plan["newline"]), plan["status"]


def report_pointer(plan, status, append_pointer):
    """What the install did with the pointer block, or what the user should do."""
    where = str(plan["path"])
    if plan["unmergeable"]:
        print("WARNING: " + plan["unmergeable"] + "; the agents were installed, but "
              + plan["name"] + " was not read for the pointer block. Paste this block into it "
              "by hand:\n", file=sys.stderr)
        print(plan["block"])
    elif append_pointer or plan["dedicated"]:
        # A dedicated rule file is ours alone, so writing it is not the
        # intrusion that changing someone's AGENTS.md would be.
        if status == "unchanged":
            print("Pointer block already present in " + where + " and current; unchanged")
        elif status == "updated":
            print("Updated pointer block in " + where)
        elif status == "migrated":
            print("Replaced an older unmarked adminwright section in " + where
                  + " with the marked pointer block")
        else:
            print("Wrote pointer block to " + where)
    elif plan["has_block"]:
        if plan["status"] == "unchanged":
            print("Pointer block already present in " + where + " and current; unchanged")
        else:
            print(where + " has an out-of-date adminwright pointer block; "
                  "rerun with --append-pointer to replace it in place, or paste this one:\n")
            print(plan["block"])
    else:
        print("\nAdd this to " + plan["name"] + " (or rerun with --append-pointer):\n")
        print(plan["block"])


# Printed after an install into these harnesses: what the files alone cannot
# make true. Each states a documented behavior of that harness.
AFTER_INSTALL = {
    # "For security, Codex loads project .codex/ layers only when you trust the
    # project" (https://learn.chatgpt.com/docs/config-file/config-basic).
    "codex": "Codex loads a project's .codex/ layers, these custom agents included, only when "
             "you trust the project; in an untrusted project the agents are silently missing, "
             "and the passes then run sequentially in the main session.",
    # Since 2026-06-18 Gemini CLI is replaced by Antigravity CLI for unpaid-tier
    # and Google One users (https://geminicli.com/docs/core/subagents/).
    "gemini": "Gemini CLI was replaced by Antigravity CLI for unpaid-tier and Google One "
              "users on 2026-06-18; if that is you, install with --harness antigravity instead.",
}


def stale_entries(record, writes, harness_dirs, project_root):
    """Recorded files in the directories this install writes to that this
    version no longer writes and that still exist (a renamed or removed role)."""
    planned = {write.rel for write in writes}
    return sorted(
        rel for rel in record
        if rel not in planned and any(rel.startswith(folder + "/") for folder in harness_dirs)
        and (project_root / rel).is_file()
    )


def print_dry_run(writes, refused, pointer, append_pointer):
    print("Dry run: nothing was written. A real run would:")
    refused_rels = {write.rel for write, _why in refused}
    for write in writes:
        if write.rel in refused_rels:
            verb = "refuse"
        else:
            verb = {"create": "create", "update": "update", "unchanged": "keep"}[write.action]
        print("  " + verb.ljust(7) + write.rel + ("" if write.action == "unchanged"
                                                  else " (" + str(len(write.data)) + " bytes)"))
    if pointer is not None and not pointer["dedicated"] and not append_pointer:
        print("  print  the pointer block for " + pointer["name"]
              + " (it is written only with --append-pointer)")


def run_install(args):
    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    project_root = Path(args.project_root).resolve()
    source_dir = skill_dir / "agents"

    if not (skill_dir / "SKILL.md").exists():
        return fail("no SKILL.md in " + str(skill_dir) + "; pass --skill-dir explicitly")
    if not project_root.exists():
        return fail("project root does not exist: " + str(project_root))
    if not project_root.is_dir():
        return fail("project root is not a directory: " + str(project_root))

    sources = [source_dir / (name + ".md") for name in AGENT_ORDER]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        return fail("agent files missing: " + ", ".join(missing))

    spec = HARNESSES[args.harness]
    skill_text, skill_rel = skill_reference(skill_dir, project_root)
    agents = [load_agent(source, skill_text) for source in sources]
    native = [render_native(spec.native_format, agent) for agent in agents] if spec.native_format else []
    pointer = plan_pointer(spec, args.harness, project_root, skill_rel, strict=args.append_pointer)

    # Plan every write.
    native_writes = [plan_write(project_root, spec.native_dir + "/" + relative, text, True)
                     for relative, text in native]
    neutral_writes = [plan_write(project_root, NEUTRAL_DIR + "/" + agent.stem + ".md", agent.text, True)
                      for agent in agents] if spec.neutral_copy else []
    machine_writes, machine_messages = plan_machine_path(project_root, skill_dir, skill_rel is None)
    pointer_write, pointer_status = plan_pointer_write(pointer, project_root, args.append_pointer)
    writes = native_writes + neutral_writes + machine_writes + ([pointer_write] if pointer_write else [])

    record = read_record(project_root)
    updated = dict(record)
    for write in writes:
        if write.tracked:
            updated[write.rel] = record_hash(write.data)
    if updated != record or not (project_root / RECORD_FILE).is_file():
        writes.append(plan_write(project_root, RECORD_FILE, record_text(updated), False))

    # Check before writing anything.
    refused = [] if args.force else conflicts(writes, record)
    if args.dry_run:
        print_dry_run(writes, refused, pointer, args.append_pointer)
        if refused:
            print("The real run would refuse: " + str(len(refused)) + " file(s) differ from what "
                  "the installer last wrote (rerun with --force to overwrite them).")
        return 2 if refused else 0
    if refused:
        lines = "".join("\n  " + str(write.path) + ": " + why for write, why in refused)
        return fail("these files differ from what the installer last wrote, so nothing was "
                    "written:" + lines + "\nKeep your changes by moving them elsewhere (for "
                    "example a project rule), or rerun with --force to overwrite them; "
                    "--dry-run shows every file a run would write.")
    for write in writes:
        if write.action != "unchanged":
            check_writable(write)

    execute(writes)

    if native_writes:
        native_dir = project_root / spec.native_dir
        print("Installed " + str(len(native)) + " agents to " + str(native_dir))
        for relative, _text in native:
            print("  " + relative)
    if neutral_writes:
        label = "harness-neutral copies" if spec.native_format else "agents"
        print("Installed " + str(len(agents)) + " " + label + " to " + str(project_root / NEUTRAL_DIR))
        if not spec.native_format:
            for agent in agents:
                print("  " + agent.stem + ".md")
    for stream, text, write in machine_messages:
        if write is None or write.action != "unchanged":
            print(text, file=stream)
    if pointer is not None:
        report_pointer(pointer, pointer_status, args.append_pointer)
    elif args.harness == "generic":
        print("\nPoint your harness at the installed files; dispatch order is in each name:")
        print(pointer_block(NEUTRAL_DIR, skill_rel))
    folders = [folder for folder in (spec.native_dir, NEUTRAL_DIR if spec.neutral_copy else None)
               if folder]
    for rel in stale_entries(record, writes, folders, project_root):
        print("note: " + rel + " was installed by an earlier version and is no longer written; "
              "delete it if that role is gone (the installer does not delete files)")
    if args.harness in AFTER_INSTALL:
        print("\nNote: " + AFTER_INSTALL[args.harness])
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
    parser.add_argument("--dry-run", action="store_true",
                        help="print every file the install would create, update or keep, and "
                             "any it would refuse; write nothing (exit 2 when it would refuse)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite agent files that differ from what the installer last "
                             "wrote (" + RECORD_FILE + "), discarding those edits")
    args = parser.parse_args(argv)
    try:
        return run_install(args)
    except InstallError as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail("could not read or write " + str(getattr(exc, "filename", None) or "a file")
                    + ": " + str(exc))
    except Exception as exc:  # noqa: BLE001 - exit codes are a contract: 0 or 2, never a traceback
        return fail("internal error: " + exc.__class__.__name__ + ": " + str(exc)[:500])


if __name__ == "__main__":
    sys.exit(main())
