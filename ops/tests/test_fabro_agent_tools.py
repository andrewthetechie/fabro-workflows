"""Tests for ops/fabro-agent-tools.py, standard library only.

    python3.11 -m unittest discover -s ops/tests
"""
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
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


FORMS_A = HERE / "fixtures/agent-tools/forms-a.jsonl"
FORMS_B = HERE / "fixtures/agent-tools/forms-b.jsonl"


class Forms(unittest.TestCase):
    def report(self, *paths):
        return fat.report([fat.load_run(p, None) for p in paths], False)

    def test_pipe_filter_counts_in_the_old_m1_only(self):
        out = self.report(FORMS_A)
        # old M1: 1 code call of 4 searches (the grep after `|` counts); new M1 drops that grep.
        self.assertIn("share 25.0%; without pipe filters 33.3%", out)
        self.assertIn("shell grep/rg 3 (grep 2, rg 1; 1 read a pipe)", out)

    def test_both_halves_of_an_or_command_count(self):
        out = self.report(FORMS_A)
        # rg, the grep after `||`, and the grep after `|`: three shell searches, one of them a pipe filter.
        self.assertIn("shell grep/rg 3 (grep 2, rg 1; 1 read a pipe)", out)

    def test_read_threshold_is_twelve_kilobytes(self):
        out = self.report(FORMS_A)
        self.assertIn("whole reads >= 12 KB: 1 = 1.00 per visit, 20480 bytes per visit", out)
        self.assertIn("2 of 2 = 100.0%", out)  # the 2 KB whole read is whole, but not big
        out_b = self.report(FORMS_B)
        self.assertIn("whole reads >= 12 KB: 1 = 1.00 per visit", out_b)  # 12288 bytes counts, 4096 offset read does not

    def test_by_repo_has_one_line_per_repository(self):
        out = self.report(FORMS_A, FORMS_B)
        block = out.split("By repo", 1)[1].split("\n\nM4", 1)[0].splitlines()[1:]
        self.assertEqual([line.split(":", 1)[0].strip() for line in block],
                         ["example/forms-alpha", "example/forms-beta"])
        self.assertIn("    example/forms-alpha: M1 33.3% (1/3); M2 22528; M3 100.0% / 100.0% / 1.00", out)
        self.assertIn("    example/forms-beta: M1 0.0% (0/2); M2 16384; M3 50.0% / 75.0% / 1.00", out)


OUTCOME_DIR = HERE / "fixtures/agent-tools"
OUTCOME_RUNS = [OUTCOME_DIR / f"{name}.jsonl" for name in ("OUTCOMEA01", "OUTCOMEB01", "OUTCOMEC01", "OUTCOMED01")]
GH_STUB = """#!/bin/sh
# Stands in for gh: `gh pr view <url> --json comments`. A pull/2 URL has two comments, and the
# newer one has the reason on its second line. Any other URL fails, as gh does for a PR it cannot see.
case "$3" in
    */pull/2) printf '%s\\n' '{"comments":[{"body":"**Reason:** older","createdAt":"2026-10-01T09:00:00Z"},{"body":"thanks\\n**Reason:** newest","createdAt":"2026-10-01T10:00:00Z"}]}' ;;
    *) echo "no such PR" >&2; exit 1 ;;
esac
"""


class Outcomes(unittest.TestCase):
    def run_cli(self, *args, path_prefix=None):
        env = dict(os.environ)
        if path_prefix:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
        return subprocess.run([sys.executable, str(HERE.parent / "fabro-agent-tools.py"), *args],
                              capture_output=True, text=True, env=env)

    def table(self, out):
        lines = out.splitlines()
        i = lines.index("Outcomes per run (creation order)")
        rows = []
        for line in lines[i + 2:]:
            if not line.strip():
                break
            rows.append(re.split(r"\s{2,}", line.strip()))
        return {row[0]: row for row in rows}

    def test_one_row_per_run_with_each_column(self):
        p = self.run_cli("--outcomes", *map(str, OUTCOME_RUNS))
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = self.table(p.stdout)
        self.assertEqual(rows["OUTCOMEA01"], ["OUTCOMEA01", "example/outcome-alpha", "2026-10-01 00:00", "20.0",
                                              "bbbbbbbbbbbb", "report_blocked", "pass", "false", "0/1", "1",
                                              "1/0/0/0", "kimi/kimi-k3>zai/glm-5.3 x1"])
        self.assertEqual(rows["OUTCOMEB01"], ["OUTCOMEB01", "example/outcome-beta", "2026-10-01 01:00", "10.0",
                                              "-", "report_merged", "-", "true", "-", "0", "0/0/0/0", "-"])
        self.assertEqual(rows["OUTCOMEC01"], ["OUTCOMEC01", "example/outcome-gamma", "2026-10-01 02:00", "5.0",
                                              "-", "failed", "-", "-", "-", "0", "0/0/0/0", "-"])
        self.assertEqual(rows["OUTCOMED01"], ["OUTCOMED01", "example/outcome-delta", "2026-10-01 03:00", "5.0",
                                              "-", "failed", "-", "-", "-", "0", "0/0/0/0", "-"])
        self.assertEqual(list(rows), ["OUTCOMEA01", "OUTCOMEB01", "OUTCOMEC01", "OUTCOMED01"])  # creation order

    def test_block_reason_is_printed_in_full_and_gh_is_not_called_without_prs(self):
        out = self.run_cli("--outcomes", *map(str, OUTCOME_RUNS)).stdout
        self.assertIn("    OUTCOMEA01  risk > 3", out)
        self.assertNotIn("OUTCOMEB01  ", out.split("Block reasons", 1)[1].split("Entries", 1)[0])

    def test_prs_takes_the_newest_reason_line_and_question_mark_on_gh_error(self):
        with tempfile.TemporaryDirectory() as bindir:
            gh = Path(bindir) / "gh"
            gh.write_text(GH_STUB)
            gh.chmod(0o755)
            p = self.run_cli("--outcomes", "--prs", *map(str, OUTCOME_RUNS), path_prefix=bindir)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("    OUTCOMEA01  risk > 3", p.stdout)  # the key wins, gh is not asked
        self.assertIn("    OUTCOMEB01  **Reason:** newest", p.stdout)
        self.assertIn("    OUTCOMEC01  ?", p.stdout)
        # A merged run publishes pr_url as "": no PR to ask, so no line and no gh call.
        self.assertNotIn("OUTCOMED01  ", p.stdout.split("Block reasons", 1)[1].split("Entries", 1)[0])

    def test_signatures_mask_digits_and_rework_entries_count_by_source(self):
        out = self.run_cli("--outcomes", *map(str, OUTCOME_RUNS)).stdout
        self.assertIn("Entries to rework_router by source node: {'validate': 1}", out)
        self.assertIn("2  stage.failed validate Script failed attempt N of N", out)  # attempt 3 and 1 of 4
        self.assertIn("1  agent.route.failover spec provider kimi usage limit N of N", out)
        self.assertIn("4 event(s)", out)

    def test_prs_needs_outcomes(self):
        self.assertEqual(self.run_cli("--prs", *map(str, OUTCOME_RUNS)).returncode, 2)

    def test_without_outcomes_the_output_is_the_metrics_report(self):
        p = self.run_cli(*map(str, OUTCOME_RUNS))
        self.assertEqual(p.returncode, 0, p.stderr)
        runs = [fat.load_run(path, None) for path in OUTCOME_RUNS]
        self.assertEqual(p.stdout, fat.report(runs, False) + "\n")


if __name__ == "__main__":
    unittest.main()
