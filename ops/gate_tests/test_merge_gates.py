"""The review-merge phase's gates: merge_gate (the hold label, diff hygiene, the Refuter
and the commit-author check), the hygiene counters and the rulings that clear them,
ci_fix_gate, refute_prep, refute_gate, refute_fix_gate, remerge_base / resolve_base_gate
and the refute guard."""

import json
import shutil

import pytest

from harness import (ORIG_PATH, REPO_ROOT, SHARED, WF, cat, count, git, git_rc, jq, jq_n,
                     lastjson, mkreadok, present, rebase, extract, rm, routing_check, run,
                     run_files, sh, stage, stub, write)

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

    def rulings(self, *rs):
        """review/hygiene_rulings.json, the file refute_gate writes, written by hand."""
        write(self.T / "review/hygiene_rulings.json", json.dumps(list(rs)) + "\n")


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

    # 9a. #92: a method call `.expect(N)` is not a tautology. wiremock's mock builder sets its
    #     expected call count with it: flakeradar#212 was held on 26, each `.expect(1)` or
    #     `.expect(0)` on its own line after `.respond_with(...)`. The same call on the
    #     chain's own line, and in a #[test] module beside the code, is not one either.
    hy_repo()
    lines_to(wt / "tests/summary_action.rs",
             "async fn comments_are_posted_once() {",
             "    let server = MockServer::start().await;",
             '    Mock::given(method("GET"))',
             '        .and(path("/repos/acme/app/issues/12/comments"))',
             "        .respond_with(ResponseTemplate::new(200).set_body_json(json!([])))",
             "        .expect(1)",
             "        .mount(&server)",
             "        .await;",
             '    Mock::given(method("POST")).and(path("/repos/acme/app/issues/12/comments")).respond_with(ResponseTemplate::new(201)).expect(2).mount(&server).await;',
             "}")
    lines_to(wt / "src/lib.rs", "fn f() -> u8 { 1 }", "", "#[cfg(test)]", "mod tests {",
             "    use super::*;", "", "    #[tokio::test]", "    async fn posted_once() {",
             "        let server = MockServer::start().await;",
             '        Mock::given(method("POST"))',
             "            .respond_with(ResponseTemplate::new(201))",
             "            .expect(3)",
             "            .mount(&server)",
             "            .await;", "    }", "}")
    commit(); hy_run()
    ck("#92 wiremock .expect(N): not a tautology", "0", hy(".tamper.tautologies_added"))
    ck("#92 wiremock .expect(N): nothing blocks", "0", hy(".blocking | length"))

    # 9b. #92: supertest's `.expect(200)` is the same kind of call, on the response: on the
    #     request's own line, or at the start of a line of its own.
    hy_repo()
    lines_to(wt / "web/api.test.ts",
             'it("GET /x returns 200", async () => {',
             '  await request(app).get("/x").expect(200);',
             "});",
             'it("POST /y returns 201", async () => {',
             "  await request(app)",
             '    .post("/y")',
             "    .expect(201);",
             "});")
    commit(); hy_run()
    ck("#92 supertest .expect(200): not a tautology", "0", hy(".tamper.tautologies_added"))
    ck("#92 supertest .expect(200): nothing blocks", "0", hy(".blocking | length"))

    # 9c. #92: the fix hides nothing. `expect(true)`, a bare `expect(1)` at the start of a
    #     line and `assert!(true)` in a Rust file each still count, and still block.
    hy_repo()
    lines_to(wt / "web/c.test.ts", "expect(true)", "expect(1)")
    lines_to(wt / "tests/t.rs", "#[test]", "fn t() {", "    assert!(true)", "}")
    commit(); hy_run()
    ck("#92 expect(true), expect(1), assert!(true): three", "3", hy(".tamper.tautologies_added"))
    ck("#92 the three still block", "tautologies_added", hy('.blocking | join(",")'))

    # 9d. #92: ...and a bare `expect(1)` still counts after a space or a bracket: `expect` must
    #     begin the line or follow a character that is not a dot and not a word character.
    hy_repo()
    lines_to(wt / "web/d.test.ts", 'it("a", () => { expect(1) })', 'it("b", () => (expect(1)))', "const seen = [expect(1)]")
    commit(); hy_run()
    ck("#92 expect(1) after a space or a bracket: three", "3", hy(".tamper.tautologies_added"))

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
    """Cases 18-30: what does and does not count as a removed assertion."""
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

    # 27. #62: a Rust lint attribute and a comment are not assertions.
    hy_repo()
    hy_base("src/lib.rs", "#![expect(clippy::too_many_lines)]", "// the assertions below cover f", "#[expect(dead_code)]", "fn f() -> u8 { 1 }")
    lines_to(wt / "src/lib.rs", "fn f() -> u8 { 1 }"); commit(); hy_run()
    ck("rust #[expect( and a comment removed: not asserts", "0", hy(".tamper.asserts_removed"))

    # 28. #62: the word in prose is not an assertion; a real one with it in a comment still is.
    hy_repo()
    hy_base("tests/test_p.py", "def test_p():", "    x = 1  # these assertions hold for x", "    assert x == 1  # assertions", "    y = 2")
    lines_to(wt / "tests/test_p.py", "def test_p():", "    y = 2"); commit(); hy_run()
    ck("prose assertions: only the real assert counts", "1", hy(".tamper.asserts_removed"))

    # 29. #62: expected values that shift across hunks (0->1, 1->2, 2->3) are edits in place,
    #     not one removal plus moves that steal the next hunk's addition.
    hy_repo()
    pa, pb = ("    pad_a = 1", "    pad_a2 = 1", "    pad_a3 = 1"), ("    pad_b = 1", "    pad_b2 = 1", "    pad_b3 = 1")
    hy_base("tests/test_s.py", "def test_s():", "    assert_near(n, 0)", *pa, "    assert_near(n, 1)", *pb, "    assert_near(n, 2)")
    lines_to(wt / "tests/test_s.py", "def test_s():", "    assert_near(n, 1)", *pa, "    assert_near(n, 2)", *pb, "    assert_near(n, 3)"); commit(); hy_run()
    ck("shifted values across hunks: not removed", "0", hy(".tamper.asserts_removed"))

    # 30. #62: two call assertions replaced by one stronger call matcher on the same subject.
    hy_repo()
    hy_base("web/b.test.ts", "function t() {", "  expect(onError).toHaveBeenCalled()", "  expect(onError).toHaveBeenCalledTimes(1)", "}")
    lines_to(wt / "web/b.test.ts", "function t() {", "  expect(onError).toHaveBeenCalledWith('boom')", "}"); commit(); hy_run()
    ck("stronger call matcher: not removed", "0", hy(".tamper.asserts_removed"))


# ---------------------------------------------------------------------------
# review-merge hygiene -- the hits a ruling can clear (#92), real git
#
# hygiene.json lists every flagged line (`hits`), and a ruling clears a hit by the hit's
# line texts. No gate writes review/hygiene_rulings.json yet, so each case writes it by
# hand in the shape refute_gate will write (decisions.md, the rulings file).
# ---------------------------------------------------------------------------
DEFAULT_TP = ["(^|/)(tests?|__tests__|spec)/", "_test[.][a-z]+$", "[.](test|spec)[.][a-z]+$", "(^|/)test_[^/]*[.]py$"]


def rule(counter, file, texts, ruling="legitimate", n=None):
    """One entry of hygiene_rulings.json: its nine keys, as refute_gate writes it. `n` is the
    ruled hit's count, one per text unless a case says otherwise."""
    return {"counter": counter, "file": file, "ruling": ruling, "basis": "requirement",
            "evidence": "delete the test for the old parser", "scenario": "",
            "reason": "quote found in issue #12", "count": len(texts) if n is None else n,
            "texts": list(texts)}


def cjson(obj):
    """Compact JSON, byte for byte what jq's `tojson` prints for the same value."""
    return json.dumps(obj, separators=(",", ":"))


def test_hygiene_hits(ck, tmp_path):
    """#92 cases H1-H5: `hits` is one entry per counter and file with every flagged line
    whole, `samples` are as before, and `test_paths` is the list the counters used."""
    H = Hygiene(ck, tmp_path)
    wt = H.wt
    lines_to, hy_repo, hy_base, commit, hy_run, hy = (H.lines_to, H.repo, H.base, H.commit, H.run, H.get)
    A, B = "assert add(0, 0) == 0", "assert add(1, 2) == 3"

    # H1. One hit per counter and file, in the order the counters emitted them. That order
    #     is not a sorted one (asserts_removed is emitted after the whole diff was read),
     #     which is why the list is built in that order and never with group_by.
    hy_repo()
    hy_base("tests/test_c.py", "def test_u():", "    assert add(2, 2) == 4", "    assert add(3, 3) == 6")
    hy_base("tests/test_zeta.py", "def test_z():", "    pass")
    lines_to(wt / "tests/test_a.py", "import pytest", "@pytest.mark.skip", "def test_s():", "    assert add(1, 1) == 2")
    lines_to(wt / "tests/test_b.py", "def test_t():", "    assert True")
    lines_to(wt / "tests/test_c.py", "def test_u():", "    assert add(3, 3) == 6")
    git("-C", wt, "rm", "-q", "tests/test_zeta.py"); commit(); hy_run()
    hits = [
        {"counter": "skips_added", "file": "tests/test_a.py", "count": 1, "texts": ["@pytest.mark.skip"]},
        {"counter": "tautologies_added", "file": "tests/test_b.py", "count": 1, "texts": ["assert True"]},
        {"counter": "tests_deleted", "file": "tests/test_zeta.py", "count": 1, "texts": ["file deleted"]},
        {"counter": "asserts_removed", "file": "tests/test_c.py", "count": 1, "texts": ["assert add(2, 2) == 4"]},
    ]
    ck("hits: one per counter and file, in diff order", cjson(hits), hy(".hits | tojson"))
    ck("hits: the order is not sorted", "skips_added,tautologies_added,tests_deleted,asserts_removed", hy('.hits | map(.counter) | join(",")'))
    ck("hits: a deleted file, one line, as samples spell it", cjson(hits[2]), hy(".hits[2] | tojson"))
    ck("hits: a removed assertion and its trimmed line", cjson(hits[3]), hy(".hits[3] | tojson"))
    ck("samples: the same four lines", "4", hy(".samples | length"))
    ck("samples: untrimmed", "    assert add(2, 2) == 4", hy(".samples[3].text"))

    # H2. A flagged line of 200 characters is whole in `hits`, and cut at 160 in `samples`.
    hy_repo()
    long = ("    assert add(0, 0) == 0  # " + "x" * 200)[:200]
    hy_base("tests/test_long.py", "def test_l():", long, "    y = 1")
    lines_to(wt / "tests/test_long.py", "def test_l():", "    y = 1"); commit(); hy_run()
    ck("200 characters: whole in hits", long.strip(), hy(".hits[0].texts[0]"))
    ck("200 characters: the hit text's length", str(len(long.strip())), hy(".hits[0].texts[0] | length"))
    ck("200 characters: cut at 160 in samples", "160", hy(".samples[0].text | length"))
    ck("200 characters: the sample is the raw prefix", long[:160], hy(".samples[0].text"))

    # H3. A flagged line can hold tabs. A hit's text loses them at its ends and keeps the
    #     one inside; the tab splits nothing (one hit, one line). A sample is the raw line.
    hy_repo()
    tabbed = "\tassert add(0, 0) == 0\t# zero\tcase   "
    hy_base("tests/test_tabs.py", "def test_t():", tabbed, "    y = 1")
    lines_to(wt / "tests/test_tabs.py", "def test_t():", "    y = 1"); commit(); hy_run()
    ck("tabs: trimmed at the ends, the inner one kept", "assert add(0, 0) == 0\t# zero\tcase", hy(".hits[0].texts[0]"))
    ck("tabs: one hit of one line", cjson([
        {"counter": "asserts_removed", "file": "tests/test_tabs.py", "count": 1,
         "texts": ["assert add(0, 0) == 0\t# zero\tcase"]}]), hy(".hits | tojson"))
    ck("tabs: the sample is the raw line", tabbed, hy(".samples[0].text"))

    # H4. Two files in one counter are two hits, each with its own count.
    hy_repo()
    hy_base("tests/test_other.py", "def test_o():", "    assert add(2, 2) == 4")
    lines_to(wt / "tests/test_math.py", "def test_a():")
    lines_to(wt / "tests/test_other.py", "def test_o():"); commit(); hy_run()
    ck("two files in one counter: two hits, texts in diff order", cjson([
        {"counter": "asserts_removed", "file": "tests/test_math.py", "count": 2, "texts": [B, A]},
        {"counter": "asserts_removed", "file": "tests/test_other.py", "count": 1,
         "texts": ["assert add(2, 2) == 4"]}]), hy(".hits | tojson"))

    # H5. `test_paths` is the list the counters used: the defaults, or the base branch's own.
    hy_repo()
    lines_to(wt / "checks/a.ts", 'it.skip("x", () => {})'); commit(); hy_run()
    ck("test_paths: the defaults", cjson(DEFAULT_TP), hy(".test_paths | tojson"))
    ck("test_paths: checks/ is no test path by default", "0", hy(".tamper.skips_added"))
    hy_repo('{"test_paths":["(^|/)checks/"]}')
    lines_to(wt / "checks/a.ts", 'it.skip("x", () => {})'); commit(); hy_run()
    ck("test_paths: the base branch's list", '["(^|/)checks/"]', hy(".test_paths | tojson"))
    ck("test_paths: it is the list the counters used", "1", hy(".tamper.skips_added"))

    # H6. The lines of a hit name what was removed, not the first lines of the hunk. An
    #     assertion updated in place (its subject is covered by an added equality) is not a
    #     removal, so the deleted neighbour below it is the line that is listed.
    total, status, owner = "assert total(o) == 10", "assert status(o) == 401", 'assert owner(o) == "ada"'
    def edited(*head):
        hy_repo()
        hy_base("tests/test_o.py", "def test_o():", *("    " + t for t in (total, status, owner)), "    done()")
        lines_to(wt / "tests/test_o.py", "def test_o():", *head, "    done()"); commit(); hy_run()
    edited("    assert total(o) == 12", "    " + owner)
    ck("updated in place beside a deletion: the deleted line is listed", cjson([
        {"counter": "asserts_removed", "file": "tests/test_o.py", "count": 1, "texts": [status]}]), hy(".hits | tojson"))
    ck("updated in place beside a deletion: the sample is that line", "    " + status, hy(".samples[0].text"))

    # H7. When added assertions net against removed ones and nothing says which is which, the
    #     count stays the net and every removal they could not account for is listed.
    edited("    assert ready(o)")
    ck("netted hunk: the count is the net", "2", hy(".tamper.asserts_removed"))
    ck("netted hunk: every removed line is listed", cjson([
        {"counter": "asserts_removed", "file": "tests/test_o.py", "count": 2, "texts": [total, status, owner]}]), hy(".hits | tojson"))
    ck("netted hunk: one sample per counted line", "2", hy(".samples | length"))

    # H8. A hunk that nets to zero is no hit, and lists nothing.
    edited("    assert a(o)", "    assert b(o)", "    assert c(o)")
    ck("nets to zero: no hit", "[]", hy(".hits | tojson"))

    # H9. The count is what it was: an added equality that covers one removal and is the
    #     destination of another still leaves one assertion lost, and the hit then lists the
    #     hunk's covered lines, because which of them was lost is not known.
    hy_repo()
    hy_base("tests/test_two.py", "def test_t():", "    assert f(x) == 1", "    assert f(x) == 2", "    done()")
    lines_to(wt / "tests/test_two.py", "def test_t():", "        assert f(x) == 2", "    done()"); commit(); hy_run()
    ck("covered and moved by one addition: the covered line is listed", cjson([
        {"counter": "asserts_removed", "file": "tests/test_two.py", "count": 1, "texts": ["assert f(x) == 1"]}]), hy(".hits | tojson"))


def test_hygiene_rulings(ck, tmp_path):
    """#92 cases R1-R11: what an entry of review/hygiene_rulings.json clears, real git. A
    hit is cleared only by exactly one legitimate ruling naming it, whose texts hold every
    line of the hit."""
    H = Hygiene(ck, tmp_path)
    T, wt = H.T, H.wt
    lines_to, hy_repo, hy_base, commit, hy_run, hy, rules = (H.lines_to, H.repo, H.base, H.commit, H.run, H.get, H.rulings)
    A, B = "assert add(0, 0) == 0", "assert add(1, 2) == 3"
    reason = T / "review/hygiene_reason.txt"

    # R1. No rulings file: `cleared` is empty and everything is as today.
    hy_repo(); git("-C", wt, "rm", "-q", "tests/test_math.py"); commit(); hy_run()
    ck("no rulings file: cleared is empty", "[]", hy(".cleared | tojson"))
    ck("no rulings file: tests_deleted blocks", "tests_deleted", hy('.blocking | join(",")'))

    # R2. One legitimate ruling naming the only hit, with its line: the hit is cleared,
    #     nothing blocks and no reason file is written. The raw count and the hit stay.
    hy_repo(); git("-C", wt, "rm", "-q", "tests/test_math.py"); commit()
    rules(rule("tests_deleted", "tests/test_math.py", ["file deleted"])); hy_run()
    ck("legitimate deletion: cleared holds the hit", cjson([
        {"counter": "tests_deleted", "file": "tests/test_math.py", "count": 1}]), hy(".cleared | tojson"))
    ck("legitimate deletion: nothing blocks", "0", hy(".blocking | length"))
    ck("legitimate deletion: no reason file", "absent", present(reason))
    ck("legitimate deletion: the raw count is kept", "1", hy(".tamper.tests_deleted"))
    ck("legitimate deletion: the hit is still listed", "1", hy(".hits | length"))
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()
    rules(rule("asserts_removed", "tests/test_math.py", [A])); hy_run()
    ck("legitimate removal: cleared holds the hit", cjson([
        {"counter": "asserts_removed", "file": "tests/test_math.py", "count": 1}]), hy(".cleared | tojson"))
    ck("legitimate removal: nothing blocks", "0", hy(".blocking | length"))

    # R3. A ruling covers lines, not a count: the hit's texts must be a sub-multiset of the
    #     ruling's texts.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()   # the hit is [A]
    rules(rule("asserts_removed", "tests/test_math.py", [A, B])); hy_run()
    ck("more ruled texts than the hit: cleared", "1", hy(".cleared | length"))
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():"); commit(); hy_run()   # the hit is [B, A]
    ck("the hit's texts are in diff order", cjson([B, A]), hy(".hits[0].texts | tojson"))
    rules(rule("asserts_removed", "tests/test_math.py", [A])); hy_run()
    ck("one of the hit's lines missing from the ruling: not cleared", "0", hy(".cleared | length"))
    ck("one line missing: blocks", "asserts_removed", hy('.blocking | join(",")'))
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()   # the hit is [A]
    rules(rule("asserts_removed", "tests/test_math.py", [B])); hy_run()
    ck("the same count with a different line: not cleared", "0", hy(".cleared | length"))
    hy_repo()
    hy_base("tests/test_dup.py", "def test_d():", "    " + A, "    " + A)
    lines_to(wt / "tests/test_dup.py", "def test_d():"); commit()   # the hit is [A, A]
    rules(rule("asserts_removed", "tests/test_dup.py", [A])); hy_run()
    ck("one line twice in the hit, once in the ruling: not cleared", "0", hy(".cleared | length"))
    rules(rule("asserts_removed", "tests/test_dup.py", [A, A])); hy_run()
    ck("one line twice in the hit, twice in the ruling: cleared", "1", hy(".cleared | length"))

    # R4. Two files in one counter, one ruled: the counter stays in blocking with the
    #     UNRULED hit's count, the reason samples that hit only, and the clause says a
    #     ruling cleared something.
    hy_repo()
    hy_base("tests/test_other.py", "def test_o():", "    assert add(2, 2) == 4")
    lines_to(wt / "tests/test_math.py", "def test_a():"); lines_to(wt / "tests/test_other.py", "def test_o():"); commit()
    rules(rule("asserts_removed", "tests/test_math.py", [B, A])); hy_run()
    ck("two files, one ruled: the counter stays", "asserts_removed", hy('.blocking | join(",")'))
    ck("two files, one ruled: the raw count is three", "3", hy(".tamper.asserts_removed"))
    ck("two files, one ruled: the reason counts the unruled hit", "1", count("asserts_removed=1", file=reason))
    ck("two files, one ruled: not the raw count", "0", count("asserts_removed=3", file=reason))
    ck("two files, one ruled: the samples are the unruled hit's", "1", count("tests/test_other.py:2 asserts_removed", file=reason))
    ck("two files, one ruled: the ruled hit is not sampled", "0", count("test_math.py", file=reason))
    ck("two files, one ruled: the cleared clause", "1", count("(1 hit(s) cleared by refuter rulings)", file=reason))

    # R5. tampering and unsure never clear anything.
    hy_repo()
    hy_base("tests/test_zeta.py", "def test_z():", "    pass")
    git("-C", wt, "rm", "-q", "tests/test_zeta.py")
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()
    rules(rule("tests_deleted", "tests/test_zeta.py", ["file deleted"], "tampering"),
          rule("asserts_removed", "tests/test_math.py", [A], "unsure")); hy_run()
    ck("tampering and unsure: nothing cleared", "0", hy(".cleared | length"))
    ck("tampering and unsure: both block", "tests_deleted,asserts_removed", hy('.blocking | join(",")'))
    ck("tampering and unsure: no cleared clause", "0", count("cleared by refuter rulings", file=reason))

    # R6. A legitimate ruling on skips_added or tautologies_added clears nothing: rulings
    #     cover tests_deleted and asserts_removed only.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "import pytest", "@pytest.mark.skip", "def test_a():", "    " + B, "    " + A)
    lines_to(wt / "tests/test_taut.py", "def test_t():", "    assert True"); commit()
    rules(rule("skips_added", "tests/test_math.py", ["@pytest.mark.skip"]),
          rule("tautologies_added", "tests/test_taut.py", ["assert True"])); hy_run()
    ck("legitimate on skips and tautologies: nothing cleared", "0", hy(".cleared | length"))
    ck("legitimate on skips and tautologies: both block", "skips_added,tautologies_added", hy('.blocking | join(",")'))
    ck("legitimate on skips and tautologies: no cleared clause", "0", count("cleared by refuter rulings", file=reason))

    # R7. A ruling that names no hit changes nothing: another file, or the hit's path
    #     spelled with the diff's a/ or b/ prefix instead of the hit's own.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()   # the hit is [A] in tests/test_math.py
    rules(rule("asserts_removed", "tests/nope.py", [A]),
          rule("asserts_removed", "b/tests/test_math.py", [A])); hy_run()
    ck("a ruling for a non-hit: nothing cleared", "0", hy(".cleared | length"))
    ck("a ruling for a non-hit: blocks", "asserts_removed", hy('.blocking | join(",")'))
    ck("a ruling for a non-hit: no cleared clause", "0", count("cleared by refuter rulings", file=reason))

    # R8. The other blocks stand whatever a ruling says, and the ruled hit is still
    #     cleared: the counters are independent.
    hy_repo()
    git("-C", wt, "rm", "-q", "tests/test_math.py"); lines_to(wt / ".fabro/test.toml", "version = 1"); commit()
    rules(rule("tests_deleted", "tests/test_math.py", ["file deleted"])); hy_run()
    ck("config_changed: blocks", "config_changed", hy('.blocking | join(",")'))
    ck("config_changed: the ruled hit is still cleared", "1", hy(".cleared | length"))
    ck("config_changed: reason", "1", count("config_changed=1", file=reason))
    ck("config_changed: the ruled counter is not in the reason", "0", count("tests_deleted", file=reason))
    ck("config_changed: the cleared clause", "1", count("(1 hit(s) cleared by refuter rulings)", file=reason))
    hy_repo('{"erosion_mode":"loud"}')
    git("-C", wt, "rm", "-q", "tests/test_math.py"); commit()
    rules(rule("tests_deleted", "tests/test_math.py", ["file deleted"])); hy_run()
    ck("an invalid base config: blocks", "config_invalid", hy('.blocking | join(",")'))
    ck("an invalid base config: the ruled hit is still cleared", "1", hy(".cleared | length"))
    hy_repo('{"erosion_mode":"block"}')
    git("-C", wt, "rm", "-q", "tests/test_math.py")
    lines_to(wt / "src/math.py", "x = 1  # noqa", "y = 2  # noqa", "z = 3  # noqa", "w = 4  # noqa"); commit()
    rules(rule("tests_deleted", "tests/test_math.py", ["file deleted"])); hy_run()
    ck("erosion in block mode: blocks", "lint_disable", hy('.blocking | join(",")'))
    ck("erosion in block mode: the ruled hit is still cleared", "1", hy(".cleared | length"))
    ck("erosion in block mode: reason", "1", count("lint_disable=4", file=reason))
    ck("erosion in block mode: the cleared clause", "1", count("cleared by refuter rulings", file=reason))
    hy_repo()
    git("-C", wt, "rm", "-q", "tests/test_math.py"); commit()
    rules(rule("tests_deleted", "tests/test_math.py", ["file deleted"]))
    write(T / "base_ref", "nosuch\n"); hy_run()
    ck("hygiene_error (no base): blocks", "hygiene_error", hy('.blocking | join(",")'))
    ck("hygiene_error (no base): hits are empty", "[]", hy(".hits | tojson"))
    ck("hygiene_error (no base): cleared is empty", "[]", hy(".cleared | tojson"))

    # R9. A malformed rulings file clears nothing, and fails the node not at all.
    hy_repo()
    git("-C", wt, "rm", "-q", "tests/test_math.py"); commit()
    for name, text in [
        ("not JSON", "nope\n"),
        ("an object, not one array", '{"counter": "tests_deleted"}\n'),
        ("an entry whose counter is a number", '[{"counter": 1, "file": "tests/test_math.py", "ruling": "legitimate", "texts": ["file deleted"]}]\n'),
        ("an entry whose ruling is pass", '[{"counter": "tests_deleted", "file": "tests/test_math.py", "ruling": "pass", "texts": ["file deleted"]}]\n'),
        ("an entry whose texts is a string", '[{"counter": "tests_deleted", "file": "tests/test_math.py", "ruling": "legitimate", "count": 1, "texts": "file deleted"}]\n'),
        ("an entry without a count", '[{"counter": "tests_deleted", "file": "tests/test_math.py", "ruling": "legitimate", "texts": ["file deleted"]}]\n'),
    ]:
        write(T / "review/hygiene_rulings.json", text)
        r = sh(H.node, cwd=wt)
        ck(f"malformed rulings ({name}): the node exits 0", "0", r.rc)
        ck(f"malformed rulings ({name}): nothing cleared", "0", hy(".cleared | length"))
        ck(f"malformed rulings ({name}): still blocks", "tests_deleted", hy('.blocking | join(",")'))

    # R10. Exactly one ruling may name a hit: two never clear it, even two identical ones.
    rules(rule("tests_deleted", "tests/test_math.py", ["file deleted"]),
          rule("tests_deleted", "tests/test_math.py", ["file deleted"])); hy_run()
    ck("two rulings for one hit: nothing cleared", "0", hy(".cleared | length"))
    ck("two rulings for one hit: blocks", "tests_deleted", hy('.blocking | join(",")'))

    # R11. Two hits, one legitimate ruling each: both cleared, in hits order, nothing
    #      blocks and no reason file is written.
    hy_repo()
    hy_base("tests/test_zeta.py", "def test_z():", "    pass")
    git("-C", wt, "rm", "-q", "tests/test_zeta.py")
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()
    rules(rule("tests_deleted", "tests/test_zeta.py", ["file deleted"]),
          rule("asserts_removed", "tests/test_math.py", [A])); hy_run()
    ck("two ruled hits: both cleared, in hits order", cjson([
        {"counter": "tests_deleted", "file": "tests/test_zeta.py", "count": 1},
        {"counter": "asserts_removed", "file": "tests/test_math.py", "count": 1}]), hy(".cleared | tojson"))
    ck("two ruled hits: nothing blocks", "0", hy(".blocking | length"))
    ck("two ruled hits: no reason file", "absent", present(reason))


def test_hygiene_rulings_ci_fix(ck, tmp_path):
    """#92: the CI-fix shape. A ruling covers the lines as they are when the counters ran,
    so a later commit that removes another assertion blocks and one that restores a ruled
    line stays cleared. ci_fix_gate runs this program again, unchanged."""
    H = Hygiene(ck, tmp_path)
    T, wt = H.T, H.wt
    lines_to, hy_repo, commit, hy_run, hy, rules = (H.lines_to, H.repo, H.commit, H.run, H.get, H.rulings)
    A, B = "assert add(0, 0) == 0", "assert add(1, 2) == 3"
    reason = T / "review/hygiene_reason.txt"
    rulings_file = T / "review/hygiene_rulings.json"

    # 1. The branch removes one assertion; the ruling names that line and clears it.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit()
    rules(rule("asserts_removed", "tests/test_math.py", [A]))
    first = cat(rulings_file); hy_run()
    ck("ci fix, before: cleared", "1", hy(".cleared | length"))
    ck("ci fix, before: nothing blocks", "0", hy(".blocking | length"))

    # 2. A later commit that adds no hit stays cleared, and the program leaves the
    #    rulings file alone.
    lines_to(wt / "src/math.py", "def add(a, b):", '    """Add."""', "    return a + b"); commit(); hy_run()
    ck("a fix that adds no hit: still cleared", "1", hy(".cleared | length"))
    ck("a fix that adds no hit: nothing blocks", "0", hy(".blocking | length"))
    ck("the rulings file is not rewritten", first, cat(rulings_file))

    # 3. A CI fix that removes a DIFFERENT assertion blocks: the ruling covers lines, not
    #    a count.
    lines_to(wt / "tests/test_math.py", "def test_a():"); commit(); hy_run()
    ck("a fix removing a different assertion: blocks", "asserts_removed", hy('.blocking | join(",")'))
    ck("a fix removing a different assertion: the reason counts both lines", "1", count("asserts_removed=2", file=reason))
    ck("a fix removing a different assertion: nothing cleared", "0", hy(".cleared | length"))
    ck("the rulings file is still not rewritten", first, cat(rulings_file))

    # 4. The Refuter rules again on the two lines (refute_prep deleted the old file) ...
    rules(rule("asserts_removed", "tests/test_math.py", [B, A]))
    second = cat(rulings_file); hy_run()
    ck("ruled again on both lines: cleared", "1", hy(".cleared | length"))
    ck("ruled again on both lines: nothing blocks", "0", hy(".blocking | length"))

    # 5. ... and a change that restores one of the ruled lines stays cleared.
    lines_to(wt / "tests/test_math.py", "def test_a():", "    " + B); commit(); hy_run()
    ck("a restored ruled line: still cleared", "1", hy(".cleared | length"))
    ck("a restored ruled line: the hit is one line now", "1", hy(".hits[0].count"))
    ck("a restored ruled line: nothing blocks", "0", hy(".blocking | length"))
    ck("the rulings file is not rewritten after a re-run", second, cat(rulings_file))

    # 6. A ruling covers the number of assertions lost, too. Two removals netted against one
    #    added assertion were ruled on as one lost; a later commit that drops the added one
    #    removes no unlisted line but loses a second assertion, and blocks.
    hy_repo()
    lines_to(wt / "tests/test_math.py", "def test_a():", "    assert ready(x)"); commit(); hy_run()
    ck("netted: one lost, both lines listed", cjson([
        {"counter": "asserts_removed", "file": "tests/test_math.py", "count": 1, "texts": [B, A]}]), hy(".hits | tojson"))
    rules(rule("asserts_removed", "tests/test_math.py", [B, A], n=1)); hy_run()
    ck("netted, ruled: cleared", "1", hy(".cleared | length"))
    lines_to(wt / "tests/test_math.py", "def test_a():"); commit(); hy_run()
    ck("a fix that drops the added assertion: nothing cleared", "0", hy(".cleared | length"))
    ck("a fix that drops the added assertion: blocks", "asserts_removed", hy('.blocking | join(",")'))


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
# review-merge refute_prep and refute_gate -- the rulings (#92), real git, the REAL node
# order: hygiene, refute_prep, a fixture refute.json carrying `hygiene`, refute_gate,
# merge_gate and, where the case needs it, ci_fix_gate on a new commit.
#
# review/hygiene_rulings.json is written only by refute_gate, so every case reaches it
# through the chain that produces it. One gh stub answers `issue view` from per-issue
# fixtures and `pr view` / `api` from the all-green fixture of test_merge_gate_hygiene_
# and_refuter. The run branch deletes one test file, removes one assertion, adds a new
# test file whose line 2 is blank and line 3 is its assertion, adds a Rust line and touches
# the module, so the counters see a tests_deleted and an asserts_removed hit and every
# replacement path the checks read is a real file.
# ---------------------------------------------------------------------------
DEFAULT_ISSUES = [
    (12, "Port the parser and delete tests/test_old.py",
     "The old parser is gone, so delete\n  tests/test_old.py as part\nof this change."),
    (13, "Replace the regression suite",
     "The regression suite is replaced; remove tests/test_old.py and its parser entry points."),
]


def hr(counter, file, ruling="legitimate", basis="requirement",
       evidence="delete tests/test_old.py as part of this change", scenario=""):
    """One entry of refute.json's `hygiene`, as the Refuter submits it."""
    return {"counter": counter, "file": file, "ruling": ruling, "basis": basis,
            "evidence": evidence, "scenario": scenario}


REQ = hr("tests_deleted", "tests/test_old.py")
REPL = hr("asserts_removed", "tests/test_math.py", basis="replacement",
          evidence="tests/test_new.py:3")
TAMPER = hr("tests_deleted", "tests/test_old.py", ruling="tampering", basis="none",
            evidence="", scenario="the deleted tests/test_old.py covered the parser error path and nothing replaced it")


class Refute:
    """One throwaway repository and the five staged nodes of the refuter chain."""

    def __init__(self, ck, tmp_path, name="ref", cfg=None):
        self.ck = ck
        self.T = T = tmp_path / name
        (T / "bin").mkdir(parents=True)
        self.hygiene = stage(ck, "hygiene", T, graph=SHARED, name="hygiene_node")
        self.prep = stage(ck, "refute_prep", T, graph=SHARED)
        self.gate = stage(ck, "refute_gate", T, graph=SHARED)
        self.mgate = T / "merge_gate.sh"
        self.mgate.write_text(rebase(extract(SHARED, "merge_gate"), T))
        self.cgate = stage(ck, "ci_fix_gate", T, graph=SHARED)
        stub(T / "bin", "gh", """#!/bin/sh
if [ "$1 $2" = "issue view" ]; then
  if [ -f "$GH_STATE/issue-$3.json" ]; then cat "$GH_STATE/issue-$3.json"; else exit 1; fi
  exit 0
fi
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
        self.env = {"GH_STATE": T, "GH_LOG": T / "gh.log"}
        self.path = f"{T / 'bin'}:{ORIG_PATH}"
        self.cfg = cfg
        self.repo()

    @staticmethod
    def lines_to(p, *ls):
        write(p, "".join(l + "\n" for l in ls))

    def repo(self):
        """The upstream and the clone, the run branch checked out. cfg, when set, is the
        base branch's .fabro/hygiene.json."""
        T = self.T
        up, wt = T / "up", T / "wt"
        rm(up, wt, T / "review", T / "_hygiene", T / "_refute", T / ".io")
        for d in ("tests", "src", ".fabro"):
            (up / d).mkdir(parents=True)
        git("-C", up, "init", "-q", "-b", "main", ".")
        git("-C", up, "config", "user.email", "t@t"); git("-C", up, "config", "user.name", "t")
        git("-C", up, "config", "receive.denyCurrentBranch", "ignore")
        self.lines_to(up / "tests/test_old.py", "def test_old():", "    assert parse_a() == 1")
        self.lines_to(up / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3", "    assert add(0, 0) == 0")
        self.lines_to(up / "tests/test_extra.py", "def test_e():", "    assert add(2, 2) == 4")
        self.lines_to(up / "src/math.py", "def add(a, b):", "    return a + b")
        self.lines_to(up / "src/lib.rs", "fn f() -> u8 { 1 }")
        self.lines_to(up / ".fabro/ci.sh", "#!/bin/sh", "exit 0")
        if self.cfg:
            write(up / ".fabro/hygiene.json", self.cfg)
        git("-C", up, "add", "-A"); git("-C", up, "commit", "-qm", "init")
        git("clone", "-q", up, wt)
        git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
        git("-C", wt, "checkout", "-qb", "fabro/run/01TEST")
        write(T / "base_ref", "main\n"); write(T / "head_ref", "fabro/run/01TEST\n")
        self.up, self.wt = up, wt

    def commit(self):
        git("-C", self.wt, "add", "-A"); git("-C", self.wt, "commit", "-qm", "change")

    def branch(self, clean=False):
        """The run branch's one commit: a deletion, a removal, a new test file, a Rust line
        and a module line; or, clean, only the module line, so no counter hit at all."""
        wt = self.wt
        if clean:
            self.lines_to(wt / "src/math.py", "def add(a, b):", '    """Add."""', "    return a + b")
            self.commit(); return
        git("-C", wt, "rm", "-q", "tests/test_old.py")
        self.lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3")
        self.lines_to(wt / "tests/test_new.py", "def test_new():", "", "    assert add(0, 0) == 0 and add(0, 1) == 1")
        self.lines_to(wt / "src/math.py", "def add(a, b):", '    """Add."""', "    return a + b")
        self.lines_to(wt / "src/lib.rs", "fn f() -> u8 { 1 }", "fn g() -> u8 { 2 }")
        self.commit()

    def issues(self, spec):
        write(self.T / "linked_issues.json", json.dumps([{"number": n} for n, _, _ in spec]) + "\n")
        for n, title, body in spec:
            write(self.T / f"issue-{n}.json", json.dumps({"number": n, "title": title, "body": body}) + "\n")

    def green(self):
        """Everything merge_gate checks 1 to 12 needs, green."""
        T = self.T
        write(T / "auto_merge", "1\n"); write(T / "pr_number", "5\n")
        write(T / "pr.json", '{"url":"https://github.com/o/r/pull/5"}\n')
        write(T / "review/fix_result.json", '{"outcome":"no_changes_needed","risk":1,"not_fixed":[],"own_findings":[],"fixes_applied":[]}\n')
        write(T / "pr.fixture.json", jq_n(r'''{labels:[{name:"agent-authored"}], state:"OPEN", isDraft:false, reviewDecision:"APPROVED",
            commits:[], title:"fix: a thing",
            body:(["x","<!-- fabro:commit-body:start -->","Resolves #1","<!-- fabro:commit-body:end -->"] | join("\n"))}''', compact=False) + "\n")

    def fresh(self, clean=False):
        """A fresh ruled-ready run: repo, branch, green PR, issues, receipt, validated tree."""
        self.repo(); self.branch(clean); self.green(); self.issues(DEFAULT_ISSUES)
        mkreadok(self.T, FV)
        write(self.T / "validated_tree", git("-C", self.wt, "rev-parse", "HEAD^{tree}") + " final\n")
        rm(self.T / "merge_block_reason", self.T / "review/hygiene_reason.txt", self.T / "refute_fix_attempts", self.T / "refute_fix_base")

    def hyg(self):
        sh(self.hygiene, cwd=self.wt)

    def run_prep(self):
        sh(self.prep, cwd=self.wt, path=self.path, env=self.env)

    def result(self, hygiene, criteria=None, defects=None, visit=FV, receipt=True):
        doc = {"summary": "checked", "criteria": criteria or [{"criterion": "the work is done", "status": "met", "evidence": "src/math.py:2"}],
               "defects": defects or [], "hygiene": hygiene}
        if receipt:
            doc = {"_io": {"visit": visit, "binary": "0.1.0", "stage": "review_merge.refute"}, **doc}
        write(self.T / "review/refute.json", json.dumps(doc) + "\n")

    def run_gate(self):
        return sh(self.gate, cwd=self.wt, stderr="keep")

    def chain(self, hygiene, **kw):
        """hygiene, refute_prep, a fixture refute.json, refute_gate: one refuter visit."""
        self.hyg(); self.run_prep(); self.result(hygiene, **kw)
        return self.run_gate()

    def ctx(self, key, r):
        return jq(f".context_updates.{key}", lastjson(r.out))

    def hy(self, filt):
        return jq(filt, file=self.T / "review/hygiene.json")

    def rulings(self, filt):
        return jq(filt, file=self.T / "review/hygiene_rulings.json")

    def mg(self):
        r = sh(self.mgate, cwd=self.wt, path=self.path, env=self.env, stderr="keep")
        return jq(".context_updates.merge_eligible", lastjson(r.out))


def test_refute_gate_rulings(ck, tmp_path):
    """#92: the shared refuter gate judges the rulings and the counters apply them."""
    X = Refute(ck, tmp_path)
    T, wt = X.T, X.wt
    findings = T / "review/refute_findings.json"
    hreason = T / "review/hygiene_reason.txt"

    # G1. A ruled deletion and a ruled assertion removal pass the whole merge: both entries
    #     are legitimate with their reasons, the guarded re-run clears both hits, the routing
    #     counts them, and merge_gate approves.
    X.fresh(); r = X.chain([REQ, REPL])
    ck("ruled: exit 0", "0", r.rc)
    ck("ruled: verdict stays pass", "pass", X.ctx("refute_verdict", r))
    ck("ruled: cleared 2", "2", X.ctx("hygiene_cleared", r))
    ck("ruled: unsure 0", "0", X.ctx("hygiene_unsure", r))
    ck("ruled: tampering 0", "0", X.ctx("hygiene_tampering", r))
    ck("ruled: both entries legitimate", "2", X.rulings('[.[] | select(.ruling == "legitimate")] | length'))
    ck("ruled: the requirement reason", "quote found in issue #12", X.rulings(".[0].reason"))
    ck("ruled: the replacement reason", "tests/test_new.py:3 is a test file this pull request changes", X.rulings(".[1].reason"))
    ck("ruled: texts copied from the hit", cjson(["file deleted"]), X.rulings(".[0].texts | tojson"))
    ck("ruled: the count copied from the hit", "1", X.rulings(".[0].count"))
    ck("ruled: hygiene cleared both", "2", X.hy(".cleared | length"))
    ck("ruled: nothing blocks", "0", X.hy(".blocking | length"))
    ck("ruled: merge eligible", "true", X.mg())

    # G2. An unruled hit blocks exactly as before the change: [] behaves as today.
    X.fresh(); r = X.chain([])
    ck("unruled: cleared 0", "0", X.ctx("hygiene_cleared", r))
    ck("unruled: verdict still pass", "pass", X.ctx("refute_verdict", r))
    ck("unruled: no findings file", "absent", present(findings))
    ck("unruled: the empty list is written", "[]", X.rulings("tojson"))
    ck("unruled: blocks", "tests_deleted,asserts_removed", X.hy('.blocking | join(",")'))
    ck("unruled: not eligible", "false", X.mg())
    ck("unruled: the counters' reason", "1", count("tests_deleted=1, asserts_removed=1", file=hreason))

    # G3. Two files hit by asserts_removed, one ruled: blocks with the unruled hit's count.
    X.fresh()
    X.lines_to(wt / "tests/test_extra.py", "def test_e():"); X.commit()
    r = X.chain([REQ, REPL])
    ck("two files, one ruled: not eligible", "false", X.mg())
    ck("two files, one ruled: the reason counts the unruled hit", "1", count("asserts_removed=1", file=hreason))
    ck("two files, one ruled: not the raw count", "0", count("asserts_removed=2", file=hreason))
    ck("two files, one ruled: both ruled hits cleared", "2", X.hy(".cleared | length"))

    # G4. The non-rulable counters and the errors block whatever the rulings say, and the
    #     ruled hits are still cleared.
    X.fresh()
    X.lines_to(wt / "tests/test_new.py", "import pytest", "@pytest.mark.skip", "def test_new():", "", "    assert add(0, 0) == 0 and add(0, 1) == 1", "    assert True")
    X.lines_to(wt / ".fabro/test.toml", "version = 1"); X.commit()
    X.chain([REQ, REPL])
    ck("non-rulable: not eligible", "false", X.mg())
    ck("non-rulable: skips block", "1", count("skips_added=1", file=hreason))
    ck("non-rulable: tautologies block", "1", count("tautologies_added=1", file=hreason))
    ck("non-rulable: config_changed blocks", "1", count("config_changed=1", file=hreason))
    ck("non-rulable: the ruled hits are still cleared", "2", X.hy(".cleared | length"))

    Y = Refute(ck, tmp_path, name="refinv", cfg='{"erosion_mode":"loud"}')
    Y.fresh(); Y.chain([REQ, REPL])
    ck("config invalid: not eligible", "false", Y.mg())
    ck("config invalid: reason", "1", count("config_invalid", file=Y.T / "review/hygiene_reason.txt"))
    ck("config invalid: the ruled hits are still cleared", "2", Y.hy(".cleared | length"))

    X.fresh(); write(T / "base_ref", "nosuch\n")
    X.hyg(); X.run_prep(); X.result([REQ]); r = X.run_gate()
    run_files(["sh", X.gate], T / "rgerr", cwd=wt, path=X.path, env=X.env)
    ck("hygiene error: not eligible", "false", X.mg())
    ck("hygiene error: reason", "1", count("counters could not be computed", file=T / "merge_block_reason"))
    ck("hygiene error: no hits to rule", "0", X.ctx("hygiene_cleared", r))
    ck("hygiene error: the ruling is ignored", "1", count("no such hit", r.err))
    routing_check(ck, "routing: hygiene error", T / "rgerr", ".context_updates.refute_verdict")

    # G5. Every failed evidence check gives unsure with a reason that names it, and nothing
    #     else changes: the verdict stays pass, no defect, no findings file, the merge is
    #     held by the counters and the routing counts one unsure. One hygiene and one prep
    #     serve them all; each gate visit re-runs the counters under its guard.
    X.fresh(); X.hyg(); X.run_prep()
    evcases = [
        ("R1", "short quote", hr("tests_deleted", "tests/test_old.py", evidence="delete test_old.py")),
        ("R2", "without the basename", hr("tests_deleted", "tests/test_old.py", evidence="The regression suite is replaced")),
        ("R3", "quote absent", hr("tests_deleted", "tests/test_old.py", evidence="delete tests/test_old.py because the issue says so here")),
        ("R3", "half in each issue", hr("tests_deleted", "tests/test_old.py", evidence="delete tests/test_old.py as part and its parser entry points")),
        ("R3", "words out of order", hr("tests_deleted", "tests/test_old.py", evidence="test_old.py as part of this change the parser is gone")),
        ("P1", "not path colon N", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/test_new.py line 3")),
        ("P1", "a path with ..", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/../tests/test_new.py:3")),
        ("P1", "line zero", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/test_new.py:0")),
        ("R1", "under 20 characters of words, padded with punctuation", hr("tests_deleted", "tests/test_old.py", evidence="tests/test_old.py !!!!!!")),
        ("P1", "a newline in the evidence", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="src/x\ntests/test_new.py:3")),
        ("P2", "unchanged by the PR", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/test_extra.py:2")),
        ("P3", "missing from HEAD", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/test_old.py:2")),
        ("P4", "not a test path", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="src/math.py:2")),
        ("P5", "line out of range", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/test_new.py:9")),
        ("P5", "line blank", hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="tests/test_new.py:2")),
    ]
    for chk, label, entry in evcases:
        X.result([entry]); r = X.run_gate()
        ck(f"failed {chk} ({label}): unsure", "unsure", X.rulings(".[0].ruling"))
        ck(f"failed {chk} ({label}): reason names the check", "1", count(chk, X.rulings(".[0].reason")))
        ck(f"failed {chk} ({label}): routing unsure 1", "1", X.ctx("hygiene_unsure", r))
        ck(f"failed {chk} ({label}): verdict unchanged", "pass", X.ctx("refute_verdict", r))
        ck(f"failed {chk} ({label}): not fixable", "false", X.ctx("refute_fixable", r))
        ck(f"failed {chk} ({label}): no findings", "absent", present(findings))
        ck(f"failed {chk} ({label}): blocks", "false", X.mg())
    X.result([hr("tests_deleted", "tests/test_old.py", basis="none", evidence="")]); r = X.run_gate()
    ck("basis none: unsure", "unsure", X.rulings(".[0].ruling"))
    ck("basis none: reason", "1", count("basis none", X.rulings(".[0].reason")))
    # R2 matches the basename as a run of words: a longer file name that merely ends in it
    # (legacy_test_old.py for tests/test_old.py) does not name the deleted file.
    X.issues(DEFAULT_ISSUES + [(14, "Clean up fixtures", "Remove legacy_test_old.py from the fixtures directory now.")])
    X.run_prep()
    X.result([hr("tests_deleted", "tests/test_old.py", evidence="Remove legacy_test_old.py from the fixtures directory")]); r = X.run_gate()
    ck("a look-alike basename: unsure", "unsure", X.rulings(".[0].ruling"))
    ck("a look-alike basename: reason names R2", "1", count("R2", X.rulings(".[0].reason")))
    # ... and neither does a quote that names another file: one whose name ends in the
    # deleted one's after a hyphen, or the same name in another directory. The quote must
    # name the hit's own path, a tail of it, or the bare file name.
    for label, sentence in [
        ("a hyphenated look-alike", "Remove tests/legacy-test_old.py from the fixtures directory now."),
        ("the same name elsewhere", "Remove crates/other/test_old.py from the fixtures directory now."),
        ("a longer extension", "Remove tests/test_old.py.snap from the fixtures directory now."),
    ]:
        X.issues(DEFAULT_ISSUES + [(14, "Clean up fixtures", sentence)]); X.run_prep()
        X.result([hr("tests_deleted", "tests/test_old.py", evidence=sentence)]); r = X.run_gate()
        ck(f"{label}: unsure", "unsure", X.rulings(".[0].ruling"))
        ck(f"{label}: reason names R2", "1", count("R2", X.rulings(".[0].reason")))
    for label, sentence in [
        ("the bare name", "Remove test_old.py, which the port replaced."),
        ("the path in backticks", "Please remove `tests/test_old.py` with this change."),
        ("the path at the end of a sentence", "With this change, remove tests/test_old.py."),
        ("a dot-slash path in a Markdown link", "Please remove [the old test](./tests/test_old.py) now."),
        ("a pytest node id", "Please remove tests/test_old.py::test_old with this change."),
    ]:
        X.issues([(14, "Clean up fixtures", sentence)]); X.run_prep()
        X.result([hr("tests_deleted", "tests/test_old.py", evidence=sentence)]); r = X.run_gate()
        ck(f"named by {label}: legitimate", "legitimate", X.rulings(".[0].ruling"))
    # R3 is word for word: only whitespace, backticks and asterisks may differ, and the quote
    # starts and ends at a word.
    X.issues([(14, "Clean up", "Out of scope:\n\n- tests/test_old.py\n\nProposal: **delete** `tests/test_old.py` in this change.")]); X.run_prep()
    for label, ev, want in [
        ("markup dropped from the quote", "delete tests/test_old.py in this change", "legitimate"),
        ("other punctuation", "delete, tests/test_old.py: in this (change)", "unsure"),
        ("stitched across a list", "tests/test_old.py Proposal delete tests/test_old.py", "unsure"),
        ("cut inside the last word", "delete tests/test_old.py in this chan", "unsure"),
        ("cut inside the first word", "elete tests/test_old.py in this change", "unsure"),
    ]:
        X.result([hr("tests_deleted", "tests/test_old.py", evidence=ev)]); r = X.run_gate()
        ck(f"word for word ({label}): {want}", want, X.rulings(".[0].ruling"))
    X.issues([(14, "Größe", "Für die Größe: bitte lösche tests/test_old.py jetzt, übrigens.")]); X.run_prep()
    X.result([hr("tests_deleted", "tests/test_old.py", evidence="bitte lösche tests/test_old.py jetzt")]); r = X.run_gate()
    ck("word for word (non-ASCII text before and in the quote): legitimate", "legitimate", X.rulings(".[0].ruling"))
    X.issues(DEFAULT_ISSUES); X.run_prep()
    write(T / "pr.fixture.json", jq('.body = (.body + "\ndelete tests/test_old.py as the port asks")', file=T / "pr.fixture.json", raw=False) + "\n")
    X.result([hr("tests_deleted", "tests/test_old.py", evidence="delete tests/test_old.py as the port asks")]); r = X.run_gate()
    ck("only in the PR description: unsure", "unsure", X.rulings(".[0].ruling"))
    ck("only in the PR description: reason names R3", "1", count("R3", X.rulings(".[0].reason")))

    # G6. Each passing check gives legitimate with its reason: a quote in the title, one in
    #     the body of the second issue, one whose whitespace differs from the issue's, a
    #     replacement in a .rs file and one matched by a base-branch test_paths pattern.
    for num, entry in [
        ("#12", hr("tests_deleted", "tests/test_old.py", evidence="Port the parser and delete tests/test_old.py")),
        ("#13", hr("tests_deleted", "tests/test_old.py", evidence="remove tests/test_old.py and its parser entry points")),
        ("#12", REQ),
    ]:
        X.result([entry]); r = X.run_gate()
        ck(f"passing quote {num}: legitimate", "legitimate", X.rulings(".[0].ruling"))
        ck(f"passing quote {num}: reason names the issue", "1", count(num, X.rulings(".[0].reason")))
        ck(f"passing quote {num}: routing cleared 1", "1", X.ctx("hygiene_cleared", r))
    X.result([hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="src/lib.rs:2")]); r = X.run_gate()
    ck("a .rs replacement: legitimate", "legitimate", X.rulings(".[0].ruling"))
    ck("a .rs replacement: its reason", "src/lib.rs:2 is a test file this pull request changes", X.rulings(".[0].reason"))

    Zp = Refute(ck, tmp_path, name="reftp", cfg='{"test_paths":["(^|/)(tests?|__tests__|spec)/","(^|/)checks/"]}')
    Zp.fresh()
    Zp.lines_to(Zp.wt / "checks/extra.ts", "assert add(3, 3) == 6"); Zp.commit()
    r = Zp.chain([REQ, hr("asserts_removed", "tests/test_math.py", basis="replacement", evidence="checks/extra.ts:1")])
    ck("a base-pattern replacement: legitimate", "legitimate", Zp.rulings(".[0].ruling"))
    ck("a base-pattern replacement: nothing blocks", "0", Zp.hy(".blocking | length"))
    ck("a base-pattern replacement: eligible", "true", Zp.mg())

    # G7. tampering becomes a defect: the verdict turns fail on it, the fix route is taken,
    #     the findings hold the defect with line null, and the hit is not cleared.
    X.fresh(); r = X.chain([TAMPER])
    ck("tampering: verdict fail", "fail", X.ctx("refute_verdict", r))
    ck("tampering: tampering 1", "1", X.ctx("hygiene_tampering", r))
    ck("tampering: cleared 0", "0", X.ctx("hygiene_cleared", r))
    ck("tampering: unsure 0", "0", X.ctx("hygiene_unsure", r))
    ck("tampering: the edge keys take refute_fix", "true", X.ctx("refute_fixable", r))
    ck("tampering: attempts 0 on the edge", "0", X.ctx("refute_fix_attempts", r))
    ck("tampering: the counter is written to 1", "1", cat(T / "refute_fix_attempts"))
    ck("tampering: the base HEAD is recorded", git("-C", wt, "rev-parse", "HEAD"), cat(T / "refute_fix_base"))
    ck("tampering: the defect has line null", "null", jq(".defects[0].line", file=findings))
    ck("tampering: the defect keeps the scenario", TAMPER["scenario"], jq(".defects[0].scenario", file=findings))
    ck("tampering: the defect names the hit's file", "tests/test_old.py", jq(".defects[0].file", file=findings))
    ck("tampering: the reason names the file", "1", count("tests/test_old.py", file=T / "review/refute_reason.txt"))
    ck("tampering: the hit is not cleared", "0", X.hy(".cleared | length"))
    ck("tampering: both counters block, the ruled one included", "tests_deleted,asserts_removed", X.hy('.blocking | join(",")'))
    ck("tampering: not eligible", "false", X.mg())

    # G7b. A hit whose texts are larger than one argument may be (128 KiB on Linux) is ruled
    #      on as any other is: the texts travel in a file, so a tampering ruling is not lost.
    X.fresh()
    big = [f"    assert value_{i}(parameter_{i}) == {{'key_{i}': '{'v' * 60}'}}" for i in range(2000)]
    X.lines_to(X.up / "tests/test_big.py", "def test_big():", *big)
    git("-C", X.up, "add", "-A"); git("-C", X.up, "commit", "-qm", "big")
    git("-C", wt, "fetch", "-q"); git("-C", wt, "reset", "-q", "--hard", "origin/main")
    X.lines_to(wt / "tests/test_big.py", "def test_big():"); X.commit()
    r = X.chain([hr("asserts_removed", "tests/test_big.py", ruling="tampering", basis="none", evidence="", scenario="2000 checks are gone")])
    ck("a large hit: verdict fail", "fail", X.ctx("refute_verdict", r))
    ck("a large hit: tampering 1", "1", X.ctx("hygiene_tampering", r))
    ck("a large hit: every text kept", "2000", X.rulings(".[0].texts | length"))
    ck("a large hit: over 128 KiB", "true", X.rulings("(.[0].texts | tojson | length) > 131072"))

    # G8. Two rulings for one hit are unsure; a ruling for a file that is not a hit, and one
    #     on a counter that is not rulable, are ignored with one stderr line each and
    #     counted nowhere.
    X.fresh()
    r = X.chain([REQ, {**REQ, "basis": "none", "ruling": "unsure"}, hr("asserts_removed", "tests/nope.py"), hr("skips_added", "tests/test_new.py")])
    ck("two rulings: one unsure entry", "1", X.rulings("length"))
    ck("two rulings: unsure", "unsure", X.rulings(".[0].ruling"))
    ck("two rulings: reason", "1", count("two rulings", X.rulings(".[0].reason")))
    ck("two rulings: routing unsure 1", "1", X.ctx("hygiene_unsure", r))
    ck("ignored: one stderr line each", "2", count("no such hit", r.err))
    ck("ignored: counted nowhere", "0", X.ctx("hygiene_cleared", r))

    # G8b. The same ruling submitted twice says one thing, so it is one ruling. Any difference
    #      (here only the evidence) makes it two.
    X.fresh(); r = X.chain([REQ, REQ])
    ck("identical rulings: one legitimate entry", "legitimate", X.rulings('map(.ruling) | join(",")'))
    ck("identical rulings: cleared 1", "1", X.ctx("hygiene_cleared", r))
    X.fresh(); r = X.chain([REQ, {**REQ, "evidence": "Port the parser and delete tests/test_old.py"}])
    ck("rulings that differ in evidence: unsure", "unsure", X.rulings('map(.ruling) | join(",")'))

    # G8c. A request is evidence only when the work is done: while any criterion is not_met, a
    #      `requirement` ruling is unsure (an issue that says to port the cases and then delete
    #      the file asked for no deletion the port does not cover). A `replacement` ruling
    #      stands, and so does a requirement beside an unverifiable criterion.
    NOT_MET = [{"criterion": "every case is ported", "status": "not_met", "evidence": "tests/test_new.py:3 has one case of two"}]
    X.fresh(); r = X.chain([REQ, REPL], criteria=NOT_MET)
    ck("not_met: the requirement is unsure", "unsure", X.rulings('map(select(.basis == "requirement"))[0].ruling'))
    ck("not_met: its reason names R4", "1", count("R4", X.rulings('map(select(.basis == "requirement"))[0].reason')))
    ck("not_met: the replacement stands", "legitimate", X.rulings('map(select(.basis == "replacement"))[0].ruling'))
    ck("not_met: routing counts them", "1 1", f'{X.ctx("hygiene_cleared", r)} {X.ctx("hygiene_unsure", r)}')
    ck("not_met: the deletion blocks", "tests_deleted", X.hy('.blocking | join(",")'))
    X.fresh(); r = X.chain([REQ], criteria=[{"criterion": "the suite is green", "status": "unverifiable", "evidence": "no run"}])
    ck("unverifiable: the requirement stands", "legitimate", X.rulings(".[0].ruling"))

    # G9. Check 13b: on a tree the refuter moved, committed or not, the gate does not re-run
    #     the counters (hygiene.json keeps its .head) and merge_gate refuses with the 13b
    #     reason once the checkpoint has committed the edit.
    Z = Refute(ck, tmp_path, name="ref13b")
    Z.fresh(clean=True); Z.hyg(); Z.run_prep()
    Z.lines_to(Z.wt / "src/math.py", "def add(a, b):", "    return a + b"); Z.commit()
    Z.result([]); Z.run_gate()
    ck("13b committed: hygiene.json keeps the old head", "0", count(git("-C", Z.wt, "rev-parse", "HEAD"), Z.hy(".head"), pipe=True))
    ck("13b committed: not eligible", "false", Z.mg())
    ck("13b committed: the 13b reason", "1", count("code changed after the diff-hygiene counters ran", file=Z.T / "merge_block_reason"))

    Z.fresh(clean=True); Z.hyg(); Z.run_prep()
    before = cat(Z.T / "review/hygiene.json")
    Z.lines_to(Z.wt / "src/math.py", "def add(a, b):", "    return a + b")
    Z.result([]); Z.run_gate()
    ck("13b uncommitted: hygiene.json untouched", before, cat(Z.T / "review/hygiene.json"))
    Z.commit()
    ck("13b uncommitted: not eligible after the checkpoint", "false", Z.mg())
    ck("13b uncommitted: the 13b reason", "1", count("code changed after the diff-hygiene counters ran", file=Z.T / "merge_block_reason"))

    # G10. An invalid verdict writes no rulings: the three fields publish 0 and the file is
    #      gone, twice-invalid included.
    X.fresh(); X.result([], receipt=False)
    write(T / "refute_attempts", "0\n")
    ck("invalid: first is a repair", "1", X.run_gate().rc)
    r = X.run_gate()
    ck("invalid: second is invalid", "invalid", X.ctx("refute_verdict", r))
    ck("invalid: cleared 0", "0", X.ctx("hygiene_cleared", r))
    ck("invalid: unsure 0", "0", X.ctx("hygiene_unsure", r))
    ck("invalid: tampering 0", "0", X.ctx("hygiene_tampering", r))
    ck("invalid: no rulings file", "absent", present(T / "review/hygiene_rulings.json"))

    # G11. The routing contract holds on every path that prints a routing object.
    X.fresh(); X.hyg(); X.run_prep(); X.result([REQ, REPL])
    run_files(["sh", X.gate], T / "rgpass", cwd=wt, path=X.path, env=X.env)
    ck("routing: the pass path", "pass", routing_check(ck, "routing: pass", T / "rgpass", ".context_updates.refute_verdict"))
    X.result([TAMPER])
    run_files(["sh", X.gate], T / "rgtamper", cwd=wt, path=X.path, env=X.env)
    ck("routing: the tampering path", "1", routing_check(ck, "routing: tampering", T / "rgtamper", ".context_updates.hygiene_tampering"))
    X.result([])
    run_files(["sh", X.gate], T / "rgempty", cwd=wt, path=X.path, env=X.env)
    ck("routing: the unruled path", "pass", routing_check(ck, "routing: unruled", T / "rgempty", ".context_updates.refute_verdict"))


def test_refute_prep_rulings(ck, tmp_path):
    """#92: refute_prep deletes the rulings and resets what they cleared."""
    X = Refute(ck, tmp_path, name="rprep")
    T, wt = X.T, X.wt

    # P1. refute_prep deletes the rulings file (and the derived judged file) and un-clears
    #     the hits: the guarded re-run reads no rulings, so every hit returns to blocking.
    X.fresh(); X.chain([REQ, REPL])
    ck("prep: cleared before", "2", X.hy(".cleared | length"))
    X.run_prep()
    ck("prep: the rulings file is deleted", "absent", present(T / "review/hygiene_rulings.json"))
    ck("prep: the judged file is deleted", "absent", present(T / "review/refute_judged.json"))
    ck("prep: cleared is empty again", "0", X.hy(".cleared | length"))
    ck("prep: the hits return to blocking", "tests_deleted,asserts_removed", X.hy('.blocking | join(",")'))
    ck("prep: the hits stay", "2", X.hy(".hits | length"))

    # P2. When the tree moved, the guard fails and refute_prep fails closed in hygiene.json:
    #     cleared is emptied and the rulable counters return to blocking, while the file
    #     keeps the .head of the commit the counters last read (13b blocks on it).
    X.chain([REQ, REPL])
    ck("prep: cleared before the move", "2", X.hy(".cleared | length"))
    h0 = X.hy(".head")
    X.lines_to(wt / "src/math.py", "def add(a, b):", "    return a + b + 1"); X.commit()
    X.run_prep()
    ck("prep moved: cleared emptied", "0", X.hy(".cleared | length"))
    ck("prep moved: blocking restored", "tests_deleted,asserts_removed", X.hy('.blocking | join(",")'))
    ck("prep moved: the old head is kept", h0, X.hy(".head"))
    ck("prep moved: the counts are kept", "1", X.hy(".tamper.tests_deleted"))
    ck("prep moved: the hits are kept", "2", X.hy(".hits | length"))
    ck("prep moved: not eligible", "false", X.mg())
    hreason = T / "review/hygiene_reason.txt"
    ck("prep moved: a reason is written", "present", present(hreason))
    ck("prep moved: the reason names the counters", "1", count("tests_deleted=1, asserts_removed=1", file=hreason))
    ck("prep moved: the reason says the rulings were dropped", "1", count("rulings were dropped", file=hreason))
    ck("prep moved: no stale cleared clause", "0", count("cleared by refuter rulings", file=hreason))

    # P3. A dirty worktree alone also fails the guard: the same fail-closed, and because
    #     13b compares trees only, the merge is held by the restored blocking instead.
    X.chain([REQ, REPL])
    ck("prep: cleared before the dirty edit", "2", X.hy(".cleared | length"))
    X.lines_to(wt / "src/math.py", "def add(a, b):", "    return a + b")
    X.run_prep()
    ck("prep dirty: cleared emptied", "0", X.hy(".cleared | length"))
    ck("prep dirty: blocking restored", "tests_deleted,asserts_removed", X.hy('.blocking | join(",")'))
    ck("prep dirty: the head still equals HEAD", git("-C", wt, "rev-parse", "HEAD"), X.hy(".head"))
    rm(T / "merge_block_reason")
    ck("prep dirty: not eligible", "false", X.mg())
    ck("prep dirty: merge_gate gives the fail-closed reason", "1", count("rulings were dropped", file=T / "merge_block_reason"))
    ck("prep dirty: not the missing-result reason", "0", count("missing or invalid", file=T / "merge_block_reason"))

    # P4. A result no ruling had cleared is left with the counters' own reason.
    X.chain([])
    before = cat(T / "review/hygiene_reason.txt")
    X.lines_to(wt / "src/math.py", "def add(a, b):", "    return a + b + 2")
    X.run_prep()
    ck("prep dirty, nothing cleared: the counters' reason is kept", before, cat(T / "review/hygiene_reason.txt"))


def test_refute_gate_rulings_ci_fix(ck, tmp_path):
    """#92: through ci_fix_gate on a new commit, a ruling covers the lines it named."""
    X = Refute(ck, tmp_path, name="rcf")
    T, wt = X.T, X.wt
    BLOCK = T / "merge_block_reason"
    NOHIT = lambda: (X.lines_to(wt / "tests/test_new.py", "import pytest", "def test_new():", "", "    assert add(0, 0) == 0 and add(0, 1) == 1"), X.commit())

    def cifix(fix, rulings="kept"):
        """A fresh ruled run, one CI-fix commit, then ci_fix_gate: ci_fix_ok."""
        X.fresh(); X.chain([REQ, REPL])
        if rulings == "absent":
            rm(T / "review/hygiene_rulings.json")
        elif rulings == "garbage":
            write(T / "review/hygiene_rulings.json", "nope\n")
        write(T / "review/merge_base_files.txt", "src/lib.rs\nsrc/math.py\ntests/test_math.py\ntests/test_new.py\ntests/test_old.py\n")
        fix()
        mkreadok(T, FV)
        write(T / "review/ci_fix_result.json", '{"outcome":"fixed","scope":"ci_only","summary":"s","_io":{"visit":"' + FV + '","binary":"0.1.0","stage":"review_merge.ci_fix_t1"}}\n')
        rm(BLOCK)
        rr = sh(X.cgate, cwd=wt, path=X.path, env=X.env, stderr="keep")
        return jq(".context_updates.ci_fix_ok", lastjson(rr.out))

    # C1. A fix that adds no hit is pushed: the rulings still clear the branch's hits.
    ck("ci fix no new hit: pushed", "true", cifix(NOHIT))
    ck("ci fix no new hit: the branch moved on origin", "1",
       "1" if git_rc("-C", X.up, "rev-parse", "-q", "--verify", "fabro/run/01TEST") == 0
       and git("-C", X.up, "rev-parse", "fabro/run/01TEST") == git("-C", wt, "rev-parse", "HEAD") else "0")

    # C2. A fix that removes a different assertion is refused: the ruling covers lines.
    ck("ci fix different assertion: refused", "false", cifix(lambda: (X.lines_to(wt / "tests/test_math.py", "def test_a():"), X.commit())))
    ck("ci fix different assertion: the hygiene reason", "1", count("diff-hygiene counters block the merge", file=BLOCK))
    ck("ci fix different assertion: not pushed", "1",
       "1" if git_rc("-C", X.up, "rev-parse", "-q", "--verify", "fabro/run/01TEST") != 0 else "0")

    # C3. A fix that restores one of the ruled lines is pushed.
    ck("ci fix restored line: pushed", "true", cifix(lambda: (X.lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(1, 2) == 3", "    assert add(0, 0) == 0"), X.commit())))

    # C4. The same count with a swapped line is refused: restoring the ruled line while
    #     removing the other one leaves an unruled removal at the same count.
    ck("ci fix swapped line: refused", "false", cifix(lambda: (X.lines_to(wt / "tests/test_math.py", "def test_a():", "    assert add(0, 0) == 0"), X.commit())))

    # C4b. The swap that a hunk's netting could hide: the branch updates one assertion in
    #      place and deletes its neighbour, which is ruled on; the fix then covers the neighbour
    #      and deletes a third one at the same count. The third was never ruled on.
    def swap(fix):
        X.repo()
        X.lines_to(X.up / "tests/test_o.py", "def test_o():", "    assert total(o) == 10", "    assert status(o) == 401", '    assert owner(o) == "ada"', "    done()")
        git("-C", X.up, "add", "-A"); git("-C", X.up, "commit", "-qm", "base")
        git("-C", wt, "fetch", "-q"); git("-C", wt, "reset", "-q", "--hard", "origin/main")
        X.lines_to(wt / "tests/test_o.py", "def test_o():", "    assert total(o) == 12", '    assert owner(o) == "ada"', "    done()"); X.commit()
        X.green(); X.issues(DEFAULT_ISSUES); mkreadok(T, FV)
        X.chain([hr("asserts_removed", "tests/test_o.py", basis="replacement", evidence="tests/test_o.py:2")])
        ck("in-place swap: the ruling holds the deleted line", cjson(["assert status(o) == 401"]), X.rulings(".[0].texts | tojson"))
        ck("in-place swap: and the hit's count", "1", X.rulings(".[0].count"))
        write(T / "review/merge_base_files.txt", "tests/test_o.py\n")
        fix()
        mkreadok(T, FV)
        write(T / "review/ci_fix_result.json", '{"outcome":"fixed","scope":"ci_only","summary":"s","_io":{"visit":"' + FV + '","binary":"0.1.0","stage":"review_merge.ci_fix_t1"}}\n')
        rm(BLOCK)
        return jq(".context_updates.ci_fix_ok", lastjson(sh(X.cgate, cwd=wt, path=X.path, env=X.env, stderr="keep").out))
    ck("in-place swap: refused", "false", swap(lambda: (X.lines_to(wt / "tests/test_o.py", "def test_o():", "    assert total(o) == 12", "    assert status(o) == 403", "    done()"), X.commit())))
    ck("in-place swap: the hygiene reason", "1", count("diff-hygiene counters block the merge", file=BLOCK))

    # C5. A missing rulings file clears nothing: the push is refused.
    ck("ci fix no rulings file: refused", "false", cifix(NOHIT, rulings="absent"))

    # C6. A malformed one clears nothing either.
    ck("ci fix malformed rulings: refused", "false", cifix(NOHIT, rulings="garbage"))


RULINGS = "/tmp/fabro/review/hygiene_rulings.json"
CONSUMERS = ("merge_gate", "ci_fix_gate")


def _invariants():
    """ops/check-graph-invariants.py as a module: its reader, splice and agent-stage test."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("graph_invariants", REPO_ROOT / "ops/check-graph-invariants.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("graph_invariants", mod)
    spec.loader.exec_module(mod)
    return mod


def _covers(entry: str, path: str) -> bool:
    """Whether a Sealed entry denies `path`, as `fabro-io guard` matches it."""
    import fnmatch
    if any(c in entry for c in "*?["):
        return fnmatch.fnmatchcase(path, entry)
    return entry in path


def test_hygiene_rulings_sealed(ck):
    """#92 review: hygiene_rulings.json is Sealed, and io-guard runs, for every agent stage of
    the shared merge phase and of ralph that can run after refute_gate. An agent that wrote
    the file there would have it read as a ruling by the next counter run (ci_fix_gate's
    unconditional re-run, or ralph's later hygiene visits) and clear its own hits. The
    stages come from the spliced graphs, so a new agent stage is covered without a list.
    An importer's own agent stage needs no seal only while every path from it to a counter
    consumer passes the shared refute_prep, which deletes the file; that is checked too."""
    import re
    import tomllib
    inv = _invariants()
    manifest = json.loads((WF / "_io/manifest.json").read_text())

    def graph(p):
        return inv.Graph(inv.read_graph(p.read_text()))

    def reach(succ, starts, barrier):
        seen, todo = set(), list(starts)
        while todo:
            for b in succ.get(todo.pop(), ()):
                if b not in seen:
                    seen.add(b)
                    if b not in barrier:
                        todo.append(b)
        return seen

    for pkg in ("backlog", "pr-review", "dependabot", "ralph", "ralph-v2"):
        d = WF / pkg
        root = graph(d / "workflow.fabro")
        imports = dict(inv.imports_of(root))
        phases = {path: graph((d / path).resolve()) for path in imports.values()}
        nodes, edges = inv.splice(root, phases)
        succ = {}
        for a, b, _ in edges:
            succ.setdefault(a, set()).add(b)
        hooks = [h for h in tomllib.loads((d / "workflow.toml").read_text()).get("run", {}).get("hooks", [])
                 if h.get("event") == "pre_tool_use" and "fabro-io guard" in h.get("script", "")]
        own = (manifest["workflows"].get(pkg) or {}).get("stages", {})
        barrier = {f"{x}.refute_prep" for x in imports}
        gates = [n for n in nodes if n.split(".")[-1] == "refute_gate"]
        ck(f"seal {pkg}: one refute_gate", "1", str(len(gates)))
        after = sorted(n for n in reach(succ, gates, barrier) if inv.is_agent_stage(nodes[n]))
        ck(f"seal {pkg}: agent stages run after refute_gate", "true", str(bool(after)).lower())
        consumers = {n for n in nodes if n.split(".")[-1] in CONSUMERS}
        for sid in after:
            x, _, local = sid.rpartition(".")
            if x:
                spec = manifest["phases"]["review-merge"].get(local, {})
            elif pkg in ("ralph", "ralph-v2"):
                spec = own.get(sid, {})
            else:
                # An importer's own stage: it must reach no counter consumer but through
                # the shared refute_prep, or it needs the seal like the rest.
                ck(f"seal {pkg} {sid}: reaches merge_gate/ci_fix_gate only through refute_prep",
                   "", ",".join(sorted(reach(succ, [sid], barrier) & consumers)))
                continue
            ck(f"seal {pkg} {sid}: hygiene_rulings.json is Sealed", "true",
               str(any(_covers(e, RULINGS) for e in spec.get("sealed") or [])).lower())
            ck(f"seal {pkg} {sid}: io-guard runs on it", "true",
               str(any(re.search(h.get("matcher", "$^"), sid) for h in hooks)).lower())


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
