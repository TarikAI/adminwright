# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TarikAI
"""A cited test must be a file a test runner would run, and an assertion must assert.

Before: 'suspend.test.log' holding a log line passed as a test by its content, a
production 'ABTest.kt' and 'test_mode.py' passed on their names alone, conftest.py
counted as a test, and console.assert and Rust's Option.expect counted as assertions.
The conventions are each runner's own (Jest testMatch, pytest discovery, Surefire
includes, xUnit attributes, Pester naming); references/verification.md lists them.
"""

import unittest
from pathlib import Path

from cli_helpers import acm


def is_test(relative, body):
    return acm.looks_like_test_file(Path("/repo") / relative, body, relative)


JS_TEST = "test('suspend', () => {\n  expect(suspend('t-1')).toEqual({ok: true});\n});\n"


class TestWhatARunnerRuns(unittest.TestCase):
    def test_real_tests_in_every_ecosystem(self):
        cases = {
            "tests/suspend.spec.ts": JS_TEST,
            "src/__tests__/suspend.ts": JS_TEST,
            "test/suspend.js": "describe('suspend', () => { it('works', () => { assert.equal(1, x); }); });",
            "tests/test_suspend.py": "def test_suspend():\n    assert suspend('t-1') is True\n",
            "pkg/suspend_test.go": "func TestSuspend(t *testing.T) {\n\tif !Suspend() { t.Fatal(\"no\") }\n}\n",
            "spec/suspend_spec.rb": "describe Tenant do\n  it 'suspends' do\n    expect(t.suspend).to eq(true)\n  end\nend\n",
            "tests/Feature/SuspendTest.php": "class SuspendTest extends TestCase {\n public function testSuspend() {"
                                             " $this->assertTrue($t->suspend()); } }\n",
            "src/test/kotlin/TenantServiceTest.kt": "class TenantServiceTest {\n  @Test fun suspends() {"
                                                    " assertEquals(true, s.suspend()) }\n}\n",
            "Api.Tests/TenantControllerTests.cs": "public class TenantControllerTests {\n [Fact] public void Suspends()"
                                                  " { Assert.True(c.Suspend()); } }\n",
            "src/tenant.rs": "#[cfg(test)]\nmod tests {\n  #[tokio::test]\n  async fn suspends() {"
                             " assert!(suspend().await); }\n}\n",
            "tests/Tenant.Tests.ps1": "Describe 'Suspend' { It 'works' { Suspend | Should -Be $true } }\n",
        }
        for relative, body in cases.items():
            self.assertTrue(is_test(relative, body), relative)

    def test_logs_notes_and_helpers_are_never_tests(self):
        cases = {
            "evidence/suspend.test.log": "tenant.suspend ok (12 ms)\ntest('x', () => {})\n",
            "docs/suspend.spec.md": JS_TEST,
            "tests/conftest.py": "import pytest\n\n@pytest.fixture\ndef tenant():\n    return make()\n",
            "tests/fixtures/tenants.spec.ts": JS_TEST,
            "playwright.config.ts": "export default defineConfig({ testDir: 'tests' });\n",
        }
        for relative, body in cases.items():
            self.assertFalse(is_test(relative, body), relative)

    def test_a_test_name_without_a_test_inside_is_production_code(self):
        self.assertFalse(is_test("src/main/kotlin/ABTest.kt", "class ABTest { fun variant() = check(on) }\n"))
        self.assertFalse(is_test("app/test_mode.py", "TEST_MODE = os.environ.get('TEST_MODE')\n"))
        self.assertFalse(is_test("src/Services/TenantService.cs",
                                 "public class TenantService { void S() { Debug.Assert(ok); } }\n"))

    def test_folders_are_judged_on_the_cited_path_not_the_checkout(self):
        # The project may itself live under a folder called tests/ or fixtures/.
        body = "export const suspend = (id) => repo.update(id, {state: 'suspended'});\n"
        self.assertFalse(acm.looks_like_test_file(Path("/home/me/tests/app/src/suspend.ts"), body, "src/suspend.ts"))
        self.assertTrue(acm.looks_like_test_file(
            Path("/home/me/evals/fixtures/app/tests/suspend.spec.ts"), JS_TEST, "tests/suspend.spec.ts"))


class TestWhatCountsAsAnAssertion(unittest.TestCase):
    def test_idioms_that_assert(self):
        for line in ("expect(x).toBe(3)", "self.assertEqual(a, b)", "assert.equal(a, b)", "XCTAssertEqual(a, b)",
                     "t.Fatalf(\"bad\")", "Expect(err).NotTo(HaveOccurred())", "$this->assertSame(1, $x)"):
            self.assertTrue(acm.has_assertion(line), line)

    def test_calls_that_only_look_like_assertions(self):
        for line in ("console.assert(ready)", "let v = opt.expect(\"present\");", "check(ready)",
                     "log('tenant.suspend ok (12 ms)')"):
            self.assertFalse(acm.has_assertion(line), line)


if __name__ == "__main__":
    unittest.main()
