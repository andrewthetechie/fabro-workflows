"""The review-merge phase's gates: merge_gate (the hold label, diff hygiene, the Refuter
and the commit-author check), the hygiene counters, ci_fix_gate, refute_prep,
refute_gate, refute_fix_gate, remerge_base / resolve_base_gate and the refute guard."""

import shutil

import pytest

from harness import (ORIG_PATH, REPO_ROOT, SHARED, WF, cat, count, git, jq, jq_n,
                     lastjson, mkreadok, present, rebase, extract, rm, run, sh, stage, stub,
                     write)

FV = "fac3fac3fac3fac3fac3fac3fac3fac3"
FIX_DIR = REPO_ROOT / "ops/fabro-io/tests/fixtures"

# `gh pr view ... --jq <expr>` is emulated by running jq on the fixture, so each check sees
# exactly what it would see from GitHub for that PR.
PR_VIEW_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "pr view" ]; then
  case "$*" in *do-not-merge*) [ -n "$GH_FAIL_DN" ] && exit 1 ;; esac
  J=""; P=""
  for a in "$@"; do [ "$P" = "--jq" ] && J="$a"; P="$a"; done
  if [ -n "$J" ]; then jq -r "$J" "$GH_STATE/pr.fixture.json"; else cat "$GH_STATE/pr.fixture.json"; fi
  exit 0
fi
exit 0
"""


# ---------------------------------------------------------------------------
# review-merge merge_gate — the do-not-merge hold label (ADR 0012 D8 amendment, gate-relief G2)
# ---------------------------------------------------------------------------
def test_merge_gate_do_not_merge(ck, tmp_path):
    T = tmp_path / "mgate"
    (T / "bin").mkdir(parents=True); (T / "review").mkdir()
    gate = stage(ck, "merge_gate", T, graph=SHARED)
    stub(T / "bin", "gh", PR_VIEW_GH)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    write(T / "auto_merge", "1\n"); write(T / "pr_number", "5\n")
    eligible = lambda out: jq(".context_updates.merge_eligible", lastjson(out))

    # 1. do-not-merge, with agent-authored: blocked, with the reason.
    write(T / "pr.fixture.json", '{"labels":[{"name":"agent-authored"},{"name":"do-not-merge"}],"state":"OPEN","isDraft":false}\n')
    rm(T / "merge_block_reason")
    r = sh(gate, path=path, env=env)
    ck("hold: exit 0", "0", r.rc)
    ck("hold: not eligible", "false", eligible(r.out))
    ck("hold: reason names the label", "1", count("do-not-merge label", file=T / "merge_block_reason"))

    # 2. architecture alone: check 2b does not hold it.
    write(T / "pr.fixture.json", '{"labels":[{"name":"agent-authored"},{"name":"architecture"}],"state":"CLOSED","isDraft":false}\n')
    rm(T / "merge_block_reason")
    sh(gate, path=path, env=env)
    ck("architecture: not held by 2b", "0", count("do-not-merge", file=T / "merge_block_reason"))

    # 3. gh fails reading the label: the check fails closed.
    write(T / "pr.fixture.json", '{"labels":[{"name":"agent-authored"}],"state":"OPEN","isDraft":false}\n')
    rm(T / "merge_block_reason")
    r = sh(gate, path=path, env={**env, "GH_FAIL_DN": "1"})
    ck("gh error on hold check: exit 0", "0", r.rc)
    ck("gh error on hold check: not eligible", "false", eligible(r.out))
    ck("gh error on hold check: reason", "1", count("do-not-merge label", file=T / "merge_block_reason"))


# ---------------------------------------------------------------------------
# review-merge hygiene — tamper and erosion counters (ADR 0013 D1, D2), real git
#
# The fixtures ARE the counters' contract (docs/merge-gate/00, C5): a pattern change starts
# here. Real git, because what is under test is what `git diff -U0 origin/main...HEAD`
# shows for each kind of change.
# ---------------------------------------------------------------------------
class Hygiene:
    """The hygiene node against a fresh upstream per case: a Python test, a module and a Rust
    file, cloned onto a run branch."""

    def __init__(self, ck, tmp_path):
        self.T = T = tmp_path / "hyg"
        self.node = stage(ck, "hygiene", T, graph=SHARED, name="hygiene_node")
        self.up, self.wt = T / "up", T / "wt"

    @staticmethod
    def lines_to(p, *ls):
        write(p, "".join(l + "\n" for l in ls))

    def repo(self, cfg=None):
        """cfg, when given, is the base branch's .fabro/hygiene.json."""
        T, up, wt = self.T, self.up, self.wt
        rm(up, wt, T / "review", T / "_hygiene")
        for d in ("tests", "src", ".fabro"):
            (up / d).mkdir(parents=True)
        git("-C", up, "init", "-q", "-b", "main", ".")
        git("-C", up, "config", "user.email", "t@t"); git("-C", up, "config", "user.name", "t")
        self.lines_to(up / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3", "    assert add(0, 0) == 0")
        self.lines_to(up / "src/math.py", "def add(a, b):", "    return a + b")
        self.lines_to(up / "src/lib.rs", "fn f() -> u8 { 1 }")
        if cfg:
            write(up / ".fabro/hygiene.json", cfg)
        git("-C", up, "add", "-A"); git("-C", up, "commit", "-qm", "init")
        git("clone", "-q", up, wt)
        git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
        git("-C", wt, "checkout", "-qb", "fabro/run/01TEST")
        write(T / "base_ref", "main\n")

    def commit(self):
        git("-C", self.wt, "add", "-A"); git("-C", self.wt, "commit", "-qm", "change")

    def run(self):
        sh(self.node, cwd=self.wt)

    def get(self, f):
        return jq(f, file=self.T / "review/hygiene.json")

    def base(self, f, *ls):
        """Give the base branch f with these lines, and restart the run branch from it."""
        self.lines_to(self.up / f, *ls)
        git("-C", self.up, "add", "-A"); git("-C", self.up, "commit", "-qm", "base")
        git("-C", self.wt, "fetch", "-q", "origin"); git("-C", self.wt, "reset", "-q", "--hard", "origin/main")

    def lint(self):
        self.lines_to(self.wt / "src/math.py", "x = 1  # noqa", "y = 2  # noqa", "z = 3  # noqa", "w = 4  # noqa")
        self.commit(); self.run()


def test_hygiene_counters(ck, tmp_path):
    """Cases 1-10: the tamper and erosion counters on ordinary changes."""
    H = Hygiene(ck, tmp_path)
    T, wt = H.T, H.wt
    lines_to, hy_repo, commit, hy_run, hy = (H.lines_to, H.repo, H.commit, H.run, H.get)

    # 1. A real change with no test impact is clear, and leaves no reason file.
    hy_repo()
    lines_to(wt / "src/math.py", "def add(a, b):", '    """Add."""', "    return a + b"); commit(); hy_run()
    ck("hygiene clean: nothing blocks", "0", hy(".blocking | length"))
    ck("hygiene clean: default config", "default", hy(".config"))
    ck("hygiene clean: no reason file", "absent", present(T / "review/hygiene_reason.txt"))

    # 2. Moving a test file is a rename, not a deletion, and must pass.
    hy_repo()
    (wt / "tests/unit").mkdir(); git("-C", wt, "mv", "tests/test_math.py", "tests/unit/test_math.py"); commit(); hy_run()
    ck("hygiene moved test: nothing blocks", "0", hy(".blocking | length"))

    # 3. tests_deleted, and the deleted file's assertions are not counted twice.
    hy_repo()
    git("-C", wt, "rm", "-q", "tests/test_math.py"); commit(); hy_run()
    ck("tests_deleted: counted", "1", hy(".tamper.tests_deleted"))
    ck("tests_deleted: blocks alone", "tests_deleted", hy('.blocking | join(",")'))
    ck("tests_deleted: asserts not doubled", "0", hy(".tamper.asserts_removed"))
    ck("tests_deleted: reason names it", "1", count("tests_deleted=1", file=T / "review/hygiene_reason.txt"))

    # 4. skips_added, with the new-file line number as the sample.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "import pytest", "@pytest.mark.skip", "def test_a():", "    assert add(1, 2) == 3", "    assert add(0, 0) == 0"); commit(); hy_run()
    ck("skips_added: pytest mark", "1", hy(".tamper.skips_added"))
    ck("skips_added: sample line", "2", hy(".samples[0].line"))

    # 5. A JS skip/only counts in a test file, and `stream.skip(` in source does not.
    hy_repo()
    lines_to(wt / "web/a.test.ts", 'it.skip("x", () => {})', 'describe.only("y", () => {})')
    lines_to(wt / "web/a.ts", "stream.skip(3)"); commit(); hy_run()
    ck("skips_added: js test file only", "2", hy(".tamper.skips_added"))

    # 6. Rust keeps tests beside the code, so #[ignore] counts in any file.
    hy_repo()
    lines_to(wt / "src/lib.rs", "fn f() -> u8 { 1 }", "#[test]", "#[ignore]", "fn t() {}"); commit(); hy_run()
    ck("skips_added: rust ignore", "1", hy(".tamper.skips_added"))

    # 7. asserts_removed: one assertion gone, nothing added in its hunk.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3"); commit(); hy_run()
    ck("asserts_removed: counted", "1", hy(".tamper.asserts_removed"))

    # 8. ...but an assertion rewritten in place is not a removal.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3", "    assert add(0, 0) == 0 and add(0, 1) == 1"); commit(); hy_run()
    ck("asserts_removed: rewrite is not", "0", hy(".tamper.asserts_removed"))

    # 9. tautologies_added: a literal subject, a same-argument matcher, `assert True` and
    #    `assert x == x`; `expect(y).toEqual(z)` is not one.
    hy_repo()
    lines_to(wt / "web/b.spec.ts", "expect(true).toBe(true)", "expect(x).toEqual(x)", "expect(y).toEqual(z)")
    lines_to(wt / "tests/test_math.py", "def test_a():", "    assert True", "    assert add(1, 2) == 3", "    assert add(0, 0) == 0", "    assert x == x"); commit(); hy_run()
    ck("tautologies_added: four", "4", hy(".tamper.tautologies_added"))

    # 10. Erosion counters report, and do not block, in the default report mode.
    hy_repo()
    lines_to(wt / "src/math.py", "def add(a, b):", "    try:", "        return a + b", "    except Exception:", "        return 0  # type: ignore", "    # TODO fix this", "    # TODO(#12) linked"); commit(); hy_run()
    ck("erosion: broad_except", "1", hy(".erosion.broad_except"))
    ck("erosion: type_escape", "1", hy(".erosion.type_escape"))
    ck("erosion: only the unlinked TODO", "1", hy(".erosion.todo_unlinked"))
    ck("erosion: report mode never blocks", "0", hy(".blocking | length"))



def test_hygiene_config(ck, tmp_path):
    """Cases 11-17: thresholds, the base branch's config, and the config files the counters guard."""
    H = Hygiene(ck, tmp_path)
    T, wt = H.T, H.wt
    lines_to, hy_repo, commit, hy_run, hy, hy_lint = (H.lines_to, H.repo, H.commit, H.run, H.get, H.lint)

    # 11. Four lint disables: over the default threshold of 3. Report mode lets it through;
    #     block mode on the base branch blocks it; a per-counter threshold of 5 lets it
    #     through again.
    hy_repo(); hy_lint()
    ck("lint_disable: counted", "4", hy(".erosion.lint_disable"))
    ck("lint_disable: report mode passes", "0", hy(".blocking | length"))
    hy_repo('{"erosion_mode":"block"}'); hy_lint()
    ck("lint_disable: block mode blocks", "lint_disable", hy('.blocking | join(",")'))
    ck("block mode: config read from base", "repo", hy(".config"))
    hy_repo('{"erosion_mode":"block","erosion_thresholds":{"lint_disable":5}}'); hy_lint()
    ck("per-counter threshold raises it", "0", hy(".blocking | length"))

    # 12. rust_unwrap in non-test Rust.
    hy_repo()
    lines_to(wt / "src/lib.rs", 'fn f() -> u8 { "1".parse().unwrap() }'); commit(); hy_run()
    ck("rust_unwrap: counted", "1", hy(".erosion.rust_unwrap"))

    # 13. A PR that edits .fabro/hygiene.json trips config_changed, and its own loosened
    #     threshold is NOT the one used.
    hy_repo()
    write(wt / ".fabro/hygiene.json", '{"erosion_mode":"report","erosion_threshold":99}'); commit(); hy_run()
    ck("config_changed: tamper", "1", hy(".tamper.config_changed"))
    ck("config_changed: PR config ignored", "3", hy(".erosion_threshold"))

    # 13a. C6: .fabro/test.toml and .fabro/ci.sh trip config_changed too (ADR 0018 D7); a
    #      sibling example file does not.
    hy_repo()
    lines_to(wt / ".fabro/test.toml", "version = 1"); commit(); hy_run()
    ck("config_changed: test.toml trips", "1", hy(".tamper.config_changed"))
    ck("config_changed: test.toml blocks", "config_changed", hy('.blocking | join(",")'))
    hy_repo()
    lines_to(wt / ".fabro/ci.sh", "set -e"); commit(); hy_run()
    ck("config_changed: ci.sh trips", "1", hy(".tamper.config_changed"))
    hy_repo()
    lines_to(wt / ".fabro/test.toml.example", "version = 1"); commit(); hy_run()
    ck("config_changed: example does not", "0", hy(".tamper.config_changed"))

    # 13b. ADR 0027 D14: .fabro/scout.toml trips config_changed too; a sibling example does not.
    hy_repo()
    lines_to(wt / ".fabro/scout.toml", "version = 1"); commit(); hy_run()
    ck("config_changed: scout.toml trips", "1", hy(".tamper.config_changed"))
    ck("config_changed: scout.toml blocks", "config_changed", hy('.blocking | join(",")'))
    hy_repo()
    lines_to(wt / ".fabro/scout.toml.example", "version = 1"); commit(); hy_run()
    ck("config_changed: scout example does not", "0", hy(".tamper.config_changed"))

    # 14. An invalid base config blocks rather than falling back silently.
    hy_repo('{"erosion_mode":"loud"}'); hy_lint()
    ck("invalid config: blocks", "config_invalid", hy('.blocking | join(",")'))

    # 15. Lockfiles are excluded entirely.
    hy_repo()
    lines_to(wt / "Cargo.lock", "# TODO nothing"); commit(); hy_run()
    ck("exclude_paths: lockfile ignored", "0", hy(".erosion.todo_unlinked"))

    # 16. A removed SQL comment is `--- ...` in the diff body, not a file header.
    hy_repo()
    lines_to(wt / "src/q.sql", "-- a comment", "select 1;"); commit()
    lines_to(wt / "src/q.sql", "select 1;"); commit(); hy_run()
    ck("diff body '---' is not a header", "0", hy(".blocking | length"))

    # 17. A base that cannot be diffed fails closed.
    hy_repo()
    write(T / "base_ref", "nosuch\n"); hy_run()
    ck("no base: hygiene_error blocks", "hygiene_error", hy('.blocking | join(",")'))



def test_hygiene_assertion_rules(ck, tmp_path):
    """Cases 18-26: what does and does not count as a removed assertion."""
    H = Hygiene(ck, tmp_path)
    wt = H.wt
    lines_to, hy_repo, commit, hy_run, hy, hy_base, hy_lint = H.lines_to, H.repo, H.commit, H.run, H.get, H.base, H.lint

    # 18. Rust's `.expect(` is a Result method, not an assertion: replacing it with `?` in
    #     ordinary code is the fix rust_unwrap asks for, and must not count as a removed
    #     assertion.
    hy_repo()
    hy_base("src/lib.rs", "fn f() -> u8 {", '    let x = g().expect("g failed");', "    x", "}")
    lines_to(wt / "src/lib.rs", "fn f() -> Result<u8> {", "    let x = g()?;", "    Ok(x)", "}"); commit(); hy_run()
    ck("rust .expect( removed: not an assert", "0", hy(".tamper.asserts_removed"))
    ck("rust .expect( removed: clear", "0", hy(".blocking | length"))

    # 19. A test moved to another file carries its assertion with it: the same text is
    #     added elsewhere in the PR, so it is not a removal.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3")
    lines_to(wt / "tests/test_zero.py", "def test_zero():", "    assert add(0, 0) == 0"); commit(); hy_run()
    ck("assert moved across files: not removed", "0", hy(".tamper.asserts_removed"))

    # 20. ...but an unrelated assertion added in another file does not excuse one removed.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3")
    lines_to(wt / "tests/test_two.py", "def test_two():", "    assert add(2, 2) == 4"); commit(); hy_run()
    ck("unrelated assert elsewhere: still removed", "1", hy(".tamper.asserts_removed"))

    # 21. A base config that is not one JSON object, or whose per-counter threshold is not
    #     a number, is invalid -- not a silent "repo" config that loosens a counter.
    hy_repo("null"); hy_lint()
    ck("config null: invalid", "config_invalid", hy('.blocking | join(",")'))
    hy_repo('{"erosion_mode":"block","erosion_thresholds":{"lint_disable":"9"}}'); hy_lint()
    ck("config string threshold: invalid", "invalid", hy(".config"))

    login = ("tests/test_login.py", "def test_login():", "    assert isinstance(out, LoginFailed)", "    assert out.clear_cookie")
    # 22. #38: two assertions on one subject replaced by a single stronger equality
    #     assertion on the same subject are not a removal.
    hy_repo(); hy_base(*login)
    lines_to(wt / "tests/test_login.py", "def test_login():", '    assert out == LoginFailed(reason="state missing or mismatch", clear_cookie=True)'); commit(); hy_run()
    ck("merged equality: same subject not removed", "0", hy(".tamper.asserts_removed"))
    ck("merged equality: nothing blocks", "0", hy(".blocking | length"))

    # 23. ...but an added equality assertion on a different subject excuses nothing.
    hy_repo(); hy_base(*login)
    lines_to(wt / "tests/test_login.py", "def test_login():", '    assert other == LoginFailed(reason="state missing or mismatch", clear_cookie=True)'); commit(); hy_run()
    ck("merged equality: other subject counted", "1", hy(".tamper.asserts_removed"))

    # 24. An assertion deleted outright still counts, with no added assertion to excuse it.
    hy_repo(); hy_base(*login)
    lines_to(wt / "tests/test_login.py", "def test_login():", "    assert isinstance(out, LoginFailed)"); commit(); hy_run()
    ck("deleted outright: counted", "1", hy(".tamper.asserts_removed"))

    # 25. `expect(x).toEqual(...)` is an equality assertion too, and excuses removals of
    #     the same subject in its hunk.
    hy_repo()
    hy_base("web/a.test.ts", "function t() {", "  expect(out.ready).toBe(true)", "  expect(out.clearCookie).toBe(true)", "}")
    lines_to(wt / "web/a.test.ts", "function t() {", "  expect(out).toEqual(LoginFailed({clearCookie: true}))", "}"); commit(); hy_run()
    ck("merged expect equality: not removed", "0", hy(".tamper.asserts_removed"))

    # 26. A plain added assertion is not an equality assertion, so it excuses nothing.
    hy_repo(); hy_base(*login)
    lines_to(wt / "tests/test_login.py", "def test_login():", "    assert out.state"); commit(); hy_run()
    ck("plain added assertion: still removed", "1", hy(".tamper.asserts_removed"))


# ---------------------------------------------------------------------------
# review-merge merge_gate — hygiene and Refuter checks (ADR 0013 D1, D5)
#
# These cases need a PR that passes checks 1 to 12, so the stub also answers the GraphQL
# thread count and the fixture carries a Conventional title and a commit-body block.
# ---------------------------------------------------------------------------


def test_merge_gate_hygiene_and_refuter(ck, tmp_path):
    T = tmp_path / "mgate2"
    (T / "bin").mkdir(parents=True); (T / "review").mkdir()
    gate = T / "merge_gate.sh"
    gate.write_text(rebase(extract(SHARED, "merge_gate"), T))
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1" = api ]; then echo 0; exit 0; fi
if [ "$1 $2" = "pr view" ]; then
  J=""; P=""
  for a in "$@"; do [ "$P" = "--jq" ] && J="$a"; P="$a"; done
  if [ -n "$J" ]; then jq -r "$J" "$GH_STATE/pr.fixture.json"; else cat "$GH_STATE/pr.fixture.json"; fi
  exit 0
fi
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    g = lambda *a, **kw: git("-C", T, *a, **kw)
    fixture = T / "pr.fixture.json"
    reason = T / "merge_block_reason"

    # Check 13b compares the tree the counters read with HEAD's, so the gate runs in a real
    # repository. `code/` is the only tracked path; the sandbox files stay untracked.
    g("init", "-q", "-b", "run", ".")
    g("config", "user.email", "t@t"); g("config", "user.name", "t")
    write(T / "code/a.txt", "one\n")
    g("add", "code"); g("commit", "-qm", "init")

    def hygiene_at_head():
        write(T / "review/hygiene.json", jq_n("{blocking:[], head:$h}", "--arg", "h", g("rev-parse", "HEAD"), compact=False) + "\n")

    def green():
        """Everything green: every check from 1 to 14 passes."""
        write(T / "auto_merge", "1\n"); write(T / "pr_number", "5\n"); write(T / "base_ref", "main\n")
        write(T / "pr.json", '{"url":"https://github.com/o/r/pull/5"}\n')
        write(fixture, jq_n(r'''{labels:[{name:"agent-authored"}], state:"OPEN", isDraft:false, reviewDecision:"APPROVED",
            commits:[], title:"fix: a thing",
            body:(["x","<!-- fabro:commit-body:start -->","Resolves #1","<!-- fabro:commit-body:end -->"] | join("\n"))}''', compact=False) + "\n")
        write(T / "review/fix_result.json", '{"outcome":"no_changes_needed","risk":1,"not_fixed":[],"own_findings":[],"fixes_applied":[]}\n')
        hygiene_at_head()
        write(T / "review/refute_verdict", "pass\n")
        rm(reason, T / "review/hygiene_reason.txt", T / "review/refute_reason.txt")

    def mg_run():
        r = sh(gate, cwd=T, path=path, env=env)
        return jq(".context_updates.merge_eligible", lastjson(r.out))

    def fixture_jq(filt, *args):
        write(fixture, jq(filt, file=fixture, args=list(args), raw=False) + "\n")

    # 1. The all-green fixture really is eligible, so every block below is the new check.
    green()
    ck("all green: eligible", "true", mg_run())

    # 1b. An architecture PR with every other check passing merges: the label does not hold it.
    green(); fixture_jq('.labels += [{name:"architecture"}]')
    ck("architecture, all green: eligible", "true", mg_run())

    # 1c. do-not-merge holds an otherwise green PR, and the reason says so.
    green(); fixture_jq('.labels += [{name:"do-not-merge"}]')
    ck("do-not-merge, all green: not eligible", "false", mg_run())
    ck("do-not-merge, all green: reason", "1", count("do-not-merge label", file=reason))

    # 2. Check 13 blocks on a non-empty `blocking`, with the counters' own reason.
    green()
    write(T / "review/hygiene.json", '{"blocking":["skips_added"]}\n')
    write(T / "review/hygiene_reason.txt", "Diff hygiene blocks the merge: skips_added=1.\n")
    ck("hygiene blocking: not eligible", "false", mg_run())
    ck("hygiene blocking: reason", "1", count("skips_added=1", file=reason))

    # 3. ...and fails closed on a missing file, or one with no `blocking` array.
    green(); rm(T / "review/hygiene.json")
    ck("hygiene missing: not eligible", "false", mg_run())
    ck("hygiene missing: reason", "1", count("missing or invalid", file=reason))
    green(); write(T / "review/hygiene.json", "{}\n")
    ck("hygiene without blocking: blocks", "false", mg_run())

    # 4. Check 14 blocks on a `fail` verdict, with the gate's reason.
    green()
    write(T / "review/refute_verdict", "fail\n")
    write(T / "review/refute_reason.txt", "The refuter could not confirm the work is done: not_met: logout works.\n")
    ck("refute fail: not eligible", "false", mg_run())
    ck("refute fail: reason", "1", count("not_met: logout", file=reason))

    # 5. ...and on NO verdict: the Refuter timed out or its provider was down.
    green(); rm(T / "review/refute_verdict")
    ck("refute missing: not eligible", "false", mg_run())
    ck("refute missing: reason", "1", count("did not return a verdict", file=reason))

    # 6. Check 13b: an empty checkpoint commit after the counters ran moves HEAD but not
    # the tree, and must not block...
    green(); g("commit", "-q", "--allow-empty", "-m", "checkpoint")
    ck("13b empty checkpoint: eligible", "true", mg_run())

    # 7. ...while a stage that edited the code after them (the refuter) blocks.
    green(); write(T / "code/a.txt", "two\n"); g("commit", "-qam", "refute edited")
    ck("13b tree changed: not eligible", "false", mg_run())
    ck("13b tree changed: reason", "1", count("code changed after the diff-hygiene", file=reason))

    # 8. ...and a hygiene.json with no head fails closed.
    green(); write(T / "review/hygiene.json", '{"blocking":[]}\n')
    ck("13b no head: not eligible", "false", mg_run())

    # Check 6, real git. The run branch holds a fabro-co-authored commit and a merge of a
    # moved origin/main, authored by the agent identity alone (what next_task and
    # open_pr_prep produce). `cm_state` writes the PR's commit list the way gh reports it.
    g("branch", "-q", "-f", "base", "HEAD")
    g("update-ref", "refs/remotes/origin/main", g("rev-parse", "base"))
    g("checkout", "-q", "-b", "runbr")
    write(T / "code/w.txt", "w\n"); g("add", "code"); g("commit", "-qm", "fabro work")
    W = g("rev-parse", "HEAD")
    g("checkout", "-q", "-b", "mainmoved", "base")
    write(T / "code/m.txt", "m\n"); g("add", "code"); g("commit", "-qm", "main moved")
    g("update-ref", "refs/remotes/origin/main", g("rev-parse", "HEAD"))
    g("checkout", "-q", "runbr")
    g("merge", "-q", "--no-edit", "origin/main")
    G = g("rev-parse", "HEAD")
    g("checkout", "-q", "-b", "other", "base")
    write(T / "code/o.txt", "o\n"); g("add", "code"); g("commit", "-qm", "unmerged side branch")
    g("checkout", "-q", "runbr")
    g("merge", "-q", "--no-edit", "other")
    GO = g("rev-parse", "HEAD")

    def cm_state(e):
        """The fabro commit W plus that case's commits: merge, other or human."""
        green()
        fixture_jq('''
      .commits = [{oid:$w, authors:[{email:"noreply@fabro.sh"}]}]
        + (if $e == "merge" then [{oid:$g, authors:[{email:"bot@users.noreply.github.com"}]}]
           elif $e == "other" then [{oid:$g, authors:[{email:"bot@users.noreply.github.com"}]},
                                    {oid:$go, authors:[{email:"bot@users.noreply.github.com"}]}]
           elif $e == "human" then [{oid:$w, authors:[{email:"someone@example.com"}]}]
           else [] end)''', "--arg", "w", W, "--arg", "g", G, "--arg", "go", GO, "--arg", "e", e)
        hygiene_at_head()

    # 9. Only fabro-co-authored commits and a merge of origin/main: not blocked by check 6.
    g("checkout", "-q", "-B", "runbr", G)
    cm_state("merge")
    ck("6 merge of main: eligible", "true", mg_run())
    # 10. A commit by a human address (no fabro co-author, one parent) still blocks.
    cm_state("human")
    ck("6 human commit: not eligible", "false", mg_run())
    ck("6 human commit: reason", "1", count("not authored by the automation", file=reason))
    # 11. A merge that brings in a branch not contained in origin/main still blocks.
    g("checkout", "-q", "-B", "runbr", GO)
    cm_state("other")
    ck("6 merge of other branch: blocks", "false", mg_run())
    ck("6 merge of other branch: reason", "1", count("not authored by the automation", file=reason))
    # 12. A commit gh reports but git cannot find fails closed.
    g("checkout", "-q", "-B", "runbr", G)
    cm_state("merge")
    fixture_jq('.commits += [{oid:"0000000000000000000000000000000000000001", authors:[{email:"x@y"}]}]')
    ck("6 unknown commit: fails closed", "false", mg_run())
    # 13. A gh error fails closed.
    cm_state("merge"); write(fixture, "not json\n")
    ck("6 gh error: fails closed", "false", mg_run())
    # 14. A commit git shows with no parents (a root, or a shallow boundary) blocks even
    # when it is in origin/main: `cut -s` keeps its own SHA from reading as its parents.
    cm_state("merge")
    root = g("rev-list", "--max-parents=0", "HEAD")
    fixture_jq('.commits += [{oid:$r, authors:[{email:"x@y"}]}]', "--arg", "r", root)
    ck("6 parentless commit: blocks", "false", mg_run())
    ck("6 parentless commit: reason", "1", count("not authored by the automation", file=reason))
    # 15. A missing base_ref fails closed: there is no origin/<base> to test parents against.
    cm_state("merge"); rm(T / "base_ref")
    ck("6 no base_ref: fails closed", "false", mg_run())
    ck("6 no base_ref: reason", "1", count("not authored by the automation", file=reason))


# ---------------------------------------------------------------------------
# review-merge ci_fix_gate — the counters run again before a CI fix is pushed
# (ADR 0013 D3), real git
# ---------------------------------------------------------------------------
def test_ci_fix_gate_hygiene(ck, tmp_path):
    T = tmp_path / "cfg"
    (T / "review").mkdir(parents=True)
    gate = stage(ck, "ci_fix_gate", T, graph=SHARED)
    hyg = T / "hygiene_node.sh"
    hyg.write_text(rebase(extract(SHARED, "hygiene"), T))
    mkreadok(T, "ok1", "ci_fix.md", "changed_files.txt", "diff.patch", "diffstat.txt", "commits.txt", "pr.json")
    up, wt = T / "up", T / "wt"

    def cf_repo():
        """An upstream whose `feat` branch is the PR head, cloned the way the sandbox is.
        `hygiene` runs once first, as it does in the graph, to write hygiene.sh."""
        rm(up, wt); (up / "tests").mkdir(parents=True)
        git("-C", up, "init", "-q", "-b", "main", ".")
        git("-C", up, "config", "user.email", "t@t"); git("-C", up, "config", "user.name", "t")
        git("-C", up, "config", "receive.denyCurrentBranch", "ignore")
        write(up / "tests/test_a.py", "def test_a():\n    assert 1 == 1 + 0\n")
        write(up / "a.py", "x\n")
        git("-C", up, "add", "-A"); git("-C", up, "commit", "-qm", "init")
        git("-C", up, "checkout", "-qb", "feat")
        with open(up / "a.py", "a") as f:
            f.write("y\n")
        git("-C", up, "commit", "-qam", "work")
        git("-C", up, "checkout", "-q", "main")
        git("clone", "-q", up, wt)
        git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
        git("-C", wt, "checkout", "-q", "-b", "feat", "origin/feat")
        write(T / "base_ref", "main\n"); write(T / "head_ref", "feat\n"); write(T / "pr_number", "5\n")
        write(T / "review/merge_base_files.txt", "a.py\ntests/test_a.py\n")
        write(T / "review/ci_fix_result.json", '{"outcome":"fixed","scope":"ci_only","summary":"s","_io":{"visit":"ok1","binary":"0.1.0","stage":"review_merge.ci_fix_t1"}}\n')
        rm(T / "merge_block_reason")
        sh(hyg, cwd=wt)

    cf_run = lambda: jq(".context_updates.ci_fix_ok", lastjson(sh(gate, cwd=wt).out))

    # 1. A CI fix that touches only code is pushed as before.
    cf_repo()
    with open(wt / "a.py", "a") as f:
        f.write("z\n")
    git("-C", wt, "commit", "-qam", "fix")
    ck("ci fix clean: pushed", "true", cf_run())

    # 2. A CI fix that skips the failing test is refused, not pushed, and says why.
    cf_repo()
    write(wt / "tests/test_a.py", "import pytest\n@pytest.mark.skip\ndef test_a():\n    assert 1 == 1 + 0\n")
    git("-C", wt, "commit", "-qam", "fix")
    ck("ci fix skip: refused", "false", cf_run())
    ck("ci fix skip: reason", "1", count("skips_added=1", file=T / "merge_block_reason"))
    ck("ci fix skip: not pushed", "2", git("-C", up, "rev-list", "--count", "feat"))


# ---------------------------------------------------------------------------
# review-merge refute_prep — the Refuter's inputs (ADR 0013 D4), real git
# ---------------------------------------------------------------------------
def test_refute_prep(ck, tmp_path):
    T = tmp_path / "rprep"
    (T / "bin").mkdir(parents=True); (T / "review").mkdir()
    prep = stage(ck, "refute_prep", T, graph=SHARED)
    # Issue 99 cannot be read; any other number returns a body.
    stub(T / "bin", "gh", '#!/bin/sh\n[ "$3" = 99 ] && exit 1\nprintf \'{"number":%s,"title":"T","body":"- [ ] it works"}\' "$3"\n')
    up, wt = T / "up", T / "wt"
    up.mkdir()
    git("-C", up, "init", "-q", "-b", "main", ".")
    git("-C", up, "config", "user.email", "t@t"); git("-C", up, "config", "user.name", "t")
    write(up / "a.txt", "a\n"); git("-C", up, "add", "-A"); git("-C", up, "commit", "-qm", "init")
    git("clone", "-q", up, wt)
    git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
    git("-C", wt, "checkout", "-qb", "fabro/run/01TEST")
    with open(wt / "a.txt", "a") as f:
        f.write("small\n")
    write(wt / "d e/f g.txt", "sp\n")
    write(wt / "big.txt", "".join(f"line number {i}\n" for i in range(40000)))
    git("-C", wt, "add", "-A"); git("-C", wt, "commit", "-qm", "change")
    write(T / "base_ref", "main\n")
    write(T / "linked_issues.json", '[{"number":5},{"number":99}]\n')
    write(T / "review/refute_verdict", "stale\n")
    run_prep = lambda: sh(prep, cwd=wt, path=f"{T / 'bin'}:{ORIG_PATH}")
    issues = T / "review/refute_issues.json"
    r = run_prep()

    # 1. Issue text is fetched, and an unreadable issue is recorded, not fatal.
    ck("refute_prep: exits 0", "0", r.rc)
    ck("refute_prep: both issues listed", "2", jq("length", file=issues, raw=False))
    ck("refute_prep: body fetched", "- [ ] it works", jq(".[0].body", file=issues))
    ck("refute_prep: unreadable recorded", "could not be read", jq(".[1].error", file=issues))

    # 2. A stale verdict from an earlier pass is cleared before the agent runs.
    ck("refute_prep: stale verdict cleared", "absent", present(T / "review/refute_verdict"))

    # 3. The cap: the largest file is listed, not diffed; small files and a path with
    #    spaces are diffed.
    ck("refute_prep: big file omitted", "1", count("^big.txt (40000 lines changed)", file=T / "review/refute_omitted.txt"))
    ck("refute_prep: small file diffed", "1", count("^+small", file=T / "review/refute_diff.patch"))
    ck("refute_prep: spaced path diffed", "1", count("^+sp$", file=T / "review/refute_diff.patch"))

    # 4. No linked issue is an empty list, which the prompt handles.
    write(T / "linked_issues.json", "[]\n")
    run_prep()
    ck("refute_prep: no issues is []", "[]", jq(".", file=issues, args=["-c"], raw=False))

    # 5. ADR 0013 D4 amendment (gate-relief G3/G4): the Post-deploy checks section is cut
    #    from the body the Refuter reads, and its checklist goes to post_deploy.md. Issue 6
    #    has the section; issue 7 has none and must come through unchanged.
    stub(T / "bin", "gh", """#!/bin/sh
case "$3" in
  99) exit 1 ;;
  6) printf '{"number":6,"title":"T","body":"criteria\\\\n\\\\n## Post-deploy checks\\\\n\\\\n- [ ] Sign in on staging\\\\n- [ ] Reload settings\\\\n\\\\n## Notes\\\\n\\\\nkept"}' ;;
  *) printf '{"number":%s,"title":"T","body":"- [ ] it works"}' "$3" ;;
esac
""")
    write(T / "linked_issues.json", '[{"number":6},{"number":7}]\n')
    run_prep()
    body0 = jq(".[0].body", file=issues)
    pd = T / "review/post_deploy.md"
    ck("refute_prep: section cut from the body", "0", count("Post-deploy", body0, pipe=True))
    ck("refute_prep: text above it kept", "1", count("^criteria$", body0, pipe=True))
    ck("refute_prep: text below it kept", "1", count("^kept$", body0, pipe=True))
    ck("refute_prep: checklist written", "1", count("^- [[] []] Sign in on staging$", file=pd))
    ck("refute_prep: two issues, a heading", "1", count("^#6$", file=pd))
    ck("refute_prep: no-section issue unchanged", '{"number":7,"title":"T","body":"- [ ] it works"}', jq(".[1]", file=issues, args=["-c"], raw=False))
    write(T / "linked_issues.json", '[{"number":6}]\n')
    run_prep()
    ck("refute_prep: one issue, no heading", "0", count("^#6$", file=pd))
    ck("refute_prep: one issue, checklist kept", "2", count("^- [[] []] ", file=pd))
    write(T / "linked_issues.json", '[{"number":7}]\n')
    run_prep()
    ck("refute_prep: no section, no checklist file", "absent", present(pd))
    ck("refute_prep: no section, body byte-identical", '{"number":7,"title":"T","body":"- [ ] it works"}', jq(".[0]", file=issues, args=["-c"], raw=False))

    # 6. ADR 0013 D5 amendment (gate-relief G8): the fix-turn counter is never reset in the
    #    run, so refute_prep must leave it alone.
    write(T / "refute_fix_attempts", "1\n")
    run_prep()
    ck("refute_prep: fix counter kept", "1", cat(T / "refute_fix_attempts"))
    rm(T / "refute_fix_attempts")

    # 7. #40: refute_prep writes the `validation` input (refute_validation.md). The worktree
    #    must be clean and the repo must have a .fabro/ci.sh to quote, so the fixture
    #    commits one first.
    write(wt / ".fabro/ci.sh", "#!/bin/sh\necho 'the fixture ci runs'\n")
    git("-C", wt, "add", ".fabro/ci.sh"); git("-C", wt, "commit", "-qm", "add ci.sh")
    ht = git("-C", wt, "rev-parse", "HEAD^{tree}")
    val = T / "review/refute_validation.md"
    write(val, "stale validation\n")
    write(T / "validated_tree", f"{ht} final\n")
    run_prep()
    ck("refute_prep: final tree -> Final validation passed", "1", count("Final validation passed on this exact tree", file=val))
    ck("refute_prep: quotes ci.sh", "1", count("the fixture ci runs", file=val))
    ck("refute_prep: stale file replaced, not appended", "0", count("^stale validation$", file=val))
    write(T / "validated_tree", f"{ht} merge\n")
    run_prep()
    ck("refute_prep: merge tree -> merge-validation passed", "1", count("The merge phase validate passed", file=val))
    rm(T / "validated_tree")
    run_prep()
    ck("refute_prep: no tree -> No validation recorded", "1", count("No validation is recorded", file=val))
    write(T / "validated_tree", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef final\n")
    run_prep()
    ck("refute_prep: different tree -> No validation recorded", "1", count("No validation is recorded", file=val))


# ---------------------------------------------------------------------------
# review-merge refute_gate — receipt check (ADR 0016 C7), one repair turn, computed
# verdict (ADR 0013 D5). Fixtures are generated by fabro-io `submit` with a fixed visit
# (ops/fabro-io/tests/fixtures/refute-{pass,fail}.json) and committed.
# ---------------------------------------------------------------------------
def test_refute_gate(ck, tmp_path):
    T = tmp_path / "rgate"
    (T / "review").mkdir(parents=True); (T / ".io").mkdir()
    gate = stage(ck, "refute_gate", T, graph=SHARED)
    # The fix route records the base HEAD of the checkout the gate runs in. The shell suite
    # ran it in this repository; a scratch repository makes the check mean the same thing.
    repo = T / "repo"
    repo.mkdir()
    git("init", "-q", ".", cwd=repo)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)

    def rg_stage():
        write(T / ".io/stage.json", f'{{"workflow":"backlog","node":"review_merge.refute","visit":"{FV}","started":"2026-09-27T00:00:00Z"}}')

    def place(src):
        shutil.copyfile(src, T / "review/refute.json")

    state = {}

    def rg_run():
        r = sh(gate, cwd=repo)
        state.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    verdict = lambda: (rg_run(), jq(".context_updates.refute_verdict", state["j"]))[1]
    rgc = lambda k: jq(f".context_updates.{k}", state["j"])

    # 1. A valid receipt, every criterion met, no defects: pass.
    rg_stage(); place(FIX_DIR / "refute-pass.json")
    write(T / "refute_attempts", "0\n")
    ck("refute_gate: valid receipt all met is pass", "pass", verdict())
    ck("refute_gate: verdict file", "pass", cat(T / "review/refute_verdict"))

    # 2. A valid receipt, one not_met: fail, and the reason names it and the defect.
    rg_stage(); place(FIX_DIR / "refute-fail.json")
    write(T / "refute_attempts", "0\n")
    ck("refute_gate: not_met is fail", "fail", verdict())
    ck("refute_gate: reason names the criterion", "1", count("rate limited", file=T / "review/refute_reason.txt"))
    ck("refute_gate: reason names the defect", "1", count("defect at auth.py:9", file=T / "review/refute_reason.txt"))

    # 3. Hand-written content (no _io) is refused with the submit-tool message: first a
    #    repair turn (exit 1, message mentions the submit tool), then `invalid`.
    rg_stage()
    handwritten = '{"summary":"s","criteria":[{"criterion":"a","status":"met","evidence":"x"}],"defects":[]}'
    write(T / "review/refute.json", handwritten)
    write(T / "refute_attempts", "0\n")
    rg_run()
    ck("refute_gate: no receipt gets a repair", "1", state["rc"])
    ck("refute_gate: repair says submit tool", "1", count("submit tool", state["out"]))
    rg_run()
    ck("refute_gate: second no-receipt invalid", "invalid", cat(T / "review/refute_verdict"))

    # 4. A valid receipt whose visit does not match stage.json (a stale file) is a repair.
    rg_stage(); place(FIX_DIR / "refute-pass.json")
    write(T / "refute_attempts", "0\n")
    write(T / ".io/stage.json", '{"workflow":"backlog","node":"review_merge.refute","visit":"ffffffffffffffffffffffffffffffff"}')
    rg_run()
    ck("refute_gate: stale receipt gets a repair", "1", state["rc"])

    # 5. stage.json missing (visit empty) is a repair, never pass.
    rm(T / ".io/stage.json"); place(FIX_DIR / "refute-pass.json")
    write(T / "refute_attempts", "0\n")
    rg_run()
    ck("refute_gate: missing stage is a repair", "1", state["rc"])
    ck("refute_gate: missing stage is never pass", "0", count('"refute_verdict":"pass"', state["out"]))

    # 6. The fix turn (ADR 0013 D5 amendments, gate-relief G6-G8, #36). The context keys
    #    decide the edge (refute_verdict=fail, refute_fixable=true, refute_fix_attempts=0),
    #    and the counter is written only when that route is taken. A not_met criterion or a
    #    defect makes a fail fixable; refute_not_met still says only whether a criterion was
    #    not_met (the scorecard).
    failf = FIX_DIR / "refute-fail.json"
    derive = lambda filt, src, name: write(T / name, jq(filt, file=src, raw=False) + "\n")
    derive('.criteria |= map(if .status == "not_met" then .status = "unverifiable" else . end) | .defects = []', failf, "unverifiable.json")
    derive('.criteria |= map(if .status == "not_met" then .status = "unverifiable" else . end)', failf, "unverifiable-defect.json")
    derive('.criteria |= map(.status = "met")', failf, "defects-only.json")
    findings = T / "review/refute_findings.json"

    rg_stage(); place(failf)
    write(T / "refute_attempts", "0\n"); write(T / "refute_fix_attempts", "0\n"); rm(findings)
    rg_run()
    ck("fix: one not_met takes the fix route", "true", rgc("refute_fixable"))
    ck("fix: one not_met says not_met", "true", rgc("refute_not_met"))
    ck("fix: first pass counter is 0 on the edge", "0", rgc("refute_fix_attempts"))
    ck("fix: the counter is written to 1", "1", cat(T / "refute_fix_attempts"))
    ck("fix: the base HEAD is recorded", head, cat(T / "refute_fix_base"))
    ck("fix: findings name the criterion", "1", jq(".not_met | length", file=findings, raw=False))
    ck("fix: findings carry the evidence", "1", count(".", jq(".not_met[0].evidence", file=findings), pipe=True))
    ck("fix: findings carry the defects", "1", jq(".defects | length", file=findings, raw=False))

    rg_stage(); place(T / "unverifiable.json")
    write(T / "refute_fix_attempts", "0\n"); rm(findings)
    rg_run()
    ck("fix: unverifiable only is not a fix route", "false", rgc("refute_fixable"))
    ck("fix: unverifiable only is not not_met", "false", rgc("refute_not_met"))
    ck("fix: unverifiable only leaves the counter", "0", cat(T / "refute_fix_attempts"))
    ck("fix: unverifiable only has no not_met", "0", jq(".not_met | length", file=findings, raw=False))

    # unverifiable plus a defect: the defect makes it fixable (#36)
    rg_stage(); place(T / "unverifiable-defect.json")
    write(T / "refute_fix_attempts", "0\n")
    rg_run()
    ck("fix: unverifiable plus a defect is a fix route", "true", rgc("refute_fixable"))
    ck("fix: unverifiable plus a defect writes the counter", "1", cat(T / "refute_fix_attempts"))

    rg_stage(); place(T / "defects-only.json")
    write(T / "refute_fix_attempts", "0\n"); rm(findings, T / "refute_fix_base")
    rg_run()
    ck("fix: defects only takes the fix route", "true", rgc("refute_fixable"))
    ck("fix: defects only is not not_met", "false", rgc("refute_not_met"))
    ck("fix: defects only is still fail", "fail", rgc("refute_verdict"))
    ck("fix: defects only carries attempts 0", "0", rgc("refute_fix_attempts"))
    ck("fix: defects only writes the counter", "1", cat(T / "refute_fix_attempts"))
    ck("fix: defects only records the base HEAD", head, cat(T / "refute_fix_base"))
    ck("fix: defects only findings, no not_met", "0", jq(".not_met | length", file=findings, raw=False))
    ck("fix: defects only findings carry them", "1", jq(".defects | length", file=findings, raw=False))

    # the second defects-only verdict: the counter is already 1, so the run goes to deliver
    rg_stage(); place(T / "defects-only.json")
    write(T / "refute_fix_attempts", "1\n")
    rg_run()
    ck("fix: second defects only carries attempts 1", "1", rgc("refute_fix_attempts"))
    ck("fix: second defects only does not rewrite it", "1", cat(T / "refute_fix_attempts"))

    # a defects: null file computes like an empty list: no defect, so not fixable on its own
    derive('.criteria |= map(.status = "met") | .defects = null', failf, "defects-null.json")
    rg_stage(); place(T / "defects-null.json")
    write(T / "refute_fix_attempts", "0\n")
    rg_run()
    ck("fix: null defects all met is pass", "pass", rgc("refute_verdict"))
    ck("fix: null defects is not a fix route", "false", rgc("refute_fixable"))

    # the second not_met: the counter is already 1, so the edge cannot match and the run
    # goes to deliver
    rg_stage(); place(failf)
    write(T / "refute_fix_attempts", "1\n")
    rg_run()
    ck("fix: second not_met carries attempts 1", "1", rgc("refute_fix_attempts"))
    ck("fix: second not_met still says not_met", "true", rgc("refute_not_met"))
    ck("fix: second not_met still fixable", "true", rgc("refute_fixable"))
    ck("fix: second not_met does not rewrite it", "1", cat(T / "refute_fix_attempts"))

    rg_stage(); place(FIX_DIR / "refute-pass.json")
    write(T / "refute_fix_attempts", "0\n"); rm(findings)
    rg_run()
    ck("fix: pass is not a fix route", "false", rgc("refute_fixable"))
    ck("fix: pass is not not_met", "false", rgc("refute_not_met"))
    ck("fix: pass writes no findings", "absent", present(findings))

    # an invalid verdict (twice) is never a fix route, and its context keys are written too
    rg_stage(); write(T / "review/refute.json", handwritten)
    write(T / "refute_attempts", "0\n"); write(T / "refute_fix_attempts", "0\n")
    sh(gate, cwd=repo)
    rg_run()
    ck("fix: invalid is not a fix route", "false", rgc("refute_fixable"))
    ck("fix: invalid is not not_met", "false", rgc("refute_not_met"))
    ck("fix: invalid keeps attempts 0", "0", rgc("refute_fix_attempts"))
    ck("fix: invalid unverifiable_only is false", "false", rgc("refute_unverifiable_only"))
    ck("fix: invalid ci_credited is 0", "0", rgc("refute_ci_credited"))
    rm(T / "refute_fix_attempts", T / "refute_fix_base")

    # 7. #40: the two new context keys. refute_unverifiable_only is true only for a fail
    #    with no not_met criterion and no defect; refute_ci_credited counts met criteria
    #    whose evidence cites ci.sh.
    derive('.criteria[0].evidence = ".fabro/ci.sh:13 runs tsc over every changed file" | .criteria[1].evidence = "src/auth.ts:40" | .defects = []',
           FIX_DIR / "refute-pass.json", "ci-credited.json")
    rg_stage(); place(T / "ci-credited.json")
    write(T / "refute_attempts", "0\n")
    rg_run()
    ck("refute_gate: pass with a ci.sh citation credits it", "1", rgc("refute_ci_credited"))
    ck("refute_gate: pass is not unverifiable-only", "false", rgc("refute_unverifiable_only"))

    rg_stage(); place(T / "unverifiable.json")
    write(T / "refute_attempts", "0\n")
    rg_run()
    ck("refute_gate: unverifiable only stays fail", "fail", rgc("refute_verdict"))
    ck("refute_gate: unverifiable only is unverifiable-only", "true", rgc("refute_unverifiable_only"))

    derive('.criteria[0].status = "unverifiable" | .defects = []', failf, "unverifiable-notmet.json")
    rg_stage(); place(T / "unverifiable-notmet.json")
    write(T / "refute_attempts", "0\n")
    rg_run()
    ck("refute_gate: unverifiable + not_met is not unverifiable-only", "false", rgc("refute_unverifiable_only"))

    rg_stage(); place(T / "unverifiable-defect.json")
    write(T / "refute_attempts", "0\n")
    rg_run()
    ck("refute_gate: unverifiable + defect is not unverifiable-only", "false", rgc("refute_unverifiable_only"))


# ---------------------------------------------------------------------------
# review-merge refute_fix_gate — the fix turn's receipt check (ADR 0013 D5 amendment, G10).
# A fix turn that did not submit writes no receipt: the gate fails, and the run goes to
# deliver. The gate writes no counter (G8) and prints no routing object.
# ---------------------------------------------------------------------------
def test_refute_fix_gate(ck, tmp_path):
    T = tmp_path / "rgate"
    (T / "review").mkdir(parents=True); (T / ".io").mkdir()
    gate = stage(ck, "refute_fix_gate", T, graph=SHARED)
    write(T / ".io/stage.json", f'{{"workflow":"backlog","node":"review_merge.refute_fix","visit":"{FV}","started":"2026-10-11T00:00:00Z"}}')
    # Every case runs inside a throwaway repo: a gate with no receipt runs git reset --hard
    # and git clean.
    rfg = T / "rfg"
    rg = lambda *a: git("-c", "user.email=t@t", "-c", "user.name=t", *a, cwd=rfg)
    git("init", "-q", rfg)
    rg("commit", "-q", "--allow-empty", "-m", "base")
    rm(T / "review/refute_fix.json", T / "refute_fix_attempts", T / "refute_fix_base")
    go = lambda: sh(gate, cwd=rfg)
    receipt = lambda visit: write(T / "review/refute_fix.json", f'{{"_io":{{"visit":"{visit}"}},"summary":"s","addressed":[],"not_addressed":[]}}')

    ck("refute_fix_gate: no file fails", "1", go().rc)
    write(T / "review/refute_fix.json", '{"summary":"s","addressed":[],"not_addressed":[]}')
    ck("refute_fix_gate: no receipt fails", "1", go().rc)
    receipt("ffffffffffffffffffffffffffffffff")
    ck("refute_fix_gate: stale receipt fails", "1", go().rc)
    receipt(FV)
    r = go()
    ck("refute_fix_gate: good receipt passes", "0", r.rc)
    ck("refute_fix_gate: writes no counter", "absent", present(T / "refute_fix_attempts"))
    ck("refute_fix_gate: prints no routing", "0", count("context_updates", r.out))

    # A turn with no receipt (failed, timed out, never submitted) is reset to
    # refute_fix_base before deliver pushes, so its partial edits never reach the PR.
    write(rfg / "a.txt", "kept\n"); rg("add", "a.txt"); rg("commit", "-q", "-m", "a")
    write(T / "refute_fix_base", rg("rev-parse", "HEAD") + "\n")
    write(rfg / "a.txt", "partial\n"); write(rfg / "new.txt", "stray\n"); rg("commit", "-q", "-am", "checkpoint")
    rm(T / "review/refute_fix.json")
    ck("refute_fix_gate: no receipt still fails", "1", go().rc)
    ck("refute_fix_gate: no receipt resets HEAD", cat(T / "refute_fix_base"), rg("rev-parse", "HEAD"))
    ck("refute_fix_gate: no receipt restores file", "kept", cat(rfg / "a.txt"))
    ck("refute_fix_gate: no receipt cleans strays", "absent", present(rfg / "new.txt"))
    write(rfg / "a.txt", "partial\n")
    receipt(FV)
    go()
    ck("refute_fix_gate: good receipt keeps edits", "partial", cat(rfg / "a.txt"))


# ---------------------------------------------------------------------------
# review-merge remerge_base and resolve_base_gate — a conflict with a base that moved after
# the review goes to resolve_base once per run (#37). Real git: a bare origin, a PR branch
# `feature` and a base `main` that moves under it. Every case runs in a throwaway clone,
# because both nodes rebase, reset and clean the checkout they run in.
# ---------------------------------------------------------------------------
def test_remerge_base_and_resolve_base_gate(ck, tmp_path):
    T = tmp_path / "rgate"
    (T / "review").mkdir(parents=True); (T / ".io").mkdir()
    remerge = stage(ck, "remerge_base", T, graph=SHARED)
    gate = stage(ck, "resolve_base_gate", T, graph=SHARED)
    w = T / "rb/w"
    rb = lambda *a, **kw: git("-c", "user.email=t@t", "-c", "user.name=t", *a, cwd=w, **kw)
    rb_in = ("base_ref", "resolve_base_pre", "resolve_conflicts.txt", "pr")

    def rb_repo(moved):
        """main changes `moved` after the branch point: a.txt conflicts, b.txt does not."""
        rm(T / "rb"); (T / "rb").mkdir()
        git("init", "-q", "--bare", "-b", "main", T / "rb/o.git")
        git("clone", "-q", T / "rb/o.git", w)
        git("-C", w, "config", "user.email", "t@t"); git("-C", w, "config", "user.name", "t")
        rb("checkout", "-q", "-b", "main")
        write(w / "a.txt", "one\n"); write(w / "b.txt", "one\n"); rb("add", "."); rb("commit", "-q", "-m", "base")
        rb("push", "-q", "origin", "main")
        rb("checkout", "-q", "-b", "feature")
        write(w / "a.txt", "feature\n"); rb("commit", "-q", "-am", "feature")
        rb("push", "-q", "origin", "feature")
        rb("checkout", "-q", "main")
        write(w / moved, "main\n"); rb("commit", "-q", "-am", "moved")
        rb("push", "-q", "origin", "main")
        rb("checkout", "-q", "feature")
        write(T / "base_ref", "main\n"); write(T / "head_ref", "feature\n")
        rm(T / "resolve_base_attempts", T / "resolve_base_pre", T / "strict_retry",
           T / "merge_block_reason", T / "needs_human_reason", T / "review/resolve_conflicts.txt",
           T / "review/resolve_base.md")

    state = {}

    def go(script):
        r = sh(script, cwd=w)
        state.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    rbc = lambda k: jq(f".context_updates.{k}", state["j"])

    # 1. A conflicting rebase, first time: the resolve route, with the pre-merge HEAD and the files.
    rb_repo("a.txt")
    h0 = rb("rev-parse", "HEAD")
    go(remerge)
    ck("remerge_base: conflict takes the resolve route", "true", rbc("remerge_conflict"))
    ck("remerge_base: conflict is not remerge_ok", "false", rbc("remerge_ok"))
    ck("remerge_base: conflict writes the counter", "1", cat(T / "resolve_base_attempts"))
    ck("remerge_base: conflict records the pre HEAD", h0, cat(T / "resolve_base_pre"))
    ck("remerge_base: conflict lists the file", "a.txt", cat(T / "review/resolve_conflicts.txt"))
    ck("remerge_base: conflict leaves no rebase", "absent",
       "present" if (w / ".git/rebase-merge").is_dir() or (w / ".git/rebase-apply").is_dir() else "absent")
    ck("remerge_base: conflict leaves HEAD alone", h0, rb("rev-parse", "HEAD"))
    ck("remerge_base: conflict sets strict_retry", "1", cat(T / "strict_retry"))
    ck("remerge_base: conflict writes no block reason", "absent", present(T / "merge_block_reason"))

    # 2. A second conflict in the same run: the counter is 1, so a human, with both reason files.
    go(remerge)
    ck("remerge_base: second conflict is no resolve route", "false", rbc("remerge_conflict"))
    ck("remerge_base: second conflict is not ok", "false", rbc("remerge_ok"))
    ck("remerge_base: second conflict block reason", "1", count("A conflict appeared after the review", file=T / "merge_block_reason"))
    ck("remerge_base: second conflict needs-human reason", "1", count("A conflict appeared after the review", file=T / "needs_human_reason"))
    ck("remerge_base: second conflict keeps the counter", "1", cat(T / "resolve_base_attempts"))

    # 3. A base that moved without conflicting: rebased, pushed, and no resolve route.
    rb_repo("b.txt")
    go(remerge)
    ck("remerge_base: clean rebase is ok", "true", rbc("remerge_ok"))
    ck("remerge_base: clean rebase is no conflict", "false", rbc("remerge_conflict"))
    ck("remerge_base: clean rebase leaves the counter", "absent", present(T / "resolve_base_attempts"))
    ck("remerge_base: clean rebase pushed the branch", rb("rev-parse", "HEAD"), git("-C", T / "rb/o.git", "rev-parse", "feature"))

    # resolve_base_gate. Each case starts from case 1's state: a conflicting base, aborted,
    # counter 1.
    def conflicted():
        rb_repo("a.txt"); go(remerge)

    def resolve():
        """The agent's merge, resolved to keep both lines, then fabro's checkpoint commit."""
        rb("fetch", "-q", "origin")
        rb("merge", "-q", "origin/main", check=False)
        write(w / "a.txt", "feature\nmain\n"); rb("add", "a.txt")
        rb("merge", "--continue", check=False, env={"GIT_EDITOR": "true"})
        rb("commit", "-q", "--allow-empty", "-m", "checkpoint")

    # 4. A good merge with a receipt: resolve_ok, the report note, and no block reason.
    conflicted(); resolve(); h1 = rb("rev-parse", "HEAD")
    mkreadok(T, FV, *rb_in)
    go(gate)
    ck("resolve_base_gate: good merge passes", "true", rbc("resolve_ok"))
    ck("resolve_base_gate: good merge keeps HEAD", h1, rb("rev-parse", "HEAD"))
    ck("resolve_base_gate: good merge writes the note", "1", count("conflicted with this branch (in: a.txt)", file=T / "review/resolve_base.md"))
    ck("resolve_base_gate: good merge writes no block", "absent", present(T / "merge_block_reason"))

    # 5. The same merge without a receipt: refused, reset to the pre-merge HEAD, both reasons.
    conflicted(); p = cat(T / "resolve_base_pre"); resolve()
    write(T / ".io/served.json", '{"visit":"ffffffffffffffffffffffffffffffff","inputs":{}}')
    go(gate)
    ck("resolve_base_gate: no receipt is refused", "false", rbc("resolve_ok"))
    ck("resolve_base_gate: no receipt resets HEAD", p, rb("rev-parse", "HEAD"))
    ck("resolve_base_gate: no receipt block reason", "1", count("no Input receipt", file=T / "merge_block_reason"))
    ck("resolve_base_gate: no receipt needs-human", "1", count("no Input receipt", file=T / "needs_human_reason"))
    ck("resolve_base_gate: no receipt writes no note", "absent", present(T / "review/resolve_base.md"))

    # 6. The agent aborted (only the checkpoint commit): origin/main is not an ancestor.
    conflicted(); p = cat(T / "resolve_base_pre"); rb("commit", "-q", "--allow-empty", "-m", "checkpoint")
    mkreadok(T, FV, *rb_in)
    go(gate)
    ck("resolve_base_gate: an abort is refused", "false", rbc("resolve_ok"))
    ck("resolve_base_gate: an abort names the cause", "1", count("still does not contain origin/main", file=T / "merge_block_reason"))
    ck("resolve_base_gate: an abort resets HEAD", p, rb("rev-parse", "HEAD"))

    # 7. Markers committed by the checkpoint (git add -A marks a conflicted file resolved).
    conflicted(); p = cat(T / "resolve_base_pre")
    rb("fetch", "-q", "origin"); rb("merge", "-q", "origin/main", check=False)
    rb("add", "-A"); rb("commit", "-q", "-m", "checkpoint")
    mkreadok(T, FV, *rb_in)
    go(gate)
    ck("resolve_base_gate: markers are refused", "false", rbc("resolve_ok"))
    ck("resolve_base_gate: markers name the file", "1", count("conflict markers were left in a.txt", file=T / "merge_block_reason"))
    ck("resolve_base_gate: markers reset HEAD", p, rb("rev-parse", "HEAD"))
    ck("resolve_base_gate: markers restore the file", "feature", cat(w / "a.txt"))

    # 8. A merge left in progress: refused, aborted and reset.
    conflicted(); p = cat(T / "resolve_base_pre")
    rb("fetch", "-q", "origin"); rb("merge", "-q", "origin/main", check=False)
    mkreadok(T, FV, *rb_in)
    go(gate)
    ck("resolve_base_gate: a merge in progress is refused", "false", rbc("resolve_ok"))
    ck("resolve_base_gate: a merge in progress is aborted", "absent", present(w / ".git/MERGE_HEAD"))
    ck("resolve_base_gate: a merge in progress resets HEAD", p, rb("rev-parse", "HEAD"))


# ---------------------------------------------------------------------------
# review-merge refute guard — Sealed paths block the refuter's reads (ADR 0016 D8). Needs
# fabro-io on PATH; skipped otherwise (present in profile images).
# ---------------------------------------------------------------------------
GUARD_MANIFEST = ('{"version":1,"min_binary":"0.0.0","stages":{"review_merge.refute":{"inputs":[],"sealed":'
                  '["/tmp/fabro/review/standards.json","/tmp/fabro/review/spec.json","/tmp/fabro/review/fix_result.json",'
                  '"/tmp/fabro/review/ci_fix_result.json","/tmp/fabro/feedback/**"]}}}')


def generated_manifest() -> str:
    """backlog's generated FABRO_IO_MANIFEST: from the first `{` to the last `}` of its line."""
    for line in (WF / "backlog/workflow.toml").read_text().split("\n"):
        if line.startswith("FABRO_IO_MANIFEST"):
            return line[line.index("{"):line.rindex("}") + 1]
    return ""


def test_refute_guard(ck, tmp_path):
    if not shutil.which("fabro-io", path=ORIG_PATH):
        pytest.skip("no fabro-io on PATH; present in profile images")
    T = tmp_path / "rguard"
    (T / ".io").mkdir(parents=True)
    base = {"FABRO_IO_ROOT": T, "FABRO_IO_MANIFEST": GUARD_MANIFEST}

    def stage_as(node):
        write(T / ".io/stage.json", f'{{"workflow":"backlog","node":"{node}","visit":"{FV}"}}')

    def guard(p, manifest=None):
        e = {**base, "FABRO_HOOK_CONTEXT": f'{{"tool_input":{{"path":"{p}"}}}}'}
        if manifest is not None:
            e["FABRO_IO_MANIFEST"] = manifest
        return run(["fabro-io", "guard"], env=e)

    # refute node: a read of a Sealed path is blocked (exit 2) with a reason.
    stage_as("review_merge.refute")
    r = guard("/tmp/fabro/review/standards.json")
    ck("refute guard: sealed read blocked", "2", r.rc)
    ck("refute guard: block reason", "1", count("is sealed for this stage", r.out))
    # a sealed glob also blocks.
    ck("refute guard: sealed glob blocked", "2", guard("/tmp/fabro/feedback/rework.md").rc)
    # a node with no sealed entry (spec) proceeds.
    stage_as("review_merge.spec")
    ck("refute guard: unsealed node proceeds", "0", guard("/tmp/fabro/review/standards.json").rc)
    # refute_fix (ADR 0013 D5 amendment, gate-relief G7): the fixer is denied the verdict
    # files and the hygiene counters, and may read its own findings. The Refuter is denied
    # the fixer's report and the findings it wrote itself, so it judges again from scratch.
    man = generated_manifest()

    def guard_as(node, p):
        stage_as(node)
        return guard(p, man).rc

    ck("refute_fix guard: verdict file blocked", "2", guard_as("review_merge.refute_fix", "/tmp/fabro/review/refute.json"))
    ck("refute_fix guard: hygiene counters blocked", "2", guard_as("review_merge.refute_fix", "/tmp/fabro/review/hygiene.json"))
    ck("refute_fix guard: its findings readable", "0", guard_as("review_merge.refute_fix", "/tmp/fabro/review/refute_findings.json"))
    ck("refute guard: the fixer report blocked", "2", guard_as("review_merge.refute", "/tmp/fabro/review/refute_fix.json"))
    ck("refute guard: its own findings blocked", "2", guard_as("review_merge.refute", "/tmp/fabro/review/refute_findings.json"))
