"""The Stage I/O receipts (ADR 0016): the real submit under backlog's generated manifest, the
reviewer gates' C7 check, the reads-only receipt on the implementer gates, and receipt
routing on the failure-routing gates. Plus the breaker limits the graphs set."""

import shutil

import pytest

from harness import (ARCH, GRAPH, ORIG_PATH, PR, SHARED, SHARED_TRIAGE, WF, cat, count, extract,
                     git, jq, jq_n, lastjson, rebase, rm, run, sh, stage, write)

FV = "fac3fac3fac3fac3fac3fac3fac3fac3"
STALE = "ffffffffffffffffffffffffffffffff"


# ---------------------------------------------------------------------------
# improve end to end — the REAL fabro-io `submit`, under backlog's GENERATED manifest, then
# the REAL improve_gate. The flat `ready` shape (task_title, task_body, ...) must survive the
# schema and the receipt, and the gate must build current_task.json from it. Needs fabro-io
# on PATH; skipped otherwise.
# ---------------------------------------------------------------------------
def test_improve_end_to_end(ck, tmp_path):
    if not shutil.which("fabro-io", path=ORIG_PATH):
        pytest.skip("no fabro-io on PATH; present in profile images")
    T = tmp_path / "ie2e"
    T.mkdir()
    # The generated line is `FABRO_IO_MANIFEST = '''<json>'''`: keep from the first `{` to
    # the last `}`, then point the absolute sandbox paths at the scratch root.
    manifest = ""
    for line in (WF / "backlog/workflow.toml").read_text().split("\n"):
        if line.startswith("FABRO_IO_MANIFEST"):
            manifest = rebase(line[line.index("{"):line.rindex("}") + 1], T)
            break
    ck("improve e2e: manifest extracted", "1", "1" if manifest else "0")
    env = {"FABRO_IO_ROOT": T, "FABRO_IO_MANIFEST": manifest}
    gate = write(T / "improve_gate.sh", rebase(extract(GRAPH, "improve_gate"), T))
    write(T / "current_task.json", '{"id":"big","title":"Big","body":"b","files":["f.py"],"covers":["c1","c2"],"priority":"high","source":"decompose"}')
    write(T / "tasks.json", '[{"id":"big","title":"Big","body":"b","files":[],"covers":["c1"],"source":"decompose"}]')
    write(T / "issue.json", '{"number":1,"title":"t","body":"b","comments":[],"labels":[]}')
    write(T / "task_index", "1\n")
    run(["fabro-io", "stage"], env={**env, "FABRO_WORKFLOW": "backlog", "FABRO_NODE_ID": "improve"})
    run(["fabro-io", "inputs"], env=env)
    # the nested shape is refused by submit, with the schema's own message
    write(T / "nested.json", '{"disposition":"ready","reason":"r","task":{"id":"big","title":"S","body":"nb"}}')
    ck("improve e2e: nested task refused by submit", "4", run(["fabro-io", "submit", "--file", T / "nested.json"], env=env).rc)
    # the flat shape is written with a receipt, and the gate builds the task from it
    write(T / "flat.json", '{"disposition":"ready","reason":"r","task_title":"Sharpened","task_body":"new body","task_covers":["c1"]}')
    ck("improve e2e: flat ready accepted by submit", "0", run(["fabro-io", "submit", "--file", T / "flat.json"], env=env).rc)
    r = sh(gate, cwd=T, env=env, stderr="drop")
    cur = T / "current_task.json"
    ck("improve e2e: gate exits 0", "0", r.rc)
    ck("improve e2e: gate routes ready", "ready", jq(".context_updates.task_disposition", lastjson(r.out)))
    ck("improve e2e: title", "Sharpened", jq(".title", file=cur))
    ck("improve e2e: body", "new body", jq(".body", file=cur))
    ck("improve e2e: id kept", "big", jq(".id", file=cur))
    ck("improve e2e: covers overridden", "c1", jq('.covers|join(" ")', file=cur))
    ck("improve e2e: files defaulted", "f.py", jq('.files|join(" ")', file=cur))
    ck("improve e2e: priority defaulted", "high", jq(".priority", file=cur))


# ---------------------------------------------------------------------------
# reviewer gates — C7 receipt check on the migrated reviewer stages (ADR 0016 task 08):
# backlog review/standards/spec/quality and review-merge standards/spec. A real submit
# stamps `_io.visit`; hand-written or stale files are refused.
# ---------------------------------------------------------------------------
def test_reviewer_gates(ck, tmp_path):
    T = tmp_path / "rgates"
    for d in ("review", "extra", "feedback", ".io"):
        (T / d).mkdir(parents=True, exist_ok=True)
    gates = {
        "review": (GRAPH, "review_gate"), "standards": (GRAPH, "standards_gate"),
        "spec": (GRAPH, "spec_gate"), "quality": (GRAPH, "quality_gate"),
        "phase_standards": (SHARED, "standards_gate"), "phase_spec": (SHARED, "spec_gate"),
    }
    s = {name: stage(ck, node, T, graph=g, name=name, label=f"{name} gate") for name, (g, node) in gates.items()}

    def vg_stage():
        write(T / ".io/stage.json", f'{{"workflow":"backlog","node":"review","visit":"{FV}","started":"2026-09-27T00:00:00Z"}}')

    def stamp(path, doc):
        write(path, jq("._io.visit = $v", doc, args=["--arg", "v", FV], raw=False, pipe=True) + "\n")

    route = lambda out, k: jq(f".context_updates.{k}", lastjson(out))

    # --- backlog review ---
    vg_stage(); stamp(T / "review/verdict.json", '{"decision":"approved","summary":"ok","findings":[]}')
    ck("review: approved routes approved", "approved", route(sh(s["review"]).out, "review_decision"))
    # changes_requested with a blocking finding writes rework.md and routes
    stamp(T / "review/verdict.json", '{"decision":"changes_requested","summary":"fix X","findings":[{"severity":"blocking","message":"a.ts:1 must change"}]}')
    ck("review: changes_requested routes", "changes_requested", route(sh(s["review"]).out, "review_decision"))
    ck("review: rework.md has the finding", "1", count("a.ts:1 must change", file=T / "feedback/rework.md"))
    # changes_requested with no blocking finding is a repair turn
    stamp(T / "review/verdict.json", '{"decision":"changes_requested","summary":"x","findings":[{"severity":"nit","message":"n"}]}')
    write(T / "review_attempts", "0\n")
    ck("review: changes w/o blocking is repair", "1", sh(s["review"]).rc)
    # hand-written (unstamped) is refused, then invalid after two tries
    write(T / "review/verdict.json", '{"decision":"approved","summary":"s","findings":[]}')
    write(T / "review_attempts", "0\n")
    r = sh(s["review"])
    ck("review: unstamped gets a repair", "1", r.rc)
    ck("review: repair says submit tool", "1", count("submit tool", r.out))
    ck("review: second unstamped is invalid", "invalid", route(sh(s["review"]).out, "review_decision"))

    # --- the five decision-enum gates ---
    for name, key, dec, out in (("standards", "standards_status", "approve", "extra/standards.json"),
                                ("spec", "spec_status", "findings", "extra/spec.json"),
                                ("quality", "quality_status", "needs_human_review", "extra/quality.json"),
                                ("phase_standards", "standards_status", "approve", "review/standards.json"),
                                ("phase_spec", "spec_status", "findings", "review/spec.json")):
        vg_stage(); stamp(T / out, f'{{"decision":"{dec}","summary":"s","findings":[]}}')
        ck(f"{name}: {dec} routes", dec, route(sh(s[name]).out, key))
    # unstamped extra verdict is a repair, then invalid
    write(T / "extra/standards.json", '{"decision":"approve","summary":"s","findings":[]}')
    write(T / "standards_attempts", "0\n")
    r = sh(s["standards"])
    ck("standards: unstamped is repair", "1", r.rc)
    ck("standards: repair says submit", "1", count("submit tool", r.out))
    ck("standards: second unstamped invalid", "invalid", route(sh(s["standards"]).out, "standards_status"))
    # stale receipt (visit mismatch) is a repair, never the decision
    vg_stage(); stamp(T / "extra/standards.json", '{"decision":"approve","summary":"s","findings":[]}')
    write(T / ".io/stage.json", f'{{"workflow":"backlog","node":"review","visit":"{STALE}"}}')
    write(T / "standards_attempts", "0\n")
    ck("standards: stale receipt is repair", "1", sh(s["standards"]).rc)
    ck("standards: stale never routes approve", "0", count('"standards_status":"approve"', sh(s["standards"]).out, pipe=True))


# ---------------------------------------------------------------------------
# implementer-gate read receipt — the reads-only form of C7 (ADR 0016 task 09). A stale
# served visit (or a missing required input) must not accept the stage's result: the
# repair-turn gates exit 1 on the first stale read, and the failure-routing gates
# (resolve_merge/ci_fix/rebase) route to their failure path instead (exit 0, never their
# success routing).
# ---------------------------------------------------------------------------
GRAPHS = {"G": GRAPH, "A": ARCH, "P": PR, "S": SHARED, "T": SHARED_TRIAGE}


def test_implementer_gate_read_receipt(ck, tmp_path):
    T = tmp_path / "rcneg"

    def fresh(*dirs):
        rm(T)
        for d in (".io",) + dirs:
            (T / d).mkdir(parents=True, exist_ok=True)

    def stale_rc(g, node):
        fresh()
        write(T / "g.sh", rebase(extract(GRAPHS[g], node), T))
        write(T / ".io/stage.json", f'{{"visit":"{FV}"}}')
        write(T / ".io/served.json", f'{{"visit":"{STALE}","inputs":{{}}}}')
        return sh(T / "g.sh", cwd=T).rc

    ck("decompose stale read is repair", "1", stale_rc("G", "decompose_gate"))
    ck("improve stale read is repair", "1", stale_rc("G", "improve_gate"))
    ck("extra stale read is repair", "1", stale_rc("G", "extra_gate"))
    ck("scan stale read is repair", "1", stale_rc("A", "scan_gate"))
    ck("review_fix stale read is repair", "1", stale_rc("S", "fix_gate"))
    ck("triage_gate stale read is repair", "1", stale_rc("T", "triage_gate"))

    # The repair hint after a receipt miss is C7's submit-tool message in every gate with a
    # repair turn; a stamped but invalid contract keeps the gate's own message.
    def miss_output(g, node):
        fresh("arch", "review", "extra")
        write(T / "g.sh", rebase(extract(GRAPHS[g], node), T))
        write(T / ".io/stage.json", f'{{"visit":"{FV}"}}')
        return sh(T / "g.sh", cwd=T).out

    for g, node in (("G", "decompose_gate"), ("G", "improve_gate"), ("G", "extra_gate"), ("A", "scan_gate"),
                    ("S", "fix_gate"), ("T", "improve_gate"), ("T", "triage_gate")):
        ck(f"{node} receipt miss says submit tool", "1", count("submit tool", miss_output(g, node), pipe=True))
    fresh()
    write(T / "g.sh", rebase(extract(GRAPH, "decompose_gate"), T))
    write(T / ".io/stage.json", f'{{"visit":"{FV}"}}')
    write(T / "decomposition.json", f'{{"status":"bogus","_io":{{"visit":"{FV}"}}}}')
    out = sh(T / "g.sh", cwd=T).out
    ck("decompose: stamped but invalid keeps its hint", "1", count("rewrite it exactly per the contract", out))
    ck("decompose: stamped but invalid not submit hint", "0", count("submit tool", out))


# ---------------------------------------------------------------------------
# receipt routing on the failure-routing gates (real git). resolve_merge_gate, rebase_gate
# and ci_fix_gate have no repair edge, so a stale or incomplete receipt must ROUTE to the
# failure path; an exit code alone proves nothing, because the success path exits 0 as
# well. rebase_gate's counter is the tier ladder: a receipt miss on tier 1 must reach
# tier 2 (rebase_attempts=1), not exit 1, which would take the unconditional edge to
# entry_failed.
# ---------------------------------------------------------------------------
def test_receipt_routing(ck, tmp_path):
    T = tmp_path / "rcroute"
    up, wt = T / "up", T / "wt"

    def rc_repo():
        rm(T)
        for d in ("up", ".io", "review"):
            (T / d).mkdir(parents=True)
        git("-C", up, "init", "-q", "-b", "main", ".")
        git("-C", up, "config", "user.email", "t@t"); git("-C", up, "config", "user.name", "t")
        write(up / "a.txt", "a\n"); git("-C", up, "add", "-A"); git("-C", up, "commit", "-qm", "init")
        git("clone", "-q", up, wt)
        write(T / "base_ref", "main\n")
        head = git("-C", wt, "rev-parse", "HEAD")
        write(T / "pre_rebase_sha", head + "\n"); write(T / "pre_merge_sha", head + "\n")
        write(T / ".io/stage.json", f'{{"visit":"{FV}"}}')

    state = {}

    def rc_gate(graph, node):
        write(T / "g.sh", rebase(extract(graph, node), T))
        r = sh(T / "g.sh", cwd=wt)
        state.update(rc=r.rc, j=lastjson(r.out))

    def rc_served(visit, parts, served):
        write(T / ".io/served.json", jq_n('''{visit:$v, inputs:{
      "pre_merge_sha":{status:"ok",parts:1,served:[1]},
      "current_task.json":{status:"ok",parts:1,served:[1]},
      "issue.json":{status:"ok",parts:$p,served:$s}}}''', "--arg", "v", visit, "--argjson", "p", parts,
                                          "--argjson", "s", served, compact=False) + "\n")

    ctx = lambda k: jq(f".context_updates.{k}", state["j"])

    rc_repo(); rc_served(FV, "1", "[1]")
    rc_gate(GRAPH, "resolve_merge_gate")
    ck("resolve_merge: complete read, clean merge", "true", ctx("merge_ok"))
    ck("resolve_merge: a task merge returns to the task", "task", ctx("merge_return"))
    rc_repo(); rc_served(FV, "1", "[1]"); write(T / "final_merge_conflict", "a.txt\n")
    rc_gate(GRAPH, "resolve_merge_gate")
    ck("resolve_merge: a final merge returns to final_merge", "pr", ctx("merge_return"))
    rc_repo(); rc_served(STALE, "1", "[1]"); write(T / "final_merge_conflict", "a.txt\n")
    rc_gate(GRAPH, "resolve_merge_gate")
    ck("resolve_merge: merge_return on the failure branch too", "pr", ctx("merge_return"))
    rc_repo(); rc_served(STALE, "1", "[1]")
    rc_gate(GRAPH, "resolve_merge_gate")
    ck("resolve_merge: stale served visit fails", "false", ctx("merge_ok"))
    ck("resolve_merge: stale is attempt 1", "1", ctx("merge_attempts"))
    rc_repo(); rc_served(FV, "2", "[1]")
    rc_gate(GRAPH, "resolve_merge_gate")
    ck("resolve_merge: incomplete input fails", "false", ctx("merge_ok"))

    rc_repo(); write(T / "rebase_result.json", f'{{"_io":{{"visit":"{FV}"}}}}\n')
    rc_gate(PR, "rebase_gate")
    ck("rebase: valid receipt, clean rebase", "true", ctx("rebase_ok"))
    ck("rebase: ok records the rebased head", git("-C", wt, "rev-parse", "HEAD"), cat(T / "run_base_sha"))
    rc_repo(); write(T / "rebase_result.json", f'{{"_io":{{"visit":"{STALE}"}}}}\n')
    rc_gate(PR, "rebase_gate")
    ck("rebase: stale receipt exits 0 (routes)", "0", state["rc"])
    ck("rebase: stale receipt is not ok", "false", ctx("rebase_ok"))
    ck("rebase: stale receipt escalates to tier 2", "1", ctx("rebase_attempts"))
    ck("rebase: reason names the submit tool", "1", count("submit tool", file=T / "needs_human_reason"))
    write(T / "rebase_attempts", "1\n"); rm(T / "rebase_result.json")
    rc_gate(PR, "rebase_gate")
    ck("rebase: tier-2 receipt miss ends the ladder", "2", ctx("rebase_attempts"))

    rc_repo()
    rc_gate(SHARED, "ci_fix_gate")
    ck("ci_fix: no receipt routes not ok", "false", ctx("ci_fix_ok"))


# ---------------------------------------------------------------------------
# breaker limits (issue #7): the run-wide failure-signature breaker must sit above the
# number of times one gate can fail in a run, or it ends the run with no PR.
# ---------------------------------------------------------------------------
def test_breaker_limits(ck):
    ck("backlog: signature limit set", "1", count("^        loop_restart_signature_limit=100,$", file=GRAPH))
    ck("pr-review: signature limit set", "1", count("^        loop_restart_signature_limit=20,$", file=PR))
