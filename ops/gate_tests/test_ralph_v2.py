"""ralph-v2 (ADR 0034, docs/plans/ralph-v2/05-07): the package's own rules and its Task loop.

Everything ralph-v2 copies from ralph is held by R10 in `ops/check-graph-invariants.py`, and
the graphs' behaviour by the gate tests that already cover ralph's nodes. What no other gate
covers is the file itself: the profile images run Debian, where `/bin/sh` is dash and rejects
a bashism at parse time, a template token left in a node renders as the literal text, and the
dispatch payload has a size the server refuses.

Pin what tasks 05-07 decided and nothing more: the fourth rung is gone from the edges, the
graph stays inside the size the plan gives it (`P14`: under 350,000 bytes), the prompts and
schemas are the plan's assets byte for byte, because the plan is the review of that text, and
the Task loop runs `pick` -> `task_plan_gate` -> `excerpts` -> `task_block_gate` -> `record` ->
`pick` in the order `task_plan_gate` and `record` were written for (C1, C2).
"""

import json
import shutil
import sys
from pathlib import Path

from harness import (ORIG_PATH, RAPH2, REPO_ROOT, WF, cat, count, diff, edges, extract, git,
                     jq, jq_n, lastjson, mkreadok, present, rm, run, script_nodes, set_visit, sh,
                     stage, stub, syntax_ok, write)
# The two substitutions the server makes in a command node's script before it runs. Every
# other `{{ }}` in an attribute is literal text, so a render token somewhere else is a bug
# only this can see: `fabro validate` accepts it.
SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b"),
)

# P14's cap on the package's graph file. The plan's own number, not a measured limit.
GRAPH_MAX_BYTES = 350_000


def test_every_command_node_is_posix_sh(ck, tmp_path):
    """`sh -n` under dash when the harness has it, the shell the images have (conftest)."""
    nodes = script_nodes(RAPH2)
    ck("the graph has command nodes to check", "true", str(bool(nodes)).lower())
    for node in nodes:
        script = extract(RAPH2, node)
        for token, value in SUBSTITUTIONS:
            script = script.replace(token, value)
        path = write(tmp_path / f"{node}.sh", script)
        ok, err = syntax_ok(path)
        ck(f"{node}: valid POSIX sh", "true", "true" if ok else (err or "sh -n failed"))


def test_no_script_carries_a_bashism_or_a_python_leak(ck):
    """`<<<` is a here-string dash does not know, and `read_text` is a Python leftover.

    `<<<<<<<` is a git conflict marker and not a here-string, so it is cut before the count:
    `resolve_merge_gate`, `resolve_base_gate` and `open_pr` grep for it on purpose.
    """
    for node in script_nodes(RAPH2):
        script = extract(RAPH2, node).replace("<<<<<<<", "")
        ck(f"{node}: no here-string", "0", count("<<<", script, flags="F"))
        ck(f"{node}: no Python", "0", count("read_text", script, flags="F"))


def test_only_claim_keeps_a_render_token(ck):
    """`claim` is the one node whose script interpolates inputs: the issue number and the
    box's pool. Anywhere else a `{{` is text the agent or the shell reads."""
    for node in script_nodes(RAPH2):
        script = extract(RAPH2, node)
        if node == "claim":
            ck("claim: the issue number", "1", count("{{ inputs.issue_number }}", script, flags="F"))
            ck("claim: the coder pool", "1", count("{{ inputs.coder_pool }}", script, flags="F"))
            ck("claim: and nothing else", "2", count("{{", script, flags="F"))
            continue
        ck(f"{node}: no unexpanded template", "0", count("{{", script, flags="F"))


def test_the_graph_fits_its_size_cap(ck):
    size = len(RAPH2.read_bytes())
    ck("the graph is under 350,000 bytes (P14)", "true", str(size < GRAPH_MAX_BYTES).lower())


# The plan's own copies of the text it reviewed (docs/plans/ralph-v2/assets).
ASSETS = REPO_ROOT / "docs/plans/ralph-v2/assets"
V2 = WF / "ralph-v2"


def same_bytes(a: Path, b: Path) -> str:
    """`""` only when both files exist and `diff` finds no difference between them.

    Not `diff(a, b)` alone: `diff` names a missing file on stderr only, which the harness
    drops, so a deleted schema, prompt or asset reads as identical to its original.
    """
    for p in (a, b):
        if not p.is_file():
            return f"missing: {p}"
    return diff(a, b)


def test_the_new_prompts_are_the_plan_s_assets(ck):
    """Task 06 copies the plan's three prompts unchanged, and each one is a read-only stage's
    only output through `submit`: a prompt that names a `/tmp/fabro` path would be naming an
    input the manifest does not serve, and one that lets the chat pass for a result loses it."""
    for name in ("task_plan", "task_refute", "task_review"):
        copy = V2 / f"prompts/{name}.md.j2"
        text = copy.read_text()
        ck(f"{name}.md.j2: byte for byte the plan's prompt", "",
           same_bytes(ASSETS / f"prompts/{name}.md.j2", copy))
        ck(f"{name}.md.j2: names no /tmp/fabro path", "0", count("/tmp/fabro", text, flags="F"))
        ck(f"{name}.md.j2: submit is the only result", "1",
           count("Only a result written by `submit`", text, flags="F"))


def test_the_new_schemas_are_the_plan_s_assets(ck):
    """The four contracts task 06 adds, copied unchanged: `cargo test` compiles them and the
    generator inlines them into a `workflow.toml`, so the asset is still what was reviewed."""
    for name in ("iteration_v2", "task_plan", "task_refute", "task_review"):
        ck(f"{name}.schema.json: byte for byte the plan's schema", "",
           same_bytes(ASSETS / f"schemas/{name}.schema.json",
                      WF / f"_io/schemas/{name}.schema.json"))


def test_iterate_is_ralphs_prompt_plus_task_06_s_edits(ck, tmp_path):
    """`iterate.md.j2` is ralph's plus the script's recorded edits, and the script is the
    record: run it on ralph's current prompt and the result is v2's. A later fix to v1's
    prompt therefore fails here instead of leaving the two arms' Iterations silently apart.
    The script itself refuses a replacement ralph's text no longer holds."""
    out = tmp_path / "iterate.md.j2"
    r = run([sys.executable, str(ASSETS / "iterate-replacements.py"),
             str(WF / "ralph/prompts/iterate.md.j2"), str(out)])
    ck("the replacements script still fits ralph's iterate prompt", "0", r.rc)
    ck("ralph-v2's iterate prompt is the script's output", "",
       same_bytes(out, V2 / "prompts/iterate.md.j2"))


def test_the_fourth_rung_is_gone_from_the_edges(ck):
    """E16's ladder is three rungs, so no edge reads `rung=4` and no node offers one."""
    e = edges(RAPH2)
    ck("the graph has edges to check", "true", str(bool(e)).lower())
    ck("no edge reaches iterate_t4", "0", count("iterate_t4", e, flags="F"))
    ck("no edge reads the fourth rung", "0", count("rung=4", e, flags="F"))
    ck("excerpts still routes the three it has", "3", count("excerpts->iterate_t", e, flags="F"))


# ---------------------------------------------------------------------------
# Task 07 (C1, C2): the Task plan, `pick` and `record`.
#
# Each runs the real scripts, in the order the graph runs them, against a real git
# repository and a fake fabro-io receipt, as `test_ralph.py` runs ralph's.
# ---------------------------------------------------------------------------

# A plan that passes every rule of C2 for a Task with three acceptance statements: `src/a.txt`
# and `test/a.txt` are in the fixture repository, and `test/a.txt` has the two lines g2 cites.
PLAN = {
    "_io": {"stage": "task_plan"},
    "context": "## Files and symbols\n\n- src/a.txt:1\n",
    "goals": [
        {"id": "g1", "goal": "Add the limit", "covers": [1, 2],
         "check": "`make check`", "files": ["src/a.txt"]},
        {"id": "g2", "goal": "Cover the empty case", "covers": [3],
         "check": "test/a.txt:2", "files": []},
    ],
    "edge_cases": [{"id": "e1", "case": "an empty list", "expected": "no error", "covers": 1}],
    "bug_class": "",
    "risks": [],
}

TASK_FILES = {"src/a.txt": "one\n", "test/a.txt": "one\ntwo\n"}


def commit_all(repo: Path, files: dict, msg: str) -> None:
    for name, text in files.items():
        write(repo / name, text)
    git("-C", repo, "add", "-A")
    git("-C", repo, "commit", "-qm", msg)


def base_repo(origin: Path, repo: Path) -> None:
    """A bare origin whose HEAD is main, and a main branch that pushed to it."""
    git("init", "-q", "--bare", origin)
    git("-C", origin, "symbolic-ref", "HEAD", "refs/heads/main")
    git("init", "-q", "-b", "main", repo)
    git("-C", repo, "config", "user.email", "t@t")
    git("-C", repo, "config", "user.name", "t")
    commit_all(repo, TASK_FILES, "base")
    git("-C", repo, "remote", "add", "origin", origin)
    git("-C", repo, "push", "-q", "origin", "main")


def plain_repo(repo: Path) -> None:
    git("-C", repo, "init", "-q", "-b", "main")
    git("-C", repo, "config", "user.email", "t@t")
    git("-C", repo, "config", "user.name", "t")
    commit_all(repo, TASK_FILES, "base")


def plan(**over) -> dict:
    """PLAN with one field replaced: one broken rule at a time."""
    p = json.loads(json.dumps(PLAN))
    p.update(over)
    return p


def jq1(filt: str, path: Path) -> str:
    """`jq -c filt path`: one line, so an array compares as text."""
    return run(["jq", "-c", filt, str(path)]).out


def published(out: str, key: str) -> str:
    """One context key a node printed, as text (`None` prints as `None`)."""
    return str(json.loads(lastjson(out))["context_updates"].get(key))


def no_braces(ck, label: str, out: str) -> None:
    """fabro scans stdout and stderr for brace-balanced objects: only the last line has one."""
    body = out[:-1] if out.endswith("\n") else out
    rest = "\n".join(body.split("\n")[:-1])
    ck(f"{label}: no other stdout or stderr line has a brace", "0",
       count("[{}]", rest, pipe=True))


def task_list(*tasks) -> str:
    return json.dumps(list(tasks)) + "\n"


def one_task(**over) -> dict:
    t = {"id": "t1", "title": "A", "source": "plan", "status": "open",
         "iterations": 0, "misses": 0, "reviews": 0}
    t.update(over)
    return t


# What C1 asks each node to print on every path: `pick` its six keys (its step 6), `record`
# its two (its step 6). An edge reads a key a path did not print from an earlier visit's context.
PICK_KEYS = "loop,loop_exhausted,phase,rebase_failed,rung,task_step"
RECORD_KEYS = "iteration_passed,review_due"


def test_task_plan_gate(ck, tmp_path):
    """C2 step 1: every rule, alone, is a retry with its own sentence; and a second
    rejection is the fallback plan, one goal per acceptance statement."""
    T = tmp_path / "plan-sb"
    repo = tmp_path / "plan-repo"
    T.mkdir(parents=True)
    repo.mkdir()
    plain_repo(repo)
    gate = stage(ck, "task_plan_gate", T, graph=RAPH2, label="task_plan_gate (ralph-v2)")
    mkreadok(T, "v1")
    task = {"id": "t1", "title": "A", "source": "plan", "acceptance": ["a1", "a2", "a3"],
            "files": ["src/a.txt"], "context": "## The Task\n\nDo the thing.\n"}

    def setup():
        rm(T / "task_plan.json", T / "task_plan_error.txt", T / "task_plan_tries",
           T / "task_plans/t1.json", T / "task-context.md")
        write(T / "tasks.json", task_list(one_task()))
        write(T / "current_task.json", json.dumps(task) + "\n")

    def put(p, visit="v1"):
        rm(T / "task_plan.json")
        if p is not None:
            write(T / "task_plan.json", json.dumps(p) + "\n")
            if visit is not None:
                set_visit(T / "task_plan.json", visit)

    def go():
        r = sh(gate, cwd=repo)
        ck("task_plan_gate: exits 0", "0", r.rc)
        no_braces(ck, "task_plan_gate", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("task_plan_gate: the four keys on every path", "route,type,goals,edges",
           ",".join(k[len("task_plan_"):] for k in
                    ("task_plan_route", "task_plan_type", "task_plan_goals", "task_plan_edges")
                    if k in pub))
        ck("task_plan_gate: never publishes rung", "False", str("rung" in pub))
        return pub

    def reject(p, sentence, visit="v1"):
        """One broken rule, alone: a retry, the reason on disk, and the plan deleted."""
        setup()
        put(p, visit)
        pub = go()
        ck("a rejected plan is a retry", "retry", pub["task_plan_route"])
        ck("a rejected plan has no type", "none", pub["task_plan_type"])
        ck("a retry counts one try", "1", cat(T / "task_plan_tries"))
        ck("a retry deletes the plan", "absent", present(T / "task_plan.json"))
        ck("a retry stores one sentence", "1", count("^" + sentence, file=T / "task_plan_error.txt"))
        ck("a retry writes no plan", "absent", present(T / "task_plans/t1.json"))

    setup()
    put(plan())
    pub = go()
    ck("a valid plan goes on", "go", pub["task_plan_route"])
    ck("a valid plan is planned", "planned", pub["task_plan_type"])
    ck("goals counted", "2", pub["task_plan_goals"])
    ck("edge cases counted", "1", pub["task_plan_edges"])
    ck("the plan is stored without _io", "false", jq("has(\"_io\")", file=T / "task_plans/t1.json"))
    ck("the stored plan starts with no goal done", "[]", jq(".goals_done", file=T / "task_plans/t1.json"))
    ck("the stored plan says planned", "planned", jq(".plan_type", file=T / "task_plans/t1.json"))
    ck("the Task's plan_type is set", "planned", jq(".[0].plan_type", file=T / "tasks.json"))
    ck("a valid plan writes no error", "absent", present(T / "task_plan_error.txt"))
    ck("a valid plan renders the dossier", "1", count("^## Goals$", file=T / "task-context.md"))
    ck("the goal line carries its covers and check", "1",
       count("^- g1: Add the limit Covers: 1, 2. Check: `make check`$", file=T / "task-context.md"))

    reject(plan(context="x" * 14001 + "\n- src/a.txt:1\n"),
           "context is 14016 bytes and the stage accepts at most 14000")
    # The limit is bytes (C2), where the schema's `maxLength` counts characters.
    reject(plan(context="é" * 7001), "context is 14002 bytes and the stage accepts at most 14000")
    setup()
    put(plan(context="é" * 7000))
    ck("14000 bytes is the limit, in bytes", "go", go()["task_plan_route"])
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1], "check": "`x`", "files": []},
                       {"id": "g2", "goal": "b", "covers": [2], "check": "`x`", "files": []}]),
           "the goals do not cover exactly the acceptance numbers 1 to 3")
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3], "check": "`x`", "files": []},
                       {"id": "g1", "goal": "b", "covers": [1], "check": "`x`", "files": []}]),
           "two goals share an id")
    # No index above `n`, and no gap: the right count is not the right set.
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 4], "check": "`x`", "files": []}]),
           "the goals do not cover exactly the acceptance numbers 1 to 3")
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3, 4], "check": "`x`", "files": []}]),
           "the goals do not cover exactly the acceptance numbers 1 to 3")
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3],
                        "check": "see the docs", "files": []}]),
           "goal g1 has no command in backticks")
    # The other half of that rule: a `path:N` the checkout does not have.
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3],
                        "check": "src/gone.txt:3", "files": []}]),
           "goal g1 has no command in backticks")
    # A backticked span holds something: an empty pair is no command.
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3],
                        "check": "run `` first", "files": []}]),
           "goal g1 has no command in backticks")
    reject(plan(context="## Files and symbols\n\n- src/gone.txt:3\n"),
           "context cites src/gone.txt, which is not a file in this checkout")
    reject(plan(context="## Files and symbols\n\n- test/a.txt:9\n"),
           "context cites test/a.txt:9, and test/a.txt has only 2 lines")
    # A range is held to its last line.
    reject(plan(context="## Files and symbols\n\n- test/a.txt:1-9\n"),
           "context cites test/a.txt:9, and test/a.txt has only 2 lines")
    reject(plan(edge_cases=[{"id": "e%d" % i, "case": "c", "expected": "e", "covers": 1}
                            for i in range(1, 8)]),
           "the plan has 7 edge cases and at most six fit")
    reject(plan(edge_cases=[{"id": "e1", "case": "c", "expected": "e", "covers": 9}]),
           "an edge case names an acceptance number the Task does not have")
    reject(plan(goals=[]), "the plan has 0 goals and must have one to four")
    reject(plan(goals=[{"id": "g%d" % i, "goal": "x", "covers": [1], "check": "`x`", "files": []}
                       for i in range(1, 6)]),
           "the plan has 5 goals and must have one to four")
    reject(plan(goals="the goals"), "goals must be a list of one to four goals")
    reject(plan(edge_cases="none"), "edge_cases must be a list, empty when the Task has no case")
    reject(plan(edge_cases=[{"id": "e1", "case": "c", "expected": "e", "covers": 1},
                            {"id": "e1", "case": "d", "expected": "f", "covers": 2}]),
           "two edge cases share an id")

    # The receipt: another stage's, a visit that is not this one, and no result at all.
    reject(plan(_io={"stage": "task_plan_fb"}), "the plan carries no valid Input receipt")
    reject(plan(), "the plan carries no valid Input receipt", visit="other")

    # Twice each: the second rejection is the fallback plan, whoever's receipt it was.
    setup()
    put(plan(_io={"stage": "task_plan_fb"}))
    ck("another stage's receipt is a retry", "retry", go()["task_plan_route"])
    put(plan(_io={"stage": "task_plan_fb"}))
    ck("another stage's receipt twice is a fallback plan", "fallback", go()["task_plan_type"])
    setup()
    put(None)
    ck("a missing plan is a retry", "retry", go()["task_plan_route"])
    put(None)
    ck("a missing plan twice is a fallback plan", "fallback", go()["task_plan_type"])
    ck("the fallback plan covers a Task it never read", "[[1],[2],[3]]",
       jq1("[.goals[].covers]", T / "task_plans/t1.json"))

    # A Task that fixes a finding must name the class of the bug, whoever's finding it is.
    for source in ("refute", "review", "local_review"):
        setup()
        write(T / "current_task.json", json.dumps(dict(task, source=source)) + "\n")
        put(plan())
        ck("a %s fix Task without a bug_class is a retry" % source, "retry", go()["task_plan_route"])
        ck("a %s fix Task has its own sentence" % source, "1",
           count("^this Task fixes a %s finding, so bug_class" % source, file=T / "task_plan_error.txt"))
    setup()
    write(T / "current_task.json", json.dumps(dict(task, source="refute")) + "\n")
    put(plan(bug_class="any handler that trusts its argument"))
    ck("a fix Task with a bug_class goes on", "go", go()["task_plan_route"])

    # A rejected plan, then a good one: the reason goes with the rejection (C2 step 2), so no
    # later planner is handed a stale `error.txt`.
    setup()
    put(plan(context="x" * 14001))
    ck("a plan over the size is rejected first", "retry", go()["task_plan_route"])
    ck("and its reason is on disk", "present", present(T / "task_plan_error.txt"))
    put(plan())
    ck("the next plan goes on", "go", go()["task_plan_route"])
    ck("a valid plan deletes the rejection's reason", "absent", present(T / "task_plan_error.txt"))

    # The second rejection: the fallback plan, for 1, 3, 4 and 6 acceptance statements: one
    # goal a statement up to three, the fourth holding every one after it, joined by `; `.
    texts = {1: '["a1"]', 3: '["a1","a2","a3"]', 4: '["a1","a2","a3","a4"]',
             6: '["a1","a2","a3","a4; a5; a6"]'}
    for n, covers, goals in ((1, "[[1]]", 1), (3, "[[1],[2],[3]]", 3),
                             (4, "[[1],[2],[3],[4]]", 4), (6, "[[1],[2],[3],[4,5,6]]", 4)):
        setup()
        write(T / "current_task.json",
              json.dumps(dict(task, acceptance=["a%d" % i for i in range(1, n + 1)])) + "\n")
        put(plan(context="x" * 14001))
        ck("the first rejection is a retry (%d statements)" % n, "retry", go()["task_plan_route"])
        put(plan(context="x" * 14001))
        pub = go()
        ck("the second rejection goes on (%d statements)" % n, "go", pub["task_plan_route"])
        ck("the fallback plan says so (%d statements)" % n, "fallback", pub["task_plan_type"])
        ck("the fallback covers every statement (%d statements)" % n, covers,
           jq1("[.goals[].covers]", T / "task_plans/t1.json"))
        ck("the fallback has one goal a statement up to four (%d statements)" % n, str(goals),
           jq(".goals | length", file=T / "task_plans/t1.json"))
        ck("the fallback names no edge case (%d statements)" % n, "0",
           jq(".edge_cases | length", file=T / "task_plans/t1.json"))
        ck("the fallback quotes each statement (%d statements)" % n, texts[n],
           jq1("[.goals[].goal]", T / "task_plans/t1.json"))
        ck("each fallback goal checks nothing and touches the Task's files (%d statements)" % n,
           "true", jq('[.goals[] | (.check == "(none: fallback plan)" and .files == ["src/a.txt"])] | all',
                       file=T / "task_plans/t1.json"))
        ck("the fallback carries no bug class and no risk (%d statements)" % n, "true",
           jq('.bug_class == "" and .risks == [] and .goals_done == []', file=T / "task_plans/t1.json"))
        ck("the fallback dossier is the Task's own (%d statements)" % n, "1",
           count("Do the thing", file=T / "task-context.md"))
        ck("the Task is marked a fallback plan (%d statements)" % n, "fallback",
           jq(".[0].plan_type", file=T / "tasks.json"))
        ck("a fallback plan clears the error file (%d statements)" % n, "absent",
           present(T / "task_plan_error.txt"))


def test_pick_task_step(ck, tmp_path):
    """C1 `pick` step 5: `task_step` is `plan` exactly for a pre-PR Task with no stored plan,
    and `none` whenever the loop is not working one."""
    RP = tmp_path / "pick-step"
    T = RP / "sb"
    T.mkdir(parents=True)
    pick = stage(ck, "pick", T, graph=RAPH2, label="pick (ralph-v2)")
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    write(T / "phase", "pre\n")
    write(T / "iterations", "0\n")

    def go():
        r = sh(pick, cwd=repo)
        ck("pick: exits 0", "0", r.rc)
        no_braces(ck, "pick", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("pick: the six keys on every path", PICK_KEYS, ",".join(sorted(pub)))
        return pub

    def tasks(t):
        write(T / "tasks.json", t)

    tasks(task_list(one_task(context="## The Task\n\nDo the thing.\n")))
    ck("a Task with no plan is planned", "plan", go()["task_step"])
    ck("the plan step still writes the Task's own context", "1",
       count("Do the thing", file=T / "task-context.md"))

    write(T / "task_plans/t1.json", json.dumps(dict(PLAN, goals_done=[], plan_type="planned")) + "\n")
    ck("a Task with a stored plan goes to work", "work", go()["task_step"])
    ck("the stored plan renders the dossier", "1", count("^## Goals$", file=T / "task-context.md"))
    ck("the render keeps the plan's context", "1", count("## Files and symbols", file=T / "task-context.md"))

    for source in ("ci", "final_fix"):
        rm(T / "task_plans/t1.json")
        tasks(task_list(one_task(source=source)))
        ck("a %s Task never plans" % source, "work", go()["task_step"])

    rm(T / "task_plans/t1.json")
    write(T / "phase", "post\n")
    write(T / "post_iterations", "0\n")
    # A Task of the planned kind, picked in phase `post`: the phase alone keeps it from planning
    # (its `ci` and `final_fix` siblings are kept out by their source above).
    tasks(task_list(one_task()))
    ck("a post Task never plans", "work", go()["task_step"])

    write(T / "phase", "pre\n")
    tasks(task_list(one_task(status="passed")))
    pub = go()
    ck("a finished loop has no step", "none", pub["task_step"])
    ck("a finished loop is done", "done", pub["loop"])

    # A mainline conflict still reaches `resolve_merge`, with no step: it resolves the merge,
    # it does not plan or work a Task.
    write(T / "phase", "pre\n")
    git("-C", repo, "checkout", "-q", "-b", "other")
    commit_all(repo, {"src/a.txt": "remote\n"}, "remote")
    git("-C", repo, "push", "-q", "origin", "other:main")
    git("-C", repo, "checkout", "-q", "main")
    commit_all(repo, {"src/a.txt": "local\n"}, "local")
    rm(T / "current_task.json", T / "task_base_sha")
    tasks(task_list(one_task()))
    pub = go()
    ck("a conflict is a rebase", "True", str(pub["rebase_failed"]))
    ck("a conflict has no task step", "none", pub["task_step"])
    ck("a conflict still names the Task to resolve_merge", "t1",
       jq(".id", file=T / "current_task.json"))
    ck("a conflict writes no rung", "absent", present(T / "rung"))
    ck("a conflict writes no task_base_sha", "absent", present(T / "task_base_sha"))
    ck("a conflict leaves a clean tree", "", git("-C", repo, "status", "--porcelain"))


def test_the_task_dossier_render(ck, tmp_path):
    """C2's render, from `pick`: goals open and done, edge cases, the class of the bug, no
    empty heading, and the same jq program `task_plan_gate` runs."""
    RP = tmp_path / "render"
    T = RP / "sb"
    T.mkdir(parents=True)
    pick = stage(ck, "pick", T, graph=RAPH2, label="pick (ralph-v2)")
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    write(T / "phase", "pre\n")
    write(T / "iterations", "0\n")
    D = T / "task-context.md"

    write(T / "tasks.json", task_list(one_task()))
    plan_full = dict(PLAN, plan_type="planned", goals_done=["g2"],
                     bug_class="any handler that trusts its argument",
                     risks=["the caller outside the diff"])
    write(T / "task_plans/t1.json", json.dumps(plan_full) + "\n")
    sh(pick, cwd=repo)
    ck("render: the dossier comes first", "1", count("^## Files and symbols$", file=D))
    ck("render: an open goal keeps its covers and check", "1",
       count("^- g1: Add the limit Covers: 1, 2. Check: `make check`$", file=D))
    ck("render: a done goal says so and nothing else", "1",
       count("^- g2 (done): Cover the empty case$", file=D))
    ck("render: edge cases are listed", "1",
       count("^- e1 (acceptance 1): an empty list -> no error$", file=D))
    ck("render: the class of the bug is its own section", "1", count("^## Class of the bug$", file=D))
    ck("render: risks are not in the dossier", "0", count("outside the diff", file=D))

    # No empty headings: a plan with no edge cases and no bug class has neither section.
    write(T / "task_plans/t1.json",
          json.dumps(dict(PLAN, plan_type="planned", goals_done=[], edge_cases=[], bug_class="")) + "\n")
    sh(pick, cwd=repo)
    ck("render: no edge cases means no heading", "0", count("## Edge cases to cover", file=D))
    ck("render: no bug class means no heading", "0", count("## Class of the bug", file=D))
    ck("render: the headings that remain are not empty", "1", count("^## Goals$", file=D))

    # `## Last attempt`: v1's, appended under the render.
    write(T / "tasks.json", task_list(one_task(misses=1)))
    write(T / "feedback/rework.md", "THE CHECK FAILED\n")
    sh(pick, cwd=repo)
    ck("render: a missed plan carries the last attempt", "1", count("^## Last attempt$", file=D))
    ck("render: the last attempt is quoted", "1", count("THE CHECK FAILED", file=D))

    # `## Review findings`: what the last round left, for the rework Iteration (C1 `pick` step
    # 5), after v1's `## Last attempt`: a heading per finding, its line only when it has one,
    # and its class only when it is set. The Task has missed twice and been reviewed once, so
    # the header's number is the reviews, not the misses.
    write(T / "tasks.json", task_list(one_task(misses=2, reviews=1)))
    write(T / "task_review/state/findings/t1.json", json.dumps([
        {"id": "t1v1f1", "severity": "error", "kind": "defect", "file": "src/a.txt", "line": 3,
         "message": "An empty input reaches the handler", "evidence": "`make check` fails",
         "class": "a handler that trusts its argument", "source": "refuter", "repeat_of": None},
        {"id": "t1v1s1", "severity": "warning", "kind": "spec", "file": "src/b.txt", "line": None,
         "message": "No test names the empty case", "evidence": "test/a.txt:2",
         "class": "", "source": "review", "repeat_of": None},
    ]) + "\n")
    sh(pick, cwd=repo)
    ck("render: the findings are their own section", "1", count("^## Review findings$", file=D))
    ck("render: the header names the attempt", "1",
       count(r"^Two reviewers found these after attempt 1\. Answer every id in `fixed` or `disputes`\.$",
             file=D))
    ck("render: a finding is a heading with its severity, kind, file and line", "1",
       count(r"^### t1v1f1: error, defect, src/a\.txt:3$", file=D))
    ck("render: a finding without a line has none", "1",
       count(r"^### t1v1s1: warning, spec, src/b\.txt$", file=D))
    ck("render: every finding shows its evidence", "2", count("^Evidence: ", file=D))
    ck("render: a finding's message is quoted", "1",
       count("^An empty input reaches the handler$", file=D))
    ck("render: a class is shown only when set", "1",
       count("^Class: a handler that trusts its argument$", file=D))
    ck("render: the findings come after the last attempt", "true",
       str(D.read_text().index("## Last attempt") < D.read_text().index("## Review findings")).lower())

    def render_program(node):
        for line in extract(RAPH2, node).split("\n"):
            if line.startswith("RENDER='") and line.endswith("'"):
                return line[len("RENDER='"):-1]
        raise LookupError("no RENDER program in " + node)

    ck("the render program is the same text in both nodes",
       render_program("pick"), render_program("task_plan_gate"))


def test_pick_rungs_and_the_cap(ck, tmp_path):
    """C1 `pick` step 4: E16's three rungs, the 6-miss block, and the cap a reviewed Task is
    exempt from (P9)."""
    RP = tmp_path / "pick-rungs"
    T = RP / "sb"
    T.mkdir(parents=True)
    pick = stage(ck, "pick", T, graph=RAPH2, label="pick (ralph-v2)")
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    write(T / "phase", "pre\n")

    def go(t, iterations="0"):
        write(T / "tasks.json", t)
        write(T / "iterations", iterations + "\n")
        r = sh(pick, cwd=repo)
        ck("pick: exits 0 (rungs)", "0", r.rc)
        no_braces(ck, "pick (rungs)", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("pick: the six keys on every path (rungs and cap)", PICK_KEYS, ",".join(sorted(pub)))
        return pub

    for misses, rung in ((0, "1"), (1, "1"), (2, "2"), (3, "2"), (4, "3"), (5, "3")):
        ck("%d misses is rung %s" % (misses, rung), rung, go(task_list(one_task(misses=misses)))["rung"])

    go(task_list(one_task(id="t1", misses=6), one_task(id="t2")))
    ck("6 misses moves on", "t2", jq(".id", file=T / "current_task.json"))
    ck("6 misses blocks the Task", "blocked", jq(".[0].status", file=T / "tasks.json"))
    ck("6 misses records the reason", "6 failed Iterations",
       jq(".[0].block_reason", file=T / "tasks.json"))

    # The cap: 3 x the list, so 3 open Tasks against 9 Iterations is exhausted.
    three = task_list(one_task(id="t1"), one_task(id="t2"), one_task(id="t3"))
    ck("the cap exhausts an unreviewed Task", "exhausted", go(three, "9")["loop"])
    ck("the cap records the exhaustion", "true", cat(T / "loop_exhausted"))
    ck("a reviewed Task is exempt from the cap", "work",
       go(task_list(one_task(id="t1", reviews=1), one_task(id="t2"), one_task(id="t3")), "9")["loop"])
    ck("the exempt Task is the one worked", "t1", jq(".id", file=T / "current_task.json"))


def test_record_writes_the_patches(ck, tmp_path):
    """C1 `record` step 2: every pre-PR Iteration of a Task leaves the diff it made, numbered,
    including a missed one and a scope block; nothing else does."""
    RP = tmp_path / "record-patch"
    T = RP / "sb"
    T.mkdir(parents=True)
    rec = stage(ck, "record", T, graph=RAPH2, label="record (ralph-v2)")
    repo = RP / "repo"
    repo.mkdir()
    plain_repo(repo)
    mkreadok(T, "v1")
    P1 = T / "task_review/state/patches/t1"
    done = json.dumps({"status": "done", "summary": "did it", "learnings": []})
    progress = json.dumps({"status": "progress", "summary": "half", "learnings": []})

    def setup(task=None, iteration="", phase="pre"):
        rm(T / "iteration.json", T / "check_ok", T / "task_block_scope.md",
           T / "feedback/rework.md", T / "progress.md", T / "task_review/state/patches/t1")
        for f, v in (("phase", phase), ("iterations", "0"), ("post_iterations", "0"),
                     ("rework_iterations", "0")):
            write(T / f, v + "\n")
        write(T / "tasks.json", task_list(task or one_task()))
        write(T / "current_task.json", json.dumps({"id": "t1", "title": "A"}) + "\n")
        write(T / "task_base_sha", git("-C", repo, "rev-parse", "HEAD") + "\n")
        if iteration:
            write(T / "iteration.json", iteration + "\n")
            set_visit(T / "iteration.json", "v1")
        write(T / "check_ok", "1\n")

    def go():
        r = sh(rec, cwd=repo)
        ck("record: exits 0", "0", r.rc)
        no_braces(ck, "record", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("record: the two keys on every path (patches)", RECORD_KEYS, ",".join(sorted(pub)))
        return pub

    def patches():
        return len(sorted(P1.glob("*.patch"))) if P1.is_dir() else 0

    setup(iteration=done)
    commit_all(repo, {"src/a.txt": "one two\n"}, "t1 work")
    ck("a passed Iteration is passed", "True", str(go()["iteration_passed"]))
    ck("a pass writes patch 001", "001.patch",
       ",".join(p.name for p in sorted(P1.glob("*.patch"))))
    ck("the patch is the diff since the task base", "1", count("one two", file=P1 / "001.patch"))

    setup(iteration=progress)
    commit_all(repo, {"src/a.txt": "one two three\n"}, "t1 again")
    ck("a miss is not passed", "False", str(go()["iteration_passed"]))
    ck("a missed Iteration writes its own patch", "001.patch",
       ",".join(p.name for p in sorted(P1.glob("*.patch"))))

    # One missed and one passed Iteration of the same Task: both patches, in order.
    rm(P1)
    setup(iteration=progress)
    commit_all(repo, {"src/a.txt": "one two three four\n"}, "again")
    go()
    commit_all(repo, {"src/a.txt": "one two three four five\n"}, "and again")
    write(T / "iteration.json", done + "\n")
    set_visit(T / "iteration.json", "v1")
    go()
    ck("a Task with a missed and a passed Iteration has both patches", "001.patch,002.patch",
       ",".join(p.name for p in sorted(P1.glob("*.patch"))))

    # An empty diff writes no file: the base is HEAD.
    rm(P1)
    setup(iteration=done)
    go()
    ck("an empty diff writes no patch", "0", str(patches()))

    # A failed diff (no base to diff against) writes no file and one stderr line, and the
    # Iteration is still recorded.
    rm(P1)
    setup(iteration=done)
    rm(T / "task_base_sha")
    r = sh(rec, cwd=repo)
    no_braces(ck, "record (failed diff)", r.out)
    ck("a failed diff writes no patch", "0", str(patches()))
    ck("a failed diff says so once", "1",
       count("record: no diff against the task base", r.out, flags="F"))
    ck("a failed diff still routes with both keys", "true",
       jq(".context_updates | has(\"iteration_passed\") and has(\"review_due\")", lastjson(r.out)))

    # A scope block is a pre-PR Iteration too.
    rm(P1)
    setup(iteration=done)
    write(T / "task_block_scope.md", "The Iteration blocked on task A: needs a workflow edit\n")
    commit_all(repo, {"src/a.txt": "blocked work\n"}, "block")
    pub = go()
    ck("a scope block is a miss", "False", str(pub["iteration_passed"]))
    ck("a scope block marks the Task blocked", "blocked", jq(".[0].status", file=T / "tasks.json"))
    ck("a scope block still records its work", "001.patch",
       ",".join(p.name for p in sorted(P1.glob("*.patch"))))

    # A post-PR Iteration is not a Task's work: no patch.
    rm(P1)
    setup(iteration=done, phase="post")
    commit_all(repo, {"src/a.txt": "post work\n"}, "post")
    go()
    ck("a post Iteration writes no patch", "0", str(patches()))

    # `--no-renames` (C1 step 2): a file the Iteration moved reaches the reviewers as a
    # deletion and an addition, both bodies whole, never as one rename line.
    rm(P1)
    setup(iteration=done)
    git("-C", repo, "mv", "src/a.txt", "src/moved.txt")
    git("-C", repo, "commit", "-qm", "moved")
    go()
    ck("a moved file is no rename line", "0", count("rename from", file=P1 / "001.patch", flags="F"))
    ck("a moved file is a deletion and an addition", "2",
       count("^(deleted|new) file mode", file=P1 / "001.patch", flags="E"))

    # `PRE` is the phase and the Task's source (C1 step 1): a `ci` or `final_fix` Task leaves
    # no patch in phase `pre` either, and its Iteration still counts against the cap (step 4).
    for source in ("ci", "final_fix"):
        rm(P1)
        setup(task=one_task(source=source), iteration=done)
        commit_all(repo, {"src/a.txt": "a %s fix\n" % source}, source)
        ck("a %s Task passes" % source, "True", str(go()["iteration_passed"]))
        ck("a %s Task in phase pre writes no patch" % source, "0", str(patches()))
        ck("a %s Task in phase pre counts against the cap" % source, "1", cat(T / "iterations"))

    # Step 5 opens on the same `PRE`: an Iteration that is not a Task's pre-PR work copies no
    # `goals_done` into a stored plan.
    rm(P1)
    setup(task=one_task(source="ci"),
           iteration=json.dumps({"status": "done", "summary": "did it", "learnings": [],
                                 "goals_done": ["g2"]}))
    write(T / "task_plans/t1.json",
          json.dumps(dict(PLAN, goals_done=[], plan_type="planned")) + "\n")
    go()
    ck("a ci Task's Iteration copies no goals_done", "[]", jq1(".goals_done", T / "task_plans/t1.json"))

    # C5 step 8 leaves `reviewed` beside a Task's patches. The count is of patches (C1 step 2),
    # so the rework Iteration's patch follows the last patch, not the last file.
    rm(P1)
    setup(iteration=done)
    write(T / "task_review/state/patches/t1/001.patch", "one\n")
    write(T / "task_review/state/patches/t1/002.patch", "two\n")
    write(T / "task_review/state/patches/t1/reviewed", "2\n")
    commit_all(repo, {"src/a.txt": "one two three four five six\n"}, "rework")
    go()
    ck("the next patch is numbered past the patches, not past the files", "001.patch,002.patch,003.patch",
       ",".join(p.name for p in sorted(P1.glob("*.patch"))))


def test_record_counts_and_answers(ck, tmp_path):
    """C1 `record` steps 4-6: the counter a rework Iteration goes to, `goals_done`, the
    answers a review reads back, and the two keys it publishes."""
    RP = tmp_path / "record-count"
    T = RP / "sb"
    T.mkdir(parents=True)
    rec = stage(ck, "record", T, graph=RAPH2, label="record (ralph-v2)")
    repo = RP / "repo"
    repo.mkdir()
    plain_repo(repo)
    mkreadok(T, "v1")
    A = T / "task_review/state/answers/t1.json"

    def setup(task, iteration):
        rm(T / "iteration.json", T / "check_ok", T / "task_block_scope.md", T / "progress.md",
           A)
        for f, v in (("phase", "pre"), ("iterations", "0"), ("post_iterations", "0"),
                     ("rework_iterations", "0")):
            write(T / f, v + "\n")
        write(T / "tasks.json", task_list(task))
        write(T / "current_task.json", json.dumps({"id": "t1", "title": "A"}) + "\n")
        write(T / "task_base_sha", git("-C", repo, "rev-parse", "HEAD") + "\n")
        write(T / "task_plans/t1.json",
              json.dumps(dict(PLAN, goals_done=["g1"], plan_type="planned")) + "\n")
        write(T / "iteration.json", json.dumps(iteration) + "\n")
        set_visit(T / "iteration.json", "v1")
        write(T / "check_ok", "1\n")

    def go():
        r = sh(rec, cwd=repo)
        ck("record: exits 0 (counts)", "0", r.rc)
        no_braces(ck, "record", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("record: the two keys on every path (counts)", RECORD_KEYS, ",".join(sorted(pub)))
        return pub

    done = {"status": "done", "summary": "did it", "learnings": []}

    # An unreviewed Task that answers findings anyway: the answers wait for a review (step 5).
    setup(one_task(), dict(done, goals_done=["g2"], fixed=["t1v1f1"], disputes=[
        {"finding_id": "t1v1s1", "reason": "the check covers it", "evidence": "test/a.txt:2"}]))
    pub = go()
    ck("record: iteration_passed on every path", "True", str(pub["iteration_passed"]))
    # Task 08 (C1 step 3): a pre-PR pass is `reviewing`, not `passed`, and the round is due.
    ck("record: a pre-PR pass is due a review", "True", str(pub["review_due"]))
    ck("record: a pre-PR pass leaves the Task in review", "reviewing",
       jq(".[0].status", file=T / "tasks.json"))
    ck("an unreviewed Iteration spends the cap", "1", cat(T / "iterations"))
    ck("an unreviewed Iteration spends no rework", "0", cat(T / "rework_iterations"))
    ck("the progress line numbers the counter it spent", "1",
       count("^## Iteration 1: t1 awaiting review (rung 0)$", file=T / "progress.md"))
    ck("goals_done is unioned into the plan", '["g1","g2"]',
       jq1("[.goals_done[]]", T / "task_plans/t1.json"))
    ck("no review means no answers file", "absent", present(A))
    # The union is a set: the same goal reported again is still one entry.
    go()
    ck("a goal reported twice is done once", '["g1","g2"]',
       jq1("[.goals_done[]]", T / "task_plans/t1.json"))

    # Nothing is taken from a result without a valid receipt (C1 step 5), reviewed or not.
    setup(one_task(reviews=1), dict(done, goals_done=["g2"], fixed=["t1v1f1"]))
    set_visit(T / "iteration.json", "v0")
    go()
    ck("a stale receipt copies no goals_done", '["g1"]',
       jq1("[.goals_done[]]", T / "task_plans/t1.json"))
    ck("a stale receipt writes no answers", "absent", present(A))

    setup(one_task(reviews=1), dict(done, goals_done=["g2"]))
    go()
    ck("a rework Iteration spends no cap", "0", cat(T / "iterations"))
    ck("a rework Iteration counts", "1", cat(T / "rework_iterations"))

    answers = dict(done, fixed=["t1v1f1"], disputes=[
        {"finding_id": "t1v1s1", "reason": "the check covers it", "evidence": "test/a.txt:2"},
        {"finding_id": "t1v1s2", "reason": "wrong", "evidence": "i looked at it"},
        {"finding_id": "t1v1f2", "reason": "it is not a defect", "evidence": "`make check`"},
        # C5's evidence rule is a non-empty span in backticks: an empty pair is no evidence.
        {"finding_id": "t1v1s3", "reason": "it passes", "evidence": "``"},
        {"finding_id": "t1v1s4", "reason": "it passes", "evidence": "run `` first"},
    ])
    setup(one_task(reviews=1), answers)
    go()
    ck("a fixed finding is answered", "fixed", jq(".[\"t1v1f1\"].answer", file=A))
    ck("a dispute with a path is answered", "disputed", jq(".[\"t1v1s1\"].answer", file=A))
    ck("the dispute keeps its evidence", "test/a.txt:2", jq(".[\"t1v1s1\"].evidence", file=A))
    ck("a dispute without evidence is not written", "null", jq(".[\"t1v1s2\"]", file=A))
    ck("a dispute with only empty backticks is not written", "null", jq(".[\"t1v1s3\"]", file=A))
    ck("a dispute whose only span is empty is not written", "null", jq(".[\"t1v1s4\"]", file=A))
    ck("an id in both lists is disputed", "disputed", jq(".[\"t1v1f2\"].answer", file=A))

    # A second round merges over what is there rather than replacing it.
    setup(one_task(reviews=2), dict(done, fixed=["t1v2f1"]))
    write(A, json.dumps({"t1v1f1": {"answer": "fixed"}}) + "\n")
    go()
    ck("answers merge over the earlier round", "fixed", jq(".[\"t1v1f1\"].answer", file=A))
    ck("and the new round is added", "fixed", jq(".[\"t1v2f1\"].answer", file=A))

    # A post-PR Iteration counts there and nowhere else.
    setup(one_task(source="ci"), done)
    write(T / "phase", "post\n")
    go()
    ck("a post Iteration counts", "1", cat(T / "post_iterations"))
    ck("a post Iteration spends no cap", "0", cat(T / "iterations"))


def test_the_task_loop_in_order(ck, tmp_path):
    """The order the edges run, with the real scripts: `pick` -> `task_plan_gate` ->
    `excerpts` -> `task_block_gate` -> `record` -> `pick`. Each node reads what the one
    before it wrote, so a contract drift between any two of them fails here."""
    RP = tmp_path / "loop"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    mkreadok(T, "v1")
    for f, v in (("phase", "pre"), ("iterations", "0"), ("post_iterations", "0"),
                 ("rework_iterations", "0")):
        write(T / f, v + "\n")
    write(T / "tasks.json",
          task_list(one_task(id="t1", context="## Task one\n",
                            acceptance=["a1", "a2", "a3"]), one_task(id="t2")))
    nodes = {n: stage(ck, n, T, graph=RAPH2, label=n + " (ralph-v2)")
             for n in ("pick", "task_plan_gate", "excerpts", "task_block_gate", "record")}

    def run_node(node, cwd):
        r = sh(nodes[node], cwd=cwd)
        ck(node + ": exits 0", "0", r.rc)
        no_braces(ck, node, r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        if node == "pick":
            ck("pick: the six keys on every path (in order)", PICK_KEYS, ",".join(sorted(pub)))
        elif node == "record":
            ck("record: the two keys on every path (in order)", RECORD_KEYS, ",".join(sorted(pub)))
        return pub

    pub = run_node("pick", repo)
    ck("the loop plans first", "plan", pub["task_step"])
    ck("the loop picked the first Task", "t1", jq(".id", file=T / "current_task.json"))

    write(T / "task_plan.json", json.dumps(plan()) + "\n")
    set_visit(T / "task_plan.json", "v1")
    pub = run_node("task_plan_gate", repo)
    ck("the gate routes the plan on", "go", pub["task_plan_route"])
    ck("the gate renders the dossier the coder reads", "1",
       count("^- g1: Add the limit", file=T / "task-context.md"))

    # `excerpts` publishes nothing: it only writes the file the coder reads.
    r = sh(nodes["excerpts"], cwd=repo)
    ck("excerpts: exits 0", "0", r.rc)
    no_braces(ck, "excerpts", r.out)
    ck("excerpts copies the code the dossier cites", "1",
       count("^## src/a.txt:1-1$", file=T / "task-code.md"))

    ck("no result is no block", "none", run_node("task_block_gate", repo)["task_block"])

    commit_all(repo, {"src/a.txt": "one two\n"}, "t1 work")
    # The Iteration's io-stage writes `.io/stage.json` again, at its own visit: the planner's
    # `v1` is over. `record` compares `iteration.json` to the visit that is current after the
    # Iteration, so a result still stamped with the planner's visit is no pass, and the same
    # result at the Iteration's own visit is.
    mkreadok(T, "v2")
    result = {"status": "done", "summary": "did it", "learnings": [], "goals_done": ["g1", "g2"]}
    write(T / "iteration.json", json.dumps(result) + "\n")
    set_visit(T / "iteration.json", "v1")
    write(T / "check_ok", "1\n")
    ck("a result at the planner's visit is not passed", "False",
       str(run_node("record", repo)["iteration_passed"]))
    write(T / "iteration.json", json.dumps(result) + "\n")
    set_visit(T / "iteration.json", "v2")
    pub = run_node("record", repo)
    ck("the Iteration passes mechanically", "True", str(pub["iteration_passed"]))
    ck("the Task waits for its review", "True", str(pub["review_due"]))
    ck("the Task is in review, not passed", "reviewing", jq(".[0].status", file=T / "tasks.json"))
    ck("each of its two Iterations left a patch", "001.patch,002.patch",
       ",".join(p.name for p in sorted((T / "task_review/state/patches/t1").glob("*.patch"))))
    ck("every goal it reported is done", '["g1","g2"]',
       jq1("[.goals_done[]]", T / "task_plans/t1.json"))

    # The plan contract files are cleared for the next Task, a rejection's reason with them:
    # a gate that crashed after writing it must not hand the next planner another Task's error.
    # The loop's own review order is `test_the_task_review_loop_in_order`; here the Task is
    # marked passed the way `task_review_gate` marks it, so the loop can take the next Task.
    write(T / "tasks.json", jq("map(if .id == \"t1\" then .status = \"passed\" else . end)",
                               file=T / "tasks.json", raw=False) + "\n")
    write(T / "task_plan_error.txt", "the plan carries no valid Input receipt, so submit it rather than write it")
    pub = run_node("pick", repo)
    ck("the loop takes the next unplanned Task", "plan", pub["task_step"])
    ck("the loop took task two", "t2", jq(".id", file=T / "current_task.json"))
    ck("the passed Task's plan is still there", "1",
       str(int((T / "task_plans/t1.json").is_file())))
    ck("the plan contract files are cleared for the next Task", "absent",
       present(T / "task_plan.json"))
    ck("the rejection's reason is cleared with them", "absent", present(T / "task_plan_error.txt"))
    ck("the tries counter is cleared", "absent", present(T / "task_plan_tries"))


def test_the_task_loop_edges(ck):
    """The edges C2 and C1 need: `pick` routes on `task_step`, the planner retries through
    its gate, and only the gate reaches `excerpts` without a condition."""
    e = edges(RAPH2)
    ck("the graph has edges to check", "true", str(bool(e)).lower())
    ck("a Task with no plan is planned", "1",
       count("pick->task_plan context.loop=work && context.rebase_failed=false && context.task_step=plan",
             e, flags="xF"))
    ck("a Task with a plan is worked", "1",
       count("pick->excerpts context.loop=work && context.rebase_failed=false && context.task_step=work",
             e, flags="xF"))
    ck("the planner is checked by its gate", "2", count("^task_plan->task_plan_gate", e))
    ck("the planner has one unconditional edge", "1", count("^task_plan->task_plan_gate $", e))
    ck("a rejected plan is retried", "1",
       count("task_plan_gate->task_plan outcome=succeeded && context.task_plan_route=retry",
             e, flags="xF"))
    ck("the gate reaches the coder unconditionally", "1", count("^task_plan_gate->excerpts $", e))
    rp = "\n".join(l for l in e.split("\n") if l.startswith("pick->"))
    ck("pick keeps exactly one unconditional edge", "1", count(" $", rp))
    ck("pick's unconditional edge is the escape", "1", count("pick->stop_reason ", rp, flags="xF"))


# ---------------------------------------------------------------------------
# Task 08 (C3, C4, C5): the Task review loop.
#
# The two check nodes own every retry, so C4's table is walked one row at a time; `prep` builds
# what the reviewers read; `task_review_gate` is the only writer of a Task's status from the
# loop, so its candidates, disputes, repeats and verdicts are pinned here; then the loop runs in
# the order the edges run it, with the real scripts.
# ---------------------------------------------------------------------------

REVIEW_TASK = {"id": "t1", "title": "A", "body": "do it", "source": "plan",
               "acceptance": ["a1", "a2"]}

REVIEW_PLAN = {
    "goals": [{"id": "g1", "goal": "Add the limit", "covers": [1, 2], "check": "`make check`"}],
    "edge_cases": [{"id": "e1", "case": "an empty input", "expected": "no crash", "covers": 1}],
    "bug_class": "", "risks": [], "goals_done": [], "plan_type": "planned",
}


def a_finding(**over) -> dict:
    """A finding that passes C5's evidence rule in the fixture repository."""
    f = {"severity": "error", "kind": "defect", "file": "src/a.txt", "line": 1,
         "message": "An empty input reaches the handler",
         "evidence": "`make check` fails", "class": "", "repeat_of": None}
    f.update(over)
    return f


def refuted(findings=(), edge_cases=None, responses=(), stage="task_refute") -> dict:
    return {"_io": {"stage": stage, "visit": "v1"}, "summary": "s",
            "edge_cases": [{"id": "e1", "covered": True, "evidence": "test/a.txt:1"}]
            if edge_cases is None else list(edge_cases),
            "findings": list(findings), "responses": list(responses)}


def reviewed(findings=(), acceptance=None, responses=(), stage="task_review") -> dict:
    return {"_io": {"stage": stage, "visit": "v2"}, "summary": "s",
            "acceptance": [{"index": 1, "met": True, "evidence": "src/a.txt:1"},
                           {"index": 2, "met": True, "evidence": "test/a.txt:1"}]
            if acceptance is None else list(acceptance),
            "findings": list(findings), "responses": list(responses)}


def tool_path(d: Path, **stubs: str) -> str:
    """A PATH of symlinks to the tools a node shells out to, plus the stubs given.

    A tool left out is a tool the node cannot find: that is what "no curl" means here.
    """
    d.mkdir(parents=True, exist_ok=True)
    for name in ("sh", "jq", "cat", "mkdir", "rm", "sed", "grep", "sort", "wc", "tr", "ls", "head"):
        src = shutil.which(name, path=ORIG_PATH)
        if src and not (d / name).exists():
            (d / name).symlink_to(src)
    for name, body in stubs.items():
        stub(d, name, body)
    return str(d)


def check_node(ck, tmp_path, node: str, k: str) -> None:
    """C4's table for `node`: a valid result, a stale one, a spent `stage-end`, the fallback's
    own receipt, the three tries, the health wait and the miss after it."""
    RP = tmp_path / f"check-{k}"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo = RP / "repo"
    repo.mkdir()
    plain_repo(repo)
    script = stage(ck, f"{node}_check", T, graph=RAPH2, label=f"{node}_check")
    OUT = T / f"task_review/out/{k}.json"
    ST = T / "task_review/state"
    NXT, TRY = f"{node}_next", f"{node}_tries"

    def stage_json(name: str, visit: str) -> None:
        write(T / ".io/stage.json", json.dumps({"workflow": "ralph-v2", "node": name,
                                                "visit": visit}) + "\n")

    def result(name: str, visit: str) -> None:
        write(OUT, json.dumps({"_io": {"stage": name, "visit": visit}, "summary": "s"}) + "\n")

    def go(path=None, env=None):
        r = sh(script, cwd=repo, path=path, env=env)
        ck(f"{node}_check: exits 0", "0", r.rc)
        no_braces(ck, f"{node}_check", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck(f"{node}_check: both keys on every path", f"{NXT},{TRY}", ",".join(sorted(pub)))
        return pub

    def reset() -> None:
        rm(OUT, ST / f"{k}.served", ST / f"{k}.fb", ST / f"{k}.tries", T / "box")
        rm(T / ".io/stage-end")
        ST.mkdir(parents=True, exist_ok=True)

    reset()
    stage_json(node, "v1")
    result(node, "v1")
    pub = go()
    ck("a valid box result is served", "box", cat(ST / f"{k}.served"))
    ck("a valid box result goes on", "done", pub[NXT])
    ck("a served result is left for the gate", "present", present(OUT))

    reset()
    stage_json(f"{node}_fb", "v3")
    result(f"{node}_fb", "v3")
    pub = go()
    ck("the fallback's own receipt is served", "fallback", cat(ST / f"{k}.served"))
    ck("the fallback's own receipt goes on", "done", pub[NXT])

    # A result an earlier visit left: not this round's, so it counts as no result at all.
    reset()
    stage_json(node, "v2")
    result(node, "v1")
    pub = go()
    ck("a stale result is no result", "retry", pub[NXT])
    ck("a stale result counts one box try", "1", pub[TRY])
    ck("a stale result is deleted", "absent", present(OUT))
    ck("a stale result is not served", "absent", present(ST / f"{k}.served"))

    # A timed-out session still submits: the visit matches, and `stage-end` never marked it as
    # succeeded. Row 1 is judged before the `stage-end` row, so this is a result.
    reset()
    stage_json(node, "v4")
    result(node, "v4")
    write(T / f".io/stage-end/{node}.succeeded", "v1\n")
    pub = go()
    ck("a result a timed-out session submitted is a result", "done", pub[NXT])
    ck("and it is served", "box", cat(ST / f"{k}.served"))

    # `stage-end` spent its two re-runs on this visit: a result still missing goes to the
    # fallback without spending a try (A3: counting both would allow nine sessions).
    reset()
    stage_json(node, "v5")
    result(node, "v1")
    write(T / f".io/stage-end/{node}.succeeded", "v5\n")
    pub = go()
    ck("a spent stage-end goes to the fallback", "fallback", pub[NXT])
    ck("a spent stage-end counts no try", "0", pub[TRY])
    ck("a spent stage-end creates the fallback marker", "present", present(ST / f"{k}.fb"))
    ck("a spent stage-end deletes the stale result", "absent", present(OUT))

    # The fallback ran and left nothing valid: the round is a miss, not another session.
    reset()
    stage_json(f"{node}_fb", "v6")
    result(f"{node}_fb", "v1")
    write(ST / f"{k}.fb", "\n")
    pub = go()
    ck("an invalid fallback result is a miss", "missed", cat(ST / f"{k}.served"))
    ck("a miss goes on", "done", pub[NXT])
    ck("a miss deletes the result", "absent", present(OUT))

    # Three box tries: one result each, and the third is the fallback.
    reset()
    write(T / "box", "coders-b\n")
    env = {"FABRO_BOX_HEALTH": "coders-a=http://127.0.0.1:1/h,coders-b=http://127.0.0.1:1/h"}
    for i, (want_next, want_tries) in enumerate((("retry", "1"), ("retry", "2"),
                                                 ("fallback", "3")), start=1):
        stage_json(node, f"t{i}")
        result(node, "earlier")
        # No curl on this PATH: the check must not wait for a box it cannot ask about.
        pub = go(path=tool_path(RP / f"bin{i}"), env=env)
        ck(f"try {i} is {want_next}", want_next, pub[NXT])
        ck(f"try {i} counts {want_tries}", want_tries, pub[TRY])
    ck("the third try creates the fallback marker", "present", present(ST / f"{k}.fb"))

    # The wait, when the box can be asked: no pool, no URL or no pair means no wait; a box that
    # never answers within the 40 polls is the fallback.
    for label, box, health, why in (
        ("no pool", None, "coders-b=http://127.0.0.1:1/h", "retry"),
        ("no health variable", "coders-b", "", "retry"),
        ("no pair for the pool", "coders-z", "coders-b=http://127.0.0.1:1/h", "retry"),
    ):
        reset()
        if box:
            write(T / "box", box + "\n")
        stage_json(node, "w1")
        result(node, "earlier")
        pub = go(path=tool_path(RP / "bin-ok", curl="#!/bin/sh\necho 200\n",
                                sleep="#!/bin/sh\nexit 0"), env={"FABRO_BOX_HEALTH": health})
        ck(f"{label}: {why}", why, pub[NXT])

    reset()
    write(T / "box", "coders-b\n")
    stage_json(node, "w2")
    result(node, "earlier")
    polls = RP / "polls"
    pub = go(path=tool_path(RP / "bin-bad",
                            curl="#!/bin/sh\necho x >> " + str(polls) + "\necho 000\n",
                            sleep="#!/bin/sh\nexit 0"),
             env={"FABRO_BOX_HEALTH": "coders-b=http://127.0.0.1:1/h"})
    ck("a box that never answers is the fallback", "fallback", pub[NXT])
    ck("the wait polls 40 times", "40", count("x", file=polls))

    # A box that answers lets the retry through after the first poll.
    reset()
    write(T / "box", "coders-b\n")
    stage_json(node, "w3")
    result(node, "earlier")
    pub = go(path=tool_path(RP / "bin-good", curl="#!/bin/sh\necho 200\n",
                            sleep="#!/bin/sh\nexit 0"),
             env={"FABRO_BOX_HEALTH": "coders-x=http://127.0.0.1:1/h,coders-b=http://127.0.0.1:1/h"})
    ck("a healthy box is retried", "retry", pub[NXT])
    ck("a healthy box leaves no fallback marker", "absent", present(ST / f"{k}.fb"))


def test_task_refute_check(ck, tmp_path):
    check_node(ck, tmp_path, "task_refute", "refute")


def test_task_review_check(ck, tmp_path):
    check_node(ck, tmp_path, "task_review", "review")


def test_the_two_check_nodes_are_one_program(ck):
    """C4: one program under two names, three substitutions apart. The refuter's text, with
    those three names changed, must be the reviewer's: a later fix to one that misses the other
    is a divergence no test of either alone can see."""
    refute = extract(RAPH2, "task_refute_check")
    review = extract(RAPH2, "task_review_check")
    ck("task_refute_check is a real program", "true",
       str(len(refute.split("\n")) > 40).lower())
    same = (refute.replace("task_refute_next", "task_review_next")
                  .replace("task_refute", "task_review")
                  .replace("refute", "review"))
    differing = [str(i) for i, (a, b) in enumerate(zip(same.split("\n"), review.split("\n")))
                 if a != b]
    ck("task_review_check is task_refute_check with the three names changed", "true",
       "true" if same == review else "false at lines " + ",".join(differing[:6]))


def test_task_review_prep(ck, tmp_path):
    """C3: the reviewers' inputs. First review and second, the delta after a rework, no patch
    and an oversized one, and the history the coder's answers become."""
    RP = tmp_path / "prep"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo = RP / "repo"
    repo.mkdir()
    plain_repo(repo)
    prep = stage(ck, "task_review_prep", T, graph=RAPH2, label="task_review_prep")
    IN = T / "task_review/in"
    ST = T / "task_review/state"
    PD = ST / "patches/t1"

    def setup(reviews=0, patches=(), reviewed=None, findings=None, answers=None, task=None):
        rm(T / "task_review", T / "progress.md", T / "task_check.json")
        write(T / "current_task.json", json.dumps(dict(REVIEW_TASK, **(task or {}))) + "\n")
        write(T / "tasks.json",
              task_list(dict(REVIEW_TASK, status="reviewing", reviews=reviews, **(task or {}))))
        write(T / "task_plans/t1.json", json.dumps(REVIEW_PLAN) + "\n")
        PD.mkdir(parents=True, exist_ok=True)
        for name in patches:
            write(PD / name, "PATCH " + name + "\n")
        if reviewed is not None:
            write(PD / "reviewed", reviewed + "\n")
        if findings is not None:
            write(ST / "findings/t1.json", json.dumps(findings) + "\n")
        if answers is not None:
            write(ST / "answers/t1.json", json.dumps(answers) + "\n")

    def go():
        r = sh(prep, cwd=repo)
        ck("prep: exits 0", "0", r.rc)
        no_braces(ck, "prep", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("prep: it publishes task_review_go on every path", "task_review_go",
           ",".join(sorted(pub)))
        return pub

    # The first review: everything but the delta and the history, which do not exist yet.
    setup(patches=("001.patch",))
    ck("a patch goes to the reviewers", "True", str(go()["task_review_go"]))
    ck("task.patch is the Task's whole diff", "PATCH 001.patch", cat(IN / "task.patch"))
    ck("task.json is the Task's four fields", "acceptance,body,source,title",
       jq("keys | sort | join(\",\")", file=IN / "task.json"))
    ck("task.json carries its acceptance", "a1,a2",
       jq(".acceptance | join(\",\")", file=IN / "task.json"))
    ck("plan.json is the plan's goals, edge cases, bug class and risks",
       "bug_class,edge_cases,goals,risks", jq("keys | sort | join(\",\")", file=IN / "plan.json"))
    ck("plan.json's goals keep their id, goal, covers and check", "id,goal,covers,check",
       jq(".goals[0] | keys_unsorted | join(\",\")", file=IN / "plan.json"))
    ck("check.json says the mechanical gate passed, in full mode, with no target", "true,full,0",
       jq("(.passed | tostring) + \",\" + .mode + \",\" + ((.targets | length) | tostring)",
          file=IN / "check.json"))
    ck("the first review has no delta", "absent", present(IN / "delta.patch"))
    ck("the first review has no history", "absent", present(IN / "history.md"))

    # The second review: only the patches above `reviewed` are the delta, and the earlier
    # findings become the history the coder's answers are written into.
    setup(reviews=1, patches=("001.patch", "002.patch", "003.patch"), reviewed="1",
          findings=[a_finding(id="t1v1f1", source="refuter"),
                    a_finding(id="t1v1f2", source="refuter", severity="warning"),
                    a_finding(id="t1v1s1", source="review", kind="spec", file="test/a.txt")],
          answers={"t1v1f1": {"answer": "fixed"},
                   "t1v1f2": {"answer": "disputed", "reason": "the check covers it",
                              "evidence": "test/a.txt:2"}})
    go()
    ck("the second review's delta is only the new patches",
       "PATCH 002.patch\nPATCH 003.patch", cat(IN / "delta.patch"))
    ck("the second review still sends the whole diff",
       "PATCH 001.patch\nPATCH 002.patch\nPATCH 003.patch", cat(IN / "task.patch"))
    ck("history.md lists every earlier finding", "3", count("^- t1v1", file=IN / "history.md"))
    ck("history.md names the source of each finding", "1",
       count("^- t1v1s1 (error, spec, review) test/a.txt:", file=IN / "history.md"))
    ck("a fixed finding says fixed", "1", count("^  Coder: fixed$", file=IN / "history.md"))
    ck("a dispute keeps its reason and evidence", "1",
       count(r"^  Coder: \*\*disputed\*\*: the check covers it Evidence: test/a\.txt:2$",
             file=IN / "history.md"))
    ck("a finding the coder never answered says so", "1",
       count("^  Coder: no answer$", file=IN / "history.md"))

    # A rework that changed nothing adds no delta file at all: an empty one would read as "the
    # reviewer changed nothing" when the coder did.
    setup(reviews=1, patches=("001.patch",), reviewed="1", findings=[a_finding(id="t1v1f1")])
    go()
    ck("a rework with no new patch has no delta", "absent", present(IN / "delta.patch"))

    # No patch: nothing to review, and the Task passes with the reason (C3 step 3).
    setup()
    ck("no patch is no review", "False", str(go()["task_review_go"]))
    ck("no patch passes the Task", "passed", jq(".[0].status", file=T / "tasks.json"))
    ck("no patch records the reason", "no diff to review",
       jq(".[0].review_note", file=T / "tasks.json"))

    # Over 2,000,000 bytes: the same, with its own reason.
    setup()
    write(PD / "001.patch", "x" * 2_000_001)
    ck("an oversized diff is no review", "False", str(go()["task_review_go"]))
    ck("an oversized diff records its reason", "the diff is too large to review",
       jq(".[0].review_note", file=T / "tasks.json"))

    # Every visit clears the last round's inputs, results and check state: a stale file is the
    # one thing the receipt cannot catch, because the gate reads `served`, not a visit.
    setup(patches=("001.patch",))
    for path in (IN / "task.json", IN / "plan.json", IN / "delta.patch", IN / "history.md",
                 T / "task_review/out/refute.json", T / "task_review/out/review.json",
                 ST / "refute.served", ST / "refute.fb", ST / "refute.tries",
                 ST / "review.served", ST / "review.fb", ST / "review.tries"):
        write(path, "x\n")
    go()
    for path in (T / "task_review/out/refute.json", T / "task_review/out/review.json",
                 ST / "refute.served", ST / "refute.fb", ST / "refute.tries",
                 ST / "review.served", ST / "review.fb", ST / "review.tries",
                 IN / "delta.patch", IN / "history.md"):
        ck(f"a new round clears {path.name}", "absent", present(path))

    # The validated mode and targets of the mechanical gate go with the inputs.
    setup(patches=("001.patch",))
    write(T / "task_check.json", json.dumps({"mode": "scoped", "ok": True,
                                             "targets": [{"name": "bun"}, {"name": "vitest"}],
                                             "results": []}) + "\n")
    go()
    ck("check.json carries the mode and the target names", "scoped,bun,vitest",
       jq(".mode + \",\" + (.targets | join(\",\"))", file=IN / "check.json"))


GATE_KEYS = ("task_review_disputed,task_review_dropped,task_review_edges,"
             "task_review_edges_covered,task_review_errors,task_review_refute_served,"
             "task_review_review_served,task_review_round,task_review_source,"
             "task_review_task,task_review_verdict,task_review_warnings,"
             "task_review_withdrawn")


class ReviewCase:
    """One `task_review_gate` visit over a fixture sandbox: the two reviewers' results as the
    check nodes served them, the previous round's findings and the coder's answers."""

    def __init__(self, ck, tmp_path, name="gate"):
        self.ck = ck
        self.T = tmp_path / name / "sb"
        self.T.mkdir(parents=True)
        self.repo = tmp_path / name / "repo"
        self.repo.mkdir()
        plain_repo(self.repo)
        self.gate = stage(ck, "task_review_gate", self.T, graph=RAPH2,
                          label="task_review_gate")
        self.prep = stage(ck, "task_review_prep", self.T, graph=RAPH2,
                          label="task_review_prep")
        self.pub: dict = {}

    def run(self, refute=None, review=None, served=("box", "box"), reviews=0, task=None,
            findings=None, answers=None, dropped=None, patches=("001.patch",), plan=None):
        # What `task_review_prep` clears, and nothing else: the round records, the dropped list
        # and `progress.md` live for the run, which is what makes a later round a later round.
        T = self.T
        rm(T / "task_review/in", T / "task_review/out", T / "task_review/state/patches",
           T / "task_review/state/answers", T / "task_review/state/findings",
           T / "task_review/state/dropped", T / "task_review/state/recovers")
        for key in ("refute", "review"):
            rm(T / f"task_review/state/{key}.served", T / f"task_review/state/{key}.fb",
               T / f"task_review/state/{key}.tries")
        for d in ("in", "out", "state/patches/t1", "state/answers", "state/findings",
                  "state/dropped", "state/rounds", "state/recovers"):
            (T / "task_review" / d).mkdir(parents=True, exist_ok=True)
        for name in patches:
            write(T / "task_review/state/patches/t1" / name, "diff\n")
        t = dict(REVIEW_TASK, status="reviewing", iterations=1, misses=1, reviews=reviews)
        t.update(task or {})
        write(T / "current_task.json", json.dumps(t) + "\n")
        write(T / "tasks.json", task_list(t))
        write(T / "task_plans/t1.json", json.dumps(plan or REVIEW_PLAN) + "\n")
        for path, doc in ((T / "task_review/state/findings/t1.json", findings),
                          (T / "task_review/state/answers/t1.json", answers),
                          (T / "task_review/state/dropped/t1.json", dropped)):
            if doc is not None:
                write(path, json.dumps(doc) + "\n")
        # The real prep, in its place in the loop: it owns `in/` and it clears `out/`, which is
        # why the reviewers' results are written after it.
        r = sh(self.prep, cwd=self.repo)
        self.ck("prep: exits 0", "0", r.rc)
        self.ck("prep: there is a diff to review", "True",
                str(json.loads(lastjson(r.out))["context_updates"]["task_review_go"]))
        for key, value in zip(("refute", "review"), served):
            if value:
                write(T / f"task_review/state/{key}.served", value + "\n")
        for path, doc in ((T / "task_review/out/refute.json", refute),
                          (T / "task_review/out/review.json", review)):
            if doc is not None:
                write(path, json.dumps(doc) + "\n")
        r = sh(self.gate, cwd=self.repo)
        self.ck("gate: exits 0", "0", r.rc)
        no_braces(self.ck, "gate", r.out)
        self.pub = json.loads(lastjson(r.out))["context_updates"]
        self.ck("gate: its thirteen keys on every path", GATE_KEYS,
                ",".join(sorted(self.pub)))
        return self.pub

    def findings(self):
        return json.loads((self.T / "task_review/state/findings/t1.json").read_text())

    def dropped(self):
        return json.loads((self.T / "task_review/state/dropped/t1.json").read_text())

    def verdict(self) -> str:
        return self.pub["task_review_verdict"]


def test_the_gate_evidence_rule(ck, tmp_path):
    """C5 step 2: a finding survives only with a file in the checkout and evidence that holds --
    a `path:N` of a file that exists, or a non-empty span in backticks."""
    C = ReviewCase(ck, tmp_path, "evidence")
    for label, find in (
        ("a file the checkout does not have", a_finding(file="src/gone.txt")),
        ("an empty evidence", a_finding(evidence="")),
        ("a path:N of a file that does not exist", a_finding(evidence="src/gone.txt:3")),
        ("no file at all", a_finding(file="")),
    ):
        C.run(refute=refuted([find]), review=reviewed())
        ck(f"{label} is dropped", "0", C.pub["task_review_errors"])
        ck(f"{label} passes the Task", "pass", C.verdict())
        ck(f"{label} is counted as dropped", "1", C.pub["task_review_dropped"])
    for label, find in (
        ("a span in backticks", a_finding(evidence="`make check` exits 1")),
        ("a path:N and a span", a_finding(evidence="test/a.txt:2 and `make check`")),
        ("an empty backtick pair beside a real path", a_finding(evidence="src/a.txt:1")),
    ):
        C.run(refute=refuted([find]), review=reviewed())
        ck(f"{label} is kept", "1", C.pub["task_review_errors"])
        ck(f"{label} is a rework", "rework", C.verdict())
    # The empty pair is no span, and it is the one bug worth pinning twice: ` `` ` must not count.
    C.run(refute=refuted([a_finding(evidence="run `` first")]), review=reviewed())
    ck("an empty backtick pair is no evidence", "0", C.pub["task_review_errors"])


def test_the_gate_acceptance_and_edge_cases(ck, tmp_path):
    """C5 step 1's synthesized findings: an unmet acceptance statement that carries evidence is
    an error, an uncovered edge case is a warning, and neither is a finding the reviewer wrote."""
    C = ReviewCase(ck, tmp_path, "synth")
    C.run(refute=refuted(), review=reviewed(acceptance=[
        {"index": 1, "met": True, "evidence": "src/a.txt:1"},
        {"index": 2, "met": False, "evidence": "src/a.txt:1"}]))
    ck("an unmet statement is an error", "1", C.pub["task_review_errors"])
    ck("the message names the statement", "Acceptance 2 is not met: a2",
       jq(".[0].message", file=C.T / "task_review/state/findings/t1.json"))
    ck("its file is the path the evidence cites", "src/a.txt",
       jq(".[0].file", file=C.T / "task_review/state/findings/t1.json"))
    ck("it is the review's finding", "review",
       jq(".[0].source", file=C.T / "task_review/state/findings/t1.json"))

    # Without evidence that holds, there is nothing to check: no finding (and the statement's
    # absence from the diff is the reviewer's own business).
    C.run(refute=refuted(), review=reviewed(acceptance=[
        {"index": 2, "met": False, "evidence": "i looked at it"}]))
    ck("an unmet statement with no valid evidence is no finding", "0", C.pub["task_review_errors"])
    ck("and the Task passes", "pass", C.verdict())

    # An index the Task does not have is ignored, not an error.
    C.run(refute=refuted(), review=reviewed(acceptance=[
        {"index": 3, "met": False, "evidence": "src/a.txt:1"}]))
    ck("an index outside the acceptance list is ignored", "0", C.pub["task_review_errors"])

    # An uncovered edge case is a warning: it rides along in a rework but triggers nothing.
    # A warning rides along in the rework the error forces; alone it would pass the Task, and
    # a pass deletes the findings file, so the round that keeps them needs the error too.
    C.run(refute=refuted([a_finding(), a_finding(severity="warning", file="test/a.txt",
                                                 evidence="test/a.txt:2 fails")],
                         edge_cases=[{"id": "e1", "covered": False,
                                      "evidence": "searched for a test of the empty input"}]),
          review=reviewed())
    ck("an uncovered edge case is a warning", "2", C.pub["task_review_warnings"])
    ck("it is a defect", "defect",
       jq("[.[] | select(.message | startswith(\"No test covers\"))][0].kind",
          file=C.T / "task_review/state/findings/t1.json"))
    ck("it names the plan's case", "No test covers edge case e1: an empty input",
       jq("[.[] | select(.message | startswith(\"No test covers\"))][0].message",
          file=C.T / "task_review/state/findings/t1.json"))
    ck("the plan's edge cases are counted", "1", C.pub["task_review_edges"])
    ck("and the covered ones are counted", "0", C.pub["task_review_edges_covered"])
    C.run(refute=refuted(), review=reviewed())
    ck("a covered edge case is counted as covered", "1", C.pub["task_review_edges_covered"])
    C.run(refute=refuted([a_finding(severity="warning")], edge_cases=[
        {"id": "e1", "covered": False, "evidence": "searched and found nothing"}]), review=reviewed())
    ck("warnings alone pass the Task", "pass", C.verdict())


def test_the_gate_disputes(ck, tmp_path):
    """C5 step 3: the coder may dispute a finding (E12). The next reviewer must uphold it with
    evidence it did not give before, or the finding is dead and recorded as withdrawn."""
    prior = [a_finding(id="t1v1f1", source="refuter")]
    disputed = {"t1v1f1": {"answer": "disputed", "reason": "the check covers it",
                           "evidence": "`make check` passes"}}

    C = ReviewCase(ck, tmp_path, "disputes")
    C.run(refute=refuted([a_finding()], responses=[]), review=reviewed(), reviews=1,
          findings=prior, answers=disputed)
    ck("a withdrawn dispute drops the finding", "0", C.pub["task_review_errors"])
    ck("a withdrawn dispute is counted", "1", C.pub["task_review_withdrawn"])
    ck("a withdrawn dispute is recorded", "src/a.txt", jq(".[0].file", file=C.T / "task_review/state/dropped/t1.json"))
    ck("the recorded evidence is normalized", "`make check` fails",
       jq(".[0].evidence_norm", file=C.T / "task_review/state/dropped/t1.json"))

    # Upheld with evidence the finding did not cite: the finding survives, with that evidence.
    C.run(refute=refuted([], responses=[
        {"finding_id": "t1v1f1", "verdict": "upheld", "new_evidence": "test/a.txt:2 shows the case"}],
    ), review=reviewed(), reviews=1, findings=prior, answers=disputed)
    ck("an upheld dispute keeps the finding", "1", C.pub["task_review_errors"])
    ck("an upheld dispute is not a withdrawal", "0", C.pub["task_review_withdrawn"])
    ck("the new evidence replaces the old", "test/a.txt:2 shows the case",
       jq(".[0].evidence", file=C.T / "task_review/state/findings/t1.json"))
    ck("and the round is a rework", "rework", C.verdict())
    ck("the upheld finding is numbered in this round", "t1v2f1",
       jq(".[0].id", file=C.T / "task_review/state/findings/t1.json"))

    # The same evidence in another case and spacing is not new evidence.
    C.run(refute=refuted([a_finding()], responses=[
        {"finding_id": "t1v1f1", "verdict": "upheld",
         "new_evidence": "  `MAKE   check` FAILS "}],
    ), review=reviewed(), reviews=1, findings=prior, answers=disputed)
    ck("the same evidence in another case is no new evidence", "0", C.pub["task_review_errors"])
    ck("and it withdraws the finding", "1", C.pub["task_review_withdrawn"])

    # An upheld dispute whose new evidence fails the rule is no evidence.
    C.run(refute=refuted([a_finding()], responses=[
        {"finding_id": "t1v1f1", "verdict": "upheld", "new_evidence": "i am sure"}],
    ), review=reviewed(), reviews=1, findings=prior, answers=disputed)
    ck("upheld without valid new evidence withdraws", "0", C.pub["task_review_errors"])

    # No response at all, and the owning reviewer missing the round altogether: both withdraw.
    C.run(refute=refuted([]), review=reviewed(), reviews=1, findings=prior, answers=disputed)
    ck("no response withdraws the finding", "0", C.pub["task_review_errors"])
    ck("no response is a withdrawal", "1", C.pub["task_review_withdrawn"])
    C.run(refute=refuted([], stage="task_refute"), review=reviewed(), served=("missed", "box"),
          reviews=1, findings=prior, answers=disputed)
    ck("a missed reviewer cannot uphold", "0", C.pub["task_review_errors"])
    ck("a missed reviewer withdraws its disputes", "1", C.pub["task_review_withdrawn"])

    # The answer is looked for in the finding's own reviewer: the other one's entries are not it.
    C.run(refute=refuted([a_finding()], responses=[]), review=reviewed(responses=[
        {"finding_id": "t1v1f1", "verdict": "upheld", "new_evidence": "test/a.txt:2"}]),
        reviews=1, findings=prior, answers=disputed)
    ck("the other reviewer's response does not count", "0", C.pub["task_review_errors"])

    # An undisputed finding is not looked up at all, and a coder's `fixed` does not silence the
    # reviewer: the finding it raises again this round stands on its own evidence.
    C.run(refute=refuted([a_finding()], responses=[]), review=reviewed(), reviews=1,
          findings=prior, answers={"t1v1f1": {"answer": "fixed"}})
    ck("a fixed finding is not a dispute", "0", C.pub["task_review_disputed"])
    ck("a re-raised finding still stands", "1", C.pub["task_review_errors"])


def test_the_gate_repeats(ck, tmp_path):
    """C5 step 4 (E12): an unchanged repeat of a dropped finding is dropped, in the round that
    dropped it and in every later one."""
    C = ReviewCase(ck, tmp_path, "repeats")
    gone = {"file": "src/a.txt", "evidence_norm": "`make check` fails",
            "message": "An empty input reaches the handler"}
    C.run(refute=refuted([a_finding()]), review=reviewed(), dropped=[gone])
    ck("a repeat of a dropped finding is dropped", "0", C.pub["task_review_errors"])
    ck("and it passes the Task", "pass", C.verdict())

    # The same in a later round: the dropped list lives for the run.
    C.run(refute=refuted([a_finding()]), review=reviewed(), reviews=2, dropped=[gone])
    ck("a repeat in a later round is dropped too", "0", C.pub["task_review_errors"])
    ck("the round number still advances", "3", C.pub["task_review_round"])

    # A different evidence is a different finding: the reviewer cited something new.
    C.run(refute=refuted([a_finding(evidence="test/a.txt:2 fails")]), review=reviewed(),
          dropped=[gone])
    ck("a finding with new evidence is not a repeat", "1", C.pub["task_review_errors"])

    # Withdrawn in this round and raised again in the same round's findings: dropped, because
    # the entry the withdrawal added counts as much as one that was already there.
    C.run(refute=refuted([a_finding()], responses=[]), review=reviewed(), reviews=1,
          findings=[a_finding(id="t1v1f1", source="refuter")],
          answers={"t1v1f1": {"answer": "disputed", "reason": "r", "evidence": "`make check`"}})
    ck("a finding withdrawn and raised again is dropped", "0", C.pub["task_review_errors"])
    ck("and the withdrawal is recorded", "1", C.pub["task_review_withdrawn"])


def test_the_gate_verdicts(ck, tmp_path):
    """C5 steps 6-7: `n` is the Task's review count plus one. An error reworks below n=4 and
    blocks at n=4, warnings alone pass, and a round neither reviewer served is a miss."""
    C = ReviewCase(ck, tmp_path, "verdicts")
    for reviews, want in ((0, "rework"), (1, "rework"), (2, "rework"), (3, "blocked")):
        C.run(refute=refuted([a_finding()]), review=reviewed(), reviews=reviews)
        ck(f"an error at review {reviews + 1} is {want}", want, C.verdict())
        ck(f"the round number is {reviews + 1}", str(reviews + 1), C.pub["task_review_round"])
        ck(f"the review count is {reviews + 1}", str(reviews + 1),
           jq(".[0].reviews", file=C.T / "tasks.json"))
        ck(f"review {reviews + 1} leaves the Task {'blocked' if want == 'blocked' else 'open'}",
           "blocked" if want == "blocked" else "open", jq(".[0].status", file=C.T / "tasks.json"))
        ck(f"the findings are kept for review {reviews + 1}", "1",
           jq("length", file=C.T / "task_review/state/findings/t1.json"))

    C.run(refute=refuted(edge_cases=[{"id": "e1", "covered": False, "evidence": "none"}]),
          review=reviewed())
    ck("warnings alone pass", "pass", C.verdict())
    ck("a pass sets passed", "passed", jq(".[0].status", file=C.T / "tasks.json"))
    ck("a pass deletes the findings", "absent",
       present(C.T / "task_review/state/findings/t1.json"))

    C.run(refute=refuted(), review=reviewed(), served=("missed", "missed"))
    ck("both reviewers missing is a miss", "missed", C.verdict())
    ck("a miss passes the Task", "passed", jq(".[0].status", file=C.T / "tasks.json"))
    ck("a miss records why", "no valid Task review", jq(".[0].review_note", file=C.T / "tasks.json"))

    # No `served` file at all is the check node having crashed: the same miss.
    C.run(refute=refuted(), review=reviewed(), served=(None, None))
    ck("a missing served file is a miss", "missed", C.verdict())
    ck("and it is published as missed", "missed", C.pub["task_review_refute_served"])

    # One miss is not a miss: the other reviewer's findings still decide the round.
    C.run(refute=refuted([a_finding()]), review=reviewed(), served=("box", "missed"))
    ck("one missed reviewer leaves the other's findings", "rework", C.verdict())
    C.run(refute=refuted(), review=reviewed(), served=("missed", "box"))
    ck("a missed refuter alone passes", "pass", C.verdict())

    # The served values are published as they were served.
    C.run(refute=refuted(stage="task_refute_fb"), review=reviewed(stage="task_review_fb"),
          served=("fallback", "fallback"))
    ck("a fallback round is published as fallback", "fallback,fallback",
       C.pub["task_review_refute_served"] + "," + C.pub["task_review_review_served"])

    # The Task's own source is published: `record` and the plan gate read it, and so will the
    # report that counts blocked-by-review Tasks.
    C.run(refute=refuted(), review=reviewed(), task={"source": "refute"})
    ck("the Task's source is published", "refute", C.pub["task_review_source"])
    ck("the Task's id is published", "t1", C.pub["task_review_task"])


def test_the_gate_state_and_records(ck, tmp_path):
    """C5 steps 5 and 8: the ids, the round record, the patch count the next delta reads, and
    the notes the run keeps."""
    C = ReviewCase(ck, tmp_path, "state")
    T = C.T
    two = [a_finding(), a_finding(evidence="test/a.txt:2 fails", message="The second one")]
    C.run(refute=refuted(two), review=reviewed([a_finding(kind="spec")]),
          patches=("001.patch", "002.patch"))
    ck("ids are numbered per source and round", "t1v1f1,t1v1f2,t1v1s1",
       jq("[.[].id] | join(\",\")", file=T / "task_review/state/findings/t1.json"))
    ck("the patch count is what the next delta reads", "2",
       cat(T / "task_review/state/patches/t1/reviewed"))
    ck("the answers are read and deleted", "absent",
       present(T / "task_review/state/answers/t1.json"))
    ck("the round record is kept", "present", present(T / "task_review/state/rounds/t1-1.json"))
    ck("the round record holds both results and the survivors", "true,true,3",
       jq("(.refute != null | tostring) + \",\" + (.review != null | tostring) + \",\" + ((.findings | length) | tostring)",
          file=T / "task_review/state/rounds/t1-1.json"))
    ck("the round record names the verdict", "rework",
       jq(".verdict", file=T / "task_review/state/rounds/t1-1.json"))
    ck("the progress heading names the round and the verdict", "1",
       count("^## Review 1 of t1: rework$", file=T / "progress.md"))
    ck("the progress lists every error", "3", count("^- ", file=T / "progress.md"))

    C.run(refute=refuted(two), review=reviewed(), reviews=1,
          findings=[a_finding(id="t1v1f1", source="refuter")])
    ck("a later round has its own ids", "t1v2f1,t1v2f2",
       jq("[.[].id] | join(\",\")", file=T / "task_review/state/findings/t1.json"))
    ck("a later round keeps only its own record", "t1-1.json,t1-2.json",
       ",".join(p.name for p in sorted((T / "task_review/state/rounds").iterdir())))
    ck("the progress keeps every round", "2", count("^## Review ", file=T / "progress.md"))

    # A message is cut at 200 characters in `block_reason`, and at 300 in the progress line.
    long = "x" * 400
    C.run(refute=refuted([a_finding(message=long)]), review=reviewed(), reviews=3)
    ck("a blocked Task's reason is a box review", "box review: " + "x" * 200,
       jq(".[0].block_reason", file=T / "tasks.json"))
    ck("the progress line is cut at 300", "1",
       count("^- " + "x" * 300 + "$", file=T / "progress.md"))

    # A Task with no reviews yet can still carry findings, which `pick` renders for the rework:
    # the class and the evidence travel with them.
    C.run(refute=refuted([a_finding(**{"class": "a handler that trusts its argument"})]),
          review=reviewed())
    ck("the class travels to the findings file", "a handler that trusts its argument",
       jq(".[0].class", file=T / "task_review/state/findings/t1.json"))
    ck("every finding carries its source", "refuter",
       jq(".[0].source", file=T / "task_review/state/findings/t1.json"))


def test_the_task_review_loop_in_order(ck, tmp_path):
    """The whole loop with the real scripts: plan, work, review (an error), rework, review (a
    dispute upheld), rework, review (clean), the next Task. Every node reads what the one before
    it wrote, so a contract drift between any two of them fails here."""
    RP = tmp_path / "loop08"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    for name in ("setup.sh", "ci.sh"):
        p = write(repo / ".fabro" / name, "#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    git("-C", repo, "add", "-A")
    git("-C", repo, "commit", "-qm", "the checks")
    for name, value in (("phase", "pre"), ("iterations", "0"), ("post_iterations", "0"),
                        ("rework_iterations", "0")):
        write(T / name, value + "\n")
    write(T / "tasks.json",
          task_list(one_task(id="t1", context="## Task one\n", acceptance=["a1", "a2", "a3"]),
                    one_task(id="t2", context="## Task two\n")))
    names = ("pick", "task_plan_gate", "excerpts", "task_block_gate", "autofix", "check",
             "record", "task_review_prep", "task_refute_check", "task_review_check",
             "task_review_gate")
    nodes = {n: stage(ck, n, T, graph=RAPH2, label=n) for n in names}

    def go(name):
        r = sh(nodes[name], cwd=repo)
        ck(f"{name}: exits 0", "0", r.rc)
        no_braces(ck, name, r.out)
        return json.loads(lastjson(r.out))["context_updates"]

    def plain(name):
        """A node that publishes nothing: `excerpts` only writes the file the coder reads."""
        r = sh(nodes[name], cwd=repo)
        ck(f"{name}: exits 0", "0", r.rc)
        no_braces(ck, name, r.out)

    def work(msg, visit, **extra):
        """One Iteration: a commit, the stage's receipt, its result, and a green gate."""
        commit_all(repo, {"src/a.txt": msg}, msg)
        mkreadok(T, visit)
        doc = {"status": "done", "summary": msg, "learnings": [],
               "_io": {"stage": "iterate_t1", "visit": visit}}
        doc.update(extra)
        write(T / "iteration.json", json.dumps(doc) + "\n")
        write(T / "check_ok", "1\n")

    def reviewer(node, visit, doc):
        mkreadok(T, visit)
        doc["_io"] = {"stage": node, "visit": visit}
        key = "refute" if node.startswith("task_refute") else "review"
        write(T / f"task_review/out/{key}.json", json.dumps(doc) + "\n")

    def iteration():
        """Everything between `pick` and the next `pick` for a Task already planned."""
        plain("excerpts")
        ck("the Iteration starts with no block", "none", go("task_block_gate")["task_block"])
        go("autofix")
        ck("the mechanical gate passes", "True", str(go("check")["task_validation_ok"]))

    # 1. The Task is planned, worked, and reviewed. The refuter finds one defect.
    pub = go("pick")
    ck("a Task with no plan is planned", "plan", pub["task_step"])
    ck("the loop takes the first Task", "t1", jq(".id", file=T / "current_task.json"))
    mkreadok(T, "p1")
    doc = dict(PLAN, _io={"stage": "task_plan", "visit": "p1"})
    write(T / "task_plan.json", json.dumps(doc) + "\n")
    ck("the plan goes on", "go", go("task_plan_gate")["task_plan_route"])
    work("t1 work", "i1")
    iteration()
    pub = go("record")
    ck("the pass is a review", "True", str(pub["review_due"]))
    ck("the Task is in review", "reviewing", jq(".[0].status", file=T / "tasks.json"))
    ck("the review has inputs", "True", str(go("task_review_prep")["task_review_go"]))
    reviewer("task_refute", "r1", refuted([a_finding()]))
    ck("the refuter's result is served", "done", go("task_refute_check")["task_refute_next"])
    reviewer("task_review", "v1", reviewed())
    ck("the reviewer's result is served", "done", go("task_review_check")["task_review_next"])
    pub = go("task_review_gate")
    ck("one error is a rework", "rework", pub["task_review_verdict"])
    ck("the rework leaves the Task open", "open", jq(".[0].status", file=T / "tasks.json"))
    ck("the finding is numbered", "t1v1f1",
       jq(".[0].id", file=T / "task_review/state/findings/t1.json"))

    # 2. The rework disputs the finding, the refuter upholds it with evidence it did not cite
    #    before, and the round is a rework again.
    pub = go("pick")
    ck("a rework works the same Task", "work", pub["task_step"])
    ck("the dossier carries the findings", "1", count("^## Review findings$",
                                                     file=T / "task-context.md"))
    ck("the dossier names the evidence", "1", count("Evidence: `make check` fails",
                                                   file=T / "task-context.md"))
    work("t1 rework", "i2", disputes=[{"finding_id": "t1v1f1", "reason": "the test covers it",
                                       "evidence": "test/a.txt:2"}])
    iteration()
    ck("a rework is due a review", "True", str(go("record")["review_due"]))
    ck("a rework spends no cap", "1", cat(T / "iterations"))
    ck("a rework counts", "1", cat(T / "rework_iterations"))
    go("task_review_prep")
    ck("the second review gets a delta", "present",
       present(T / "task_review/in/delta.patch"))
    ck("the history carries the dispute", "1",
       count(r"^  Coder: \*\*disputed\*\*: the test covers it Evidence: test/a\.txt:2$",
             file=T / "task_review/in/history.md"))
    reviewer("task_refute", "r2", refuted([], responses=[
        {"finding_id": "t1v1f1", "verdict": "upheld", "new_evidence": "test/a.txt:2 fails"}],
        edge_cases=[]))
    go("task_refute_check")
    reviewer("task_review", "v2", reviewed())
    go("task_review_check")
    pub = go("task_review_gate")
    ck("an upheld dispute is a rework", "rework", pub["task_review_verdict"])
    ck("the upheld finding keeps its new evidence", "test/a.txt:2 fails",
       jq(".[0].evidence", file=T / "task_review/state/findings/t1.json"))
    ck("the upheld finding is numbered in the second round", "t1v2f1",
       jq(".[0].id", file=T / "task_review/state/findings/t1.json"))

    # 3. A clean round passes the Task, and the loop takes the next one.
    go("pick")
    work("t1 again", "i3")
    iteration()
    go("record")
    go("task_review_prep")
    reviewer("task_refute", "r3", refuted([], edge_cases=[{"id": "e1", "covered": True,
                                                           "evidence": "test/a.txt:1"}]))
    go("task_refute_check")
    reviewer("task_review", "v3", reviewed())
    go("task_review_check")
    pub = go("task_review_gate")
    ck("a clean round passes the Task", "pass", pub["task_review_verdict"])
    ck("the Task is passed", "passed", jq(".[0].status", file=T / "tasks.json"))
    ck("the findings are cleared", "absent",
       present(T / "task_review/state/findings/t1.json"))
    pub = go("pick")
    ck("the loop takes the next unplanned Task", "plan", pub["task_step"])
    ck("the loop took task two", "t2", jq(".id", file=T / "current_task.json"))


def test_four_rejected_reviews_block_the_task(ck, tmp_path):
    """E10: the fourth review with an error blocks the Task, and `pick` reads the block as the
    loop running out (C1 step 4)."""
    RP = tmp_path / "blocked08"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    gate = stage(ck, "task_review_gate", T, graph=RAPH2, label="task_review_gate")
    pick = stage(ck, "pick", T, graph=RAPH2, label="pick")
    (T / "task_review/out").mkdir(parents=True)
    (T / "task_review/state").mkdir(parents=True)
    write(T / "task_review/state/refute.served", "box\n")
    write(T / "task_review/state/review.served", "box\n")
    write(T / "task_review/out/refute.json", json.dumps(refuted([a_finding()])) + "\n")
    write(T / "task_review/out/review.json", json.dumps(reviewed()) + "\n")
    write(T / "current_task.json", json.dumps(dict(REVIEW_TASK, reviews=3, source="plan")) + "\n")
    write(T / "task_plans/t1.json", json.dumps(REVIEW_PLAN) + "\n")
    write(T / "tasks.json", task_list(one_task(id="t1", reviews=3, iterations=2, misses=1)))
    write(T / "phase", "pre\n")
    r = sh(gate, cwd=repo)
    ck("the gate: exits 0", "0", r.rc)
    pub = json.loads(lastjson(r.out))["context_updates"]
    ck("the fourth error blocks the Task", "blocked", pub["task_review_verdict"])
    ck("the Task is blocked", "blocked", jq(".[0].status", file=T / "tasks.json"))
    ck("the block reason is the first error", "box review: An empty input reaches the handler",
       jq(".[0].block_reason", file=T / "tasks.json"))
    r = sh(pick, cwd=repo)
    ck("pick: exits 0", "0", r.rc)
    pub = json.loads(lastjson(r.out))["context_updates"]
    ck("a blocked Task runs the loop out", "exhausted", pub["loop"])
    ck("and the run records it", "true", cat(T / "loop_exhausted"))


def test_pick_recovers_a_review_that_did_not_finish(ck, tmp_path):
    """C1 step 3: only a crashed gate leaves a Task `reviewing`. `pick` runs the review again,
    three times, and the fourth leaves the Task passed with a note."""
    RP = tmp_path / "recover08"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    pick = stage(ck, "pick", T, graph=RAPH2, label="pick (ralph-v2)")
    write(T / "phase", "pre\n")
    write(T / "iterations", "0\n")
    write(T / "tasks.json",
          task_list(one_task(id="t1", status="reviewing", reviews=1, context="## one\n"),
                    one_task(id="t2", context="## two\n")))

    def go():
        r = sh(pick, cwd=repo)
        ck("pick: exits 0 (recover)", "0", r.rc)
        no_braces(ck, "pick (recover)", r.out)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck("pick: the six keys on every path (recover)", PICK_KEYS, ",".join(sorted(pub)))
        return pub

    for attempt in (1, 2, 3):
        pub = go()
        ck(f"recovery {attempt} runs the review", "review", pub["loop"])
        ck(f"recovery {attempt} names no step", "none", pub["task_step"])
        ck(f"recovery {attempt} counts", str(attempt),
           cat(T / "task_review/state/recovers/t1"))
        ck(f"recovery {attempt} names the reviewing Task", "t1",
           jq(".id", file=T / "current_task.json"))
        ck(f"recovery {attempt} does not move the Task", "reviewing",
           jq(".[0].status", file=T / "tasks.json"))
    pub = go()
    ck("the fourth recovery gives up on the review", "plan", pub["task_step"])
    ck("the Task is passed", "passed", jq(".[0].status", file=T / "tasks.json"))
    ck("with the reason recorded", "the Task review did not finish",
       jq(".[0].review_note", file=T / "tasks.json"))
    ck("and the loop takes the next Task", "t2", jq(".id", file=T / "current_task.json"))

    # A conflict leaves the recovery alone: the merge is resolved before any review runs, and
    # `pick` must not spend a recovery on it.
    rm(T / "task_review/state/recovers/t1")
    write(T / "tasks.json", task_list(one_task(id="t1", status="reviewing", reviews=1)))
    git("-C", repo, "checkout", "-q", "-b", "other")
    commit_all(repo, {"src/a.txt": "remote\n"}, "remote")
    git("-C", repo, "push", "-q", "origin", "other:main")
    git("-C", repo, "checkout", "-q", "main")
    commit_all(repo, {"src/a.txt": "local\n"}, "local")
    rm(T / "current_task.json", T / "task_base_sha")
    pub = go()
    ck("a conflict is a rebase", "True", str(pub["rebase_failed"]))
    ck("a conflict spends no recovery", "absent", present(T / "task_review/state/recovers/t1"))
    ck("a conflict writes no task_base_sha", "absent", present(T / "task_base_sha"))


def test_a_fallback_plan_is_reviewed_like_any_other(ck, tmp_path):
    """A Task whose planner was rejected twice runs the fallback plan (C2 step 4). Its review
    reads that plan's four goals and no edge case, and nothing else about it is special."""
    C = ReviewCase(ck, tmp_path, "fallback-plan")
    fallback = {"context": "## The Task\n", "plan_type": "fallback", "goals_done": [],
                "goals": [{"id": "g1", "goal": "a1", "covers": [1], "check": "(none: fallback plan)"},
                          {"id": "g2", "goal": "a2", "covers": [2], "check": "(none: fallback plan)"}],
                "edge_cases": [], "bug_class": "", "risks": []}
    C.run(refute=refuted([], edge_cases=[]), review=reviewed(), plan=fallback)
    ck("a fallback plan is a plan", "pass", C.verdict())
    ck("it has no edge case to cover", "0", C.pub["task_review_edges"])
    ck("its goals reach the reviewers", "2",
       jq(".goals | length", file=C.T / "task_review/in/plan.json"))
    ck("and their checks travel as written", "(none: fallback plan),0",
       jq("([.goals[].check] | unique | join(\",\")) + \",\" + ((.edge_cases | length) | tostring)",
          file=C.T / "task_review/in/plan.json"))


def test_a_post_iteration_is_never_reviewed(ck, tmp_path):
    """E7: post-PR CI Tasks and the `final_fix` Task are exempt. Their pass is `passed`, not
    `reviewing`, so `review_due` never sends them to the reviewers."""
    RP = tmp_path / "post08"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo = RP / "repo"
    repo.mkdir()
    plain_repo(repo)
    rec = stage(ck, "record", T, graph=RAPH2, label="record (ralph-v2)")
    mkreadok(T, "i1")
    write(T / "phase", "post\n")
    for name, value in (("iterations", "0"), ("post_iterations", "0"), ("rework_iterations", "0")):
        write(T / name, value + "\n")
    write(T / "iteration.json", json.dumps({"status": "done", "summary": "s", "learnings": [],
                                            "_io": {"stage": "iterate_t1", "visit": "i1"}}) + "\n")
    write(T / "check_ok", "1\n")
    write(T / "current_task.json", json.dumps({"id": "t1", "title": "A"}) + "\n")
    write(T / "tasks.json", task_list(one_task(id="t1", source="ci")))
    r = sh(rec, cwd=repo)
    ck("a post Iteration: exits 0", "0", r.rc)
    pub = json.loads(lastjson(r.out))["context_updates"]
    ck("a post Iteration passes the Task", "passed", jq(".[0].status", file=T / "tasks.json"))
    ck("a post Iteration is never due a review", "False", str(pub["review_due"]))
    ck("a post Iteration counts as post", "1", cat(T / "post_iterations"))
    for source in ("ci", "final_fix"):
        write(T / "phase", "pre\n")
        write(T / "tasks.json", task_list(one_task(id="t1", source=source)))
        r = sh(rec, cwd=repo)
        pub = json.loads(lastjson(r.out))["context_updates"]
        ck(f"a {source} Task in phase pre is never reviewed", "False", str(pub["review_due"]))
        ck(f"a {source} Task passes outright", "passed", jq(".[0].status", file=T / "tasks.json"))


def test_the_task_review_loop_edges(ck):
    """The edges C1, C3, C4 and C5 name: one way into the reviewers, one check per reviewer,
    and the gate's unconditional edge back to `pick`."""
    e = edges(RAPH2)
    ck("the graph has edges to check", "true", str(bool(e)).lower())
    ck("a Task in review is reviewed before anything else is worked", "1",
       count("pick->task_review_prep context.loop=review && context.rebase_failed=false",
             e, flags="xF"))
    ck("a recorded review is due exactly when record says so", "1",
       count("record->task_review_prep outcome=succeeded && context.review_due=true", e, flags="xF"))
    ck("record still has its unconditional way back to pick", "1", count("^record->pick $", e))
    ck("the prep is the only way into the refuter", "1",
       count("task_review_prep->task_refute outcome=succeeded && context.task_review_go=true",
             e, flags="xF"))
    ck("the prep has its escape", "1", count("^task_review_prep->pick $", e))
    for node in ("task_refute", "task_refute_fb", "task_review", "task_review_fb"):
        check = node.rsplit("_fb", 1)[0] + "_check"
        outs = [l for l in e.split("\n") if l.startswith(node + "->")]
        ck(f"every edge out of {node} goes to {check}", "2", str(len(outs)))
        ck(f"every edge out of {node} goes to {check} (targets)", check,
           ",".join(sorted({line.split("->")[1].split(" ")[0] for line in outs})))
    ck("a retry goes back to the box reviewer", "1",
       count("task_refute_check->task_refute outcome=succeeded && context.task_refute_next=retry",
             e, flags="xF"))
    ck("a retry goes back to the box reviewer (review)", "1",
       count("task_review_check->task_review outcome=succeeded && context.task_review_next=retry",
             e, flags="xF"))
    ck("the fallback is entered only from the check", "1",
       count("task_refute_check->task_refute_fb outcome=succeeded && context.task_refute_next=fallback",
             e, flags="xF"))
    ck("the fallback is entered only from the check (review)", "1",
       count("task_review_check->task_review_fb outcome=succeeded && context.task_review_next=fallback",
             e, flags="xF"))
    ck("the refuter's check goes on to the reviewer", "1", count("^task_refute_check->task_review $", e))
    ck("the reviewer's check goes on to the gate", "1",
       count("^task_review_check->task_review_gate $", e))
    ck("the gate's only way out is pick", "1", count("^task_review_gate->pick $", e))


def test_the_checker_holds_the_task_review_loop(ck, tmp_path):
    """R19 exists, and each of its violations is one it names: no retry on either reviewer, no
    check node, a missing or retrying fallback, a fallback on the box, an edge that skips the
    check, and a check with no way out."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "graph_invariants_r19", REPO_ROOT / "ops/check-graph-invariants.py")
    inv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inv)
    ck("the real graph satisfies R19", "", "\n".join(
        " ".join(v) for v in inv.r19_task_review_loop(REPO_ROOT)))

    src = RAPH2.read_text()
    ck("R19 is silent without a graph", "[]", str(inv.r19_task_review_loop(tmp_path)))

    def run(mutate, label):
        root = tmp_path / label
        (root / ".fabro/workflows/ralph-v2").mkdir(parents=True)
        (root / ".fabro/workflows/ralph-v2/workflow.fabro").write_text(mutate(src))
        found = [" ".join(v) for v in inv.r19_task_review_loop(root)]
        ck(f"R19 names {label}", "true", str(bool(found)).lower())
        return "\n".join(found)

    def swap(old, new):
        def f(text):
            ck(f"the mutation {'applies' if old in text else 'is missing'}: {old[:40]}", "true",
               str(old in text).lower())
            return text.replace(old, new, 1)
        return f

    ck("R19: a node retry on the refuter", "true", str("task_refute sets max_retries='1'" in run(
        swap('task_refute [label="Refute the Task", class="task-refute", timeout="30m", max_retries=0',
             'task_refute [label="Refute the Task", class="task-refute", timeout="30m", max_retries=1'),
        "retry")).lower())
    ck("R19: a missing check node", "true", str("task_refute_check is missing" in run(
        lambda text: text.replace("    task_refute_check [label", "    task_refute_check_off [label"),
        "nocheck")).lower())
    ck("R19: a missing fallback", "true", str("task_review_fb is missing" in run(
        lambda text: text.replace("    task_review_fb [label", "    task_review_fbx [label"),
        "nofb")).lower())
    ck("R19: a retrying fallback", "true", str("task_refute_fb sets max_retries" in run(
        swap('task_refute_fb [label="Refute the Task (hosted fallback)", class="review-fallback", '
             'timeout="25m", max_retries=0',
             'task_refute_fb [label="Refute the Task (hosted fallback)", class="review-fallback", '
             'timeout="25m", max_retries=1'),
        "fbtries")).lower())
    ck("R19: a fallback on the box", "true", str("task_review_fb resolves to" in run(
        swap('task_review_fb [label="Review the Task (hosted fallback)", class="review-fallback"',
             'task_review_fb [label="Review the Task (hosted fallback)", class="task-review"'),
        "fbbox")).lower())
    ck("R19: an edge that skips the check", "true", str(
        "task_review has an edge to task_review_gate" in run(
            swap("    task_review -> task_review_check          [condition=\"outcome=succeeded\"]",
                 "    task_review -> task_review_gate           [condition=\"outcome=succeeded\"]"),
            "skipcheck")).lower())
    ck("R19: a check with no way out", "true", str("task_review_check has no unconditional edge" in run(
        swap("    task_review_check -> task_review_gate\n", ""), "noexit")).lower())


def test_a_reworked_task_is_worked_past_the_cap(ck, tmp_path):
    """P9: a Task whose last allowed Iteration passed is reviewed, and the rework it asks for
    must still be worked. Without the `reviews` exemption the run would exhaust the cap with
    the Task open and nothing to run."""
    RP = tmp_path / "cap08"
    T = RP / "sb"
    T.mkdir(parents=True)
    repo, origin = RP / "repo", RP / "origin.git"
    base_repo(origin, repo)
    gate = stage(ck, "task_review_gate", T, graph=RAPH2, label="task_review_gate")
    pick = stage(ck, "pick", T, graph=RAPH2, label="pick")
    three = task_list(one_task(id="t1", reviews=1), one_task(id="t2"), one_task(id="t3"))
    write(T / "phase", "pre\n")
    write(T / "iterations", "9\n")
    write(T / "tasks.json", three)
    write(T / "current_task.json", json.dumps(dict(REVIEW_TASK, reviews=1)) + "\n")
    write(T / "task_plans/t1.json", json.dumps(REVIEW_PLAN) + "\n")
    (T / "task_review/out").mkdir(parents=True)
    (T / "task_review/state").mkdir(parents=True)
    write(T / "task_review/state/refute.served", "box\n")
    write(T / "task_review/state/review.served", "box\n")
    write(T / "task_review/out/refute.json", json.dumps(refuted([a_finding()])) + "\n")
    write(T / "task_review/out/review.json", json.dumps(reviewed()) + "\n")

    r = sh(gate, cwd=repo)
    ck("the gate: exits 0", "0", r.rc)
    ck("the review sends the Task back", "rework",
       json.loads(lastjson(r.out))["context_updates"]["task_review_verdict"])
    r = sh(pick, cwd=repo)
    ck("pick: exits 0", "0", r.rc)
    pub = json.loads(lastjson(r.out))["context_updates"]
    ck("a reworked Task is still worked past the cap", "work", pub["loop"])
    ck("and it is the Task that was reviewed", "t1", jq(".id", file=T / "current_task.json"))
    ck("the plan is kept, so the rework needs no new plan", "work", pub["task_step"])
    ck("the findings reach the rework's dossier", "1", count("^## Review findings$",
                                                             file=T / "task-context.md"))
