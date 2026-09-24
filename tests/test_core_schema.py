# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Drift guard for the admin core schema pack (assets/admin-core-schema/).

postgres.sql is canonical; the Prisma, Drizzle, Django, Laravel and Rails files are
translations of it. Nothing here needs a database. Every file is parsed into the same
normalised model (SQL through a small expression parser, Django through the ast module, the
other DSLs through regular expressions) and compared with the canonical one, for the things a
translation gets wrong silently:

  * the tables and, per table, the columns and their type families (bigint, integer,
    smallint, text, boolean, timestamptz, jsonb, uuid, inet; varchar and citext count as
    text, a native enum as text, and every enum value must fit a varchar length);
  * CHECK constraints: the names, the value set of every enum-like column, and the
    expressions, compared by logical equivalence over their atomic comparisons (so
    `a <> b` equals Django's `~Q(a=F("b"))`, while `interval '8 hours'` differs from 800);
  * indexes and named unique constraints: the names, and the definitions (table, UNIQUE,
    the ordered key columns or expressions with their direction, the WHERE predicate);
  * foreign-key ON DELETE actions (blocking vs CASCADE vs SET NULL);
  * the text of every trigger and function that a variant carries inline or in a
    companion .sql file (schema qualifiers aside).

What it cannot see: what a tool adds or changes at migration time (the Django, Prisma and
Drizzle DB tests build those variants for real), and meaning beyond the parsed SQL subset
(a CHECK is compared as two-valued logic over its comparisons). The Django parse assumes
DEFAULT_AUTO_FIELD = BigAutoField, as the django_models.py header asks.

The CI step "Schema translations cover the same tables" runs exactly this file, so the CI
check and the local test cannot disagree. Standard library only (Python 3.9+).

Run:  python -m unittest discover -s tests -p "test_core_schema.py" -v
"""

import ast
import datetime
import itertools
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "assets" / "admin-core-schema"
MODEL_DOC = ROOT / "references" / "admin-data-model.md"

VARIANTS = ("prisma", "drizzle", "django", "laravel", "rails")

# Differences that are deliberate and documented in the variant's header.
KNOWN_EXTRA_COLUMNS = {
    "django": {("admin_role_permission", "id")},  # surrogate key; unique pair below
}
KNOWN_EXTRA_INDEXES = {
    "django": {"admin_role_permission_pk"},  # the unique pair standing in for the composite PK
}
# Words in the model doc that are not schema names (scope operators, JSON keys, and the
# one column the doc says is deliberately absent).
DOC_NON_SCHEMA_WORDS = {"gte", "neq", "not_in", "prefix", "limits", "heartbeat_at"}


def read(name):
    return (SCHEMA / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- helpers


def strip_sql_comments(text):
    return re.sub(r"--[^\n]*", "", text)


def balanced(text, open_index, opener="(", closer=")"):
    """Return (inner, end_index) for the group opening at text[open_index]; quote-aware."""
    depth = 0
    quote = None
    i = open_index
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and text[i + 1: i + 2] == "'":
                    i += 1  # doubled quote inside a SQL string
                else:
                    quote = None
            elif ch == "\\" and quote in ('"', "`"):
                i += 1
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[open_index + 1: i], i + 1
        i += 1
    raise ValueError("unbalanced group at %d: %r" % (open_index, text[open_index: open_index + 60]))


def split_top_level(body):
    """Split on commas that are not inside (), [], {} or quotes."""
    parts, cur, depth, quote = [], [], 0, None
    for i, ch in enumerate(body):
        if quote:
            cur.append(ch)
            if ch == quote and not (quote == "'" and body[i + 1: i + 2] == "'"):
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


ACTIONS = {
    "NO ACTION": "block",
    "RESTRICT": "block",
    "CASCADE": "cascade",
    "SET NULL": "setnull",
}


# ------------------------------------------------------------------ SQL expressions
#
# A recursive-descent parser for the SQL that appears in CHECK constraints, index WHERE
# clauses and index key expressions. Nodes are tuples:
#   ("or"|"and", (children...)) ("not", x) ("cmp", op, left, right) ("isnull", x)
#   ("in", x, frozenset(values)) ("col", name) ("str", s) ("num", n) ("bool", b) ("null",)
#   ("interval", seconds) ("func", name, (args...)) ("add"|"sub", left, right)

_TOKEN = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<str>'(?:[^']|'')*')"
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<op><=|>=|<>|!=|::|[=<>+\-*/(),])"
    r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
)

INTERVAL_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}


def tokenize(text):
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError("cannot tokenize SQL at %r" % text[pos: pos + 40])
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        value = m.group(m.lastgroup)
        if m.lastgroup == "str":
            value = value[1:-1].replace("''", "'")
        tokens.append((m.lastgroup, value))
    return tokens


def interval_seconds(text):
    found = re.findall(r"(\d+)\s*(second|minute|hour|day|week)s?\b", text.lower())
    if not found:
        raise ValueError("unsupported interval %r" % text)
    return sum(int(amount) * INTERVAL_UNITS[unit] for amount, unit in found)


def number(text):
    value = float(text)
    return int(value) if value.is_integer() else value


class SqlParser:
    COMPARISONS = ("=", "<>", "!=", "<", "<=", ">", ">=")

    def __init__(self, text):
        self.tokens = tokenize(text)
        self.pos = 0

    def peek(self, offset=0):
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else (None, None)

    def at_word(self, *words, offset=0):
        kind, value = self.peek(offset)
        return kind == "word" and value.upper() in words

    def at_op(self, *ops):
        kind, value = self.peek()
        return kind == "op" and value in ops

    def take(self):
        tok = self.peek()
        if tok[0] is None:
            raise ValueError("SQL expression ends early")
        self.pos += 1
        return tok

    def expect_op(self, op):
        kind, value = self.take()
        if kind != "op" or value != op:
            raise ValueError("expected %r, found %r" % (op, value))

    def done(self):
        return self.pos >= len(self.tokens)

    def expression(self):
        parts = [self.conjunction()]
        while self.at_word("OR"):
            self.take()
            parts.append(self.conjunction())
        return parts[0] if len(parts) == 1 else ("or", tuple(parts))

    def conjunction(self):
        parts = [self.negation()]
        while self.at_word("AND"):
            self.take()
            parts.append(self.negation())
        return parts[0] if len(parts) == 1 else ("and", tuple(parts))

    def negation(self):
        if self.at_word("NOT"):
            self.take()
            return ("not", self.negation())
        return self.comparison()

    def comparison(self):
        left = self.sum()
        if self.at_op(*self.COMPARISONS):
            op = self.take()[1]
            return ("cmp", "<>" if op == "!=" else op, left, self.sum())
        if self.at_word("IS"):
            self.take()
            negated = self.at_word("NOT")
            if negated:
                self.take()
            kind, value = self.take()
            if kind != "word" or value.upper() != "NULL":
                raise ValueError("only IS [NOT] NULL is supported, found IS %s" % value)
            return ("not", ("isnull", left)) if negated else ("isnull", left)
        negated = self.at_word("NOT") and self.at_word("IN", offset=1)
        if negated:
            self.take()
        if self.at_word("IN"):
            self.take()
            self.expect_op("(")
            values = [self.sum()]
            while self.at_op(","):
                self.take()
                values.append(self.sum())
            self.expect_op(")")
            node = ("in", left, frozenset(literal(v) for v in values))
            return ("not", node) if negated else node
        return left

    def sum(self):
        node = self.cast()
        while self.at_op("+", "-"):
            op = self.take()[1]
            node = ("add" if op == "+" else "sub", node, self.cast())
        return node

    def cast(self):
        node = self.primary()
        while self.at_op("::"):
            self.take()
            self.take()  # the type name: a cast does not change what is being compared here
        return node

    def primary(self):
        kind, value = self.take()
        if kind == "op" and value == "(":
            node = self.expression()
            self.expect_op(")")
            return node
        if kind == "op" and value == "-":
            inner = self.primary()
            return ("num", -inner[1]) if inner[0] == "num" else ("sub", ("num", 0), inner)
        if kind == "str":
            return ("str", value)
        if kind == "num":
            return ("num", number(value))
        if kind == "word":
            upper = value.upper()
            if upper == "INTERVAL":
                kind2, value2 = self.take()
                if kind2 != "str":
                    raise ValueError("INTERVAL needs a string literal")
                return ("interval", interval_seconds(value2))
            if upper in ("TRUE", "FALSE"):
                return ("bool", upper == "TRUE")
            if upper == "NULL":
                return ("null",)
            if self.at_op("("):
                self.take()
                args = []
                if not self.at_op(")"):
                    args.append(self.expression())
                    while self.at_op(","):
                        self.take()
                        args.append(self.expression())
                self.expect_op(")")
                return ("func", value.lower(), tuple(args))
            return ("col", value.lower())
        raise ValueError("unexpected %r in a SQL expression" % value)


def literal(node):
    if node[0] in ("str", "num", "bool"):
        return node[1]
    raise ValueError("an IN list needs literals, found %r" % (node,))


def parse_sql_expr(text):
    parser = SqlParser(text)
    node = parser.expression()
    if not parser.done():
        raise ValueError("unparsed SQL after the expression: %r" % (parser.tokens[parser.pos:],))
    return node


def parse_index_item(text):
    """`expr [ASC | DESC]` -> (key text, descending)."""
    parser = SqlParser(text)
    node = parser.sum()
    descending = False
    if parser.at_word("ASC", "DESC"):
        descending = parser.take()[1].upper() == "DESC"
    if not parser.done():
        raise ValueError("unsupported index key %r (NULLS FIRST/LAST, opclasses and COLLATE are not parsed)" % text)
    return vkey(node), descending


def vkey(node):
    """Canonical text of a value expression: an index key, or one side of a comparison."""
    kind = node[0]
    if kind == "col":
        return node[1]
    if kind == "str":
        return "'%s'" % node[1].replace("'", "''")
    if kind == "num":
        return repr(node[1])
    if kind == "bool":
        return "true" if node[1] else "false"
    if kind == "null":
        return "null"
    if kind == "interval":
        return "interval(%ss)" % node[1]
    if kind == "func":
        return "%s(%s)" % (node[1], ",".join(vkey(a) for a in node[2]))
    if kind in ("add", "sub"):
        return "(%s%s%s)" % (vkey(node[1]), "+" if kind == "add" else "-", vkey(node[2]))
    raise ValueError("not a value expression: %r" % (node,))


# ------------------------------------------------------- logical equivalence of predicates

PREDICATES = ("or", "and", "not", "cmp", "isnull", "in")


def linear(node):
    """(terms, constant) for sums and differences of columns, numbers and intervals, else None."""
    kind = node[0]
    if kind == "col":
        return {node[1]: 1}, 0
    if kind in ("num", "interval"):
        return {}, node[1]
    if kind in ("add", "sub"):
        a, b = linear(node[1]), linear(node[2])
        if a is None or b is None:
            return None
        sign = 1 if kind == "add" else -1
        terms = dict(a[0])
        for column, coef in b[0].items():
            terms[column] = terms.get(column, 0) + sign * coef
        return terms, a[1] + sign * b[1]
    return None


def form_key(terms, const, fix_sign):
    items = sorted((c, k) for c, k in terms.items() if k)
    if fix_sign and ((items and items[0][1] < 0) or (not items and const < 0)):
        items = [(c, -k) for c, k in items]
        const = -const
    return tuple(items), const


def comparison(op, left, right):
    """A comparison as a predicate over atoms. Order comparisons of linear forms become
    `form <= 0` atoms (so `a > b` and `NOT (a <= b)` agree), equality of truth values an iff."""
    if left[0] in PREDICATES or right[0] in PREDICATES or left[0] == "bool" or right[0] == "bool":
        if op not in ("=", "<>"):
            raise ValueError("ordering comparison between truth values")
        both = ("iff", predicate(left), predicate(right))
        return both if op == "=" else ("not", both)
    a, b = linear(left), linear(right)
    if a is not None and b is not None:
        terms = dict(a[0])
        for column, coef in b[0].items():
            terms[column] = terms.get(column, 0) - coef
        const = a[1] - b[1]
        neg = {c: -k for c, k in terms.items()}
        if op == "<=":
            return ("atom", ("le",) + form_key(terms, const, False))
        if op == ">=":
            return ("atom", ("le",) + form_key(neg, -const, False))
        if op == "<":
            return ("not", ("atom", ("le",) + form_key(neg, -const, False)))
        if op == ">":
            return ("not", ("atom", ("le",) + form_key(terms, const, False)))
        eq = ("atom", ("eq",) + form_key(terms, const, True))
        return eq if op == "=" else ("not", eq)
    sides = (vkey(left), vkey(right))
    if op in ("=", "<>"):
        eq = ("atom", ("eq", tuple(sorted(sides))))
        return eq if op == "=" else ("not", eq)
    return ("atom", ("cmp", op) + sides)


def predicate(node):
    kind = node[0]
    if kind in ("or", "and"):
        return (kind, tuple(predicate(c) for c in node[1]))
    if kind == "not":
        return ("not", predicate(node[1]))
    if kind == "bool":
        return ("const", node[1])
    if kind == "col":  # a boolean column used as a condition
        return ("atom", ("true", node[1]))
    if kind == "isnull":
        return ("atom", ("isnull", vkey(node[1])))
    if kind == "in":
        return ("atom", ("in", vkey(node[1]), node[2]))
    if kind == "cmp":
        return comparison(node[1], node[2], node[3])
    raise ValueError("not a condition: %r" % (node,))


def atoms_of(p, out):
    if p[0] == "atom":
        out.add(p[1])
    elif p[0] in ("or", "and"):
        for child in p[1]:
            atoms_of(child, out)
    elif p[0] == "not":
        atoms_of(p[1], out)
    elif p[0] == "iff":
        atoms_of(p[1], out)
        atoms_of(p[2], out)


def evaluate(p, env):
    kind = p[0]
    if kind == "atom":
        return env[p[1]]
    if kind == "const":
        return p[1]
    if kind == "not":
        return not evaluate(p[1], env)
    if kind == "and":
        return all(evaluate(c, env) for c in p[1])
    if kind == "or":
        return any(evaluate(c, env) for c in p[1])
    if kind == "iff":
        return evaluate(p[1], env) == evaluate(p[2], env)
    raise ValueError(kind)


def equivalent(a, b):
    """True when two conditions agree on every truth assignment of their atoms."""
    pa, pb = predicate(a), predicate(b)
    atoms = set()
    atoms_of(pa, atoms)
    atoms_of(pb, atoms)
    atoms = sorted(atoms, key=repr)
    if len(atoms) > 16:
        raise ValueError("too many atoms to compare (%d)" % len(atoms))
    for bits in itertools.product((False, True), repeat=len(atoms)):
        env = dict(zip(atoms, bits))
        if evaluate(pa, env) != evaluate(pb, env):
            return False
    return True


# -------------------------------------------------------------------------- the model

TYPE_FAMILIES = {
    "bigserial": "bigint", "bigint": "bigint",
    "integer": "integer", "smallint": "smallint",
    "text": "text", "citext": "text", "varchar": "text",
    "boolean": "boolean",
    "timestamptz": "timestamptz", "timestamp": "timestamp (no time zone)",
    "jsonb": "jsonb", "json": "json",
    "uuid": "uuid", "inet": "inet",
}


class IndexDef:
    def __init__(self, table, unique, items, where=None, source=""):
        self.table = table
        self.unique = bool(unique)
        self.items = tuple(items)  # ((key, descending), ...)
        self.where = where
        self.source = " ".join(source.split())

    def describe(self):
        keys = ", ".join(k + (" DESC" if d else "") for k, d in self.items)
        return "%s%s (%s)%s" % ("UNIQUE " if self.unique else "", self.table, keys, " WHERE ..." if self.where else "")


class Model:
    """What one variant declares, normalised so the six can be compared."""

    def __init__(self, name):
        self.name = name
        self.tables = set()
        self.columns = {}  # table -> set(column)
        self.types = {}  # (table, column) -> (family, varchar length or None)
        self.checks = {}  # constraint name -> table
        self.check_exprs = {}  # constraint name -> (expression node, source text)
        self.enums = {}  # (table, column) -> frozenset(values)
        self.enum_checks = set()  # names of CHECKs that are a bare `col IN (...)`
        self.indexes = {}  # name -> table (indexes and named unique constraints)
        self.index_defs = {}  # name -> IndexDef
        self.fks = {}  # (table, column) -> block | cascade | setnull
        self.unnamed_indexes = []  # diagnostics: indexes a variant left unnamed
        self.uniques = set()  # (table, column) declared UNIQUE on its own

    def add_column(self, table, column, sqltype=None, length=None):
        self.columns.setdefault(table, set()).add(column)
        if sqltype is not None:
            self.types[(table, column)] = (TYPE_FAMILIES[sqltype], length)

    def add_check(self, name, table, node, source=""):
        self.checks[name] = table
        self.check_exprs[name] = (node, " ".join(source.split()))
        if node[0] == "in" and node[1][0] == "col":
            self.enums[(table, node[1][1])] = frozenset(node[2])
            self.enum_checks.add(name)

    def add_index(self, name, table, unique, items, where=None, source=""):
        self.indexes[name] = table
        self.index_defs[name] = IndexDef(table, unique, items, where, source)


def implicit_unique(name, model):
    """PostgreSQL's <table>_<column>_key names, which no variant is expected to spell."""
    table = model.indexes.get(name)
    return bool(table) and any(name == "%s_%s_key" % (table, c) for c in model.columns.get(table, ()))


# ------------------------------------------------------------------ canonical (SQL)


def add_sql_statements(model, text):
    """CHECK / index / FK statements that live outside CREATE TABLE (ALTER, CREATE INDEX)."""
    for m in re.finditer(r"CREATE\s+(UNIQUE\s+)?INDEX\s+(\w+)\s+ON\s+(\w+)\s*\(", text):
        inner, end = balanced(text, m.end() - 1)
        items = [parse_index_item(part) for part in split_top_level(inner)]
        where, stop = None, end
        wm = re.match(r"\s*WHERE\b", text[end:])
        if wm:
            start = end + wm.end()
            stop = text.find(";", start)
            stop = len(text) if stop < 0 else stop
            where = parse_sql_expr(text[start:stop])
        model.add_index(m.group(2), m.group(3), bool(m.group(1)), items, where, text[m.start():stop])
    for m in re.finditer(r"ALTER TABLE\s+(\w+)\s+ADD CONSTRAINT\s+(\w+)\s+CHECK\s*\(", text):
        inner, _ = balanced(text, m.end() - 1)
        model.add_check(m.group(2), m.group(1), parse_sql_expr(inner), inner)
    for m in re.finditer(
        r"ALTER TABLE\s+(\w+)\s+ADD CONSTRAINT\s+\w+\s+FOREIGN KEY\s*\((\w+)\)\s+REFERENCES\s+\w+\s*\(\w+\)"
        r"(?:\s+ON DELETE\s+(CASCADE|RESTRICT|SET NULL|NO ACTION))?",
        text,
    ):
        model.fks[(m.group(1), m.group(2))] = ACTIONS[m.group(3) or "NO ACTION"]


def parse_canonical(text):
    text = strip_sql_comments(text)
    model = Model("canonical")
    unnamed_checks = 0
    for m in re.finditer(r"CREATE TABLE\s+(\w+)\s*\(", text):
        table = m.group(1)
        body, _ = balanced(text, m.end() - 1)
        model.tables.add(table)
        for item in split_top_level(body):
            if re.match(r"(CONSTRAINT\s+\w+\s+)?(PRIMARY KEY|UNIQUE|CHECK|FOREIGN KEY)\b", item):
                nm = re.match(r"CONSTRAINT\s+(\w+)\s+UNIQUE\s*\(", item)
                if nm:
                    inner, _ = balanced(item, nm.end() - 1)
                    items = [parse_index_item(part) for part in split_top_level(inner)]
                    model.add_index(nm.group(1), table, True, items, source=item)
            else:
                parts = item.split()
                column = parts[0]
                model.add_column(table, column, parts[1].lower())
                if re.search(r"\bUNIQUE\b", item):
                    model.uniques.add((table, column))
                fk = re.search(
                    r"REFERENCES\s+\w+\s*\(\w+\)(?:\s+ON DELETE\s+(CASCADE|RESTRICT|SET NULL|NO ACTION))?", item
                )
                if fk:
                    model.fks[(table, column)] = ACTIONS[fk.group(1) or "NO ACTION"]
            for cm in re.finditer(r"CHECK\s*\(", item):
                inner, _ = balanced(item, cm.end() - 1)
                named = re.search(r"CONSTRAINT\s+(\w+)\s+$", item[: cm.start()])
                if named:
                    model.add_check(named.group(1), table, parse_sql_expr(inner), inner)
                else:
                    unnamed_checks += 1
    add_sql_statements(model, text)
    model.unnamed_checks = unnamed_checks
    return model


# ---------------------------------------------------------------------- Prisma

PRISMA_TYPES = {"BigInt": "bigint", "String": "text", "Boolean": "boolean", "DateTime": "timestamp",
                "Json": "jsonb", "Int": "integer"}
PRISMA_DB_TYPES = {"Uuid": "uuid", "Inet": "inet", "Timestamptz": "timestamptz", "SmallInt": "smallint",
                   "Text": "text", "VarChar": "varchar", "Timestamp": "timestamp", "BigInt": "bigint",
                   "Integer": "integer"}


def parse_prisma(schema_text, raw_sql_text):
    text = re.sub(r"//[^\n]*", "", schema_text)
    model = Model("prisma")
    enums = {}
    for m in re.finditer(r"^enum\s+(\w+)\s*\{(.*?)^\}", text, re.M | re.S):
        values = []
        for line in m.group(2).strip().splitlines():
            vm = re.match(r"\s*(\w+)(?:\s+@map\(\"([^\"]+)\"\))?", line)
            if vm:
                values.append(vm.group(2) or vm.group(1))
        enums[m.group(1)] = frozenset(values)
    models = {}
    for m in re.finditer(r"^model\s+(\w+)\s*\{(.*?)^\}", text, re.M | re.S):
        models[m.group(1)] = m.group(2)
    scalar = set(PRISMA_TYPES)
    for mname, body in models.items():
        table_m = re.search(r'@@map\("(\w+)"\)', body)
        table = table_m.group(1)
        model.tables.add(table)
        fields = {}
        relations = []
        block_lines = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("@@"):
                if line.startswith(("@@index", "@@unique")):
                    block_lines.append(line)
                continue
            fm = re.match(r"(\w+)\s+(\w+)(\[\])?(\?)?\s*(.*)$", line)
            if not fm:
                continue
            fname, ftype, is_list, optional, rest = fm.groups()
            if ftype in scalar or ftype in enums:
                cm = re.search(r'@map\("(\w+)"\)', rest)
                column = cm.group(1) if cm else fname
                fields[fname] = (column, bool(optional))
                db = re.search(r"@db\.(\w+)(?:\((\d+)\))?", rest)
                if ftype in enums:
                    sqltype, length = "text", None  # a native enum holding the canonical strings
                elif db:
                    sqltype, length = PRISMA_DB_TYPES[db.group(1)], int(db.group(2)) if db.group(2) else None
                else:
                    sqltype, length = PRISMA_TYPES[ftype], None
                model.add_column(table, column, sqltype, length)
                if "@unique" in rest:
                    model.uniques.add((table, column))
                if ftype in enums:
                    model.enums[(table, column)] = enums[ftype]
            elif ftype in models and not is_list:
                relations.append(rest)
        for line in block_lines:
            mm = re.search(r'map:\s*"(\w+)"', line)
            if not mm:
                model.unnamed_indexes.append((table, line))
                continue
            listed = re.match(r"@@(index|unique)\(\[(.*?)\]", line)
            items = []
            for part in split_top_level(listed.group(2)):
                pm = re.match(r"(\w+)(?:\(sort:\s*(Asc|Desc)\))?$", part)
                items.append((fields[pm.group(1)][0], pm.group(2) == "Desc"))
            model.add_index(mm.group(1), table, listed.group(1) == "unique", items, source=line)
        for rest in relations:
            rm = re.search(r"@relation\(.*?fields:\s*\[(\w+)\]", rest)
            if not rm:
                continue
            column, optional = fields[rm.group(1)]
            dm = re.search(r"onDelete:\s*(\w+)", rest)
            action = dm.group(1) if dm else ("SetNull" if optional else "Restrict")
            model.fks[(table, column)] = {
                "Cascade": "cascade",
                "Restrict": "block",
                "NoAction": "block",
                "SetNull": "setnull",
            }[action]
    add_sql_statements(model, strip_sql_comments(raw_sql_text))
    return model


# --------------------------------------------------------------------- Drizzle

DRIZZLE_TYPES = {"bigserial": "bigserial", "bigint": "bigint", "text": "text", "varchar": "varchar",
                 "boolean": "boolean", "timestamp": "timestamp", "jsonb": "jsonb", "integer": "integer",
                 "smallint": "smallint", "uuid": "uuid", "inet": "inet"}


def strip_js_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//[^\n]*$|(?<=[\s,;)}\]])//[^\n]*$", "", text)


def drizzle_sql(template, field_to_col):
    """The SQL of a sql`...` template, with ${t.field} replaced by the column name."""
    m = re.match(r"\s*sql`(.*)`\s*$", template, re.S)
    if not m:
        raise ValueError("expected a sql`` template, found %r" % template)
    return re.sub(r"\$\{t\.(\w+)\}", lambda k: field_to_col[k.group(1)], m.group(1))


def parse_drizzle(text):
    text = strip_js_comments(text)
    model = Model("drizzle")
    for m in re.finditer(r'adminCore\.table\(\s*"(\w+)"\s*,\s*\{', text):
        table = m.group(1)
        model.tables.add(table)
        body, end = balanced(text, m.end() - 1, "{", "}")
        field_to_col = {}
        for entry in split_top_level(body):
            em = re.match(r"(\w+):\s*(.*)$", entry, re.S)
            if not em:
                continue
            cm = re.match(r'(\w+)\(\s*"(\w+)"\s*(?:,\s*\{([^}]*)\})?', em.group(2))
            if not cm:
                continue
            builder, column, options = cm.group(1), cm.group(2), cm.group(3) or ""
            sqltype = DRIZZLE_TYPES[builder]
            if builder == "timestamp" and re.search(r"withTimezone:\s*true", options):
                sqltype = "timestamptz"
            length = re.search(r"length:\s*(\d+)", options)
            field_to_col[em.group(1)] = column
            model.add_column(table, column, sqltype, int(length.group(1)) if length else None)
            if ".unique()" in em.group(2):
                model.uniques.add((table, column))
            fk = re.search(
                r'\.references\(\s*\(\)\s*=>\s*\w+\.\w+\s*(?:,\s*(?:\{\s*onDelete:\s*"([a-z ]+)"\s*\})?)?\s*\)',
                em.group(2),
            )
            if fk:
                model.fks[(table, column)] = ACTIONS[(fk.group(1) or "no action").upper()]
        rest = text[end:]
        nxt = rest.find("adminCore.table(")
        callback = rest if nxt < 0 else rest[:nxt]
        for cm in re.finditer(r'check\(\s*"(\w+)"\s*,\s*sql`', callback):
            tail = callback[cm.end():]
            source = drizzle_sql("sql`" + tail[: tail.index("`") + 1], field_to_col)
            model.add_check(cm.group(1), table, parse_sql_expr(source), source)
        for im in re.finditer(r'(uniqueIndex|index)\(\s*"(\w+)"\s*\)', callback):
            chain = callback[im.end():]
            om = re.match(r"\s*\.on\(", chain)
            args, end_on = balanced(chain, om.end() - 1)
            items = []
            for part in split_top_level(args):
                fm = re.match(r"t\.(\w+)$", part)
                items.append((field_to_col[fm.group(1)], False) if fm else parse_index_item(drizzle_sql(part, field_to_col)))
            where = None
            wm = re.match(r"\s*\.where\(", chain[end_on:])
            if wm:
                inner, _ = balanced(chain, end_on + wm.end() - 1)
                where = parse_sql_expr(drizzle_sql(inner, field_to_col))
            model.add_index(im.group(2), table, im.group(1) == "uniqueIndex", items, where, im.group(0) + chain[:end_on])
    return model


# ---------------------------------------------------------------------- Django
#
# Parsed with the ast module: fields, Meta.db_table, Meta.constraints and Meta.indexes, and
# every Q / F / Coalesce / timedelta expression translated into the SQL node shapes above.

DJANGO_TYPES = {
    "BigAutoField": "bigint", "AutoField": "integer", "BigIntegerField": "bigint", "IntegerField": "integer",
    "SmallIntegerField": "smallint", "TextField": "text", "CharField": "varchar", "EmailField": "varchar",
    "UUIDField": "uuid", "BooleanField": "boolean", "DateTimeField": "timestamptz", "JSONField": "jsonb",
    "GenericIPAddressField": "inet",
}
DJANGO_ACTIONS = {
    "CASCADE": "cascade", "DB_CASCADE": "cascade", "PROTECT": "block", "RESTRICT": "block",
    "DO_NOTHING": "block", "SET_NULL": "setnull", "DB_SET_NULL": "setnull",
}
DJANGO_LOOKUPS = {"exact": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return dotted(node.value) + "." + node.attr
    return ""


def keywords(call):
    return {k.arg: k.value for k in call.keywords}


def django_value(node, columns, field_strings=False):
    """A Django expression as a SQL value node. With field_strings, a bare string names a field
    (as in Coalesce("tenant_id", ...)); otherwise it is a literal."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return ("bool", node.value)
        if isinstance(node.value, (int, float)):
            return ("num", node.value)
        if isinstance(node.value, str):
            return ("col", columns[node.value]) if field_strings else ("str", node.value)
    if isinstance(node, ast.Call):
        name = dotted(node.func)
        if name == "F":
            return ("col", columns[node.args[0].value])
        if name == "Value":
            return django_value(node.args[0], columns)
        if name == "timedelta":
            delta = datetime.timedelta(**{k.arg: k.value.value for k in node.keywords})
            return ("interval", int(delta.total_seconds()))
        if name == "Coalesce":
            return ("func", "coalesce", tuple(django_value(a, columns, field_strings=True) for a in node.args))
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        kind = "add" if isinstance(node.op, ast.Add) else "sub"
        return (kind, django_value(node.left, columns), django_value(node.right, columns))
    raise ValueError("unsupported Django expression %s" % ast.dump(node))


def django_lookup(key, value, columns):
    parts = key.split("__")
    if len(parts) > 2 or (len(parts) == 2 and parts[1] not in DJANGO_LOOKUPS and parts[1] not in ("in", "isnull")):
        raise ValueError("unsupported Django lookup %r" % key)
    field, lookup = parts[0], parts[1] if len(parts) == 2 else "exact"
    column = ("col", columns[field])
    if lookup == "in":
        return ("in", column, frozenset(literal(django_value(e, columns)) for e in value.elts))
    if lookup == "isnull":
        return ("isnull", column) if value.value else ("not", ("isnull", column))
    return ("cmp", DJANGO_LOOKUPS[lookup], column, django_value(value, columns))


def django_condition(node, columns):
    """A Q expression (with ~, & and |) as a SQL condition node."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
        return ("not", django_condition(node.operand, columns))
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitOr, ast.BitAnd)):
        kind = "or" if isinstance(node.op, ast.BitOr) else "and"
        return (kind, (django_condition(node.left, columns), django_condition(node.right, columns)))
    if isinstance(node, ast.Call) and dotted(node.func) == "Q":
        parts = [django_condition(a, columns) for a in node.args]
        parts += [django_lookup(k.arg, k.value, columns) for k in node.keywords]
        return parts[0] if len(parts) == 1 else ("and", tuple(parts))
    raise ValueError("unsupported Django condition %s" % ast.dump(node))


def parse_django(text):
    model = Model("django")
    classes = [n for n in ast.parse(text).body
               if isinstance(n, ast.ClassDef) and any(dotted(b) == "models.Model" for b in n.bases)]
    info = {}
    for cls in classes:
        fields, meta = {}, {}
        for stmt in cls.body:
            if (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)
                    and dotted(stmt.value.func).startswith("models.")):
                fields[stmt.targets[0].id] = stmt.value
            elif isinstance(stmt, ast.ClassDef) and stmt.name == "Meta":
                meta = {s.targets[0].id: s.value for s in stmt.body if isinstance(s, ast.Assign)}
        columns = {"id": "id", "pk": "id"}
        for fname, call in fields.items():
            if dotted(call.func) == "models.ForeignKey":
                column = keywords(call).get("db_column")
                columns[fname] = column.value if column is not None else fname + "_id"
            elif dotted(call.func) != "models.ManyToManyField":
                columns[fname] = fname
        info[cls.name] = (fields, meta, columns)

    def field_type(cls_name, fname):
        call = info[cls_name][0][fname]
        kind = dotted(call.func)[len("models."):]
        if kind == "ForeignKey":
            target = call.args[0]
            target = target.id if isinstance(target, ast.Name) else target.value
            to_field = keywords(call).get("to_field")
            return field_type(target, to_field.value) if to_field is not None else ("bigint", None)
        length = keywords(call).get("max_length")
        if kind == "EmailField" and length is None:
            return DJANGO_TYPES[kind], 254
        return DJANGO_TYPES[kind], length.value if length is not None else None

    for cls in classes:
        fields, meta, columns = info[cls.name]
        table = meta["db_table"].value
        model.tables.add(table)
        model.add_column(table, "id", "bigint")  # DEFAULT_AUTO_FIELD = BigAutoField
        for fname, call in fields.items():
            kind = dotted(call.func)[len("models."):]
            if kind == "ManyToManyField":
                continue
            kw = keywords(call)
            model.add_column(table, columns[fname], *field_type(cls.name, fname))
            if kind == "ForeignKey":
                model.fks[(table, columns[fname])] = DJANGO_ACTIONS[dotted(kw["on_delete"]).split(".")[-1]]
            elif isinstance(kw.get("unique"), ast.Constant) and kw["unique"].value is True:
                model.uniques.add((table, fname))
        for call in list(getattr(meta.get("constraints"), "elts", [])) + list(getattr(meta.get("indexes"), "elts", [])):
            kind = dotted(call.func)
            kw = keywords(call)
            name = kw["name"].value
            if kind == "models.CheckConstraint":
                node = django_condition(kw["condition"], columns)
                model.add_check(name, table, node, ast.unparse(kw["condition"]))
                continue
            where = django_condition(kw["condition"], columns) if "condition" in kw else None
            if call.args:
                items = [(vkey(django_value(a, columns, field_strings=True)), False) for a in call.args]
            else:
                items = []
                for element in kw["fields"].elts:
                    fname = element.value
                    items.append((columns[fname.lstrip("-")], fname.startswith("-")))
            unique = kind == "models.UniqueConstraint"
            model.add_index(name, table, unique, items, where, ast.unparse(call))
            if unique and where is None and len(items) == 1 and name == "%s_%s_key" % (table, items[0][0]):
                model.uniques.add((table, items[0][0]))  # spelled as a constraint to skip Django's *_like index
    return model


# --------------------------------------------------------------------- Laravel

LARAVEL_TYPES = {"bigIncrements": "bigserial", "uuid": "uuid", "unsignedBigInteger": "bigint",
                 "bigInteger": "bigint", "text": "text", "string": "varchar", "boolean": "boolean",
                 "timestampTz": "timestamptz", "jsonb": "jsonb", "ipAddress": "inet",
                 "smallInteger": "smallint", "integer": "integer"}


def php_sql(text):
    """The SQL passed to DB::statement() / DB::unprepared(), one statement per string."""
    found = re.findall(
        r"DB::(?:statement|unprepared)\(\s*(?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'|<<<'SQL'\n(.*?)\n\s*SQL)\s*\)",
        text, re.S)
    return strip_sql_comments(";\n".join(next(g for g in groups if g) for groups in found))


def parse_laravel(text):
    model = Model("laravel")
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"Schema::create\('(\w+)'", text)]
    down = text.index("public function down")
    for n, (pos, table) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else down
        chunk = text[pos:end]
        model.tables.add(table)
        for cm in re.finditer(r"\$table->(%s)\('(\w+)'(?:,\s*(\d+))?\)([^;\n]*)" % "|".join(LARAVEL_TYPES), chunk):
            method, column, size, rest = cm.groups()
            length = int(size) if size and method == "string" else (255 if method == "string" else None)
            model.add_column(table, column, LARAVEL_TYPES[method], length)
            if "->unique()" in rest:
                model.uniques.add((table, column))
        for fk in re.finditer(
            r"\$table->foreign\('(\w+)'\)->references\('\w+'\)->on\('\w+'\)(?:->(cascadeOnDelete|restrictOnDelete|nullOnDelete)\(\))?",
            chunk,
        ):
            model.fks[(table, fk.group(1))] = {
                None: "block",
                "cascadeOnDelete": "cascade",
                "restrictOnDelete": "block",
                "nullOnDelete": "setnull",
            }[fk.group(2)]
        for im in re.finditer(r"\$table->(unique|index)\(\[([^\]]*)\],\s*'(\w+)'\)", chunk):
            items = [(c, False) for c in re.findall(r"'(\w+)'", im.group(2))]
            model.add_index(im.group(3), table, im.group(1) == "unique", items, source=im.group(0))
    add_sql_statements(model, php_sql(text[: text.index("public function down")]))
    return model


# ----------------------------------------------------------------------- Rails

RAILS_TYPES = {"text": "text", "string": "varchar", "bigint": "bigint", "uuid": "uuid", "jsonb": "jsonb",
               "boolean": "boolean", "integer": "integer", "timestamptz": "timestamptz", "inet": "inet"}
RAILS_INTEGER_LIMITS = {"2": "smallint", "4": "integer", "8": "bigint"}


def ruby_sql(text):
    """The SQL passed to execute: heredocs and single-line string literals."""
    found = re.findall(r"execute\s+(?:<<~'?SQL'?\n(.*?)\n\s*SQL\b|'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")",
                       text, re.S)
    return strip_sql_comments(";\n".join(next(g for g in groups if g) for groups in found))


def rails_keys(spec):
    spec = spec.strip()
    if spec.startswith(":"):
        return [(spec[1:], False)]
    listed = re.match(r"%i\[(.*?)\]$", spec)
    if not listed:
        raise ValueError("unsupported Rails column list %r" % spec)
    return [(c, False) for c in listed.group(1).split()]


def rails_where(options):
    wm = re.search(r"where:\s*(?:'([^']*)'|\"([^\"]*)\")", options)
    return parse_sql_expr(wm.group(1) or wm.group(2)) if wm else None


def parse_rails(text):
    model = Model("rails")
    starts = [(m.start(), m.group(1), m.group(0)) for m in re.finditer(r"create_table :(\w+)([^\n]*)", text)]
    down = text.index("  def down")
    for n, (pos, table, header) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else down
        chunk = text[pos:end]
        model.tables.add(table)
        if "primary_key:" not in header:
            model.add_column(table, "id", "bigserial")
        for cm in re.finditer(r"\bt\.(%s)\s+:(\w+)([^\n]*)" % "|".join(RAILS_TYPES), chunk):
            kind, column, rest = cm.groups()
            limit = re.search(r"limit:\s*(\d+)", rest)
            sqltype, length = RAILS_TYPES[kind], None
            if kind == "string" and limit:
                length = int(limit.group(1))
            if kind == "integer" and limit:
                sqltype = RAILS_INTEGER_LIMITS[limit.group(1)]
            model.add_column(table, column, sqltype, length)
        for rm in re.finditer(r"\bt\.references\s+:(\w+)([^\n]*(?:\n[ ]{10,}[^\n]*)*)", chunk):
            column = rm.group(1) + "_id"
            model.add_column(table, column, "bigint")
            od = re.search(r"on_delete:\s*:(\w+)", rm.group(2))
            model.fks[(table, column)] = {"cascade": "cascade", "restrict": "block", "nullify": "setnull"}.get(
                od.group(1) if od else "", "block"
            )
        for fk in re.finditer(r"\bt\.foreign_key\s+:\w+,\s*column:\s*:(\w+)([^\n]*)", chunk):
            od = re.search(r"on_delete:\s*:(\w+)", fk.group(2))
            model.fks[(table, fk.group(1))] = {"cascade": "cascade", "restrict": "block", "nullify": "setnull"}.get(
                od.group(1) if od else "", "block"
            )
        for cc in re.finditer(r"\bt\.check_constraint\s+", chunk):
            tail = chunk[cc.end():]
            q = tail[0]
            expr_end = tail.index(q, 1)
            expr = tail[1:expr_end]
            nm = re.search(r"name:\s*'(\w+)'", tail[expr_end:])
            model.add_check(nm.group(1), table, parse_sql_expr(expr), expr)
        for im in re.finditer(r"\bt\.index\s+(:\w+|%i\[[^\]]*\])([^\n]*(?:\n[ ]{10,}[^\n]*)*)", chunk):
            options = im.group(2)
            nm = re.search(r"name:\s*'(\w+)'", options)
            unique = bool(re.search(r"unique:\s*true", options))
            if nm:
                model.add_index(nm.group(1), table, unique, rails_keys(im.group(1)), rails_where(options), im.group(0))
            single = re.match(r"\bt\.index\s+:(\w+),\s*unique:\s*true", im.group(0))
            if single:
                model.uniques.add((table, single.group(1)))
    for am in re.finditer(r"add_index\s+:(\w+),\s*(:\w+|%i\[[^\]]*\])([^\n]*(?:\n[ ]{10,}[^\n]*)*)", text):
        table, spec, options = am.groups()
        nm = re.search(r"name:\s*'(\w+)'", options)
        if nm:
            unique = bool(re.search(r"unique:\s*true", options))
            model.add_index(nm.group(1), table, unique, rails_keys(spec), rails_where(options), am.group(0))
    add_sql_statements(model, ruby_sql(text[:down]))
    return model


# ---------------------------------------------------- inline function / trigger text


def normalise_sql(text):
    text = strip_sql_comments(text)
    text = re.sub(r"(?<!['\w])admin_core\.", "", text)
    text = re.sub(r"SET search_path = (?:admin_core|public), pg_temp", "SET search_path = SCHEMA, pg_temp", text)
    return re.sub(r"\s+", " ", text).strip()


def integrity_statements(text):
    text = strip_sql_comments(text)
    found = re.findall(
        r"CREATE OR REPLACE FUNCTION\b.*?\$\$.*?\$\$[^;]*;"
        r"|CREATE TRIGGER \w+\s+BEFORE .*?;"
        r"|REVOKE ALL ON FUNCTION .*?;",
        text,
        re.S,
    )
    return [normalise_sql(s) for s in found]


def load_models():
    return {
        "canonical": parse_canonical(read("postgres.sql")),
        "prisma": parse_prisma(read("prisma.schema"), read("prisma_raw.sql")),
        "drizzle": parse_drizzle(read("drizzle.ts")),
        "django": parse_django(read("django_models.py")),
        "laravel": parse_laravel(read("laravel_migration.php")),
        "rails": parse_rails(read("rails_migration.rb")),
    }


MODELS = load_models()
CANON = MODELS["canonical"]


def named_indexes(model):
    return {n for n in model.indexes if not implicit_unique(n, model)}


# ----------------------------------------------------- definition comparison (drift lists)


def type_drift(model, canon=None):
    canon = canon or CANON
    problems = []
    for key, (family, _) in sorted(canon.types.items()):
        got = model.types.get(key)
        if got is not None and got[0] != family:
            problems.append("%s.%s is %s, canonical %s" % (key[0], key[1], got[0], family))
    for key, values in sorted(canon.enums.items()):
        got = model.types.get(key)
        if got is not None and got[1] is not None and max(len(v) for v in values) > got[1]:
            problems.append("%s.%s is varchar(%d) but holds %r" % (key[0], key[1], got[1], max(values, key=len)))
    return problems


def index_drift(model, canon=None):
    canon = canon or CANON
    problems = []
    for name, want in sorted(canon.index_defs.items()):
        got = model.index_defs.get(name)
        if got is None:
            continue  # a missing name is reported by test_same_index_names
        diffs = []
        if got.table != want.table:
            diffs.append("table %s" % got.table)
        if got.unique != want.unique:
            diffs.append("UNIQUE" if got.unique else "not UNIQUE")
        if got.items != want.items:
            diffs.append("keys %s" % ", ".join(k + (" DESC" if d else "") for k, d in got.items))
        if (got.where is None) != (want.where is None):
            diffs.append("no WHERE" if got.where is None else "an extra WHERE")
        elif want.where is not None and not equivalent(want.where, got.where):
            diffs.append("a different WHERE")
        if diffs:
            problems.append("%s: %s (canonical %s)" % (name, "; ".join(diffs), want.describe()))
    return problems


def check_drift(model, canon=None):
    canon = canon or CANON
    problems = []
    for name, (want, want_source) in sorted(canon.check_exprs.items()):
        got = model.check_exprs.get(name)
        if got is not None and not equivalent(want, got[0]):
            problems.append("%s: %s  (canonical: %s)" % (name, got[1], want_source))
    return problems


# ----------------------------------------------------------------------- tests


class TestCanonicalIsSelfConsistent(unittest.TestCase):
    """Guards on the canonical file itself, so a parser gap cannot pass everything vacuously."""

    def test_parsed_the_whole_schema(self):
        self.assertGreaterEqual(len(CANON.tables), 18)
        self.assertGreater(sum(len(c) for c in CANON.columns.values()), 200)
        self.assertGreater(len(CANON.checks), 20)
        self.assertGreater(len(CANON.indexes), 20)
        self.assertGreater(len(CANON.fks), 20)
        self.assertEqual(len(CANON.types), sum(len(c) for c in CANON.columns.values()))
        self.assertEqual(set(CANON.check_exprs), set(CANON.checks))
        self.assertEqual(set(CANON.index_defs), set(CANON.indexes))

    def test_every_check_is_named(self):
        # An unnamed CHECK gets a PostgreSQL-generated name no variant can match.
        self.assertEqual(CANON.unnamed_checks, 0)

    def test_approval_decision_is_append_only_and_restricts_its_request(self):
        # audit finding 5.9: a decision could be deleted (and re-entered) by deleting its request.
        self.assertEqual(CANON.fks[("approval_decision", "request_id")], "block")
        sql = read("postgres.sql")
        self.assertRegex(sql, r"request_id\s+bigint\s+NOT NULL REFERENCES approval_request\(id\) ON DELETE RESTRICT")
        for op in ("update", "delete", "truncate"):
            for table in ("approval_decision", "audit_event"):
                self.assertRegex(
                    sql,
                    r"CREATE TRIGGER %s_no_%s\s+BEFORE %s ON admin_core\.%s" % (table, op, op.upper(), table),
                )

    def test_configuration_keys_are_unique_per_environment(self):
        # audit finding 5.10: UNIQUE (key) made "same key in production and staging" impossible.
        sql = read("postgres.sql")
        for table in ("config_setting", "feature_flag"):
            self.assertRegex(
                sql,
                r"CREATE UNIQUE INDEX %s_key_scope_unique\s+ON %s \(key, environment, COALESCE\(tenant_id, '\*'\)\)"
                % (table, table),
            )
            self.assertNotRegex(sql, r"CREATE TABLE %s \([^;]*key\s+text\s+NOT NULL UNIQUE" % table)
        self.assertIn("tenant_id", CANON.columns["feature_flag"])
        self.assertIn("tenant_id", CANON.columns["config_change"])

    def test_deleting_a_job_cannot_erase_its_failures(self):
        # round-2 finding: admin_job_failure is immutable history, yet ON DELETE CASCADE let a
        # job delete take its failure rows with it (test_same_foreign_key_delete_actions then
        # holds every variant to the same RESTRICT).
        self.assertEqual(CANON.fks[("admin_job_failure", "job_id")], "block")
        self.assertRegex(read("postgres.sql"), r"job_id\s+bigint\s+NOT NULL REFERENCES admin_job\(id\) ON DELETE RESTRICT")

    def test_live_grant_uniqueness_covers_global_grants(self):
        self.assertIn("COALESCE(scope_tenant_id, '*')", read("postgres.sql"))
        live = CANON.index_defs["admin_actor_role_live_unique"]
        self.assertTrue(live.unique)
        self.assertEqual(live.items[2], ("coalesce(scope_tenant_id,'*')", False))


class TestExpressionComparator(unittest.TestCase):
    """The comparison the definition tests rely on: equal meaning passes, a change does not."""

    def same(self, a, b):
        return equivalent(parse_sql_expr(a), parse_sql_expr(b))

    def test_spelling_differences_are_equivalent(self):
        self.assertTrue(self.same("granted_by <> actor_id", "NOT (granted_by = actor_id)"))
        self.assertTrue(self.same("expires_at > started_at", "NOT (expires_at <= started_at)"))
        self.assertTrue(self.same("expires_at - started_at <= interval '8 hours'",
                                  "expires_at <= started_at + interval '480 minutes'"))
        self.assertTrue(self.same("(revoked_at IS NULL) = (revoked_by IS NULL)",
                                  "(revoked_at IS NULL AND revoked_by IS NULL) "
                                  "OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)"))
        self.assertTrue(self.same("is_secret = (value_type = 'secret_ref')",
                                  "(is_secret = TRUE AND value_type = 'secret_ref') "
                                  "OR (is_secret = FALSE AND NOT value_type = 'secret_ref')"))
        self.assertTrue(self.same("kind IN ('a', 'b')", "kind IN ('b','a')"))

    def test_real_changes_are_not_equivalent(self):
        self.assertFalse(self.same("granted_by <> actor_id", "granted_by IS NOT NULL"))
        self.assertFalse(self.same("expires_at - started_at <= interval '8 hours'",
                                   "expires_at - started_at <= interval '800 hours'"))
        self.assertFalse(self.same("expires_at IS NULL OR expires_at > starts_at", "expires_at > starts_at"))
        self.assertFalse(self.same("kind IN ('a', 'b')", "kind IN ('a', 'b', 'c')"))
        self.assertFalse(self.same("processed_count >= 0", "processed_count > 0"))


class TestTranslationsMatchCanonical(unittest.TestCase):
    def check_set(self, label, variant, expected, actual):
        missing = sorted(map(str, expected - actual))
        extra = sorted(map(str, actual - expected))
        self.assertFalse(
            missing or extra,
            "%s drift in %s:\n  missing: %s\n  unexpected: %s" % (label, variant, missing, extra),
        )

    def test_same_tables(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                self.check_set("tables", v, CANON.tables, MODELS[v].tables)

    def test_same_columns_per_table(self):
        for v in VARIANTS:
            m = MODELS[v]
            extras = KNOWN_EXTRA_COLUMNS.get(v, set())
            with self.subTest(variant=v):
                for table in sorted(CANON.tables):
                    expected = CANON.columns[table]
                    actual = {c for c in m.columns.get(table, set()) if (table, c) not in extras}
                    self.check_set("columns of %s" % table, v, expected, actual)

    def test_same_check_constraint_names(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                expected = set(CANON.checks)
                if v == "prisma":
                    # enum-like columns are native enums in Prisma: nothing to name there
                    # (their value sets are compared in test_same_enum_value_sets)
                    expected -= CANON.enum_checks
                self.check_set("CHECK names", v, expected, set(MODELS[v].checks))

    def test_same_enum_value_sets(self):
        # a renamed or extra value ("phishing_resistant", "in_progress") is silent data drift
        for v in VARIANTS:
            with self.subTest(variant=v):
                m = MODELS[v]
                for key, values in CANON.enums.items():
                    self.assertIn(key, m.enums, "%s does not constrain %s.%s" % (v, key[0], key[1]))
                    self.assertEqual(values, m.enums[key], "%s: values of %s.%s differ" % (v, key[0], key[1]))

    def test_same_index_names(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                actual = named_indexes(MODELS[v]) - KNOWN_EXTRA_INDEXES.get(v, set())
                self.check_set("index names", v, named_indexes(CANON), actual)
                self.assertEqual(MODELS[v].unnamed_indexes, [], "index without an explicit name in %s" % v)

    def test_same_single_column_unique_constraints(self):
        # UNIQUE (key) on feature_flag is the audit-5.10 bug: an implicit, unnamed unique
        # constraint that the name comparison above cannot see.
        for v in VARIANTS:
            with self.subTest(variant=v):
                self.check_set("single-column UNIQUE", v, CANON.uniques, MODELS[v].uniques)

    def test_same_foreign_key_delete_actions(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                expected = set(CANON.fks.items())
                actual = set(MODELS[v].fks.items())
                self.check_set("FK on-delete actions", v, expected, actual)

    def test_no_foreign_key_erases_audit_history(self):
        for v in ["canonical"] + list(VARIANTS):
            self.assertNotIn("setnull", set(MODELS[v].fks.values()), "%s has an ON DELETE SET NULL FK" % v)


class TestDefinitionsMatchCanonical(unittest.TestCase):
    """Beyond names: what each index, CHECK and column actually is (audit finding 5.13)."""

    def test_every_column_has_a_parsed_type(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                m = MODELS[v]
                untyped = sorted("%s.%s" % (t, c) for t, cols in m.columns.items() for c in cols
                                 if (t, c) not in m.types)
                self.assertEqual(untyped, [], "columns whose type %s's parser did not read" % v)

    def test_same_column_types(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                self.assertEqual(type_drift(MODELS[v]), [], "column type drift in %s" % v)

    def test_every_canonical_index_has_a_parsed_definition(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                missing = sorted(n for n in named_indexes(CANON) if n not in MODELS[v].index_defs)
                self.assertEqual(missing, [], "indexes %s declares without a definition this test read" % v)

    def test_same_index_definitions(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                self.assertEqual(index_drift(MODELS[v]), [], "index definition drift in %s" % v)

    def test_same_check_expressions(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                self.assertEqual(check_drift(MODELS[v]), [], "CHECK expression drift in %s" % v)


class TestTheComparisonCatchesRealDrift(unittest.TestCase):
    """Each case puts back a bug the audit found (or a sibling of it) into one variant, in memory,
    and asserts the definition comparison reports it. If a parser stops reading something,
    these fail instead of the drift tests passing vacuously."""

    PARSERS = {
        "laravel_migration.php": parse_laravel,
        "rails_migration.rb": parse_rails,
        "django_models.py": parse_django,
        "drizzle.ts": parse_drizzle,
        "prisma_raw.sql": lambda raw: parse_prisma(read("prisma.schema"), raw),
        "prisma.schema": lambda schema: parse_prisma(schema, read("prisma_raw.sql")),
    }

    def mutated(self, filename, old, new):
        text = read(filename)
        self.assertIn(old, text, "the mutation no longer applies to %s; update this test" % filename)
        return self.PARSERS[filename](text.replace(old, new, 1))

    def assert_reported(self, problems, name):
        self.assertTrue(any(name in p for p in problems), "drift in %s went unreported: %s" % (name, problems))

    def test_live_grant_index_without_coalesce(self):
        # audit finding 5.2: NULL tenants are distinct, so global grants could be duplicated
        old = "ON admin_actor_role (actor_id, role_id, COALESCE(scope_tenant_id, '*'))"
        new = "ON admin_actor_role (actor_id, role_id, scope_tenant_id)"
        for filename in ("laravel_migration.php", "rails_migration.rb", "prisma_raw.sql"):
            with self.subTest(file=filename):
                self.assert_reported(index_drift(self.mutated(filename, old, new)), "admin_actor_role_live_unique")

    def test_feature_flag_unique_on_the_key_alone(self):
        # audit finding 5.10: one row per key, so production and staging could not differ
        old = "ON feature_flag (key, environment, COALESCE(tenant_id, '*'))"
        for filename in ("laravel_migration.php", "rails_migration.rb", "prisma_raw.sql"):
            with self.subTest(file=filename):
                self.assert_reported(index_drift(self.mutated(filename, old, "ON feature_flag (key)")),
                                     "feature_flag_key_scope_unique")

    def test_one_live_session_index_that_is_not_unique(self):
        drift = index_drift(self.mutated("rails_migration.rb",
                                         "add_index :impersonation_session, :real_actor_id, unique: true,",
                                         "add_index :impersonation_session, :real_actor_id,"))
        self.assert_reported(drift, "impersonation_session_one_live")

    def test_partial_index_that_lost_its_where(self):
        drift = index_drift(self.mutated("drizzle.ts",
                                         "    .where(sql`${t.revokedAt} IS NULL`),\n  index(\"admin_actor_role_live_lookup\")",
                                         ",\n  index(\"admin_actor_role_live_lookup\")"))
        self.assert_reported(drift, "admin_actor_role_live_unique")

    def test_weakened_self_grant_check(self):
        drift = check_drift(self.mutated("rails_migration.rb", "'granted_by <> actor_id'", "'granted_by IS NOT NULL'"))
        self.assert_reported(drift, "admin_actor_role_no_self_grant")

    def test_longer_impersonation_limit(self):
        for filename, old, new in (
            ("rails_migration.rb", "interval '8 hours'", "interval '800 hours'"),
            ("laravel_migration.php", "interval '8 hours'", "interval '80 hours'"),
            ("django_models.py", "timedelta(hours=8)", "timedelta(hours=80)"),
        ):
            with self.subTest(file=filename):
                self.assert_reported(check_drift(self.mutated(filename, old, new)), "impersonation_max_duration")

    def test_acted_as_subject_stored_as_a_number(self):
        drift = type_drift(self.mutated("laravel_migration.php", "$table->text('effective_actor_id')",
                                        "$table->unsignedBigInteger('effective_actor_id')"))
        self.assert_reported(drift, "audit_event.effective_actor_id")

    def test_timestamp_without_time_zone(self):
        drift = type_drift(self.mutated("prisma.schema", '@map("occurred_at") @db.Timestamptz', '@map("occurred_at")'))
        self.assert_reported(drift, "audit_event.occurred_at")

    def test_enum_value_longer_than_its_column(self):
        drift = type_drift(self.mutated("laravel_migration.php", "$table->string('state', 16)->default('pending')",
                                        "$table->string('state', 8)->default('pending')"))
        self.assert_reported(drift, "approval_request.state")


class TestInlineSqlMatchesCanonical(unittest.TestCase):
    """The triggers and functions are copied, not translated: they must be byte-for-byte the
    same statements (schema qualifier and search_path aside)."""

    CARRIERS = {
        "laravel_migration.php": "laravel",
        "rails_migration.rb": "rails",
        "django_triggers.sql": "django",
        "prisma_raw.sql": "prisma",
    }

    def test_every_canonical_function_and_trigger_is_carried(self):
        canonical = integrity_statements(read("postgres.sql"))
        self.assertGreaterEqual(len(canonical), 16)
        for filename, variant in self.CARRIERS.items():
            body = normalise_sql(read(filename))
            for stmt in canonical:
                with self.subTest(variant=variant, statement=stmt[:70]):
                    self.assertIn(stmt, body, "%s no longer carries this canonical statement" % filename)

    def test_variants_do_not_carry_stale_copies(self):
        canonical = set(integrity_statements(read("postgres.sql")))
        for filename in self.CARRIERS:
            for stmt in integrity_statements(read(filename)):
                with self.subTest(file=filename, statement=stmt[:70]):
                    self.assertIn(stmt, canonical, "%s has a function/trigger that is not in postgres.sql" % filename)

    def test_drizzle_tells_the_reader_to_append_the_integrity_section(self):
        self.assertIn("Integrity section of", read("drizzle.ts"))


class TestPackContents(unittest.TestCase):
    def test_every_variant_and_the_readme_point_at_grants_sql(self):
        for name in ("prisma.schema", "drizzle.ts", "django_models.py", "laravel_migration.php",
                     "rails_migration.rb", "README.md", "postgres.sql"):
            with self.subTest(file=name):
                self.assertIn("grants.sql", read(name))
        self.assertTrue((SCHEMA / "grants.sql").is_file())

    def test_grants_sql_revokes_mutation_on_the_append_only_tables(self):
        grants = read("grants.sql")
        for table in ("audit_event", "approval_decision", "config_change", "admin_job_failure"):
            self.assertIn(":\"schema\".%s" % table, grants)
        self.assertIn("REVOKE ALL ON", grants)
        self.assertRegex(grants, r"GRANT SELECT, INSERT ON")
        self.assertIn("scrub_audit_payload", grants)

    def test_django_uses_the_condition_keyword_on_every_check_constraint(self):
        # audit finding 5.1: CheckConstraint(check=...) is a TypeError on Django 6.
        src = read("django_models.py")
        self.assertNotRegex(src, r"CheckConstraint\(\s*check=")
        n_checks = len(re.findall(r"models\.CheckConstraint\(", src))
        self.assertEqual(n_checks, len(re.findall(r"models\.CheckConstraint\(\s*condition=", src)))
        self.assertGreaterEqual(n_checks, len(CANON.checks))

    def test_django_live_grant_index_is_on_the_coalesced_tenant(self):
        # audit finding 5.2: NULL tenants are distinct, so a plain field list accepts duplicates.
        src = read("django_models.py")
        block = src[src.index("admin_actor_role_live_unique") - 400: src.index("admin_actor_role_live_unique") + 60]
        self.assertIn('Coalesce("scope_tenant_id"', block)

    def test_django_fk_columns_keep_their_canonical_names(self):
        # audit finding 5.3: requested_by became requested_by_id and broke the SoD trigger.
        m = MODELS["django"]
        for table, column in (("approval_request", "requested_by"), ("admin_actor_role", "granted_by"),
                              ("admin_actor_role", "revoked_by"), ("admin_job", "initiated_by"),
                              ("config_change", "changed_by"), ("data_subject_request", "assigned_to")):
            self.assertIn(column, m.columns[table])
            self.assertNotIn(column + "_id", m.columns[table])

    def test_prisma_states_every_relation_action(self):
        # audit finding 5.6: an optional relation defaults to SetNull.
        src = re.sub(r"//[^\n]*", "", read("prisma.schema"))
        for m in re.finditer(r"@relation\([^)]*fields:[^)]*\)", src):
            self.assertIn("onDelete:", m.group(0), "relation without an explicit onDelete: " + m.group(0))

    def test_prisma_schema_has_no_datasource_url(self):
        # audit finding 5.7: Prisma 7 rejects `url` in the schema (it lives in prisma.config.ts).
        src = re.sub(r"//[^\n]*", "", read("prisma.schema"))
        datasource = re.search(r"datasource\s+\w+\s*\{(.*?)\}", src, re.S).group(1)
        self.assertNotIn("url", datasource)

    def test_readme_carries_a_runnable_verification_block(self):
        readme = read("README.md")
        section = readme[readme.index("## Verifying the install"):]
        block = re.search(r"```sql\n(.*?)\n```", section, re.S).group(1)
        for needle in ("VERIFY FAILED", "DELETE audit_event", "DELETE approval_decision",
                       "TRUNCATE audit_event", "verify_audit_chain", "scrub_audit_payload",
                       "feature_flag", "count(*)"):
            self.assertIn(needle, block)
        self.assertTrue(block.lstrip().startswith("BEGIN;") and block.rstrip().endswith("ROLLBACK;"))


class TestModelDocMatchesSchema(unittest.TestCase):
    def test_doc_stays_within_the_reference_line_budget(self):
        self.assertLessEqual(len(MODEL_DOC.read_text(encoding="utf-8").splitlines()), 450)

    def test_doc_only_names_columns_and_objects_the_schema_has(self):
        # audit finding 5.11: the doc named read_only, execution_failed, constraint_expr,
        # admin_actor.status ... that existed in no variant.
        doc = MODEL_DOC.read_text(encoding="utf-8")
        sql = read("postgres.sql") + read("grants.sql")
        names = set(re.findall(r"`([a-z][a-z0-9_]*)`", doc))
        missing = sorted(n for n in names if n not in sql and n not in DOC_NON_SCHEMA_WORDS)
        self.assertEqual(missing, [], "the model doc names things postgres.sql does not define")
        for gone in ("constraint_expr", "end_reason"):
            self.assertNotIn(gone, doc)
        self.assertNotIn("admin_actor.status", doc)


if __name__ == "__main__":
    unittest.main()
