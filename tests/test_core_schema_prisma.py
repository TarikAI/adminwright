# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Runs prisma.schema through the real Prisma 7 CLI and applies the result to PostgreSQL.

Skipped unless the PostgreSQL environment of _core_schema_pg.py is available, `npm` and `npx`
are on PATH, and ADMINWRIGHT_PRISMA_TEST=1 (it downloads prisma@7 from the npm registry, so it
is opt-in locally; CI's "core schema on PostgreSQL" job sets it). It checks what the audit
checked by hand: `prisma validate` passes on Prisma 7 (no datasource `url`), the DDL Prisma
emits has no ON DELETE SET NULL, and the emitted DDL plus prisma_raw.sql produces a database
that passes the schema README's verification SQL.

Standard library only (Python 3.9+).
"""

import os
import shutil
import subprocess
import unittest

from _core_schema_pg import ENABLED, SCHEMA, SKIP_REASON, PgCase, readme_verification_sql

NPM = shutil.which("npm")
NPX = shutil.which("npx")
OPTED_IN = os.environ.get("ADMINWRIGHT_PRISMA_TEST") == "1" and bool(NPM and NPX)

CONFIG = """\
import { defineConfig } from "prisma/config";
export default defineConfig({
  schema: "schema.prisma",
  datasource: { url: "postgresql://%(user)s@%(host)s:%(port)s/%(db)s" },
});
"""


@unittest.skipUnless(ENABLED and OPTED_IN, SKIP_REASON + "; plus ADMINWRIGHT_PRISMA_TEST=1 with npm/npx on PATH")
class TestPrismaTranslationOnPostgres(PgCase):
    def run_cmd(self, cwd, *cmd):
        result = subprocess.run(
            list(cmd), cwd=str(cwd), capture_output=True, encoding="utf-8", errors="replace", timeout=600
        )
        self.assertEqual(result.returncode, 0, "%s failed:\n%s\n%s" % (cmd, result.stdout, result.stderr))
        return result

    def test_prisma7_validates_and_its_ddl_plus_raw_sql_passes_the_verification(self):
        db = self.new_database(apply_schema=False)
        project = self.tmp / "prisma-project"
        project.mkdir()
        shutil.copy(str(SCHEMA / "prisma.schema"), str(project / "schema.prisma"))
        (project / "prisma.config.ts").write_text(
            CONFIG % {"user": os.environ["PGUSER"], "host": os.environ.get("PGHOST", "localhost"),
                      "port": os.environ.get("PGPORT", "5432"), "db": db},
            encoding="utf-8",
        )
        self.run_cmd(project, NPM, "init", "-y")
        self.run_cmd(project, NPM, "install", "--no-audit", "--no-fund", "prisma@7")

        validated = self.run_cmd(project, NPX, "prisma", "validate")  # audit 5.7
        self.assertIn("is valid", validated.stdout + validated.stderr)
        self.run_cmd(project, NPX, "prisma", "migrate", "diff", "--from-empty", "--to-schema", "schema.prisma",
                     "--script", "-o", "ddl.sql")
        ddl = (project / "ddl.sql").read_text(encoding="utf-8-sig")
        self.assertNotIn("ON DELETE SET NULL", ddl)  # audit 5.6
        self.assertIn("ON DELETE RESTRICT", ddl)  # approval_decision.request_id (audit 5.9)

        self.psql(db, "-f", str(project / "ddl.sql"))
        self.psql(db, "-f", str(SCHEMA / "prisma_raw.sql"))
        verified = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql("public")))
        self.assertIn("all admin_core checks passed", verified.stderr + verified.stdout)


if __name__ == "__main__":
    unittest.main()
