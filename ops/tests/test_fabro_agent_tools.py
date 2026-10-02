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

    def test_quoted_and_escaped_words_are_not_commands(self):
        self.assertEqual([w[0] for w in fat.commands("grep -n 'a\\|b' f | head")], ["grep", "head"])
        self.assertEqual([w[0] for w in fat.commands('echo "git stash"')], ["echo"])
        self.assertEqual(self.mutating("grep -n 'git stash' f"), [])
        self.assertEqual([w[0] for w in fat.commands("grep -n 'def\\|class' f")], ["grep"])

    def test_prefixes_dropped(self):
        self.assertEqual([w[0] for w in fat.commands("cd x && timeout 30 uv run pytest -q")], ["pytest"])


class Report(unittest.TestCase):
    def test_local_only(self):
        out = report(True)
        self.assertIn("code_* + fabro-code 2; native grep 1; shell grep/rg 1", out)
        self.assertIn("2 calls, 110 bytes over 3 stage visits", out)
        self.assertIn("1 of 2 = 50.0%", out)  # whole-file share
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


if __name__ == "__main__":
    unittest.main()
