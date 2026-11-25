"""The Stage I/O receipts (ADR 0016): the real submit under backlog's generated manifest, the
reviewer gates' C7 check, the reads-only receipt on the implementer gates, and receipt
routing on the failure-routing gates. Plus the breaker limits the graphs set."""

import shutil

import pytest

from harness import (ARCH, GRAPH, ORIG_PATH, PR, REPO_ROOT, SHARED, SHARED_TRIAGE, WF, cat, count, extract,
                     git, jq, jq_n, lastjson, present, rebase, rm, run, sh, stage, write)

FV = "fac3fac3fac3fac3fac3fac3fac3fac3"
STALE = "ffffffffffffffffffffffffffffffff"
FIX_DIR = REPO_ROOT / "ops/fabro-io/tests/fixtures"


# ---------------------------------------------------------------------------
# improve end to end — DELETED 2026-11-23. Its subject left the repository: ADR 0030
# removed backlog's `improve` stage (and with it `improve_gate` and the `improve`
# manifest stage) on 2026-10-21, and the test then silently skipped everywhere it ran,
# because no `fabro-io` is on this Mac's PATH (`make check` never saw it). Building the
# binary for #92 put it on PATH and the test failed on the manifest's answer, "no stage
# 'improve'". What it still proved is proved for a live stage by
# `test_refute_hygiene_end_to_end` below: the REAL `fabro-io submit` under backlog's
# GENERATED manifest, a refusal, an acceptance and the receipt.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# refute end to end — the REAL fabro-io `submit`, under backlog's GENERATED manifest (stage
# `review_merge.refute`), on the diff-hygiene rulings (#92, plan decision 9). `hygiene` is a
# required array and may be `[]`: a result that leaves it out is refused in the Refuter's own
# session, so a hit is never unruled by accident. `submit` also refuses a ruling on a counter
# nobody may rule on, and a `tampering` with no scenario. It accepts a `legitimate` ruling
# whatever its basis: whether the evidence holds is the gate's check, which makes a failed one
# `unsure`. The two refute fixtures the gate tests call valid pass the same schema. Needs
# fabro-io on PATH; skipped otherwise.
# ---------------------------------------------------------------------------
def test_refute_hygiene_end_to_end(ck, tmp_path):
    if not shutil.which("fabro-io", path=ORIG_PATH):
        pytest.skip("no fabro-io on PATH; present in profile images")
    T = tmp_path / "rhe2e"
    T.mkdir()
    manifest = ""
    for line in (WF / "backlog/workflow.toml").read_text().split("\n"):
        if line.startswith("FABRO_IO_MANIFEST"):
            manifest = rebase(line[line.index("{"):line.rindex("}") + 1], T)
            break
    ck("refute hygiene e2e: manifest extracted", "1", "1" if manifest else "0")
    env = {"FABRO_IO_ROOT": T, "FABRO_IO_MANIFEST": manifest}
    # the stage's six required inputs, each one page
    write(T / "review/refute_issues.json", '[{"number":31,"title":"cap the fee","body":"cap the fee at five percent"}]\n')
    write(T / "pr.json", '{"number":12,"title":"cap the fee","body":"closes #31"}\n')
    write(T / "review/refute_diff.patch", "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n-    assert net(50, 0) == 50\n")
    write(T / "review/refute_omitted.txt", "")
    write(T / "review/hygiene.json", '{"hits":[],"cleared":[]}\n')
    write(T / "review/refute_validation.md", "final passed on this tree\n")
    run(["fabro-io", "stage"], env={**env, "FABRO_WORKFLOW": "backlog", "FABRO_NODE_ID": "review_merge.refute"})
    run(["fabro-io", "inputs"], env=env)
    out = T / "review/refute.json"

    def rule(counter, ruling, basis, scenario):
        # one ruling, as compact JSON
        return jq_n('{counter:$c, file:"a/tests/test_phone.py", ruling:$r, basis:$b, evidence:"", scenario:$s}',
                    "--arg", "c", counter, "--arg", "r", ruling, "--arg", "b", basis, "--arg", "s", scenario)

    def result(hygiene=None):
        # a passing result, with the given `hygiene` array (JSON text) or without the key
        doc = ('{summary:"s", criteria:[{criterion:"login with valid credentials succeeds", status:"met", '
               'evidence:"auth.py:12"}], defects:[]')
        return jq_n(doc + (", hygiene:$h}" if hygiene is not None else "}"),
                    *(["--argjson", "h", hygiene] if hygiene is not None else []))

    def submit(doc):
        write(T / "result.json", doc + "\n")
        return run(["fabro-io", "submit", "--file", T / "result.json"], env=env)

    # 1. No `hygiene` at all: refused, and the message says what is missing. (`submit` names a
    #    missing root property in its message and a field inside the list by its pointer.)
    r = submit(result())
    ck("refute hygiene e2e: no hygiene is refused with exit 4", "4", r.rc)
    ck("refute hygiene e2e: the refusal names hygiene", "1", count('"hygiene" is a required property', r.out))
    # 2. `tampering` with an empty scenario: refused, the message names the field.
    r = submit(result("[" + rule("asserts_removed", "tampering", "none", "") + "]"))
    ck("refute hygiene e2e: tampering with no scenario refused", "4", r.rc)
    ck("refute hygiene e2e: the refusal points at the scenario", "1", count("^/hygiene/0/scenario: ", r.out))
    # 3. A ruling on a counter nobody may rule on: refused, the message names the counter.
    r = submit(result("[" + rule("skips_added", "legitimate", "requirement", "") + "]"))
    ck("refute hygiene e2e: a skips_added ruling refused", "4", r.rc)
    ck("refute hygiene e2e: the refusal points at the counter", "1", count("^/hygiene/0/counter: ", r.out))
    # A refused result leaves no file, so the gate sees that the Refuter submitted nothing.
    ck("refute hygiene e2e: a refused result writes nothing", "absent", present(out))
    # 4. `legitimate` with basis `none`: the schema passes it; the gate's check makes it `unsure`.
    r = submit(result("[" + rule("tests_deleted", "legitimate", "none", "") + "]"))
    ck("refute hygiene e2e: legitimate with basis none accepted", "0", r.rc)
    # 5. An empty list is a valid result, written with its receipt.
    r = submit(result("[]"))
    ck("refute hygiene e2e: empty hygiene accepted", "0", r.rc)
    ck("refute hygiene e2e: written", "written: " + str(out), r.out)
    ck("refute hygiene e2e: receipt stage", "review_merge.refute", jq("._io.stage", file=out))
    ck("refute hygiene e2e: receipt visit", jq(".visit", file=T / ".io/stage.json"), jq("._io.visit", file=out))
    ck("refute hygiene e2e: receipt hashes every input", "diff hygiene issues omitted pr validation",
       jq("._io.inputs | keys | join(\" \")", file=out))
    ck("refute hygiene e2e: hygiene written as an empty list", "true", jq(".hygiene == []", file=out))
    # 6. The two refute fixtures `test_refute_gate` calls valid pass the same schema. `_io` is
    #    the server's stamp, so it is cut before submit sees them, as it is for a model's call.
    for name in ("refute-pass.json", "refute-fail.json"):
        ck(f"refute hygiene e2e: {name} passes the schema", "0",
           submit(jq("del(._io)", file=FIX_DIR / name, raw=False)).rc)


# ---------------------------------------------------------------------------
# ralph-v2's Task I/O is Sealed where task 06 says (docs/plans/ralph-v2 C3): an Iteration must
# not read the review state written after it, the Refuter must not read the hosted reviewers'
# results, which are fresh when it runs in v2, and the per-Task planner (task 07) reads
# neither the Task list nor the review state. The REAL `fabro-io guard` under
# ralph-v2's GENERATED manifest, with the committed `/tmp/fabro` paths, because the hook
# compares the tool's path with the sealed entry. Needs fabro-io on PATH; skipped otherwise.
# ---------------------------------------------------------------------------
def test_ralph_v2_task_io_sealed(ck, tmp_path):
    if not shutil.which("fabro-io", path=ORIG_PATH):
        pytest.skip("no fabro-io on PATH; present in profile images")
    T = tmp_path / "v2sealed"
    (T / ".io").mkdir(parents=True)
    manifest = next(line[line.index("{"):line.rindex("}") + 1]
                    for line in (WF / "ralph-v2/workflow.toml").read_text().split("\n")
                    if line.startswith("FABRO_IO_MANIFEST"))

    def guard(node, path):
        write(T / ".io/stage.json", f'{{"workflow":"ralph-v2","node":"{node}","visit":"{FV}"}}')
        e = {"FABRO_IO_ROOT": T, "FABRO_IO_MANIFEST": manifest,
             "FABRO_HOOK_CONTEXT": f'{{"tool_input":{{"path":"{path}"}}}}'}
        return run(["fabro-io", "guard"], env=e)

    for node, path in (("iterate_t1", "/tmp/fabro/task_review/state/findings/t1.json"),
                       ("iterate_t1", "/tmp/fabro/task_plans/t1.json"),
                       ("refute", "/tmp/fabro/review/standards.json"),
                       ("refute", "/tmp/fabro/task_plan.json"),
                       ("task_plan", "/tmp/fabro/tasks.json"),
                       ("task_plan", "/tmp/fabro/task_plans/t1.json"),
                       ("task_plan", "/tmp/fabro/task_review/state/findings/t1.json"),
                       ("task_plan", "/tmp/fabro/feedback/rework.md")):
        r = guard(node, path)
        ck(f"v2 sealed: {node} blocks {path}", "2", r.rc)
        ck(f"v2 sealed: {node} says why", "1", count("is sealed for this stage", r.out))
    # The Iteration still reads its own Task and writes its own result, the Refuter still
    # reads its counters, and the planner reads its Task and its dossier and writes its plan:
    # a guard that blocked those would stop every visit.
    for node, path in (("iterate_t1", "/tmp/fabro/current_task.json"),
                       ("iterate_t1", "/tmp/fabro/iteration.json"),
                       ("refute", "/tmp/fabro/review/hygiene.json"),
                       ("task_plan", "/tmp/fabro/current_task.json"),
                       ("task_plan", "/tmp/fabro/task-context.md"),
                       ("task_plan", "/tmp/fabro/task_plan.json")):
        ck(f"v2 sealed: {node} still reads {path}", "0", guard(node, path).rc)


# ---------------------------------------------------------------------------
# the rulings file is Sealed (#92): `refute_gate` writes /tmp/fabro/review/hygiene_rulings.json
# after the Refuter's visit, so the Refuter never sees an earlier round's rulings, and neither
# the fix turn nor ralph's reviewers do. The REAL `fabro-io guard` under each GENERATED
# manifest, with the committed `/tmp/fabro` paths, because the hook compares the tool's path
# with the sealed entry. Needs fabro-io on PATH; skipped otherwise.
# ---------------------------------------------------------------------------
def test_hygiene_rulings_sealed(ck, tmp_path):
    if not shutil.which("fabro-io", path=ORIG_PATH):
        pytest.skip("no fabro-io on PATH; present in profile images")
    T = tmp_path / "hrsealed"
    (T / ".io").mkdir(parents=True)

    def guard(workflow, node, path):
        manifest = next(line[line.index("{"):line.rindex("}") + 1]
                        for line in (WF / workflow / "workflow.toml").read_text().split("\n")
                        if line.startswith("FABRO_IO_MANIFEST"))
        write(T / ".io/stage.json", f'{{"workflow":"{workflow}","node":"{node}","visit":"{FV}"}}')
        e = {"FABRO_IO_ROOT": T, "FABRO_IO_MANIFEST": manifest,
             "FABRO_HOOK_CONTEXT": f'{{"tool_input":{{"path":"{path}"}}}}'}
        return run(["fabro-io", "guard"], env=e)

    rulings = "/tmp/fabro/review/hygiene_rulings.json"
    sealed = (("backlog", "review_merge.refute"), ("backlog", "review_merge.refute_fix"),
              ("ralph", "refute"), ("ralph", "standards"), ("ralph", "spec"),
              ("ralph", "local_standards"), ("ralph", "local_spec"),
              ("ralph-v2", "refute"), ("ralph-v2", "standards"), ("ralph-v2", "spec"),
              ("ralph-v2", "local_standards"), ("ralph-v2", "local_spec"))
    for workflow, node in sealed:
        r = guard(workflow, node, rulings)
        ck(f"rulings sealed: {workflow} {node} blocks the read", "2", r.rc)
        ck(f"rulings sealed: {workflow} {node} says why", "1", count("is sealed for this stage", r.out))
    # The Refuter still reads its counters and writes its own result.
    ck("rulings sealed: refute reads the counters", "0", guard("backlog", "review_merge.refute", "/tmp/fabro/review/hygiene.json").rc)
    ck("rulings sealed: refute writes its result", "0", guard("backlog", "review_merge.refute", "/tmp/fabro/review/refute.json").rc)


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
