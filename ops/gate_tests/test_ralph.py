"""ralph (docs/ralph): the Iteration workflow's command nodes. Each is extracted from ralph's
own graph and rebased onto its own scratch directory; the edge checks read the parsed graph
rather than the text, because a wrong route is invisible to `fabro validate`."""


from harness import (ORIG_PATH, RAPH, WF, cat, count, edges, extract, git,
                     grep, head1, jq, jq_n, lastjson, mkreadok, present, rebase, rm, run, sh,
                     stage, stub, syntax_ok, write)

ENTRY_GH = """#!/bin/sh
case "$1 $2" in
  "pr view")     echo '{"number":7,"title":"t","body":"b","url":"https://x/pull/7","state":"OPEN","isCrossRepository":false,"headRefName":"feat","baseRefName":"main","headRefOid":"abc","labels":[],"closingIssuesReferences":[]}' ;;
  "issue list")  case "$*" in *needs-triage*) echo '[{"number":5,"title":"x"}]' ;; *) echo '[]' ;; esac ;;
esac
exit 0
"""
ENTRY_FC = """#!/bin/sh
echo 'FABRO-CODE LEAKED TO STDOUT'
case "$1" in index) printf 'ok\\n' > "$FABRO_TEST_STATE" ;; esac
exit 0
"""


def stage_r(ck, node, T, root=None):
    return stage(ck, node, T, graph=RAPH, root=root, label=f"{node} (ralph)")


def ctx(j, key):
    return jq(f".context_updates.{key}", j)


def keep_only(d, name):
    """`find d -mindepth 1 -maxdepth 1 ! -name name -exec rm -rf {} +`."""
    d.mkdir(parents=True, exist_ok=True)
    for p in d.iterdir():
        if p.name != name:
            rm(p)


def bare_and_repo(origin, repo, files):
    """A bare origin whose HEAD is main, and a main-branch repository that pushed to it."""
    git("init", "-q", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/main")
    git("init", "-q", "-b", "main", repo)
    git("-C", repo, "config", "user.email", "t@t"); git("-C", repo, "config", "user.name", "t")
    for name, text in files.items():
        p = write(repo / name, text)
        if name.endswith(".sh"):
            p.chmod(0o755)
    git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qm", "base")
    git("-C", repo, "remote", "add", "origin", origin)
    git("-C", repo, "push", "-q", "origin", "main")


def append(p, text):
    with open(p, "a") as f:
        f.write(text)


def test_ralph_prep(ck, tmp_path):
    RP = tmp_path / "ralph-prep-repo"
    for sub in ("repo", "bin", "sb"):
        (RP / sub).mkdir(parents=True)
    git("-C", RP / "repo", "init", "-q", "-b", "main", ".")
    git("-C", RP / "repo", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    stub(RP / "bin", "gh", ENTRY_GH)
    stub(RP / "bin", "fabro-code", ENTRY_FC)
    stub(RP / "repo/.fabro", "setup.sh", "#!/bin/sh\ntrue\n")
    prep = write(RP / "prep.sh", rebase(extract(RAPH, "prep"), RP / "sb"))
    ok, err = syntax_ok(prep)
    if not ok:
        ck.fail("prep (ralph) is not valid POSIX sh", err)
    sb = RP / "sb"

    def rp_prep(body):
        rm(sb); sb.mkdir()
        write(sb / "issue.json", jq_n('{number:12,title:"t",body:$b,labels:[]}', "--arg", "b", body) + "\n")
        sh(prep, cwd=RP / "repo", path=f"{RP / 'bin'}:{ORIG_PATH}", env={"FABRO_TEST_STATE": sb / "code_index", "FABRO_AGENT_GUIDE": ""})

    rp_prep("Intro line.\n\n## Task map\n\n- first: the parser\n- second: the caller\n\n## Notes\nignored")
    ck("prep: task-map.md is the section", "2", count("^- ", file=sb / "task-map.md"))
    ck("prep: task-map.md stops at the next heading", "0", count("ignored", file=sb / "task-map.md"))
    rp_prep("No map here, just prose.")
    ck("prep: no Task map writes no file", "absent", present(sb / "task-map.md"))
    ck("prep: phase starts pre", "pre", cat(sb / "phase"))
    ck("prep: five counters", "0|0|0|0|0", "|".join(cat(sb / f) for f in ("iterations", "post_iterations", "refute_visits", "final_fix_attempts", "plan_attempts")))
    write(sb / "loop_exhausted", "x"); write(sb / "progress.md", "x")
    rp_prep("No map here, just prose.")
    ck("prep: clears loop_exhausted", "absent", present(sb / "loop_exhausted"))
    ck("prep: clears progress.md", "absent", present(sb / "progress.md"))


def test_ralph_plan_gate(ck, tmp_path):
    T = tmp_path / "ralph-plan"
    gate = stage_r(ck, "plan_gate", T)
    mkreadok(T, "ok1")

    def setup(task_list):
        for p in list(T.glob("*.json")) + [T / "plan_attempts"]:
            rm(p)
        write(T / "task_list.json", jq("._io.visit=$v", task_list, args=["--arg", "v", "ok1"], raw=False, pipe=True) + "\n")

    def task(i, title):
        return jq_n('{id:$i,title:$t,body:"b",acceptance:["a"],files:[],covers:["c"],context:"## Plan\n1. path:1-2 do it"}', "--arg", "i", i, "--arg", "t", title)

    st = {}

    def go():
        r = sh(gate)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    status = lambda: ctx(st["j"], "plan_status")
    lst = lambda tasks, disp="ready", summary="s": jq_n("{disposition:$d,summary:$s,contradictions:[],tasks:$t}", "--arg", "d", disp, "--arg", "s", summary, "--argjson", "t", tasks)

    setup(lst("[" + ",".join([task("t1", "A"), task("t2", "B"), task("t3", "C")]) + "]"))
    go()
    ck("pg ready: exits 0", "0", st["rc"])
    ck("pg ready: routes ready", "ready", status())
    ck("pg ready: three tasks", "3", jq("length", file=T / "tasks.json", raw=False))
    ck("pg ready: source stamped", "plan", jq(".[0].source", file=T / "tasks.json"))
    ck("pg ready: round 0", "0", jq(".[0].round", file=T / "tasks.json"))
    ck("pg ready: status open", "open", jq(".[0].status", file=T / "tasks.json"))
    ck("pg ready: misses 0", "0", jq(".[0].misses", file=T / "tasks.json"))
    ck("pg ready: task_index 0", "0", cat(T / "task_index"))
    ck("pg ready: remainder empty", "0", jq("length", file=T / "remainder.json", raw=False))
    ck("pg ready: no stuck reason", "absent", present(T / "stuck_reason.md"))
    ck("pg ready: one routing object", "1", count("context_updates", st["out"]))

    setup(lst("[" + ",".join(task(f"t{i}", f"T{i}") for i in range(1, 11)) + "]")); go()
    ck("pg ten: eight in tasks.json", "8", jq("length", file=T / "tasks.json", raw=False))
    ck("pg ten: two in the remainder", "2", jq("length", file=T / "remainder.json", raw=False))
    ck("pg ten: remainder keeps titles", "T9|T10", jq('[.[].title] | join("|")', file=T / "remainder.json"))

    setup(lst("[" + task("t1", "A") + "," + task("t1", "B") + "]")); go()
    ck("pg duplicate id: first attempt exits 1", "1", st["rc"])
    ck("pg duplicate id: counter 1", "1", cat(T / "plan_attempts"))
    go()
    ck("pg duplicate id: second attempt exits 0", "0", st["rc"])
    ck("pg duplicate id: routes invalid", "invalid", status())

    setup(jq_n('{disposition:"ready",summary:"s",contradictions:[],tasks:[]}')); go()
    ck("pg ready with no tasks: first attempt exits 1", "1", st["rc"])
    go()
    ck("pg ready with no tasks: invalid", "invalid", status())

    setup(jq_n('{disposition:"contradiction",summary:"Resend needs a backend the issue rules out.",\n  contradictions:[{criterion:"The screen offers a \\"Resend email\\" action",non_goal:"No backend changes",needed_surface:"a backend that calls the Auth0 resend API"}],\n  tasks:[]}'))
    go()
    ck("pg contradiction: exits 0", "0", st["rc"])
    ck("pg contradiction: routes", "contradiction", status())
    ck("pg contradiction: return source", "plan", jq(".source", file=T / "return.json"))
    ck("pg contradiction: return why", "a backend that calls the Auth0 resend API", jq(".items[0].why", file=T / "return.json"))
    q = ctx(st["j"], "triage_questions")
    ck("pg contradiction: questions name it", "1", count("Resend email action (conflicts with: No backend changes)", q, pipe=True))
    ck("pg contradiction: no quote in questions", "0", count('\\"', q, pipe=True))
    ck("pg contradiction: no tasks written", "absent", present(T / "tasks.json"))

    setup(jq_n('{disposition:"contradiction",summary:"s",contradictions:[],tasks:[]}')); go()
    ck("pg empty contradiction: retries", "1", st["rc"])

    setup(jq_n('{disposition:"needs_human",summary:"blocked by #1278",contradictions:[],tasks:[]}')); go()
    ck("pg needs_human: routes", "needs_human", status())
    ck("pg needs_human: writes reason", "blocked by #1278", cat(T / "stuck_reason.md"))

    setup(jq_n('{disposition:"redundant",summary:"s",contradictions:[],tasks:[]}')); go()
    ck("pg redundant: routes", "redundant", status())

    # No receipt for this visit: invalid, and the retry names the submit tool.
    setup(jq_n("{disposition:\"ready\",summary:\"s\",contradictions:[],tasks:$t}", "--argjson", "t", "[" + task("t1", "A") + "]"))
    write(T / "task_list.json", jq("{disposition,summary,contradictions,tasks}", file=T / "task_list.json", args=["-c"], raw=False))
    go()
    ck("pg no receipt: first attempt exits 1", "1", st["rc"])
    ck("pg no receipt: names submit", "1", count("submit tool", st["out"]))


def test_ralph_edges(ck):
    redg = edges(RAPH)
    pg = "\n".join(l[len("plan_gate->"):] for l in redg.split("\n") if l.startswith("plan_gate->"))
    ck("pg edges: seven exits", "7", count(".", pg))
    statuses = run(["sort"], stdin=grep("context.plan_status", pg) + "\n").out
    ck("pg edges: the conditional set is exactly the five statuses",
       "close_noop context.plan_status=invalid|close_noop context.plan_status=redundant|mark_stuck context.plan_status=needs_human|pick context.plan_status=ready|return_to_triage context.plan_status=contradiction",
       statuses.replace("\n", "|"))
    ck("pg edges: the retry is conditional on failure", "1", count("plan outcome=failed", pg, flags="xF"))
    ck("pg edges: unconditional escape to stop_reason", "1", count("stop_reason ", pg, flags="xF"))
    ck("pg edges: no bare exit", "0", count("^exit ", pg))
    ck("edges: plan_gate has one unconditional edge", "1", count(" $", pg))


R_MATCHES = r'''
    BEGIN { n = split(pairs, ps, / /); for (i = 1; i <= n; i++) { split(ps[i], kv, "="); v[kv[1]] = kv[2] } }
    $1 == node && $2 == "->" && match($0, /condition="[^"]*"/) {
        c = substr($0, RSTART + 11, RLENGTH - 12); n2 = split(c, cl, / && /); ok = 1
        for (i = 1; i <= n2 && ok; i++) {
            if (cl[i] !~ /^context[.][a-z_]+!?=[A-Za-z0-9_]+$/) { ok = 0; break }
            ne = (cl[i] ~ /!=/); split(cl[i], kv, /!?=/); k = kv[1]; sub(/^context[.]/, "", k)
            if (!(k in v)) { ok = 0; break }
            if (ne ? (v[k] == kv[2]) : (v[k] != kv[2])) ok = 0
        }
        if (ok) m++
    }
    END { print m + 0 }'''


def r_matches(node, *pairs):
    """The number of evaluable conditional edges out of node that match the k=v state."""
    return int(run(["awk", "-v", f"node={node}", "-v", f"pairs={' '.join(pairs)}", R_MATCHES, str(RAPH)]).out)


def test_ralph_pick(ck, tmp_path):
    RP = tmp_path / "ralph-pick"
    T = RP / "sb"
    T.mkdir(parents=True)
    pick = stage_r(ck, "pick", T)
    repo, origin = RP / "repo", RP / "origin.git"

    def rp_repo():
        rm(repo, origin)
        # Keep the staged pick.sh: the fixtures rebuild the repository and the state
        # directory, not the node under test.
        keep_only(T, "pick.sh")
        bare_and_repo(origin, repo, {"a.txt": "base\n"})
        write(T / "phase", "pre\n")

    tasks = lambda t: write(T / "tasks.json", t)
    st = {}

    def go():
        r = sh(pick, cwd=repo)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    val = lambda k: ctx(st["j"], k)
    cur = lambda: jq(".id", file=T / "current_task.json")

    rp_repo()
    tasks('[{"id":"t1","status":"open","misses":0,"context":"PLAN ONE"},{"id":"t2","status":"open","misses":0},{"id":"t3","status":"open","misses":0}]')
    go()
    ck("pick: exits 0", "0", st["rc"])
    ck("pick: loop work", "work", val("loop"))
    ck("pick: rung 1", "1", val("rung"))
    ck("pick: phase pre", "pre", val("phase"))
    ck("pick: no rebase", "false", val("rebase_failed"))
    ck("pick: loop_exhausted false", "false", val("loop_exhausted"))
    ck("pick: first task selected", "t1", cur())
    ck("pick: task-context.md is the plan", "1", count("PLAN ONE", file=T / "task-context.md"))
    ck("pick: no Last attempt on a first visit", "0", count("## Last attempt", file=T / "task-context.md"))
    ck("pick: task_base_sha written", "1", count(cat(T / "task_base_sha"), git("-C", repo, "rev-parse", "HEAD"), pipe=True))
    ck("pick: one routing object", "1", count("context_updates", st["out"]))

    tasks('[{"id":"t1","status":"open","misses":3},{"id":"t2","status":"open","misses":0}]'); go()
    ck("pick: 3 misses is rung 2", "2", val("rung"))
    tasks('[{"id":"t1","status":"open","misses":6},{"id":"t2","status":"open","misses":0}]'); go()
    ck("pick: 6 misses is rung 4", "4", val("rung"))
    tasks('[{"id":"t1","status":"open","misses":8},{"id":"t2","status":"open","misses":0}]'); go()
    ck("pick: 8 misses blocks the task", "blocked", jq(".[0].status", file=T / "tasks.json"))
    ck("pick: 8 misses moves to the next", "t2", cur())
    ck("pick: the next task is still open", "open", jq(".[1].status", file=T / "tasks.json"))

    tasks('[{"id":"t1","status":"passed"},{"id":"t2","status":"passed"}]'); go()
    ck("pick: every task passed is done", "done", val("loop"))
    ck("pick: done has no rung", "0", val("rung"))
    tasks('[{"id":"t1","status":"passed"},{"id":"t2","status":"blocked"}]'); go()
    ck("pick: a blocked task exhausts", "exhausted", val("loop"))
    ck("pick: exhausted writes the file", "true", cat(T / "loop_exhausted"))
    ck("pick: exhausted sets the key", "true", val("loop_exhausted"))

    tasks('[{"id":"t1","status":"open"},{"id":"t2","status":"open"},{"id":"t3","status":"open"}]')
    write(T / "iterations", "9\n"); go()
    ck("pick: the cap is 3x the list (9 of 9)", "exhausted", val("loop"))
    tasks(jq_n('[range(30) | {id: ("t" + (. | tostring)), status: "open"}]'))
    write(T / "iterations", "23\n"); go()
    ck("pick: 23 of 24 still works", "work", val("loop"))
    write(T / "iterations", "24\n"); go()
    ck("pick: the cap is at most 24", "exhausted", val("loop"))

    tasks('[{"id":"t1","status":"open"}]')
    rm(T / "iterations", T / "loop_exhausted", T / "merge_block_reason", T / "needs_human_reason")
    write(T / "phase", "post\n"); write(T / "post_iterations", "2\n"); go()
    ck("pick post: 2 Iterations exhaust", "exhausted", val("loop"))
    ck("pick post: phase published", "post", val("phase"))
    ck("pick post: merge_block_reason written", "CI is red after 2 post-PR Iterations", cat(T / "merge_block_reason"))
    ck("pick post: needs_human_reason written", "CI is red after 2 post-PR Iterations", cat(T / "needs_human_reason"))

    rp_repo()
    tasks('[{"id":"t1","status":"open","misses":1,"context":"THE PLAN"}]')
    write(T / "feedback/rework.md", "THE REVIEWER SAID NO\n")
    go()
    ck("pick: last attempt appended", "1", count("## Last attempt", file=T / "task-context.md"))
    ck("pick: the feedback is quoted", "1", count("THE REVIEWER SAID NO", file=T / "task-context.md"))
    ck("pick: plan still first", "1", count("THE PLAN", file=T / "task-context.md"))

    rp_repo()
    tasks('[{"id":"t0","status":"passed"},{"id":"t1","status":"open"}]')
    git("-C", repo, "checkout", "-q", "-b", "other")
    write(repo / "a.txt", "remote\n"); git("-C", repo, "commit", "-qam", "remote")
    git("-C", repo, "push", "-q", "origin", "other:main")
    git("-C", repo, "checkout", "-q", "main")
    write(repo / "a.txt", "local\n"); git("-C", repo, "commit", "-qam", "local")
    go()
    ck("pick conflict: rebase_failed true", "true", val("rebase_failed"))
    ck("pick conflict: loop stays work", "work", val("loop"))
    ck("pick conflict: tree is clean", "", git("-C", repo, "status", "--porcelain"))
    ck("pick conflict: no merge in progress", "absent", present(repo / ".git/MERGE_HEAD"))
    # resolve_merge requires current_task.json, and on the first visit no Task has written it.
    ck("pick conflict, first visit: the first open Task is resolve_merge's", "t1", cur())
    ck("pick conflict: no rung file", "absent", present(T / "rung"))
    write(T / "current_task.json", '{"id":"t9"}'); go()
    ck("pick conflict, later visit: the current Task is kept", "t9", cur())

    write(T / "phase", "post\n"); rm(T / "current_task.json")
    before = git("-C", repo, "rev-parse", "HEAD")
    go()
    ck("pick post: origin/main is not merged here", before, git("-C", repo, "rev-parse", "HEAD"))
    ck("pick post: no rebase", "false", val("rebase_failed"))


def test_ralph_record(ck, tmp_path):
    T = tmp_path / "ralph-record"
    rec = stage_r(ck, "record", T)
    mkreadok(T, "ok1")

    def setup(iteration="", check_ok="", phase=""):
        rm(T / "iteration.json", T / "check_ok", T / "task_block_scope.md", T / "feedback/rework.md", T / "progress.md", T / "task_index")
        write(T / "phase", "pre\n")
        (T / "feedback").mkdir(exist_ok=True)
        write(T / "tasks.json", '[{"id":"t1","title":"A","status":"open","iterations":0,"misses":0},{"id":"t2","title":"B","status":"open","iterations":0,"misses":0}]')
        write(T / "current_task.json", '{"id":"t1","title":"A"}')
        if iteration:
            write(T / "iteration.json", jq("._io.visit=$v", iteration, args=["--arg", "v", "ok1"], raw=False, pipe=True) + "\n")
        if check_ok:
            write(T / "check_ok", check_ok + "\n")
        if phase:
            write(T / "phase", phase + "\n")
        write(T / "iterations", "0\n")
        write(T / "post_iterations", "0\n")

    st = {}

    def go():
        r = sh(rec)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    passed = lambda: ctx(st["j"], "iteration_passed")
    t0 = lambda k: jq(f".[0].{k}", file=T / "tasks.json")

    setup('{"status":"done","summary":"did it","learnings":["use make check"]}', "1"); go()
    ck("record: exits 0", "0", st["rc"])
    ck("record: passed", "true", passed())
    ck("record: task passed", "passed", t0("status"))
    ck("record: iterations bumped", "1", t0("iterations"))
    ck("record: misses untouched", "0", t0("misses"))
    ck("record: task_index counts passed", "1", cat(T / "task_index"))
    ck("record: run counter", "1", cat(T / "iterations"))
    ck("record: progress line", "1", count("^## Iteration 1: t1 passed (rung 0)$", file=T / "progress.md"))
    ck("record: the summary is logged", "1", count("did it", file=T / "progress.md"))
    ck("record: learnings are logged", "1", count("^- use make check$", file=T / "progress.md"))

    setup('{"status":"progress","summary":"half","learnings":[]}', "1"); go()
    ck("record: check_ok 1 but progress is a miss", "false", passed())
    ck("record: progress increments misses", "1", t0("misses"))
    ck("record: progress leaves the task open", "open", t0("status"))
    setup('{"status":"done","summary":"done","learnings":[]}', ""); go()
    ck("record: no check_ok is a miss", "false", passed())

    setup(); go()
    ck("record: no result is a miss", "false", passed())
    ck("record: no result says so", "1", count("(no result submitted)", file=T / "progress.md"))

    setup('{"status":"done","summary":"done","learnings":[]}', "1")
    write(T / "task_block_scope.md", "The Iteration blocked on task A: needs a workflow edit")
    go()
    ck("record: a scope block is a miss", "false", passed())
    ck("record: a scope block marks blocked", "blocked", t0("status"))
    ck("record: the scope text becomes the feedback", "1", count("needs a workflow edit", file=T / "feedback/rework.md"))

    setup('{"status":"done","summary":"done","learnings":[]}', "1", "post"); go()
    ck("record post: post_iterations advances", "1", cat(T / "post_iterations"))
    ck("record post: iterations does not", "0", cat(T / "iterations"))
    ck("record post: the Task still passes", "passed", t0("status"))

    # The edges: at most one conditional edge can match any state `pick` can publish.
    worst = 0
    for ph in ("pre", "post"):
        for le in ("true", "false"):
            for loop, rung, rf in (("work", "1", "false"), ("work", "4", "false"), ("work", "0", "true"), ("done", "0", "false"), ("exhausted", "0", "false")):
                worst = max(worst, r_matches("pick", f"loop={loop}", f"rung={rung}", f"phase={ph}", f"rebase_failed={rf}", f"loop_exhausted={le}"))
    ck("pick: no state matches two conditional edges", "1", worst)
    ex_worst, zero = 0, ""
    for r in range(1, 5):
        m = r_matches("excerpts", f"rung={r}")
        ex_worst = max(ex_worst, m)
        if m != 1:
            zero += f" excerpts/rung={r}"
    ck("excerpts: no state matches two conditional edges", "1", ex_worst)
    ck("excerpts: every rung reaches a node", "", zero)
    rp = grep("^pick->", edges(RAPH))
    ck("pick: exactly one unconditional edge", "1", count(" $", rp))
    ck("pick: the unconditional edge is the escape", "1", count("pick->stop_reason ", rp, flags="xF"))
    ck("pick: excerpts is gated on no rebase", "1", count("pick->excerpts context.loop=work && context.rebase_failed=false", rp, flags="xF"))
    ck("pick: resolve_merge is gated on a rebase", "1", count("pick->resolve_merge context.rebase_failed=true", rp, flags="xF"))


def test_ralph_refute_gate(ck, tmp_path):
    RG = tmp_path / "ralph-refute-gate"
    T = RG / "sb"
    (RG / "repo/src").mkdir(parents=True); (T / "review").mkdir(parents=True)
    gate = stage_r(ck, "refute_gate", T)
    write(RG / "repo/src/a.py", "x\n")
    mkreadok(T, "ok1")

    def setup(criteria, defects, visits="1", exhausted=False):
        for p in (T / "review").iterdir():
            rm(p)
        rm(T / "refute_visits", T / "loop_exhausted")
        write(T / "tasks.json", "[]")
        write(T / "review/refute.json", jq_n('{summary:"s",criteria:$c,defects:$d,_io:{visit:"ok1"}}', "--argjson", "c", criteria, "--argjson", "d", defects) + "\n")
        write(T / "refute_visits", visits + "\n")
        if exhausted:
            write(T / "loop_exhausted", "true\n")

    st = {}

    def go():
        r = sh(gate, cwd=RG / "repo")
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    val = lambda k: ctx(st["j"], k)
    crit = lambda s, c, e: jq_n("{criterion:$c,status:$s,evidence:$e}", "--arg", "s", s, "--arg", "c", c, "--arg", "e", e)
    defect = lambda f, s: jq_n("{file:$f,line:1,scenario:$s}", "--arg", "f", f, "--arg", "s", s)
    ntasks = lambda: jq("length", file=T / "tasks.json", raw=False)
    bad_token = "The parser rejects a bad token"

    setup(f"[{crit('met', bad_token, 'src/a.py:12 shows the raise')}]", "[]"); go()
    ck("rg pass: exits 0", "0", st["rc"])
    ck("rg pass: routes pass", "pass", val("refute_route"))
    ck("rg pass: verdict pass", "pass", val("refute_verdict"))
    ck("rg pass: the verdict file", "pass", cat(T / "review/refute_verdict"))
    ck("rg pass: no task appended", "0", ntasks())
    ck("rg pass: one routing object", "1", count("context_updates", st["out"]))

    setup(f"[{crit('not_met', bad_token, 'src/a.py:12 shows the raise is missing')}]", "[]", "1"); go()
    ck("rg evidence: routes tasks", "tasks", val("refute_route"))
    ck("rg evidence: verdict fail", "fail", val("refute_verdict"))
    ck("rg evidence: not_met true", "true", val("refute_not_met"))
    ck("rg evidence: one task", "1", ntasks())
    ck("rg evidence: source refute", "refute", jq(".[0].source", file=T / "tasks.json"))
    ck("rg evidence: round 1", "1", jq(".[0].round", file=T / "tasks.json"))
    ck("rg evidence: acceptance is the criterion", bad_token, jq(".[0].acceptance[0]", file=T / "tasks.json"))
    ck("rg evidence: task is open", "open", jq(".[0].status", file=T / "tasks.json"))

    setup(f"[{crit('not_met', bad_token, 'src/missing.py:12 shows the raise is missing')}]", "[]", "1"); go()
    ck("rg missing path: routes pass", "pass", val("refute_route"))
    ck("rg missing path: dropped listed", "1", jq("length", file=T / "review/refute_dropped.json", raw=False))
    ck("rg missing path: nothing dropped is a task", "0", ntasks())

    setup(f"[{crit('not_met', 'The suite passes', '`./.fabro/ci.sh` exits 0')}]", "[]", "1"); go()
    ck("rg backticked command: survives", "tasks", val("refute_route"))
    ck("rg backticked command: one task", "1", ntasks())

    setup(f"[{crit('met', bad_token, 'src/a.py:12')}]", f"[{defect('src/gone.py', 'it crashes')}]", "1"); go()
    ck("rg missing defect file: dropped", "pass", val("refute_route"))
    ck("rg missing defect file: listed", "1", jq("length", file=T / "review/refute_dropped.json", raw=False))

    setup(f"[{crit('not_met', bad_token, 'src/a.py:12')}]", f"[{defect('src/a.py', 'it crashes')}]", "1"); go()
    ck("rg defect: two tasks", "2", ntasks())
    ck("rg defect: title names it", "Fix: src/a.py", jq(".[1].title", file=T / "tasks.json"))
    ck("rg defect: files holds it", "src/a.py", jq(".[1].files[0]", file=T / "tasks.json"))

    setup(f"[{crit('not_met', bad_token, 'src/a.py:12')}]", "[]", "3"); go()
    ck("rg round cap: routes hold", "hold", val("refute_route"))
    ck("rg round cap: verdict fail", "fail", cat(T / "review/refute_verdict"))
    ck("rg round cap: no task", "0", ntasks())
    ck("rg round cap: reason written", "1", count("could not confirm", file=T / "review/refute_reason.txt"))

    setup(f"[{crit('unverifiable', 'The screen looks right', 'needs a browser')}]", "[]", "1"); go()
    ck("rg unverifiable only: routes hold", "hold", val("refute_route"))
    ck("rg unverifiable only: flag", "true", val("refute_unverifiable_only"))
    ck("rg unverifiable only: no task", "0", ntasks())

    setup(f"[{crit('not_met', bad_token, 'src/a.py:12')}]", "[]", "1", exhausted=True); go()
    ck("rg exhausted: routes hold", "hold", val("refute_route"))
    ck("rg exhausted: no task", "0", ntasks())
    ck("rg exhausted: key published", "true", val("loop_exhausted"))
    ck("rg exhausted: not mergeable", "needs_human", jq(".outcome", file=T / "review/fix_result.json"))

    write(T / "review/refute.json", '{"summary":"s","criteria":[],"defects":[]}')
    rm(T / "loop_exhausted")
    go()
    ck("rg no receipt: invalid", "invalid", val("refute_verdict"))
    ck("rg no receipt: hold", "hold", val("refute_route"))
    ck("rg no receipt: no task", "0", ntasks())

    # O18: a loop that ran out never reaches the reviews, even when the Refuter passes.
    setup(f"[{crit('met', bad_token, 'src/a.py:12 shows the raise')}]", "[]", "1", exhausted=True)
    write(T / "tasks.json", '[{"id":"t1","title":"The parser","status":"open"}]'); go()
    ck("rg exhausted pass: routes hold", "hold", val("refute_route"))
    ck("rg exhausted pass: the verdict stays pass", "pass", val("refute_verdict"))
    ck("rg exhausted pass: not mergeable", "needs_human", jq(".outcome", file=T / "review/fix_result.json"))
    ck("rg exhausted pass: lists the unpassed Task", "t1", jq(".not_fixed[0].finding_id", file=T / "review/fix_result.json"))

    write(T / "review/refute.json", '{"summary":"s","criteria":[],"defects":[]}'); rm(T / "review/fix_result.json"); go()
    ck("rg exhausted no receipt: hold", "hold", val("refute_route"))
    ck("rg exhausted no receipt: not mergeable", "needs_human", jq(".outcome", file=T / "review/fix_result.json"))


def test_ralph_refute_prep(ck, tmp_path):
    RQ = tmp_path / "ralph-refute-prep"
    T = RQ / "sb"
    T.mkdir(parents=True)
    repo = RQ / "repo"
    bare_and_repo(RQ / "origin.git", repo, {"ci.sh": "#!/bin/sh\nexit 0\n", ".fabro/ci.sh": "#!/bin/sh\nexit 0\n", "a.txt": "one\n"})
    append(repo / "a.txt", "two\n"); git("-C", repo, "commit", "-qam", "work")
    prep = stage_r(ck, "refute_prep", T)

    def setup(body, visits=""):
        # Keep the staged refute_prep.sh: the fixtures rebuild the run's state, not the node.
        keep_only(T, "refute_prep.sh")
        write(T / "issue.json", jq_n('{number:12,title:"An issue",body:$b,labels:[],comments:[]}', "--arg", "b", body) + "\n")
        write(T / "tasks.json", '[{"id":"r1c1","title":"The parser rejects a bad token","status":"open","source":"refute","round":1}]')
        if visits:
            write(T / "refute_visits", visits + "\n")
            write(T / "refute_tree", git("-C", repo, "rev-parse", "HEAD^{tree}") + "\n")

    go = lambda: sh(prep, cwd=repo)
    issues = T / "review/refute_issues.json"
    nonempty = lambda p: "1" if p.is_file() and p.stat().st_size else "0"

    setup("Body text.\n\n## Post-deploy checks\n\n- [ ] The deploy page shows the new field\n\n## Notes\nkeep me")
    r = go()
    ck("rp: exits 0", "0", r.rc)
    ck("rp: visit counter is 1", "1", cat(T / "refute_visits"))
    ck("rp: the issue is one entry", "1", jq("length", file=issues, raw=False))
    ck("rp: the issue number survives", "12", jq(".[0].number", file=issues))
    ck("rp: post-deploy is cut", "0", count("Post-deploy", file=issues))
    ck("rp: the checklist item is cut", "0", count("deploy page shows", file=issues))
    ck("rp: the rest of the body survives", "1", count("keep me", file=issues))
    ck("rp: post_deploy.md holds the checklist", "1", count("deploy page shows", file=T / "review/post_deploy.md"))
    ck("rp: the diff is written", "1", nonempty(T / "review/refute_diff.patch"))
    ck("rp: visit 1 writes no delta", "absent", present(T / "review/refute_delta.patch"))
    ck("rp: visit 1 writes no history", "absent", present(T / "review/refute_history.md"))
    ck("rp: the validation input", "1", count("No validation is recorded", file=T / "review/refute_validation.md"))
    ck("rp: the tree is recorded", git("-C", repo, "rev-parse", "HEAD^{tree}"), cat(T / "refute_tree"))

    setup("No post-deploy section here.", "1")
    append(repo / "a.txt", "three\n"); git("-C", repo, "commit", "-qam", "a second round of work")
    go()
    ck("rp round 2: visit counter is 2", "2", cat(T / "refute_visits"))
    ck("rp round 2: the delta is written", "1", nonempty(T / "review/refute_delta.patch"))
    ck("rp round 2: the history is written", "1", count("The parser rejects a bad token", file=T / "review/refute_history.md"))
    ck("rp round 2: the history names the task", "1", count("r1c1", file=T / "review/refute_history.md"))
    ck("rp round 2: the history names the status", "1", count("(open)", file=T / "review/refute_history.md"))

    # The prompt promises "Dropped by the gate last round"; the node deletes that file, so it
    # must read it first.
    setup("No post-deploy section here.", "2")
    write(T / "review/refute_dropped.json", '[{"criterion":"The form validates","status":"not_met","evidence":"I could not find it"},{"file":"src/gone.py","line":1,"scenario":"it crashes"}]')
    append(repo / "a.txt", "four\n"); git("-C", repo, "commit", "-qam", "a third round of work")
    go()
    hist = T / "review/refute_history.md"
    ck("rp round 3: the dropped criterion is in the history", "1", count("Dropped by the gate last round: The form validates (evidence given: I could not find it)", file=hist, flags="F"))
    ck("rp round 3: the dropped defect is in the history", "1", count("Dropped by the gate last round: defect at src/gone.py: it crashes", file=hist, flags="F"))
    ck("rp round 3: the dropped file is cleared", "absent", present(T / "review/refute_dropped.json"))
    setup("No post-deploy section here.", "3")
    write(T / "tasks.json", "[]")
    append(repo / "a.txt", "five\n"); git("-C", repo, "commit", "-qam", "a fourth round of work")
    go()
    ck("rp: nothing earlier writes no history", "absent", present(hist))


def test_ralph_review_gate(ck, tmp_path):
    RW = tmp_path / "ralph-review-gate"
    T = RW / "sb"
    (T / "review").mkdir(parents=True)
    gate = stage_r(ck, "review_gate", T)
    fix = T / "review/fix_result.json"

    def setup(standards, spec, visits="1", route="pass", tasks="[]"):
        (T / "review").mkdir(exist_ok=True)
        for p in (T / "review").iterdir():
            rm(p)
        rm(T / "refute_visits", T / "refute_route", T / "merge_block_reason", T / "stuck_reason.md", T / "tasks.json", T / "loop_exhausted")
        write(T / "review/standards.json", jq('._io={stage:$n,visit:"v1"}', standards, args=["--arg", "n", "standards"], raw=False, pipe=True) + "\n")
        write(T / "review/spec.json", jq('._io={stage:$n,visit:"v2"}', spec, args=["--arg", "n", "spec"], raw=False, pipe=True) + "\n")
        write(T / "refute_visits", visits + "\n")
        write(T / "refute_route", route + "\n")
        write(T / "tasks.json", tasks)

    st = {}

    def go():
        r = sh(gate)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    val = lambda k: ctx(st["j"], k)
    fx = lambda k: jq(f".{k}", file=fix)
    find = lambda i, sev, m, f: jq_n("{id:$id,severity:$sev,message:$m,files:[$f],line:1,suggestion:\"do it\"}", "--arg", "id", i, "--arg", "sev", sev, "--arg", "m", m, "--arg", "f", f)
    findings = lambda f, extra="": jq_n("{decision:\"findings\",summary:\"one\",findings:$f" + extra + "}", "--argjson", "f", f"[{f}]")
    std_ok = '{"decision":"approve","summary":"clean","findings":[]}'
    spec_approve = '{"decision":"approve","summary":"clean","findings":[],"risk":2}'
    ntasks = lambda: jq("length", file=T / "tasks.json", raw=False)

    setup(std_ok, spec_approve, "1", "pass"); go()
    ck("rv pass: exits 0", "0", st["rc"])
    ck("rv pass: routes pass", "pass", val("review_route"))
    ck("rv pass: outcome mergeable", "no_changes_needed", fx("outcome"))
    ck("rv pass: risk from spec", "2", fx("risk"))
    ck("rv pass: not_fixed empty", "0", jq("length", jq(".not_fixed", file=fix, raw=False), pipe=True, raw=False))
    ck("rv pass: fixes_applied empty", "0", jq(".fixes_applied | length", file=fix, raw=False))
    ck("rv pass: nothing became a task", "0", ntasks())
    schema = WF / "_io/schemas/fix_result.schema.json"
    matches = run(["jq", "-e", "--slurpfile", "s", str(schema),
                   '(. as $f | (($s[0].required - ($f | keys)) | length) == 0 and ((($f | keys) - ($s[0].properties | keys)) | length) == 0 and (($s[0].properties.outcome.enum | index($f.outcome)) != null) and (($f.risk | type) == "number"))',
                   str(fix)]).rc
    ck("rv pass: the file matches the schema", "1", "1" if matches == 0 else "0")
    ck("rv pass: one routing object", "1", count("context_updates", st["out"]))

    setup(findings(find("STD-001", "error", "the retry never happens", "src/a.py")), spec_approve, "1", "pass"); go()
    ck("rv error: routes tasks", "tasks", val("review_route"))
    ck("rv error: one task", "1", ntasks())
    ck("rv error: source review", "review", jq(".[0].source", file=T / "tasks.json"))
    ck("rv error: axis standards", "standards", jq(".[0].axis", file=T / "tasks.json"))
    ck("rv error: it keeps the finding id", "STD-001", jq(".[0].finding_id", file=T / "tasks.json"))
    ck("rv error: no fix_result yet", "absent", present(fix))

    setup(std_ok, findings(find("STD-001", "error", "the retry never happens", "src/a.py"), ",risk:2"), "3", "pass"); go()
    ck("rv round cap: routes pass", "pass", val("review_route"))
    ck("rv round cap: no task", "0", ntasks())
    ck("rv round cap: not mergeable", "needs_human", fx("outcome"))
    ck("rv round cap: not_fixed lists it", "STD-001", jq(".not_fixed[0].finding_id", file=fix))
    ck("rv round cap: merge_block_reason", "1", count("blocking finding", file=T / "merge_block_reason"))

    setup(jq_n('{decision:"findings",summary:"style",findings:$f}', "--argjson", "f", f"[{find('STD-002', 'warning', 'this could be tidier', 'src/b.py')}]"), spec_approve, "1", "pass"); go()
    ck("rv warning only: routes pass", "pass", val("review_route"))
    ck("rv warning only: mergeable", "no_changes_needed", fx("outcome"))
    ck("rv warning only: no task", "0", ntasks())

    setup(findings(find("STD-003", "error", "the retry never happens", "src/a.py")), spec_approve, "1", "hold"); go()
    ck("rv refuter held: no task", "0", ntasks())
    ck("rv refuter held: not mergeable", "needs_human", fx("outcome"))

    setup('{"decision":"needs_human_review","summary":"inputs disagree","findings":[]}', jq_n('{decision:"approve",summary:"ok",findings:[],risk:2}'), "1", "pass"); go()
    ck("rv human review: not mergeable", "needs_human", fx("outcome"))
    ck("rv human review: reason names it", "1", count("standards review asked for a human", file=T / "merge_block_reason"))

    setup(std_ok, jq_n('{decision:"approve",summary:"ok",findings:[],risk:4}'), "1", "pass"); go()
    ck("rv risk 4: outcome still mergeable", "no_changes_needed", fx("outcome"))
    ck("rv risk 4: the value is written", "4", fx("risk"))

    setup(std_ok, spec_approve, "1", "pass", '[{"id":"rv1s1","title":"STD-001 the retry never happens","source":"review","axis":"standards","finding_id":"STD-001","severity":"error","status":"passed"}]'); go()
    ck("rv fixes_applied: earlier round listed", "STD-001", jq(".fixes_applied[0].finding_id", file=fix))
    ck("rv fixes_applied: outcome fixed", "fixed", fx("outcome"))

    # Reviewer ids restart every round: a round-3 STD-001 is a new error, not the round-1 fix.
    setup(findings(find("STD-001", "error", "a different bug", "src/c.py")), spec_approve, "3", "pass",
          '[{"id":"rv1s1","title":"STD-001 the retry never happens","source":"review","axis":"standards","finding_id":"STD-001","severity":"error","status":"passed"}]'); go()
    ck("rv reused id: not mergeable", "needs_human", fx("outcome"))
    ck("rv reused id: not_fixed lists this round's error", "a different bug", jq(".not_fixed[0].original_finding", file=fix))

    setup(std_ok, spec_approve, "1", "pass"); write(T / "loop_exhausted", "true\n"); go()
    ck("rv exhausted: not mergeable", "needs_human", fx("outcome"))
    ck("rv exhausted: reason names it", "1", count("loop ran out", file=T / "merge_block_reason"))
    setup(findings(find("STD-004", "error", "the retry never happens", "src/a.py")), spec_approve, "1", "pass")
    write(T / "loop_exhausted", "true\n"); go()
    ck("rv exhausted: a blocking finding is no task", "0", ntasks())

    write(T / "review/standards.json", '{"decision":"approve","summary":"x","findings":[]}')
    go()
    ck("rv bad receipt: exits 1", "1", st["rc"])
    ck("rv bad receipt: names the cause", "1", count("no valid result", file=T / "stuck_reason.md"))


def test_ralph_final_check(ck, tmp_path):
    FC = tmp_path / "ralph-final-check"
    T = FC / "sb"
    T.mkdir(parents=True)
    fcs = stage_r(ck, "final_check", T)
    repo = FC / "repo"

    def fc_repo():
        rm(repo, FC / "origin.git")
        keep_only(T, "final_check.sh")
        bare_and_repo(FC / "origin.git", repo, {".fabro/setup.sh": "#!/bin/sh\nexit 0\n", ".fabro/ci.sh": "#!/bin/sh\nexit 0\n", "a.txt": "base\n"})
        append(repo / "a.txt", "work\n"); git("-C", repo, "commit", "-qam", "feat: the work")
        write(T / "issue.json", jq_n('{number:12,title:"Add a widget",body:"A paragraph of prose about the widget.\\n\\n## Post-deploy checks\\n\\n- [ ] the widget deploys\\n",labels:[]}') + "\n")
        write(T / "completed.md", "The first Iteration summary\n")
        write(T / "phase", "pre\n"); write(T / "final_fix_attempts", "0\n"); write(T / "iterations", "1\n")
        write(T / "tasks.json", '[{"id":"t1","status":"passed"}]')

    st = {}

    def go():
        r = sh(fcs, cwd=repo)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    val = lambda k: ctx(st["j"], k)
    tree = lambda: git("-C", repo, "rev-parse", "HEAD^{tree}")

    def red_ci(text, msg):
        stub(repo / ".fabro", "ci.sh", text)
        git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qm", msg)

    fc_repo(); go()
    ck("fc green: exits 0", "0", st["rc"])
    ck("fc green: final_ok", "true", val("final_ok"))
    ck("fc green: routes pr", "pr", val("final_route"))
    ck("fc green: phase published", "pre", val("phase"))
    ck("fc green: validated_tree", f"{tree()} final", cat(T / "validated_tree"))
    ck("fc green: final_validated_tree", tree(), cat(T / "final_validated_tree"))
    ck("fc green: base_ref is main", "main", cat(T / "base_ref"))
    ck("fc green: the subject is conventional", "1", count("^chore: Add a widget \\(#12\\)$", file=T / "commit_subject.txt", flags="E"))
    ck("fc green: the body resolves the issue", "1", count("^Resolves #12$", file=T / "commit_body.md"))
    ck("fc green: the PR title is the subject", "chore: Add a widget (#12)", head1(T / "pr_title.txt"))
    ck("fc green: the body carries the marker", "1", count("fabro:commit-body:start", file=T / "pr_body.md"))
    ck("fc green: post-deploy checks are in the body", "1", count("the widget deploys", file=T / "pr_body.md"))

    fc_repo()
    red_ci('#!/bin/sh\necho "FAIL src/widget.test.ts > renders"\nexit 1\n', "a red check")
    go()
    body1 = jq(".[1].body", file=T / "tasks.json")
    ck("fc red: routes fix", "fix", val("final_route"))
    ck("fc red: no validated_tree", "absent", present(T / "validated_tree"))
    ck("fc red: one final_fix Task", "1", jq('[.[] | select(.source == "final_fix")] | length', file=T / "tasks.json", raw=False))
    ck("fc red: the Task is a final_fix", "true", jq(".[1].final_fix", file=T / "tasks.json"))
    ck("fc red: the failing name is in it", "1", "1" if int(count("FAIL src/widget.test.ts", body1, pipe=True) or 0) >= 1 else "0")
    ck("fc red: the counter is spent", "1", cat(T / "final_fix_attempts"))
    ck("fc red: the full suite is asked for", "1", count("exits 0", jq(".[1].acceptance[0]", file=T / "tasks.json"), pipe=True))
    go()
    ck("fc red again: routes stuck", "stuck", val("final_route"))
    ck("fc red again: the reason names the failure", "1", count("FAIL src/widget.test.ts", file=T / "stuck_reason.md"))
    ck("fc red again: no second Task", "2", jq("length", file=T / "tasks.json", raw=False))

    fc_repo()
    red_ci("#!/bin/sh\nexit 1\n", "red")
    write(T / "iterations", "3\n")
    go()
    ck("fc red at the cap: routes stuck", "stuck", val("final_route"))
    ck("fc red at the cap: no Task", "1", jq("length", file=T / "tasks.json", raw=False))

    fc_repo()
    git("-C", repo, "reset", "-q", "--hard", "origin/main")
    go()
    ck("fc empty tree: routes stuck", "stuck", val("final_route"))
    ck("fc empty tree: says why", "1", count("identical to origin/main", file=T / "stuck_reason.md"))

    fc_repo()
    write(repo / ".github/workflows/x.yml", "on: push\n")
    git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qm", "ci: touch a workflow")
    go()
    ck("fc workflow edit: routes stuck", "stuck", val("final_route"))
    ck("fc workflow edit: names it", "1", count("changes a file under .github/workflows/", file=T / "stuck_reason.md"))
    ck("fc workflow edit: names the commit", "1", count("x.yml", file=T / "stuck_reason.md"))

    # A merge commit of main that carries a workflow change is not refused.
    fc_repo()
    git("-C", repo, "checkout", "-q", "-b", "other")
    write(repo / ".github/workflows/y.yml", "on: pull_request\n")
    git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qm", "ci: main adds a workflow")
    git("-C", repo, "push", "-q", "origin", "other:main")
    git("-C", repo, "checkout", "-q", "main")
    git("-C", repo, "merge", "-q", "--no-edit", "origin/main")
    append(repo / "a.txt", "more\n"); git("-C", repo, "commit", "-qam", "feat: the local work")
    go()
    ck("fc workflow on main: not refused", "pr", val("final_route"))

    fc_repo()
    red_ci("#!/bin/sh\nexit 1\n", "red again")
    write(T / "phase", "post\n"); write(T / "final_fix_attempts", "1\n")
    go()
    ck("fc red post: both reason files", "1", count("Final validation", file=T / "merge_block_reason"))
    ck("fc red post: needs_human reason", "1", count("Final validation", file=T / "needs_human_reason"))
    ck("fc red post: route", "needs_human", val("final_route"))

    # The edges.
    redg = edges(RAPH)
    fce = grep("^final_check->", redg)
    ck("fc edges: six exits", "6", count(".", fce))
    for name, e in (("fc edges: pr needs final_route=pr in phase pre", "final_check->hygiene outcome=succeeded && context.final_route=pr && context.phase=pre"),
                    ("fc edges: pr needs final_route=pr in phase post", "final_check->hygiene outcome=succeeded && context.final_route=pr && context.phase=post"),
                    ("fc edges: a human is needed in post", "final_check->mark_needs_human outcome=succeeded && context.final_route=needs_human"),
                    ("fc edges: fix returns to pick", "final_check->pick outcome=succeeded && context.final_route=fix"),
                    ("fc edges: stuck goes to mark_stuck", "final_check->mark_stuck outcome=succeeded && context.final_route=stuck"),
                    ("fc edges: one unconditional escape", "final_check->stop_reason ")):
        ck(name, "1", count(e, fce, flags="xF"))
    hye = grep("^hygiene->", redg)
    ck("hygiene edges: the pre-PR route only", "1", count("hygiene->refute_prep outcome=succeeded && context.phase=pre", hye, flags="xF"))
    ck("hygiene edges: one unconditional escape", "1", count("hygiene->stop_reason ", hye, flags="xF"))


def test_ralph_ci_task(ck, tmp_path):
    CT = tmp_path / "ralph-ci-task"
    T = CT / "sb"
    (T / "feedback").mkdir(parents=True)
    ct = stage_r(ck, "ci_task", T)

    def setup(post="0", log=""):
        (T / "feedback").mkdir(exist_ok=True)
        write(T / "post_iterations", post + "\n")
        write(T / "feedback/ci_fix.md", log)
        write(T / "tasks.json", '[{"id":"t1","status":"passed"}]')
        write(T / "phase", "pre\n")
        rm(T / "merge_block_reason", T / "needs_human_reason", T / "ci_task.json")

    st = {}

    def go():
        r = sh(ct)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    val = lambda k: ctx(st["j"], k)
    body1 = lambda: jq(".[1].body", file=T / "tasks.json")

    setup("0", "## Failing GitHub checks\n\ntest (pull_request)\n### Failing log for run 123\nFAIL src/widget.test.ts > renders")
    go()
    ck("ci_task: exits 0", "0", st["rc"])
    ck("ci_task: routes task", "task", val("ci_route"))
    ck("ci_task: phase is post", "post", val("phase"))
    ck("ci_task: one Task appended", "1", jq('[.[] | select(.source == "ci")] | length', file=T / "tasks.json", raw=False))
    ck("ci_task: the Task is open", "open", jq(".[1].status", file=T / "tasks.json"))
    ck("ci_task: the failing check is in it", "1", count("test (pull_request)", body1(), pipe=True))
    ck("ci_task: the log tail is in it", "1", count("FAIL src/widget.test.ts", body1(), pipe=True))
    ck("ci_task: acceptance names the check", "1", count("passes on the pushed head", jq(".[1].acceptance[0]", file=T / "tasks.json"), pipe=True))

    setup("1", "second round"); go()
    ck("ci_task round 2: the id moves", "ci2", jq(".[1].id", file=T / "tasks.json"))

    setup("2", "the cap is spent"); go()
    ck("ci_task at the cap: routes stop", "stop", val("ci_route"))
    ck("ci_task at the cap: no Task", "1", jq("length", file=T / "tasks.json", raw=False))
    ck("ci_task at the cap: merge_block_reason", "1", count("CI is red after 2 post-PR Iterations", file=T / "merge_block_reason"))
    ck("ci_task at the cap: needs_human_reason", "1", count("CI is red after 2 post-PR Iterations", file=T / "needs_human_reason"))


def test_ralph_merge_gate(ck, tmp_path):
    MG = tmp_path / "ralph-merge-gate"
    T = MG / "sb"
    for d in ("bin", "review"):
        (T / d).mkdir(parents=True)
    repo = MG / "repo"
    repo.mkdir()
    gate = stage_r(ck, "merge_gate", T)
    git("-C", repo, "init", "-q", "-b", "main")
    git("-C", repo, "config", "user.email", "t@t"); git("-C", repo, "config", "user.name", "t")
    write(repo / "a.txt", "x\n"); git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qm", "base")
    git("init", "-q", "--bare", MG / "origin.git"); git("-C", MG / "origin.git", "symbolic-ref", "HEAD", "refs/heads/main")
    git("-C", repo, "remote", "add", "origin", MG / "origin.git"); git("-C", repo, "push", "-q", "origin", "main")
    append(repo / "a.txt", "y\n"); git("-C", repo, "commit", "-qam", "feat: the work")
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view")
    J=""; P=""
    for a in "$@"; do [ "$P" = "--jq" ] && J="$a"; P="$a"; done
    if [ -n "$J" ]; then jq -r "$J" "$GH_STATE/pr.fixture.json"; else cat "$GH_STATE/pr.fixture.json"; fi
    exit 0 ;;
  "api graphql") echo 0; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}

    def setup(title="", labels="", fix="", blocking="[]", verdict="pass"):
        (T / "review").mkdir(exist_ok=True)
        write(T / "pr.fixture.json", jq_n('{labels:$l, state:"OPEN", isDraft:false, reviewDecision:"APPROVED", commits:[], title:$t,\n        body:"## Summary\\n\\n<!-- fabro:commit-body:start -->\\nResolves #12\\n<!-- fabro:commit-body:end -->\\n"}',
                                          "--arg", "t", title or "feat: Add a widget (#12)", "--argjson", "l", labels or '[{"name":"agent-authored"}]') + "\n")
        write(T / "pr.json", jq_n('{number:7, url:"https://github.com/o/r/pull/7", title:"t", body:"b", state:"OPEN", headRefName:"b", baseRefName:"main", labels:[], closingIssuesReferences:[]}') + "\n")
        write(T / "review/fix_result.json", fix or '{"outcome":"fixed","risk":2,"summary":"s","own_findings":[],"fixes_applied":[],"not_fixed":[]}')
        write(T / "review/hygiene.json", jq_n('{version:1, config:"default", tamper:{}, erosion:{}, blocking:$b, samples:[], head:$h}',
                                              "--argjson", "b", blocking, "--arg", "h", git("-C", repo, "rev-parse", "HEAD")) + "\n")
        write(T / "review/refute_verdict", verdict)
        write(T / "review/hygiene_reason.txt", "hygiene blocked\n")
        write(T / "auto_merge", "1\n"); write(T / "pr_number", "7\n"); write(T / "base_ref", "main\n")
        rm(T / "merge_block_reason", T / "merge_deadline", T / "gh_fix_attempts")

    st = {}

    def go():
        r = sh(gate, cwd=repo, path=path, env=env)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    eligible = lambda: ctx(st["j"], "merge_eligible")
    reason = T / "merge_block_reason"

    setup(); go()
    ck("mg green: exits 0", "0", st["rc"])
    ck("mg green: eligible", "true", eligible())
    ck("mg green: the deadline is set", "1", count("^[0-9]+$", file=T / "merge_deadline", flags="E"))
    ck("mg green: fix attempts reset", "0", cat(T / "gh_fix_attempts"))
    ck("mg green: no block reason", "absent", present(reason))

    setup(blocking='["test_skipped"]'); go()
    ck("mg tamper: not eligible", "false", eligible())
    ck("mg tamper: the hygiene reason is the block", "1", count("hygiene blocked", file=reason))

    setup(verdict="fail"); go()
    ck("mg refuter fail: not eligible", "false", eligible())

    setup(fix='{"outcome":"needs_human","risk":2,"summary":"s","own_findings":[],"fixes_applied":[],"not_fixed":[]}'); go()
    ck("mg unmergeable fix_result: not eligible", "false", eligible())
    ck("mg unmergeable fix_result: says so", "1", count("did not end in a mergeable state", file=reason))

    setup(fix='{"outcome":"fixed","risk":2,"summary":"s","own_findings":[],"fixes_applied":[],"not_fixed":[{"finding_id":"STD-001"}]}'); go()
    ck("mg not_fixed: not eligible", "false", eligible())
    ck("mg not_fixed: says so", "1", count("unfixed findings", file=reason))

    setup(fix='{"outcome":"fixed","risk":4,"summary":"s","own_findings":[],"fixes_applied":[],"not_fixed":[]}'); go()
    ck("mg risk 4: not eligible", "false", eligible())
    ck("mg risk 4: says so", "1", count("risk rating is missing or above 3", file=reason))

    setup(title="Add a widget (#12)"); go()
    ck("mg title not conventional: not eligible", "false", eligible())

    setup(labels='[{"name":"agent-authored"},{"name":"do-not-merge"}]'); go()
    ck("mg do-not-merge: not eligible", "false", eligible())


def test_ralph_deliver_and_merge_edges(ck, tmp_path):
    DL = tmp_path / "ralph-deliver"
    T = DL / "sb"
    T.mkdir(parents=True); (DL / "bin").mkdir()
    dl = stage_r(ck, "deliver", T)
    repo = DL / "repo"
    bare_and_repo(DL / "origin.git", repo, {"a.txt": "base\n"})
    git("-C", repo, "checkout", "-q", "-b", "fabro/run/x")
    append(repo / "a.txt", "work\n"); git("-C", repo, "commit", "-qam", "feat: the work")
    git("-C", repo, "push", "-q", "origin", "fabro/run/x")
    git("-C", repo, "reset", "-q", "--hard", "HEAD~1")
    append(repo / "a.txt", "more\n"); git("-C", repo, "commit", "-qam", "feat: the review edit")
    stub(DL / "bin", "gh", '#!/bin/sh\necho "$*" >> "$DL_LOG"\nexit 0\n')
    write(T / "pr_number", "7\n"); write(T / "head_ref", "fabro/run/x\n"); write(T / "base_ref", "main\n")
    r = sh(dl, cwd=repo, path=f"{DL / 'bin'}:{ORIG_PATH}", env={"DL_LOG": T / "gh.log"})
    ck("deliver: exits 0", "0", r.rc)
    ck("deliver: delivered", "true", jq(".context_updates.delivered", lastjson(r.out)))
    ck("deliver: the new head is on the remote", "feat: the review edit", git("-C", DL / "origin.git", "log", "-1", "--format=%s", "refs/heads/fabro/run/x"))
    ck("deliver: the marker is written", "delivered", cat(T / "delivery"))
    ck("deliver: the label is added", "1", count("ai-review-complete", file=T / "gh.log"))

    # The edges of the merge stage.
    redg = edges(RAPH)
    mgedge = grep(["-E", "^(merge_prep|deliver|merge_gate|watch_checks|ci_task|merge|remerge_base|resolve_base|resolve_base_gate)->"], redg)
    ck("merge edges: the stage is entered through merge_prep", "1", count("pr_handoff->merge_prep outcome=succeeded", redg, flags="xF"))
    ck("merge edges: merge_prep reaches merge_gate", "1", count("merge_prep->merge_gate outcome=succeeded", mgedge, flags="xF"))
    ck("merge edges: a red check becomes a Task", "2", count("^watch_checks->ci_task context.checks_ok=false && context.gh_fix_attempts=", mgedge))
    ck("merge edges: the deadline queues instead of blocking", "1", count("watch_checks->report_queued context.checks_deadline=true", mgedge, flags="xF"))
    ck("merge edges: a flake block outranks the fix tier", "1", count("watch_checks->report_blocked context.checks_blocked=true", mgedge, flags="xF"))
    ck("merge edges: ci_task returns to the loop", "1", count("ci_task->pick context.ci_route=task", mgedge, flags="xF"))
    ck("merge edges: a base conflict revalidates", "1", count("resolve_base_gate->final_check context.resolve_ok=true", mgedge, flags="xF"))
    ck("merge edges: post-PR Iterations end at hygiene", "1", count("pick->hygiene context.loop=done && context.phase=post", redg, flags="xF"))
    ck("merge edges: post-PR exhaustion reports", "1", count("pick->report_blocked context.loop=exhausted && context.phase=post", redg, flags="xF"))
    ck("merge edges: post failures reach mark_needs_human", "1", count("stop_reason->mark_needs_human context.stop_phase=post", redg, flags="xF"))
    ck("merge edges: final_check's pr routes split on phase", "2", count("^final_check->hygiene outcome=succeeded && context.final_route=pr && context.phase=", redg))
    ck("merge edges: every merge-stage node has one unconditional escape", "10",
       count("^(deliver|merge_gate|watch_checks|ci_task|merge|remerge_base|resolve_base|resolve_base_gate|report_merged|report_blocked)->[a-z_]+ $", redg, flags="E"))


def test_ralph_merge_prep(ck, tmp_path):
    # merge_prep is rm_setup byte for byte (R10). This checks the part ralph's first copy left
    # out: without the switch resolution merge_gate finds no auto_merge file and blocks every PR.
    T = tmp_path / "sb"
    T.mkdir()
    (tmp_path / "bin").mkdir()
    mp = stage_r(ck, "merge_prep", tmp_path / "stage", root=T)
    stub(tmp_path / "bin", "gh", '#!/bin/sh\necho "$*" >> "$MP_LOG"\nexit 0\n')

    def go(repo_switch, host_switch):
        for p in T.iterdir():
            rm(p)
        (T / "review").mkdir()
        for f, v in (("pr_number", "7"), ("base_ref", "main"), ("head_ref", "fabro/run/x"),
                     ("pr.json", "{}"), ("linked_issues.json", "[]"), ("run_base_sha", "abc"),
                     ("merge_mode", "watch"), ("auto_merge_repo", repo_switch)):
            write(T / f, v + "\n")
        write(T / "fix_attempts", "3\n"); write(T / "delivery", "x\n")
        r = sh(mp, cwd=tmp_path, path=f"{tmp_path / 'bin'}:{ORIG_PATH}",
               env={"MP_LOG": T / "gh.log", "FABRO_AUTO_MERGE": host_switch})
        return r

    r = go("1", "1")
    ck("merge_prep: exits 0", "0", r.rc)
    ck("merge_prep: both switches on: auto_merge 1", "1", cat(T / "auto_merge"))
    ck("merge_prep: both switches on: no off reason", "", cat(T / "auto_merge_off_reason"))
    ck("merge_prep: resets fix_attempts", "0", cat(T / "fix_attempts"))
    ck("merge_prep: clears delivery", "absent", present(T / "delivery"))
    ck("merge_prep: removes merge-on-green", "1", count("pr edit 7 --remove-label merge-on-green", file=T / "gh.log"))
    ck("merge_prep: publishes watch", "watch", ctx(lastjson(r.out), "merge_mode"))
    ck("merge_prep: writes the renderers", "present|present", present(T / "render.jq") + "|" + present(T / "render_comment.sh"))
    go("0", "1")
    ck("merge_prep: repo switch off: auto_merge 0", "0", cat(T / "auto_merge"))
    ck("merge_prep: repo switch off: says so", "1", count("switched off for this repository", file=T / "auto_merge_off_reason"))
    go("1", "0")
    ck("merge_prep: host switch off: auto_merge 0", "0", cat(T / "auto_merge"))
    ck("merge_prep: host switch off: says so", "1", count("switched off host-wide", file=T / "auto_merge_off_reason"))


def test_ralph_endings(ck):
    # learn_prep reads the ending from /tmp/fabro/ending; a node id is not otherwise available.
    into = [l.split("->")[0] for l in edges(RAPH).split("\n") if l.split(" ")[0].endswith("->learn_prep")]
    ck("endings: seven nodes end in learn_prep", "7", str(len(set(into))))
    for node in sorted(set(into)):
        ck(f"endings: {node} records its id", "1", count(f"echo {node} > /tmp/fabro/ending", extract(RAPH, node), flags="F"))


def test_ralph_learn_prep(ck, tmp_path):
    LP = tmp_path / "ralph-learn-prep"
    T = LP / "sb"
    T.mkdir(parents=True)
    lp = stage_r(ck, "learn_prep", T)

    def setup(iterations="0", ending=""):
        keep_only(T, "learn_prep.sh")
        write(T / "iterations", iterations + "\n")
        if ending:
            write(T / "ending", ending + "\n")
        write(T / "tasks.json", '[{"id":"t1","source":"plan","status":"passed","iterations":2,"misses":1},{"id":"r1c1","source":"refute","status":"blocked","iterations":0,"misses":8}]')
        write(T / "progress.md", "## Iteration 1: t1 not passed (rung 1)\n\nstuck on a trap\n")
        write(T / "review/refute_history.md", "- The parser rejects a bad token -> task r1c1 (blocked)\n")
        write(T / "refute_visits", "2\n")

    st = {}

    def go():
        r = sh(lp)
        st.update(out=r.out, rc=r.rc, j=lastjson(r.out))

    inputs = T / "learn/inputs.md"
    setup("0", "mark_stuck"); go()
    ck("learn_prep: exits 0", "0", st["rc"])
    ck("learn_prep: no Iteration is a skip", "skip", ctx(st["j"], "learn"))
    ck("learn_prep: no inputs written", "absent", present(inputs))

    setup("3", "report_merged"); go()
    ck("learn_prep: Iterations mean run", "run", ctx(st["j"], "learn"))
    ck("learn_prep: the inputs exist", "1", "1" if inputs.is_file() and inputs.stat().st_size else "0")
    ck("learn_prep: the ending is named", "1", count("Ending: report_merged", file=inputs))
    ck("learn_prep: the counters are named", "1", count("Iterations: 3", file=inputs))
    ck("learn_prep: the Task table is there", "1", count("t1 \\[plan\\] passed, iterations=2, misses=1, highest rung=1", file=inputs))
    ck("learn_prep: the rung is derived from misses", "1", count("r1c1 \\[refute\\] blocked, iterations=0, misses=8, highest rung=4", file=inputs))
    ck("learn_prep: the progress log is quoted", "1", count("stuck on a trap", file=inputs))
    ck("learn_prep: the refute history is quoted", "1", count("The parser rejects a bad token", file=inputs))

    setup("1", ""); go()
    ck("learn_prep: an unknown ending says so", "1", count("Ending: unknown", file=inputs))


def test_ralph_learn_post(ck, tmp_path):
    LW = tmp_path / "ralph-learn-post"
    T = LW / "sb"
    (T / "bin").mkdir(parents=True)
    repo = LW / "repo"
    repo.mkdir()
    lw = stage_r(ck, "learn_post", T)
    git("-C", repo, "init", "-q", "-b", "fabro/run/x")
    git("-C", repo, "config", "user.email", "t@t"); git("-C", repo, "config", "user.name", "t")
    git("-C", repo, "commit", "-q", "--allow-empty", "-m", "base")
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$LW_LOG"
case "$1 $2" in
  "issue list")
    [ -n "$LW_LIST_FAIL" ] && exit 1
    cat "${LW_LIST:-/dev/null}" ;;
  "issue create")
    [ -n "$LW_CREATE_FAIL" ] && exit 1
    echo "https://github.com/o/r/issues/77" ;;
  "issue comment") [ -n "$LW_COMMENT_FAIL" ] && exit 1; exit 0 ;;
  "label create") [ -n "$LW_LABEL_FAIL" ] && exit 1; exit 0 ;;
esac
exit 0
""")
    log = T / "gh.log"
    write(T / "pr_number", "7\n"); write(T / "issue.json", jq_n("{number:12}") + "\n")

    def setup(learnings=""):
        rm(T / "learnings.json", log, T / "learn/keep.json")
        (T / "learn").mkdir(exist_ok=True)
        if learnings:
            write(T / "learnings.json", learnings)
        rm(T / "learn/comment.md", T / "learn/issue.md")

    def go(**env):
        return sh(lw, cwd=repo, path=f"{T / 'bin'}:{ORIG_PATH}", env={"LW_LOG": log, **env})

    calls = lambda pat: count(pat, file=log) if log.is_file() else "0"
    ent = lambda r, e, h: jq_n("{rule:$r,evidence:$e,home:$h}", "--arg", "r", r, "--arg", "e", e, "--arg", "h", h)
    learnings = lambda *es: jq_n("{learnings:$l}", "--argjson", "l", "[" + ",".join(es) + "]")
    comment = T / "learn/comment.md"

    setup()
    r = go()
    ck("learn_post: no file exits 0", "0", r.rc)
    ck("learn_post: no file calls no gh", "0", calls("."))
    ck("learn_post: no file says so on stderr", "1", count("no learnings result", r.out))

    setup(learnings(ent("run the formatter", "because it is nice", "agents_md")))
    r = go()
    ck("learn_post: no evidence exits 0", "0", r.rc)
    ck("learn_post: no evidence calls no gh", "0", calls("."))
    ck("learn_post: no evidence says why", "1", count("no Learning had evidence", r.out))

    setup(learnings(ent("run uv run ruff", "Iteration 2 on t1: `uv run ruff check` passed where bare ruff failed", "test_toml"),
                    ent("read the i18n file", "t1 missed apps/web/src/i18n/en.json:3", "ralph_prompt")))
    write(T / "issues.json", '[{"number":42,"body":"<!-- fabro:ralph-learnings -->\\n\\nhow to accept"}]\n')
    r = go(LW_LIST=T / "issues.json")
    ck("learn_post: an existing issue exits 0", "0", r.rc)
    ck("learn_post: it did not create an issue", "0", count("^issue create", file=log))
    ck("learn_post: one comment", "1", count("^issue comment", file=log))
    ck("learn_post: the comment names the run", "1", count("fabro/run/x", file=comment))
    ck("learn_post: the comment names the PR", "1", count("pull request #7", file=comment))
    ck("learn_post: the rule is quoted", "1", count("run uv run ruff", file=comment))
    ck("learn_post: the evidence is quoted", "1", count("uv run ruff check", file=comment))
    ck("learn_post: the home is named", "1", count("test_toml", file=comment))

    one = learnings(ent("run uv run ruff", "Iteration 2 on t1: `uv run ruff check`", "test_toml"))
    setup(one)
    r = go(LW_LIST=T / "empty.json")
    ck("learn_post: no issue exits 0", "0", r.rc)
    ck("learn_post: the label is created", "1", count("^label create", file=log))
    ck("learn_post: the issue is created", "1", count("^issue create", file=log))
    ck("learn_post: the body carries the marker", "1", count("fabro:ralph-learnings", file=T / "learn/issue.md"))
    ck("learn_post: the body says how to accept", "1", count("A Learning is a proposal", file=T / "learn/issue.md"))
    ck("learn_post: one comment on the new issue", "1", count("^issue comment 77", file=log))

    setup(one)
    r = go(LW_LIST_FAIL="1")
    ck("learn_post: a gh failure exits 0", "0", r.rc)
    ck("learn_post: a gh failure says so once", "1", count("could not list the learning issues", r.out))
    ck("learn_post: a gh failure posts nothing", "0", calls("^issue comment"))

    ck("learn_post: the issue body never names the agent label", "0", calls("agent"))

    # The edges: exit is reachable only through the learnings stage.
    redg = edges(RAPH)
    into_exit = grep(["-E", "--", "->exit( |$)"], redg)
    bad = count(".", grep(["-vE", "^(learn_prep|learn_post)->exit( |$)"], into_exit), pipe=True)
    ck("learn edges: only learn_prep and learn_post reach exit", "0", bad)
    ck("learn edges: learn_prep is the guard", "1", count("learn_prep->learn context.learn=run", redg, flags="xF"))
    ck("learn edges: every terminal node reaches learn_prep", "7",
       count("^(close_noop|return_to_triage|mark_stuck|report_merged|report_blocked|report_queued|mark_needs_human)->learn_prep", redg, flags="E"))
