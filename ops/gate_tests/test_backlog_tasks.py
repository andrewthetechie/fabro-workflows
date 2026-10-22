"""backlog's per-task support nodes: excerpts, the task budget and remainder, and the rescue
gate's brief and partial remainder."""

import glob
from types import SimpleNamespace

from harness import (ORIG_PATH, cat, count, grep, head1, jq, lastjson, lines, mkreadok, present,
                     rm, rm_glob, run, sh, stage, stub, wc_c, which, write)


def nofc_path(tmp_path):
    """The tools a node needs, minus fabro-code: jq and git only, then the system dirs."""
    d = tmp_path / "nofc-bin"
    d.mkdir(parents=True, exist_ok=True)
    for tool in ("jq", "git"):
        (d / tool).symlink_to(which(tool))
    return f"{d}:/usr/bin:/bin"


# ---------------------------------------------------------------------------
# excerpts — the code the task dossier cites, copied for the coder, always exit 0
# ---------------------------------------------------------------------------
def test_excerpts(ck, tmp_path):
    T = tmp_path / "excerpts"
    for d in ("bin", "wt/src", "wt/tests"):
        (T / d).mkdir(parents=True, exist_ok=True)
    gate = stage(ck, "excerpts", T)
    NOFC = nofc_path(tmp_path)
    fc_path = f"{T / 'bin'}:{NOFC}"
    ex_run = lambda path: sh(gate, cwd=T / "wt", path=path)
    write(T / "wt/src/a.py", "".join(f"line {i} of a\n" for i in range(1, 61)))
    write(T / "wt/tests/t.py", "".join(f"line {i} of t\n" for i in range(1, 31)))
    # 2000 lines of 30 bytes: one 150-line range is 4500 bytes, so eight ranges fit the cap.
    write(T / "wt/src/big.py", "".join(f"big {i:025d}\n" for i in range(1, 2001)))
    code = T / "task-code.md"
    headings = lambda: grep("^## ", file=code).replace("\n", "|")

    # 1. No dossier: nothing is written, and a stale task-code.md is removed.
    rm(T / "task-context.md"); write(code, "stale\n")
    r = ex_run(NOFC)
    ck("no dossier: exit 0", "0", r.rc)
    ck("no dossier: no task-code.md", "absent", present(code))
    ck("no dossier: says so", "1", count("no task dossier", r.out))

    # 2. Ranges are copied verbatim, merged per file, and missing files are skipped. A
    # single line with no fabro-code is the window N-3..N+12, clamped to the file.
    write(T / "task-context.md", """# Task dossier: t1 Do the thing
## Files and symbols
- src/a.py:10-14 `f`: the seam; also src/a.py:16-20 `g`, merged with the first
- src/a.py:40-45 `h`; `src/a.py:2` the import line
- ./src/a.py:58-70 runs past the end of the file
- gone/x.py:1-5 does not exist; :30 a bare line number is skipped
## Tests
- tests/t.py:29
""")
    r = ex_run(NOFC)
    ck("ranges: exit 0", "0", r.rc)
    ck("ranges: one line of stdout", "1", count(".", r.out))
    ck("ranges: no routing object", "0", count("context_updates", r.out))
    ck("ranges: headings", "## src/a.py:1-20|## src/a.py:40-45|## src/a.py:58-60|## tests/t.py:26-30", headings())
    ck("ranges: block is the file text",
       "\n".join(f"line {i} of a" for i in range(40, 46)),
       run(["awk", "/^## src[/]a[.]py:40-45$/ {getline; on = 1; next} on && /^````$/ {exit} on", str(code)]).out)
    ck("ranges: missing file skipped", "0", count("gone/x.py", file=code))
    scratch = glob.glob(f"{T}/excerpts.refs*") + ([str(T / "task-code.md.tmp")] if (T / "task-code.md.tmp").exists() else [])
    ck("ranges: no scratch files left", "0", str(len(scratch)))

    # 3. With fabro-code, a single line becomes the narrowest symbol that holds it.
    stub(T / "bin", "fabro-code", """#!/bin/sh
[ "$1" = show ] || exit 64
case "$2" in
  src/a.py:2)    echo '# src/a.py:1-4  function  imports' ;;
  src/a.py:30)   echo '# src/a.py:30-30  variable  LIMIT' ;;
  tests/t.py:29) echo 'no symbol at tests/t.py:29'; exit 1 ;;
esac
echo '## calls'
exit 0
""")
    r = ex_run(fc_path)
    ck("fabro-code: exit 0", "0", r.rc)
    ck("fabro-code: symbol range used",
       "## src/a.py:1-4|## src/a.py:10-20|## src/a.py:40-45|## src/a.py:58-60|## tests/t.py:26-30", headings())
    write(T / "task-context.md", "# Task dossier: const\n- src/a.py:30 `LIMIT` is one line\n")
    ex_run(fc_path)
    ck("fabro-code: a one-line symbol keeps the window", "## src/a.py:27-42", grep("^## ", file=code))

    # 4. Past 40000 bytes a range is listed, not copied, and nothing is cut mid-range.
    big_ranges = "".join(f"- src/big.py:{i * 160 + 1}-{i * 160 + 150}\n" for i in range(12))
    write(T / "task-context.md", "# Task dossier: big\n" + big_ranges)
    r = ex_run(NOFC)
    ck("cap: exit 0", "0", r.rc)
    ck("cap: copied ranges", "8", count("^## src/big.py", file=code))
    ck("cap: listed ranges", "4", count("^- src/big.py", file=code))
    ck("cap: under the byte cap", "1", "1" if code.stat().st_size < 42000 else "0")
    ck("cap: says how much", "1", count("copied 8 range(s), 36000 bytes", r.out))

    # 4b. Call sites (docs/coder-tweaks/07). A stub fabro-code whose `callers` prints two
    # rows and a footer: both rows appear, each followed by its call line, and no footer.
    stub(T / "bin", "fabro-code", """#!/bin/sh
case "$1" in
  show) echo '## calls'; exit 0 ;;
  callers)
    case "$2" in
      f) echo 'src/a.py:30  function  g'; echo 'tests/t.py:5  function  test_f'; echo '[index synced]'; exit 0 ;;
      *) echo "no callers of $2 found"; exit 1 ;;
    esac ;;
esac
exit 64
""")
    write(T / "task-context.md", """# Task dossier: t1 Do the thing
## Files and symbols
- src/a.py:10-14 `f` and `nobody(x)`; `src/a.py:2` is not a symbol; `f` again
## Tests
- tests/t.py:29 `ignored_symbol`
""")
    r = ex_run(fc_path)
    ck("callers: exit 0", "0", r.rc)
    ck("callers: section after the code", "## src/a.py:1-14|## tests/t.py:26-30|## Where these are used", headings())
    ck("callers: both rows", "2", count("^- .*:[0-9]*  function  ", file=code))
    ck("callers: call line follows row", "    line 30 of a", grep(["-A1", "^- src/a.py:30  function  g"], file=code).split("\n")[-1])
    ck("callers: second call line", "    line 5 of t", grep(["-A1", "^- tests/t.py:5  "], file=code).split("\n")[-1])
    ck("callers: no footer", "0", count("index synced", file=code))
    ck("callers: one symbol listed", "1", count("^### ", file=code))
    ck("callers: scratch files removed", "0", str(len(glob.glob(f"{T}/task-code.md.*"))))
    ex_run(NOFC)
    ck("callers: no fabro-code, no section", "0", count("Where these are used", file=code))
    # Near the cap the call sites go first: eight 4500-byte code blocks leave no room.
    write(T / "task-context.md", "# Task dossier: big\n## Files and symbols\n- `s1` `s2` `s3` `s4` `s5` `s6` `s7` `s8` `s9` `s10` `s11` `s12`\n" + big_ranges)
    stub(T / "bin", "fabro-code", """#!/bin/sh
[ "$1" = callers ] || { echo '## calls'; exit 0; }
i=0; while [ $i -lt 15 ]; do echo "src/big.py:$((i + 1))  function  caller$i"; i=$((i + 1)); done
""")
    r = ex_run(fc_path)
    ck("callers cap: exit 0", "0", r.rc)
    ck("callers cap: every code block kept", "8", count("^## src/big.py", file=code))
    ck("callers cap: some call sites cut", "1", "1" if int(count("^### ", file=code)) < 12 else "0")
    ck("callers cap: under the byte cap", "1", "1" if code.stat().st_size <= 42000 else "0")

    # 5. A dossier with no usable range leaves no task-code.md behind.
    write(code, "stale\n")
    write(T / "task-context.md", "# Task dossier: none\n- src/a.py has no range\n")
    r = ex_run(NOFC)
    ck("no ranges: exit 0", "0", r.rc)
    ck("no ranges: no task-code.md", "absent", present(code))


# ---------------------------------------------------------------------------
# task budget and remainder (ADR 0011 D6)
#
# A run implements at most 8 DECOMPOSED tasks -- counted as tasks improve_gate routes to
# the coder, decompose tasks and their split slices -- and next_task moves the decomposed
# tasks past that to remainder.json. file_remainder then files them as ONE issue the
# scheduler holds until the PR merges. Extra-review follow-ups, and slices split from one
# (`from_extra`), are exempt: they never count and are never moved, so they are worked in
# this run even with the budget spent.
# ---------------------------------------------------------------------------
def tasks_json(a: int, b: int) -> str:
    """t<a>..t<b> as a compact array, the way the fixtures print it."""
    return "[" + ",".join(
        f'{{"id":"t{i}","title":"T{i}","body":"do t{i}","files":[],"covers":[],"source":"decompose"}}'
        for i in range(a, b + 1)) + "]"


def test_task_budget_and_remainder(ck, tmp_path):
    T = tmp_path / "budget"
    for d in ("bin", "feedback", "extra"):
        (T / d).mkdir(parents=True, exist_ok=True)
    nt = stage(ck, "next_task", T)
    ig = stage(ck, "improve_gate", T)
    ep = stage(ck, "extra_prep", T)
    fr = stage(ck, "file_remainder", T)
    mkreadok(T, "ok1", "current_task.json", "issue.json")
    stub(T / "bin", "git", """#!/bin/sh
case "$1 $2" in
  "rev-parse --abbrev-ref") echo "fabro/run/01TESTRUN" ;;
esac
exit 0
""")
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue create")
      [ -f "$GH_STATE/create_fails" ] && { echo "HTTP 422" >&2; exit 1; }
      echo "https://github.com/o/r/issues/77"; exit 0 ;;
  "pr comment")
      # keep the body so the checks can read it
      shift 2; while [ $# -gt 0 ]; do [ "$1" = "--body-file" ] && cp "$2" "$GH_STATE/pr_comment.txt"; shift; done
      exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": T / "gh.log", "GH_STATE": T}
    log = T / "gh.log"
    run_ig = lambda: sh(ig, cwd=T, path=path, env=env)
    run_nt = lambda: sh(nt, path=path, env=env, stderr="drop")
    coded = lambda: cat(T / "tasks_coded")
    cur = lambda: jq(".id", file=T / "current_task.json")
    ids = lambda f: jq('[.[].id]|join(" ")', file=T / f)

    def setup(n_coded, idx, last):
        rm_glob(T, "remainder*.json", "remainder_issue", "remainder_body.md", "pr_comment.txt",
                "create_fails", "improve_result.json", "extra_round")
        write(log, "")
        write(T / "tasks.json", tasks_json(1, last))
        write(T / "tasks_coded", f"{n_coded}\n")
        write(T / "task_index", f"{idx}\n")

    def jq_inplace(filt, f="tasks.json"):
        write(T / f, jq(filt, file=T / f, raw=False) + "\n")

    def result(text):
        write(T / "improve_result.json", text)

    # 1. improve_gate counts a task routed to the coder, and only that.
    setup(3, 1, 2)
    write(T / "current_task.json", '{"id":"t1","title":"T1","body":"b","files":[],"covers":[],"source":"decompose"}')
    result('{"disposition":"ready","task_title":"T1","task_body":"sharpened","_io":{"visit":"ok1"}}')
    run_ig()
    ck("ready counts toward the budget", "4", coded())
    result('{"disposition":"redundant","reason":"already done","_io":{"visit":"ok1"}}')
    run_ig()
    ck("redundant does not count", "4", coded())
    write(T / "current_task.json", '{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}')
    result('{"disposition":"ready","task_title":"X1","task_body":"sharpened","_io":{"visit":"ok1"}}')
    run_ig()
    ck("extra-review ready does not count", "4", coded())

    # 1b. A split of an extra-review follow-up stamps its slices from_extra, and a slice
    #     that is then routed to the coder does not count either.
    setup(4, 9, 8)
    jq_inplace('. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}]')
    write(T / "current_task.json", jq(".[8]", file=T / "tasks.json", raw=False) + "\n")
    rm(T / "split_rounds")
    result('{"disposition":"split","tasks":[{"id":"x1a","title":"X1a","body":"b"},{"id":"x1b","title":"X1b","body":"b"}],"_io":{"visit":"ok1"}}')
    run_ig()
    ck("extra split: slices carry from_extra", "true true", jq('[.[8:][] | .from_extra | tostring] | join(" ")', file=T / "tasks.json"))
    ck("extra split: nothing counted", "4", coded())
    write(T / "current_task.json", jq(".[8]", file=T / "tasks.json", raw=False) + "\n")
    result('{"disposition":"ready","task_title":"X1a","task_body":"sharpened","_io":{"visit":"ok1"}}')
    run_ig()
    ck("extra slice ready does not count", "4", coded())
    setup(4, 1, 2)
    result('{"disposition":"split","tasks":[{"id":"t1a","title":"T1a","body":"b"},{"id":"t1b","title":"T1b","body":"b"}],"_io":{"visit":"ok1"}}')
    write(T / "current_task.json", jq(".[0]", file=T / "tasks.json", raw=False) + "\n")
    run_ig()
    ck("decompose split: slices not exempt", "false false", jq('[.[0:2][] | .from_extra | tostring] | join(" ")', file=T / "tasks.json"))
    rm(T / "split_rounds")

    # 2. Under budget, next_task selects as before.
    setup(7, 7, 10)
    run_nt()
    ck("under budget: selects t8", "t8", cur())
    ck("under budget: no remainder", "absent", present(T / "remainder.json"))

    # 3. Budget spent with two tasks left: both go to the remainder, the run moves on.
    setup(8, 8, 10)
    out = run_nt().out
    ck("spent: tasks_done", "true", jq(".context_updates.tasks_done", lastjson(out)))
    ck("spent: remainder holds t9 t10", "t9 t10", ids("remainder.json"))
    ck("spent: queue trimmed to 8", "8", jq("length", file=T / "tasks.json"))
    ck("spent: says so", "1", count("task budget of 8 decomposed tasks spent", out))

    # 4. Extra-review follow-ups appended after the budget is spent (as extra_gate does,
    #    including a slice split from one) are worked in this run, not moved.
    jq_inplace('''. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"},
         {"id":"x2a","title":"X2a","body":"b","files":[],"covers":[],"source":"split","from_extra":true}]''')
    out = run_nt().out
    ck("extras: not done", "false", jq(".context_updates.tasks_done", lastjson(out)))
    ck("extras: selects x1", "x1", cur())
    ck("extras: remainder unchanged", "t9 t10", ids("remainder.json"))
    run_nt()
    ck("extras: then selects x2a", "x2a", cur())
    out = run_nt().out
    ck("extras: then done", "true", jq(".context_updates.tasks_done", lastjson(out)))

    # 4b. A decomposed task behind the budget still moves, and joins the remainder without
    #     duplicating an id already there; an extra-review task beside it stays.
    setup(8, 8, 10)
    write(T / "remainder.json", tasks_json(9, 9))
    jq_inplace('. + [{"id":"x1","title":"X1","body":"b","files":[],"covers":[],"source":"extra-review"}]')
    run_nt()
    ck("mixed: remainder t9 t10", "t9 t10", ids("remainder.json"))
    ck("mixed: queue keeps x1 at slot 9", "x1", jq(".[8].id", file=T / "tasks.json"))
    ck("mixed: selects x1", "x1", cur())

    # 5. extra_prep: a remainder does not stop the second extra-review round; the round
    #    cap of 2 still does.
    write(T / "extra_round", "0\n")
    done = lambda: jq(".context_updates.extra_done", lastjson(sh(ep, path=path, env=env, stderr="drop").out))
    ck("first extra round runs", "false", done())
    ck("second round runs with a remainder", "false", done())
    ck("round cap still ends it", "true", done())

    # 6. file_remainder with nothing to file: no gh call at all.
    setup(3, 3, 3)
    write(T / "issue.json", '{"number":350,"title":"Big issue"}'); write(T / "pr_number", "7\n")
    r = sh(fr, path=path, env=env)
    ck("no remainder: exit 0", "0", r.rc)
    ck("no remainder: no gh call", "0", lines(log))

    # 7. file_remainder files one held issue, with the marker first, and says so on the PR.
    write(T / "remainder.json", tasks_json(9, 10))
    r = sh(fr, path=path, env=env)
    creates = lambda: grep("^issue create", file=log)
    ck("files: exit 0", "0", r.rc)
    ck("files: one issue create", "1", count("^issue create", file=log))
    ck("files: held, not queued", "1", count("--label agent-remainder --label ai-generated", creates(), pipe=True))
    ck("files: never labels agent", "0", count("--label agent ", creates(), pipe=True))
    ck("files: marker is the first line", "<!-- fabro:remainder parent=350 pr=7 -->", head1(T / "remainder_body.md"))
    ck("files: lists the tasks", "2", count("^### T", file=T / "remainder_body.md"))
    ck("files: records the number", "77", cat(T / "remainder_issue"))
    ck("files: PR comment names it", "yes", "yes" if run(["grep", "-q", "#77", str(T / "pr_comment.txt")]).rc == 0 else "no")

    # 8. A second visit (human_rescue -> [P] -> open_pr) files nothing new.
    write(log, "")
    sh(fr, path=path, env=env)
    ck("second visit: no new issue", "0", count("^issue create", file=log))

    # 9. If the issue cannot be filed, the tasks are listed on the PR instead.
    rm(T / "remainder_issue", T / "pr_comment.txt"); write(T / "create_fails", ""); write(log, "")
    r = sh(fr, path=path, env=env)
    ck("create fails: still exit 0", "0", r.rc)
    ck("create fails: tasks on the PR", "1", count("^- T10$", file=T / "pr_comment.txt"))
    ck("create fails: nothing recorded", "absent", present(T / "remainder_issue"))

    # 10. An Architecture issue's remainder keeps the label (ADR 0012 D8).
    rm(T / "remainder_issue", T / "create_fails"); write(log, "")
    write(T / "issue.json", '{"number":350,"title":"Big issue","labels":[{"name":"architecture"}]}')
    sh(fr, path=path, env=env)
    ck("architecture remainder: labelled", "1", count("--label architecture", creates(), pipe=True))


# ---------------------------------------------------------------------------
# rescue_brief and partial_remainder — the rescue gate explains itself
#
# Every edge into human_rescue passes through rescue_brief, which writes the gate's summary
# into `response.rescue_brief` and points `last_stage` at it. Its facts come from
# failure_signature (stdin) and the run's files. partial_remainder sits on the [P] edge and
# moves the unfinished and unstarted tasks to remainder.json, so file_remainder files them
# instead of dropping them. Fixtures are shaped on run 01M3Y86Z013N2V1QDNNG591MBQ
# (womens-fantasy-sports#1279): 7 tasks, 5 landed, task 6 out of rework tiers on a
# .github/workflows/ finding, task 7 never started.
# ---------------------------------------------------------------------------
RESCUE_GIT = """#!/bin/sh
case "$1 $2" in
  "diff --shortstat") echo " 40 files changed, 859 insertions(+), 983 deletions(-)" ;;
  "diff --quiet") [ -f "$GIT_STATE/empty_diff" ] && exit 0; exit 1 ;;
  "log --format=%H") [ -f "$GIT_STATE/wf_commit" ] && echo deadbeef ;;
  "log --no-merges") [ -f "$GIT_STATE/wf_commit" ] && echo deadbeef ;;
  "rev-list --no-merges") echo deadbeef ;;
  "diff-tree --no-commit-id") [ -f "$GIT_STATE/wf_commit" ] && echo .github/workflows/ci.yml ;;
  "rev-parse --abbrev-ref") echo "fabro/run/01TESTRUN" ;;
  "diff origin/main") [ -f "$GIT_STATE/markers" ] && echo '+<<<<<<< HEAD' ;;
  "log --format=") echo frontend/src/x.test.tsx ;;
  "log --format=%cI") echo 2026-10-12T21:44:44Z ;;
  "log --format=%h %s") echo 'ca63aaa refactor: the same change (#1746)' ;;
esac
exit 0
"""


def rescue_env(ck, tmp_path):
    """rescue_brief, partial_remainder, file_remainder and next_task staged against one fabro dir."""
    T = tmp_path / "rescue"
    (T / "bin").mkdir(parents=True)
    FAB = T / "fabro"
    stub(T / "bin", "git", RESCUE_GIT)
    stub(T / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue create") echo "https://github.com/o/r/issues/88"; exit 0 ;;
  "pr comment") shift 2; while [ $# -gt 0 ]; do [ "$1" = "--body-file" ] && cp "$2" "$GH_STATE/pr_comment.txt"; shift; done; exit 0 ;;
esac
exit 0
""")
    path = f"{T / 'bin'}:{ORIG_PATH}"
    base = {"GIT_STATE": T, "GH_LOG": T / "gh.log", "GH_STATE": T}
    rb = stage(ck, "rescue_brief", T, root=FAB)
    pr = stage(ck, "partial_remainder", T, root=FAB)
    fr = stage(ck, "file_remainder", T, root=FAB)
    nt = stage(ck, "next_task", T, root=FAB)

    def rs_tasks(n):
        return tasks_json(1, n)

    def setup(idx, landed, cur):
        rm(FAB, T / "empty_diff", T / "wf_commit", T / "markers", T / "pr_comment.txt")
        (FAB / "feedback").mkdir(parents=True); (FAB / ".io").mkdir()
        write(T / "gh.log", "")
        write(FAB / "issue.json", '{"number":1279,"title":"Cut over to session auth","labels":[{"name":"agent-in-progress"}]}')
        write(FAB / "tasks.json", rs_tasks(7))
        write(FAB / "task_index", f"{idx}\n")
        if landed:
            write(FAB / "completed.md", "".join(f"### Task: T{i}\n- Review: ok\n\n" for i in range(1, landed + 1)))
        write(FAB / "current_task.json", jq(f'.[] | select(.id == "{cur}")', file=FAB / "tasks.json", raw=False) + "\n")
        write(FAB / "round", "0\n")
        write(FAB / ".io/stage.json", '{"workflow":"backlog","node":"review","visit":"v1","started":"2026-10-02T00:00:00Z"}')

    state = {}

    def rs_run(sig, **env):
        e = {**base, **env}
        unset = () if "FABRO_AUTO_MERGE" in env else ("FABRO_AUTO_MERGE",)
        r = sh(rb, path=path, env=e, unset=unset, stdin=sig)
        state["out"], state["rc"] = r.out, r.rc
        state["j"] = sh(rb, path=path, env=e, unset=unset, stdin=sig, stderr="drop").out

    text = lambda **kw: jq('.context_updates["response.rescue_brief"]', state["j"], **kw)
    has = lambda s: "yes" if any(s in line for line in text().split("\n")) else "no"
    only_object = lambda: count(".", state["out"])

    run_pr = lambda: sh(pr, path=path, env=base)
    return SimpleNamespace(**locals())


def test_rescue_brief(ck, tmp_path):
    """Cases 1-9: what the rescue gate's brief says, for each way a run stops."""
    E = rescue_env(ck, tmp_path)
    T, FAB, setup, rs_run, state, text, has, only_object, rs_tasks = (E.T, E.FAB, E.setup, E.rs_run, E.state, E.text, E.has, E.only_object, E.rs_tasks)

    # 1. The rework ladder ran out on a finding no agent may fix.
    setup(6, 5, "t6"); write(FAB / "round", "6\n")
    write(FAB / "feedback/rework.md", "## Review findings to fix\nCriterion 4 is missing.\n- .github/workflows/build-publish.yml:73-77 still passes VITE_AUTH0_*\n")
    rs_run("", FABRO_AUTO_MERGE="1")
    ck("ladder: exits 0", "0", state["rc"])
    ck("ladder: routing object is last", "rescue_brief", jq(".context_updates.last_stage", lastjson(state["out"])))
    ck("ladder: brief is the gate's text", "rescue_brief", jq(".context_updates.last_stage", state["j"]))
    ck("ladder: says why", "yes", has("the rework ladder ran out. Task 6 failed validate or review at every tier (6 rounds)."))
    ck("ladder: names the task", "yes", has("Unfinished task 6 of 7: T6"))
    ck("ladder: counts landed and unstarted", "yes", has("Tasks: 5 of 7 passed review, 1 not started."))
    ck("ladder: branch size", "yes", has("Branch against main: 40 files changed"))
    ck("ladder: quotes the findings", "yes", has("Criterion 4 is missing."))
    ck("ladder: flags the workflow file", "yes", has("No agent may edit that directory"))
    ck("ladder: guidance cannot help", "yes", has("**Type guidance:** Cannot help"))
    ck("ladder: [P] says what ships", "yes", has("Opens a PR with the 5 task(s) that passed review and the unreviewed edits of task 6"))
    ck("ladder: [P] files the rest", "yes", has("The 2 task(s) not done (task 6 included) go to one held remainder issue"))
    ck("ladder: [P] can auto-merge", "yes", has("merges it with no human"))
    ck("ladder: [X] says what is lost", "yes", has("so the 5 task(s) that passed review are lost"))
    ck("ladder: [X] names the issue", "yes", has("Labels #1279 agent-stuck"))
    ck("ladder: prints only the object", "1", only_object())
    ck("ladder: brief kept in a file", "1", count("^\\*\\*Why the run stopped:\\*\\*", file=FAB / "rescue_brief.md"))

    # 2. A failed node: the signature names it, and guidance retries the unfinished task.
    setup(3, 2, "t3"); write(FAB / "round", "1\n")
    write(FAB / "feedback/rework.md", "## Review findings to fix\nThe test asserts nothing.\n")
    rs_run("rework_t2|transient_infra|stage timed out after <n>ms")
    ck("failed node: exits 0", "0", state["rc"])
    ck("failed node: names it", "yes", has("rework_t2 failed (transient_infra): stage timed out after <n>ms"))
    ck("failed node: guidance retries it", "yes", has("tries task 3 again with your text"))
    ck("failed node: no workflow flag", "no", has("No agent may edit"))
    ck("failed node: auto-merge off host", "yes", has("Auto-merge is off on this host"))

    # 3. A validate failure: header plus the tail, with ANSI colour stripped.
    setup(2, 1, "t2"); write(FAB / "round", "2\n")
    esc = "\x1b"
    write(FAB / "feedback/rework.md", "## Validation failed (exit 1): ./.fabro/setup.sh && ./.fabro/ci.sh\n"
          + "".join(f"{esc}[31mline {i}{esc}[39m\n" for i in range(1, 31)))
    rs_run("")
    ck("validate: keeps the header", "yes", has("## Validation failed (exit 1)"))
    ck("validate: keeps the last line", "yes", has("line 30"))
    ck("validate: drops the middle", "no", has("line 5"))
    ck("validate: strips ANSI", "0", count(esc, text(), pipe=True))
    ck("validate: gate reason when round<6", "yes", has("a gate rejected its agent"))
    ck("validate: names the last agent", "yes", has("The last agent stage was review."))

    # 4. Findings from an earlier task are not shown: round 0 means no rework yet.
    setup(4, 3, "t4")
    write(FAB / "feedback/rework.md", "## Review findings to fix\nSTALE from task 3\n")
    rs_run("improve|deterministic|script failed")
    ck("stale findings: not shown", "no", has("STALE from task 3"))

    # 5. Every task landed: nothing unfinished, guidance has nothing to fix.
    setup(7, 7, "t7")
    rs_run("standards|deterministic|unknown")
    ck("all landed: no unfinished task", "no", has("Unfinished task"))
    ck("all landed: counts", "yes", has("Tasks: 7 of 7 passed review, 0 not started."))
    ck("all landed: guidance is useless", "yes", has("No task is unfinished"))
    ck("all landed: nothing to file", "no", has("remainder issue"))

    # 6. open_pr_prep's two refusals, and the PR-already-open case.
    setup(6, 5, "t6"); write(T / "empty_diff", ""); rs_run("")
    ck("empty diff: [P] refused", "yes", has("The branch changes no file"))
    setup(6, 5, "t6"); write(T / "wf_commit", ""); rs_run("")
    ck("workflow commit: [P] refused", "yes", has("which the sandbox token cannot push"))
    setup(7, 7, "t7"); write(FAB / "pr_number", "1319\n"); rs_run("review_merge|deterministic|unknown")
    ck("PR open: [P] reuses it", "yes", has("PR #1319 is already open"))
    ck("PR open: [X] keeps it", "yes", has("PR #1319 stays open"))

    # 6b. The final merge conflicted and resolve_merge_gate gave up: no signature, a file.
    setup(7, 7, "t7"); write(FAB / "final_merge_conflict", "frontend/src/x.test.tsx\n"); rs_run("")
    ck("final conflict: says why", "yes", has("main changed the same lines as this branch before the PR was built"))
    ck("final conflict: names the file", "yes", has("Conflicting files: frontend/src/x.test.tsx"))
    ck("final conflict: [P] retries merge", "yes", has("Runs the merge agent once more on the same conflict"))
    ck("final conflict: guidance cannot", "yes", has("which may not run git"))
    ck("final conflict: prints only object", "1", only_object())

    # 6c. Committed conflict markers: [P] cannot get past open_pr_prep.
    setup(7, 7, "t7"); write(T / "markers", ""); rs_run("open_pr_prep|deterministic|script failed with exit code: <n>")
    ck("markers: [P] refused", "yes", has("adds unresolved conflict markers"))
    ck("markers: no [P] PR promise", "no", has("Opens a PR"))

    # 6d. Landed tasks on a tree equal to main: main already has the change.
    setup(7, 7, "t7"); write(T / "empty_diff", ""); rs_run("open_pr_prep|deterministic|script failed with exit code: <n>")
    ck("superseded: says so", "yes", has("Main already has this change"))
    ck("superseded: lists main's commit", "yes", has("- ca63aaa refactor: the same change (#1746)"))
    ck("superseded: closing is the human's", "yes", has("close #1279 as completed by it"))
    ck("superseded: prints only object", "1", only_object())
    rm(FAB); (FAB / "feedback").mkdir(parents=True)
    write(FAB / "issue.json", '{"number":1279,"title":"t","labels":[]}'); write(FAB / "tasks.json", rs_tasks(2))
    write(T / "empty_diff", ""); rs_run("open_pr_prep|deterministic|script failed with exit code: <n>")
    ck("superseded: not without a landed task", "no", has("Main already has this change"))
    rm(T / "empty_diff")

    # 6b. The coder blocked on scope (#17, docs/unmet-criteria C2): task_block_gate kept the
    # reason, and the brief says it instead of "a gate rejected its agent's output".
    setup(2, 1, "t2")
    write(FAB / "task_block_scope.md", "The coder blocked on task 2 (T2): Needs src/api/client.ts, outside the task.\n")
    rs_run("")
    ck("scope block: says the coder blocked", "yes", has("Needs src/api/client.ts, outside the task."))
    ck("scope block: not the generic line", "no", has("a gate rejected its agent"))
    ck("scope block: prints only the object", "1", only_object())

    # 7. The auto-merge sentence follows the same switches the merge phase reads.
    setup(6, 5, "t6")
    write(FAB / "issue.json", '{"number":1279,"labels":[{"name":"architecture"}]}')
    rs_run("", FABRO_AUTO_MERGE="1")
    ck("architecture: no hold, default sentence", "yes", has("every merge gate passes"))
    setup(6, 5, "t6"); write(FAB / "auto_merge_repo", "0\n"); rs_run("", FABRO_AUTO_MERGE="1")
    ck("repo switch off: a human merges", "yes", has("off for this repository"))

    # 8. claim died before anything: the directory does not exist yet.
    rm(FAB)
    rs_run("claim|deterministic|script failed with exit code: <n>")
    ck("nothing: exits 0", "0", state["rc"])
    ck("nothing: creates the directory", "yes", "yes" if FAB.is_dir() else "no")
    ck("nothing: still a routing object", "rescue_brief", jq(".context_updates.last_stage", state["j"]))
    ck("nothing: [P] refused", "yes", has("never read the issue"))
    ck("nothing: no tasks", "yes", has("Tasks: none"))
    write(FAB / "issue_number", "353\n"); rs_run("")
    ck("nothing: issue from issue_number", "yes", has("Labels #353 agent-stuck"))

    # 9. The brief is capped, however long the findings are.
    setup(6, 5, "t6"); write(FAB / "round", "6\n")
    write(FAB / "feedback/rework.md", "## Review findings to fix\n" + "".join(f"finding {i} {0:0390d}\n" for i in range(1, 41)))
    rs_run("")
    ck("cap: brief at most 3500 bytes", "yes", "yes" if wc_c(text(strip=False)) <= 3501 else "no")
    ck("cap: still valid JSON", "rescue_brief", jq(".context_updates.last_stage", state["j"]))



def test_partial_remainder_and_final_red_brief(ck, tmp_path):
    """Cases 10-18: partial_remainder, file_remainder, and the brief after a red final validation."""
    E = rescue_env(ck, tmp_path)
    T, FAB, path, base, fr, nt, setup, rs_run, has, only_object, run_pr = (E.T, E.FAB, E.path, E.base, E.fr, E.nt, E.setup, E.rs_run, E.has, E.only_object, E.run_pr)

    # 10. Mid-task: the unfinished task and the unstarted one move; tasks.json keeps the landed.
    setup(6, 5, "t6")
    write(FAB / "task_block_scope.md", "The coder blocked on task T6: out of scope\n")
    r = run_pr()
    ck("partial: drops the scope note", "absent", present(FAB / "task_block_scope.md"))
    ck("partial: exits 0", "0", r.rc)
    ck("partial: moves task 6 and 7", "t6,t7", jq('map(.id) | join(",")', file=FAB / "remainder.json"))
    ck("partial: tasks.json keeps 5", "5", jq("length", file=FAB / "tasks.json"))
    ck("partial: marks the kind", "partial", cat(FAB / "remainder_kind"))
    ck("partial: says so", "1", count("2 unfinished task(s) moved", r.out))
    ck("partial: marks unreviewed edits", "present", present(FAB / "partial_unreviewed"))
    write(FAB / "final_merge_conflict", "a.txt\n"); write(FAB / "merge_attempts", "2\n")
    r = sh(nt, path=path, env=base)
    ck("partial: next_task starts nothing", "true", jq(".context_updates.tasks_done", lastjson(r.out)))
    ck("next_task done: clears the final conflict", "absent", present(FAB / "final_merge_conflict"))
    ck("next_task done: resets merge_attempts", "0", cat(FAB / "merge_attempts"))

    # 11. The current task already landed: only the unstarted ones move.
    setup(6, 6, "t6")
    run_pr()
    ck("landed current: moves only t7", "t7", jq('map(.id) | join(",")', file=FAB / "remainder.json"))
    ck("landed current: nothing unreviewed", "absent", present(FAB / "partial_unreviewed"))

    # 12. A budget remainder already holds t7: no duplicate.
    setup(6, 5, "t6")
    write(FAB / "remainder.json", '[{"id":"t7","title":"T7","body":"do t7"},{"id":"t9","title":"T9","body":"do t9"}]')
    run_pr()
    ck("dedupe: keeps one t7", "t7,t9,t6", jq('map(.id) | join(",")', file=FAB / "remainder.json"))

    # 13. Every task finished: nothing moves, nothing is marked.
    setup(7, 7, "t7")
    r = run_pr()
    ck("all done: exits 0", "0", r.rc)
    ck("all done: no remainder", "absent", present(FAB / "remainder.json"))
    ck("all done: no kind marker", "absent", present(FAB / "remainder_kind"))

    # 14. Before decompose: no tasks.json, still exits 0.
    rm(FAB); FAB.mkdir()
    r = run_pr()
    ck("no tasks: exits 0", "0", r.rc)
    ck("no tasks: nothing written", "absent", present(FAB / "remainder.json"))

    # 15. file_remainder tells the next run why the tasks were not done.
    setup(6, 5, "t6"); write(FAB / "pr_number", "1319\n")
    run_pr()
    r = sh(fr, path=path, env=base)
    ck("partial filing: exits 0", "0", r.rc)
    ck("partial filing: held issue", "1", count("--label agent-remainder", grep("^issue create", file=T / "gh.log"), pipe=True))
    ck("partial filing: marker first", "<!-- fabro:remainder parent=1279 pr=1319 -->", head1(FAB / "remainder_body.md"))
    ck("partial filing: body says why", "1", count("an operator chose Accept partial", file=FAB / "remainder_body.md"))
    ck("partial filing: no budget claim", "0", count("task budget", file=FAB / "remainder_body.md"))
    ck("partial filing: lists both tasks", "2", count("^### T", file=FAB / "remainder_body.md"))
    ck("partial filing: PR comment says why", "1", count("chose Accept partial", file=T / "pr_comment.txt"))

    # 16. #35: the final validation failed twice after the fix turn. No signature, and
    #     final_stop (written by open_pr_prep) holds the failing tests.
    setup(7, 7, "t7")
    write(FAB / "final_stop", "FAIL  src/Form.test.tsx > patches the profile\nFAILED tests/test_api.py::test_patch\n")
    rs_run("")
    ck("final red: says why", "yes", has("the final validation (./.fabro/setup.sh && ./.fabro/ci.sh after main was merged in) failed twice"))
    ck("final red: names a test", "yes", has("FAIL  src/Form.test.tsx > patches the profile"))
    ck("final red: names the other", "yes", has("FAILED tests/test_api.py::test_patch"))
    ck("final red: [P] says a draft is next", "yes", has("opens a draft PR with the 7 task(s) that passed review"))
    ck("final red: [P] not back here", "yes", has("instead of coming back here"))
    ck("final red: guidance is a fix task", "yes", has("as a new task to make the final validation pass"))
    ck("final red: prints only the object", "1", only_object())
    rs_run("final_merge|deterministic|script failed")
    ck("final red: a later signature wins", "yes", has("final_merge failed (deterministic)"))
    ck("final red: not the final text then", "no", has("failed twice"))
    ck("final red: outranked stop cleared", "absent", present(FAB / "final_stop"))
    rm(FAB / "final_stop")
    # #17: a scope note left by an earlier block never outranks the final validation, and
    # the final_stop it would have hidden is kept for record_guidance.
    setup(7, 7, "t7"); write(FAB / "final_stop", "FAILED tests/test_api.py::test_patch\n")
    write(FAB / "task_block_scope.md", "The coder blocked on task T3: an old reason\n")
    rs_run("")
    ck("final red over scope: final text", "yes", has("failed twice"))
    ck("final red over scope: no old block", "no", has("an old reason"))
    ck("final red over scope: stop kept", "present", present(FAB / "final_stop"))
    rm(FAB / "final_stop", FAB / "task_block_scope.md")

    # 17. Mid-task [P] says a red final validation drops the unreviewed edits.
    setup(6, 5, "t6"); rs_run("")
    ck("partial: [P] says red edits are dropped", "yes", has("If setup or CI fails with them, they are dropped"))

    # 18. The final-fix task is the unfinished one: it is never a task to file.
    setup(8, 7, "t7")
    write(FAB / "tasks.json", jq('. + [{"id":"final-fix","title":"Make the final validation pass","body":"b","final_fix":true}]', file=FAB / "tasks.json", raw=False) + "\n")
    write(FAB / "current_task.json", jq(".[7]", file=FAB / "tasks.json", raw=False) + "\n"); write(FAB / "round", "7\n")
    write(FAB / "feedback/rework.md", "## Validation failed (exit 1): ./.fabro/setup.sh && ./.fabro/ci.sh\nstill red\n")
    rs_run("")
    ck("fix task: says the fix turn failed", "yes", has("the fix turn for the final validation failed validate or review."))
    ck("fix task: not filed", "no", has("remainder issue"))
    write(FAB / "final_failures.txt", "FAILED tests/test_api.py::test_patch\n"); rs_run("")
    ck("fix task: repeats the final tests", "yes", has("Failing tests in the final validation, before the fix turn"))
    ck("fix task: names the test", "yes", has("FAILED tests/test_api.py::test_patch"))
    r = run_pr()
    ck("fix task: partial exits 0", "0", r.rc)
    ck("fix task: nothing moves", "absent", present(FAB / "remainder.json"))
    ck("fix task: its edits are unreviewed", "present", present(FAB / "partial_unreviewed"))
