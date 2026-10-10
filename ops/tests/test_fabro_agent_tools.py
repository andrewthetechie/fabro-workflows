"""Tests for ops/fabro-agent-tools.py, standard library only.

    python3.11 -m unittest discover -s ops/tests
"""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fat", HERE.parent / "fabro-agent-tools.py")
fat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fat)
FIXTURE = HERE / "fixtures/agent-tools/run-fixture.jsonl"


def report(local_only):
    run = fat.load_run(FIXTURE, None)
    return fat.report([run], local_only)


class Classification(unittest.TestCase):
    def mutating(self, command):
        return [g for w in fat.commands(command) if w[0] == "git" and (g := fat.git_is_mutating(w))]

    def test_stash_and_pop_in_one_call(self):
        self.assertEqual(self.mutating("cd a && git stash && uv run mypy x; git stash pop"), ["stash", "stash"])

    def test_read_only_forms_pass(self):
        for c in ("git -C x status", "git --no-pager log -3", "git stash list", "git branch --show-current",
                  "git branch -a --contains abc123", "git worktree list", "git config --get user.name",
                  "git diff HEAD^ HEAD | head"):
            self.assertEqual(self.mutating(c), [], c)

    def test_mutating_forms(self):
        for c, want in (("git checkout -- a", "checkout"), ("git fetch", "fetch"), ("git -C x commit -m y", "commit"),
                        ("git branch -D x", "branch"), ("xargs git add", "add")):
            self.assertEqual(self.mutating(c), [want], c)

    def test_git_after_a_reserved_word(self):
        self.assertEqual(self.mutating("if git diff --quiet; then git stash; fi"), ["stash"])
        self.assertEqual(self.mutating("for f in a b; do git checkout -- $f; done"), ["checkout"])
        self.assertEqual(self.mutating("! git checkout -- x"), ["checkout"])
        self.assertEqual(self.mutating("echo then git stash"), [])

    def test_guard_reasons_count_as_blocked(self):
        # The reason as fabro hands it to the agent (bridge.rs passes it unchanged).
        self.assertTrue(fat.BLOCKED_RE.search(
            "git-guard: git stash is blocked: agents do not change files, the index or branches with git."))

    def test_quoted_and_escaped_words_are_not_commands(self):
        self.assertEqual([w[0] for w in fat.commands("grep -n 'a\\|b' f | head")], ["grep", "head"])
        self.assertEqual([w[0] for w in fat.commands('echo "git stash"')], ["echo"])
        self.assertEqual(self.mutating("grep -n 'git stash' f"), [])
        self.assertEqual([w[0] for w in fat.commands("grep -n 'def\\|class' f")], ["grep"])

    def test_prefixes_dropped(self):
        self.assertEqual([w[0] for w in fat.commands("cd x && timeout 30 uv run pytest -q")], ["pytest"])

    def test_stdin_filters(self):
        def filters(command):
            return [w[0] for w, f in fat.commands_piped(command) if f]
        self.assertEqual(filters("cargo test 2>&1 | grep 'test result'"), ["grep"])
        self.assertEqual(filters("a |\n grep x"), ["grep"])
        self.assertEqual(filters("a | (rg x)"), ["rg"])
        # A search over files is not a filter, even when it reads a pipe through xargs.
        self.assertEqual(filters("find . -name x | xargs grep -l Foo"), [])
        self.assertEqual(filters("grep -rn foo src/ | head"), ["head"])
        self.assertEqual(filters("a || grep x f; b && rg y"), [])
        self.assertEqual(filters("grep -n 'a|b' f"), [])


class Report(unittest.TestCase):
    def test_local_only(self):
        out = report(True)
        self.assertIn("code_* + fabro-code 2; native grep 1; shell grep/rg 1", out)
        self.assertIn("2 calls, 110 bytes over 3 stage visits", out)
        self.assertIn("1 of 2 = 50.0%; by bytes 90.9%", out)  # whole-file share
        self.assertIn("whole reads >= 12 KB: 0 = 0.00 per visit", out)
        self.assertIn("(grep 1, rg 0; 0 read a pipe)", out)
        self.assertIn("share 50.0%; without pipe filters 50.0%", out)
        self.assertIn("1 of 1 improve visits", out)  # only the tracked edit counts
        self.assertIn("3.0 min over 2 coder visit(s)", out)

    def test_all_models_counts_hosted_search(self):
        self.assertIn("native grep 2", report(False))

    def test_git_counts_executed_outside_exempt_stages_only(self):
        out = report(True)
        self.assertIn("executed 1 shell call(s) in 1 stage visit(s)", out)
        self.assertIn("'stash': 2", out)
        self.assertIn("blocked attempts (reported separately): 1", out)

    def test_memory_and_review(self):
        out = report(True)
        self.assertIn("1 of 3 = 33.3%", out)
        self.assertIn("1 of 1 tasks approved at the first review_gate", out)
        self.assertIn("'rework_t1': 1", out)


TEST_ENV_FIXTURES = [
    HERE / "fixtures/agent-tools/test-env-womens.jsonl",
    HERE / "fixtures/agent-tools/test-env-lawncare.jsonl",
]


class TestEnv(unittest.TestCase):
    def report(self):
        runs = [fat.load_run(p, None) for p in TEST_ENV_FIXTURES]
        return fat.report(runs, False)

    def test_repo_of(self):
        self.assertEqual(fat.repo_of("https://github.com/andrewthetechie/womens-fantasy-sports.git"),
                         "andrewthetechie/womens-fantasy-sports")
        self.assertEqual(fat.repo_of("git@github.com:andrewthetechie/lawncare-saas.git"),
                         "andrewthetechie/lawncare-saas")
        self.assertEqual(fat.repo_of(""), "unknown")

    def test_test_kind(self):
        self.assertEqual(fat.test_kind(["pytest", "-q"]), "test")
        self.assertEqual(fat.test_kind(["npm", "run", "test:unit"]), "test")
        self.assertEqual(fat.test_kind(["bun", "run", "build"]), None)
        self.assertEqual(fat.test_kind(["fabro-test", "backend"]), "fabro")
        self.assertEqual(fat.test_kind(["fabro-io", "run-tests", "backend"]), "fabro")
        self.assertEqual(fat.test_kind(["grep", "pytest", "x"]), None)

    def test_m_env_counts_the_refusals_per_repository(self):
        out = self.report()
        self.assertIn("all repositories: 2 of 3 stage visit(s) = 66.7%", out)
        self.assertIn("andrewthetechie/womens-fantasy-sports: 1 of 2 stage visit(s) = 50.0%; 1 of 1 run(s)", out)
        self.assertIn("andrewthetechie/lawncare-saas: 1 of 1 stage visit(s) = 100.0%; 1 of 1 run(s)", out)
        self.assertIn("by stage {'coder': 2}", out)

    def test_m_run_counts_entry_points_against_direct_runners(self):
        out = self.report()
        self.assertIn("2 of 7 = 28.6%", out)
        self.assertIn("'run_tests': 1", out)
        self.assertIn("'fabro-test': 1", out)
        self.assertIn("'direct pytest': 2", out)


if __name__ == "__main__":
    unittest.main()
