# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""Runs drizzle.ts through the real drizzle-kit and applies the result to PostgreSQL.

Skipped unless the PostgreSQL environment of _core_schema_pg.py is available and
ADMINWRIGHT_NPM_TEST=1 with npm/npx on PATH (it downloads drizzle-orm, drizzle-kit and TypeScript
from the npm registry). It does what the drizzle.ts header tells a user to do: `drizzle-kit
generate`, then append the Integrity section of postgres.sql to the emitted migration. The result
must apply, expose all 18 tables and every canonical CHECK name, and pass the schema README's
verification SQL (the tables live in the admin_core schema, so the README block runs unchanged).
It also type-checks, with tsc, an audit insert that leaves the trigger-owned columns out, as the
header says to write it.

Standard library only (Python 3.9+).
"""

import shutil
import subprocess
import unittest

from _core_schema_pg import (NPM, NPM_ENABLED, NPM_SKIP_REASON, NPX, SCHEMA, PgCase, canonical_check_names,
                             integrity_section, readme_verification_sql)

NPM_PACKAGES = ("drizzle-orm@0.45", "drizzle-kit@0.31", "typescript@5")

# The insert the drizzle.ts header describes: chain_seq, prev_hash and row_hash left to the trigger.
PROBE = """\
import { auditEvent } from "./schema";

export const row: typeof auditEvent.$inferInsert = {
  occurredAt: new Date(),
  environment: "production",
  action: "user.suspend",
  targetType: "user",
  result: "succeeded",
};
"""
TSCONFIG = ('{ "compilerOptions": { "strict": true, "noEmit": true, "module": "esnext", '
            '"moduleResolution": "bundler", "target": "es2022", "skipLibCheck": true }, '
            '"files": ["schema.ts", "probe.ts"] }\n')


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
        # Pinned to the release lines this was checked on: a new drizzle major (1.0 is in release
        # candidates, with a different migration layout) must not turn CI red on its own.
        self.run_cmd(project, NPM, "install", "--no-audit", "--no-fund", *NPM_PACKAGES)
        self.run_cmd(project, NPX, "drizzle-kit", "generate", "--config", "drizzle.config.ts")

        # the header's insert compiles: chainSeq and rowHash have client-side placeholders ...
        (project / "probe.ts").write_text(PROBE, encoding="utf-8")
        (project / "tsconfig.json").write_text(TSCONFIG, encoding="utf-8")
        self.run_cmd(project, NPX, "tsc", "-p", "tsconfig.json")

        emitted = sorted((project / "out").glob("*.sql"))
        self.assertEqual(len(emitted), 1)
        migration = emitted[0].read_text(encoding="utf-8").replace("--> statement-breakpoint", "")
        # ... that stay out of the DDL, which matches postgres.sql
        self.assertRegex(migration, r'"chain_seq" bigint NOT NULL,')
        self.assertRegex(migration, r'"row_hash" text NOT NULL,')
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
