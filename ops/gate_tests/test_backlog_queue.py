"""backlog's task queue: decompose_gate, improve_gate, rework_router, drop_followup,
next_task and extra_gate.

`decompose_gate`, `improve_gate` and `next_task` are shell embedded as DOT attributes.
`fabro validate` checks the graph parses; it never runs them. These tests run them against
fixtures: the QUEUE mechanics (selection, splicing, cursor, guards), not whether an agent
fills the contract.
"""

from harness import (ORIG_PATH, cat, count, git, git_rc, jq, jq_ok, lastjson, mkreadok,
                     present, rm, rm_glob, set_visit, sh, stage, stub, write)

TASKS = """[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"},
        {"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c2","c3","c4","c5"],"source":"decompose"},
        {"id":"z","title":"Z","body":"b","files":[],"covers":["c6"],"source":"decompose"}]"""
CUR = '{"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c2","c3","c4","c5"],"source":"decompose"}'
SPLIT2 = """{"disposition":"split","reason":"too big","tasks":[
    {"id":"big-module","title":"Add module","body":"x","files":[],"covers":["c1"]},
    {"id":"big-migrate","title":"Migrate callers","body":"y","files":[],"covers":["c2","c3"]}]}"""

GIT_STUB = "#!/bin/sh\nexit 0\n"


def rc_exists(p) -> str:
    """`[ -e p ]; echo $?`."""
    return "0" if present(p) == "present" else "1"


# ---------------------------------------------------------------------------
# decompose_gate — `covers` normalisation and the oversize warning
# ---------------------------------------------------------------------------
def test_decompose_gate(ck, tmp_path):
    T = tmp_path / "dg"
    gate = stage(ck, "decompose_gate", T)

    def setup(doc):
        rm_glob(T, "*.json", "decompose_attempts", "task_index")
        set_visit(T / "decomposition.json", "ok1", doc)

    def ctx(j, key):
        return jq(f".context_updates.{key}", j)

    mkreadok(T, "ok1", "issue.json")

    setup('''{"status":"issues","summary":"s","issues":[
  {"id":"a","title":"A","body":"b","files":[],"covers":["c1"]},
  {"id":"b","title":"B","body":"b","files":[],"covers":["c2","c3"]}]}''')
    r = sh(gate); J = lastjson(r.out)
    ck("well-sized exits 0", "0", r.rc)
    ck("well-sized oversized=0", "0", ctx(J, "oversized_tasks"))
    ck("well-sized task_count", "2", ctx(J, "task_count"))
    ck("covers preserved", "c1", jq(".[0].covers[0]", file=T / "tasks.json"))
    ck("source stamped", "decompose", jq(".[0].source", file=T / "tasks.json"))
    ck("no spurious warning", "0", count("WARNING", r.out))

    setup('''{"status":"issues","summary":"s","issues":[
  {"id":"a","title":"A","body":"b","files":[],"covers":["c2"]},
  {"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c3","c4","c5","c6"]}]}''')
    r = sh(gate); J = lastjson(r.out)
    ck("oversize does not fail", "0", r.rc)
    ck("oversize counted", "1", ctx(J, "oversized_tasks"))
    ck("oversize warns", "1", count("WARNING", r.out))
    ck("oversize names the task", "1", count("- big: 5 requirements", r.out))
    ck("routing JSON is last object", "issues", ctx(J, "decomp_status"))

    # Boundary: three is the documented limit and must not trip.
    setup('{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":["1","2","3"]}]}')
    ck("three covers not flagged", "0", ctx(lastjson(sh(gate).out), "oversized_tasks"))

    # A model that forgets `covers`, or sends the wrong type, must not break the run
    # or be counted by string length.
    setup('{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[]}]}')
    r = sh(gate)
    ck("missing covers exits 0", "0", r.rc)
    ck("missing covers becomes []", "0", jq(".[0].covers | length", file=T / "tasks.json"))

    setup('{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":"a long string over three chars"}]}')
    r = sh(gate)
    ck("string covers exits 0", "0", r.rc)
    ck("string covers coerced to []", "0", jq(".[0].covers | length", file=T / "tasks.json"))
    ck("string covers not flagged", "0", ctx(lastjson(r.out), "oversized_tasks"))

    setup('{"status":"no_work","summary":"nothing","issues":[]}')
    J = lastjson(sh(gate).out)
    ck("no_work preserved", "no_work", ctx(J, "decomp_status"))
    ck("no_work oversized=0", "0", ctx(J, "oversized_tasks"))

    setup('{"status":"issues","summary":"s","issues":[{"id":"","title":"A","body":"b","files":[]}]}')
    ck("invalid entry still retries", "1", sh(gate).rc)

    # needs_human_review routes straight to mark_stuck, which posts the summary on the
    # issue. The gate is the only writer of stuck_reason.md; prep deletes it.
    rm(T / "stuck_reason.md")
    setup('{"status":"needs_human_review","summary":"blocked by #1278, which is not merged","issues":[]}')
    r = sh(gate); J = lastjson(r.out)
    ck("needs_human_review exits 0", "0", r.rc)
    ck("needs_human_review routes", "needs_human_review", ctx(J, "decomp_status"))
    ck("needs_human_review writes reason", "blocked by #1278, which is not merged", cat(T / "stuck_reason.md"))
    ck("needs_human_review one routing object", "1", count("context_updates", r.out))
    rm(T / "stuck_reason.md")
    setup('{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[]}]}')
    sh(gate)
    ck("issues writes no stuck reason", "absent", present(T / "stuck_reason.md"))
    ck("issues: empty triage_questions", "", ctx(lastjson(sh(gate).out), "triage_questions"))

    # A Contradiction (#17, docs/unmet-criteria C3) goes back to triage, not to mark_stuck:
    # return.json carries both sides, and triage_questions is published for the Discord
    # hook, which fires on return_to_triage's stage_start, before that node runs.
    rm(T / "return.json", T / "stuck_reason.md")
    setup('{"status":"contradiction","summary":"Resend needs a backend the issue rules out.","issues":[],"contradictions":[{"criterion":"The screen offers a \\"Resend email\\" action","non_goal":"No backend changes","needed_surface":"a backend that calls the Auth0 resend API"}]}')
    r = sh(gate); J = lastjson(r.out)
    ck("contradiction exits 0", "0", r.rc)
    ck("contradiction routes", "contradiction", ctx(J, "decomp_status"))
    ck("contradiction one routing object", "1", count("context_updates", r.out))
    ck("contradiction: return source", "decompose", jq(".source", file=T / "return.json"))
    ck("contradiction: return criterion", 'The screen offers a "Resend email" action', jq(".items[0].criterion", file=T / "return.json"))
    ck("contradiction: return non_goal", "No backend changes", jq(".items[0].non_goal", file=T / "return.json"))
    ck("contradiction: return why", "a backend that calls the Auth0 resend API", jq(".items[0].why", file=T / "return.json"))
    ck("contradiction: questions name it", "1", count("Resend email action (conflicts with: No backend changes)", ctx(J, "triage_questions"), pipe=True))
    ck("contradiction: no quote in questions", "0", count('\\"', ctx(J, "triage_questions"), pipe=True))
    ck("contradiction: no stuck reason", "absent", present(T / "stuck_reason.md"))
    rm(T / "return.json")
    setup('{"status":"contradiction","summary":"s","issues":[],"contradictions":[]}')
    ck("contradiction without entries retries", "1", sh(gate).rc)
    setup('{"status":"contradiction","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[]}],"contradictions":[{"criterion":"c","non_goal":"n","needed_surface":"s"}]}')
    ck("contradiction with issues retries", "1", sh(gate).rc)
    setup('{"status":"contradiction","summary":"s","issues":[],"contradictions":[{"criterion":"","non_goal":"n","needed_surface":"s"}]}')
    ck("contradiction empty side retries", "1", sh(gate).rc)
    ck("invalid contradiction writes no return", "absent", present(T / "return.json"))


# ---------------------------------------------------------------------------
# improve_gate — the split splice and its guards
# ---------------------------------------------------------------------------
def test_improve_gate(ck, tmp_path):
    T = tmp_path / "ig"
    gate = stage(ck, "improve_gate", T)
    mkreadok(T, "ok1", "current_task.json", "issue.json")

    def setup(tasks, cur, result, idx, rounds):
        rm_glob(T, "*.json", "task_index", "split_rounds", "improve_attempts")
        write(T / "tasks.json", tasks)
        write(T / "current_task.json", cur)
        set_visit(T / "improve_result.json", "ok1", result)
        write(T / "task_index", f"{idx}\n")
        write(T / "split_rounds", f"{rounds}\n")

    def go():
        return sh(gate, cwd=T, stderr="drop")

    def disp(out):
        return jq(".context_updates.task_disposition", lastjson(out))

    ids = lambda: jq('[.[].id]|join(" ")', file=T / "tasks.json")

    setup(TASKS, CUR, SPLIT2, 2, 0)
    r = go(); J = lastjson(r.out)
    ck("split exits 0", "0", r.rc)
    ck("split disposition", "split", jq(".context_updates.task_disposition", J))
    ck("spliced in place, in order", "a big-module big-migrate z", ids())
    ck("slices stamped source", "decompose split split decompose", jq('[.[].source]|join(" ")', file=T / "tasks.json"))
    ck("cursor rewound to the slot", "1", cat(T / "task_index"))
    ck("split_rounds incremented", "1", cat(T / "split_rounds"))
    ck("task_count republished", "4", jq(".context_updates.task_count", J))

    # Guard 1: a slice can never be split again.
    setup(TASKS, '{"id":"big-1","title":"S","body":"b","files":[],"source":"split"}', SPLIT2, 2, 0)
    r = go()
    ck("slice refuses re-split", "ready", disp(r.out))
    ck("refusal leaves queue alone", "a big z", ids())
    ck("refusal keeps current_task", "big-1", jq(".id", file=T / "current_task.json"))

    # Guard 2: the per-run budget.
    setup(TASKS, CUR, SPLIT2, 2, 2)
    r = go()
    ck("spent budget coerces ready", "ready", disp(r.out))
    ck("spent budget keeps current", "big", jq(".id", file=T / "current_task.json"))

    setup(TASKS, CUR, '{"disposition":"split","tasks":[{"id":"x","title":"X","body":"b","files":[]}]}', 2, 0)
    ck("one slice is invalid", "1", go().rc)

    setup(TASKS, CUR, '{"disposition":"split","tasks":[{"id":"z","title":"X","body":"b","files":[]},{"id":"q","title":"Q","body":"b","files":[]}]}', 2, 0)
    ck("id colliding with queue", "1", go().rc)

    setup(TASKS, CUR, '{"disposition":"split","tasks":[{"id":"dup","title":"X","body":"b","files":[]},{"id":"dup","title":"Q","body":"b","files":[]}]}', 2, 0)
    ck("duplicate ids within split", "1", go().rc)

    # The replaced task's own id is free to reuse -- it is leaving the queue.
    setup(TASKS, CUR, '{"disposition":"split","tasks":[{"id":"big","title":"X","body":"b","files":[]},{"id":"q","title":"Q","body":"b","files":[]}]}', 2, 0)
    ck("slice may reuse replaced id", "0", go().rc)

    # Splice at both ends of the queue.
    setup(TASKS, '{"id":"a","source":"decompose"}', SPLIT2, 1, 0)
    go()
    ck("splice at head", "big-module big-migrate big z", ids())
    ck("splice at head rewinds to 0", "0", cat(T / "task_index"))

    setup(TASKS, '{"id":"z","source":"decompose"}', SPLIT2, 3, 0)
    go()
    ck("splice at tail", "a big big-module big-migrate", ids())

    # The pre-existing dispositions must be untouched by all of the above.
    setup(TASKS, CUR, '{"disposition":"ready","task_id":"big","task_title":"Sharpened","task_body":"nb","task_files":[],"task_covers":["c1"]}', 2, 0)
    r = go()
    ck("ready disposition", "ready", disp(r.out))
    ck("ready rewrites current_task", "Sharpened", jq(".title", file=T / "current_task.json"))
    ck("ready flat: body", "nb", jq(".body", file=T / "current_task.json"))
    ck("ready flat: explicit covers", "c1", jq('.covers|join(" ")', file=T / "current_task.json"))

    # Flat ready: id, files, covers and priority default from the current task, and an
    # id the agent supplies cannot replace it with another (the gate keeps byte-for-byte).
    curp = '{"id":"big","title":"Big","body":"b","files":["f.py"],"covers":["c1","c2"],"priority":"high","source":"decompose"}'
    setup(TASKS, curp, '{"disposition":"ready","task_id":"other","task_title":"S","task_body":"nb"}', 2, 0)
    go()
    ck("flat ready: id from current", "big", jq(".id", file=T / "current_task.json"))
    ck("flat ready: files from current", "f.py", jq('.files|join(" ")', file=T / "current_task.json"))
    ck("flat ready: covers from current", "c1 c2", jq('.covers|join(" ")', file=T / "current_task.json"))
    ck("flat ready: priority from current", "high", jq(".priority", file=T / "current_task.json"))
    ck("flat ready: no null keys", "0", jq("[.[]|select(.==null)]|length", file=T / "current_task.json", raw=False))

    # The nested shape that corrupted in run 01M3SFYASZQ7MEKVKR1A38C4J0 is no longer valid
    # input, and an empty body is not a body.
    setup(TASKS, CUR, '{"disposition":"ready","task":{"id":"big","title":"S","body":"nb"}}', 2, 0)
    ck("nested task is refused", "1", go().rc)
    setup(TASKS, CUR, '{"disposition":"ready","task_title":"S","task_body":""}', 2, 0)
    ck("empty task_body is refused", "1", go().rc)
    ck("ready leaves queue alone", "a big z", ids())

    setup(TASKS, CUR, '{"disposition":"redundant","reason":"done"}', 2, 0)
    ck("redundant disposition", "redundant", disp(go().out))

    setup(TASKS, CUR, '{"disposition":"nonsense"}', 2, 0)
    ck("unknown disposition retries", "1", go().rc)

    # A truncated counter file must not read as "budget unspent". Unquoted `[ $SR
    # -ge 2 ]` on an empty value raises "unary operator expected", which short-
    # circuits the && and silently PERMITS the split.
    setup(TASKS, CUR, SPLIT2, 2, "")
    write(T / "split_rounds", "")
    r = go()
    ck("empty split_rounds is not a crash", "split", disp(r.out))
    ck("empty split_rounds counts as 0", "1", cat(T / "split_rounds"))

    # Same for a garbage counter: treat as spent-from-zero, never as an error.
    setup(TASKS, CUR, SPLIT2, 2, "garbage")
    ck("garbage split_rounds normalised", "split", disp(go().out))

    # `oversized_tasks` and `task_count` are interpolated bare into the routing
    # JSON, so an empty value emits `"oversized_tasks":}` -- unparseable, which makes
    # fabro's routing scan go inert with no error anywhere (AGENTS.md invariant 1).
    T2 = tmp_path / "dg2"
    dg = stage(ck, "decompose_gate", T2)
    mkreadok(T2, "ok1", "issue.json")
    write(T2 / "decomposition.json", '{"status":"issues","summary":"s","issues":[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"]}],"_io":{"visit":"ok1"}}')
    out = sh(dg).out
    ck("routing JSON always parses", "0", jq_ok(lastjson(out)))
    ck("oversized_tasks is a number", "number", jq(".context_updates.oversized_tasks | type", lastjson(out)))


# ---------------------------------------------------------------------------
# improve_gate backstop (docs/coder-tweaks/06): REAL git. An improve checkpoint
# that changed the tree is reverted (staged, not committed) and counted; a clean
# one is left alone. Only HEAD is reverted, never the stage before it.
# ---------------------------------------------------------------------------
def test_improve_gate_backstop(ck, tmp_path):
    T = tmp_path / "igb"
    repo = T / "repo"
    repo.mkdir(parents=True)
    gate = stage(ck, "improve_gate", T)
    mkreadok(T, "ok1", "current_task.json", "issue.json")

    def git_setup():
        rm(repo, T / "improve_touched_tree")
        repo.mkdir()
        git("init", "-q", ".", cwd=repo)
        git("config", "user.email", "t@t", cwd=repo)
        git("config", "user.name", "t", cwd=repo)
        write(repo / "mod.txt", "one\n"); write(repo / "del.txt", "two\n")
        git("add", "-A", cwd=repo); git("commit", "-qm", "base", cwd=repo)
        write(repo / "coder.txt", "coder\n")
        git("add", "-A", cwd=repo); git("commit", "-qm", "coder-checkpoint", cwd=repo)
        write(T / "tasks.json", TASKS); write(T / "current_task.json", CUR)
        write(T / "improve_result.json", '{"disposition":"redundant","reason":"r","_io":{"visit":"ok1"}}')
        write(T / "task_index", "2\n"); write(T / "split_rounds", "0\n")

    git_setup()
    write(repo / "mod.txt", "changed\n"); git("rm", "-q", "del.txt", cwd=repo)
    write(repo / "add.txt", "new\n")
    git("add", "-A", cwd=repo); git("commit", "-qm", "improve-checkpoint", cwd=repo)
    r = sh(gate, cwd=repo, stderr="drop")
    ck("touched improve: exits 0", "0", r.rc)
    ck("touched improve: routing unchanged", "redundant", jq(".context_updates.task_disposition", lastjson(r.out)))
    ck("touched improve: file count", "3", cat(T / "improve_touched_tree"))
    ck("touched improve: names the files", "3", count("^plan changed (reverted): ", r.out))
    ck("touched improve: tree is the coder's", "", git("diff", "--cached", "--name-only", "HEAD^", "--", "coder.txt", cwd=repo))
    ck("touched improve: mod.txt restored", "one", cat(repo / "mod.txt"))
    ck("touched improve: del.txt restored", "two", cat(repo / "del.txt"))
    ck("touched improve: add.txt removed", "1", rc_exists(repo / "add.txt"))
    ck("touched improve: coder work kept", "coder", cat(repo / "coder.txt"))
    ck("touched improve: revert is staged, not committed", "improve-checkpoint", git("log", "-1", "--format=%s", cwd=repo))
    git_setup()
    git("commit", "-q", "--allow-empty", "-m", "improve-checkpoint", cwd=repo)
    r = sh(gate, cwd=repo, stderr="drop")
    ck("clean improve: nothing written", "1", rc_exists(T / "improve_touched_tree"))
    ck("clean improve: nothing reverted", "", git("status", "--porcelain", cwd=repo))
    ck("clean improve: routing unchanged", "redundant", jq(".context_updates.task_disposition", lastjson(r.out)))


# ---------------------------------------------------------------------------
# rework_router — `task_exempt` is looked up in tasks.json by id, because
# improve_gate rewrites current_task.json without `source`/`from_extra`. Only an
# exempt task takes the round-6 edge to drop_followup.
# ---------------------------------------------------------------------------
def test_rework_router(ck, tmp_path):
    T = tmp_path / "rr"
    gate = stage(ck, "rework_router", T)
    write(T / "tasks.json", '[{"id":"t1","source":"decompose"},{"id":"f1","source":"extra-review"},{"id":"s1","source":"split","from_extra":true},{"id":"s2","source":"split","from_extra":false}]')

    def rr_run(task_id, rnd):
        write(T / "current_task.json", f'{{"id":"{task_id}","title":"T"}}')
        write(T / "round", f"{rnd}\n")
        return lastjson(sh(gate).out)

    exempt = lambda j: jq(".context_updates.task_exempt", j)
    J = rr_run("f1", 5)
    ck("round increments", "6", jq(".context_updates.round", J))
    ck("extra-review follow-up exempt", "true", exempt(J))
    ck("round persisted", "6", cat(T / "round"))
    ck("slice of a follow-up exempt", "true", exempt(rr_run("s1", 0)))
    ck("decomposed task not exempt", "false", exempt(rr_run("t1", 5)))
    ck("slice of a decomposed task not exempt", "false", exempt(rr_run("s2", 5)))
    ck("unknown id not exempt", "false", exempt(rr_run("nope", 5)))
    rm(T / "tasks.json")
    J = rr_run("f1", 0)
    ck("no tasks.json not exempt", "false", exempt(J))
    ck("no tasks.json still counts", "1", jq(".context_updates.round", J))
    rm(T / "round")
    write(T / "current_task.json", '{"id":"x"}')
    ck("no round file starts at 1", "1", jq(".context_updates.round", lastjson(sh(gate).out)))


# ---------------------------------------------------------------------------
# drop_followup — REAL git. The follow-up's commits are undone back to the task
# base (added files deleted, modified and deleted files restored), work from
# earlier tasks stays, nothing is committed (the checkpoint does that), and the
# drop is recorded in completed.md for the PR body.
# ---------------------------------------------------------------------------
def test_drop_followup(ck, tmp_path):
    T = tmp_path / "df"
    repo = T / "repo"
    repo.mkdir(parents=True); (T / "review").mkdir()
    gate = stage(ck, "drop_followup", T)
    git("init", "-q", ".", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo); git("config", "user.name", "t", cwd=repo)
    write(repo / "mod.txt", "one\n"); write(repo / "del.txt", "two\n"); write(repo / "earlier.txt", "earlier\n")
    git("add", "-A", cwd=repo); git("commit", "-qm", "task-base", cwd=repo)
    write(T / "task_base_sha", git("rev-parse", "HEAD", cwd=repo) + "\n")
    write(repo / "mod.txt", "changed\n"); git("rm", "-q", "del.txt", cwd=repo)
    write(repo / "add.txt", "new\n"); write(repo / "d" / "f.txt", "x\n")
    git("add", "-A", cwd=repo); git("commit", "-qm", "followup-rework", cwd=repo)
    write(T / "current_task.json", '{"id":"update-vitest-docs","title":"Fix the CI comment"}')
    write(T / "review" / "verdict.json", '{"decision":"changes_requested","summary":"the workflow comment is still stale"}')
    write(T / "completed.md", "### Task: earlier\n")
    r = sh(gate, cwd=repo)
    ck("drop: exits 0", "0", r.rc)
    ck("drop: tree equals the task base", "0", str(git_rc("diff", "--quiet", cat(T / "task_base_sha"), "--", cwd=repo)))
    ck("drop: mod.txt restored", "one", cat(repo / "mod.txt"))
    ck("drop: del.txt restored", "two", cat(repo / "del.txt"))
    ck("drop: add.txt removed", "absent", present(repo / "add.txt"))
    ck("drop: nested added file removed", "absent", present(repo / "d" / "f.txt"))
    ck("drop: earlier work kept", "earlier", cat(repo / "earlier.txt"))
    ck("drop: not committed", "followup-rework", git("log", "-1", "--format=%s", cwd=repo))
    ck("drop: earlier entries kept", "1", count("^### Task: earlier$", file=T / "completed.md"))
    ck("drop: recorded for the PR body", "1", count("^### Dropped follow-up: Fix the CI comment$", file=T / "completed.md"))
    ck("drop: names the task id", "1", count("update-vitest-docs", file=T / "completed.md"))
    ck("drop: carries the last review", "1", count("Last review: the workflow comment is still stale", file=T / "completed.md"))
    rm(T / "task_base_sha")
    ck("drop: no task base fails", "1", "1" if sh(gate, cwd=repo).rc != 0 else "0")
    write(T / "task_base_sha", "0" * 40 + "\n")
    ck("drop: unknown task base fails", "1", "1" if sh(gate, cwd=repo).rc != 0 else "0")


# ---------------------------------------------------------------------------
# next_task drops the baseline_check worktree (docs/coder-tweaks 04): REAL git, so the
# stale `base-tree` of the last task is gone before the next one starts, and the node
# still prints exactly one routing object.
# ---------------------------------------------------------------------------
def test_next_task_worktree_cleanup(ck, tmp_path):
    T = tmp_path / "ntwt"
    repo = T / "repo"
    repo.mkdir(parents=True)
    gate = stage(ck, "next_task", T)
    mkreadok(T, "ok1", "current_task.json", "issue.json")
    git("init", "-q", "-b", "main", ".", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo); git("config", "user.name", "t", cwd=repo)
    write(repo / "a.txt", "a\n"); git("add", "-A", cwd=repo); git("commit", "-qm", "base", cwd=repo)
    write(repo / "b.txt", "b\n"); git("add", "-A", cwd=repo); git("commit", "-qm", "second", cwd=repo)
    git("worktree", "add", "-q", "--detach", T / "base-tree", "HEAD~1", cwd=repo)
    (T / "base-tree" / "node_modules").mkdir()
    (T / "base-tree" / "linked").symlink_to(repo)
    write(T / "tasks.json", "[]\n"); write(T / "task_index", "0\n")
    wt = lambda: str(len(git("worktree", "list", cwd=repo).split("\n")))
    ck("worktree exists before", "2", wt())
    r = sh(gate, cwd=repo, stderr="drop")
    ck("stale worktree removed", "1", wt())
    ck("stale worktree dir removed", "absent", present(T / "base-tree"))
    ck("link target survives", "1", "1" if (repo / "a.txt").is_file() else "0")
    ck("one routing object", "1", count("context_updates", r.out))
    ck("routing says done", "true", jq(".context_updates.tasks_done", lastjson(r.out)))
    ck("no worktree at all is fine", "0", sh(gate, cwd=repo).rc)


# ---------------------------------------------------------------------------
# next_task + improve_gate — the split must resume ON the first slice
# ---------------------------------------------------------------------------
def test_next_task_improve_gate(ck, tmp_path):
    T = tmp_path / "loop"
    (T / "bin").mkdir(parents=True); (T / "feedback").mkdir()
    nt = stage(ck, "next_task", T)
    ig = stage(ck, "improve_gate", T)
    mkreadok(T, "ok1", "current_task.json", "issue.json")
    # next_task shells out to git for the mainline refresh; only selection is under test.
    stub(T / "bin", "git", GIT_STUB)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    run_nt = lambda **kw: sh(nt, path=path, **kw)
    run_ig = lambda: sh(ig, cwd=T, path=path)
    ids = lambda: jq('[.[].id]|join(" ")', file=T / "tasks.json")
    cur = lambda: jq(".id", file=T / "current_task.json")

    write(T / "tasks.json", """[{"id":"small","title":"Small","body":"b","files":[],"covers":["c1"],"source":"decompose"},
 {"id":"big","title":"Big","body":"b","files":[],"covers":["c1","c2","c3","c4","c5"],"source":"decompose"}]
""")
    write(T / "task_index", "0\n"); write(T / "split_rounds", "0\n")

    write(T / "task_blocked.json", '{"kind":"scope","reason":"old"}\n')
    r = run_nt(stderr="drop")
    ck("selects first task", "small", cur())
    ck("drops a stale block file", "absent", present(T / "task_blocked.json"))
    ck("not done yet", "false", jq(".context_updates.tasks_done", lastjson(r.out)))
    run_nt()
    ck("selects oversized task", "big", cur())

    write(T / "improve_result.json", """{"disposition":"split","reason":"five criteria","tasks":[
 {"id":"big-module","title":"Add module","body":"x","files":[],"covers":["c1"]},
 {"id":"big-migrate","title":"Migrate callers","body":"y","files":[],"covers":["c2","c3"]},
 {"id":"big-adr","title":"Record ADR","body":"z","files":[],"covers":["c4","c5"]}],"_io":{"visit":"ok1"}}
""")
    run_ig()
    ck("queue spliced", "small big-module big-migrate big-adr", ids())

    # The regression this guards: an off-by-one here silently SKIPS the first slice.
    r = run_nt(stderr="drop")
    ck("resumes on the first slice", "big-module", cur())
    ck("disposition reset by next", "none", jq(".context_updates.task_disposition", lastjson(r.out)))

    run_ig()
    ck("slice cannot re-split", "small big-module big-migrate big-adr", ids())

    run_nt()
    ck("drains to second slice", "big-migrate", cur())
    run_nt()
    ck("drains to third slice", "big-adr", cur())
    r = run_nt(stderr="drop")
    ck("queue reports done", "true", jq(".context_updates.tasks_done", lastjson(r.out)))

    # AGENTS.md: "Delete a contract file before the agent that writes it runs — an
    # agent that exits succeeded without writing hands the gate its predecessor's
    # result." With `split` in play a stale improve_result.json no longer just
    # mis-improves one task, it can splice the previous task's slices into the queue.
    write(T / "task_index", "0\n")
    write(T / "improve_result.json", '{"disposition":"split","tasks":[],"_io":{"visit":"ok1"}}')
    run_nt()
    ck("next_task clears improve_result", "absent", present(T / "improve_result.json"))

    # Task 04 / 07: next_task also deletes the task dossier (delete-before-write) and
    # re-renders the map after the mainline refresh, discarding fabro-code's stdout --
    # this node's stdout is scanned for `context_updates`, so a leaked map would risk a
    # wrong route. A stub fabro-code that prints to stdout proves the discard.
    stub(T / "bin", "fabro-code", "#!/bin/sh\necho 'MAP LEAKED TO STDOUT'\nexit 0\n")
    write(T / "task-context.md", "# Task dossier: stale")
    write(T / "task-code.md", "# Code the task dossier cites: stale")
    write(T / "tasks.json", '[{"id":"t1","title":"T1","body":"b","files":[],"covers":["c1"],"source":"decompose"}]\n')
    write(T / "task_index", "0\n")
    r = run_nt()
    ck("next_task deletes task-context", "absent", present(T / "task-context.md"))
    ck("next_task deletes task-code", "absent", present(T / "task-code.md"))
    ck("next_task discards fabro-code stdout", "0", count("MAP LEAKED", r.out))
    ck("next_task still one routing object", "1", count("^{.*}$", r.out))


# ---------------------------------------------------------------------------
# extra_gate — follow-up tasks join the same queue under the same size budget
# ---------------------------------------------------------------------------
def test_extra_gate(ck, tmp_path):
    T = tmp_path / "eg"
    (T / "extra").mkdir(parents=True)
    gate = stage(ck, "extra_gate", T)
    mkreadok(T, "ok1", "standards.json", "spec.json", "quality.json", "changed_files.txt",
             "diffstat.txt", "issue.json", "completed.md")

    def setup(tasks, followups):
        rm_glob(T, "*.json", "extra/*.json", "extra_decompose_attempts")
        write(T / "tasks.json", tasks)
        write(T / "extra" / "followups.json", followups)
        set_visit(T / "extra" / "followups.json", "ok1")

    ids = lambda: jq('[.[].id]|join(" ")', file=T / "tasks.json")
    base = '[{"id":"a","title":"A","body":"b","files":[],"covers":["c1"],"source":"decompose"}]'

    setup(base, '{"status":"issues","summary":"s","issues":[{"id":"f1","title":"F","body":"b","files":[],"covers":["finding 1"]}]}')
    r = sh(gate)
    ck("follow-up appends", "0", r.rc)
    ck("follow-up joins the queue", "a f1", ids())
    ck("follow-up source stamped", "extra-review", jq(".[1].source", file=T / "tasks.json"))
    ck("follow-up covers preserved", "finding 1", jq(".[1].covers[0]", file=T / "tasks.json"))
    ck("new_tasks counted", "1", jq(".context_updates.new_tasks", lastjson(r.out)))

    # The queue must stay uniform: `improve` reads `covers` off every task it is
    # handed, whatever wrote it.
    setup(base, '{"status":"issues","summary":"s","issues":[{"id":"f1","title":"F","body":"b","files":[]}]}')
    sh(gate)
    ck("missing covers normalised", "0", jq(".[1].covers | length", file=T / "tasks.json"))

    setup(base, '{"status":"issues","summary":"s","issues":[{"id":"big","title":"F","body":"b","files":[],"covers":["1","2","3","4"]}]}')
    r = sh(gate)
    ck("oversized follow-up warns", "1", count("WARNING", r.out))
    ck("oversized follow-up still merges", "a big", ids())
    ck("extra_gate JSON parses", "0", jq_ok(lastjson(r.out)))

    # Dedup by id is what stops a follow-up round re-adding what is already queued.
    setup(base, '{"status":"issues","summary":"s","issues":[{"id":"a","title":"dup","body":"b","files":[],"covers":["x"]}]}')
    r = sh(gate)
    ck("duplicate follow-up dropped", "a", ids())
    ck("dropped duplicate counts 0", "0", jq(".context_updates.new_tasks", lastjson(r.out)))
