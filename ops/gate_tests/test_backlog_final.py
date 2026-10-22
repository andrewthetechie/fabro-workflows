"""backlog's final stretch: open_pr_prep's floors and final validation, final_merge,
integrate's workflow-file refusal, drop_unreviewed, record_guidance, final_fix_prep, and
the open_pr + deliver lease push. Every case here uses REAL git."""

from types import SimpleNamespace

from harness import (GRAPH, ORIG_PATH, SHARED, Run, cat, count, edges, git, head1, jq,
                     lastjson, lines, present, rm, run, sh, sh_c, stage, stub, write)

BR = "fabro/run/01TEST"

RED_CI = """#!/bin/sh
echo ci >> "$OPP_LOG"
echo 'render {"props":{"a":1}}'
printf '\\033[31m FAIL \\033[39m src/components/Form.test.tsx > BusinessDetailsForm > patches the profile\\n'
echo ' FAIL  src/components/Form.test.tsx > BusinessDetailsForm > patches the profile'
echo 'FAILED tests/test_api.py::test_patch_profile - AssertionError'
echo 'test api::tests::patches ... FAILED'
echo '--- FAIL: TestPatchProfile (0.00s)'
echo ' Test Files  2 failed | 40 passed'
exit 1
"""
WORKFLOW_YML = "name: t\non: push\njobs: {}\n"


# ---------------------------------------------------------------------------
# open_pr_prep — the two floors in front of the PR (empty diff, workflow files)
# ---------------------------------------------------------------------------
# Every other gate stubs `git`. This one must not: what is being checked is whether
# `git diff --quiet origin/main HEAD` tells the truth about a real branch with real fabro
# checkpoint commits on it, and a stub would only test the stub. So each case builds an
# actual repo and clones it over file://, which needs no network. `.fabro/setup.sh` and
# `.fabro/ci.sh` are repo files rather than PATH commands, so they are written into the
# clone and log their own invocations -- that log is how the "refuses before spending two
# CI runs" check reads.
#
# The hole this covers: `open_pr_prep` derives the PR title and body entirely from
# issue.json and never looks at the tree, so on a branch where no task landed it produced a
# well-formed PR asserting the issue was implemented (womens-fantasy-sports#1236 and
# writers-app#949, both 0 files changed, both on 2026-09-21).
def opp_env(ck, tmp_path):
    """The staged open_pr_prep and final_merge, and the fixtures every case builds from."""
    T = tmp_path / "prep"
    (T / "bin").mkdir(parents=True)
    opp = stage(ck, "open_pr_prep", T)
    fm = stage(ck, "final_merge", T)
    # Final validation retries a red CI once after `sleep 5`; nine cases here take that
    # path. Stub it, as the other retry sections do.
    stub(T / "bin", "sleep", "#!/bin/sh\nexit 0\n")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    up, wt = T / "up", T / "wt"
    oplog = T / "opp.log"

    def g(*a, **kw):
        return git(*a, path=path, **kw)

    def opp_repo(kind):
        """A fresh upstream + clone. kind is the run branch's content: "empty" leaves the
        tree equal to main, "work" puts a real change on it, "workflow" adds a commit that
        edits a CI workflow file, "wfrevert" edits it and takes it back out."""
        rm(up, wt)
        up.mkdir(parents=True)
        g("-C", up, "init", "-q", "-b", "main", ".")
        g("-C", up, "config", "user.email", "t@t"); g("-C", up, "config", "user.name", "t")
        stub(up / ".fabro", "setup.sh", '#!/bin/sh\necho setup >> "$OPP_LOG"\n')
        stub(up / ".fabro", "ci.sh", '#!/bin/sh\necho ci >> "$OPP_LOG"\n')
        write(up / "a.txt", "base\n")
        g("-C", up, "add", "-A"); g("-C", up, "commit", "-qm", "init")
        g("clone", "-q", up, wt)
        g("-C", wt, "config", "user.email", "t@t"); g("-C", wt, "config", "user.name", "t")
        g("-C", wt, "checkout", "-qb", BR)
        # What fabro leaves on the branch when nothing lands: one checkpoint commit per
        # stage, none of them touching a repository file.
        g("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): claim (succeeded)")
        g("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): prep (failed)")
        if kind in ("work", "workflow", "wfrevert"):
            with open(wt / "a.txt", "a") as f:
                f.write("implemented\n")
            g("-C", wt, "commit", "-qam", "fabro(01TEST): integrate (succeeded)")
        if kind in ("workflow", "wfrevert"):
            write(wt / ".github/workflows/test-backend.yml", WORKFLOW_YML)
            g("-C", wt, "add", "-A"); g("-C", wt, "commit", "-qm", "fabro(01TEST): coder (succeeded)")
        # ...and then took it back out again. The net diff against main is now clean while
        # the COMMITS still carry it, which is the whole reason the gate reads `git log`.
        if kind == "wfrevert":
            g("-C", wt, "rm", "-q", ".github/workflows/test-backend.yml")
            g("-C", wt, "commit", "-qm", "fabro(01TEST): rework_t1 (succeeded)")
        write(oplog, "")
        rm(T / "completed.md", T / "pr_body.md", T / "pr_title.txt", T / "commit_subject.txt")
        write(T / "issue.json", '{"number":1205,"title":"month_played_and_crowned","body":"","labels":[]}')

    def opp_run():
        # final_merge brings main in first, as the graph does.
        r = sh(fm, cwd=wt, path=path)
        if r.rc != 0:
            return Run("", r.rc)
        return sh(opp, cwd=wt, path=path, env={"OPP_LOG": oplog})

    fm_run = lambda: sh(fm, cwd=wt, path=path)

    def main_changes_ci():
        write(up / ".github/workflows/test-backend.yml", WORKFLOW_YML)
        g("-C", up, "add", "-A"); g("-C", up, "commit", "-qm", "main changes its own CI")

    # 14. final_merge on a conflict. open_pr_prep used to run this merge bare under
    #     `set -e`: the conflict failed the node with the merge left in the tree, the
    #     checkpoint committed the markers, and every [P] Accept partial looped on them
    #     (wfs#1834 run 01M4PF5JV0FHXNV5TA0PC8TV3E, wfs#1747 run 01M4S16BRA0W1J3W32S6ENJBXH).
    def fm_conflict():
        opp_repo("work")
        with open(up / "a.txt", "a") as f:
            f.write("other\n")
        g("-C", up, "commit", "-qam", "main edits the same line")

    # #35: a red final validation routes instead of failing. The policy, in order:
    # unreviewed edits on a branch with no PR are dropped (drop); the first failure in a
    # run goes to one fix turn (fix); a tree's first failure after that goes to the gate
    # (rescue); the same tree failing again opens a draft PR instead of the gate (draft).
    def opp_red():
        # Test-runner summaries from vitest, pytest, cargo and go, plus a line with braces
        # that must never reach stdout. Written over the clone's tracked ci.sh.
        stub(wt / ".fabro", "ci.sh", RED_CI)
        g("-C", wt, "update-index", "--assume-unchanged", ".fabro/ci.sh")

    route = lambda out: jq(".context_updates.final_route", lastjson(out))

    def opp_key():
        # The key final_fail_keys holds: the patch-id of the run's own change on top of main.
        return sh_c('git -C "$1" diff origin/main HEAD | git patch-id --stable | cut -d" " -f1',
                    "sh", wt, path=path).out

    ff = T / "final_failures.txt"
    return SimpleNamespace(**locals())


def test_open_pr_prep_floors(ck, tmp_path):
    """Cases 1-12: the empty-diff and workflow-file floors."""
    E = opp_env(ck, tmp_path)
    T, up, wt, oplog, opp, g, opp_repo, opp_run, main_changes_ci = (E.T, E.up, E.wt, E.oplog, E.opp, E.g, E.opp_repo, E.opp_run, E.main_changes_ci)

    # 1. The regression itself. A branch carrying only checkpoint commits must not become
    #    a PR, and must say so rather than failing obscurely.
    opp_repo("empty")
    r = opp_run()
    ck("empty tree exits nonzero", "1", r.rc)
    ck("empty tree names the cause", "1", count("identical to origin/main", r.out))
    ck("empty tree says no task landed", "1", count("no task ever reached integrate", r.out))
    ck("empty tree points at [X]", "1", count("X. Abandon", r.out))
    # 2. It refuses BEFORE the setup/ci loop.
    ck("empty tree runs no CI", "0", lines(oplog))
    # 3. And it publishes nothing a later node could read as a real PR.
    ck("empty tree writes no body", "absent", present(T / "pr_body.md"))

    # 4. The happy path is untouched: real work still validates and derives.
    opp_repo("work")
    r = opp_run()
    ck("real work exits 0", "0", r.rc)
    ck("real work runs CI", "2", lines(oplog))
    ck("real work records the validated tree", g("-C", wt, "rev-parse", "HEAD^{tree}"), cat(T / "final_validated_tree"))
    ck("real work writes a body", "1", count("fabro:commit-body:start", file=T / "pr_body.md"))
    ck("real work derives a CC subject", "1", count("^(feat|fix|docs|chore|refactor|test|ci|build|perf|revert)(\\(.*\\))?!?: ", file=T / "commit_subject.txt", flags="E"))
    ck("real work resolves the issue", "1", count("^Resolves #1205", file=T / "pr_body.md"))

    # 4b. A Post-deploy checks section in the issue body reaches the PR body after the
    #     commit-body block, and never the commit body (ADR 0013 D4 amendment).
    opp_repo("work")
    write(T / "issue.json", '{"number":1205,"title":"month_played_and_crowned","body":"Prose here.\\n\\n## Post-deploy checks\\n\\n- [ ] Sign in on staging\\n\\n## Notes\\n\\nkept","labels":[]}')
    r = opp_run()
    ck("post-deploy PR body: exit 0", "0", r.rc)
    ck("post-deploy PR body: heading", "1", count("^## Post-deploy checks$", file=T / "pr_body.md"))
    ck("post-deploy PR body: checkbox", "1", count("^- [[] []] Sign in on staging$", file=T / "pr_body.md"))
    ck("post-deploy PR body: not in commit body", "0", count("Sign in on staging", file=T / "commit_body.md"))

    # 5. The run's work was already on main, so after `git merge origin/main` the branch
    #    adds nothing: the floor has to hold there too.
    opp_repo("empty")
    g("-C", up, "commit", "-q", "--allow-empty", "-m", "someone else shipped it")
    r = opp_run()
    ck("no-op after merge exits nonzero", "1", r.rc)
    ck("no-op after merge runs no CI", "0", lines(oplog))

    # 6. Main moving underneath a branch that DID work is not an empty diff.
    opp_repo("work")
    g("-C", up, "commit", "-q", "--allow-empty", "-m", "main moves on")
    r = opp_run()
    ck("main moved, work kept, exits 0", "0", r.rc)
    ck("main moved, work kept, runs CI", "2", lines(oplog))

    # 7. The `.github/workflows/` floor. The branch is pushed with an OAuth App token that
    #    has no `workflow` scope, so one commit touching a workflow file makes the whole
    #    branch unpushable (run 01M33SDMZF55NAEV8JV29A8EA4, 2026-09-22).
    opp_repo("workflow")
    r = opp_run()
    ck("workflow file exits nonzero", "1", r.rc)
    ck("workflow file names the path", "1", count("changes a file under .github/workflows/", r.out))
    ck("workflow file names the scope", "1", count("workflow. scope", r.out))
    ck("workflow file lists the commit", "1", count("coder (succeeded)", r.out))
    ck("workflow file lists the file", "1", count("^    .github/workflows/test-backend.yml", r.out))
    ck("workflow file says [P] will not help", "1", count("NOT recoverable by answering", r.out))
    ck("workflow file runs no CI", "0", lines(oplog))
    ck("workflow file writes no body", "absent", present(T / "pr_body.md"))

    # 8. Edited and then reverted: GitHub judges the commits in the push, not the end state.
    opp_repo("wfrevert")
    r = opp_run()
    ck("reverted workflow edit still refused", "1", r.rc)
    ck("reverted workflow edit names the commit", "1", count("coder (succeeded)", r.out))

    # 9. A change reachable from origin/main is not in the push and must not trip the floor.
    opp_repo("work")
    main_changes_ci()
    r = opp_run()
    ck("workflow change from main is not ours", "0", r.rc)
    ck("workflow change from main still runs CI", "2", lines(oplog))

    # 10. The guard must exclude merge commits (--no-merges): run 01M4DSYPAWDMJB68CCX5HM3M56
    #     (womens-fantasy-sports#1391, 2026-10-08). Grepping the extracted script is the
    #     binding contract test: no fixture can cheaply rebuild the historical merge state.
    ck("guard excludes merge commits", "1", count("rev-list --no-merges origin/main..HEAD", file=opp))
    ck("guard reads each commit's own diff", "1", count("diff-tree --no-commit-id --name-only -r .* -- .github/workflows/", file=opp))
    ck("guard still scopes to workflows path", "1", "1" if run(["grep", "-q", "--", "-- .github/workflows/", str(opp)]).rc == 0 else "0")

    # 11. Issue #18. A real merge of a main that changed a workflow file, with a stage
    #     checkpoint after it: the PR opens, and the checkpoint is not blamed.
    opp_repo("work")
    main_changes_ci()
    g("-C", wt, "fetch", "-q", "origin", "main")
    g("-C", wt, "merge", "-q", "--no-edit", "origin/main")
    g("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): next_task (succeeded)")
    r = opp_run()
    ck("merged main workflow change opens the PR", "0", r.rc)

    # 12. The message names only the offending commit and file, never a merge commit.
    opp_repo("workflow")
    g("-C", up, "commit", "-q", "--allow-empty", "-m", "main moves on")
    r = opp_run()
    ck("workflow after a merge still refused", "1", r.rc)
    ck("workflow message names no merge commit", "0", count("Merge remote-tracking", r.out))
    ck("workflow message names the commit", "1", count("^  [0-9a-f]* fabro(01TEST): coder", r.out))



def test_open_pr_prep_integrate_and_final_merge(ck, tmp_path):
    """Cases 13-18: integrate's workflow refusal and final_merge's conflicts."""
    E = opp_env(ck, tmp_path)
    T, up, wt, oplog, path, g, opp_repo, opp_run, fm_run, main_changes_ci, fm_conflict = (E.T, E.up, E.wt, E.oplog, E.path, E.g, E.opp_repo, E.opp_run, E.fm_run, E.main_changes_ci, E.fm_conflict)

    # 13. `integrate` refuses at the end of the task whose commit edited a workflow file,
    #     and passes a task whose branch only merged one in from main.
    integ = stage(ck, "integrate", T)
    write(T / "current_task.json", '{"title":"t"}')
    (T / "review").mkdir(exist_ok=True); write(T / "completed.md", "")
    integrate_run = lambda: sh(integ, cwd=wt, path=path)
    opp_repo("workflow")
    write(T / "current_task.json", '{"title":"t"}')
    rm(T / "reviewed_sha")
    r = integrate_run()
    ck("integrate refuses a task workflow edit", "1", r.rc)
    ck("integrate records no reviewed commit on refusal", "absent", present(T / "reviewed_sha"))
    ck("integrate names the commit", "1", count("^  [0-9a-f]* fabro(01TEST): coder", r.out))
    ck("integrate names the file", "1", count("^    .github/workflows/test-backend.yml", r.out))
    ck("integrate records nothing on refusal", "absent", "present" if (T / "completed.md").exists() and (T / "completed.md").stat().st_size > 0 else "absent")
    opp_repo("work")
    main_changes_ci()
    g("-C", wt, "fetch", "-q", "origin", "main"); g("-C", wt, "merge", "-q", "--no-edit", "origin/main")
    write(T / "current_task.json", '{"title":"t"}')
    r = integrate_run()
    ck("integrate passes a merge of main", "0", r.rc)
    ck("integrate records the reviewed commit (#35)", g("-C", wt, "rev-parse", "HEAD"), cat(T / "reviewed_sha"))
    rm(T / "reviewed_sha")

    fm_conflict()
    pre = g("-C", wt, "rev-parse", "HEAD")
    r = fm_run(); J = lastjson(r.out)
    ck("final_merge conflict: routes, exits 0", "0", r.rc)
    ck("final_merge conflict: final_conflict true", "true", jq(".context_updates.final_conflict", J))
    ck("final_merge conflict: names the file", "a.txt", cat(T / "final_merge_conflict"))
    ck("final_merge conflict: pre_merge_sha", pre, cat(T / "pre_merge_sha"))
    ck("final_merge conflict: HEAD unchanged", pre, g("-C", wt, "rev-parse", "HEAD"))
    ck("final_merge conflict: no merge in progress", "absent", present(wt / ".git/MERGE_HEAD"))
    ck("final_merge conflict: clean tree", "", g("-C", wt, "status", "--porcelain"))
    ck("final_merge conflict: no markers in tree", "0", count("^<<<<<<<", file=wt / "a.txt"))
    ck("final_merge conflict: one routing object", "1", count("{", r.out))

    # 15. After resolve_merge lands the merge, final_merge runs again: a no-op that routes
    #     on and clears the conflict file resolve_merge_gate reads.
    g("-C", wt, "merge", "-q", "--no-edit", "origin/main", check=False)
    write(wt / "a.txt", "base\nimplemented\nother\n")
    g("-C", wt, "add", "a.txt")
    g("-C", wt, "merge", "--continue", check=False, env={"GIT_EDITOR": "true"})
    r = fm_run(); J = lastjson(r.out)
    ck("final_merge after resolve: exits 0", "0", r.rc)
    ck("final_merge after resolve: final_conflict false", "false", jq(".context_updates.final_conflict", J))
    ck("final_merge after resolve: conflict file gone", "absent", present(T / "final_merge_conflict"))

    # 16. A failed fetch is not a conflict: resolve_merge may not fetch, so exit 1.
    opp_repo("work")
    g("-C", wt, "remote", "set-url", "origin", T / "nowhere")
    r = fm_run()
    ck("final_merge fetch failure: exits 1", "1", r.rc)
    ck("final_merge fetch failure: no conflict file", "absent", present(T / "final_merge_conflict"))

    # 17. The incident's branch, rebuilt: a conflicted merge committed by a checkpoint.
    #     final_merge finds main "Already up to date", and open_pr_prep must refuse in one
    #     line before CI rather than run CI on the markers.
    fm_conflict()
    g("-C", wt, "fetch", "-q", "origin", "main")
    g("-C", wt, "merge", "-q", "--no-edit", "origin/main", check=False)
    g("-C", wt, "add", "-A")
    g("-C", wt, "commit", "-qm", "fabro(01TEST): open_pr_prep (failed)")
    r = opp_run()
    ck("committed markers: refused", "1", r.rc)
    ck("committed markers: names the cause", "1", count("adds unresolved conflict markers", r.out))
    ck("committed markers: names the file", "1", count("^  a.txt$", r.out))
    ck("committed markers: [P] will not help", "1", count("cannot remove them", r.out))
    ck("committed markers: runs no CI", "0", lines(oplog))

    # 18. Markers that main carries (a fixture) are not this branch's, and do not trip it.
    opp_repo("work")
    write(up / "fixture.txt", "<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> other\n")
    g("-C", up, "add", "-A"); g("-C", up, "commit", "-qm", "main adds a conflict fixture")
    r = opp_run()
    ck("markers from main: PR still opens", "0", r.rc)



def test_open_pr_prep_red_final_validation(ck, tmp_path):
    """Cases 19-25: the routes a red final validation takes (#35)."""
    E = opp_env(ck, tmp_path)
    T, up, wt, oplog, g, opp_repo, opp_run, opp_red, route, opp_key, ff = (E.T, E.up, E.wt, E.oplog, E.g, E.opp_repo, E.opp_run, E.opp_red, E.route, E.opp_key, E.ff)

    rm(T / "final_fix_attempts", T / "final_fail_keys", T / "reviewed_sha", T / "pr_number",
       T / "pr_draft", T / "final_stop", T / "partial_unreviewed")

    # 19. The first red final validation goes to the one fix turn, not the gate.
    opp_repo("work"); opp_red()
    r = opp_run()
    ck("red final: routes, exits 0", "0", r.rc)
    ck("red final: first failure is fix", "fix", route(r.out))
    ck("red final: fix counted", "1", cat(T / "final_fix_attempts"))
    ck("red final: retried once", "4", lines(oplog))
    ck("red final: only the routing braces", "1", count("[{}]", r.out))
    ff = T / "final_failures.txt"
    ck("red final: names the vitest test", "1", count("^FAIL  src/components/Form.test.tsx > BusinessDetailsForm > patches the profile$", file=ff))
    ck("red final: names the pytest test", "1", count("^FAILED tests/test_api.py::test_patch_profile", file=ff))
    ck("red final: names the cargo test", "1", count("^test api::tests::patches ... FAILED$", file=ff))
    ck("red final: names the go test", "1", count("^--- FAIL: TestPatchProfile", file=ff))
    ck("red final: no ANSI in the names", "0", count("\x1b", file=ff))
    ck("red final: rework.md has the validate shape", "1", count("^## Validation failed", head1(T / "feedback/rework.md"), pipe=True))
    head8 = "\n".join(cat(T / "feedback/rework.md").split("\n")[:8])
    ck("red final: rework.md lists the tests first", "1", count("^FAILED tests/test_api.py::test_patch_profile", head8, pipe=True))
    ck("red final: no PR body", "absent", present(T / "pr_body.md"))
    ck("red final: fix records the code", opp_key(), cat(T / "final_fail_keys"))
    ck("red final: writes no validated tree", "absent", present(T / "final_validated_tree"))

    # 20. The fix turn changed the code and it is still red: the gate, which records it.
    with open(wt / "a.txt", "a") as f:
        f.write("fixed\n")
    g("-C", wt, "commit", "-qam", "fabro(01TEST): rework_t4 (succeeded)")
    r = opp_run()
    ck("red again: routes, exits 0", "0", r.rc)
    ck("red again: goes to the gate", "rescue", route(r.out))
    ck("red again: code recorded", opp_key(), cat(T / "final_fail_keys").split("\n")[-1])
    ck("red again: final_stop names the tests", "1", count("test_patch_profile", file=T / "final_stop"))

    # 21. The same code failing again never returns to the gate: a draft PR is built.
    r = opp_run()
    ck("same tree: routes, exits 0", "0", r.rc)
    ck("same tree: draft", "draft", route(r.out))
    ck("same tree: draft marker", "present", present(T / "pr_draft"))
    ck("same tree: body derived", "1", count("fabro:commit-body:start", file=T / "pr_body.md"))
    ck("same tree: body says validation failed", "1", count("failed after the final merge with origin/main", file=T / "pr_body.md"))
    ck("same tree: body names the tests", "1", count("test_patch_profile", file=T / "pr_body.md"))
    ck("same tree: body does not claim green", "0", count("green after final merge", file=T / "pr_body.md"))
    ck("same tree: subject derived", "1", count("(#1205)$", file=T / "commit_subject.txt"))

    # 22. Main moving underneath is not a code change of this run's: still a draft, not
    #     the gate (the gate's 4h wait is long enough for main to move on a busy repo).
    write(up / "m.txt", "moved\n"); g("-C", up, "add", "-A"); g("-C", up, "commit", "-qm", "main moves")
    r = opp_run()
    ck("main moved: still the same code", "draft", route(r.out))
    # A change to the run's own code is new code: the gate again.
    with open(wt / "a.txt", "a") as f:
        f.write("again\n")
    g("-C", wt, "commit", "-qam", "fabro(01TEST): rework_t4 (succeeded)")
    r = opp_run()
    ck("new code: gate again", "rescue", route(r.out))
    ck("new code: no draft marker", "absent", present(T / "pr_draft"))
    ck("new code: three keys recorded", "3", lines(T / "final_fail_keys"))

    # 23. [P] brought unreviewed edits after the last integrate, and no PR yet: drop them
    #     first, even before the fix turn.
    rm(T / "final_fix_attempts", T / "final_fail_keys", T / "pr_draft", T / "final_stop")
    opp_repo("work"); write(T / "reviewed_sha", g("-C", wt, "rev-parse", "HEAD") + "\n")
    write(wt / "b.txt", "half\n"); g("-C", wt, "add", "-A")
    g("-C", wt, "commit", "-qm", "fabro(01TEST): coder (partially_succeeded)")
    g("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): partial_remainder (succeeded)")
    opp_red()
    r = opp_run()
    ck("unreviewed, no [P] marker: not dropped", "fix", route(r.out))
    rm(T / "final_fix_attempts", T / "final_fail_keys")
    write(T / "partial_unreviewed", "")
    r = opp_run()
    ck("unreviewed: dropped first", "drop", route(r.out))
    ck("unreviewed: fix turn kept", "absent", present(T / "final_fix_attempts"))

    # 24. With a PR open, commits after the last integrate are the merge phase's: no drop.
    write(T / "pr_number", "42\n")
    r = opp_run()
    ck("PR open: no drop", "fix", route(r.out))
    rm(T / "pr_number")

    # 25. Only empty checkpoints and merges after the last integrate: nothing to drop,
    #     even with the [P] marker.
    rm(T / "final_fix_attempts", T / "final_fail_keys"); write(T / "partial_unreviewed", "")
    opp_repo("work"); write(T / "reviewed_sha", g("-C", wt, "rev-parse", "HEAD") + "\n")
    g("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): extra_prep (succeeded)")
    opp_red()
    r = opp_run()
    ck("reviewed only: fix, not drop", "fix", route(r.out))



def test_open_pr_prep_recovery_nodes(ck, tmp_path):
    """Cases 25b-27: drop_unreviewed, record_guidance, final_fix_prep, the green path and the edges."""
    E = opp_env(ck, tmp_path)
    T, up, wt, path, g, opp_repo, opp_run, route, ff = (E.T, E.up, E.wt, E.path, E.g, E.opp_repo, E.opp_run, E.route, E.ff)

    # 25b. drop_unreviewed puts the branch back on the last reviewed commit. A reset, not a
    #      restore: the commits after it include main's merges, and restoring the old tree
    #      on top would revert main's changes in the PR. final_merge merges main again.
    du = stage(ck, "drop_unreviewed", T)
    opp_repo("work")
    rs = g("-C", wt, "rev-parse", "HEAD"); write(T / "reviewed_sha", rs + "\n"); write(T / "partial_unreviewed", "")
    write(T / "completed.md", "### Task: T1\n- Review: ok\n\n")
    write(T / "current_task.json", '{"id":"t2","title":"Wire the profile form"}')
    write(up / "m.txt", "moved\n"); g("-C", up, "add", "-A"); g("-C", up, "commit", "-qm", "main moves")
    g("-C", wt, "fetch", "-q", "origin", "main"); g("-C", wt, "merge", "-q", "--no-edit", "origin/main")
    write(wt / "b.txt", "half\n"); g("-C", wt, "add", "-A")
    g("-C", wt, "commit", "-qm", "fabro(01TEST): coder (partially_succeeded)")
    r = sh(du, cwd=wt, path=path)
    ck("drop: exits 0", "0", r.rc)
    ck("drop: HEAD is the reviewed commit", rs, g("-C", wt, "rev-parse", "HEAD"))
    ck("drop: unreviewed file gone", "absent", present(wt / "b.txt"))
    ck("drop: reviewed work kept", "1", count("implemented", file=wt / "a.txt"))
    ck("drop: clean tree", "", g("-C", wt, "status", "--porcelain"))
    ck("drop: PR body says what was dropped", "1", count("^### Dropped: the unreviewed edits of Wire the profile form$", file=T / "completed.md"))
    ck("drop: no task line added", "1", count("^### Task: ", file=T / "completed.md"))
    ck("drop: [P] marker consumed", "absent", present(T / "partial_unreviewed"))
    rm(T / "reviewed_sha")
    r = sh(du, cwd=wt, path=path)
    ck("drop: no reviewed commit fails", "1", r.rc)
    rm(T / "completed.md", T / "current_task.json")

    # 25c. record_guidance: guidance after a final-validation stop goes through
    #      final_fix_prep, so the reviewer judges the fix and not main's merge.
    rg = stage(ck, "record_guidance", T)
    (T / "feedback").mkdir(exist_ok=True); rm(T / "final_stop")
    r = sh(rg, path=path, stdin="mock the PATCH call")
    ck("guidance: recorded", "mock the PATCH call", cat(T / "feedback/rescue.md"))
    ck("guidance: not a final stop", "false", jq(".context_updates.guidance_final", lastjson(r.out)))
    ck("guidance: only the routing braces", "1", count("[{}]", r.out))
    write(T / "final_stop", "FAILED x\n")
    r = sh(rg, path=path, stdin="mock it")
    ck("guidance: after a final stop", "true", jq(".context_updates.guidance_final", lastjson(r.out)))
    rm(T / "final_stop", T / "feedback/rescue.md")

    # 25d. final_fix_prep turns the failure into a task the ladder can review: a task
    #      appended to tasks.json and selected, with the merged HEAD as its diff base.
    ffp = stage(ck, "final_fix_prep", T)
    opp_repo("work")
    write(T / "tasks.json", '[{"id":"t1","title":"T1","body":"b"},{"id":"t2","title":"T2","body":"b"}]')
    write(T / "task_index", "2\n")
    write(T / "current_task.json", jq(".[1]", file=T / "tasks.json", raw=False) + "\n")
    write(T / "round", "3\n"); write(T / "review_attempts", "1\n")
    write(T / "task-context.md", "ctx\n"); write(T / "task-code.md", "code\n"); write(T / "final_stop", "x\n")
    write(ff, "FAILED tests/test_api.py::test_patch_profile\n")
    r = sh(ffp, cwd=wt, path=path)
    ck("fix prep: exits 0", "0", r.rc)
    ck("fix prep: task appended", "3", jq("length", file=T / "tasks.json"))
    ck("fix prep: task marked", "final-fix true", jq('.[2] | "\\(.id) \\(.final_fix)"', file=T / "tasks.json"))
    ck("fix prep: task selected", "final-fix", jq(".id", file=T / "current_task.json"))
    ck("fix prep: task names the tests", "1", count("test_patch_profile", jq(".body", file=T / "current_task.json"), pipe=True))
    ck("fix prep: task_index past it", "3", cat(T / "task_index"))
    ck("fix prep: diff base is the merged HEAD", g("-C", wt, "rev-parse", "HEAD"), cat(T / "task_base_sha"))
    ck("fix prep: one rework, then the gate", "6", cat(T / "round"))
    ck("fix prep: review attempts reset", "0", cat(T / "review_attempts"))
    ck("fix prep: stale dossier gone", "absent",
       "present" if present(T / "task-context.md") == "present" or present(T / "task-code.md") == "present" else "absent")
    ck("fix prep: final_stop consumed", "absent", present(T / "final_stop"))
    sh(ffp, cwd=wt, path=path)
    ck("fix prep again: no second task", "3", jq("length", file=T / "tasks.json"))
    ck("fix prep again: same index", "3", cat(T / "task_index"))
    rm(T / "tasks.json", T / "task_index", T / "current_task.json", T / "round",
       T / "review_attempts", T / "task_base_sha", ff)

    # 26. Green clears what a red visit left, and routes to the PR.
    opp_repo("work"); write(T / "pr_draft", "stale\n"); write(T / "final_stop", "stale\n")
    r = opp_run()
    ck("green: exits 0", "0", r.rc)
    ck("green: routes to the PR", "pr", route(r.out))
    ck("green: stale draft marker cleared", "absent", present(T / "pr_draft"))
    ck("green: stale final_stop cleared", "absent", present(T / "final_stop"))
    ck("green: only the routing braces", "1", count("[{}]", r.out))

    # 27. The routes are only as good as the edges that read them. Read the parsed graph.
    E = edges(GRAPH)
    for e in ("open_pr_prep->open_pr outcome=succeeded && context.final_route=pr",
              "open_pr_prep->open_pr outcome=succeeded && context.final_route=draft",
              "open_pr_prep->drop_unreviewed outcome=succeeded && context.final_route=drop",
              "open_pr_prep->final_fix_prep outcome=succeeded && context.final_route=fix",
              "open_pr_prep->rescue_brief ",
              "drop_unreviewed->final_merge outcome=succeeded",
              "final_fix_prep->rework_t4 outcome=succeeded",
              "file_remainder->mark_stuck outcome=succeeded && context.pr_draft=true",
              "file_remainder->pr_handoff outcome=succeeded && context.pr_draft=false",
              "record_guidance->final_fix_prep outcome=succeeded && context.guidance_final=true",
              "record_guidance->rework_t4 "):
        ck(f"edge: {e}", "1", count(e, E, flags="xF"))
    ck("edge: open_pr_prep has no other exit", "5", count("^open_pr_prep->", E))


# ---------------------------------------------------------------------------
# open_pr + deliver — the lease push once checkpoints stop pushing (ADR 0011 D1)
#
# REAL git, against a real bare remote, because what is under test is git's own
# --force-with-lease rule: with no remote-tracking ref for the branch it refuses as
# `stale info`. The sandbox clone is single-branch, so that ref only exists if open_pr
# creates it. Case 3 is the control: it proves this test can see the failure, so a green
# case 1 means something.
# ---------------------------------------------------------------------------


def test_open_pr_deliver(ck, tmp_path):
    T = tmp_path / "lease"
    (T / "bin").mkdir(parents=True)
    op = stage(ck, "open_pr", T)
    dl = stage(ck, "deliver", T, graph=SHARED)
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view") echo "https://github.com/o/r/pull/7"; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    upg, seed, wt = T / "up.git", T / "seed", T / "wt"
    g = lambda *a, **kw: git(*a, path=path, **kw)

    def lease_repo():
        rm(upg, seed, wt)
        g("init", "-q", "--bare", "-b", "main", upg)
        g("clone", "-q", upg, seed)
        g("-C", seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        g("-C", seed, "push", "-q", "origin", "HEAD:main")
        g("clone", "-q", "--single-branch", "--branch", "main", upg, wt)
        g("-C", wt, "config", "user.email", "t@t"); g("-C", wt, "config", "user.name", "t")
        g("-C", wt, "checkout", "-qb", BR)
        write(wt / "a.txt", "work\n")
        g("-C", wt, "add", "-A"); g("-C", wt, "commit", "-qm", "fabro(01TEST): coder (succeeded)")
        write(T / "issue.json", '{"number":350,"title":"t"}')
        write(T / "pr_title.txt", "fix: a title")
        write(T / "pr_body.md", "")
        write(T / "head_ref", BR + "\n"); write(T / "base_ref", "main\n")
        write(T / "gh.log", "")

    refspec = f"+refs/heads/{BR}:refs/remotes/origin/{BR}"
    delivered = lambda out: jq(".context_updates.delivered", lastjson(out))

    def more():
        with open(wt / "a.txt", "a") as f:
            f.write("more\n")

    # 1. open_pr pushes and creates the tracking ref; deliver then pushes a new commit.
    lease_repo()
    r = sh(op, cwd=wt, path=path, env=env)
    ck("open_pr exits 0 on a single-branch clone", "0", r.rc)
    ck("open_pr creates the tracking ref", g("-C", wt, "rev-parse", "HEAD"),
       g("-C", wt, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{BR}", check=False))
    more(); g("-C", wt, "commit", "-qam", "fabro(01TEST): review_merge.review_fix (succeeded)")
    r = sh(dl, cwd=wt, path=path, env=env)
    ck("deliver reports delivered=true", "true", delivered(r.out))
    ck("deliver moved the remote branch", g("-C", wt, "rev-parse", "HEAD"), g("-C", upg, "rev-parse", BR))

    # 2. A second open_pr (human_rescue -> [P]) adds no duplicate refspec.
    sh(op, cwd=wt, path=path, env=env)
    ck("second open_pr keeps one refspec line", "1",
       count(refspec, g("-C", wt, "config", "--get-all", "remote.origin.fetch", check=False), flags="xF", pipe=True))

    # 3. Control: push WITHOUT the widening, as the old open_pr did; deliver is refused.
    lease_repo()
    g("push", "-q", "-u", "origin", "HEAD", cwd=wt, check=False)
    more(); g("-C", wt, "commit", "-qam", "more")
    r = sh(dl, cwd=wt, path=path, env=env)
    ck("control: no tracking ref, deliver refused", "false", delivered(r.out))
    ck("control: git says stale info", "1", count("stale info", r.out))
