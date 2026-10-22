"""What the merge phase reports: deliver and mark_needs_human on the PR branch, the refusals
that must write both reason files, the rendered comment, and its commit list."""

import filecmp
from types import SimpleNamespace

from harness import (ORIG_PATH, PR, SHARED, cat, count, extract, git, grep, jq, lastjson,
                     present, rebase, rm, run, sh, stage, stub, write)

BR = "fabro/run/01TEST"


def same(a, b) -> str:
    """`cmp -s a b && echo 1 || echo 0`."""
    try:
        return "1" if filecmp.cmp(a, b, shallow=False) else "0"
    except FileNotFoundError:
        return "0"


# ---------------------------------------------------------------------------
# deliver and mark_needs_human — what the PR says about undelivered fixes (issues #8 and
# #9). REAL git against a bare remote, so the lease rule is the one under test. `delivery`
# is the marker both nodes share: delivered, rejected, pushed_on_stop, not_pushed, unchanged.
# ---------------------------------------------------------------------------
def delivery_env(ck, tmp_path):
    """deliver, rm_setup and mark_needs_human against a bare remote and a run clone."""
    T = tmp_path / "delivery"
    FAB = T / "fab"
    (T / "bin").mkdir(parents=True); FAB.mkdir()
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  "pr view "*"--json state"*)
    S=$(cat "$GH_STATE/pr_state" 2>/dev/null || echo OPEN)
    [ "$S" = ERR ] && exit 1
    echo "$S"; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    deliver = stage(ck, "deliver", T, graph=SHARED, root=FAB, label="deliver.sh")
    rm_setup = stage(ck, "rm_setup", T, graph=SHARED, root=FAB, label="rm_setup.sh")
    mark = stage(ck, "mark_needs_human", T, graph=SHARED, root=FAB, name="mark", label="mark.sh")
    upg, seed, wt, other = T / "up.git", T / "seed", T / "wt", T / "other"
    reason = FAB / "merge_block_reason"

    def dl_setup(moved=False):
        """moved: another clone pushes to the PR branch after the run's last push, so the
        lease push is refused. The run has one review_fix commit and one empty checkpoint
        commit on top of the branch's tracking ref."""
        rm(upg, seed, wt, other, FAB); FAB.mkdir()
        git("init", "-q", "--bare", "-b", "main", upg)
        git("clone", "-q", upg, seed)
        git("-C", seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        git("-C", seed, "push", "-q", "origin", "HEAD:main")
        git("clone", "-q", "--single-branch", "--branch", "main", upg, wt)
        git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
        git("-C", wt, "checkout", "-qb", BR)
        write(wt / "a.txt", "work\n")
        git("-C", wt, "add", "-A"); git("-C", wt, "commit", "-qm", "fabro(01TEST): coder (succeeded)")
        git("-C", wt, "push", "-q", "origin", BR)
        # open_pr widens the refspec so the lease has a tracking ref to compare against.
        git("-C", wt, "config", "--add", "remote.origin.fetch", f"+refs/heads/{BR}:refs/remotes/origin/{BR}")
        git("-C", wt, "fetch", "-q", "origin")
        with open(wt / "a.txt", "a") as f:
            f.write("more\n")
        git("-C", wt, "commit", "-qam", "fabro(01TEST): review_merge.review_fix (succeeded)")
        git("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): review_merge.refute (succeeded)")
        if moved:
            git("clone", "-q", "--branch", BR, upg, other)
            git("-C", other, "config", "user.email", "t@t"); git("-C", other, "config", "user.name", "t")
            write(other / "b.txt", "elsewhere\n")
            git("-C", other, "add", "-A"); git("-C", other, "commit", "-qm", "someone else")
            git("-C", other, "push", "-q", "origin", BR)
        write(FAB / "pr_number", "7")
        write(FAB / "head_ref", BR + "\n"); write(FAB / "base_ref", "main\n")
        write(FAB / "pr_url", "https://github.com/o/r/pull/7\n")
        write(FAB / "pr.json", '{"url":"https://github.com/o/r/pull/7"}')
        write(FAB / "linked_issues.json", "[]")
        write(FAB / "run_base_sha", git("-C", wt, "rev-parse", "origin/main"))
        rm(T / "pr_state"); write(T / "gh.log", "")

    def run_deliver():
        return sh(deliver, cwd=wt, path=path, env=env).out

    delivered = lambda out: jq(".context_updates.delivered", lastjson(out))
    remote = lambda: git("-C", upg, "rev-parse", BR)

    # mark_needs_human: a stop before deliver pushes what the run made, or says it did not.
    def run_rm_setup(cwd=None):
        sh(rm_setup, cwd=cwd, path=path, env={**env, "FABRO_AUTO_MERGE": "1"})

    def mh_setup(moved=False):
        dl_setup(moved)
        run_rm_setup()
        # The phase began at the pushed branch tip, before review_fix's commit; rm_setup ran
        # after dl_setup made that commit, so put the entry tree back where a run has it.
        write(FAB / "review_entry_tree", git("-C", wt, "rev-parse", f"refs/remotes/origin/{BR}^{{tree}}") + "\n")
        write(FAB / "review/fix_result.json", '{"outcome":"fixed","risk":1,"summary":"s","own_findings":[],"fixes_applied":[{"finding_id":"STD-002","axis":"standards","description":"CONTEXT.md doc fix"}],"not_fixed":[]}\n')

    run_mark = lambda: sh(mark, cwd=wt, path=path, env=env)
    comment = FAB / "pr_comment.md"

    return SimpleNamespace(**locals())


def test_deliver(ck, tmp_path):
    """deliver: what a rejected push says, and a delivered one."""
    E = delivery_env(ck, tmp_path)
    T, FAB, wt, reason, dl_setup, run_deliver, delivered, remote = (E.T, E.FAB, E.wt, E.reason, E.dl_setup, E.run_deliver, E.delivered, E.remote)

    # deliver: a rejected push says why.
    dl_setup(moved=True); write(T / "pr_state", "MERGED\n")
    out = run_deliver()
    ck("rejected: delivered=false", "false", delivered(out))
    ck("rejected: marker is rejected", "rejected", cat(FAB / "delivery"))
    ck("merged: says merged by hand", "1", count("merged by hand before the review finished", file=reason))
    ck("merged: counts one undelivered", "1", count("1 commit(s) from this review were not delivered", file=reason))
    ck("merged: lists the review commit", "1", count("review_merge.review_fix", file=reason))
    ck("merged: skips the empty checkpoint", "0", count("review_merge.refute", file=reason))
    ck("merged: both reason files agree", "1", same(reason, FAB / "needs_human_reason"))

    dl_setup(moved=True); write(T / "pr_state", "CLOSED\n")
    run_deliver()
    ck("closed: says closed", "1", count("was closed before the review finished", file=reason))
    ck("closed: both reason files agree", "1", same(reason, FAB / "needs_human_reason"))

    dl_setup(moved=True); write(T / "pr_state", "OPEN\n")
    out = run_deliver()
    ck("open: keeps the moved-branch text", "1", count("the PR branch moved while this run was working", file=reason))
    ck("open: delivered=false", "false", delivered(out))
    ck("open: marker is rejected", "rejected", cat(FAB / "delivery"))

    dl_setup(moved=True); write(T / "pr_state", "ERR\n")
    out = run_deliver()
    ck("gh error: moved-branch text", "1", count("the PR branch moved while this run was working", file=reason))
    ck("gh error: delivered=false", "false", delivered(out))
    ck("gh error: marker is rejected", "rejected", cat(FAB / "delivery"))

    dl_setup(); write(T / "pr_state", "OPEN\n")
    out = run_deliver()
    ck("delivered: delivered=true", "true", delivered(out))
    ck("delivered: marker", "delivered", cat(FAB / "delivery"))
    ck("delivered: remote is HEAD", git("-C", wt, "rev-parse", "HEAD"), remote())



def test_mark_needs_human(ck, tmp_path):
    """mark_needs_human: a stop before deliver pushes what the run made, or says it did not; rm_setup's entry tree."""
    E = delivery_env(ck, tmp_path)
    T, FAB, wt, other, remote, run_rm_setup, mh_setup, run_mark, comment = (E.T, E.FAB, E.wt, E.other, E.remote, E.run_rm_setup, E.mh_setup, E.run_mark, E.comment)

    mh_setup(); write(T / "pr_state", "OPEN\n")
    run_mark()
    ck("stop pushes: remote is HEAD", git("-C", wt, "rev-parse", "HEAD"), remote())
    ck("stop pushes: marker", "pushed_on_stop", cat(FAB / "delivery"))
    ck("stop pushes: fixes listed applied", "1", count("^### Fixes applied$", file=comment))
    ck("stop pushes: says so", "1", count("Pushed to the PR branch when the run stopped", file=comment))
    ck("stop pushes: commit links present", "1", "1" if int(count("/commits/", file=comment) or 0) >= 1 else "0")

    mh_setup(); write(T / "pr_state", "MERGED\n")
    before = remote()
    run_mark()
    ck("merged: no push", before, remote())
    ck("merged: marker not_pushed", "not_pushed", cat(FAB / "delivery"))
    ck("merged: NOT pushed heading", "1", count("^### Fixes made in this run but NOT pushed$", file=comment))
    ck("merged: no applied heading", "0", count("^### Fixes applied$", file=comment))
    ck("merged: no commit links", "0", count("/commits/", file=comment))

    mh_setup(moved=True); write(T / "pr_state", "OPEN\n")
    run_mark()
    ck("stop rejected: marker rejected", "rejected", cat(FAB / "delivery"))
    ck("stop rejected: remote kept theirs", git("-C", other, "rev-parse", "HEAD"), remote())
    ck("stop rejected: says NOT pushed", "1", count("NOT pushed", file=comment))

    mh_setup(); write(T / "pr_state", "OPEN\n"); write(FAB / "delivery", "rejected\n")
    before = remote()
    run_mark()
    ck("prior rejection: no second push", before, remote())
    ck("prior rejection: marker kept", "rejected", cat(FAB / "delivery"))

    mh_setup(); write(T / "pr_state", "OPEN\n"); write(FAB / "delivery", "delivered\n")
    before = remote()
    run_mark()
    ck("prior delivery: no push", before, remote())
    ck("prior delivery: applied heading", "1", count("^### Fixes applied$", file=comment))

    mh_setup(); write(T / "pr_state", "ERR\n")
    before = remote()
    run_mark()
    ck("gh error: no push", before, remote())
    ck("gh error: marker not_pushed", "not_pushed", cat(FAB / "delivery"))

    # A stop before the review changed any file pushes nothing: in pr-review HEAD holds
    # rebase_check's unreviewed rebase, and in backlog only empty checkpoint commits.
    mh_setup(); write(T / "pr_state", "OPEN\n")
    write(FAB / "review_entry_tree", git("-C", wt, "rev-parse", "HEAD^{tree}") + "\n")
    before = remote()
    write(T / "gh.log", "")
    run_mark()
    ck("unchanged: no push", before, remote())
    ck("unchanged: marker", "unchanged", cat(FAB / "delivery"))
    ck("unchanged: no PR state read", "0", count("pr view", file=T / "gh.log"))
    ck("unchanged: no NOT pushed heading", "0", count("NOT pushed", file=comment))

    # No entry tree recorded: the push still happens, as before the guard existed.
    mh_setup(); write(T / "pr_state", "OPEN\n"); rm(FAB / "review_entry_tree")
    run_mark()
    ck("no entry tree: pushes", git("-C", wt, "rev-parse", "HEAD"), remote())
    ck("no entry tree: marker", "pushed_on_stop", cat(FAB / "delivery"))

    # rm_setup records the tree the phase starts from.
    mh_setup()
    run_rm_setup(cwd=wt)
    ck("rm_setup records the entry tree", git("-C", wt, "rev-parse", "HEAD^{tree}"), cat(FAB / "review_entry_tree"))

    # rm_setup clears a marker left by the previous run.
    mh_setup(); write(FAB / "delivery", "delivered\n")
    run_rm_setup()
    ck("rm_setup clears a stale marker", "0", "1" if present(FAB / "delivery") == "present" else "0")




# ---------------------------------------------------------------------------
# ci_fix_gate and remerge_base — a refusal writes BOTH reason files, because a node that
# fails routes to mark_needs_human, which reads needs_human_reason.
# ---------------------------------------------------------------------------
def test_refusals_write_both_reason_files(ck, tmp_path):
    T = tmp_path / "bothreasons"
    FAB = T / "fab"
    (T / "bin").mkdir(parents=True); (FAB / "review").mkdir(parents=True)
    # Everything succeeds except a rebase, which conflicts.
    stub(T / "bin", "git", '#!/bin/sh\n# Everything succeeds except a rebase, which conflicts.\ncase "$1" in rebase) [ "$2" = --abort ] && exit 0; exit 1 ;; esac\nexit 0\n')
    path = f"{T / 'bin'}:{ORIG_PATH}"
    reason, nh = FAB / "merge_block_reason", FAB / "needs_human_reason"

    cfg = write(T / "ci_fix_gate.sh", rebase(extract(SHARED, "ci_fix_gate"), FAB))
    rm(FAB / "review/ci_fix_result.json")
    out = sh(cfg, path=path).out
    ck("ci_fix: refusal routes ci_fix_ok=false", "false", jq(".context_updates.ci_fix_ok", lastjson(out)))
    ck("ci_fix: merge_block_reason written", "1", "1" if reason.is_file() and reason.stat().st_size else "0")
    ck("ci_fix: needs_human_reason written", "1", "1" if nh.is_file() and nh.stat().st_size else "0")
    ck("ci_fix: no stray backslash in reason", "0", count("\\\\", file=reason))
    ck("shared graph: no backslash-quote", "0", count("\\'", file=SHARED, flags="F"))
    ck("ci_fix: both reason files agree", "1", same(reason, nh))

    rem = write(T / "remerge.sh", rebase(extract(SHARED, "remerge_base"), FAB))
    write(FAB / "base_ref", "main\n"); write(FAB / "head_ref", "fabro/run/01TEST\n")
    rm(reason, nh)
    # the resolve turn already ran in this run (#37), so this conflict is the one that blocks
    write(FAB / "resolve_base_attempts", "1\n")
    out = sh(rem, path=path).out
    ck("remerge: conflict routes remerge_ok=false", "false", jq(".context_updates.remerge_ok", lastjson(out)))
    ck("remerge: names the conflict", "1", count("A conflict appeared", file=reason))
    ck("remerge: both reason files agree", "1", same(reason, nh))


# ---------------------------------------------------------------------------
# review-merge render — the Refuter and hygiene rows and sections (ADR 0013 D5)
# ---------------------------------------------------------------------------
def test_render(ck, tmp_path):
    T = tmp_path / "render"
    (T / "review").mkdir(parents=True)
    rm_setup = write(T / "rm_setup.sh", rebase(extract(SHARED, "rm_setup"), T))
    write(T / "pr_number", "5\n")
    for f in ("base_ref", "head_ref", "run_base_sha"):
        write(T / f, "x\n")
    write(T / "pr.json", '{"url":"https://github.com/o/r/pull/5"}\n')
    write(T / "linked_issues.json", "[]\n")
    setup = lambda: sh(rm_setup, env={"FABRO_AUTO_MERGE": "1"})
    setup()
    render = lambda kind: sh(T / "render_comment.sh", kind, cwd=T).out

    write(T / "review/refute.json", '{"summary":"s","criteria":[{"criterion":"logout works","status":"not_met","evidence":"no handler"}],"defects":[{"file":"a.py","line":9,"scenario":"crashes on empty"}]}\n')
    write(T / "review/refute_verdict", "fail\n")
    write(T / "review/hygiene.json", '{"tamper":{"skips_added":1},"erosion":{"lint_disable":0},"erosion_mode":"report","blocking":["skips_added"],"samples":[{"counter":"skips_added","file":"t.py","line":2,"text":"@pytest.mark.skip"}]}\n')
    c = render("blocked")
    ck("render: refuter row", "1", count("^| Refuter | fail |$", c))
    ck("render: refuter defect", "1", count("^- \\*\\*defect\\*\\* - a.py:9 - crashes on empty$", c))
    ck("render: hygiene sample", "1", count("t.py:2. skips_added", c))
    write(T / "review/hygiene.json", '{"version":1,"config":"default","error":"git diff against the base failed","tamper":{},"erosion":{},"blocking":["hygiene_error"],"samples":[]}\n')
    c = render("blocked")
    ck("render: hygiene error shown", "1", count("could not be computed: git diff against the base failed", c))
    ck("render: hygiene error not 'none'", "0", count("No counter fired", c))
    write(T / "review/hygiene.json", '{"config":"invalid","tamper":{"skips_added":0},"erosion":{},"erosion_mode":"report","blocking":["config_invalid"],"samples":[]}\n')
    ck("render: invalid config explained", "1", count("hygiene.json is not valid", render("blocked")))
    write(T / "review/hygiene.json", '{"tamper":{"config_changed":1},"erosion":{},"erosion_mode":"report","blocking":["config_changed"],"samples":[]}\n')
    ck("render: config_changed names every protected file", "1",
       count(".fabro/hygiene.json, .fabro/test.toml, .fabro/ci.sh or .fabro/scout.toml", render("blocked")))

    # Issue #19: the reason mark_needs_human builds from a failure signature is what the
    # needs-human comment shows, instead of "unspecified".
    rm(T / "merge_block_reason")
    write(T / "needs_human_reason", "retryable: review_fix failed (transient_infra): timed out")
    c = render("needs-human")
    ck("render: needs-human shows the failed stage", "1", count("retryable: review_fix failed (transient_infra): timed out", c))
    ck("render: needs-human is not unspecified", "0", count("unspecified", c))

    # ADR 0013 D4 amendment (gate-relief G3): the checklist is shown to the human, never judged.
    write(T / "review/post_deploy.md", "- [ ] Sign in on staging\n")
    c = render("blocked")
    ck("render: post-deploy heading shown", "1", count("^### Post-deploy checks$", c))
    ck("render: post-deploy item verbatim", "1", count("^- [[] []] Sign in on staging$", c))
    ck("render: post-deploy caveat", "1", count("Not checked by the agents. Verify after deploy.", c))

    # #37: a base merge resolved after the review is shown, and rm_setup clears a stale note.
    ck("render: no base re-merge section by default", "0", count("^### Base re-merge$", c))
    write(T / "review/resolve_base.md", "The base moved after the review and conflicted with this branch (in: a.txt).")
    c = render("merged")
    ck("render: base re-merge heading", "1", count("^### Base re-merge$", c))
    ck("render: base re-merge note verbatim", "1", count("^The base moved after the review and conflicted with this branch (in: a.txt).$", c))
    setup()
    ck("render: rm_setup clears the note", "absent", present(T / "review/resolve_base.md"))
    rm(T / "review/post_deploy.md")
    ck("render: no checklist, no heading", "0", count("^### Post-deploy checks$", render("blocked")))

    # ADR 0013 D5 amendment (gate-relief step 8): one line when the fix turn ran.
    write(T / "review/refute_fix.json", '{"summary":"s","addressed":[{"criterion":"logout works","change":"auth.py:9"}],"not_addressed":[{"criterion":"x","why":"y"}]}\n')
    ck("render: fix turn line", "1", count("One fix turn ran after the refuter's first verdict: 1 addressed, 1 not.", render("blocked")))
    rm(T / "review/refute_fix.json")
    ck("render: no fix turn, no line", "0", count("One fix turn ran", render("blocked")))

    # #17 (docs/unmet-criteria C5): the Unmet criteria section, in every report, from the
    # Refuter's checklist alone: not_met and unverifiable, never met.
    write(T / "review/refute.json", '{"summary":"s","criteria":[{"criterion":"The screen offers a Resend email action","status":"not_met","evidence":"EmailVerificationScreen.tsx:66 only re-checks"},{"criterion":"429 shows its copy","status":"met","evidence":"ok"},{"criterion":"a sign-in works","status":"unverifiable","evidence":"needs Auth0"}],"defects":[]}\n')
    for k in ("blocked", "merged", "needs-human", "queued", "complete"):
        ck(f"render {k}: unmet heading", "1", count("^### Unmet criteria$", render(k)))
    c = render("blocked")
    um = run(["awk", "/^### Unmet criteria$/{s=1; next} s && /^### /{s=0} s"], stdin=c + "\n").out
    ck("render: unmet lists not_met", "1", count("^- \\*\\*not_met\\*\\* - The screen offers a Resend email action - EmailVerificationScreen.tsx:66 only re-checks$", um))
    ck("render: unmet lists unverifiable", "1", count("^- \\*\\*unverifiable\\*\\* - a sign-in works - needs Auth0$", um))
    ck("render: unmet omits met", "0", count("429 shows its copy", um))
    line_of = lambda pat: int(grep(["-n", pat], c).split(":")[0])
    ck("render: unmet before findings", "yes", "yes" if line_of("^### Unmet criteria$") < line_of("^### Findings$") else "no")
    write(T / "review/refute.json", '{"summary":"s","criteria":[{"criterion":"a","status":"met","evidence":"x"}],"defects":[]}\n')
    ck("render: all met says None", "1", count("^_None: the Refuter found every criterion met._$", render("merged")))
    rm(T / "review/refute.json")
    ck("render: no refuter says so", "1", count("^_The Refuter did not return a result, so no criterion was checked._$", render("blocked")))


# ---------------------------------------------------------------------------
# the report's commit list (real git): only the review's own commits. pr-review's
# rebase_check moved run_base_sha past the rebase, and the render excludes origin/<base>, so
# neither a rebase nor resolve_base's merge (#37) lists base commits.
# ---------------------------------------------------------------------------
def test_report_commit_list(ck, tmp_path):
    T = tmp_path / "cmlist"
    FAB = T / "fab"
    (FAB / "review").mkdir(parents=True)
    upg, seed, wt = T / "up.git", T / "seed", T / "wt"
    git("init", "-q", "--bare", "-b", "main", upg)
    git("clone", "-q", upg, seed)
    git("-C", seed, "config", "user.email", "t@t"); git("-C", seed, "config", "user.name", "t")
    write(seed / "a.txt", "a\n"); git("-C", seed, "add", "-A"); git("-C", seed, "commit", "-qm", "init"); git("-C", seed, "push", "-q", "origin", "main")
    git("clone", "-q", upg, wt)
    git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
    git("-C", wt, "checkout", "-qb", "pr")
    write(wt / "p.txt", "p\n"); git("-C", wt, "add", "-A"); git("-C", wt, "commit", "-qm", "the pr change")
    write(seed / "m.txt", "m\n"); git("-C", seed, "add", "-A"); git("-C", seed, "commit", "-qm", "main moved"); git("-C", seed, "push", "-q", "origin", "main")
    write(FAB / "pr_number", "5\n"); write(FAB / "base_ref", "main\n"); write(FAB / "head_ref", "pr\n")
    write(FAB / "pr.json", '{"url":"https://github.com/o/r/pull/5"}\n'); write(FAB / "linked_issues.json", "[]\n")
    write(FAB / "run_base_sha", git("-C", wt, "rev-parse", "HEAD") + "\n")
    rbc = write(T / "rebase_check.sh", rebase(extract(PR, "rebase_check"), FAB))
    out = sh(rbc, cwd=wt).out
    ck("cmlist: the pr rebases clean", "clean", jq(".context_updates.rebase_state", lastjson(out)))
    ck("cmlist: rebase_check moves run_base_sha", git("-C", wt, "rev-parse", "HEAD"), cat(FAB / "run_base_sha"))
    rms = write(T / "rm_setup.sh", rebase(extract(SHARED, "rm_setup"), FAB))
    sh(rms, cwd=wt, env={"FABRO_AUTO_MERGE": "1"})
    write(wt / "f.txt", "f\n"); git("-C", wt, "add", "-A"); git("-C", wt, "commit", "-qm", "fabro(01TEST): review_merge.review_fix (succeeded)")
    git("-C", wt, "commit", "-q", "--allow-empty", "-m", "fabro(01TEST): review_merge.refute (succeeded)")
    render = lambda: sh(FAB / "render_comment.sh", "merged", cwd=wt).out
    c = render()
    ck("cmlist: lists the review's commit", "1", count("review_merge.review_fix", c))
    ck("cmlist: skips the empty checkpoint", "0", count("review_merge.refute", c))
    ck("cmlist: no base commit after a rebase", "0", count("main moved", c))
    ck("cmlist: not the pr's own commit", "0", count("the pr change", c))
    write(seed / "m2.txt", "m2\n"); git("-C", seed, "add", "-A"); git("-C", seed, "commit", "-qm", "main moved again"); git("-C", seed, "push", "-q", "origin", "main")
    git("-C", wt, "fetch", "-q", "origin", "main")
    git("-C", wt, "merge", "-q", "--no-edit", "origin/main")
    c = render()
    ck("cmlist: no base commit after a merge", "0", count("main moved again", c))
    ck("cmlist: still lists the review's commit", "1", count("review_merge.review_fix", c))
    write(FAB / "base_ref", "gone\n")
    ck("cmlist: a missing base ref still lists", "1", count("review_merge.review_fix", render()))
