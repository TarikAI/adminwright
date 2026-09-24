# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Shared plumbing for the tests that need a real PostgreSQL (not itself a test module).

Enabled only when ADMINWRIGHT_PG_TEST=1 and a `psql` binary is on PATH (or ADMINWRIGHT_PSQL
points at one). The connection comes from the standard libpq variables (PGHOST, PGPORT,
PGUSER, PGPASSWORD); the user must be a superuser, because the tests create and drop
databases and roles. CI's "core schema on PostgreSQL" job sets all of that up against a
service container. Locally:

    docker run --rm -d -p 54320:5432 -e POSTGRES_PASSWORD=pw postgres:16
    ADMINWRIGHT_PG_TEST=1 PGHOST=127.0.0.1 PGPORT=54320 PGUSER=postgres PGPASSWORD=pw \\
        python -m unittest discover -s tests -p "test_core_schema_*.py" -v

Standard library only (Python 3.9+).
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "assets" / "admin-core-schema"

PSQL = os.environ.get("ADMINWRIGHT_PSQL") or shutil.which("psql")
ENABLED = os.environ.get("ADMINWRIGHT_PG_TEST") == "1" and bool(PSQL)
SKIP_REASON = "set ADMINWRIGHT_PG_TEST=1 and provide psql + a PostgreSQL superuser to run"

# The Prisma and Drizzle translations are checked with their real CLIs, which are downloaded
# from the npm registry, so those tests are opt-in: ADMINWRIGHT_NPM_TEST=1 with npm/npx on PATH.
NPM = shutil.which("npm")
NPX = shutil.which("npx")
NPM_ENABLED = ENABLED and os.environ.get("ADMINWRIGHT_NPM_TEST") == "1" and bool(NPM and NPX)
NPM_SKIP_REASON = SKIP_REASON + "; plus ADMINWRIGHT_NPM_TEST=1 with npm and npx on PATH"


def readme_verification_sql(schema="admin_core"):
    """The SQL block under "Verifying the install" in the schema README, aimed at `schema`."""
    readme = (SCHEMA / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Verifying the install"):]
    sql = re.search(r"```sql\n(.*?)\n```", section, re.S).group(1) + "\n"
    if schema != "admin_core":
        sql = sql.replace("SET LOCAL search_path = admin_core, public;", "SET LOCAL search_path = %s;" % schema)
    return sql


def integrity_section():
    """postgres.sql from the "Integrity" heading to COMMIT: every function and trigger. This is
    what the Drizzle header tells a user to append to the migration drizzle-kit generates."""
    sql = (SCHEMA / "postgres.sql").read_text(encoding="utf-8")
    start = sql.index("-- Integrity: hash chain")
    return sql[start: sql.index("\nCOMMIT;", start)] + "\n"


def canonical_check_names():
    text = re.sub(r"--[^\n]*", "", (SCHEMA / "postgres.sql").read_text(encoding="utf-8"))
    return set(re.findall(r"CONSTRAINT\s+(\w+)\s+CHECK\b", text))


class PgCase(unittest.TestCase):
    """Base class: a scratch directory, throwaway databases and roles, a psql wrapper."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aw-core-schema-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dbs = []
        self.roles = []
        self.addCleanup(self._cleanup_server)
        self.tag = "%d_%s" % (os.getpid(), self.id().rsplit(".", 1)[-1][-24:].lower())

    def psql(self, db, *args, check=True):
        result = subprocess.run(
            [PSQL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db, *args],
            capture_output=True, encoding="utf-8", errors="replace", timeout=300,
        )
        if check and result.returncode != 0:
            self.fail("psql %s failed (rc=%d):\n%s\n%s" % (args, result.returncode, result.stdout, result.stderr))
        return result

    def sql_file(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8", newline="\n")
        return str(path)

    def new_database(self, apply_schema=True):
        name = "aw_core_%s" % self.tag
        self.psql("postgres", "-c", 'DROP DATABASE IF EXISTS "%s"' % name)
        self.psql("postgres", "-c", 'CREATE DATABASE "%s"' % name)
        self.dbs.append(name)
        if apply_schema:
            self.psql(name, "-f", str(SCHEMA / "postgres.sql"))
        return name

    def _cleanup_server(self):
        for db in self.dbs:
            self.psql("postgres", "-c", 'DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % db, check=False)
        for role in self.roles:
            self.psql("postgres", "-c", 'DROP ROLE IF EXISTS "%s"' % role, check=False)

    def scalar(self, db, sql):
        return self.psql(db, "-t", "-A", "-c", sql).stdout.strip()

    def server_version(self):
        """(major, minor) of the server, e.g. (14, 23): what a client library's minimum is checked against."""
        num = int(self.scalar("postgres", "SHOW server_version_num"))
        return num // 10000, num % 10000
