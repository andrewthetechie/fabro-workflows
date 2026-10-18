#!/usr/bin/env python3
"""fabro-change-compare.py — before/after numbers for a change to the factory.

Compares the `backlog` runs dispatched before a change with the runs dispatched after
it, from the coder scheduler's own database. A change is a moment (a prompt commit
reaching `main`, a settings-overlay edit) and optionally a coder pool (a model swap on
one box). See docs/change-compare/README.md for the method, what each column means,
and the 2026-10-18 result this was written for.

Run it inside the scheduler container, which holds the database and Python 3.11. It
ships over ssh on stdin, so nothing is copied to the host:

    ssh andrew@10.10.0.32 'docker exec -i fabro-scheduler python - \\
        --since 2026-10-15 \\
        --change prompts=2026-10-18T06:59:40Z \\
        --change coders-a=2026-10-18T04:53:23Z@coders-a' < ops/fabro-change-compare.py

    --change LABEL=ISO[@POOL]  repeatable. Without @POOL every run is split at ISO and
                               compared within each repo x pool stratum. With @POOL
                               only that pool's runs, split at ISO, per repo and pooled.
    --since DATE / --until DATE  the window of dispatch times (UTC, inclusive dates).
    --exclude RUN_ID           repeatable; a run prefix to drop (an infra death the
                               endings below cannot recognise, e.g. a disk-full night).
    --runs                     also print one line per run.
    --json                     print the data instead of the tables.
    --db PATH                  default /data/scheduler.db (opened read-only).

It reads `run_history`, `run_summary` and `run_stage_stats` only; it never streams
events (the 2026-10-11 retro exported raw events and filled the host's disk).
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
import statistics
import sys
from datetime import datetime

# A run fabro never started: no stages, no signal, dropped at load. A server-wide fault
# makes hundreds of these (2026-10-18: 148 in two hours), which would swamp any count.
NEVER_RAN = {"launch_failed"}
# Endings that say nothing about the change: the run started but never got a fair go.
# Kept for the speed figures (their stages ran), left out of the outcome figures.
INFRA_REASONS = {"terminated", "cancelled"}
INFRA_KINDS = {"lost"}
# Stages whose output tokens and coder-box time make the speed figure.
CODE_STAGES = ("coder",)
CODE_PREFIXES = ("rework_t",)
# Exact rank test only below this many arrangements; above it, say so.
MAX_ARRANGEMENTS = 300_000


def parse_change(text: str) -> dict:
    label, sep, rest = text.partition("=")
    if not sep or not label:
        raise argparse.ArgumentTypeError(f"--change needs LABEL=ISO[@POOL], got {text!r}")
    moment, _, pool = rest.partition("@")
    try:
        at = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--change {label}: not an ISO time: {moment!r}") from exc
    if at.tzinfo is None:
        raise argparse.ArgumentTypeError(f"--change {label}: give a UTC offset or Z: {moment!r}")
    return {"label": label, "at": at, "pool": pool or None}


def is_code_stage(node: str) -> bool:
    return node in CODE_STAGES or node.startswith(CODE_PREFIXES)


def load_runs(db: sqlite3.Connection, since: str, until: str | None, exclude: list[str]) -> list[dict]:
    db.row_factory = sqlite3.Row
    sql = """select h.*, s.terminal_node, s.dispositions, s.first_edit_ms, s.refute
             from run_history h left join run_summary s using (run_id)
             where h.finished_at is not null and h.dispatched_at >= ?"""
    params: list[object] = [since]
    if until:
        sql += " and h.dispatched_at < date(?, '+1 day')"
        params.append(until)
    runs = []
    for row in db.execute(sql + " order by h.dispatched_at", params):
        if (row["reason"] or "") in NEVER_RAN or any(row["run_id"].startswith(x) for x in exclude):
            continue
        stages = {s["node_id"]: s for s in db.execute(
            "select * from run_stage_stats where run_id = ?", (row["run_id"],))}
        runs.append(build_run(row, stages))
    return runs


def build_run(row: sqlite3.Row, stages: dict) -> dict:
    code = [s for n, s in stages.items() if is_code_stage(n)]
    out_tokens = sum(s["output_tokens"] or 0 for s in code)
    # box_ms is the part of wall_ms spent on the run's coder pool; a stage that fell
    # back to a hosted model has none, so its wall time stands in.
    code_ms = sum((s["box_ms"] or s["wall_ms"] or 0) for s in code)
    refute = json.loads(row["refute"] or "[]")
    first_edit = json.loads(row["first_edit_ms"] or "[]")
    terminal = row["terminal_node"] or ""
    dispatched = datetime.fromisoformat(row["dispatched_at"])
    finished = datetime.fromisoformat(row["finished_at"])
    return {
        "run_id": row["run_id"],
        "dispatched_at": row["dispatched_at"],
        "_at": dispatched,
        "pool": row["coder_pool"],
        "repo": row["repo"],
        "issue": row["issue_number"],
        "kind": row["kind"],
        "reason": row["reason"],
        "terminal": terminal,
        "infra": row["kind"] in INFRA_KINDS or (row["reason"] or "") in INFRA_REASONS,
        "summarized": bool(stages),
        "merged": row["merged"],
        "reached_pr": terminal.startswith("review_merge."),
        "gave_up": terminal in ("demote_lane", "return_to_triage", "mark_stuck", "human_rescue"),
        "minutes": round((finished - dispatched).total_seconds() / 60),
        "tasks": (stages.get("coder")["visits"] if "coder" in stages else 0),
        "rework": sum(s["visits"] or 0 for n, s in stages.items() if n.startswith("rework_t")),
        "refute_first": refute[0].get("verdict") if refute else None,
        "first_edit_s": round(statistics.median(first_edit) / 1000) if first_edit else None,
        "tps": round(out_tokens / (code_ms / 1000), 1) if code_ms and out_tokens else None,
        "code_ktok": round(out_tokens / 1000),
    }


def median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def rank_p(before: list, after: list, higher_is_better: bool) -> float | None:
    """One-sided exact rank-sum p: the chance that `after` ranks this well by luck.

    Ties get midranks. Every arrangement of the pooled values is enumerated, so it is
    exact for the small samples this tool exists for, and refuses (None) when there
    are too many arrangements to count or a side is empty.
    """
    before = [v for v in before if v is not None]
    after = [v for v in after if v is not None]
    n, k = len(before), len(after)
    if not n or not k or math.comb(n + k, k) > MAX_ARRANGEMENTS:
        return None
    pooled = sorted(before + after)
    rank = {}
    for value in set(pooled):
        first = pooled.index(value)
        rank[value] = first + (pooled.count(value) + 1) / 2
    ranks = [rank[v] for v in before + after]
    observed = sum(rank[v] for v in after)
    hits = total = 0
    for combo in itertools.combinations(ranks, k):
        s = sum(combo)
        total += 1
        if (s >= observed - 1e-9) if higher_is_better else (s <= observed + 1e-9):
            hits += 1
    return hits / total


def summarize(runs: list[dict]) -> dict:
    """Outcome figures over fair runs, speed figures over every summarized run."""
    fair = [r for r in runs if not r["infra"] and r["summarized"]]
    refuted = [r for r in fair if r["refute_first"]]
    return {
        "runs": len(runs),
        "fair": len(fair),
        "merged": sum(1 for r in fair if r["merged"] == 1),
        "reached_pr": sum(1 for r in fair if r["reached_pr"]),
        "gave_up": sum(1 for r in fair if r["gave_up"]),
        "refute_pass_first": f"{sum(1 for r in refuted if r['refute_first'] == 'pass')}/{len(refuted)}",
        "no_rework": f"{sum(1 for r in fair if r['rework'] == 0)}/{len(fair)}",
        "median_rework": median([r["rework"] for r in fair]),
        "median_minutes": median([r["minutes"] for r in fair if r["kind"] == "succeeded"]),
        "median_first_edit_s": median([r["first_edit_s"] for r in runs if r["summarized"]]),
        "median_tps": median([r["tps"] for r in runs]),
        "max_tps": max((r["tps"] for r in runs if r["tps"] is not None), default=None),
    }


def compare(before: list[dict], after: list[dict]) -> dict:
    fair = lambda rs: [r for r in rs if not r["infra"] and r["summarized"]]
    return {
        "before": summarize(before),
        "after": summarize(after),
        "p_tps_higher": rank_p([r["tps"] for r in before], [r["tps"] for r in after], True),
        "p_rework_lower": rank_p([r["rework"] for r in fair(before)],
                                 [r["rework"] for r in fair(after)], False),
        "p_minutes_lower": rank_p(
            [r["minutes"] for r in fair(before) if r["kind"] == "succeeded"],
            [r["minutes"] for r in fair(after) if r["kind"] == "succeeded"], False),
    }


def strata_for(change: dict, runs: list[dict]) -> list[tuple[str, list[dict], list[dict]]]:
    at, pool = change["at"], change["pool"]
    if pool:
        runs = [r for r in runs if r["pool"] == pool]
        keys = sorted({r["repo"] for r in runs})
        groups = [(f"{pool} (all repos)", runs)] + [
            (f"{pool} / {k.split('/')[-1]}", [r for r in runs if r["repo"] == k]) for k in keys]
    else:
        keys = sorted({(r["repo"], r["pool"]) for r in runs})
        groups = [(f"{repo.split('/')[-1]} / {p}", [r for r in runs if (r["repo"], r["pool"]) == (repo, p)])
                  for repo, p in keys]
    out = []
    for name, rs in groups:
        after = [r for r in rs if r["_at"] >= at]
        # A stratum with no summarized run after the change says nothing about it.
        if any(r["summarized"] for r in after):
            out.append((name, [r for r in rs if r["_at"] < at], after))
    return out


def confounders(change: dict, before: list[dict], after: list[dict], changes: list[dict]) -> list[str]:
    """The other changes that also split this stratum's runs.

    One does when it touches these runs' pools (a global change touches every pool)
    and its moment falls between the stratum's first and last run: then the before/after
    difference belongs to both changes and this stratum cannot separate them.
    """
    rs = before + after
    if not rs:
        return []
    first, last = min(r["_at"] for r in rs), max(r["_at"] for r in rs)
    pools = {r["pool"] for r in rs}
    return [o["label"] for o in changes
            if o is not change and (o["pool"] is None or o["pool"] in pools) and first < o["at"] <= last]


def fmt_p(p: float | None) -> str:
    return "n/a" if p is None else (f"{p:.4f}" if p >= 0.0001 else f"{p:.1e}")


def print_tables(changes: list[dict], runs: list[dict], show_runs: bool) -> None:
    cols = ["fair", "merged", "reached_pr", "gave_up", "refute_pass_first", "no_rework",
            "median_rework", "median_minutes", "median_first_edit_s", "median_tps", "max_tps"]
    for change in changes:
        where = f" on {change['pool']}" if change["pool"] else ", per repo x pool"
        print(f"\n== {change['label']}: split at {change['at'].isoformat()}{where}")
        strata = strata_for(change, runs)
        if not strata:
            print("   no run dispatched after the change yet")
        for name, before, after in strata:
            c = compare(before, after)
            print(f"\n  {name}")
            print("    " + " ".join(f"{h:>13}" for h in ["side", "runs"] + cols))
            for side in ("before", "after"):
                s = c[side]
                print("    " + " ".join(f"{str(v):>13}" for v in [side, s["runs"]] + [s[k] for k in cols]))
            print(f"    rank-test p (after better by luck): tps {fmt_p(c['p_tps_higher'])}, "
                  f"rework {fmt_p(c['p_rework_lower'])}, minutes {fmt_p(c['p_minutes_lower'])}")
            mixed = confounders(change, before, after, changes)
            if mixed:
                print(f"    CONFOUNDED with {', '.join(mixed)}: this stratum cannot separate them")
    if show_runs:
        print("\n== runs")
        for r in runs:
            print(f"  {r['dispatched_at'][:16]} {r['pool']:9} {r['repo'].split('/')[-1]:22} "
                  f"#{r['issue']:<5} {r['kind']}/{(r['terminal'] or r['reason'] or '')[:24]:24} "
                  f"infra={int(r['infra'])} merged={r['merged']} rework={r['rework']} "
                  f"refute={r['refute_first']} tps={r['tps']} min={r['minutes']} {r['run_id']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--change", type=parse_change, action="append", required=True)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD, first dispatch day (UTC)")
    ap.add_argument("--until", help="YYYY-MM-DD, last dispatch day (UTC), inclusive")
    ap.add_argument("--exclude", action="append", default=[], help="run id prefix to drop")
    ap.add_argument("--runs", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default="/data/scheduler.db")
    args = ap.parse_args(argv)

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    runs = load_runs(db, args.since, args.until, args.exclude)
    if args.json:
        out = {c["label"]: [{"stratum": n, "confounded_with": confounders(c, b, a, args.change),
                             **compare(b, a)} for n, b, a in strata_for(c, runs)]
               for c in args.change}
        out["runs"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in runs]
        print(json.dumps(out, indent=1))
    else:
        print_tables(args.change, runs, args.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
