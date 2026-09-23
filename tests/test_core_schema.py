# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Drift guard for the admin core schema pack (assets/admin-core-schema/).

postgres.sql is canonical; the Prisma, Drizzle, Django, Laravel and Rails files are
translations of it. Nothing here needs a database: the six files are compared at the
regex level for the things a translation gets wrong silently:

  * the tables and, per table, the columns;
  * CHECK constraint names, and the value set of every enum-like column;
  * index names (partial and expression indexes included);
  * foreign-key ON DELETE actions (blocking vs CASCADE vs SET NULL);
  * the text of every trigger and function that a variant carries inline or in a
    companion .sql file (schema qualifiers aside).

The CI job "Schema translations cover the same tables" runs exactly this file, so the
CI check and the local test cannot disagree. Standard library only (Python 3.9+).

Run:  python -m unittest discover -s tests -p "test_core_schema.py" -v
"""

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


def in_values(expr):
    """For a bare `col IN ('a','b')` expression return (col, frozenset) else None."""
    m = re.match(r"^\s*\(?\s*(\w+)\s+IN\s*\((.*)\)\s*\)?\s*$", expr, re.S)
    if not m:
        return None
    return m.group(1), frozenset(re.findall(r"""['"]([^'"]*)['"]""", m.group(2)))


class Model:
    """What one variant declares, normalised so the six can be compared."""

    def __init__(self, name):
        self.name = name
        self.tables = set()
        self.columns = {}  # table -> set(column)
        self.checks = {}  # constraint name -> table
        self.enums = {}  # (table, column) -> frozenset(values)
        self.enum_checks = set()  # names of CHECKs that are a bare `col IN (...)`
        self.indexes = {}  # name -> table (indexes and named unique constraints)
        self.fks = {}  # (table, column) -> block | cascade | setnull
        self.unnamed_indexes = []  # diagnostics: indexes a variant left unnamed
        self.uniques = set()  # (table, column) declared UNIQUE on its own

    def add_column(self, table, column):
        self.columns.setdefault(table, set()).add(column)

    def add_check(self, name, table, expr):
        self.checks[name] = table
        found = in_values(expr) if expr else None
        if found:
            self.enums[(table, found[0])] = found[1]
            self.enum_checks.add(name)


def implicit_unique(name, model):
    """PostgreSQL's <table>_<column>_key names, which no variant is expected to spell."""
    table = model.indexes.get(name)
    return bool(table) and any(name == "%s_%s_key" % (table, c) for c in model.columns.get(table, ()))


# ------------------------------------------------------------------ canonical (SQL)


def add_sql_statements(model, text):
    """CHECK / index / FK statements that live outside CREATE TABLE (ALTER, CREATE INDEX)."""
    for m in re.finditer(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(\w+)\s+ON\s+(\w+)", text):
        model.indexes[m.group(1)] = m.group(2)
    for m in re.finditer(r"ALTER TABLE\s+(\w+)\s+ADD CONSTRAINT\s+(\w+)\s+CHECK\s*\(", text):
        inner, _ = balanced(text, m.end() - 1)
        model.add_check(m.group(2), m.group(1), inner)
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
                nm = re.match(r"CONSTRAINT\s+(\w+)\s+UNIQUE", item)
                if nm:
                    model.indexes[nm.group(1)] = table
            else:
                column = item.split()[0]
                model.add_column(table, column)
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
                    model.add_check(named.group(1), table, inner)
                else:
                    unnamed_checks += 1
    add_sql_statements(model, text)
    model.unnamed_checks = unnamed_checks
    return model


# ---------------------------------------------------------------------- Prisma


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
    scalar = {"BigInt", "String", "Boolean", "DateTime", "Json", "Int"}
    for mname, body in models.items():
        table_m = re.search(r'@@map\("(\w+)"\)', body)
        table = table_m.group(1)
        model.tables.add(table)
        fields = {}
        relations = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("@@"):
                if line.startswith(("@@index", "@@unique")):
                    mm = re.search(r'map:\s*"(\w+)"', line)
                    if mm:
                        model.indexes[mm.group(1)] = table
                    else:
                        model.unnamed_indexes.append((table, line))
                continue
            fm = re.match(r"(\w+)\s+(\w+)(\[\])?(\?)?\s*(.*)$", line)
            if not fm:
                continue
            fname, ftype, is_list, optional, rest = fm.groups()
            if ftype in scalar or ftype in enums:
                cm = re.search(r'@map\("(\w+)"\)', rest)
                column = cm.group(1) if cm else fname
                fields[fname] = (column, bool(optional))
                model.add_column(table, column)
                if "@unique" in rest:
                    model.uniques.add((table, column))
                if ftype in enums:
                    model.enums[(table, column)] = enums[ftype]
            elif ftype in models and not is_list:
                relations.append(rest)
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


def strip_js_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//[^\n]*$|(?<=[\s,;)}\]])//[^\n]*$", "", text)


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
            cm = re.search(r'\(\s*"(\w+)"', em.group(2))
            if not cm:
                continue
            column = cm.group(1)
            field_to_col[em.group(1)] = column
            model.add_column(table, column)
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
            expr = tail[: tail.index("`")]
            expr = re.sub(r"\$\{t\.(\w+)\}", lambda k: field_to_col.get(k.group(1), k.group(1)), expr)
            model.add_check(cm.group(1), table, expr)
        for im in re.finditer(r'(?:uniqueIndex|index)\(\s*"(\w+)"\s*\)', callback):
            model.indexes[im.group(1)] = table
    return model


# ---------------------------------------------------------------------- Django


def kwarg(args, name):
    for part in split_top_level(args):
        if part.startswith(name + "="):
            return part[len(name) + 1:].strip()
    return None


def strip_py_comments(text):
    return re.sub(r"(?m)(^|\s)#[^\n]*$", "", text)


def parse_django(text):
    text = strip_py_comments(text)
    model = Model("django")
    chunks = re.split(r"^(?=class \w+\()", text, flags=re.M)
    for chunk in chunks:
        cm = re.match(r"class (\w+)\(models\.Model\):", chunk)
        if not cm:
            continue
        table = re.search(r'db_table = "(\w+)"', chunk).group(1)
        model.tables.add(table)
        model.add_column(table, "id")
        for fm in re.finditer(r"^    (\w+) = models\.(\w+)\(", chunk, re.M):
            fname, ftype = fm.groups()
            if ftype == "ManyToManyField":
                continue
            args, _ = balanced(chunk, fm.end() - 1)
            if ftype == "ForeignKey":
                column = (kwarg(args, "db_column") or '"%s_id"' % fname).strip('"')
                action = (kwarg(args, "on_delete") or "models.CASCADE").split(".")[-1]
                model.fks[(table, column)] = {
                    "CASCADE": "cascade",
                    "DB_CASCADE": "cascade",
                    "PROTECT": "block",
                    "RESTRICT": "block",
                    "DO_NOTHING": "block",
                    "SET_NULL": "setnull",
                    "DB_SET_NULL": "setnull",
                }[action]
                model.add_column(table, column)
            else:
                model.add_column(table, fname)
                if kwarg(args, "unique") == "True":
                    model.uniques.add((table, fname))
        for c in re.finditer(r"models\.CheckConstraint\(", chunk):
            args, _ = balanced(chunk, c.end() - 1)
            name = kwarg(args, "name").strip('"')
            cond = kwarg(args, "condition") or ""
            expr = None
            em = re.match(r"^Q\(\s*(\w+)__in=\[(.*)\]\s*,?\s*\)$", cond, re.S)
            if em:
                expr = "%s IN (%s)" % (em.group(1), em.group(2))
            model.add_check(name, table, expr)
        for c in re.finditer(r"models\.(?:UniqueConstraint|Index)\(", chunk):
            args, _ = balanced(chunk, c.end() - 1)
            name = kwarg(args, "name").strip('"')
            model.indexes[name] = table
            single = re.match(r'^\["(\w+)"\]$', kwarg(args, "fields") or "")
            if single and c.group(0).startswith("models.Unique") and name == "%s_%s_key" % (table, single.group(1)):
                model.uniques.add((table, single.group(1)))  # spelled as a constraint to skip Django's *_like index
    return model


# --------------------------------------------------------------------- Laravel


def parse_laravel(text):
    model = Model("laravel")
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"Schema::create\('(\w+)'", text)]
    down = text.index("public function down")
    for n, (pos, table) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else down
        chunk = text[pos:end]
        model.tables.add(table)
        for cm in re.finditer(
            r"\$table->(?:bigIncrements|uuid|unsignedBigInteger|text|string|boolean|timestampTz|jsonb|ipAddress"
            r"|bigInteger|smallInteger|integer)\('(\w+)'([^;\n]*)",
            chunk,
        ):
            model.add_column(table, cm.group(1))
            if "->unique()" in cm.group(2):
                model.uniques.add((table, cm.group(1)))
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
        for im in re.finditer(r"\$table->(?:unique|index)\(\[[^\]]*\],\s*'(\w+)'\)", chunk):
            model.indexes[im.group(1)] = table
    add_sql_statements(model, text)
    return model


# ----------------------------------------------------------------------- Rails


def parse_rails(text):
    model = Model("rails")
    starts = [(m.start(), m.group(1), m.group(0)) for m in re.finditer(r"create_table :(\w+)([^\n]*)", text)]
    down = text.index("  def down")
    for n, (pos, table, header) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else down
        chunk = text[pos:end]
        model.tables.add(table)
        if "primary_key:" not in header:
            model.add_column(table, "id")
        for cm in re.finditer(
            r"\bt\.(?:text|string|bigint|uuid|jsonb|boolean|integer|timestamptz|inet)\s+:(\w+)", chunk
        ):
            model.add_column(table, cm.group(1))
        for rm in re.finditer(r"\bt\.references\s+:(\w+)([^\n]*(?:\n[ ]{10,}[^\n]*)*)", chunk):
            column = rm.group(1) + "_id"
            model.add_column(table, column)
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
            model.add_check(nm.group(1), table, expr)
        for im in re.finditer(r"\bt\.index\b[^\n]*(?:\n[ ]{10,}[^\n]*)*", chunk):
            nm = re.search(r"name:\s*'(\w+)'", im.group(0))
            if nm:
                model.indexes[nm.group(1)] = table
            single = re.match(r"\bt\.index\s+:(\w+),\s*unique:\s*true", im.group(0))
            if single:
                model.uniques.add((table, single.group(1)))
    for am in re.finditer(r"add_index\s+:(\w+)([^\n]*(?:\n[ ]{10,}[^\n]*)*)", text):
        nm = re.search(r"name:\s*'(\w+)'", am.group(2))
        if nm:
            model.indexes[nm.group(1)] = am.group(1)
    add_sql_statements(model, text)
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


# ----------------------------------------------------------------------- tests


class TestCanonicalIsSelfConsistent(unittest.TestCase):
    """Guards on the canonical file itself, so a parser gap cannot pass everything vacuously."""

    def test_parsed_the_whole_schema(self):
        self.assertGreaterEqual(len(CANON.tables), 18)
        self.assertGreater(sum(len(c) for c in CANON.columns.values()), 200)
        self.assertGreater(len(CANON.checks), 20)
        self.assertGreater(len(CANON.indexes), 20)
        self.assertGreater(len(CANON.fks), 20)

    def test_every_check_is_named(self):
        # An unnamed CHECK gets a PostgreSQL-generated name no variant can match.
        self.assertEqual(CANON.unnamed_checks, 0)

    def test_approval_decision_is_append_only_and_restricts_its_request(self):
        # audit finding 5.9: a decision could be deleted (and re-entered) by deleting its request.
        self.assertEqual(CANON.fks[("approval_decision", "request_id")], "block")
        sql = read("postgres.sql")
        self.assertRegex(sql, r"request_id\s+bigint\s+NOT NULL REFERENCES approval_request\(id\) ON DELETE RESTRICT")
        for op in ("update", "delete", "truncate"):
            self.assertRegex(sql, r"CREATE TRIGGER approval_decision_no_%s\s+BEFORE %s ON approval_decision" % (op, op.upper()))
            self.assertRegex(sql, r"CREATE TRIGGER audit_event_no_%s\s+BEFORE %s ON audit_event" % (op, op.upper()))

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

    def test_live_grant_uniqueness_covers_global_grants(self):
        self.assertIn("COALESCE(scope_tenant_id, '*')", read("postgres.sql"))


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
