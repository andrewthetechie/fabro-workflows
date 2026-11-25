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
import sys
from pathlib import Path

from harness import (RAPH2, REPO_ROOT, WF, cat, count, diff, edges, extract, git, jq, jq_n,
                     lastjson, mkreadok, present, rm, run, script_nodes, set_visit, sh, stage,
                     syntax_ok, write)
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
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1], "check": "`x`", "files": []},
                       {"id": "g2", "goal": "b", "covers": [2], "check": "`x`", "files": []}]),
           "the goals do not cover exactly the acceptance numbers 1 to 3")
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3], "check": "`x`", "files": []},
                       {"id": "g1", "goal": "b", "covers": [1], "check": "`x`", "files": []}]),
           "two goals share an id")
    reject(plan(goals=[{"id": "g1", "goal": "a", "covers": [1, 2, 3],
                        "check": "see the docs", "files": []}]),
           "goal g1 has no command in backticks")
    reject(plan(context="## Files and symbols\n\n- src/gone.txt:3\n"),
           "context cites src/gone.txt, which is not a file in this checkout")
    reject(plan(context="## Files and symbols\n\n- test/a.txt:9\n"),
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

    # A Task that fixes a finding must name the class of the bug.
    setup()
    write(T / "current_task.json", json.dumps(dict(task, source="refute")) + "\n")
    put(plan())
    ck("a fix Task without a bug_class is a retry", "retry", go()["task_plan_route"])
    ck("the bug class has its own sentence", "1",
       count("^this Task fixes a refute finding", file=T / "task_plan_error.txt"))
    setup()
    put(plan(bug_class="any handler that trusts its argument"))
    ck("a fix Task with a bug_class goes on", "go", go()["task_plan_route"])

    # The second rejection: the fallback plan, for 1, 3, 4 and 6 acceptance statements.
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
        return json.loads(lastjson(r.out))["context_updates"]

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
    tasks(task_list(one_task(source="ci")))
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
        return json.loads(lastjson(sh(pick, cwd=repo).out))["context_updates"]

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
        return json.loads(lastjson(r.out))["context_updates"]

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
        no_braces(ck, "record", r.out)
        return json.loads(lastjson(r.out))["context_updates"]

    done = {"status": "done", "summary": "did it", "learnings": []}

    setup(one_task(), dict(done, goals_done=["g2"]))
    pub = go()
    ck("record: iteration_passed on every path", "True", str(pub["iteration_passed"]))
    ck("record: no review is due in task 07", "False", str(pub["review_due"]))
    ck("an unreviewed Iteration spends the cap", "1", cat(T / "iterations"))
    ck("an unreviewed Iteration spends no rework", "0", cat(T / "rework_iterations"))
    ck("the progress line numbers the counter it spent", "1",
       count("^## Iteration 1: t1 passed (rung 0)$", file=T / "progress.md"))
    ck("goals_done is unioned into the plan", '["g1","g2"]',
       jq1("[.goals_done[]]", T / "task_plans/t1.json"))
    ck("no review means no answers file", "absent", present(A))

    setup(one_task(reviews=1), dict(done, goals_done=["g2"]))
    go()
    ck("a rework Iteration spends no cap", "0", cat(T / "iterations"))
    ck("a rework Iteration counts", "1", cat(T / "rework_iterations"))

    answers = dict(done, fixed=["t1v1f1"], disputes=[
        {"finding_id": "t1v1s1", "reason": "the check covers it", "evidence": "test/a.txt:2"},
        {"finding_id": "t1v1s2", "reason": "wrong", "evidence": "i looked at it"},
        {"finding_id": "t1v1f2", "reason": "it is not a defect", "evidence": "`make check`"},
    ])
    setup(one_task(reviews=1), answers)
    go()
    ck("a fixed finding is answered", "fixed", jq(".[\"t1v1f1\"].answer", file=A))
    ck("a dispute with a path is answered", "disputed", jq(".[\"t1v1s1\"].answer", file=A))
    ck("the dispute keeps its evidence", "test/a.txt:2", jq(".[\"t1v1s1\"].evidence", file=A))
    ck("a dispute without evidence is not written", "null", jq(".[\"t1v1s2\"]", file=A))
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
        return json.loads(lastjson(r.out))["context_updates"]

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
    write(T / "iteration.json",
          json.dumps({"status": "done", "summary": "did it", "learnings": [],
                      "goals_done": ["g1", "g2"]}) + "\n")
    set_visit(T / "iteration.json", "v1")
    write(T / "check_ok", "1\n")
    pub = run_node("record", repo)
    ck("the Task passes", "True", str(pub["iteration_passed"]))
    ck("the Task is passed in the list", "passed", jq(".[0].status", file=T / "tasks.json"))
    ck("its work is one patch", "001.patch",
       ",".join(p.name for p in sorted((T / "task_review/state/patches/t1").glob("*.patch"))))
    ck("every goal it reported is done", '["g1","g2"]',
       jq1("[.goals_done[]]", T / "task_plans/t1.json"))

    pub = run_node("pick", repo)
    ck("the loop takes the next unplanned Task", "plan", pub["task_step"])
    ck("the loop took task two", "t2", jq(".id", file=T / "current_task.json"))
    ck("the passed Task's plan is still there", "1",
       str(int((T / "task_plans/t1.json").is_file())))
    ck("the plan contract files are cleared for the next Task", "absent",
       present(T / "task_plan.json"))
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
