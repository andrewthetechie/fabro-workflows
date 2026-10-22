"""The merge-queue package (fast-path series, ADR 0024): prep, decide, inventory, act,
summarize and the merge-queue-summary Discord kind."""

from harness import (MQ, ORIG_PATH, WF, cat, count, extract, git, grep, jq, jq_n, jq_ok,
                     rebase, rm, routing_check, run, run_files, sh, stub, syntax_ok, tail1, write)

MQ_A = "a" * 40
MQ_B = "b" * 40
MQ_C = "c" * 40


def staged(ck, node, prefix, root, label):
    script = write(f"{prefix}.sh", rebase(extract(MQ, node), root))
    ok, err = syntax_ok(script)
    if not ok:
        ck.fail(f"{label} is not valid POSIX sh", err)
    return script


def last_route(prefix, filt):
    """`tail -1 prefix.out | jq -r filt`."""
    return jq(filt, tail1(f"{prefix}.out"), pipe=True)


# ---------------------------------------------------------------------------
# merge-queue — prep (fast-path series, task 05): the switches, the overlap guard and
# env.json. gh and curl are stubbed. The stubs read the automation rows, the active-run pages
# and the failure switches from the environment, so one script serves every case.
# ---------------------------------------------------------------------------
def test_merge_queue_prep(ck, tmp_path):
    stubs = tmp_path / "mq-stubs/bin"
    stub(stubs, "gh", """#!/bin/sh
case "$*" in
  "repo view --json nameWithOwner"*) echo andrewthetechie/jelly-swipe ;;
  "repo view --json defaultBranchRef"*) echo main ;;
  "api user"*) printf '%s\\n' "${MQ_LOGIN-fabro-bot}" ;;
  *) echo "gh $*" >&2 ;;
esac
exit 0
""")
    stub(stubs, "curl", """#!/bin/sh
for a in "$@"; do case "$a" in http*) URL="$a" ;; esac; done
case "$URL" in
  */automations) [ -n "${MQ_FAIL_AUTO:-}" ] && exit 22; cat "$MQ_AUTOROWS" ;;
  */runs?*) [ -n "${MQ_FAIL_RUNS:-}" ] && exit 22; cat "$MQ_RUNS" ;;
  *) exit 22 ;;
esac
""")

    def mq_run_row(run_id, repo, at, auto):
        """A run row as GET /runs returns it (spike S4)."""
        return jq_n('{id: $id, workflow: {slug: "merge-queue"}, repository: {name: $repo}, timestamps: {created_at: $at}, automation: (if $auto == "" then null else {id: $auto} end)}',
                    "--arg", "id", run_id, "--arg", "repo", f"andrewthetechie/{repo}", "--arg", "at", at, "--arg", "auto", auto)

    def case(name, description, auto_merge, runs, **extra):
        """description is the pr-review row's description, or "-" for no row."""
        d = tmp_path / f"mq-{name}"
        rm(d, f"{d}.sh"); (d / "mq").mkdir(parents=True)
        git("-C", d, "init", "-q", "-b", "fabro/run/01RUNME")
        git("-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        rows = write(f"{d}.rows.json", '{"data":[]}' if description == "-" else
                     f'{{"data":[{{"id":"pr-review-jelly-swipe","workflow":"pr-review","target":{{"repo":"andrewthetechie/jelly-swipe"}},"description":{description}}}]}}')
        runs_f = write(f"{d}.runs.json", f'{{"data":{runs},"meta":{{"has_more":false}}}}')
        script = staged(ck, "prep", d, d, "merge-queue prep")
        run_files(["sh", script], d, cwd=d, path=f"{stubs}:{ORIG_PATH}",
                  env={"MQ_AUTOROWS": rows, "MQ_RUNS": runs_f, "FABRO_API_URL": "http://fabro.test/api/v1",
                       "FABRO_API_TOKEN": "test-token", "FABRO_AUTO_MERGE": auto_merge, **extra})
        return d

    sweep = lambda d: last_route(d, ".context_updates.sweep")
    none = "[]"

    # 1. Both switches on, no other run.
    d = case("on", '""', "1", none)
    routed = routing_check(ck, "merge-queue prep, both on", d, ".context_updates.sweep")
    ck("merge-queue prep: both on: sweep=run", "run", routed)
    ck("merge-queue prep: env.json has every key", "base,login,merge_cap,now,repo,run", jq('keys | join(",")', file=d / "mq/env.json"))
    ck("merge-queue prep: env.json values", "main fabro-bot 01RUNME andrewthetechie/jelly-swipe 5",
       jq('[.base, .login, .run, .repo, (.merge_cap|tostring)] | join(" ")', file=d / "mq/env.json"))
    ck("merge-queue prep: env.json now is a number", "number", jq(".now | type", file=d / "mq/env.json"))
    ck("merge-queue prep: no skip_reason", "0", "1" if (d / "mq/skip_reason").exists() else "0")

    # 2. The host switch is off.
    d = case("host-off", '""', "0", none)
    routed = routing_check(ck, "merge-queue prep, host off", d, ".context_updates.sweep")
    ck("merge-queue prep: FABRO_AUTO_MERGE=0: skip", "skip", routed)
    ck("merge-queue prep: FABRO_AUTO_MERGE=0: reason names the host switch", "1", count("host-wide", file=d / "mq/skip_reason"))
    d = case("host-unset", '""', "", none)
    ck("merge-queue prep: FABRO_AUTO_MERGE empty: skip", "skip", sweep(d))

    # 3. The pr-review row.
    d = case("row-off", '"auto_merge=false"', "1", none)
    ck("merge-queue prep: auto_merge=false: skip", "skip", sweep(d))
    ck("merge-queue prep: auto_merge=false: reason names the pr-review row", "1", count("pr-review-jelly-swipe row", file=d / "mq/skip_reason"))
    ck("merge-queue prep: no token: run", "run", sweep(case("row-notoken", '"just a description"', "1", none)))
    ck("merge-queue prep: no row at all: run (an absent token means on, as in pr_handoff)", "run", sweep(case("row-missing", "-", "1", none)))
    ck("merge-queue prep: auto_merge=true: run", "run", sweep(case("row-true", '"x auto_merge=true"', "1", none)))
    ck("merge-queue prep: automations API fails: skip", "skip", sweep(case("api-down", '"auto_merge=true"', "1", none, MQ_FAIL_AUTO="1")))

    # 4. The overlap guard. This run is 01RUNME, created 10:05.
    this = mq_run_row("01RUNME", "jelly-swipe", "2026-10-12T10:05:00.000Z", "merge-queue-jelly-swipe")
    older = mq_run_row("01OLDER", "jelly-swipe", "2026-10-12T09:55:00.000Z", "merge-queue-jelly-swipe")
    d = case("older", '""', "1", f"[{this},{older}]")
    ck("merge-queue prep: older run, same repo: skip", "skip", sweep(d))
    ck("merge-queue prep: older run: the reason names its id", "an older merge-queue run (01OLDER) is still active", cat(d / "mq/skip_reason"))
    other = mq_run_row("01OTHER", "writers-app", "2026-10-12T09:55:00.000Z", "merge-queue-writers-app")
    ck("merge-queue prep: older run, another repo: run", "run", sweep(case("other-repo", '""', "1", f"[{this},{other}]")))
    newer = mq_run_row("01NEWER", "jelly-swipe", "2026-10-12T10:15:00.000Z", "merge-queue-jelly-swipe")
    ck("merge-queue prep: newer run, same repo: run", "run", sweep(case("newer", '""', "1", f"[{this},{newer}]")))
    backlog = jq_n('{id: "01BACK", workflow: {slug: "backlog"}, repository: {name: "andrewthetechie/jelly-swipe"}, timestamps: {created_at: "2026-10-12T09:00:00.000Z"}, automation: null}')
    ck("merge-queue prep: an older backlog run does not count: run", "run", sweep(case("backlog", '""', "1", f"[{this},{backlog}]")))
    ck("merge-queue prep: this run alone: run", "run", sweep(case("self-only", '""', "1", f"[{this}]")))
    ck("merge-queue prep: this run not listed, another active: skip (cannot order, so do not act)", "skip", sweep(case("no-self", '""', "1", f"[{newer}]")))
    d = case("runs-down", '""', "1", none, MQ_FAIL_RUNS="1")
    ck("merge-queue prep: GET /runs fails: skip", "skip", sweep(d))
    ck("merge-queue prep: GET /runs fails: reason", "could not list active runs", cat(d / "mq/skip_reason"))

    # 5. A missing fact stops the sweep.
    d = case("no-login", '""', "1", none, MQ_LOGIN="")
    ck("merge-queue prep: no token login: exit 1", "1", cat(f"{d}.rc"))
    ck("merge-queue prep: no token login: stop_reason written", "prep could not read the token login", cat(d / "mq/stop_reason"))

    # 6. The graph shape: prep's two edges are exclusive on sweep, and every node reaches summarize.
    ck("merge-queue: prep -> inventory needs sweep=run", "1", count("prep -> inventory .*context.sweep=run", file=MQ))
    ck("merge-queue: prep has an unconditional edge to summarize", "1", count("^    prep -> summarize$", file=MQ))


# ---------------------------------------------------------------------------
# merge-queue — decide and inventory (fast-path series, task 06). decide is pure jq: every row
# of 00 C8 is a fixture. inventory runs with gh stubbed, so norm.jq, the marker filter, the
# check dedupe and the attempt lookup are tested on a GraphQL response shaped like the live one.
# ---------------------------------------------------------------------------
MQ_BASE = ('{id: "", title: "fix: a thing", head: "' + MQ_A + '", draft: false, merge_state: "CLEAN", review_decision: null,\n'
           '  labels: ["agent-authored", "merge-on-green"], unresolved_threads: 0, body_block: "Resolves #5",\n'
           '  markers: [{kind: "approved", head: "' + MQ_A + '", at: "2026-10-12T10:00:00Z"}],\n'
           '  checks: {total: 1, fail: [], pending: [], pass: 1, skip: 0}}')


def mq_pr(n, filt="."):
    """A PR record: the default good PR with filt applied."""
    return jq_n(f'{MQ_BASE} | .number = $n | .id = "PR_" + ($n | tostring) | {filt}', "--argjson", "n", str(n))


def arr(*prs):
    return "[" + ",".join(prs) + "]"


def test_merge_queue_decide(ck, tmp_path):
    now = jq_n('"2026-10-12T12:00:00Z" | fromdateiso8601')
    st = {}

    def dec(name, prs):
        d = tmp_path / f"mqd-{name}"
        rm(d, f"{d}.sh"); (d / "mq").mkdir(parents=True)
        write(d / "mq/prs.json", prs)
        write(d / "mq/env.json", jq_n('{repo: "andrewthetechie/jelly-swipe", base: "main", login: "fabro-bot", run: "01R", now: $now, merge_cap: 5}', "--argjson", "now", now, compact=False) + "\n")
        script = staged(ck, "decide", d, d, "merge-queue decide")
        run_files(["sh", script], d, cwd=d)
        st["d"] = d
        return d

    plan = lambda: jq('[.[] | (.number | tostring) + ":" + .action] | join(",")', file=st["d"] / "mq/plan.json")
    field = lambda filt: jq(filt, file=st["d"] / "mq/plan.json")
    counts = '.context_updates | [.plan_merge, .plan_rebase, .plan_rerun, .plan_handoff, .plan_wait] | join(",")'
    head_is = lambda h: '.head = "' + h + '"'
    rebased = lambda frm, to, at: '{kind: "rebased", from: "' + frm + '", to: "' + to + '", at: "' + at + '"}'

    d = dec("good", arr(mq_pr(1)))
    routed = routing_check(ck, "merge-queue decide, one good PR", d, counts)
    ck("merge-queue decide: 1. approved head, green, CLEAN: merge", "1:merge", plan())
    ck("merge-queue decide: routing counts", "1,0,0,0,0", routed)
    ck("merge-queue decide: plan record shape", "action,approved_at,head,number,reason,rerun_ids,retryable,subject", field('.[0] | keys | join(",")'))

    dec("noappr", arr(mq_pr(1, ".markers = []")))
    ck("merge-queue decide: 2. no approval marker: handoff", "1:handoff", plan())
    ck("merge-queue decide: 2. its reason is not retryable", "the PR carries merge-on-green but no approval from the merge phase|false", field('.[0] | .reason + "|" + (.retryable | tostring)'))
    dec("moved", arr(mq_pr(1, head_is(MQ_B))))
    ck("merge-queue decide: 4. head differs from the approved head: handoff", "1:handoff", plan())
    ck("merge-queue decide: 4. reason says the head moved", "1", field('.[0].reason | startswith("the head moved") | if . then 1 else 0 end'))
    rb = rebased(MQ_A, MQ_B, "2026-10-12T10:20:00Z")
    dec("rebased", arr(mq_pr(1, head_is(MQ_B) + " | .markers += [" + rb + "]")))
    ck("merge-queue decide: 5. approved A, rebased A to B, head B: merge", "1:merge", plan())
    dec("rebased-c", arr(mq_pr(1, head_is(MQ_C) + " | .markers += [" + rb + "]")))
    ck("merge-queue decide: 6. approved A, rebased A to B, head C: handoff", "1:handoff", plan())
    dec("rebased-other", arr(mq_pr(1, head_is(MQ_B) + " | .markers += [" + rebased(MQ_C, MQ_B, "2026-10-12T10:20:00Z") + "]")))
    ck("merge-queue decide: 6. a rebase marker whose from is not the chain's link is not a link: handoff", "1:handoff", plan())
    dec("twice", arr(mq_pr(1, head_is(MQ_B) + ' | .markers = [{kind: "approved", head: "' + MQ_A + '", at: "2026-10-12T09:00:00Z"}, {kind: "approved", head: "' + MQ_B + '", at: "2026-10-12T10:00:00Z"}]')))
    ck("merge-queue decide: 7. a newer approval supersedes the older one: merge", "1:merge", plan())

    for name, filt in (("draft", ".draft = true"), ("do-not-merge", '.labels += ["do-not-merge"]'),
                       ("needs-human", '.labels += ["ai-review-needs-human"]'), ("no-agent", '.labels = ["merge-on-green"]'),
                       ("changes", '.review_decision = "CHANGES_REQUESTED"'), ("thread", ".unresolved_threads = 1"),
                       ("title", '.title = "Fix the thing"'), ("no-resolves", '.body_block = "just text"'),
                       ("empty-block", '.body_block = ""')):
        dec(f"h-{name}", arr(mq_pr(1, filt)))
        ck(f"merge-queue decide: 8/9. {name}: handoff, not retryable", "1:handoff|false", plan() + "|" + field(".[0].retryable"))

    dec("arch", arr(mq_pr(1, '.labels += ["architecture"]')))
    ck("merge-queue decide: 2b. architecture alone does not hold: merge", "1:merge", plan())

    dec("dirty", arr(mq_pr(1, '.merge_state = "DIRTY"')))
    ck("merge-queue decide: 10. DIRTY: retryable handoff naming the base", "1:handoff|retryable: the PR conflicts with main|true",
       plan() + "|" + field(".[0].reason") + "|" + field(".[0].retryable"))
    dec("unknown", arr(mq_pr(1, '.merge_state = "UNKNOWN"')))
    ck("merge-queue decide: 11. UNKNOWN: wait", "1:wait", plan())

    bh = '.merge_state = "BEHIND"'
    at = lambda t: ' | .markers[0].at = "' + t + '"'
    dec("behind2", arr(mq_pr(2, bh + at("2026-10-12T10:05:00Z")), mq_pr(1, bh)))
    ck("merge-queue decide: 12. two BEHIND: the older rebases, the newer waits", "1:rebase,2:wait", plan())
    ck("merge-queue decide: 12. the waiting one says why", "another rebase is waiting for CI", field(".[1].reason"))
    fl = mq_pr(3, head_is(MQ_C) + " | .markers += [" + rebased(MQ_A, MQ_C, "2026-10-12T11:50:00Z") + "]" + at("2026-10-12T10:10:00Z") + ' | .checks.pending = ["test"] | .checks.pass = 0')
    dec("inflight", arr(mq_pr(1, bh), mq_pr(2, bh + at("2026-10-12T10:05:00Z")), fl))
    ck("merge-queue decide: 13. a rebase is in flight: both BEHIND wait, and so does the pending one", "1:wait,2:wait,3:wait", plan())

    dec("red1", arr(mq_pr(1, '.checks.fail = [{name: "test", run_id: 123, attempt: 1}] | .checks.pass = 0')))
    ck("merge-queue decide: 14. red, Actions, attempt 1: rerun with its run id", "1:rerun|[123]", plan() + "|" + field(".[0].rerun_ids | tojson"))
    dec("red2", arr(mq_pr(1, '.checks.fail = [{name: "test", run_id: 123, attempt: 2}] | .checks.pass = 0')))
    ck("merge-queue decide: 14. red, attempt 2: retryable handoff after one rerun", "1:handoff|retryable: CI failed after one rerun: test", plan() + "|" + field(".[0].reason"))
    dec("red3", arr(mq_pr(1, '.checks.fail = [{name: "ext", run_id: null, attempt: null}] | .checks.pass = 0')))
    ck("merge-queue decide: 14. red, not an Actions run: handoff, not retryable", "1:handoff|CI failed and the failing check cannot be rerun: ext|false",
       plan() + "|" + field(".[0].reason") + "|" + field(".[0].retryable"))
    dec("red4", arr(mq_pr(1, '.checks.fail = [{name: "a", run_id: 1, attempt: 1}, {name: "b", run_id: 2, attempt: 1}, {name: "c", run_id: 1, attempt: 1}] | .checks.pass = 0')))
    ck("merge-queue decide: 14. several failing checks: one rerun per unique run id", "1:rerun|[1,2]", plan() + "|" + field(".[0].rerun_ids | tojson"))
    dec("red5", arr(mq_pr(1, '.checks.fail = [{name: "a", run_id: 1, attempt: null}] | .checks.pass = 0')))
    ck("merge-queue decide: 14. attempt unknown (the lookup failed): cannot be rerun", "1:handoff|false", plan() + "|" + field(".[0].retryable"))

    pending = '.checks.pending = ["test"] | .checks.pass = 0'
    dec("pend-new", arr(mq_pr(1, pending + at("2026-10-12T10:01:00Z"))))
    ck("merge-queue decide: 15. pending, marker 1h59m old: wait", "1:wait", plan())
    dec("pend-old", arr(mq_pr(1, pending + at("2026-10-12T09:59:00Z"))))
    ck("merge-queue decide: 15. pending, marker 2h01m old: retryable handoff", "1:handoff|retryable: CI on the head did not settle in 2 hours", plan() + "|" + field(".[0].reason"))
    dec("pend-chain", arr(mq_pr(1, head_is(MQ_B) + " | " + pending + " | .markers += [" + rebased(MQ_A, MQ_B, "2026-10-12T11:30:00Z") + "]")))
    ck("merge-queue decide: 15. the pending clock starts at the newest marker in the chain: wait", "1:wait", plan())

    nochecks = ".checks = {total: 0, fail: [], pending: [], pass: 0, skip: 0}"
    dec("nochecks", arr(mq_pr(1, nochecks + at("2026-10-12T11:50:00Z"))))
    ck("merge-queue decide: 16. no checks yet, marker 10 minutes old: wait", "1:wait", plan())
    dec("unstable-none", arr(mq_pr(1, nochecks + ' | .merge_state = "UNSTABLE"')))
    ck("merge-queue decide: spike S1. no checks and UNSTABLE: handoff", "1:handoff", plan())

    dec("blocked", arr(mq_pr(1, '.merge_state = "BLOCKED"')))
    ck("merge-queue decide: 17. green and BLOCKED: handoff naming the status", "1:handoff|branch protection or a hidden check blocks the merge: BLOCKED", plan() + "|" + field(".[0].reason"))
    dec("unstable", arr(mq_pr(1, '.merge_state = "UNSTABLE"')))
    ck("merge-queue decide: 17. green and UNSTABLE: handoff", "1:handoff", plan())
    dec("hooks", arr(mq_pr(1, '.merge_state = "HAS_HOOKS"')))
    ck("merge-queue decide: 17. green and HAS_HOOKS: merge", "1:merge", plan())
    dec("skips", arr(mq_pr(1, ".checks = {total: 2, fail: [], pending: [], pass: 1, skip: 1}")))
    ck("merge-queue decide: skipped checks do not block: merge", "1:merge", plan())

    seven = run(["jq", "-cs", "."], stdin="\n".join(mq_pr(n, '.markers[0].at = "2026-10-12T10:0' + str(n) + ':00Z"') for n in (7, 3, 1, 5, 2, 6, 4)) + "\n").out
    dec("cap", seven)
    ck("merge-queue decide: 18. seven green PRs: the five oldest merge, the other two wait", "1:merge,2:merge,3:merge,4:merge,5:merge,6:wait,7:wait", plan())
    ck("merge-queue decide: 18. the cap is the reason", "merge cap of 5 per tick reached", field(".[5].reason"))
    dec("cap-hand", arr(mq_pr(1, ".markers = []"), mq_pr(2)))
    ck("merge-queue decide: a handoff does not use up the cap, and is listed first (no approval time)", "1:handoff,2:merge", plan())

    d = dec("empty", "[]")
    routed = routing_check(ck, "merge-queue decide, no PRs", d, counts)
    ck("merge-queue decide: 19. no PRs: []", "[]", jq(".", file=d / "mq/plan.json", args=["-c"], raw=False))
    ck("merge-queue decide: 19. every count is 0", "0,0,0,0,0", routed)
    d = dec("garbage", "not json")
    ck("merge-queue decide: an unreadable inventory exits 1 and writes stop_reason", "1|decide: the decision program failed", cat(f"{d}.rc") + "|" + cat(d / "mq/stop_reason"))
    ck("merge-queue: decide declares routing; inventory does not", "1|0",
       count('decide \\[.*output_schema="routing"', file=MQ) + "|" + count("inventory \\[.*output_schema", file=MQ))


def test_merge_queue_inventory(ck, tmp_path):
    stubs = tmp_path / "mqi-stubs/bin"
    stub(stubs, "gh", """#!/bin/sh
case "$1 $2" in
  "api graphql")
      N=$(cat "$MQI_STATE/calls" 2>/dev/null || echo 0); N=$((N + 1)); echo "$N" > "$MQI_STATE/calls"
      [ -n "${MQI_FAIL:-}" ] && exit 1
      if [ -f "$MQI_GQL.$N" ]; then cat "$MQI_GQL.$N"; else cat "$MQI_GQL"; fi ;;
  "run view")
      case "$3" in 111) echo 1 ;; 222) echo 2 ;; *) exit 1 ;; esac ;;
  *) echo "gh $*" >&2 ;;
esac
""")
    stub(stubs, "sleep", "#!/bin/sh\nexit 0\n")
    st = {}

    def inv(name, gql, **extra):
        d = tmp_path / f"mqi-{name}"
        rm(d, f"{d}.sh"); (d / "mq").mkdir(parents=True); (d / "state").mkdir()
        write(d / "mq/env.json", jq_n('{repo: "andrewthetechie/jelly-swipe", base: "main", login: "fabro-bot", run: "01R", now: 1, merge_cap: 5}', compact=False) + "\n")
        script = staged(ck, "inventory", d, d, "merge-queue inventory")
        run_files(["sh", script], d, cwd=d, path=f"{stubs}:{ORIG_PATH}", env={"MQI_STATE": d / "state", "MQI_GQL": gql, **extra})
        st["d"] = d
        return d

    def comment(login, body, at):
        return jq_n("{author: {login: $l}, body: $b, createdAt: $at}", "--arg", "l", login, "--arg", "b", body, "--arg", "at", at)

    def pr(n, ms, comments, rollup):
        return jq_n('{number: $n, id: ("PR_" + ($n | tostring)), title: "fix: a thing", body: "intro\\n<!-- fabro:commit-body:start -->\\nfix: a thing\\n\\nResolves #5\\n<!-- fabro:commit-body:end -->\\nfooter",'
                    ' isDraft: false, headRefOid: "' + MQ_A + '", mergeable: "MERGEABLE", mergeStateStatus: $ms, reviewDecision: null,'
                    ' labels: {nodes: [{name: "agent-authored"}, {name: "merge-on-green"}]},'
                    ' reviewThreads: {nodes: [{isResolved: true}, {isResolved: false}]},'
                    ' comments: {nodes: $cm},'
                    ' commits: {nodes: [{commit: {statusCheckRollup: (if $ru == null then null else {state: "FAILURE", contexts: {nodes: $ru}} end)}}]}}',
                    "--argjson", "n", str(n), "--arg", "ms", ms, "--argjson", "cm", comments, "--argjson", "ru", rollup)

    def resp(nodes, more="false"):
        return jq_n("{data: {repository: {pullRequests: {pageInfo: {hasNextPage: $more, endCursor: \"CUR\"}, nodes: $nodes}}}}",
                    "--argjson", "nodes", nodes, "--argjson", "more", more) + "\n"

    cms = jq_n("[$a, $b, $c, $d, $e]",
               "--argjson", "a", comment("fabro-bot", f"<!-- fabro:merge-queue approved head={MQ_A} run=01X -->\n## AI PR review complete", "2026-10-12T10:00:00Z"),
               "--argjson", "b", comment("someone-else", f"<!-- fabro:merge-queue approved head={MQ_B} run=01Y -->", "2026-10-12T10:01:00Z"),
               "--argjson", "c", comment("fabro-bot", f"<!-- fabro:merge-queue rebased from={MQ_A} to={MQ_B} run=01Z -->", "2026-10-12T10:30:00Z"),
               "--argjson", "d", comment("fabro-bot", f"<!-- fabro:merge-queue handoff head={MQ_B} run=01W -->\nstopped", "2026-10-12T10:40:00Z"),
               "--argjson", "e", comment("fabro-bot", f"approved head={MQ_A}, but only in prose", "2026-10-12T10:50:00Z"))
    ru = '''[{"__typename":"CheckRun","name":"test","status":"COMPLETED","conclusion":"FAILURE","startedAt":"2026-10-12T10:00:00Z","checkSuite":{"workflowRun":{"databaseId":999}}},
     {"__typename":"CheckRun","name":"test","status":"COMPLETED","conclusion":"SUCCESS","startedAt":"2026-10-12T10:10:00Z","checkSuite":{"workflowRun":{"databaseId":998}}},
     {"__typename":"CheckRun","name":"lint","status":"COMPLETED","conclusion":"FAILURE","startedAt":"2026-10-12T10:10:00Z","checkSuite":{"workflowRun":{"databaseId":222}}},
     {"__typename":"CheckRun","name":"unit","status":"COMPLETED","conclusion":"FAILURE","startedAt":"2026-10-12T10:10:00Z","checkSuite":{"workflowRun":{"databaseId":111}}},
     {"__typename":"CheckRun","name":"ext","status":"COMPLETED","conclusion":"TIMED_OUT","startedAt":"2026-10-12T10:10:00Z","checkSuite":{"workflowRun":null}},
     {"__typename":"CheckRun","name":"slow","status":"IN_PROGRESS","conclusion":null,"startedAt":"2026-10-12T10:10:00Z","checkSuite":{"workflowRun":{"databaseId":5}}},
     {"__typename":"CheckRun","name":"docs","status":"COMPLETED","conclusion":"SKIPPED","startedAt":"2026-10-12T10:10:00Z","checkSuite":{"workflowRun":{"databaseId":6}}},
     {"__typename":"StatusContext","context":"ci/x","state":"PENDING","createdAt":"2026-10-12T10:10:00Z"},
     {"__typename":"StatusContext","context":"ci/y","state":"ERROR","createdAt":"2026-10-12T10:10:00Z"}]'''
    main = write(tmp_path / "mqi-main.json", resp(f"[{pr(11, 'BEHIND', cms, ru)},{pr(12, 'UNSTABLE', '[]', 'null')}]"))
    d = inv("main", main)
    P = d / "mq/prs.json"
    q = lambda filt: jq(filt, file=P)
    ck("merge-queue inventory: exits 0 and reads two PRs", "0|2", cat(f"{d}.rc") + "|" + jq("length", file=P, raw=False))
    ck("merge-queue inventory: record keys", "body_block,checks,draft,head,id,labels,markers,merge_state,number,review_decision,title,unresolved_threads", q('.[0] | keys | join(",")'))
    ck("merge-queue inventory: only the token's own marker comments count (not another login, a handoff or prose)", f"approved:{MQ_A},rebased:{MQ_B}",
       q('.[0].markers | map(.kind + ":" + (.head // .to)) | join(",")'))
    ck("merge-queue inventory: markers keep the comment time", "2026-10-12T10:00:00Z", q(".[0].markers[0].at"))
    ck("merge-queue inventory: body_block, threads, labels, state", "fix: a thing\n\nResolves #5|1|agent-authored,merge-on-green|BEHIND",
       q('.[0] | .body_block + "|" + (.unresolved_threads | tostring) + "|" + (.labels | join(",")) + "|" + .merge_state'))
    ck("merge-queue inventory: a check name is judged by its newest run (test failed, then passed: pass)", "1",
       jq('[.[0].checks.fail[].name] | index("test") | if . == null then 1 else 0 end', file=P, raw=False))
    ck("merge-queue inventory: failing checks, with run ids", "ci/y:null,ext:null,lint:222,unit:111", q('.[0].checks.fail | sort_by(.name) | map(.name + ":" + (.run_id | tostring)) | join(",")'))
    ck("merge-queue inventory: attempts come from gh run view; a failed lookup and a non-Actions check leave null", "ci/y:null,ext:null,lint:2,unit:1",
       q('.[0].checks.fail | sort_by(.name) | map(.name + ":" + (.attempt | tostring)) | join(",")'))
    ck("merge-queue inventory: pending, pass, skip, total", "ci/x,slow|1|1|8",
       q('.[0].checks | (.pending | sort | join(",")) + "|" + (.pass | tostring) + "|" + (.skip | tostring) + "|" + (.total | tostring)'))
    ck("merge-queue inventory: a null rollup is zero checks", "0|UNSTABLE", q(".[1].checks.total | tostring") + "|" + q(".[1].merge_state"))
    ck("merge-queue inventory: no stdout or stderr line has a brace", "0", count("[{}]", (d.parent / f"{d.name}.out").read_text() + (d.parent / f"{d.name}.err").read_text(), pipe=True))

    unk = write(tmp_path / "mqi-unk.json", resp(f"[{pr(20, 'UNKNOWN', '[]', 'null')}]"))
    write(tmp_path / "mqi-unk.json.2", resp(f"[{pr(20, 'CLEAN', '[]', 'null')}]"))
    d = inv("unknown", unk)
    ck("merge-queue inventory: UNKNOWN is re-read, and the answer replaces it", "CLEAN|2", jq(".[0].merge_state", file=d / "mq/prs.json") + "|" + cat(d / "state/calls"))
    stuck = write(tmp_path / "mqi-stuck.json", resp(f"[{pr(21, 'UNKNOWN', '[]', 'null')}]"))
    d = inv("stuck", stuck)
    ck("merge-queue inventory: a PR that stays UNKNOWN is re-read 3 times, then passed on as UNKNOWN", "0|UNKNOWN|4",
       cat(f"{d}.rc") + "|" + jq(".[0].merge_state", file=d / "mq/prs.json") + "|" + cat(d / "state/calls"))
    page = write(tmp_path / "mqi-page.json", resp(f"[{pr(30, 'CLEAN', '[]', 'null')}]", "true"))
    write(tmp_path / "mqi-page.json.2", resp(f"[{pr(31, 'CLEAN', '[]', 'null')}]"))
    d = inv("paged", page)
    ck("merge-queue inventory: a second page is read and appended", "30,31", jq('map(.number | tostring) | join(",")', file=d / "mq/prs.json"))
    nonef = write(tmp_path / "mqi-none.json", resp("[]"))
    d = inv("none", nonef)
    ck("merge-queue inventory: zero PRs is []", "0|[]", cat(f"{d}.rc") + "|" + jq(".", file=d / "mq/prs.json", args=["-c"], raw=False))
    d = inv("fail", nonef, MQI_FAIL="1")
    ck("merge-queue inventory: a failed query exits 1 and writes stop_reason", "1|inventory: could not read the merge-on-green PRs", cat(f"{d}.rc") + "|" + cat(d / "mq/stop_reason"))
    bad = write(tmp_path / "mqi-bad.json", '{"data":null}\n')
    d = inv("bad", bad)
    ck("merge-queue inventory: a response without the PR list exits 1", "1", cat(f"{d}.rc"))
    ck("merge-queue: inventory -> decide needs success; both reach summarize", "1|1|1",
       count("^    inventory -> decide .*outcome=succeeded", file=MQ) + "|" + count("^    inventory -> summarize$", file=MQ) + "|" + count("^    decide -> summarize$", file=MQ))


# ---------------------------------------------------------------------------
# merge-queue — act (fast-path series, task 07): each action of plan.json through a gh stub
# that answers from per-case fixture files and logs every call (and the first line of every
# comment).
# ---------------------------------------------------------------------------
ACT_GH = """#!/bin/sh
S="$MQA_STUB"
echo "gh $*" >> "$S/calls.log"
bodyfile() { while [ $# -gt 0 ]; do [ "$1" = "--body-file" ] && { echo "$2"; return; }; shift; done; }
case "$1 $2" in
  "pr view")
    case "$*" in
      *headRefOid\\ --jq*) cat "$S/newhead" 2>/dev/null ;;
      *) cat "$S/live.json" ;;
    esac ;;
  "pr merge")
    n=$(cat "$S/merge.count" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$S/merge.count"
    f="$S/merge.$n"; [ -f "$f" ] || f="$S/merge.last"
    rc=$(cut -d'|' -f1 "$f"); cut -d'|' -f2- "$f" >&2; exit "$rc" ;;
  "pr comment")
    b=$(bodyfile "$@"); if [ -n "$b" ]; then echo "COMMENT $(head -n 1 "$b")" >> "$S/calls.log"
    else while [ $# -gt 0 ]; do [ "$1" = "--body" ] && echo "COMMENT $2" >> "$S/calls.log"; shift; done; fi
    [ -f "$S/comment_fail" ] && exit 1; exit 0 ;;
  "pr edit") [ -f "$S/edit_fail" ] && { echo "label failed" >&2; exit 1; }; exit 0 ;;
  "issue edit") exit 0 ;;
  "run rerun") for a in "$@"; do [ -f "$S/rerun_fail.$a" ] && { echo "run $a cannot be rerun" >&2; exit 1; }; done; exit 0 ;;
  "api graphql") [ -f "$S/gql_err" ] && { cat "$S/gql_err" >&2; exit 1; }; echo '{"data":{"updatePullRequestBranch":{"pullRequest":{"headRefOid":"OLD"}}}}'; exit 0 ;;
  *) echo "unexpected gh $*" >&2; exit 1 ;;
esac
exit 0
"""
OLD = "1" * 40
NEW = "2" * 40


def test_merge_queue_act(ck, tmp_path):
    stubs = tmp_path / "mqa-stubs/bin"
    stub(stubs, "gh", ACT_GH)
    st = {}

    def setup(name, plan):
        d = tmp_path / f"mqa-{name}"
        s = tmp_path / f"mqa-{name}.stub"
        rm(d, s, f"{d}.sh"); (d / "mq").mkdir(parents=True); s.mkdir()
        write(d / "mq/plan.json", plan)
        write(d / "mq/prs.json", jq_n('[{number: 7, id: "PR_7"}, {number: 8, id: "PR_8"}, {number: 9, id: "PR_9"}]') + "\n")
        write(d / "mq/env.json", jq_n('{repo: "andrewthetechie/jelly-swipe", base: "main", login: "fabro-bot", run: "01RUN", now: 0, merge_cap: 5}', compact=False) + "\n")
        st.update(d=d, s=s, script=staged(ck, "act", d, d, "merge-queue act"))
        return d, s

    def act(**extra):
        run_files(["sh", st["script"]], st["d"], cwd=st["d"], path=f"{stubs}:{ORIG_PATH}",
                  env={"MQA_STUB": st["s"], "MQ_REREAD_WAIT": "0", "MQ_MARKER_WAIT": "0", "MQ_RETRY_WAIT": "0", **extra})

    def plan(n, h, a, r="", ids="[]"):
        return jq_n('[{number: $n, head: $h, action: $a, reason: $r, retryable: false, approved_at: "", subject: "s", rerun_ids: $ids}]',
                    "--argjson", "n", str(n), "--arg", "h", h, "--arg", "a", a, "--arg", "r", r, "--argjson", "ids", ids)

    def live(state, head, mstate, title):
        write(st["s"] / "live.json", jq_n('{state: $s, headRefOid: $h, mergeStateStatus: $m, title: $t, body: "intro\\n<!-- fabro:commit-body:start -->\\nDid the thing.\\n\\nResolves #5\\n<!-- fabro:commit-body:end -->\\n"}',
                                          "--arg", "s", state, "--arg", "h", head, "--arg", "m", mstate, "--arg", "t", title) + "\n")

    outcomes = lambda: jq('[.[] | .outcome] | join(",")', file=st["d"] / "mq/results.json")
    calls = lambda pat: count(pat, file=st["s"] / "calls.log")
    detail = lambda: jq(".[0].detail", file=st["d"] / "mq/results.json")
    log = lambda: st["s"] / "calls.log"

    # 1. merge, unchanged PR.
    d, s = setup("m1", plan(7, OLD, "merge")); live("OPEN", OLD, "CLEAN", "fix: a thing"); write(s / "merge.last", "0|\n")
    act()
    ck("merge-queue act: 1. exit 0 and one merged result", "0|merged", cat(f"{d}.rc") + "|" + outcomes())
    ck("merge-queue act: 1. one gh pr merge with --match-head-commit, --squash, --delete-branch=false, the live title", "1",
       count(f"^gh pr merge 7 --squash --delete-branch=false --match-head-commit {OLD} --subject fix: a thing --body-file", file=log()))
    ck("merge-queue act: 1. no --admin", "0", calls(" --admin"))
    ck("merge-queue act: 1. the merge body is the commit-body block", "Did the thing.", (cat(d / "mq/block_7.md").split("\n") or [""])[0])
    ck("merge-queue act: 1. the resolved issue loses Review, then the comment",
       "gh issue edit 5 --remove-label Review|COMMENT Merged by the merge queue (run 01RUN): checks green on 1111111.",
       grep(["-E", "^gh issue edit|^COMMENT"], file=log()).replace("\n", "|"))

    # 2. merge, already MERGED.
    setup("m2", plan(7, OLD, "merge")); live("MERGED", OLD, "CLEAN", "fix: a thing")
    act()
    ck("merge-queue act: 2. MERGED: merged, already merged, no merge call", "merged|already merged|0", outcomes() + "|" + detail() + "|" + calls("gh pr merge"))
    # 3. merge, new head.
    setup("m3", plan(7, OLD, "merge")); live("OPEN", NEW, "CLEAN", "fix: a thing")
    act()
    ck("merge-queue act: 3. a new head: waiting, no merge call", "waiting|0", outcomes() + "|" + calls("gh pr merge"))
    # 4. merge, BEHIND.
    setup("m4", plan(7, OLD, "merge")); live("OPEN", OLD, "BEHIND", "fix: a thing")
    act()
    ck("merge-queue act: 4. BEHIND: waiting with the status, no merge call", "waiting|1|0",
       outcomes() + "|" + jq('.[0].detail | contains("BEHIND") | if . then 1 else 0 end', file=st["d"] / "mq/results.json") + "|" + calls("gh pr merge"))
    # 5. merge, retry on Base branch was modified.
    d, s = setup("m5", plan(7, OLD, "merge")); live("OPEN", OLD, "CLEAN", "fix: a thing")
    write(s / "merge.1", "1|Base branch was modified. Review and try the merge again.\n"); write(s / "merge.last", "0|\n")
    act()
    ck("merge-queue act: 5. Base branch was modified, then success: merged with 2 merge calls", "merged|2", outcomes() + "|" + calls("gh pr merge"))
    # 6. merge, other failure.
    d, s = setup("m6", plan(7, OLD, "merge")); live("OPEN", OLD, "CLEAN", "fix: a thing"); write(s / "merge.last", "1|X is blocked by policy\n")
    act()
    ck("merge-queue act: 6. another merge error: failed, 1 merge call, no label change, no hand-off", "failed|1|0|0",
       outcomes() + "|" + calls("gh pr merge") + "|" + calls("remove-label") + "|" + calls("gh pr edit"))
    ck("merge-queue act: 6. the detail is the last gh line", "X is blocked by policy", detail())
    # 6b. merge, live title is not a Conventional Commit: hand-off, no merge.
    setup("m7", plan(7, OLD, "merge")); live("OPEN", OLD, "CLEAN", "a thing")
    act()
    ck("merge-queue act: 6b. a bad live title: handed_off, no merge call", "handed_off|0", outcomes() + "|" + calls("gh pr merge"))

    # 7. rebase success.
    d, s = setup("r1", plan(8, OLD, "rebase")); write(s / "newhead", NEW + "\n")
    act()
    ck("merge-queue act: 7. rebase: rebased, detail old -> new", "rebased|1111111 -> 2222222", outcomes() + "|" + detail())
    ck("merge-queue act: 7. the C5 marker has from= and to= (the re-read head)", "1", count(f"^COMMENT <!-- fabro:merge-queue rebased from={OLD} to={NEW} run=01RUN -->$", file=log()))
    ck("merge-queue act: 7. the mutation passes the old head as expectedHeadOid and the PR id", "1", count(f"^gh api graphql .* id=PR_8 -f h={OLD}", file=log()))
    # 7b. rebase requested but the head does not change.
    d, s = setup("r1b", plan(8, OLD, "rebase")); write(s / "newhead", OLD + "\n")
    act()
    ck("merge-queue act: 7b. head never changes: failed, no marker", "failed|0", outcomes() + "|" + calls("^COMMENT"))
    # 7c. marker cannot be posted.
    d, s = setup("r1c", plan(8, OLD, "rebase")); write(s / "newhead", NEW + "\n"); write(s / "comment_fail", "")
    act()
    ck("merge-queue act: 7c. marker fails 3 times: failed", "failed|3", outcomes() + "|" + calls("^COMMENT"))
    # 8. rebase, stale expected head.
    d, s = setup("r2", plan(8, OLD, "rebase")); write(s / "gql_err", "gh: head sha didn't match the current head ref.\n")
    act()
    ck("merge-queue act: 8. stale expectedHeadOid: waiting, no comment", "waiting|0", outcomes() + "|" + calls("^COMMENT"))
    # 9. rebase, conflict.
    d, s = setup("r3", plan(8, OLD, "rebase")); write(s / "gql_err", "gh: Merge conflict between base and head\n")
    act()
    ck("merge-queue act: 9. a conflict: handed_off, the C6 comment starts retryable:, and the label swap", "handed_off|1|1",
       outcomes() + "|" + count("^COMMENT <!-- fabro:merge-queue handoff head=", file=log()) + "|" + count("gh pr edit 8 --remove-label merge-on-green --add-label ai-review-needs-human", file=log()))
    ck("merge-queue act: 9. the hand-off reason starts with retryable:", "1",
       count("\\*\\*The merge queue stopped on this PR.\\*\\* retryable: the queue could not rebase this PR on main: gh: Merge conflict", file=d / "mq/handoff_8.md"))

    # 10. rerun with two ids, one fails.
    d, s = setup("rr", plan(9, OLD, "rerun", "", "[111,222]")); write(s / "rerun_fail.222", "")
    act()
    ck("merge-queue act: 10. two ids, one fails: reran, 2 rerun calls", "reran|2", outcomes() + "|" + calls("gh run rerun"))
    d, s = setup("rr2", plan(9, OLD, "rerun", "", "[222]")); write(s / "rerun_fail.222", "")
    act()
    ck("merge-queue act: 10b. every rerun fails: failed with the first error", "failed|run 222 cannot be rerun", outcomes() + "|" + detail())

    # 11. handoff: comment before label.
    setup("h1", plan(9, OLD, "handoff", "the head moved after the merge phase approved it; the new commits were not reviewed"))
    act()
    ck("merge-queue act: 11. hand-off: handed_off", "handed_off", outcomes())
    first_line = lambda pat: (grep(["-n", pat], file=log()).split("\n")[0].split(":")[0] if grep(["-n", pat], file=log()) else "")
    c, e = first_line("^COMMENT"), first_line("^gh pr edit")
    ck("merge-queue act: 11. the comment call comes before the label call", "1", "1" if c and e and int(c) < int(e) else "0")
    ck("merge-queue act: 11. the first line is the C6 marker", "1", count(f"^COMMENT <!-- fabro:merge-queue handoff head={OLD} run=01RUN -->$", file=log()))
    d, s = setup("h2", plan(9, OLD, "handoff", "why")); write(s / "edit_fail", "")
    act()
    ck("merge-queue act: 11b. the label call fails: failed (the next tick plans it again)", "failed", outcomes())
    d, s = setup("h3", plan(9, OLD, "wait", "checks pending"))
    act()
    ck("merge-queue act: 11c. wait: waiting with the reason, no gh call", "waiting|checks pending|0",
       outcomes() + "|" + detail() + "|" + ("1" if (s / "calls.log").exists() else "0"))

    # 12. budget.
    d, s = setup("b1", jq_n('[{number: 7, head: $h, action: "merge", reason: "", rerun_ids: []}, {number: 8, head: $h, action: "rebase", reason: "", rerun_ids: []}, {number: 9, head: $h, action: "handoff", reason: "x", rerun_ids: []}]', "--arg", "h", OLD))
    act(MQ_BUDGET_SECONDS="0")
    ck("merge-queue act: 12. budget 0: three skipped_cap, no gh call", "skipped_cap,skipped_cap,skipped_cap|no calls",
       outcomes() + "|" + ("calls" if (s / "calls.log").exists() else "no calls"))
    # 13. one result per plan entry, in order, in a mixed plan.
    d, s = setup("x1", jq_n('[{number: 7, head: $h, action: "wait", reason: "a", rerun_ids: []}, {number: 8, head: $h, action: "bogus", reason: "", rerun_ids: []}, {number: 9, head: $h, action: "wait", reason: "b", rerun_ids: []}]', "--arg", "h", OLD))
    act()
    ck("merge-queue act: 13. one result per plan entry, in plan order, unknown action failed", "7,8,9|waiting,failed,waiting",
       jq('[.[].number | tostring] | join(",")', file=d / "mq/results.json") + "|" + outcomes())
    # bad plan
    d, s = setup("bad", "not json")
    act()
    ck("merge-queue act: an unreadable plan exits 1 and writes stop_reason", "1|act: plan.json is missing or unreadable", cat(f"{d}.rc") + "|" + cat(d / "mq/stop_reason"))
    # empty plan
    d, s = setup("empty", "[]")
    act()
    ck("merge-queue act: an empty plan: exit 0, empty results", "0|[]", cat(f"{d}.rc") + "|" + jq(".", file=d / "mq/results.json", args=["-c"], raw=False))


# ---------------------------------------------------------------------------
# merge-queue — summarize (fast-path series, task 08): the one-line summary, empty for a
# quiet tick.
# ---------------------------------------------------------------------------
def test_merge_queue_summarize(ck, tmp_path):
    st = {}

    def mqs(name, results="", skip="", stop="", stopped=""):
        d = tmp_path / f"mqs-{name}"
        rm(d, f"{d}.sh"); (d / "mq").mkdir(parents=True)
        for f, v in (("results.json", results), ("skip_reason", skip), ("stop_reason", stop), ("stopped_at", stopped)):
            if v:
                write(d / "mq" / f, v)
        script = staged(ck, "summarize", d, d, "merge-queue summarize")
        run_files(["sh", script], d, cwd=d)
        st.update(d=d, sum=last_route(d, ".context_updates.mq_summary"))
        return d

    rc = lambda: cat(f"{st['d']}.rc")
    d = mqs("a", '[{"number":1,"action":"merge","outcome":"merged","detail":""},{"number":2,"action":"merge","outcome":"merged","detail":""},{"number":3,"action":"handoff","outcome":"handed_off","detail":"retryable: CI failed after one rerun: test"},{"number":4,"action":"wait","outcome":"waiting","detail":""},{"number":5,"action":"wait","outcome":"waiting","detail":""},{"number":6,"action":"wait","outcome":"waiting","detail":""}]')
    ck("merge-queue summarize: 2 merged, 1 handed off, 3 waiting", "0|Merged #1, #2 | Handed off #3 (retryable: CI failed after one rerun: test) | Waiting: 3", rc() + "|" + st["sum"])
    mqs("b", '[{"number":4,"action":"wait","outcome":"waiting","detail":""},{"number":5,"action":"wait","outcome":"skipped_cap","detail":""}]')
    ck("merge-queue summarize: only waiting entries: empty summary", "|0", st["sum"] + "|" + rc())
    mqs("c", skip="auto-merge is off for this repository (pr-review row)")
    ck("merge-queue summarize: a switch that is off: empty summary", "", st["sum"])
    mqs("d", skip="could not list active runs")
    ck("merge-queue summarize: could not list active runs is a stop line", "Stopped at prep: could not list active runs", st["sum"])
    mqs("e", stop="inventory: GraphQL failed", stopped="inventory")
    ck("merge-queue summarize: a stop_reason names the node", "Stopped at inventory: inventory: GraphQL failed", st["sum"])
    mqs("f", '[{"number":1,"action":"merge","outcome":"merged","detail":""}]', stop="act: plan.json is missing or unreadable", stopped="act")
    ck("merge-queue summarize: a stop keeps earlier results", "Stopped at act: act: plan.json is missing or unreadable | Merged #1", st["sum"])
    mqs("g", '[{"number":9,"action":"rebase","outcome":"rebased","detail":""},{"number":10,"action":"rerun","outcome":"reran","detail":""},{"number":11,"action":"merge","outcome":"failed","detail":"Base branch was modified"}]')
    ck("merge-queue summarize: rebased, reran, failed lines in order", "Rebased #9 | Reran CI on #10 | Failed #11 (Base branch was modified)", st["sum"])
    d = mqs("h")
    ck("merge-queue summarize: nothing at all: empty summary, exit 0", "|0", st["sum"] + "|" + rc())
    ck("merge-queue summarize: the routing object is the last line and valid JSON", "0", jq_ok(tail1(f"{d}.out")))


# ---------------------------------------------------------------------------
# merge-queue-summary Discord kind (task 08): quiet tick posts nothing, a repeated Stopped line
# posts once an hour.
# ---------------------------------------------------------------------------
def test_merge_queue_summary_discord_kind(ck, tmp_path):
    MN = tmp_path / "mqnotify"
    (MN / "storage/secrets").mkdir(parents=True)
    write(MN / "storage/secrets/discord_webhook_url", "http://discord.test/hook")
    write(MN / "storage/server.dev-token", "tok")
    notify = write(MN / "notify.sh", (WF / "backlog/scripts/discord-notify.sh").read_text().replace("/storage", str(MN / "storage")))
    stub(MN / "stub", "wget", """#!/bin/sh
case "$*" in
  *"/state"*) printf '{"mq_summary":"%s"}' "$MQ_SUMMARY" ;;
  *"/api/v1/runs/"*) echo '{"origin_url":"https://github.com/andrewthetechie/jelly-swipe.git"}' ;;
esac
exit 0
""")
    stub(MN / "stub", "curl", '#!/bin/sh\necho call >> "$MN_CALLS"\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && printf \'%s\' "$2" > "$MN_PAYLOAD"; shift; done\nexit 0\n')
    calls_f, payload = MN / "calls", MN / "payload.json"

    def mn_run(summary, now):
        sh(notify, "merge-queue-summary", path=f"{MN / 'stub'}:{ORIG_PATH}", stdin='{"run_id":"01X"}\n',
           env={"MN_CALLS": calls_f, "MN_PAYLOAD": payload, "MQ_SUMMARY": summary, "MQ_NOTIFY_NOW": now})

    calls = lambda: str(calls_f.read_text().count("\n")) if calls_f.exists() else "0"
    mn_run("", "1000")
    ck("notify merge-queue-summary: an empty mq_summary posts nothing", "0", calls())
    mn_run("Merged #7 | Waiting: 2", "1000")
    ck("notify merge-queue-summary: a Merged summary posts", "1", calls())
    ck("notify merge-queue-summary: header, one line per part", "1|1",
       count("fabro merge queue on andrewthetechie/jelly-swipe", file=payload) + "|" + count("Merged #7", file=payload))
    ck("notify merge-queue-summary: the payload is valid JSON", "0", jq_ok(cat(payload)))
    mn_run("Merged #7 | Waiting: 2", "1001")
    ck("notify merge-queue-summary: a Merged summary always posts", "2", calls())
    mn_run("Stopped at prep: could not list active runs", "2000")
    ck("notify merge-queue-summary: the first Stopped line posts", "3", calls())
    mn_run("Stopped at prep: could not list active runs", "2100")
    ck("notify merge-queue-summary: the same Stopped line within an hour does not post", "3", calls())
    mn_run("Stopped at inventory: other", "2200")
    ck("notify merge-queue-summary: a different Stopped line posts", "4", calls())
    mn_run("Stopped at inventory: other", "5801")
    ck("notify merge-queue-summary: the same Stopped line after 3601 s posts again", "5", calls())
