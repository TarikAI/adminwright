# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Runs drizzle.ts through the real drizzle-kit and applies the result to PostgreSQL.

Skipped unless the PostgreSQL environment of _core_schema_pg.py is available and
ADMINWRIGHT_NPM_TEST=1 with npm/npx on PATH (it downloads drizzle-orm and drizzle-kit from the npm
registry). It does what the drizzle.ts header tells a user to do: `drizzle-kit generate`, then append
the Integrity section of postgres.sql to the emitted migration. The result must apply, expose all 18
tables and every canonical CHECK name, and pass the schema README's verification SQL (the tables
live in the admin_core schema, so the README block runs unchanged).

Standard library only (Python 3.9+).
"""

import shutil
import subprocess
import unittest

from _core_schema_pg import (NPM, NPM_ENABLED, NPM_SKIP_REASON, NPX, SCHEMA, PgCase, canonical_check_names,
                             integrity_section, readme_verification_sql)


@unittest.skipUnless(NPM_ENABLED, NPM_SKIP_REASON)
class TestDrizzleTranslationOnPostgres(PgCase):
    def run_cmd(self, cwd, *cmd):
        result = subprocess.run(
            list(cmd), cwd=str(cwd), capture_output=True, encoding="utf-8", errors="replace", timeout=600
        )
        self.assertEqual(result.returncode, 0, "%s failed:\n%s\n%s" % (cmd, result.stdout, result.stderr))
        return result

    def test_generated_migration_plus_integrity_section_passes_the_verification(self):
        db = self.new_database(apply_schema=False)
        project = self.tmp / "drizzle-project"
        project.mkdir()
        shutil.copy(str(SCHEMA / "drizzle.ts"), str(project / "schema.ts"))
        (project / "drizzle.config.ts").write_text(
            "export default { dialect: 'postgresql', schema: './schema.ts', out: './out' };\n", encoding="utf-8"
        )
        self.run_cmd(project, NPM, "init", "-y")
        self.run_cmd(project, NPM, "install", "--no-audit", "--no-fund", "drizzle-orm", "drizzle-kit")
        self.run_cmd(project, NPX, "drizzle-kit", "generate", "--config", "drizzle.config.ts")

        emitted = sorted((project / "out").glob("*.sql"))
        self.assertEqual(len(emitted), 1)
        migration = emitted[0].read_text(encoding="utf-8").replace("--> statement-breakpoint", "")
        self.psql(db, "-f", self.sql_file("migration.sql", migration + "\n" + integrity_section()))

        self.assertEqual(self.scalar(db, "SELECT count(*) FROM pg_tables WHERE schemaname = 'admin_core'"), "18")
        actual = set(self.scalar(
            db,
            "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE c.contype = 'c' AND n.nspname = 'admin_core'",
        ).split())
        self.assertEqual(actual, canonical_check_names())
        verified = self.psql(db, "-f", self.sql_file("verify.sql", readme_verification_sql()))
        self.assertIn("all admin_core checks passed", verified.stderr + verified.stdout)


if __name__ == "__main__":
    unittest.main()
