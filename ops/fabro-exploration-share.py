#!/usr/bin/env python3
"""Share of a run's tool calls, input tokens and turn time spent exploring the checkout.

Merges the two throwaway ADR 0014 scripts (formerly .scratch/b4-code-context/analyze.py
and extra.py) into one checked-in tool so that the baseline and the post-deploy result
are measured with the same code, and so the B1 scorecard can reuse it.

Fetch loop (documented; the script itself never ssh's anywhere):

    # fabro 0.362 has no run-listing verb (`fabro run list` parses `list` as a workflow
    # name), so list runs through the API:
    ssh andrew@10.10.0.32 'T=$(docker exec fabro-fabro-1 cat /storage/server.dev-token); \
        curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:32276/api/v1/runs?limit=100"'
    # .data[] | {id, workflow.slug, lifecycle.status.kind, timestamps.created_at}
    # for each completed run id:
    ssh andrew@10.10.0.32 "cd ~/fabro && docker compose exec -T fabro fabro events <RUN> --json" \
        > ev/<RUN>.jsonl

Input is a file, or a directory of files, each produced by `fabro events <RUN> --json`.

Usage:
    ops/fabro-exploration-share.py [--since <ISO date>] [--workflow <slug>] <file-or-dir> [<file-or-dir> ...]

Event field paths (verified on fabro 0.362.0-nightly.0):
    agent.tool.started    -> properties.event.ToolCallStarted.{tool_name, arguments}
    agent.tool.completed  -> properties.event.ToolCallCompleted.output_bytes_retained
    agent.llm.started     -> properties.session_id, ts
    agent.message         -> properties.event.AssistantMessage.usage.tokens.{input, cache_read}
    agent.loop.detected   -> (per stage)
    stage.completed       -> properties.timing.{wall_time_ms, inference_time_ms, tool_time_ms}
    run.created           -> properties.workflow_slug, ts
    top level             -> node_id, stage_id, session_id, ts
"""
import argparse
import collections
import json
import os
import random
import re
import statistics
import sys
from datetime import datetime


def ts(s):
    s = s.rstrip("Z")
    if "." in s:
        a, b = s.split("."); s = a + "." + b[:6]
    return datetime.fromisoformat(s).timestamp()


SEARCH = {"rg", "grep", "egrep", "find", "ls", "cat", "sed", "head", "tail", "wc",
          "tree", "awk", "fd", "file", "stat", "less", "nl", "sort", "uniq", "cut",
          "jq", "diff"}
TEST = re.compile(
    r"(pytest|npm |npx |pnpm|yarn|vitest|jest|cargo|make\b|ruff|mypy|tsc\b|eslint|ci\.sh|setup\.sh|alembic|uv run|uv sync|pip |prettier|python3? -m (pytest|mypy|ruff|compileall)|playwright|go test|node )")


def first_cmd(c):
    c = c.strip()
    # strip leading cd X && / env assignments
    while True:
        m = re.match(r"^(cd\s+\S+\s*(&&|;)\s*)", c)
        if m:
            c = c[m.end():]; continue
        m = re.match(r"^([A-Z_][A-Z0-9_]*=\S*\s+)", c)
        if m:
            c = c[m.end():]; continue
        break
    return c


def classify_shell(c):
    f = first_cmd(c)
    tok = f.split()[0] if f.split() else ""
    tok = tok.split("/")[-1]
    if tok == "fabro-code":
        return "shell:fabro-code"
    if TEST.search(f):
        return "shell:test/build"
    if tok == "git":
        sub = f.split()[1] if len(f.split()) > 1 else ""
        if sub == "grep":
            return "shell:search"
        return "shell:git"
    if tok in ("rg", "grep", "egrep", "find", "fd", "tree", "ls"):
        return "shell:search"
    if tok in SEARCH:
        return "shell:read"  # cat/sed/head/tail/wc/awk/jq...
    if tok in ("python", "python3"):
        return "shell:python"
    return "shell:other"


def category(name, args):
    if name == "read_file":
        p = args.get("file_path", "")
        return "read_file:contract" if p.startswith("/tmp/fabro") else "read_file:repo"
    if name in ("grep", "glob"):
        return name
    if name == "shell":
        return classify_shell(args.get("command", ""))
    return name


EXPLORE = {"read_file:repo", "grep", "glob", "shell:search", "shell:read"}

# The shell-category columns, in the order they print. fabro-code gets its own column so
# the post-deploy runs show it, but it is deliberately NOT in EXPLORE: it is the mechanism
# that replaces exploration, so counting it as exploration would hide the benefit.
COLS = ["read_file:repo", "read_file:contract", "grep", "glob", "shell:fabro-code",
        "shell:search", "shell:read", "shell:git", "shell:test/build", "shell:python",
        "shell:other", "edit_file", "write_file"]


def norm(n):
    return n.split(".")[-1] if n else n


# --- input-reads section (ADR 0016, docs/stage-io task 01) ---
# An INPUT READ is a tool call that reads a stage input under /tmp/fabro/: a read_file
# whose arguments name a /tmp/fabro path, or a shell whose first command is one of these
# read verbs and whose arguments name a /tmp/fabro path. lead_turns / lead_secs columns are
# counted from agent.llm.started (one LLM call = one turn).
SREAD = {"cat", "head", "tail", "sed", "jq", "wc", "ls"}
LINENUM = re.compile(r"^\s*(\d+)\s*\| ")
# The Refuter's Sealed list, from _shared/review-merge/prompts/refute.md.j2 before task 07
# (task 07 moves it to the Stage manifest; this list is the baseline source of truth).
REFUTE_SEALED = [
    "/tmp/fabro/review/standards.json",
    "/tmp/fabro/review/spec.json",
    "/tmp/fabro/review/fix_result.json",
    "/tmp/fabro/review/ci_fix_result.json",
    "/tmp/fabro/feedback/",
]


def io_node(n):
    """Node id for the input-reads section: strip the @visit suffix, keep the import prefix."""
    return n.split("@")[0] if n else n


def is_input_read(name, args):
    astr = json.dumps(args)
    if name == "read_file":
        return "/tmp/fabro/" in astr
    if name == "shell":
        cmd = args.get("command", "")
        f = first_cmd(cmd)
        tok = f.split()[0].split("/")[-1] if f.split() else ""
        return tok in SREAD and "/tmp/fabro/" in astr
    return False


def is_sealed(astr):
    return any(s in astr for s in REFUTE_SEALED)


def med(a):
    return statistics.median(a) if a else 0


def p90(a):
    if not a:
        return 0
    s = sorted(a)
    return s[int(0.90 * (len(s) - 1))]


def iter_event_files(paths):
    """Yield event-log filenames from files or directories."""
    for p in paths:
        if os.path.isdir(p):
            names = sorted(os.listdir(p))
            for n in names:
                if n.endswith((".jsonl", ".json")):
                    yield os.path.join(p, n)
        else:
            yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="ISO_DATE",
                    help="Only runs created at or after this ISO datetime.")
    ap.add_argument("--workflow", metavar="SLUG",
                    help="Only runs whose workflow_slug matches this exact slug.")
    ap.add_argument("paths", nargs="+", help="Event-log file(s) or directory(ies).")
    args = ap.parse_args()
    since_ts = ts(args.since) if args.since else None

    runs = 0
    tool_counts = collections.defaultdict(collections.Counter)
    cat_bytes = collections.defaultdict(collections.Counter)
    cat_resident = collections.defaultdict(collections.Counter)  # bytes x later-llm-calls
    input_tok = collections.Counter()
    llm_calls = collections.Counter()
    loops = collections.Counter()
    wall = collections.defaultdict(list)
    inf = collections.defaultdict(list)
    tooltm = collections.defaultdict(list)
    explore_turn_time = collections.Counter()
    turn_time = collections.Counter()
    reads = collections.Counter()
    visits = collections.defaultdict(set)   # node -> {(run file, stage_id)}
    samples = []
    # dossier presence: improve sessions that ended `ready` vs those that also wrote
    # /tmp/fabro/task-context.md
    improve_ready = collections.Counter()
    improve_with_dossier = collections.Counter()
    # input-reads state (task 01)
    io_sess_node = {}
    io_sess_llm = collections.defaultdict(list)
    io_sess_tools = collections.defaultdict(list)  # (ts, is_input_read)
    input_sizes = collections.defaultdict(list)    # /tmp/fabro path -> [bytes of read output]
    io_partial = collections.Counter()
    io_capped = collections.Counter()
    io_sealed = collections.Counter()
    io_calls_c = collections.Counter()
    gr_total_turns = 0
    gr_lead_turns = 0
    gr_lead_secs = 0.0
    gr_stage_wall_ms = 0

    for fn in iter_event_files(args.paths):
        evs = [json.loads(l) for l in open(fn)]
        evs.sort(key=lambda e: e["ts"])
        if not evs:
            continue
        run_ts = None
        slug = None
        for e in evs:
            if e["event"] == "run.created":
                run_ts = ts(e["ts"])
                slug = (e.get("properties", {}) or {}).get("workflow_slug")
                break
        if run_ts is not None and since_ts is not None and run_ts < since_ts:
            continue
        if args.workflow and slug != args.workflow:
            continue
        # skip run-created-only files that had no timestamps and remain unparsed
        runs += 1

        started = {}
        sess_llm = collections.defaultdict(list)   # session -> [ts of llm.started]
        sess_tools = collections.defaultdict(list)  # session -> [(ts, node, cat, bytes)]
        sess_node = {}
        sess_dossier = collections.defaultdict(bool)   # session wrote task-context.md
        sess_ready = collections.defaultdict(bool)     # session wrote improve_result ready

        for e in evs:
            ev = e["event"]; node = norm(e.get("node_id")); sid = e.get("session_id")
            p = e.get("properties", {})
            if ev == "agent.tool.started":
                t = p["event"]["ToolCallStarted"]
                a = t.get("arguments") or {}
                c = category(t["tool_name"], a)
                started[t["tool_call_id"]] = (c, a, t["tool_name"])
                tool_counts[node][c] += 1
                visits[node].add((fn, e.get("stage_id")))
                if t["tool_name"] == "read_file":
                    reads[(fn, e["stage_id"], a.get("file_path"), node)] += 1
                if c in ("grep", "glob", "shell:search"):
                    samples.append((node, t["tool_name"], json.dumps(a)[:220]))
                if t["tool_name"] == "write_file":
                    fp = a.get("file_path", "")
                    if node == "improve" and fp.endswith("improve_result.json"):
                        content = a.get("content", "")
                        if isinstance(content, str):
                            try:
                                obj = json.loads(content)
                                if isinstance(obj, dict) and obj.get("disposition") == "ready":
                                    sess_ready[sid] = True
                            except (ValueError, TypeError):
                                pass
                    if fp.endswith("task-context.md"):
                        sess_dossier[sid] = True
            elif ev == "agent.tool.completed":
                t = p["event"]["ToolCallCompleted"]
                c = started.get(t["tool_call_id"], ("?",))[0]
                b = t.get("output_bytes_retained") or 0
                cat_bytes[node][c] += b
                if sid:
                    sess_tools[sid].append((ts(e["ts"]), node, c, b))
                # input-reads tracking
                ionode = io_sess_node.get(sid)
                st = started.get(t["tool_call_id"], ("?", {}, ""))
                tname = st[2] if len(st) > 2 else ""
                targs = st[1] if len(st) > 1 else {}
                if tname in ("mcp__io__inputs", "mcp__io__submit"):
                    io_calls_c[ionode] += 1
                out = t.get("output") or ""
                if is_input_read(tname, targs):
                    osz = b or len(out.encode())
                    io_sess_tools[sid].append((ts(e["ts"]), True))
                    if tname == "read_file":
                        fp = targs.get("file_path", "")
                        if fp:
                            input_sizes[fp].append(osz)
                        if "limit" in targs or "offset" in targs:
                            io_partial[ionode] += 1
                        last = 0
                        for ln in out.splitlines():
                            m = LINENUM.match(ln)
                            if m:
                                last = int(m.group(1))
                        if last >= 2000:
                            io_capped[ionode] += 1
                        if ionode == "review_merge.refute" and is_sealed(json.dumps(targs)):
                            io_sealed[ionode] += 1
                else:
                    io_sess_tools[sid].append((ts(e["ts"]), False))
            elif ev == "agent.llm.started":
                sess_llm[sid].append(ts(e["ts"]))
                llm_calls[node] += 1
                sess_node[sid] = node
                io_sess_node[sid] = io_node(e["node_id"])
                io_sess_llm[sid].append(ts(e["ts"]))
            elif ev == "agent.message":
                u = p["event"]["AssistantMessage"].get("usage", {}).get("tokens", {})
                input_tok[node] += (u.get("input") or 0) + (u.get("cache_read") or 0)
            elif ev == "agent.loop.detected":
                loops[node] += 1
            elif ev == "stage.completed":
                tm = p.get("timing", {})
                if tm.get("wall_time_ms"):
                    gr_stage_wall_ms += tm["wall_time_ms"]
                if tm.get("inference_time_ms", 0) > 0:
                    wall[node].append(tm["wall_time_ms"])
                    inf[node].append(tm["inference_time_ms"])
                    tooltm[node].append(tm["tool_time_ms"])

        # resident weighting + turn classification
        for sid, tools in sess_tools.items():
            L = sess_llm.get(sid, [])
            for (t0, node, c, b) in tools:
                later = sum(1 for x in L if x > t0)
                cat_resident[node][c] += b * later
            for i, s in enumerate(L):
                e_ = L[i + 1] if i + 1 < len(L) else None
                if e_ is None:
                    continue
                cs = [c for (t0, n, c, b) in tools if s <= t0 < e_]
                node = sess_node[sid]
                turn_time[node] += e_ - s
                if cs and all(c in EXPLORE for c in cs):
                    explore_turn_time[node] += e_ - s
            if sess_ready[sid]:
                improve_ready[node] += 1
                if sess_dossier[sid]:
                    improve_with_dossier[node] += 1

    # --- input-reads post-processing (task 01): lead turns per session ---
    lead_counts = collections.defaultdict(list)
    lead_secs_list = collections.defaultdict(list)
    ir_per_sess = collections.defaultdict(list)
    io_sessions = collections.Counter()
    for sid0, L in io_sess_llm.items():
        node0 = io_sess_node.get(sid0)
        if node0 is None:
            continue
        L = sorted(L)
        tools = sorted(io_sess_tools[sid0])  # (ts, bool)
        turn_bools = [[] for _ in L]
        ti = 0
        tl = len(tools)
        for j in range(len(L)):
            lo = L[j]
            hi = L[j + 1] if j + 1 < len(L) else float("inf")
            while ti < tl and tools[ti][0] < hi:
                if tools[ti][0] >= lo:
                    turn_bools[j].append(tools[ti][1])
                ti += 1
        lead = 0
        for tb in turn_bools:
            if tb and all(tb):
                lead += 1
            else:
                break
        io_sessions[node0] += 1
        lead_counts[node0].append(lead)
        gr_lead_turns += lead
        gr_total_turns += len(L)
        ir_per_sess[node0].append(sum(1 for _, b in tools if b))
        ls = 0.0
        if lead:
            gr_lead_secs_ = (L[lead] - L[0]) if lead < len(L) else (L[-1] - L[0])
            ls = gr_lead_secs_
            for i in range(lead):
                if i + 1 < len(L):
                    gr_lead_secs += L[i + 1] - L[i]
        lead_secs_list[node0].append(ls)

    print(f"runs analysed: {runs}\n")

    nodes = sorted(tool_counts, key=lambda n: -sum(tool_counts[n].values()))
    print("## 1. tool calls per stage")
    print("| stage | total | " + " | ".join(COLS) + " |")
    tot = collections.Counter()
    for n in nodes:
        tc = tool_counts[n]; tot.update(tc)
        print(f"| {n} | {sum(tc.values())} | " + " | ".join(str(tc[c]) for c in COLS) + " |")
    all_calls = sum(tot.values())
    expl_cols = [c for c in COLS if c in EXPLORE]
    print(f"| ALL | {all_calls} | " + " | ".join(str(tot[c]) for c in COLS) + " |")
    expl_calls = sum(tot[c] for c in expl_cols)
    print(f"\nexploration = {expl_calls}/{all_calls} = {100*expl_calls/all_calls if all_calls else 0:.0f}% of tool calls")

    print("\n## 2. tool-result bytes and exploration share of input tokens")
    print("The resident estimate is: retained result bytes - 4 x the number of later LLM")
    print("calls in the same session, summed; then compare with billed input tokens.")
    print("| stage | LLM calls | input tok (billed) | result KB | explore KB | explore % of result bytes | resident explore tok est | % of billed input |")
    T = collections.Counter()
    for n in nodes:
        cb = cat_bytes[n]; tb = sum(cb.values()); eb = sum(cb[c] for c in EXPLORE)
        res = sum(cat_resident[n][c] for c in EXPLORE) / 4
        it = input_tok[n]
        T["calls"] += llm_calls[n]; T["it"] += it; T["tb"] += tb; T["eb"] += eb; T["res"] += res
        print(f"| {n} | {llm_calls[n]} | {it:,} | {tb/1024:.0f} | {eb/1024:.0f} | {100*eb/tb if tb else 0:.0f}% | {res:,.0f} | {100*res/it if it else 0:.0f}% |")
    all_res = T["res"]; all_it = T["it"]
    print(f"| ALL | {T['calls']} | {T['it']:,} | {T['tb']/1024:.0f} | {T['eb']/1024:.0f} | {100*T['eb']/T['tb']:.0f}% | {T['res']:,.0f} | {100*T['res']/T['it'] if T['it'] else 0:.0f}% |")

    print("\n## 3. repeated reads (same path, same stage visit)")
    rep = collections.Counter(); visits_with = collections.Counter()
    for (fn, st, path, node), k in reads.items():
        if k > 1:
            rep[node] += k - 1; visits_with[node] += 1
    tr = collections.Counter()
    for (fn, st, path, node), k in reads.items():
        tr[node] += k
    for n in sorted(rep, key=lambda n: -rep[n]):
        print(f"| {n} | read_file calls {tr[n]} | redundant re-reads {rep[n]} ({100*rep[n]/tr[n] if tr[n] else 0:.0f}%) | distinct (visit,path) repeated {visits_with[n]} |")
    print("top offenders:")
    for (fn, st, path, node), k in sorted(reads.items(), key=lambda x: -x[1])[:15]:
        print(f"  {k}x  {node:14s} {st:18s} {fn.split('/')[-1][:12]}  {path}")

    print("\n## 4. agent.loop.detected per stage")
    print(dict(loops.most_common()))

    print("\n## 5. wall time per stage (agent stages)")
    print("| stage | visits | median wall min | total wall h | inference % | tool % | explore-turn % of turn time |")
    for n in sorted(wall, key=lambda n: -sum(wall[n])):
        w = wall[n]; tw = sum(w)
        print(f"| {n} | {len(w)} | {statistics.median(w)/60000:.1f} | {tw/3.6e6:.2f} | {100*sum(inf[n])/tw if tw else 0:.0f}% | {100*sum(tooltm[n])/tw if tw else 0:.0f}% | {100*explore_turn_time[n]/turn_time[n] if turn_time[n] else 0:.0f}% |")
    tt = sum(turn_time.values())
    print(f"ALL explore-turn share: {100*sum(explore_turn_time.values())/tt if tt else 0:.0f}%")

    print("\n## 5b. agent visits per stage (for data sufficiency)")
    print("| stage | visits | tool calls |")
    for n in sorted(visits, key=lambda n: -len(visits[n])):
        print(f"| {n} | {len(visits[n])} | {sum(tool_counts[n].values())} |")
    ok = len(visits["improve"]) >= 30 and len(visits["coder"]) >= 30
    print(f"task 08 threshold (30 improve and 30 coder visits): {'met' if ok else 'not met'}")

    print("\n## 5c. Task-dossier presence rate (improve sessions, disposition=ready)")
    ready_total = sum(improve_ready.values()); with_doss = sum(improve_with_dossier.values())
    print(f"| improve ready sessions: {ready_total} | wrote task-context.md: {with_doss} | rate: {100*with_doss/ready_total if ready_total else 0:.0f}% |")

    print("\n## 6. input reads (ADR 0016, task 01)")
    print("An input read is a read_file of a /tmp/fabro path, or a shell whose first command")
    print("is one of cat/head/tail/sed/jq/wc/ls naming a /tmp/fabro path. lead_turns is the")
    print("leading run of turns (LLM calls) in which every tool call is an input read.")
    print("| node | sessions | lead_turns med/p90 | lead_secs med/p90 | input_reads med | partial | capped | sealed | io_calls |")
    ir_nodes = sorted(io_sessions, key=lambda n: -io_sessions[n])
    for n in ir_nodes:
        lead = lead_counts[n]
        print(f"| {n} | {io_sessions[n]} | {med(lead)}/{p90(lead)} | {med(lead_secs_list[n]):.0f}/{p90(lead_secs_list[n]):.0f} "
              f"| {med(ir_per_sess[n])} | {io_partial[n]} | {io_capped[n]} | {io_sealed[n]} | {io_calls_c[n]} |")

    print("\n## 6b. input path sizes (bytes of read output; page budget evidence, Decision 4)")
    for fp, sizes in sorted(input_sizes.items(), key=lambda kv: -max(kv[1])):
        s = sorted(sizes)
        print(f"| {fp} | n={len(s)} | p50={s[len(s)//2]} | p90={s[int(0.90*(len(s)-1))]} | max={max(s)} |")

    print("\n## 6c. whole run")
    print(f"total turns {gr_total_turns}, lead turns {gr_lead_turns} "
          f"({100*gr_lead_turns/gr_total_turns if gr_total_turns else 0:.1f}%), "
          f"lead seconds {gr_lead_secs:.0f}, "
          f"share of stage wall {100*gr_lead_secs/(gr_stage_wall_ms/1000) if gr_stage_wall_ms else 0:.1f}%")

    print("\n## 7. sample search args")
    random.seed(7)
    for s in random.sample(samples, min(40, len(samples))):
        print(" ", s)


if __name__ == "__main__":
    sys.exit(main())
