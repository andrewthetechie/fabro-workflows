"""The ralph trial's per-run review tables (docs/ralph/13-reviewing-the-arms.md).

Runs inside the scheduler container, never on the Mac: `ops/fabro-trial-review.sh` pipes
this file to `docker exec -i fabro-scheduler python -I -`. It opens the scheduler's
database read-only and imports `fabro_scheduler.scorecard` for the two rules the
scorecard owns (`merged_by_workflow`, `held_bucket`), so a review and the scorecard
cannot disagree on what merged or why a PR was held. Standard library otherwise.

Usage: python -I - [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--db PATH] [--ids WORKFLOW]
Both dates are inclusive (UTC); a window holds the runs dispatched in it, as the
scorecard's does.
"""

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

from fabro_scheduler.scorecard import held_bucket, merged_by_workflow

# ADR 0029 task 12, step 6: the trial window starts here.
SWITCH = "2026-10-22T21:28:06+00:00"

# Hosted prices per million tokens: (input, cache read, cache write, output). Only models
# that bill per token and that fabro does not price go here; a row fabro priced
# (`cost_usd_micros`) is taken as fabro priced it. Kimi and z.ai are flat-rate plans and
# the coder boxes are local, so their marginal cost is $0. Source: ADR 0013, D6 amendment.
RATES = {
    ("openai", "gpt-6-luna"): (0.10, 0.01, 0.125, 0.50),
}
FLAT = {"kimi", "zai"}
BUDGET_USD = 100.0  # O3: total hosted spend a month

ITERATE = ("iterate_t1", "iterate_t2", "iterate_t3", "iterate_t4")
HOSTED_RALPH = ("plan", "refute", "standards", "spec")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default=SWITCH[:10])
    ap.add_argument("--to", dest="end", default=datetime.now(UTC).date().isoformat())
    ap.add_argument("--db", default="/data/scheduler.db")
    ap.add_argument("--ids", metavar="WORKFLOW", help="print only the window's run ids for WORKFLOW")
    a = ap.parse_args()
    start = datetime.combine(date.fromisoformat(a.start), datetime.min.time(), UTC)
    end = datetime.combine(date.fromisoformat(a.end), datetime.min.time(), UTC) + timedelta(days=1)

    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    bounds = (start.isoformat(), end.isoformat())
    # Issue runs only, as the scorecard reads them (`Store.scorecard_rows`): the PR doctor's
    # follow-up reviews are `target_kind = 'pr'` rows and get their own section at the end.
    history = c.execute(
        "SELECT * FROM run_history WHERE dispatched_at >= ? AND dispatched_at < ? "
        "AND target_kind = 'issue' ORDER BY dispatched_at",
        bounds).fetchall()
    reviews = c.execute(
        "SELECT * FROM run_history WHERE dispatched_at >= ? AND dispatched_at < ? "
        "AND target_kind = 'pr' ORDER BY dispatched_at",
        bounds).fetchall()
    if a.ids:
        for h in history:
            if h["workflow"] == a.ids:
                print(h["run_id"], f"{h['repo']}#{h['issue_number']}")
        return
    ids = [h["run_id"] for h in history]
    marks = ",".join("?" * len(ids)) or "NULL"
    summaries = {r["run_id"]: r for r in c.execute(f"SELECT * FROM run_summary WHERE run_id IN ({marks})", ids)}
    stages = defaultdict(dict)
    for r in c.execute(f"SELECT * FROM run_stage_stats WHERE run_id IN ({marks})", ids):
        stages[r["run_id"]][r["node_id"]] = r
    failovers = c.execute(f"SELECT * FROM run_failovers WHERE run_id IN ({marks})", ids).fetchall()
    usage = c.execute(f"SELECT * FROM run_model_usage WHERE run_id IN ({marks})", ids).fetchall()
    facts = {(r["repo"], r["pr_number"]): r for r in c.execute("SELECT * FROM pr_facts")}
    by_pr = defaultdict(list)
    for h in history:
        if h["pr_number"]:
            by_pr[(h["repo"], h["pr_number"])].append(h)

    wf = {h["run_id"]: h["workflow"] for h in history}
    now = min(datetime.now(UTC), end)
    days = max((now - start).total_seconds() / 86400, 1 / 24)

    print(f"# Trial review tables, {a.start} to {a.end}\n")
    pre = sum(1 for h in history if h["dispatched_at"] < SWITCH)
    print(f"Window: runs dispatched {start:%Y-%m-%d} to {end - timedelta(days=1):%Y-%m-%d} (UTC, inclusive), "
          f"{len(history)} runs. {pre} of them were dispatched before the switch ({SWITCH}).\n")

    # --- Runs ---------------------------------------------------------------------------
    print("## Runs\n")
    print("| arm | run | repo | issue | pool | dispatched | lease h | ending | PR | fate | held | op commits | refute | ralph: iter / visits / tasks |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    arm = defaultdict(lambda: Counter())
    lease_h = defaultdict(float)
    for h in history:
        s = summaries.get(h["run_id"])
        st = stages.get(h["run_id"], {})
        key = (h["repo"], h["pr_number"])
        fact = facts.get(key) if h["pr_number"] else None
        merged = bool(h["pr_number"]) and merged_by_workflow(by_pr[key], fact)
        if not h["pr_number"]:
            fate, held = "no PR", ""
        elif merged:
            fate, held = "merged (workflow)", ""
        else:
            fate = fact["state"] if fact else "not read"
            held = held_bucket(fact)
        ops = fact["operator_commits"] if fact else None
        lh = _hours(h["dispatched_at"], h["finished_at"])
        lease_h[h["workflow"]] += lh
        n = arm[h["workflow"]]
        n["runs"] += 1
        n["prs"] += bool(h["pr_number"])
        n["merged"] += merged
        n["merged_clean"] += merged and ops == 0
        refute = "".join("P" if v.get("verdict") == "pass" else "F" if v.get("verdict") == "fail" else "?"
                         for v in json.loads(s["refute"] or "[]")) if s else ""
        extra = ""
        if h["workflow"] == "ralph":
            iters = sum(st[n_]["visits"] for n_ in ITERATE if n_ in st)
            rungs = "/".join(str(st[n_]["visits"]) if n_ in st else "0" for n_ in ITERATE)
            visits = st["refute"]["visits"] if "refute" in st else 0
            src = json.loads(s["task_sources"] or "{}") if s else {}
            extra = f"{iters} ({rungs}) / {visits} / " + ", ".join(f"{k} {v}" for k, v in sorted(src.items()))
        print(f"| {h['workflow']} | {h['run_id'][-8:]} | {h['repo'].split('/')[1]} | {h['issue_number']} | "
              f"{h['coder_pool']} | {h['dispatched_at'][5:16]} | {lh:.1f} | {_ending(h, s)} | "
              f"{h['pr_number'] or ''} | {fate} | {held} | {'' if ops is None else ops} | {refute} | {extra} |")
    print()

    # --- O3 ------------------------------------------------------------------------------
    print("## O3: the primary metric and the box-hours guardrail\n")
    print("| arm | runs | PRs | merged by the workflow | of which no operator commit | per run | box-h | box-h per clean merge |")
    print("|---|---|---|---|---|---|---|---|")
    per_merge = {}
    for w in sorted(arm):
        n = arm[w]
        per_merge[w] = lease_h[w] / n["merged_clean"] if n["merged_clean"] else None
        print(f"| {w} | {n['runs']} | {n['prs']} | {n['merged']} | {n['merged_clean']} | "
              f"{n['merged_clean'] / n['runs']:.2f} | {lease_h[w]:.1f} | "
              f"{'—' if per_merge[w] is None else f'{per_merge[w]:.2f}'} |")
    if per_merge.get("ralph") and per_merge.get("backlog"):
        d = per_merge["ralph"] / per_merge["backlog"] - 1
        print(f"\nralph's box-hours per clean merge are {d:+.0%} against backlog's (the guardrail is +20%).")
    print()

    # --- ralph runs ----------------------------------------------------------------------
    # A stage's wall time counts only the attempt that finished, so a timed-out attempt
    # that was retried shows up under `other`, and `timeouts` says where it was.
    print("## ralph runs: where each lease went (minutes)\n")
    print("| run | lease | plan | iterations | check | final_check | refute | standards+spec | merge stage | learn | other | timeouts |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for h in history:
        if h["workflow"] != "ralph":
            continue
        st = stages.get(h["run_id"], {})
        m = lambda *ns: sum(st[n_]["wall_ms"] for n_ in ns if n_ in st) / 60000
        merge = m("merge_prep", "merge_gate", "watch_checks", "merge", "deliver", "remerge_base",
                  "resolve_base", "resolve_base_gate", "ci_task")
        parts = [m("plan"), m(*ITERATE), m("check"), m("final_check"), m("refute"), m("standards", "spec"),
                 merge, m("learn")]
        lease = _hours(h["dispatched_at"], h["finished_at"]) * 60
        timeouts = ", ".join(f"{n_} {r['timeouts']}" for n_, r in sorted(st.items()) if r["timeouts"])
        print(f"| {h['run_id'][-8:]} | {lease:.0f} | " + " | ".join(f"{p:.0f}" for p in parts)
              + f" | {lease - sum(parts):.0f} | {timeouts} |")
    print()

    # --- Flags --------------------------------------------------------------------------
    # What the per-run read should look at first. The first one is the 2026-10-23 bug:
    # watch_checks queued every PR on its first poll (docs/ralph/review-2026-10-23.md).
    print("## Flags: ralph runs to read first\n")
    flagged = 0
    for h in history:
        if h["workflow"] != "ralph":
            continue
        s = summaries.get(h["run_id"])
        st = stages.get(h["run_id"], {})
        flags = []
        if s is None:
            flags.append("not summarized yet")
        elif _ending(h, s) == "report_queued" and "watch_checks" in st and st["watch_checks"]["wall_ms"] < 60000:
            flags.append("queued with watch_checks under a minute: CI was never read")
        flags += [f"{n_} timed out {r['timeouts']}x" for n_, r in sorted(st.items()) if r["timeouts"]]
        if s is not None:
            iters = sum(st[n_]["visits"] for n_ in ITERATE if n_ in st)
            tasks = sum(json.loads(s["task_sources"] or "{}").values())
            if iters > tasks:
                flags.append(f"{iters - tasks} Iteration(s) more than Tasks: misses")
            if any(n_ in st for n_ in ITERATE[1:]):
                flags.append("a Task climbed the ladder")
            if s["loop_exhausted"]:
                flags.append("the loop ran out (O18)")
            if s["post_iterations"]:
                flags.append(f"{s['post_iterations']} post-PR Iteration(s)")
        if flags:
            flagged += 1
            print(f"- {h['run_id'][-8:]} ({h['repo'].split('/')[1]}#{h['issue_number']}): " + "; ".join(flags))
    if not flagged:
        print("- none")
    print()

    # --- Stage time per arm --------------------------------------------------------------
    print("## Stage time per arm: the ten longest nodes\n")
    for w in sorted(arm):
        tot = defaultdict(lambda: [0, 0, 0, 0])
        for rid, nodes in stages.items():
            if wf.get(rid) != w:
                continue
            for node, r in nodes.items():
                t = tot[node]
                t[0] += 1; t[1] += r["visits"]; t[2] += r["wall_ms"]; t[3] += r["box_ms"]
        print(f"**{w}** ({arm[w]['runs']} runs, {lease_h[w]:.1f} lease hours)\n")
        print("| node | runs | visits | wall min | per run | on the box |")
        print("|---|---|---|---|---|---|")
        for node, t in sorted(tot.items(), key=lambda kv: -kv[1][2])[:10]:
            print(f"| {node} | {t[0]} | {t[1]} | {t[2] / 60000:.0f} | {t[2] / 60000 / arm[w]['runs']:.1f} | "
                  f"{t[3] / 60000:.0f} |")
        print()

    # --- Failovers -----------------------------------------------------------------------
    print("## Provider failovers\n")
    print("| arm | node | from | to | kind | runs | events |")
    print("|---|---|---|---|---|---|---|")
    fo = defaultdict(lambda: [set(), 0])
    for r in failovers:
        k = (wf[r["run_id"]], r["node_id"], r["from_model"], r["to_model"], r["error_kind"])
        fo[k][0].add(r["run_id"]); fo[k][1] += r["count"]
    for k, (runs, events) in sorted(fo.items()):
        print("| " + " | ".join(k) + f" | {len(runs)} | {events} |")
    print()

    # --- Spend ---------------------------------------------------------------------------
    print("## Hosted spend (estimate)\n")
    print("| arm | provider | model | runs | input | cache read | cache write | output | USD | priced by |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    spend = defaultdict(float)
    rows = defaultdict(lambda: [set(), 0, 0, 0, 0, 0.0, ""])
    for r in usage:
        k = (wf[r["run_id"]], r["provider"], r["model_id"])
        usd, how = _price(r)
        t = rows[k]
        t[0].add(r["run_id"]); t[1] += r["input_tokens"]; t[2] += r["cache_read_tokens"]
        t[3] += r["cache_write_tokens"]; t[4] += r["output_tokens"]; t[5] += usd; t[6] = how
        spend[k[0]] += usd
    for k, t in sorted(rows.items()):
        print("| " + " | ".join(k) + f" | {len(t[0])} | {_tok(t[1])} | {_tok(t[2])} | {_tok(t[3])} | "
              f"{_tok(t[4])} | {t[5]:.2f} | {t[6]} |")
    total = sum(spend.values())
    print()
    for w in sorted(arm):
        print(f"- {w}: ${spend[w]:.2f} over {arm[w]['runs']} runs, ${spend[w] / max(arm[w]['runs'], 1):.3f} a run.")
    print(f"- Both arms: ${total:.2f} in {days:.1f} days, about ${total / days * 30:.0f} a month at this rate "
          f"(the guardrail is ${BUDGET_USD:.0f}). Runs outside run_history (automation pr-review, "
          "dependabot, arch-review, hand fires) and the doctor's reviews (below) are not in it: "
          "check the dashboards for the final result.")
    print()

    # --- The PR doctor's follow-up reviews -------------------------------------------------
    # more-local-inference Phase 2: the scheduler leases these as `pr` rows. They are not an
    # arm, so they stay out of every table above.
    print("## The PR doctor's follow-up reviews\n")
    if not reviews:
        print("- none in this window")
        return
    print("| run | repo | PR | pool | dispatched | lease h | ending | PR now |")
    print("|---|---|---|---|---|---|---|---|")
    review_ids = [h["run_id"] for h in reviews]
    rmarks = ",".join("?" * len(review_ids))
    rsummaries = {r["run_id"]: r for r in c.execute(
        f"SELECT * FROM run_summary WHERE run_id IN ({rmarks})", review_ids)}
    for h in reviews:
        fact = facts.get((h["repo"], h["issue_number"]))
        print(f"| {h['run_id'][-8:]} | {h['repo'].split('/')[1]} | {h['issue_number']} | {h['coder_pool']} | "
              f"{h['dispatched_at'][5:16]} | {_hours(h['dispatched_at'], h['finished_at']):.1f} | "
              f"{_ending(h, rsummaries.get(h['run_id']))} | {fact['state'] if fact else 'not read'} |")
    rspend = sum(_price(r)[0] for r in c.execute(
        f"SELECT * FROM run_model_usage WHERE run_id IN ({rmarks})", review_ids))
    merged = sum(1 for h in reviews
                 if (f := facts.get((h["repo"], h["issue_number"]))) and f["state"] == "merged")
    print(f"\n- {len(reviews)} reviews, {merged} of their PRs merged by now, ${rspend:.2f} hosted spend.")


def _price(r) -> tuple[float, str]:
    if r["cost_usd_micros"] is not None:
        return r["cost_usd_micros"] / 1e6, "fabro"
    rate = RATES.get((r["provider"], r["model_id"]))
    if rate:
        i, cr, cw, o = rate
        return (r["input_tokens"] * i + r["cache_read_tokens"] * cr + r["cache_write_tokens"] * cw
                + r["output_tokens"] * o) / 1e6, "ADR 0013 rates"
    if r["provider"] in FLAT:
        return 0.0, "flat plan"
    if r["provider"].startswith("box-"):
        return 0.0, "local"
    return 0.0, "UNPRICED"


def _ending(h, s) -> str:
    """The node a run ended at: ralph's is the node that routed into learn_prep."""
    if h["kind"] != "succeeded":
        return f"{h['kind']}: {h['reason'] or ''}"
    if s is None:
        return "not summarized"
    path = json.loads(s["path"] or "[]")
    for edge in reversed(path):
        frm, _, to = edge.partition(">")
        if to == "learn_prep":
            return frm
    node = s["terminal_node"] or ""
    if node == "review_merge.done":
        for edge in reversed(path):
            frm, _, to = edge.partition(">")
            if to == "review_merge.done":
                return frm.removeprefix("review_merge.")
    return node


def _hours(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def _tok(n: int) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


if __name__ == "__main__":
    main()
