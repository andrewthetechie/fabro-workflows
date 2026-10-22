"""backlog's issue lifecycle: open_pr, claim, mark_stuck, return_to_triage and
task_block_gate."""


from harness import (GRAPH, ORIG_PATH, cat, count, edges, extract, grep, head1, jq, jq_n,
                     lastjson, lines, mkreadok, present, rebase, rm, sh, stage, stub, syntax_ok,
                     wc_c, write)

BR = "fabro/run/01TEST"

# Logs every invocation and reads its behaviour out of marker files. claim's and
# mark_stuck's.
CLAIM_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "label create") exit 0 ;;
  "issue edit")   exit 0 ;;
  "issue view")
      n=0
      [ -f "$GH_STATE/view_attempts" ] && n=$(cat "$GH_STATE/view_attempts")
      n=$((n + 1)); echo "$n" > "$GH_STATE/view_attempts"
      fail_until=0
      [ -f "$GH_STATE/view_fails_until" ] && fail_until=$(cat "$GH_STATE/view_fails_until")
      if [ "$n" -le "$fail_until" ]; then
          echo "could not connect to api.github.com" >&2; exit 1
      fi
      cat "$GH_STATE/issue_payload.json"; exit 0 ;;
esac
exit 0
"""
SLEEP_STUB = "#!/bin/sh\nexit 0\n"


# ---------------------------------------------------------------------------
# open_pr — gh must never be left to infer the branch
# ---------------------------------------------------------------------------
def test_open_pr(ck, tmp_path):
    T = tmp_path / "openpr"
    (T / "bin").mkdir(parents=True)
    gate = stage(ck, "open_pr", T)

    # The sandbox clone is single-branch, so `refs/remotes/origin/<run branch>` never
    # exists and gh cannot resolve the head remote from it. These stubs therefore do
    # NOT emulate that lookup -- they assert the script never depends on it, by
    # logging every gh invocation so the checks below can read the arguments back.
    stub(T / "bin", "git", """#!/bin/sh
case "$1 $2" in
  "rev-parse --abbrev-ref") echo "fabro/run/01TEST" ;;
  "push -u")                echo "Everything up-to-date" ;;
esac
exit 0
""")
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
      if [ -f "$GH_STATE/pr_exists" ] || [ -f "$GH_STATE/created" ]; then
          echo "https://github.com/andrewthetechie/jelly-swipe/pull/123"; exit 0
      fi
      echo "no pull requests found for branch" >&2; exit 1 ;;
  "pr create")
      if [ -f "$GH_STATE/create_fails" ]; then
          echo "aborted: you must first push the current branch to a remote, or use the --head flag" >&2
          exit 1
      fi
      : > "$GH_STATE/created"; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    go = lambda: sh(gate, path=path, env=env)
    log = T / "gh.log"

    def setup(marker=None):
        rm(log, T / "pr_exists", T / "created", T / "create_fails", T / "pr_number")
        write(T / "issue.json", '{"number":350,"title":"t"}')
        write(T / "pr_title.txt", "fix: a title")
        write(T / "pr_body.md", "")
        if marker:
            write(T / marker, "")

    url = lambda out: jq(".context_updates.pr_url", lastjson(out))

    # 1. No PR yet: create it, then read the URL back.
    setup()
    r = go()
    ck("opens a PR, exit 0", "0", r.rc)
    ck("pr_url reported", "https://github.com/andrewthetechie/jelly-swipe/pull/123", url(r.out))
    ck("pr_number extracted", "123", cat(T / "pr_number"))

    # THE REGRESSION. A bare `gh pr create` resolves the head branch through
    # refs/remotes/origin/<branch>, which a single-branch clone cannot store.
    ck("create passes --head", "1", count(f"pr create --head {BR}", file=log))
    # Same for `gh pr view`: with no argument it resolves the CURRENT branch the same
    # way. Every view call must name the branch.
    ck("every pr view names branch", "0", grep(["-vc", f"^pr view {BR}"], grep("^pr view", file=log), pipe=True))

    # 2. A PR already exists (retry after a partial failure): reuse, do not create.
    setup("pr_exists")
    r = go()
    ck("existing PR, exit 0", "0", r.rc)
    ck("existing PR is reused", "0", count("pr create", file=log))
    ck("existing PR url reported", "https://github.com/andrewthetechie/jelly-swipe/pull/123", url(r.out))

    # 3. A real create failure must surface, not be swallowed by a fallback.
    setup("create_fails")
    r = go()
    ck("create failure fails stage", "1", r.rc)
    ck("gh's own error survives", "1", count("you must first push the current branch", r.out))
    ck("no pr_url on failure", "", jq('.context_updates.pr_url // ""', lastjson(r.out), pipe=True))

    # 4. An Architecture issue's PR gets the label (ADR 0012 D8); a plain one does not.
    setup()
    write(T / "issue.json", '{"number":350,"title":"t","labels":[{"name":"architecture"}]}')
    go()
    ck("architecture PR: labelled", "1", count(f"^pr edit {BR} --add-label architecture", file=log))
    setup()
    go()
    ck("plain PR: not labelled", "0", count("add-label architecture", file=log))

    # 5. #35: the same tree failed the final validation again. The PR opens as a draft so
    #    the reviewed work is not lost, the issue is not handed to review, and the reason
    #    mark_stuck posts names the PR and the failing tests.
    setup("pr_draft"); rm(T / "draft_reason.md")
    write(T / "final_failures.txt", "FAILED tests/test_api.py::test_patch_profile\n")
    r = go()
    ck("draft: exit 0", "0", r.rc)
    ck("draft: created as a draft", "1", count(f"^pr create --head {BR} .*--draft", file=log))
    ck("draft: routes as a draft", "true", jq(".context_updates.pr_draft", lastjson(r.out)))
    ck("draft: no Review label", "0", count("add-label Review", file=log))
    ck("draft: no run-complete comment", "0", count("Agent run complete", file=log))
    ck("draft: reason names the PR", "1", count("https://github.com/andrewthetechie/jelly-swipe/pull/123", file=T / "draft_reason.md"))
    ck("draft: reason names the tests", "1", count("test_patch_profile", file=T / "draft_reason.md"))
    setup("pr_exists"); write(T / "pr_draft", "")
    go()
    ck("draft, PR open: back to draft", "1", count(f"^pr ready {BR} --undo", file=log))
    ck("draft, PR open: body replaced", "1", count(f"^pr edit {BR} --body-file", file=log))
    rm(T / "pr_draft")
    setup(); write(T / "draft_reason.md", "stale\n")
    r = go()
    ck("not a draft: routes false", "false", jq(".context_updates.pr_draft", lastjson(r.out)))
    ck("not a draft: no stale reason", "absent", present(T / "draft_reason.md"))
    ck("not a draft: plain create", "0", count("--draft", file=log))


# ---------------------------------------------------------------------------
# claim — the run's first node, and the only one that creates /tmp/fabro
#
# This section exists because of a live incident. Draft 10 deleted `acquire`,
# `acquire` was the only node that ran `mkdir -p /tmp/fabro`, and `claim` --
# now the first node -- still redirected into that directory. Every backlog run
# died at its second node and parked on human_rescue for 4h holding a coder box,
# and `fabro-fire-backlog.sh` failed identically. `fabro validate` and
# check-graph-invariants.py were both green: neither runs a node's shell.
# ---------------------------------------------------------------------------
def test_claim(ck, tmp_path):
    T = tmp_path / "claim"
    (T / "bin").mkdir(parents=True)
    # NOT created: the point of the first check below is that `claim` creates it.
    FAB = T / "fabro"
    # `sleep` is stubbed so the retry path costs no wall-clock time.
    stub(T / "bin", "sleep", SLEEP_STUB)
    stub(T / "bin", "gh", CLAIM_GH)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    script = T / "claim.sh"
    go = lambda: sh(script, path=path, env=env)

    def setup(number="356"):
        # Re-extracted per case because the issue number is a MiniJinja input, not a
        # variable: the graph carries `{{ inputs.issue_number }}` literally. The empty
        # string is a case under test -- it is what an unset MiniJinja input renders to.
        rm(FAB, T / "view_attempts", T / "view_fails_until")
        # Created empty, not deleted: a case that makes no gh call at all still has to
        # be countable.
        write(T / "gh.log", "")
        write(T / "issue_payload.json", '{"state":"OPEN","number":356,"title":"t","body":"b","labels":[{"name":"agent-in-progress"}]}')
        script.write_text(rebase(extract(GRAPH, "claim"), FAB).replace("{{ inputs.issue_number }}", number))

    setup("356")
    ok, err = syntax_ok(script)
    if not ok:
        ck.fail("claim is not valid POSIX sh", err)

    # 1. THE REGRESSION. /tmp/fabro does not exist when claim starts, and claim is
    #    the only node that can make it: prep's mkdir is one node too late.
    r = go()
    ck("happy path exits 0", "0", r.rc)
    ck("claim creates /tmp/fabro", "yes", "yes" if FAB.is_dir() else "no")
    ck("issue.json is written", "356", jq(".number", file=FAB / "issue.json"))

    # 2. The number is written before any network call, so mark_stuck can always name
    #    the issue whose receipt it has to remove.
    ck("issue_number is written", "356", cat(FAB / "issue_number"))

    # 3. The label swap is idempotent and never left to a default.
    ck("swaps both labels", "1", count("issue edit 356 --add-label agent-in-progress --remove-label agent", file=T / "gh.log"))

    # 4. Input validation fails closed, before anything is written to GitHub.
    for bad in ("not-a-number", "", "0", "12x"):
        setup(bad)
        r = go()
        ck(f"rejects issue_number '{bad}'", "1", r.rc)
        ck(f"no gh call for '{bad}'", "0", lines(T / "gh.log"))

    # 5. A closed issue is a definitive answer: refuse, never work it.
    setup("356")
    write(T / "issue_payload.json", '{"state":"CLOSED","number":356,"title":"t","body":"b","labels":[{"name":"agent-in-progress"}]}')
    r = go()
    ck("refuses a CLOSED issue", "1", r.rc)
    ck("says why it refused", "1", count("not OPEN", r.out))

    # 6. The receipt must actually be on the issue after the swap, or this run is
    #    about to work an issue the scheduler did not lease.
    setup("356")
    write(T / "issue_payload.json", '{"state":"OPEN","number":356,"title":"t","body":"b","labels":[{"name":"agent"}]}')
    r = go()
    ck("refuses without the receipt", "1", r.rc)
    ck("names the wrong-issue risk", "1", count("refusing to work the wrong issue", r.out))

    # 7. A transient gh failure must not cost a coder box for four hours. Two
    #    failures then a success is a working run, not a human gate.
    setup("356")
    write(T / "view_fails_until", "2\n")
    r = go()
    ck("retries a transient failure", "0", r.rc)
    ck("took three view attempts", "3", cat(T / "view_attempts"))

    # 8. A permanent failure still fails closed, bounded, with gh's own reason.
    setup("356")
    write(T / "view_fails_until", "99\n")
    r = go()
    ck("gives up on a hard failure", "1", r.rc)
    ck("bounded at three attempts", "3", cat(T / "view_attempts"))
    ck("surfaces gh's own error", "1", count("could not connect", r.out))
    ck("issue_number survives failure", "356", cat(FAB / "issue_number"))


# ---------------------------------------------------------------------------
# mark_stuck — the receipt must come off, especially when claim failed
#
# An issue left wearing `agent-in-progress` is in no collection the scheduler
# reads, so it silently leaves the queue. Before 2026-09-19 this node read the
# number only from issue.json and skipped all label work when that file was
# absent -- which is precisely the run that reaches it.
# ---------------------------------------------------------------------------
def test_mark_stuck(ck, tmp_path):
    T = tmp_path / "markstuck"
    (T / "bin").mkdir(parents=True)
    FAB = T / "fabro"
    FAB.mkdir()
    stub(T / "bin", "gh", CLAIM_GH)
    gate = stage(ck, "mark_stuck", T, root=FAB)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    go = lambda: sh(gate, path=path, env=env)
    log = T / "gh.log"

    def setup():
        write(log, "")
        rm(FAB / "issue.json", FAB / "issue_number", FAB / "stuck_reason.md",
           FAB / "stuck_comment.md", FAB / "draft_reason.md")

    setup()
    write(FAB / "issue.json", '{"number":356}')
    r = go()
    ck("exits 0 with issue.json", "0", r.rc)
    ck("swaps the receipt for stuck", "1", count("issue edit 356 --remove-label agent-in-progress --add-label agent-stuck", file=log))
    ck("comments from a file", "1", count(f"issue comment 356 --body-file {FAB}/stuck_comment.md", file=log))
    ck("generic comment without a reason", "1", count("could not complete this issue and gave up", file=FAB / "stuck_comment.md"))

    # decompose_gate's needs_human_review route: the decomposer's summary is the comment.
    setup()
    write(FAB / "issue.json", '{"number":1279}')
    write(FAB / "stuck_reason.md", "blocker #1278 is not merged\n")
    r = go()
    ck("reason: exits 0", "0", r.rc)
    ck("reason: still swaps the label", "1", count("issue edit 1279 --remove-label agent-in-progress --add-label agent-stuck", file=log))
    ck("reason: comment carries it", "1", count("^blocker #1278 is not merged$", file=FAB / "stuck_comment.md"))
    ck("reason: introduces it", "1", count("^Agent run stopped before implementing this issue. The run reported:$", file=FAB / "stuck_comment.md"))

    # THE REGRESSION: claim died before writing issue.json, so the only record of the
    # issue is the number claim wrote first.
    setup()
    write(FAB / "issue_number", "353\n")
    r = go()
    ck("falls back to issue_number", "1", count("issue edit 353 --remove-label agent-in-progress --add-label agent-stuck", file=log))
    ck("fallback exits 0", "0", r.rc)

    # A truncated or half-written issue.json reads back as null, not as a number.
    setup()
    write(FAB / "issue.json", '{"number":null}')
    write(FAB / "issue_number", "2277\n")
    go()
    ck("null number falls back too", "1", count("issue edit 2277 --remove-label agent-in-progress", file=log))

    # #35: the draft path. open_pr wrote the reason, which names the draft PR.
    setup()
    write(FAB / "issue.json", '{"number":2733}')
    write(FAB / "draft_reason.md", "Draft PR https://github.com/o/r/pull/9 holds the reviewed work.\n")
    r = go()
    ck("draft: exits 0", "0", r.rc)
    ck("draft: swaps the label", "1", count("issue edit 2733 --remove-label agent-in-progress --add-label agent-stuck", file=log))
    ck("draft: comment is the reason", "1", count("pull/9 holds the reviewed work", file=FAB / "stuck_comment.md"))
    ck("draft: not the generic line", "0", count("gave up", file=FAB / "stuck_comment.md"))
    ck("draft: Review label removed", "1", count("issue edit 2733 --remove-label Review", file=log))

    # Nothing to name at all: say nothing to GitHub rather than edit issue 0.
    setup()
    r = go()
    ck("no number, no gh call", "0", lines(log))
    ck("no number still exits 0", "0", r.rc)


# ---------------------------------------------------------------------------
# return_to_triage — a Contradiction goes back to triage (#17, docs/unmet-criteria C4)
#
# The comment must start with the triage-questions marker, or issue-triage and
# arch-review would re-triage the issue at once instead of waiting for a human. The
# receipt (`agent-in-progress`) must come off, or the scheduler's receipt scan requeues
# it. The routing object is the only stdout, and return_reason survives braces.
# ---------------------------------------------------------------------------
def test_return_to_triage(ck, tmp_path):
    T = tmp_path / "rtt"
    (T / "bin").mkdir(parents=True)
    FAB = T / "fabro"
    FAB.mkdir()
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
echo 'gh says {"noise": true}'
[ -f "$GH_STATE/gh_fail" ] && [ "$1" = issue ] && exit 1
exit 0
""")
    gate = stage(ck, "return_to_triage", T, root=FAB)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    go = lambda **kw: sh(gate, path=path, env=env, **kw)
    log = T / "gh.log"

    def setup():
        write(log, "")
        rm(T / "gh_fail", FAB / "issue.json", FAB / "issue_number", FAB / "stuck_reason.md",
           FAB / "return_comment.md", FAB / "return.log")
        write(FAB / "issue.json", '{"number":2723}')
        write(FAB / "return.json", jq_n('{source:"coder",summary:"s",items:[{criterion:"The screen offers a {Resend} email action",non_goal:"No backend changes",why:"Resending calls the Auth0 API with a secret, so a backend must proxy it."}]}', compact=False) + "\n")

    reason = lambda out, **kw: jq(".context_updates.return_reason", out, **kw)

    setup()
    r = go(stderr="drop")
    ck("rtt: exits 0", "0", r.rc)
    ck("rtt: stdout is one object", "1", count(".", r.out))
    ck("rtt: return_reason one line", "1", count(".", reason(r.out), pipe=True))
    ck("rtt: return_reason names it", "1", count("Resend", reason(r.out), pipe=True))
    ck("rtt: return_reason <= 300", "yes", "yes" if wc_c(reason(r.out, strip=False)) <= 301 else "no")
    ck("rtt: marker is the first line", "<!-- fabro:triage-questions -->", head1(FAB / "return_comment.md"))
    ck("rtt: comment quotes criterion", "1", count("The screen offers a {Resend} email action", file=FAB / "return_comment.md"))
    ck("rtt: comment quotes non-goal", "1", count("No backend changes", file=FAB / "return_comment.md"))
    ck("rtt: comment says how to answer", "1", count("Editing this comment is not detected", file=FAB / "return_comment.md"))
    ck("rtt: comments from the file", "1", count(f"issue comment 2723 --body-file {FAB}/return_comment.md", file=log))
    ck("rtt: swaps labels", "1", count("issue edit 2723 --remove-label agent --remove-label agent-in-progress --add-label needs-info", file=log))
    first = grep(["-E", "^issue (comment|edit)"], file=log).split("\n")[0]
    ck("rtt: comment before labels", "comment", first.split(" ")[1] if " " in first else first)
    ck("rtt: stuck reason for fallback", "1", count("Resend", file=FAB / "stuck_reason.md"))
    long = "x" * 400
    setup()
    write(FAB / "return.json", jq(".items[0].criterion=$l", file=FAB / "return.json", args=["--arg", "l", long], raw=False) + "\n")
    r = go(stderr="drop")
    ck("rtt: long reason is cut", "yes", "yes" if wc_c(reason(r.out, strip=False)) <= 301 else "no")
    # gh fails: the node fails, and mark_stuck's fallback still has the reason.
    setup(); write(T / "gh_fail", "")
    r = go()
    ck("rtt: gh failure fails the node", "1", "1" if r.rc != 0 else "0")
    ck("rtt: gh failure keeps reason", "1", count("Resend", file=FAB / "stuck_reason.md"))
    # No return.json: fail, so mark_stuck says something rather than an empty question.
    setup(); rm(FAB / "return.json")
    r = go()
    ck("rtt: no return.json fails", "1", "1" if r.rc != 0 else "0")
    ck("rtt: no return.json, no gh", "0", count("^issue", file=log))
    # issue.json gone: fall back to the number claim wrote first, like mark_stuck.
    setup(); rm(FAB / "issue.json"); write(FAB / "issue_number", "2724\n")
    go()
    ck("rtt: falls back to issue_number", "1", count("issue edit 2724 ", file=log))

    E = edges(GRAPH)
    for e in ("return_to_triage->exit outcome=succeeded",
              "return_to_triage->mark_stuck ",
              "decompose_gate->return_to_triage context.decomp_status=contradiction"):
        ck(f"edge: {e}", "1", count(e, E, flags="xF"))
    ck("edge: return_to_triage has no other exit", "2", count("^return_to_triage->", E))


# ---------------------------------------------------------------------------
# task_block_gate — the coder and the rework tiers block through a contract
# (#17, docs/unmet-criteria C2), never by substitution
#
# No file, or one from an older visit, is `none` (the old file is deleted). A valid
# criterion block returns the issue to triage; a scope block, or any file this visit
# wrote that is not a valid criterion block, goes to a human. Never `none`: a stage
# that tried to block and got the shape wrong must not have its empty diff reviewed.
# ---------------------------------------------------------------------------
def test_task_block_gate(ck, tmp_path):
    T = tmp_path / "tbg"
    gate = stage(ck, "task_block_gate", T)
    mkreadok(T, "ok1", "current_task.json", "issue.json")

    def setup(body, visit):
        rm(T / "task_blocked.json", T / "return.json", T / "task_block_scope.md")
        write(T / "current_task.json", '{"id":"t1","title":"Verification screen","body":"b","files":[]}')
        if body:
            out = jq("._io.visit=$v", body, args=["--arg", "v", visit], raw=False, pipe=True)
            write(T / "task_blocked.json", out + "\n" if out else "")

    def go():
        r = sh(gate)
        return r, lastjson(r.out)

    block = lambda j: jq(".context_updates.task_block", j)

    setup("", "ok1"); r, J = go()
    ck("tbg none: exits 0", "0", r.rc)
    ck("tbg none: routes on", "none", block(J))
    ck("tbg none: one routing object", "1", count("context_updates", r.out))
    ck("tbg none: empty questions", "", jq(".context_updates.triage_questions", J))
    setup('{"kind":"criterion","criterion":"c","non_goal":"n","reason":"r"}', "older"); r, J = go()
    ck("tbg stale: none", "none", block(J))
    ck("tbg stale: file deleted", "absent", present(T / "task_blocked.json"))
    ck("tbg stale: no return", "absent", present(T / "return.json"))
    setup('{"kind":"criterion","criterion":"The screen offers a \\"Resend email\\" action","non_goal":"No backend changes","reason":"Resending calls the Auth0 API with a secret; the task allows no backend."}', "ok1"); r, J = go()
    ck("tbg criterion: exits 0", "0", r.rc)
    ck("tbg criterion: routes", "criterion", block(J))
    ck("tbg criterion: source", "coder", jq(".source", file=T / "return.json"))
    ck("tbg criterion: criterion", 'The screen offers a "Resend email" action', jq(".items[0].criterion", file=T / "return.json"))
    ck("tbg criterion: non_goal", "No backend changes", jq(".items[0].non_goal", file=T / "return.json"))
    ck("tbg criterion: why", "1", count("Auth0", jq(".items[0].why", file=T / "return.json"), pipe=True))
    ck("tbg criterion: questions", "1", count("Resend email action (conflicts with: No backend changes)", jq(".context_updates.triage_questions", J), pipe=True))
    ck("tbg criterion: no quote", "0", count('\\"', jq(".context_updates.triage_questions", J), pipe=True))
    ck("tbg criterion: file consumed", "absent", present(T / "task_blocked.json"))
    setup('{"kind":"criterion","criterion":"c","non_goal":"","reason":"the code cannot do it"}', "ok1"); r, J = go()
    ck("tbg no non-goal: criterion", "criterion", block(J))
    ck("tbg no non-goal: says so", "1", count("constraints", jq(".items[0].non_goal", file=T / "return.json"), pipe=True))
    setup('{"kind":"scope","criterion":"","non_goal":"","reason":"Needs a change to src/api/client.ts, outside the task."}', "ok1"); r, J = go()
    ck("tbg scope: routes", "scope", block(J))
    ck("tbg scope: reason kept", "1", count("src/api/client.ts", file=T / "task_block_scope.md"))
    ck("tbg scope: names the task", "1", count("Verification screen", file=T / "task_block_scope.md"))
    ck("tbg scope: no return", "absent", present(T / "return.json"))
    setup('{"kind":"criterion","criterion":"","non_goal":"n","reason":"r"}', "ok1"); r, J = go()
    ck("tbg empty criterion: scope", "scope", block(J))
    setup('{"kind":"bogus","criterion":"c","non_goal":"n","reason":"r"}', "ok1"); r, J = go()
    ck("tbg bad kind: scope", "scope", block(J))
    setup("not json", "ok1"); write(T / "task_blocked.json", "not json"); r, J = go()
    ck("tbg unreadable: not none", "scope", block(J))
    setup("", "ok1"); write(T / "task_block_scope.md", '{"kind":"scope"}\n'); r, J = go()
    ck("tbg none: old scope note gone", "absent", present(T / "task_block_scope.md"))

    E = edges(GRAPH)
    for e in ("coder->task_block_gate outcome=succeeded",
              "coder->task_block_gate outcome=partially_succeeded",
              "rework_t1->task_block_gate outcome=succeeded",
              "rework_t2->task_block_gate outcome=succeeded",
              "rework_t3->task_block_gate outcome=succeeded",
              "rework_t4->task_block_gate outcome=succeeded",
              "task_block_gate->autofix outcome=succeeded && context.task_block=none",
              "task_block_gate->return_to_triage outcome=succeeded && context.task_block=criterion",
              "task_block_gate->rescue_brief "):
        ck(f"edge: {e}", "1", count(e, E, flags="xF"))
    ck("edge: task_block_gate has no other exit", "3", count("^task_block_gate->", E))
    ck("edge: no coder or rework straight to autofix", "0", count("^(coder|rework_t[0-9])->autofix", E, flags="E"))


# ---------------------------------------------------------------------------
# close_noop — the decomposer's reason goes on the issue (issue #10)
#
# `no_work` used to post one fixed sentence, so the operator read a decomposer failure,
# re-labelled the issue `agent`, and burned another run. The summary is the reason. The
# label is a separate call, so a failure there cannot stop the receipt removal or the
# comment.
# ---------------------------------------------------------------------------
def test_close_noop(ck, tmp_path):
    T = tmp_path / "closenoop"
    (T / "bin").mkdir(parents=True)
    FAB = T / "fab"
    FAB.mkdir()
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  *"--add-label agent-noop"*) [ -f "$GH_STATE/add_fails" ] && exit 1 ;;
esac
while [ $# -gt 0 ]; do
  [ "$1" = --body-file ] && cp "$2" "$GH_STATE/comment.md"
  shift
done
exit 0
""")
    gate = stage(ck, "close_noop", T, root=FAB)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    log, comment = T / "gh.log", T / "comment.md"

    def cn_run(doc=None, fails=False):
        """doc: decomposition.json's body, or None for a missing file; fails: make
        --add-label fail."""
        rm(FAB); FAB.mkdir()
        write(FAB / "issue.json", '{"number":1393}')
        if doc:
            write(FAB / "decomposition.json", doc)
        write(log, ""); rm(comment, T / "add_fails")
        if fails:
            write(T / "add_fails", "")
        return sh(gate, path=path, env=env)

    r = cn_run('{"status":"no_work","summary":"Already done by #1426.\\nSecond line.","issues":[]}')
    ck("noop: exits 0", "0", r.rc)
    ck("noop: removes the receipt", "1", count("issue edit 1393 --remove-label agent-in-progress", file=log))
    ck("noop: keeps the generic sentence", "1", count("^Agent run found no actionable decomposition for this issue; leaving it for human triage.$", file=comment))
    ck("noop: names the decomposer", "1", count("^The decomposer reported:$", file=comment))
    ck("noop: quotes line one", "1", count("^> Already done by #1426\\.$", file=comment))
    ck("noop: quotes line two", "1", count("^> Second line\\.$", file=comment))
    ck("noop: labels agent-noop", "1", count("issue edit 1393 --add-label agent-noop", file=log))
    ck("noop: creates the label", "1", count("^label create agent-noop", file=log))
    ck("noop: comments from a file", "1", count("issue comment 1393 --body-file", file=log))

    r = cn_run('{"status":"no_work","summary":"","issues":[]}')
    ck("empty summary: exits 0", "0", r.rc)
    ck("empty summary: no quote block", "0", count("decomposer reported", file=comment))
    ck("empty summary: generic sentence", "1", count("^Agent run found no actionable", file=comment))
    ck("empty summary: still labels", "1", count("issue edit 1393 --add-label agent-noop", file=log))

    cn_run('{"status":"no_work","summary":"   ","issues":[]}')
    ck("blank summary: no quote block", "0", count("decomposer reported", file=comment))

    r = cn_run()
    ck("missing file: exits 0", "0", r.rc)
    ck("missing file: generic sentence", "1", count("^Agent run found no actionable", file=comment))
    ck("missing file: no quote block", "0", count("decomposer reported", file=comment))
    ck("missing file: still labels", "1", count("issue edit 1393 --add-label agent-noop", file=log))

    r = cn_run('{"status":"no_work","summary":"Done.","issues":[]}', fails=True)
    ck("label fails: exits 0", "0", r.rc)
    ck("label fails: still removes receipt", "1", count("remove-label agent-in-progress", file=log))
    ck("label fails: still comments", "1", count("issue comment 1393 --body-file", file=log))
