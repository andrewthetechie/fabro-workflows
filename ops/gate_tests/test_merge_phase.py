"""The review-merge phase's `merge` and `watch_checks`, backlog's `autofix`, and the outcome
keys the stop nodes publish (ADR 0020)."""

import time

from harness import (GRAPH, ORIG_PATH, PR, SHARED, cat, count, diff, extract, git, jq,
                     lastjson, nonempty, present, rebase, rm, rm_glob, routing_check, run_node,
                     sh, stage, stub, tail1, which, write)

SLEEP_STUB = "#!/bin/sh\nexit 0\n"


# ---------------------------------------------------------------------------
# review-merge `merge` — the checkpoint-push race
#
# Fabro commits and pushes the run branch after EVERY stage, and `merge` is the one node
# whose branch is the thing being merged: the push lands ~15ms before the node starts,
# GitHub invalidates the computed merge ref, and the mutation is rejected with "Base branch
# was modified". The base is innocent -- jelly-swipe#386 lost its merge to this on
# 2026-09-19 with `main` unmoved for 35h -- so the node waits for mergeability to settle,
# retries the race, and only then gives up.
# ---------------------------------------------------------------------------
MERGE_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
      case "$*" in
        *"--json state"*)
            cat "$GH_STATE/pr_state" 2>/dev/null || echo OPEN ;;
        *"--json mergeable"*)
            n=0; [ -f "$GH_STATE/m_calls" ] && n=$(cat "$GH_STATE/m_calls")
            n=$((n + 1)); echo "$n" > "$GH_STATE/m_calls"
            u=0; [ -f "$GH_STATE/unknown_until" ] && u=$(cat "$GH_STATE/unknown_until")
            if [ "$n" -le "$u" ]; then echo UNKNOWN; else echo MERGEABLE; fi ;;
      esac
      exit 0 ;;
  "pr checks")
      # Real `gh pr checks` exits 8 while anything is pending and still prints the
      # rows, so the node must read the JSON and ignore the exit code. The stub
      # reproduces that, because a `|| echo []` fallback would silently turn every
      # pending poll into "no checks at all".
      n=0; [ -f "$GH_STATE/check_polls" ] && n=$(cat "$GH_STATE/check_polls")
      n=$((n + 1)); echo "$n" > "$GH_STATE/check_polls"
      p=0; [ -f "$GH_STATE/checks_pending_until" ] && p=$(cat "$GH_STATE/checks_pending_until")
      if [ "$n" -le "$p" ]; then
          echo '[{"name":"test","bucket":"pending"}]'; exit 8
      fi
      echo '[{"name":"test","bucket":"pass"}]'; exit 0 ;;
  "pr merge")
      n=0; [ -f "$GH_STATE/merge_calls" ] && n=$(cat "$GH_STATE/merge_calls")
      n=$((n + 1)); echo "$n" > "$GH_STATE/merge_calls"
      f=0; [ -f "$GH_STATE/merge_fails_until" ] && f=$(cat "$GH_STATE/merge_fails_until")
      if [ "$n" -le "$f" ]; then
          cat "$GH_STATE/merge_error" >&2
          exit 1
      fi
      echo "Squashed and merged pull request #386"; exit 0 ;;
esac
exit 0
"""
MERGE_GIT = """#!/bin/sh
case "$1 $2" in
  "merge-base --is-ancestor")
      # exit 0 = base IS an ancestor of HEAD = we are NOT behind it
      [ -f "$GH_STATE/behind_base" ] && exit 1
      exit 0 ;;
esac
exit 0
"""
PROTECTION = "X Pull request andrewthetechie/jelly-swipe#389 is not mergeable: the base branch policy prohibits the merge.\n"


def test_review_merge_merge(ck, tmp_path):
    T = tmp_path / "rmmerge"
    (T / "bin").mkdir(parents=True)
    gate = stage(ck, "merge", T, graph=SHARED)
    stub(T / "bin", "sleep", SLEEP_STUB)
    stub(T / "bin", "git", MERGE_GIT)
    stub(T / "bin", "gh", MERGE_GH)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    go = lambda: sh(gate, path=path, env=env)
    state = lambda out: jq(".context_updates.merge_state", lastjson(out))
    reason = T / "merge_block_reason"

    def setup():
        write(T / "gh.log", "")
        rm(*(T / f for f in ("m_calls", "merge_calls", "unknown_until", "merge_fails_until",
                              "behind_base", "pr_state", "strict_retry", "merge_block_reason",
                              "needs_human_reason", "merge_attempt.log", "check_polls",
                              "checks_pending_until", "merge_checks.json")))
        write(T / "pr_number", "386\n")
        write(T / "base_ref", "main\n")
        # A real budget, or `settle` sees DL=0, returns on its first line, and every
        # assertion below about waiting for CI would pass without running anything.
        write(T / "merge_deadline", f"{int(time.time()) + 3600}\n")
        write(T / "commit_subject.txt", "fix: a subject (#356)")
        write(T / "commit_body.md", "")
        write(T / "merge_error", "GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)\n")

    # 1. Clean merge.
    setup()
    r = go()
    ck("clean merge exits 0", "0", r.rc)
    ck("clean merge reports merged", "merged", state(r.out))
    ck("clean merge tries once", "1", cat(T / "merge_calls"))

    # 2. THE RACE, survived. First attempt is rejected, the retry wins.
    setup(); write(T / "merge_fails_until", "1\n")
    r = go()
    ck("race retried, exits 0", "0", r.rc)
    ck("race retried, merged", "merged", state(r.out))
    ck("race took two attempts", "2", cat(T / "merge_calls"))

    # 3. THE RACE, unsurvivable. Bounded at three, and the reason must NOT blame
    #    permissions -- that wording is what sent a human looking for a token problem.
    setup(); write(T / "merge_fails_until", "9\n")
    r = go()
    ck("persistent race fails", "1", r.rc)
    ck("bounded at three attempts", "3", cat(T / "merge_calls"))
    ck("names the race, not perms", "1", count("head-push race", file=reason))
    ck("says the PR is mergeable", "1", count("mergeable as it stands", file=reason))

    # THE OTHER HALF OF #386: mark_needs_human renders `needs-human`, which reads
    # needs_human_reason, NOT merge_block_reason. Both must be written.
    ck("writes merge_block_reason", "1", nonempty(reason))
    ck("writes needs_human_reason", "1", nonempty(T / "needs_human_reason"))
    ck("both reasons agree", "", diff(reason, T / "needs_human_reason"))

    # 4. A genuine failure is NOT retried, and it QUOTES GitHub instead of guessing
    #    (jelly-swipe#389 was refused by branch protection).
    setup(); write(T / "merge_fails_until", "9\n")
    write(T / "merge_error", "GraphQL: Resource not accessible by integration (mergePullRequest)\n")
    r = go()
    ck("non-race fails", "1", r.rc)
    ck("non-race is not retried", "1", cat(T / "merge_calls"))
    ck("non-race quotes GitHub", "1", count("Resource not accessible", file=reason))
    ck("non-race is not the race", "0", count("head-push race", file=reason))
    ck("non-race sets both files", "1", nonempty(T / "needs_human_reason"))

    # 4b. THE #389 FAILURE: branch protection refuses because the checkpoint push reset
    #     the required checks. The node must wait for CI on the NEW head and retry -- in
    #     this node, because any node boundary is another checkpoint and another new head.
    setup(); write(T / "merge_fails_until", "1\n"); write(T / "checks_pending_until", "2\n")
    write(T / "merge_error", PROTECTION)
    r = go()
    ck("protection retried, exits 0", "0", r.rc)
    ck("protection retried, merged", "merged", state(r.out))
    ck("protection took two tries", "2", cat(T / "merge_calls"))
    ck("waited out pending CI", "1", "1" if int(cat(T / "check_polls") or 0) >= 3 else "0")

    # 4c. Protection refuses for good. Bounded, and the reason names the real cause.
    setup(); write(T / "merge_fails_until", "9\n"); write(T / "merge_error", PROTECTION)
    r = go()
    ck("protection persists, fails", "1", r.rc)
    ck("protection bounded at three", "3", cat(T / "merge_calls"))
    ck("names branch protection", "1", count("Branch protection on the base", file=reason))
    ck("names the required check", "1", count("required status check", file=reason))
    ck("protection sets both files", "", diff(reason, T / "needs_human_reason"))

    # 4d. THE #1234 FAILURE: the same checkpoint push, but GitHub serves a STALE
    #     `MERGEABLE` instead of UNKNOWN, so the wait loop never engages and the mutation
    #     refuses. Not a conflict -- the branch is not behind -- so it must be retried.
    setup(); write(T / "merge_fails_until", "1\n")
    write(T / "merge_error", "GraphQL: Pull Request is not mergeable (mergePullRequest)\n")
    r = go()
    ck("stale-mergeable retried", "0", r.rc)
    ck("stale-mergeable merged", "merged", state(r.out))
    ck("stale-mergeable two tries", "2", cat(T / "merge_calls"))

    # 4e. It persists: bounded, and reported as the race rather than as a conflict.
    setup(); write(T / "merge_fails_until", "9\n")
    write(T / "merge_error", "GraphQL: Pull Request is not mergeable (mergePullRequest)\n")
    r = go()
    ck("stale-mergeable fails", "1", r.rc)
    ck("stale-mergeable bounded", "3", cat(T / "merge_calls"))
    ck("names the race not conflict", "1", count("head-push race", file=reason))
    ck("rules out a conflict", "1", count("cannot be a conflict", file=reason))
    ck("no stray doubled quote", "0", count("GitHubs", file=reason))

    # 4f. ORDERING TRAP: the protection rejection also contains "not mergeable", so a
    #     general match must not swallow it.
    setup(); write(T / "merge_fails_until", "9\n"); write(T / "merge_error", PROTECTION)
    go()
    ck("protection wins the match", "1", count("Branch protection on the base", file=reason))
    ck("not reported as the race", "0", count("head-push race", file=reason))

    # 5. Mergeability is UNKNOWN right after the checkpoint push; wait for it.
    setup(); write(T / "unknown_until", "2\n")
    r = go()
    ck("waits out UNKNOWN", "0", r.rc)
    ck("polled mergeability", "3", cat(T / "m_calls"))
    ck("then merged", "merged", state(r.out))

    # 6. A PR someone else already handled is not a failure.
    setup(); write(T / "pr_state", "CLOSED\n")
    r = go()
    ck("closed PR exits 0", "0", r.rc)
    ck("closed PR reports closed", "closed", state(r.out))
    ck("closed PR never merges", "0", count("pr merge", file=T / "gh.log"))

    # 7. Genuinely behind the base: hand off to remerge_base, do not call it a failure.
    setup(); write(T / "merge_fails_until", "9\n"); write(T / "behind_base", "")
    r = go()
    ck("behind base exits 0", "0", r.rc)
    ck("behind base is stale", "stale", state(r.out))


# ---------------------------------------------------------------------------
# review-merge `watch_checks` — one rerun of the failed jobs before ci_fix (ADR 0011 D2)
#
# Four of the seven "CI-fix could not fix" blocks in the 2026-09-24 review were flakes or
# CI infrastructure faults that passed on a plain rerun. The node reruns the failed Actions
# jobs once per merge phase, and only while at least 30 minutes of merge_deadline remain
# and the node has run under 20 minutes. `date` is stubbed with a controllable clock so the
# 20-minute guard can be reached without waiting; with no clock file it defers to the real
# `date`.
# ---------------------------------------------------------------------------
WC_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr checks")
      n=0; [ -f "$GH_STATE/polls" ] && n=$(cat "$GH_STATE/polls")
      n=$((n + 1)); echo "$n" > "$GH_STATE/polls"
      while [ "$n" -gt 1 ] && [ ! -f "$GH_STATE/checks_$n.json" ]; do n=$((n - 1)); done
      cat "$GH_STATE/checks_$n.json"; exit 0 ;;
  "run rerun") exit 0 ;;
  "run view")  echo "the failing log"; exit 0 ;;
  "api repos/"*) echo "${WC_ATTEMPT-2}"; exit 0 ;;
esac
exit 0
"""
# FlakeRadar (ADR 0028 F8): /api/repos from $GH_STATE/fr/repos.json, a CI-run lookup from
# fr/run_<run>_<attempt>.json, and 404 for anything missing.
WC_CURL = """#!/bin/sh
OUT=/dev/null; URL=""
while [ $# -gt 0 ]; do
  case "$1" in -o) OUT=$2; shift ;; -w|-m) shift ;; -*) ;; *) URL=$1 ;; esac
  shift
done
echo "curl $URL" >> "$GH_LOG"
P=${URL#*/api/}
case "$P" in
  repos) F=repos.json ;;
  ci-runs/*) ID=${P#ci-runs/}; F=run_${ID%%/*}_${P##*ci_run_attempt=}.json ;;
  *) F=none ;;
esac
if [ -e "$GH_STATE/fr/$F" ]; then cat "$GH_STATE/fr/$F" > "$OUT"; printf 200; else printf '{}' > "$OUT"; printf 404; fi
"""
# fabro-io: `scout config` prints the ignore_jobs in WC_IGNORE; the n-th `flakeradar
# classify` prints $GH_STATE/classify_<n>.json, else classify.json, and writes section.md as
# its section. The rule itself is fabro-io's (ops/fabro-io/tests/flakeradar_classify_tests.rs).
WC_FABRO_IO = """#!/bin/sh
echo "fabro-io $*" >> "$GH_LOG"
case "$1 $2" in
  "scout config") printf '{"flaky":{"ignore_jobs":%s}}' "${WC_IGNORE-[]}" ;;
  "flakeradar classify")
      while [ $# -gt 0 ]; do [ "$1" = --section ] && cp "$GH_STATE/section.md" "$2"; shift; done
      n=0; [ -f "$GH_STATE/classifies" ] && n=$(cat "$GH_STATE/classifies"); n=$((n + 1)); echo "$n" > "$GH_STATE/classifies"
      if [ -f "$GH_STATE/classify_$n.json" ]; then cat "$GH_STATE/classify_$n.json"; else cat "$GH_STATE/classify.json"; fi ;;
  *) exit 2 ;;
esac
"""
FAILING = '[{"name":"test","state":"FAILURE","bucket":"fail","link":"https://github.com/o/r/actions/runs/111/job/9"}]'
PASSING = '[{"name":"test","state":"SUCCESS","bucket":"pass","link":"https://github.com/o/r/actions/runs/111/job/10"}]'
KNOWN = '{"known_flaky":true,"checks":[{"name":"test","run":111,"job":9,"known_flaky":true,"reason":null,"tests":[{"name":"welcome mat shows","project":"pilot","tier":"flaky","score":0.7,"proven":2,"category":"timing","issue":41,"message":"timed out"}]}]}'
MIXED = '{"known_flaky":false,"checks":[{"name":"test","run":111,"job":9,"known_flaky":false,"reason":"test x is stable","tests":[]}]}'
UNKNOWN = '{"known_flaky":false,"unknown":true,"checks":[{"name":"test","run":111,"job":9,"known_flaky":false,"reason":"FlakeRadar has no report of the run","tests":[]}]}'
SECTION = """## Known flaky (FlakeRadar)

- `welcome mat shows` in `pilot`: flaky, 2 proven flakes (check `test`), issue #41"""


def test_review_merge_watch_checks(ck, tmp_path):
    T = tmp_path / "wchecks"
    for d in ("bin", "review", "feedback"):
        (T / d).mkdir(parents=True, exist_ok=True)
    gate = stage(ck, "watch_checks", T, graph=SHARED)
    real_date = which("date")
    stub(T / "bin", "sleep", SLEEP_STUB)
    stub(T / "bin", "date", f"""#!/bin/sh
if [ "$1" = "+%s" ] && [ -f "$GH_STATE/clock" ]; then
    now=$(cat "$GH_STATE/clock"); echo "$now"
    echo $((now + $(cat "$GH_STATE/clock_step"))) > "$GH_STATE/clock"
    exit 0
fi
exec {real_date} "$@"
""")
    stub(T / "bin", "gh", WC_GH)
    stub(T / "bin", "curl", WC_CURL)
    stub(T / "bin", "fabro-io", WC_FABRO_IO)
    # git: `git show origin/<base>:.fabro/scout.toml` answers from $GH_STATE/base_scout.toml.
    stub(T / "bin", "git", f"""#!/bin/sh
if [ "$1" = show ]; then
    echo "git $*" >> "$GH_LOG"
    [ -f "$GH_STATE/base_scout.toml" ] || exit 128
    cat "$GH_STATE/base_scout.toml"; exit 0
fi
exec {which("git")} "$@"
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    log = T / "gh.log"

    def wc_run(**env):
        e = {"GH_LOG": T / "gh.log", "GH_STATE": T, **env}
        unset = tuple(k for k in ("FLAKERADAR_URL", "WC_ATTEMPT", "WC_IGNORE") if k not in env)
        return sh(gate, path=path, env=e, unset=unset)

    def setup():
        rm_glob(T, "gh.log", "polls", "checks_*.json", "ci_rerun_done", "clock", "clock_step",
                "gh_fix_attempts", "merge_block_reason", "feedback/ci_fix.md",
                "ci_flaky_rerun_done", "review/known_flaky.md", "classify.json", "section.md",
                "classify_*.json", "classifies", "base_scout.toml", "flaky_tests.txt")
        write(T / "base_ref", "main\n")
        rm(T / "flakeradar", T / "fr")
        (T / "fr").mkdir()
        write(T / "fr/repos.json", '[{"name":"o/r","projects":[]}]')
        write(T / "fr/run_111_2.json", '{"reports":{"pending":0,"processed":1,"failed":0},"failures":[],"failed_jobs":[]}')
        write(T / "classify.json", '{"known_flaky":false,"checks":[]}')
        write(T / "section.md", "")
        write(log, "")
        write(T / "pr_number", "7\n")
        write(T / "merge_deadline", f"{int(time.time()) + 3600}\n")

    ctx = lambda out, k: jq(f".context_updates.{k}", lastjson(out))
    ok_tier = lambda out: jq('"\\(.context_updates.checks_ok):\\(.context_updates.gh_fix_attempts)"', lastjson(out))

    # 1. A flake: fails, is rerun once, passes. Merges without ci_fix.
    setup()
    write(T / "checks_1.json", FAILING); write(T / "checks_2.json", PASSING)
    out = wc_run().out
    ck("flake: checks_ok after the rerun", "true", ctx(out, "checks_ok"))
    ck("flake: exactly one rerun, of run 111", "1", count("^run rerun 111 --failed$", file=log))
    ck("flake: no ci_fix attempt spent", "absent", present(T / "gh_fix_attempts"))

    # 2. A real failure: fails, is rerun once, fails again. Goes to ci_fix tier 1.
    setup()
    write(T / "checks_1.json", FAILING)
    out = wc_run().out
    ck("real failure: routes to ci_fix", "false:1", ok_tier(out))
    ck("real failure: still only one rerun", "1", count("^run rerun", file=log))
    ck("real failure: ci_fix.md carries the log", "1", count("the failing log", file=T / "feedback/ci_fix.md"))

    # 3. Second visit in the same merge phase (after a ci_fix push): no second rerun.
    setup()
    write(T / "ci_rerun_done", ""); write(T / "gh_fix_attempts", "1\n")
    write(T / "checks_1.json", FAILING)
    out = wc_run().out
    ck("second visit: no rerun", "0", count("^run rerun", file=log))
    ck("second visit: ci_fix tier 2", "2", ctx(out, "gh_fix_attempts"))

    # 4. Under 30 minutes of budget left: no rerun, straight to ci_fix.
    setup()
    write(T / "merge_deadline", f"{int(time.time()) + 1000}\n")
    write(T / "checks_1.json", FAILING)
    out = wc_run().out
    ck("low budget: no rerun", "0", count("^run rerun", file=log))
    ck("low budget: says why", "1", count("under 30 minutes", out))

    # 5. The node has already waited 20 minutes: no rerun. The clock advances 700s per
    #    `date +%s` call, so by the post-poll check 2100s have passed since T0.
    setup()
    write(T / "clock", "1000000\n"); write(T / "clock_step", "700\n")
    write(T / "merge_deadline", "1010000\n")
    write(T / "checks_1.json", FAILING)
    out = wc_run().out
    ck("20 minutes in node: no rerun", "0", count("^run rerun", file=log))
    ck("20 minutes in node: says why", "1", count("already waited 20 minutes", out))

    # 6. A failing check that is not an Actions run (no /actions/runs/ link): nothing to
    #    rerun, so it goes to ci_fix at once instead of waiting for nothing.
    setup()
    write(T / "checks_1.json", '[{"name":"codeql","state":"FAILURE","bucket":"fail","link":"https://github.com/o/r/runs/5"}]')
    out = wc_run().out
    ck("non-Actions failure: no rerun", "0", count("^run rerun", file=log))
    ck("non-Actions failure: routes to ci_fix", "1", ctx(out, "gh_fix_attempts"))

    # 7. The rerun never settles: the node reports blocked at its own 47-minute cap instead
    #    of running into its 50m timeout. 300s per `date +%s` call.
    setup()
    write(T / "clock", "1000000\n"); write(T / "clock_step", "300\n")
    write(T / "merge_deadline", "1010000\n")
    write(T / "checks_1.json", FAILING)
    write(T / "checks_2.json", '[{"name":"test","state":"QUEUED","bucket":"pending","link":"https://github.com/o/r/actions/runs/111/job/11"}]')
    out = wc_run().out
    ck("rerun never settles: one rerun", "1", count("^run rerun", file=log))
    ck("rerun never settles: blocked", "true", ctx(out, "checks_blocked"))
    ck("rerun never settles: names the cap", "1", count("after 47 minutes", file=T / "merge_block_reason"))

    # 8-14. FlakeRadar (ADR 0028 F8-F10, docs/flakeradar C10). classify.json is what
    # `fabro-io flakeradar classify` would decide; section.md is its F10 section.

    # 8. Known flaky after the plain rerun: a second rerun, which passes. No ci_fix.
    setup()
    write(T / "checks_1.json", FAILING); write(T / "checks_2.json", FAILING); write(T / "checks_3.json", PASSING)
    write(T / "classify.json", KNOWN)
    out = wc_run(FLAKERADAR_URL="https://fr.test/").out
    ck("known flaky: checks_ok after the second rerun", "true", ctx(out, "checks_ok"))
    ck("known flaky: two reruns", "2", count("^run rerun 111 --failed$", file=log))
    ck("known flaky: the second is recorded", "present", present(T / "ci_flaky_rerun_done"))
    ck("known flaky: no ci_fix attempt spent", "absent", present(T / "gh_fix_attempts"))
    ck("known flaky: the lookup is at the run's current attempt", "1",
       count("^curl https://fr.test/api/ci-runs/111/failures?repo=o/r&ci_run_attempt=2$", file=log))
    ck("known flaky: a 200 lookup with nothing pending is kept for classify", "1",
       "1" if (T / "flakeradar/run_111.json").is_file() else "0")
    ck("known flaky: classify reads checks.json and the lookups", "1",
       count(f"^fabro-io flakeradar classify --checks {T}/checks.json --failures-dir {T}/flakeradar --ignore-jobs \\[\\] --section {T}/review/known_flaky.md$", file=log))
    ck("known flaky: no brace on any line but the routing object", "1", count("[{}]", out))

    # 9. Still red after the second rerun: held, naming the test and its issue. No ci_fix.
    setup()
    write(T / "checks_1.json", FAILING); write(T / "classify.json", KNOWN)
    out = wc_run(FLAKERADAR_URL="https://fr.test").out
    ck("known flaky, still red: blocked", "true", ctx(out, "checks_blocked"))
    ck("known flaky, still red: two reruns", "2", count("^run rerun", file=log))
    ck("known flaky, still red: the reason names the test and issue", "1",
       count("only on known flaky tests (welcome mat shows #41) after a second rerun, so ci_fix was not run", file=T / "merge_block_reason"))
    ck("known flaky, still red: no ci_fix attempt", "absent", present(T / "gh_fix_attempts"))

    # 10. Not every failure is known flaky: ci_fix as before, and ci_fix.md carries the section.
    setup()
    write(T / "checks_1.json", FAILING)
    write(T / "classify.json", MIXED); write(T / "section.md", SECTION + "\n")
    write(T / "base_scout.toml", "version = 1\n")
    out = wc_run(FLAKERADAR_URL="https://fr.test", WC_IGNORE='["alls-green"]').out
    ck("mixed: routes to ci_fix", "false:1", ok_tier(out))
    ck("mixed: one rerun", "1", count("^run rerun", file=log))
    ck("mixed: ci_fix.md has the log, then the section", "1|1",
       count("the failing log", file=T / "feedback/ci_fix.md") + "|" + count("^## Known flaky (FlakeRadar)$", file=T / "feedback/ci_fix.md"))
    ck("mixed: scout.toml is read from the base branch", "1", count("^git show origin/main:.fabro/scout.toml$", file=log))
    ck("mixed: the aggregators from scout.toml reach classify", "1", count('--ignore-jobs \\["alls-green"\\]', file=log))
    ck("mixed: the run log says why", "1", count("^flakeradar: test: test x is stable$", out))

    # 11. No FLAKERADAR_URL: today's path exactly, with no call to FlakeRadar or classify.
    setup()
    write(T / "checks_1.json", FAILING); write(T / "classify.json", KNOWN)
    out = wc_run(FLAKERADAR_URL="").out
    ck("no URL: routes to ci_fix", "1", ctx(out, "gh_fix_attempts"))
    ck("no URL: no curl, no classify", "0|0", count("^curl", file=log) + "|" + count("flakeradar classify", file=log))
    ck("no URL: ci_fix.md has no section", "0", count("Known flaky", file=T / "feedback/ci_fix.md"))

    # 12. A repository FlakeRadar does not list (jelly-swipe): no lookup and no 3-minute wait.
    setup()
    write(T / "checks_1.json", FAILING)
    write(T / "fr/repos.json", '[{"name":"o/other","projects":[]}]')
    out = wc_run(FLAKERADAR_URL="https://fr.test").out
    ck("unlisted repository: no CI-run lookup", "0", count("api/ci-runs", file=log))
    ck("unlisted repository: routes to ci_fix", "1", ctx(out, "gh_fix_attempts"))

    # 13. Reports still pending: polled for 3 minutes (30s per date call), then judged
    #     without the lookup, which classify reads as no report.
    setup()
    write(T / "clock", "1000000\n"); write(T / "clock_step", "30\n")
    write(T / "merge_deadline", "1010000\n")
    write(T / "checks_1.json", FAILING)
    write(T / "fr/run_111_2.json", '{"reports":{"pending":1,"processed":0,"failed":0},"failures":[],"failed_jobs":[]}')
    wc_run(FLAKERADAR_URL="https://fr.test")
    n = int(count("api/ci-runs/111", file=log) or 0)
    ck("pending: polled more than once, at most 10 times", "1", "1" if 1 < n <= 10 else "0")
    ck("pending: the lookup is not kept", "0", "1" if (T / "flakeradar/run_111.json").is_file() else "0")

    # 14. Known flaky with no time for a second rerun: held at once, no rerun at all.
    setup()
    write(T / "merge_deadline", f"{int(time.time()) + 800}\n")
    write(T / "checks_1.json", FAILING); write(T / "classify.json", KNOWN)
    out = wc_run(FLAKERADAR_URL="https://fr.test").out
    ck("known flaky, no time: blocked", "true", ctx(out, "checks_blocked"))
    ck("known flaky, no time: no rerun", "0", count("^run rerun", file=log))
    ck("known flaky, no time: the reason says so", "1", count("no time left for a second rerun", file=T / "merge_block_reason"))
    ck("merge_gate clears both rerun markers", "1",
       count("rm -f /tmp/fabro/ci_rerun_done /tmp/fabro/ci_flaky_rerun_done /tmp/fabro/flaky_tests.txt", file=SHARED))
    ck("no base scout.toml: no aggregators", "1", count("--ignore-jobs \\[\\] ", file=log))

    # 15. After the second rerun FlakeRadar cannot judge the new attempt (not reported
    #     yet, or down): held, naming the tests the second rerun was for. No ci_fix.
    setup()
    write(T / "checks_1.json", FAILING)
    write(T / "classify_1.json", KNOWN); write(T / "classify_2.json", UNKNOWN)
    out = wc_run(FLAKERADAR_URL="https://fr.test").out
    ck("unknown after the second rerun: blocked", "true", ctx(out, "checks_blocked"))
    ck("unknown after the second rerun: the reason names the tests", "1",
       count("second rerun of known flaky tests (welcome mat shows #41), and FlakeRadar cannot judge the new attempt", file=T / "merge_block_reason"))
    ck("unknown after the second rerun: no ci_fix attempt", "absent", present(T / "gh_fix_attempts"))

    # 16. After the second rerun a failure shown to be real goes to ci_fix.
    setup()
    write(T / "checks_1.json", FAILING)
    write(T / "classify_1.json", KNOWN); write(T / "classify_2.json", MIXED)
    out = wc_run(FLAKERADAR_URL="https://fr.test").out
    ck("real failure after the second rerun: ci_fix", "false:1", ok_tier(out))
    ck("real failure after the second rerun: two reruns", "2", count("^run rerun", file=log))

    # 17. The FlakeRadar wait never runs past the node's 47-minute cap (T0 + 2820), which
    #     keeps the node inside its 50m timeout and its reason out of mark_needs_human.
    ck("the FlakeRadar wait is clamped to the 47-minute cap", "1",
       count("-le $((T0 + 2820)) ] || FRT=$((T0 + 2820))", file=SHARED, flags="F"))


# ---------------------------------------------------------------------------
# autofix — the optional per-repository .fabro/fix.sh, always fail-open (ADR 0011 D5)
# ---------------------------------------------------------------------------
def test_autofix(ck, tmp_path):
    T = tmp_path / "autofix"
    gate = stage(ck, "autofix", T)
    wt = T / "wt"

    def af_repo():
        rm(wt); wt.mkdir()
        git("-C", wt, "init", "-q", "-b", "main", ".")
        write(wt / "a.txt", "unformatted\n")

    af_run = lambda: sh(gate, cwd=wt)

    # 1. No fix.sh: nothing happens, and the stage still succeeds.
    af_repo()
    r = af_run()
    ck("no fix.sh: exit 0", "0", r.rc)
    ck("no fix.sh: says so", "1", count("no .fabro/fix.sh", r.out))

    # 2. An executable fix.sh that reformats a file: the edit is left in the tree.
    af_repo()
    stub(wt / ".fabro", "fix.sh", '#!/bin/sh\nprintf "formatted\\n" > a.txt\n')
    r = af_run()
    ck("fix.sh ran: exit 0", "0", r.rc)
    ck("fix.sh ran: file changed", "formatted", cat(wt / "a.txt"))

    # 3. A fix.sh that fails: output kept, stage still succeeds.
    af_repo()
    stub(wt / ".fabro", "fix.sh", "#!/bin/sh\necho clippy blew up\nexit 3\n")
    r = af_run()
    ck("failing fix.sh: exit 0", "0", r.rc)
    ck("failing fix.sh: rc reported", "1", count("fix.sh exited 3", r.out))
    ck("failing fix.sh: output kept", "1", count("clippy blew up", r.out))

    # 4. A fix.sh without the executable bit still runs, under bash.
    af_repo()
    write(wt / ".fabro/fix.sh", 'printf "formatted\\n" > a.txt\n')
    r = af_run()
    ck("non-executable fix.sh: exit 0", "0", r.rc)
    ck("non-executable fix.sh: ran", "formatted", cat(wt / "a.txt"))


# ---------------------------------------------------------------------------
# Outcome keys (ADR 0020, #13): the four stop and autofix nodes publish one routing object
# as the LAST stdout line. Every other line of stdout and stderr is brace-free, because
# fabro scans both streams for objects. Their gh output and warnings go to report.log, and
# autofix's fix.sh output goes to autofix.log.
# ---------------------------------------------------------------------------
def test_outcome_keys(ck, tmp_path):
    T = tmp_path / "outcome-keys"
    (T / "bin").mkdir(parents=True)
    stub(T / "bin", "gh", """#!/bin/sh
echo "gh $*"
if [ -n "${GH_FAIL:-}" ]; then echo '{"gh":"braces"}'; exit 1; fi
exit 0
""")
    reason_f = ".context_updates.merge_block_reason"

    def fresh(name):
        d = T / name
        rm(d); d.mkdir(parents=True)
        return d

    # report_blocked: a reason with braces, quotes, a tab and three lines.
    RB = fresh("rb")
    write(RB / "merge_block_reason", "risk {x} \"q\" 's\tt\nline two {y}\nline three\n")
    write(RB / "pr_number", "42")
    run_node(ck, "report_blocked", SHARED, RB, T / "bin")
    got = routing_check(ck, "report_blocked, hostile reason", RB, reason_f)
    ck("report_blocked: reason is one line", "1", str(got.count("\n") + 1))
    ck("report_blocked: reason under 301 bytes", "1", "1" if len(got.encode()) < 301 else "0")
    ck("report_blocked: reason keeps the first line", "1", count("line two", got, pipe=True))
    # No reason file: the node's own fallback.
    RB2 = fresh("rb2"); write(RB2 / "pr_number", "42")
    run_node(ck, "report_blocked", SHARED, RB2, T / "bin")
    got = routing_check(ck, "report_blocked, no reason file", RB2, reason_f)
    ck("report_blocked: fallback text", "The merge was blocked for an unspecified reason.", got)
    # gh prints braces and fails: still one routing object, the braces only in report.log.
    RB3 = fresh("rb3"); write(RB3 / "pr_number", "42"); write(RB3 / "merge_block_reason", "why")
    run_node(ck, "report_blocked", SHARED, RB3, T / "bin", env={"GH_FAIL": "1"})
    routing_check(ck, "report_blocked, gh fails", RB3, reason_f)
    ck("report_blocked, gh fails: braces in report.log", "1", count('{"gh":"braces"}', file=RB3 / "report.log"))

    # mark_needs_human: the reason comes from needs_human_reason.
    MN = fresh("mn"); write(MN / "pr_number", "42")
    write(MN / "needs_human_reason", 'Could not read {PR} "42"\n')
    run_node(ck, "mark_needs_human", SHARED, MN, T / "bin")
    got = routing_check(ck, "mark_needs_human", MN, reason_f)
    ck("mark_needs_human: reason from needs_human_reason", 'Could not read {PR} "42"', got)

    # mark_needs_human with no PR: still the routing object, and the same key.
    MN2 = fresh("mn2"); write(MN2 / "needs_human_reason", "no pr")
    run_node(ck, "mark_needs_human", SHARED, MN2, T / "bin")
    got = routing_check(ck, "mark_needs_human, no PR", MN2, reason_f)
    ck("mark_needs_human, no PR: reason", "no pr", got)

    # mark_needs_human, issue #19: a node that FAILS into it (an agent timeout, a provider
    # error) writes no reason of its own. The engine's `failure_signature` (`node|class|
    # reason`, on stdin via stdin_source) names the stage, so the reason is built from it;
    # a transient failure carries the `retryable:` prefix the PR shepherd (#31) reads; and
    # a reason file left by an earlier stage is never reused for it.
    def mn_sig(name, sig, stale=None):
        d = fresh(name); write(d / "pr_number", "42")
        if stale:
            write(d / "needs_human_reason", stale)
        script = write(f"{d}.sh", rebase(extract(SHARED, "mark_needs_human"), d))
        r = sh(script, cwd=d, path=f"{T / 'bin'}:{ORIG_PATH}", stdin=sig, stderr="keep")
        write(f"{d}.out", r.out + "\n" if r.out else "")
        return jq(reason_f, tail1(f"{d}.out"), pipe=True)

    got = mn_sig("mn3", "review_fix|transient_infra|handler timed out after 900000ms")
    ck("mark_needs_human timeout: names the stage", "1", count("review_fix", got))
    ck("mark_needs_human timeout: says timed out", "1", count("timed out", got))
    ck("mark_needs_human timeout: retryable prefix", "retryable: ", got[:11])
    ck("mark_needs_human timeout: never unspecified", "0", count("unspecified", got))
    ck("mark_needs_human timeout: needs_human_reason written", got, cat(T / "mn3/needs_human_reason"))
    got = mn_sig("mn9", "review_merge.review_fix|transient_infra|handler timed out after 900000ms", "earlier stage")
    ck("mark_needs_human spliced node id: names the bare stage", "retryable: review_fix failed (transient_infra): timed out", got)
    got = mn_sig("mn4", "review_fix|transient_infra|handler timed out after 900000ms", "validate placeholder from an earlier stage")
    ck("mark_needs_human timeout: stale reason not reused", "0", count("earlier stage", got))
    got = mn_sig("mn5", "standards|deterministic|agent session failed", "old reason")
    ck("mark_needs_human agent failure: names the stage", "1", count("standards failed (deterministic)", got))
    ck("mark_needs_human agent failure: stale reason not reused", "0", count("old reason", got))
    ck("mark_needs_human agent failure: not retryable", "0", count("^retryable:", got))
    got = mn_sig("mn6", "merge|deterministic|script failed with exit code: 1", "branch protection forbids it")
    ck("mark_needs_human command failure: keeps its own reason", "branch protection forbids it", got)
    got = mn_sig("mn7", "merge|deterministic|script failed with exit code: 1")
    ck("mark_needs_human command failure, no reason: names the stage", "1", count("merge failed (deterministic)", got))
    got = mn_sig("mn8", "", "fix agent asked for a human")
    ck("mark_needs_human gate route: keeps the gate's reason", "fix agent asked for a human", got)

    # entry_failed (pr-review): its own fallback when the reason file is missing.
    EF = fresh("ef"); write(EF / "pr_number", "7")
    run_node(ck, "entry_failed", PR, EF, T / "bin")
    got = routing_check(ck, "entry_failed", EF, reason_f)
    ck("entry_failed: fallback text", "The review could not start.", got)

    # autofix: no fix.sh, a fix.sh that exits 0 and prints braces, and one that exits 3.
    af_f = '.context_updates | "\\(.autofix_ran) \\(.autofix_rc)"'
    AF = fresh("af0"); (AF / "wt").mkdir()
    run_node(ck, "autofix", GRAPH, AF, T / "bin")
    got = routing_check(ck, "autofix, no fix.sh", AF, af_f)
    ck("autofix, no fix.sh: false -1", "false -1", got)
    AF = fresh("af1")
    stub(AF / ".fabro", "fix.sh", "#!/bin/sh\necho {}\n")
    run_node(ck, "autofix", GRAPH, AF, T / "bin")
    got = routing_check(ck, "autofix, fix.sh exits 0", AF, af_f)
    ck("autofix, fix.sh exits 0: true 0", "true 0", got)
    AF = fresh("af3")
    stub(AF / ".fabro", "fix.sh", "#!/bin/sh\necho clippy {x}\nexit 3\n")
    run_node(ck, "autofix", GRAPH, AF, T / "bin")
    got = routing_check(ck, "autofix, fix.sh exits 3", AF, af_f)
    ck("autofix, fix.sh exits 3: true 3", "true 3", got)
    ck("autofix, fix.sh output kept in the tail, braces removed", "1", count("clippy x", file=f"{AF}.out"))
