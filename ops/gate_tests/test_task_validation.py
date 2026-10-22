"""Task validation (ADR 0025 C7): backlog's validate -- the scoped gate, every full-suite
fallback, the placeholder a timeout leaves, the parsed edge -- and the merge phase's skip of a
tree Final validation passed (C8)."""

from harness import (GRAPH, ORIG_PATH, SHARED, cat, count, edges, git, head1, jq, lastjson,
                     lines, present, rm, sh, stage, stub, write)

SCOPED_OK = '{"version":1,"mode":"scoped","reason":"","base":"deadbeef","changed":["backend/app/a.py"],"checks":[{"name":"prek","args":[]}],"targets":[{"name":"backend","args":["tests/a.py"],"whole":false,"why":{}}],"untested":[],"unchecked":[],"skipped":[],"ok":true,"prepared_seconds":3,"prepare":{"exit_code":0},"results":[{"kind":"check","name":"prek","exit_code":0,"seconds":1},{"kind":"target","name":"backend","exit_code":0,"seconds":2}],"log":"/tmp/fabro/task_check.log"}'
SCOPED_RED = '{"version":1,"mode":"scoped","reason":"","base":"deadbeef","changed":["backend/app/a.py"],"checks":[{"name":"prek","args":[]}],"targets":[{"name":"backend","args":["tests/a.py"],"whole":false,"why":{}}],"untested":["backend/app/quiet.py"],"unchecked":["docs/x.md"],"skipped":[],"ok":false,"prepared_seconds":4,"prepare":{"exit_code":0},"results":[{"kind":"check","name":"prek","exit_code":1,"seconds":1,"tail":"lint exploded"},{"kind":"target","name":"backend","exit_code":1,"seconds":2,"tail":"tests/a.py::t FAILED"}],"log":"/tmp/fabro/task_check.log"}'
FULL = '{"version":1,"mode":"full","reason":"no_config","base":"deadbeef","changed":[],"checks":[],"targets":[],"untested":[],"unchecked":[],"skipped":[],"ok":null,"prepared_seconds":0,"results":[],"log":"/tmp/fabro/task_check.log"}'
SCOPED_PREP = '{"version":1,"mode":"scoped","reason":"","base":"deadbeef","changed":["backend/app/models.py"],"checks":[{"name":"prek","args":[]}],"targets":[{"name":"backend","args":["tests/a.py"],"whole":false,"why":{}}],"untested":[],"unchecked":[],"skipped":[],"ok":false,"prepared_seconds":0,"prepare":{"exit_code":1,"error":"prepare step 3 failed: alembic upgrade head exited 1"},"results":[{"kind":"check","name":"prek","exit_code":0,"seconds":1}],"log":"/tmp/fabro/task_check.log"}'

# The stub fabro-io: writes the canned result to --out, echoes it, records that it was
# called, and captures the placeholder rework.md before it is replaced.
FABRO_IO_STUB = """#!/bin/sh
out=
while [ $# -gt 0 ]; do
  case "$1" in --out) out=$2; shift 2 ;; *) shift ;; esac
done
if [ -n "${TV_READS_REWORK:-}" ] && [ -f "$TV_ROOT/feedback/rework.md" ]; then
  cp "$TV_ROOT/feedback/rework.md" "$TV_READS_REWORK"
fi
if [ -n "${TV_CANNED:-}" ] && [ -f "$TV_CANNED" ]; then
  [ -n "$out" ] && cp "$TV_CANNED" "$out"
  cat "$TV_CANNED"
fi
[ -n "${TV_SAY:-}" ] && echo "$TV_SAY"
[ -n "${TV_MARK:-}" ] && echo called >> "$TV_MARK"
exit "${TV_IO_RC:-0}"
"""


def test_task_validation(ck, tmp_path):
    T = tmp_path / "tv"
    for d in ("repo/.fabro", "feedback", "bin"):
        (T / d).mkdir(parents=True, exist_ok=True)
    gate = stage(ck, "validate", T)
    stub(T / "repo/.fabro", "setup.sh", '#!/bin/sh\necho setup >> "$TV_LOG"\nexit "${TV_SETUP_RC:-0}"\n')
    stub(T / "repo/.fabro", "ci.sh", '#!/bin/sh\necho ci >> "$TV_LOG"\nexit "${TV_CI_RC:-0}"\n')
    stub(T / "bin", "fabro-io", FABRO_IO_STUB)
    log, mark = T / "run.log", T / "io.called"
    fixed = {"TV_LOG": log, "TV_MARK": mark, "TV_ROOT": T}
    st = {}

    def reset(canned, final_fix=False):
        write(log, ""); rm(mark, T / "task_check.json", T / "validate_output.log", T / "reads_rework")
        rm(T / "feedback"); (T / "feedback").mkdir()
        write(T / "current_task.json", '{"final_fix":false}')
        write(T / "task_base_sha", "deadbeef\n")
        write(T / "canned.json", canned)
        st["env"] = {"TV_CANNED": T / "canned.json", "TV_IO_RC": "0", "TV_SETUP_RC": "0", "TV_CI_RC": "0"}
        rm(T / "rework.prev", T / "task_check_error.log")
        if final_fix:
            write(T / "current_task.json", '{"final_fix":true}')

    def tv_run():
        r = sh(gate, cwd=T / "repo", path=f"{T / 'bin'}:{ORIG_PATH}", env={**fixed, **st["env"]})
        st.update(out=r.out, rc=r.rc)

    ctx = lambda k: jq(f".context_updates.{k}", lastjson(st["out"]))
    rework = T / "feedback/rework.md"
    called = lambda: present(mark)

    # Scoped green: no setup.sh, no ci.sh, one routing object, ok true.
    reset(SCOPED_OK); tv_run()
    ck("tv scoped green: exits 0", "0", st["rc"])
    ck("tv scoped green: mode", "scoped", ctx("task_validation_mode"))
    ck("tv scoped green: ok", "true", ctx("task_validation_ok"))
    ck("tv scoped green: fabro-io called", "present", called())
    ck("tv scoped green: no setup/ci", "0", lines(log))
    ck("tv scoped green: one brace object", "1", count("[{}]", st["out"]))
    ck("tv scoped green: reason empty", "", ctx("task_validation_reason"))
    ck("tv scoped green: selected", "1", ctx("task_validation_selected"))
    ck("tv scoped green: prepare seconds", "3", ctx("task_validation_prepare_s"))

    # Scoped red: the rework file names the failing items and their tails; exit 0.
    reset(SCOPED_RED); tv_run()
    ck("tv scoped red: exits 0", "0", st["rc"])
    ck("tv scoped red: ok", "false", ctx("task_validation_ok"))
    ck("tv scoped red: header", "## Validation failed (exit 1): task validation", head1(rework))
    ck("tv scoped red: check line", "1", count("^- check `prek` (exit 1)$", file=rework))
    ck("tv scoped red: target line with args", "1", count("^- target `backend` (exit 1): tests/a.py$", file=rework))
    ck("tv scoped red: check tail", "1", count("lint exploded", file=rework))
    ck("tv scoped red: target tail", "1", count("tests/a.py::t FAILED", file=rework))
    ck("tv scoped red: no setup/ci", "0", lines(log))
    ck("tv scoped red: one brace object", "1", count("[{}]", st["out"]))
    ck("tv scoped red: unchecked published", "1", ctx("task_validation_unchecked"))

    # mode: full (no_config): the full suite runs; green and red.
    reset(FULL); tv_run()
    ck("tv full green: exits 0", "0", st["rc"])
    ck("tv full green: mode", "full", ctx("task_validation_mode"))
    ck("tv full green: reason", "no_config", ctx("task_validation_reason"))
    ck("tv full green: ok", "true", ctx("task_validation_ok"))
    ck("tv full green: setup and ci ran", "2", lines(log))
    ck("tv full green: counts are 0", "0 0 0",
       jq('[.context_updates.task_validation_selected, .context_updates.task_validation_unchecked, .context_updates.task_validation_prepare_s] | join(" ")', lastjson(st["out"])))

    reset(FULL); st["env"]["TV_CI_RC"] = "1"; tv_run()
    ck("tv full red: exits 0", "0", st["rc"])
    ck("tv full red: ok", "false", ctx("task_validation_ok"))
    ck("tv full red: today's header", "## Validation failed (exit 1): ./.fabro/setup.sh && ./.fabro/ci.sh", head1(rework))

    # final_fix: fabro-io is never called; the full suite runs; reason=final_fix.
    reset(SCOPED_OK, final_fix=True); tv_run()
    ck("tv final_fix: mode", "full", ctx("task_validation_mode"))
    ck("tv final_fix: reason", "final_fix", ctx("task_validation_reason"))
    ck("tv final_fix: fabro-io not called", "absent", called())
    ck("tv final_fix: full suite ran", "2", lines(log))

    # No task-check in the image: fabro-io writes no result and exits 1 -> full suite.
    reset(SCOPED_OK); st["env"].update(TV_CANNED="", TV_IO_RC="1"); tv_run()
    ck("tv no task-check: mode", "full", ctx("task_validation_mode"))
    ck("tv no task-check: reason", "task_check_error", ctx("task_validation_reason"))
    ck("tv no task-check: full suite ran", "2", lines(log))

    # The placeholder is written before anything runs, so a timeout is not read as the
    # previous round's feedback.
    reset(SCOPED_OK); write(rework, "STALE FEEDBACK\n")
    st["env"]["TV_READS_REWORK"] = T / "reads_rework"; tv_run()
    ck("tv placeholder: replaced before fabro-io runs", "1", count("^## Validation failed (timeout): task validation did not finish$", file=T / "reads_rework"))
    ck("tv placeholder: not the stale text", "0", count("STALE FEEDBACK", file=T / "reads_rework"))

    # A green visit puts back the feedback it found, so a later reader (rescue_brief quotes
    # rework.md when round > 0) never sees the timeout placeholder for a passing visit.
    reset(SCOPED_OK); write(rework, "PRIOR ROUND\n"); tv_run()
    ck("tv green restores: previous feedback back", "PRIOR ROUND", cat(rework))
    reset(SCOPED_OK); tv_run()
    ck("tv green restores: no placeholder left", "absent", present(rework))
    reset(FULL); write(rework, "PRIOR ROUND\n"); tv_run()
    ck("tv full green restores: previous feedback", "PRIOR ROUND", cat(rework))

    # A preparation failure stops the targets: rework.md names it and quotes its error.
    reset(SCOPED_PREP); tv_run()
    ck("tv prepare red: ok", "false", ctx("task_validation_ok"))
    ck("tv prepare red: header", "## Validation failed (exit 1): task validation", head1(rework))
    ck("tv prepare red: listed", "1", count("^- preparation of the Test environment (exit 1)$", file=rework))
    ck("tv prepare red: error quoted", "1", count("alembic upgrade head exited 1", file=rework))
    ck("tv prepare red: check not listed", "0", count("^- check", file=rework))

    # untested is published beside unchecked.
    reset(SCOPED_RED); tv_run()
    ck("tv untested published", "1", ctx("task_validation_untested"))
    reset(FULL); tv_run()
    ck("tv untested 0 on the full path", "0", ctx("task_validation_untested"))

    # If the routing object cannot be built, the node fails instead of exiting 0 with no
    # update, which would let a stale task_validation_ok=true reach review.
    reset(jq('.prepared_seconds = "abc"', SCOPED_OK, args=["-c"], raw=False)); tv_run()
    ck("tv emission failure: exits 1", "1", st["rc"])
    ck("tv emission failure: no object", "0", count("[{}]", st["out"]))

    # task-check crashed: its output is kept before the full suite overwrites the log.
    reset(SCOPED_OK); st["env"].update(TV_CANNED="", TV_IO_RC="2", TV_SAY="task-check: boom"); tv_run()
    ck("tv task_check_error: output kept", "1", count("task-check: boom", file=T / "task_check_error.log"))

    # The parsed edges.
    E = edges(GRAPH)
    ck("tv edge: validate->review needs task_validation_ok", "1", count("validate->review outcome=succeeded && context.task_validation_ok=true", E, flags="xF"))
    ck("tv edge: validate->rework_router unconditional", "1", count("validate->rework_router ", E, flags="xF"))
    ck("tv edge: validate has no other exit", "2", count("^validate->", E))


def test_task_validation_merge_phase_skip(ck, tmp_path):
    T = tmp_path / "tvmerge"
    (T / "feedback").mkdir(parents=True)
    gate = stage(ck, "validate", T, graph=SHARED, label="validate (shared)")
    log = T / "run.log"
    repo = T / "repo"

    def tv_repo():
        rm(repo); (repo / ".fabro").mkdir(parents=True)
        git("-C", repo, "init", "-q", "-b", "main")
        git("-C", repo, "config", "user.email", "t@t"); git("-C", repo, "config", "user.name", "t")
        stub(repo / ".fabro", "setup.sh", '#!/bin/sh\necho setup >> "$TV_LOG"\n')
        stub(repo / ".fabro", "ci.sh", '#!/bin/sh\necho ci >> "$TV_LOG"\n')
        write(repo / "a.txt", "x\n")
        git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qm", "init")
        write(log, ""); rm(T / "final_validated_tree", T / "fix_attempts", T / "validated_tree")

    def run_it():
        return sh(gate, cwd=repo, env={"TV_LOG": log})

    tree = lambda: git("-C", repo, "rev-parse", "HEAD^{tree}")
    vt_field = lambda i: cat(T / "validated_tree").split(" ")[i] if " " in cat(T / "validated_tree") else cat(T / "validated_tree")

    tv_repo(); write(T / "fix_attempts", "2\n")
    write(T / "final_validated_tree", tree() + "\n")
    r = run_it()
    ck("merge skip: exits 0", "0", r.rc)
    ck("merge skip: no setup/ci", "0", lines(log))
    ck("merge skip: ci_ok true", "true", jq(".context_updates.ci_ok", lastjson(r.out)))
    ck("merge skip: says skipped", "1", count("validate: skipped", r.out))
    ck("merge skip: fix_attempts unchanged", "2", cat(T / "fix_attempts"))
    ck("merge skip: the file survives", "present", present(T / "final_validated_tree"))
    ck("merge skip: records the tree as final", "final", vt_field(1))
    ck("merge skip: records the matching tree", cat(T / "final_validated_tree"), vt_field(0))

    tv_repo()
    write(T / "final_validated_tree", tree() + "\n")
    with open(repo / "a.txt", "a") as f:
        f.write("y\n")
    git("-C", repo, "commit", "-qam", "a real change")
    run_it()
    ck("merge skip: a new commit runs the suite", "2", lines(log))
    ck("merge skip: a changed tree writes merge, not final", "merge", vt_field(1))

    tv_repo()
    write(T / "final_validated_tree", tree() + "\n")
    with open(repo / "a.txt", "a") as f:
        f.write("dirty\n")
    run_it()
    ck("merge skip: a dirty worktree runs the suite", "2", lines(log))
    ck("merge skip: a dirty worktree writes merge", "merge", vt_field(1))
    git("-C", repo, "checkout", "-q", "--", "a.txt")

    tv_repo()
    run_it()
    ck("merge skip: no file runs the suite", "2", lines(log))

    # A red run writes no validated_tree: the rm at the top stays, and the red branch writes
    # nothing.
    tv_repo()
    stub(repo / ".fabro", "ci.sh", '#!/bin/sh\necho ci >> "$TV_LOG"\nexit 1\n')
    git("-C", repo, "add", "-A"); git("-C", repo, "commit", "-qam", "red ci")
    run_it()
    ck("merge skip: a red run leaves no validated_tree", "absent", present(T / "validated_tree"))
