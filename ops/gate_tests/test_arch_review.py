"""arch-review: scan_gate, file_issues and summarize, and the triage queue (build_queue,
next_issue) (ADR 0012)."""

import time

from harness import (ARCH, ORIG_PATH, cat, count, head1, jq, lastjson, mkreadok, rm, sh, stage,
                     stub, write)


def cand(slug, strength):
    return (f'{{"slug":"{slug}","title":"refactor: {slug}","strength":"{strength}","files":["a.py"],'
            f'"problem":"p","solution":"s","benefits":"b","diagram":""}}')


def summary(out):
    return jq(".context_updates.arch_summary", lastjson(out))


def test_arch_review_scan_and_file(ck, tmp_path):
    T = tmp_path / "arch"
    (T / "bin").mkdir(parents=True); (T / "arch").mkdir()
    s = {n: stage(ck, n, T, graph=ARCH) for n in ("scan_gate", "file_issues", "summarize")}
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue list")   cat "$GH_STATE/live.json"; exit 0 ;;
  "issue create") C=$(cat "$GH_STATE/counter" 2>/dev/null || echo 400); C=$((C+1)); echo $C > "$GH_STATE/counter"
                  echo "https://github.com/o/r/issues/$C"; exit 0 ;;
  "repo view")    echo "jelly-swipe"; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    run = lambda n: sh(s[n], path=path, env=env)
    log = T / "gh.log"
    mkreadok(T, "ok1", "hotspots.txt", "existing.json")

    def scandump(doc):
        write(T / "arch/candidates.json", jq("._io.visit = $v", doc, args=["--arg", "v", "ok1"], raw=False, pipe=True) + "\n")

    ctx = lambda out, k: jq(f".context_updates.{k}", lastjson(out))

    # 1. A valid file.
    write(T / "arch/scan_attempts", "0\n")
    scandump(f'{{"candidates":[{cand("one-a", "Strong")},{cand("two-b", "Worth exploring")}]}}')
    r = run("scan_gate")
    ck("scan_gate valid: exit 0", "0", r.rc)
    ck("scan_gate valid: ok", "ok", ctx(r.out, "scan_status"))
    ck("scan_gate valid: count", "2", ctx(r.out, "candidate_count"))

    # 2. A duplicate slug: one repair turn, then failed.
    write(T / "arch/scan_attempts", "0\n")
    scandump(f'{{"candidates":[{cand("one-a", "Strong")},{cand("one-a", "Strong")}]}}')
    ck("scan_gate dup: first retries", "1", run("scan_gate").rc)
    r = run("scan_gate")
    ck("scan_gate dup: second exit 0", "0", r.rc)
    ck("scan_gate dup: failed", "failed", cat(T / "arch/scan_status"))

    # 3. An unknown strength.
    write(T / "arch/scan_attempts", "0\n")
    scandump(f'{{"candidates":[{cand("one-a", "Maybe")}]}}')
    ck("scan_gate bad strength: retry", "1", run("scan_gate").rc)

    # 3b. Fields file_issues concatenates must be strings: a number passes an emptiness
    #     test and then stops file_issues partway through the list.
    for bad in (".files = [1]", ".problem = 5", ".slug = 123", ".diagram = 7"):
        write(T / "arch/scan_attempts", "0\n")
        scandump(jq(f"{{candidates: [({cand('one-a', 'Strong')}) | {bad}]}}", "null", args=["-c"], raw=False))
        ck(f"scan_gate {bad}: retry", "1", run("scan_gate").rc)

    # 4. file_issues: 5 Strong, 5 Worth exploring, 1 Speculative; c0 already filed.
    cands = ([cand(f"c{i}", "Strong") for i in range(5)] + [cand(f"c{i}", "Worth exploring") for i in range(5, 10)]
             + [cand("spec-one", "Speculative")])
    scandump('{"candidates":[' + ",".join(cands) + "]}")
    write(T / "live.json", '[{"body":"<!-- fabro:arch-candidate slug=c0 -->\\nold"}]')
    rm(T / "counter"); write(log, "")
    r = run("file_issues")
    ck("file: caps at 8", "8", count("^issue create", file=log))
    ck("file: skips an existing slug", "0", count("refactor: c0 --body-file", file=log))
    ck("file: never Speculative", "0", count("refactor: spec-one --body-file", file=log))
    ck("file: stops after the cap", "0", count("refactor: c9 --body-file", file=log))
    ck("file: all three labels", "8", count("--label architecture --label needs-triage --label ai-generated", file=log))
    ck("file: marker is line one", "<!-- fabro:arch-candidate slug=c8 -->", head1(T / "arch/body.md"))
    ck("file: filed_count", "8", ctx(r.out, "filed_count"))

    # 5. summarize: the two shapes of the line.
    write(T / "arch/scan_status", "ok\n"); write(T / "arch/filed.json", "[401,402]\n")
    ck("summary: filed", "jelly-swipe: 2 filed (#401 #402); 0 triaged: 0 agent, 0 split, 0 needs-info, 0 not actionable",
       summary(run("summarize").out))
    write(T / "arch/scan_status", "failed\n"); write(T / "arch/filed.json", "[]\n")
    ck("summary: scan failed", "jelly-swipe: scan failed, 0 filed; 0 triaged: 0 agent, 0 split, 0 needs-info, 0 not actionable",
       summary(run("summarize").out))


def test_arch_review_triage_queue(ck, tmp_path):
    T = tmp_path / "archq"
    (T / "bin").mkdir(parents=True); (T / "arch").mkdir()
    s = {n: stage(ck, n, T, graph=ARCH) for n in ("build_queue", "next_issue", "summarize")}
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$*" in
  "issue list --label needs-info"*)   cat "$GH_STATE/info.json"; exit 0 ;;
  "issue list --label needs-triage"*) cat "$GH_STATE/triage.json"; exit 0 ;;
  "repo view"*)                       echo "jelly-swipe"; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    run = lambda n: sh(s[n], path=path, env=env)
    ctx = lambda out, k: jq(f".context_updates.{k}", lastjson(out))

    # 1. build_queue: this run's issues first, answered needs-info next, then needs-triage
    #    oldest first, at most 10 others, no duplicates.
    write(T / "arch/filed.json", "[401,402]\n")
    write(T / "info.json", '[{"number":6,"comments":[{"body":"<!-- fabro:triage-questions -->"}]},{"number":5,"comments":[{"body":"the answer is yes"}]}]\n')
    write(T / "triage.json", '[{"number":401},{"number":19},{"number":3},{"number":7},{"number":8},{"number":11},{"number":12},{"number":13},{"number":14},{"number":15},{"number":16},{"number":17},{"number":18}]\n')
    r = run("build_queue")
    ck("queue: exit 0", "0", r.rc)
    ck("queue: order and cap", "401 402 5 3 7 8 11 12 13 14 15 16", jq('map(tostring) | join(" ")', file=T / "queue.json"))
    ck("queue: unanswered not queued", "0", jq("[.[] | select(. == 6)] | length", file=T / "queue.json", raw=False))
    ck("queue: length published", "12", ctx(r.out, "queue_length"))
    ck("queue: lists past 100 issues", "2", count("--limit 1000", file=T / "gh.log"))
    ck("graph: signature limit covers the loop", "1", count("^        loop_restart_signature_limit=20,$", file=ARCH))

    # 2. next_issue walks the queue and counts each outcome.
    write(T / "queue.json", "[11,12]\n"); write(T / "queue_index", "0\n"); write(T / "run_started", f"{int(time.time())}\n")
    write(T / "tally.json", '{"ready":0,"needs_info":0,"not_actionable":0,"skipped":0,"released":0,"split":0}\n')
    write(T / "arch/deferred", "0\n"); rm(T / "arch/in_flight")
    r = run("next_issue")
    ck("next: first issue", "11", cat(T / "issue_number"))
    ck("next: publishes issue_number", "11", ctx(r.out, "issue_number"))
    ck("next: not done", "false", ctx(r.out, "queue_done"))
    write(T / "triage_outcome", "ready\n")
    run("next_issue")
    ck("next: counts ready", "1", jq(".ready", file=T / "tally.json"))
    ck("next: second issue", "12", cat(T / "issue_number"))
    write(T / "triage_outcome", "needs_info\n")
    last = run("next_issue")
    ck("next: counts needs_info", "1", jq(".needs_info", file=T / "tally.json"))
    write(T / "queue.json", "[5]\n"); write(T / "queue_index", "0\n"); rm(T / "arch/in_flight")
    write(T / "tally.json", '{"ready":0,"split":0,"needs_info":0,"not_actionable":0,"skipped":0,"released":0}\n')
    run("next_issue")
    write(T / "triage_outcome", "split\n")
    run("next_issue")
    ck("next: counts split", "1", jq(".split", file=T / "tally.json"))
    # The shell suite read this from the needs_info visit's output, the last one it kept.
    ck("next: done at the end", "true", ctx(last.out, "queue_done"))

    # 3. The 6-hour budget stops new triages and records what is left.
    write(T / "queue.json", "[21,22,23]\n"); write(T / "queue_index", "1\n"); rm(T / "arch/in_flight")
    write(T / "run_started", f"{int(time.time()) - 21601}\n")
    r = run("next_issue")
    ck("budget: done", "true", ctx(r.out, "queue_done"))
    ck("budget: deferred", "2", cat(T / "arch/deferred"))

    # 4. summarize: the full line.
    write(T / "arch/scan_status", "ok\n"); write(T / "arch/filed.json", "[401,402,403]\n")
    write(T / "tally.json", '{"ready":10,"split":1,"needs_info":1,"not_actionable":1,"skipped":0,"released":1}\n')
    write(T / "arch/deferred", "2\n")
    ck("summary: full line",
       "jelly-swipe: 3 filed (#401 #402 #403); 14 triaged: 10 agent, 1 split, 1 needs-info, 1 not actionable, 1 released; 2 left for the next review",
       summary(run("summarize").out))
