"""The fast path (ADR 0023): review-merge's queue mode and backlog's fast lane -- route_lane,
next_task's reset, pr_handoff's diff ceiling, demote_lane, and the exclusivity of the edges
the lane adds."""

import re

from harness import (GRAPH, ORIG_PATH, SHARED, cat, count, extract, git, grep, head1, jq, jq_n,
                     lastjson, lines, present, rebase, rm, run, run_files, sh, sh_c, stage, stub,
                     syntax_ok, tail1, write)


def staged(ck, graph, node, d, root):
    script = write(d / f"{node}.sh", rebase(extract(graph, node), root))
    ok, err = syntax_ok(script)
    if not ok:
        ck.fail(f"{node} is not valid POSIX sh", err)
    return script


def awk(program, text=None, *, file=None, vars=()):
    argv = ["awk"]
    for k, v in vars:
        argv += ["-v", f"{k}={v}"]
    argv.append(program)
    if file is not None:
        argv.append(str(file))
    return run(argv, stdin=text if text is not None else "", stderr="drop").out


# ---------------------------------------------------------------------------
# review-merge queue mode (fast-path task 02): rm_setup's merge_mode, report_queued's
# marker-before-label order and tree check, done accepting `queued`, and the two merge_gate
# edges being exclusive.
# ---------------------------------------------------------------------------
def test_review_merge_queue_mode(ck, tmp_path):
    QM = tmp_path / "queuemode"
    (QM / "bin").mkdir(parents=True); (QM / "repo").mkdir()
    s = {n: staged(ck, SHARED, n, QM, QM / "fab") for n in ("rm_setup", "report_queued", "done")}
    stub(QM / "bin", "gh", """#!/bin/sh
echo "$*" >> "$QM_LOG"
case "$1 $2" in
  "pr view") cat "$QM_HEAD_FILE"; exit 0 ;;
  "pr comment") [ -n "$QM_FAIL_COMMENT" ] && exit 1; cp "$5" "$QM_COMMENT_COPY"; exit 0 ;;
esac
exit 0
""")
    repo = QM / "repo"
    git("-C", repo, "init", "-q", "-b", "main")
    git("-C", repo, "config", "user.email", "t@t"); git("-C", repo, "config", "user.name", "t")
    write(repo / "a", "a\n"); git("-C", repo, "add", "a"); git("-C", repo, "commit", "-qm", "one")
    git("-C", repo, "checkout", "-q", "-b", "fabro/run/01QUEUETEST")
    qhead = git("-C", repo, "rev-parse", "HEAD")
    log, comment = QM / "gh.log", QM / "comment.md"
    env = {"QM_LOG": log, "QM_HEAD_FILE": QM / "head", "QM_COMMENT_COPY": comment}
    st = {}

    def qm_run(name, **extra):
        rc = run_files(["sh", s[name]], QM / "q", cwd=repo, path=f"{QM / 'bin'}:{ORIG_PATH}", env={**env, **extra})
        st.update(out=cat(QM / "q.out"), rc=rc)

    def reset():
        rm(QM / "fab"); (QM / "fab").mkdir(); write(log, ""); rm(comment)
        write(QM / "fab/pr_number", "55\n"); write(QM / "fab/base_ref", "main\n"); write(QM / "fab/head_ref", "fast\n")
        write(QM / "fab/pr.json", '{"url":"u","labels":[]}\n'); write(QM / "fab/linked_issues.json", '[{"number":9}]\n')
        write(QM / "fab/run_base_sha", git("-C", repo, "rev-parse", "HEAD") + "\n")
        write(QM / "head", qhead + "\n")

    mode = lambda: jq(".context_updates.merge_mode", lastjson(st["out"]))
    both = lambda: "1" if all((QM / "fab" / f).is_file() and (QM / "fab" / f).stat().st_size for f in ("needs_human_reason", "merge_block_reason")) else "0"

    # 1-2. rm_setup: the mode, and the label removal on every entry.
    reset(); qm_run("rm_setup")
    ck("rm_setup, no file: watch", "watch", mode())
    ck("rm_setup removes merge-on-green", "1", count("pr edit 55 --remove-label merge-on-green", file=log))
    reset(); write(QM / "fab/merge_mode", "queue\n"); qm_run("rm_setup")
    ck("rm_setup, queue: queue", "queue", mode())
    reset(); write(QM / "fab/merge_mode", "QUEUE\n"); qm_run("rm_setup")
    ck("rm_setup, QUEUE: watch", "watch", mode())
    ck("rm_setup writes the normalised mode back", "watch", cat(QM / "fab/merge_mode"))
    # 3. report_queued, head equal to HEAD.
    reset(); qm_run("rm_setup"); qm_run("report_queued")
    ck("report_queued exits 0", "0", st["rc"])
    ck("report_queued: first line is the approval marker", "1",
       count("^<!-- fabro:merge-queue approved head=[0-9a-f]{40} run=01QUEUETEST -->$", head1(comment), flags="E", pipe=True))
    order = [l for l in grep(["-E", "^pr (comment|edit) "], file=log).split("\n") if l and "remove-label" not in l]
    ck("report_queued: comment before label", "pr comment pr edit", " ".join(" ".join(l.split()[:2]) for l in order))
    ck("report_queued: outcome file", "queued", cat(QM / "fab/review_merge_outcome"))
    ck("report_queued: stdout is one JSON object", "1", count(".", st["out"]))
    ck("report_queued: stdout carries merge_mode queue", "queue", jq(".context_updates.merge_mode", st["out"]))
    ck("report_queued: renders the queued heading", "1", count("queued to merge on green", file=comment))
    # 4. a head whose tree differs.
    reset(); qm_run("rm_setup")
    write(repo / "b", "b\n"); git("-C", repo, "add", "b"); git("-C", repo, "commit", "-qm", "two")
    write(QM / "head", git("-C", repo, "rev-parse", "HEAD") + "\n"); git("-C", repo, "reset", "-q", "--hard", "HEAD~1")
    qm_run("report_queued")
    ck("report_queued, other tree: exit 1", "1", st["rc"])
    ck("report_queued, other tree: both reasons", "1", both())
    ck("report_queued, other tree: no label", "0", count("add-label", file=log))
    # 5. a failing comment.
    reset(); qm_run("rm_setup"); qm_run("report_queued", QM_FAIL_COMMENT="1")
    ck("report_queued, comment fails: exit 1", "1", st["rc"])
    ck("report_queued, comment fails: no label", "0", count("add-label", file=log))
    ck("report_queued, comment fails: both reasons", "1", both())
    # 6. done.
    reset(); write(QM / "fab/review_merge_outcome", "queued\n"); qm_run("done")
    ck("done publishes queued", "queued", jq(".context_updates.review_merge_outcome", lastjson(st["out"])))
    # 7. merge_gate edges: exclusive.
    ck("merge_gate: the two eligible edges split on merge_mode", "1 1",
       awk("/merge_mode!=queue/{a=1} /context.merge_mode=queue/{b=1} END{print a, b}",
           grep(["-E", "merge_gate +-> +(watch_checks|report_queued) "], file=SHARED) + "\n"))


# ---------------------------------------------------------------------------
# backlog fast lane (fast-path task 03, ADR 0023): route_lane, next_task's reset of
# rebase_failed, the diff ceiling in pr_handoff, and the exclusivity of the edges that the
# lane adds.
# ---------------------------------------------------------------------------
FL_MATCHES = r'''
    BEGIN { v["lane"] = lane; v["tasks_done"] = td; v["rebase_failed"] = rf; v["merge_ok"] = mo; v["merge_return"] = mr }
    $1 == node && $2 == "->" && match($0, /condition="[^"]*"/) {
        c = substr($0, RSTART + 11, RLENGTH - 12); n = split(c, cl, / && /); ok = 1
        for (i = 1; i <= n && ok; i++) {
            if (cl[i] !~ /^context[.][a-z_]+!?=[A-Za-z0-9_]+$/) { ok = 0; break }
            ne = (cl[i] ~ /!=/); split(cl[i], kv, /!?=/); k = kv[1]; sub(/^context[.]/, "", k)
            if (!(k in v)) { ok = 0; break }
            if (ne ? (v[k] == kv[2]) : (v[k] != kv[2])) ok = 0
        }
        if (ok) m++
    }
    END { print m + 0 }'''

RR_MATCHES = r'''
    BEGIN { v["round"] = r; v["lane"] = ln; v["task_exempt"] = ex }
    $1 == "rework_router" && $2 == "->" && match($0, /condition="[^"]*"/) {
        c = substr($0, RSTART + 11, RLENGTH - 12); n = split(c, cl, / && /); ok = 1
        for (i = 1; i <= n && ok; i++) {
            if (!match(cl[i], /^context[.][a-z_]+(>=|<=|!=|=|<|>)[A-Za-z0-9_]+$/)) { ok = 0; break }
            t = cl[i]; sub(/^context[.]/, "", t); match(t, /(>=|<=|!=|=|<|>)/)
            k = substr(t, 1, RSTART - 1); op = substr(t, RSTART, RLENGTH); val = substr(t, RSTART + RLENGTH)
            x = v[k]
            if (op == "=") g = (x == val); else if (op == "!=") g = (x != val)
            else if (op == "<") g = (x + 0 < val + 0); else if (op == ">") g = (x + 0 > val + 0)
            else if (op == ">=") g = (x + 0 >= val + 0); else g = (x + 0 <= val + 0)
            if (!g) ok = 0
        }
        if (ok) m++
    }
    END { print m + 0 }'''


def test_backlog_fast_lane(ck, tmp_path):
    FL = tmp_path / "fastlane"
    (FL / "bin").mkdir(parents=True)
    route_lane = staged(ck, GRAPH, "route_lane", FL, FL / "fab")
    handoff = staged(ck, GRAPH, "pr_handoff", FL, FL / "fab")
    fab = FL / "fab"
    st = {}

    def fl_issue(labels, body):
        write(fab / "issue.json", jq_n("{number: 42, title: \"Fix the thing\", body: $b, labels: ($l | map({name: .}))}",
                                       "--argjson", "l", labels, "--arg", "b", body, compact=False) + "\n")

    def fl_route(labels, body):
        rm(fab); fab.mkdir(); fl_issue(labels, body)
        r = sh(route_lane, stderr="keep")
        st.update(out=r.out, rc=r.rc)

    lane = lambda: jq(".context_updates.lane", lastjson(st["out"]))
    tasks = fab / "tasks.json"

    # 1. fast.
    fl_route('["agent-in-progress","fast-path"]', "Do it carefully.")
    ck("route_lane fast: exit 0", "0", st["rc"])
    ck("route_lane fast: lane", "fast", lane())
    ck("route_lane fast: lane file", "fast", cat(fab / "lane"))
    ck("route_lane fast: one task", "1", jq("length", file=tasks, raw=False))
    ck("route_lane fast: task source", "fast-path", jq(".[0].source", file=tasks))
    ck("route_lane fast: task title and body", "Fix the thing|Do it carefully.", jq('.[0].title + "|" + .[0].body', file=tasks))
    ck("route_lane fast: task_index 0", "0", cat(fab / "task_index"))
    # 2. standard, and near misses.
    for lbl in ('["agent-in-progress"]', '["Fast-Path"]', '["fast-path-x"]', "[]"):
        fl_route(lbl, "b")
        ck(f"route_lane {lbl}: standard", "standard", lane())
        ck(f"route_lane {lbl}: tasks.json untouched", "absent", present(tasks))
    # 3. an empty (or blank) body.
    fl_route('["fast-path"]', "")
    ck("route_lane empty body", "Resolve issue #42: Fix the thing", jq(".[0].body", file=tasks))
    fl_route('["fast-path"]', "   ")
    ck("route_lane blank body", "Resolve issue #42: Fix the thing", jq(".[0].body", file=tasks))
    # 3b. the planner (#25, closed by ADR 0030): `route_lane` publishes `lane` only -- the
    # trial's per-run `arm` and /tmp/fabro/arm went with `improve` -- and every planning route
    # reaches `plan`.
    for lbl in ('["fast-path"]', '["agent-in-progress"]'):
        fl_route(lbl, "b")
        ck(f"route_lane {lbl}: no arm key", "null|absent", jq(".context_updates.arm", lastjson(st["out"])) + "|" + present(fab / "arm"))
    ck("the improve node is gone", "0", count("^ +improve \\[", file=GRAPH, flags="E"))
    ck("no edge targets improve", "0", count("^ +[a-z_]+ -> improve($| )", file=GRAPH, flags="E"))
    ck("nothing reads the trial's arm", "0", count("context.arm", file=GRAPH))
    into_plan = sorted({re.sub(r" *-> plan", "", l, count=1).replace(" ", "")
                        for l in grep(["-oE", "^ +[a-z_]+ -> plan($| )"], file=GRAPH).split("\n") if l})
    ck("every edge into plan comes from the three handing-off nodes", "improve_gate next_task resolve_merge_gate", " ".join(into_plan))
    ck("next_task falls through to plan", "1", count("^ +next_task -> plan$", file=GRAPH, flags="E"))
    ck("resolve_merge_gate reaches plan on a resolved merge in the standard lane", "1",
       count("merge_return=task && context.lane!=fast", grep(["-E", "^ +resolve_merge_gate -> plan "], file=GRAPH), pipe=True))
    ck("improve_gate's rejected result goes back to plan", "1", count('^ +improve_gate -> plan +\\[condition="outcome=failed"\\]$', file=GRAPH, flags="E"))
    # The edges out of the three nodes that hand a task on stay exclusive: exactly one is
    # unconditional (the fallthrough to the rescue gate or, on next_task, to the planner),
    # and the conditional ones split on a key that only one branch can match.
    for n in ("next_task", "resolve_merge_gate", "improve_gate"):
        ck(f"{n}: exactly one unconditional edge", "1", count("\\[", grep(["-E", f"^ +{n} -> "], file=GRAPH), flags="v", pipe=True))
    edge_awk = lambda pat, prog: awk(prog, grep(["-E", pat], file=GRAPH) + "\n")
    ck("next_task's done edges split on lane", "1 1",
       edge_awk("^ +next_task -> (extra_prep|final_merge) ", "/lane!=fast/{a=1} /lane=fast/{b=1} END{print a+0, b+0}"))
    ck("next_task's coder edge requires the fast lane and an undone task", "1",
       count("tasks_done=false && context.rebase_failed=false && context.lane=fast", grep(["-E", "^ +next_task -> coder "], file=GRAPH), pipe=True))
    ck("resolve_merge_gate's task edges split on lane", "1 1",
       edge_awk("^ +resolve_merge_gate -> (coder|plan) ", "/lane!=fast/{a=1} /lane=fast/{b=1} END{print a+0, b+0}"))
    ck("improve_gate routes each disposition and the retry once", "1 1 1 1",
       edge_awk("^ +improve_gate -> ", "/task_disposition=redundant/{a=1} /task_disposition=split/{b=1} /task_disposition=ready/{c=1} /outcome=failed/{d=1} END{print a+0, b+0, c+0, d+0}"))
    # 4. no issue.json, and one without labels: exit 1, no routing object.
    rm(fab); fab.mkdir()
    r = sh(route_lane, stderr="drop")
    ck("route_lane, no issue.json: exit 1", "1", r.rc)
    ck("route_lane, no issue.json: no routing object", "", lastjson(r.out))
    write(fab / "issue.json", '{"number":1,"title":"t","body":"b"}\n')
    r = sh(route_lane, stderr="drop")
    ck("route_lane, no labels key: exit 1", "1", r.rc)
    ck("route_lane, no labels key: no routing object", "", lastjson(r.out))
    # 5. next_task resets rebase_failed on the done branch (both of its done lines).
    T = tmp_path / "fl_nt"
    nt = stage(ck, "next_task", T)
    write(T / "tasks.json", '[{"id":"a","title":"A","body":"b"}]'); write(T / "task_index", "1\n")
    nt_ctx = lambda k: jq(f".context_updates.{k}", lastjson(sh(nt, stderr="drop").out))
    ck("next_task done: rebase_failed false", "false", nt_ctx("rebase_failed"))
    ck("next_task done: tasks_done true", "true", nt_ctx("tasks_done"))
    write(T / "tasks_coded", "8\n"); write(T / "tasks.json", '[{"id":"a","title":"A","body":"b"},{"id":"b","title":"B","body":"b"}]'); write(T / "task_index", "1\n")
    ck("next_task done after the budget move: rebase_failed false", "false", nt_ctx("rebase_failed"))
    # 6. the ceiling's exclude list equals the hygiene counters' default.
    xp_handoff = run(["sed", "-n", "s/.*XP='\\(\\[.*\\]\\)'.*/\\1/p", str(GRAPH)]).out
    xp_hygiene = run(["sed", "-n", "s/.*exclude_paths:\\(\\[.*\\]\\)}.*/\\1/p", str(SHARED)]).out
    ck("pr_handoff's exclude list is non-empty", "1", "1" if xp_handoff else "0")
    ck("pr_handoff's exclude list equals hygiene's default.json", xp_hygiene, xp_handoff)
    # 7. pr_handoff: the merge mode, in a scratch repository with a bare origin.
    stub(FL / "bin", "gh", """#!/bin/sh
echo "$*" >> "$FL_LOG"
case "$1 $2" in
  "pr view") echo '{"number":7,"title":"t","body":"b","url":"u","state":"OPEN","isCrossRepository":false,"headRefName":"run","baseRefName":"main","headRefOid":"x","labels":[],"closingIssuesReferences":[]}'; exit 0 ;;
  "repo view") exit 1 ;;
esac
exit 0
""")
    log = FL / "gh.log"
    env = {"FL_LOG": log, "FL_DIR": FL}
    path = f"{FL / 'bin'}:{ORIG_PATH}"
    repo = FL / "repo"
    git("init", "-q", "--bare", "-b", "main", FL / "origin.git")
    git("clone", "-q", FL / "origin.git", repo)
    git("-C", repo, "config", "user.email", "t@t"); git("-C", repo, "config", "user.name", "t")
    write(repo / "base", "base\n"); git("-C", repo, "add", "base"); git("-C", repo, "commit", "-qm", "base"); git("-C", repo, "push", "-q", "origin", "main")

    def run_handoff():
        return run_files(["sh", handoff], FL / "h", cwd=repo, path=path, env=env)

    def fl_handoff(lane_name, setup):
        git("-C", repo, "checkout", "-q", "main"); git("-C", repo, "branch", "-q", "-D", "run", check=False)
        git("-C", repo, "checkout", "-q", "-b", "run")
        rm(fab); fab.mkdir(); write(log, ""); write(fab / "pr_number", "7\n")
        if lane_name != "-":
            write(fab / "lane", lane_name + "\n")
        sh_c(f"{setup} && git add -A && git commit -qm change", cwd=repo)
        st["rc"] = run_handoff()
        st["mm"] = cat(fab / "merge_mode")
        st["comments"] = count("pr comment", file=log)

    fl_handoff("fast", "seq 1 30 > code.txt")
    ck("pr_handoff fast, 30 lines: exit 0", "0", st["rc"])
    ck("pr_handoff fast, 30 lines: queue", "queue", st["mm"])
    ck("pr_handoff fast, 30 lines: no comment", "0", st["comments"])
    ck("pr_handoff keeps printing handoff ready", "handoff ready", tail1(FL / "h.out"))
    fl_handoff("fast", "seq 1 201 > code.txt")
    ck("pr_handoff fast, 201 lines: watch", "watch", st["mm"])
    ck("pr_handoff fast, 201 lines: one comment naming the size", "1 1", f"{st['comments']} {count('201 lines', file=log)}")
    fl_handoff("fast", "seq 1 30 > code.txt; seq 1 5000 > package-lock.json")
    ck("pr_handoff fast, 30 lines + 5000-line lockfile: queue", "queue", st["mm"])
    fl_handoff("fast", "mkdir -p dist; seq 1 5000 > dist/bundle.js; seq 1 3 > code.txt")
    ck("pr_handoff fast, a dist/ file at the top level is excluded: queue", "queue", st["mm"])
    fl_handoff("fast", "i=1; while [ $i -le 11 ]; do echo x > f$i; i=$((i+1)); done")
    ck("pr_handoff fast, 11 one-line files: watch", "watch", st["mm"])
    fl_handoff("fast", "i=1; while [ $i -le 10 ]; do echo x > f$i; i=$((i+1)); done")
    ck("pr_handoff fast, 10 one-line files: queue", "queue", st["mm"])
    fl_handoff("fast", "seq 1 30 > code.txt; echo partial > /dev/null")
    write(fab / "remainder_kind", "partial\n")
    run_handoff()
    ck("pr_handoff fast with remainder_kind: watch", "watch", cat(fab / "merge_mode"))
    ck("pr_handoff fast with remainder_kind: no comment", "0", count("pr comment", file=log))
    fl_handoff("standard", "seq 1 30 > code.txt")
    ck("pr_handoff standard: watch", "watch", st["mm"])
    fl_handoff("-", "seq 1 30 > code.txt")
    ck("pr_handoff with no lane file: watch", "watch", st["mm"])
    fl_handoff("fast", "seq 1 30 > code.txt")
    git("-C", repo, "update-ref", "-d", "refs/remotes/origin/main"); git("-C", repo, "remote", "set-url", "origin", FL / "nowhere.git")
    run_handoff()
    ck("pr_handoff fast, the base cannot be diffed: watch", "watch", cat(fab / "merge_mode"))

    # 8. edge exclusivity: at most one conditional edge matches each state that a node can publish.
    worst, zero = 0, ""
    for n in ("next_task", "route_lane", "resolve_merge_gate"):
        for ln in ("fast", "standard"):
            for td in ("true", "false"):
                for rf in ("true", "false"):
                    for mo in ("true", "false"):
                        for mr in ("task", "pr"):
                            if td == "true" and rf == "true":
                                continue
                            if n == "route_lane" and td + rf + mo != "falsefalsefalse":
                                continue
                            if n == "resolve_merge_gate" and (td + rf != "falsefalse" or mo != "true"):
                                continue
                            if n != "resolve_merge_gate" and mr == "pr":
                                continue
                            m = int(awk(FL_MATCHES, file=GRAPH, vars=(("node", n), ("lane", ln), ("td", td), ("rf", rf), ("mo", mo), ("mr", mr))))
                            worst = max(worst, m)
                            # next_task's standard-lane task case has no conditional edge: it
                            # falls through to `plan` unconditionally (ADR 0030).
                            if n == "next_task" and td + rf == "falsefalse" and ln == "standard":
                                continue
                            if m == 0:
                                zero += f" {n}/{ln}/{td}/{rf}/{mo}/{mr}"
    ck("no state matches two conditional edges out of next_task, route_lane or resolve_merge_gate", "1", worst)
    ck("every published state has a matching conditional edge (standard next_task falls to plan)", "", zero)

    # 9. demote_lane (task 04).
    dl = FL / "dl"
    demote = staged(ck, GRAPH, "demote_lane", FL, dl)
    stub(FL / "bin", "gh", """#!/bin/sh
echo "$*" >> "$FL_LOG"
case "$1 $2" in
  "issue edit")
    case "$*" in *"--remove-label fast-path"*) [ -f "$FL_DIR/edit_fail" ] && exit 1 ;; esac
    exit 0 ;;
  "issue view") if [ -f "$FL_DIR/still_there" ]; then printf 'agent-in-progress\\nfast-path\\n'; else printf 'agent-in-progress\\n'; fi; exit 0 ;;
  "issue comment") for a in "$@"; do p="$q"; q="$a"; [ "$p" = "--body-file" ] && cp "$a" "$FL_DIR/comment.md"; done; exit 0 ;;
esac
exit 0
""")

    def fl_demote(number):
        rm(dl, FL / "comment.md", FL / "edit_fail", FL / "still_there"); (dl / "feedback").mkdir(parents=True); write(log, "")
        if number != "-":
            write(dl / "issue.json", f'{{"number":{number}}}\n')
        write(dl / "feedback/rework.md", "## Review findings to fix\n\n\x1b[31mfirst finding\x1b[0m\n- second\n")

    def demote_run():
        st["rc"] = run_files(["sh", demote], FL / "d", path=path, env=env)

    fl_demote("42"); demote_run()
    ck("demote_lane: exit 0", "0", st["rc"])
    ck("demote_lane: prints demoted", "demoted", tail1(FL / "d.out"))
    order = grep(["-n", "-e", "remove-label fast-path", "-e", "issue view", "-e", "add-label agent --remove-label agent-in-progress", "-e", "issue comment"], file=log)
    ck("demote_lane: call order", "1 2 3 4", " ".join(l.split(":")[0] for l in order.split("\n") if l))
    ck("demote_lane: comment has the fixed sentence", "1", count("^The fast path could not land this issue", file=FL / "comment.md"))
    ck("demote_lane: comment has the findings, without escapes", "1 0",
       f"{count('first finding', file=FL / 'comment.md')} {count(chr(27), file=FL / 'comment.md')}")
    fl_demote("42"); write(FL / "edit_fail", ""); demote_run()
    ck("demote_lane remove fails: exit 1", "1", st["rc"])
    ck("demote_lane remove fails: no add-label agent", "0", count("--add-label agent", file=log))
    fl_demote("42"); write(FL / "still_there", ""); demote_run()
    ck("demote_lane label still there: exit 1", "1", st["rc"])
    ck("demote_lane label still there: no add-label agent", "0", count("--add-label agent", file=log))
    fl_demote("-"); demote_run()
    ck("demote_lane no number: exit 1", "1", st["rc"])
    ck("demote_lane no number: no gh call", "0", lines(log))
    fl_demote("-"); write(dl / "issue_number", "77\n"); demote_run()
    ck("demote_lane falls back to issue_number", "0 1", f"{st['rc']} {count('issue edit 77 --remove-label fast-path', file=log)}")
    fl_demote("42"); rm(dl / "feedback/rework.md"); demote_run()
    ck("demote_lane without rework.md: still demotes", "0 1", f"{st['rc']} {count('add-label agent', file=log)}")

    # rework_router exclusivity: at most one conditional edge matches each (round, lane, task_exempt).
    rr_worst, rr_bad = 0, ""
    for r in range(1, 8):
        for ln in ("fast", "standard"):
            for ex in ("true", "false"):
                # fast + task_exempt is unreachable: a fast task's source is fast-path, never extra-review.
                if ln + ex == "fasttrue":
                    continue
                m = int(awk(RR_MATCHES, file=GRAPH, vars=(("r", r), ("ln", ln), ("ex", ex))))
                rr_worst = max(rr_worst, m)
                # round 1..5 climb the ladder; round 6 has one conditional route; 7 falls to
                # rescue_brief (zero matches).
                if r == 6 and (ln == "fast" or ex == "true"):
                    want = 1
                elif (r == 6 and ln == "standard" and ex == "false") or r == 7:
                    want = 0
                else:
                    want = 1
                if m != want:
                    rr_bad += f" {r}/{ln}/{ex}={m}"
    ck("no state matches two conditional edges out of rework_router", "1", rr_worst)
    ck("rework_router: round 6 fast demotes, standard exempt drops, standard otherwise rescues", "", rr_bad)
