#!/usr/bin/env python3.11
"""Per-attempt metrics for a stage over `fabro events <run> --json` files (#63).

Usage: iterate-attempts.py [--before ISO] EVENTS.jsonl...

Reports, per repository, the attempts of `iterate_t1`..`iterate_t4`: how many failed, and
for the failed and the passed ones the duration, the turns, the minutes spent in tool calls
against the minutes the model was generating, the tokens, the peak context, the context
compactions, the turns longer than 60 s, the check cadence and the Task's shape.

--before ISO keeps only runs created before that instant (a window's upper bound; the
`2026-11-03` form is enough, it compares the created_at prefix).

Why "attempt" and not "visit": a visit (`stage_id`, `iterate_t1@7`) that times out emits
stage.started, stage.failed and stage.retrying, then starts again under the same stage_id
with `attempt: 2`. Keyed by stage_id alone, the timed-out attempt is invisible: the second
attempt's `stage.completed` overwrites it and the visit reads as one success. Keyed by
(stage_id, attempt) it is one attempt with no `stage.completed`, which is what a timeout is.
`ops/fabro-agent-tools.py` keys by stage_id, so its durations are one attempt per visit.

A check is `run_tests` or `task_check`. "Edits since the last check" is the number of
`edit_file`/`write_file` calls after the last of them: 0 means the attempt ended on a check.

Standard library only. Runs on the Mac or on the host, never in a sandbox.
Event files carry issue text: never commit them.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ITER = ("iterate_t1", "iterate_t2", "iterate_t3", "iterate_t4")
CHECKS = ("run_tests", "task_check")
EDITS = ("edit_file", "write_file")
# A context window this full is fabro's compaction threshold (`with_compaction_threshold_percent`
# 80, over 262144 tokens).
COMPACT_PEAK = 209_000
# The p50 of a failed and of a passed attempt, for each metric below. Order is the report's.
METRICS = ("dur", "turns", "calls", "tool_m", "model_m", "reason_tok", "out_tok", "peak",
           "comps", "slow_gaps", "checks", "check_min", "edits", "edits_after_check",
           "free_streak", "first_check", "task_files", "task_acc", "task_ctx", "inputs_bytes")


def ts(value: str) -> float:
    base, _, frac = value.rstrip("Z").partition(".")
    t = datetime.fromisoformat(base).replace(tzinfo=timezone.utc).timestamp()
    return t + (float("0." + frac[:6]) if frac else 0.0)


def pct(values: list[float], p: float):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def brace_json(s: str, start: int):
    """The JSON object that begins at `start`, by matching braces (not by its last line)."""
    depth = 0
    i = start
    instr = esc = False
    while i < len(s):
        ch = s[i]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        i += 1
    return None


def task_of(blob: str) -> dict:
    """The Task from the `inputs` tool's answer: its `current_task.json` section."""
    i = blob.find("=== current_task.json")
    if i < 0:
        return {}
    j = blob.find("\n{", i)
    if j < 0:
        return {}
    try:
        return json.loads(brace_json(blob, j + 1) or "{}")
    except ValueError:
        return {}


def load(path: Path, before: str) -> list[dict]:
    created = ""
    origin = ""
    attempts: dict[tuple[str, int], dict] = {}
    inputs: dict[str, str] = {}
    with path.open() as fh:
        lines = fh.readlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = ev.get("event", "")
        props = ev.get("properties") or {}
        if kind == "run.created":
            created = (ev.get("ts") or "")[:19]
            origin = str(((props.get("git") or {}).get("origin_url")) or "")
            continue
        node = ev.get("node_id") or ""
        sid = ev.get("stage_id") or ""
        if not sid or node not in ITER:
            continue
        t = ts(ev["ts"])
        if kind == "stage.started":
            attempts[(sid, props.get("attempt", 1))] = {
                "run": path.stem, "repo": origin.rsplit("/", 1)[-1].removesuffix(".git"),
                "created": created, "sid": sid, "att": props.get("attempt", 1),
                "start": t, "end": None, "status": "", "failure": "", "msgs": [],
                "tools": [], "comps": 0,
            }
            continue
        same = [a for (s2, _), a in attempts.items() if s2 == sid]
        if not same:
            continue
        a = max(same, key=lambda x: x["start"])
        if kind in ("stage.completed", "stage.failed"):
            a["end"] = t
            a["status"] = "passed" if kind == "stage.completed" else "failed"
            if kind == "stage.failed":
                f = props.get("failure") or {}
                a["failure"] = str(f.get("system_actor") or f.get("category") or "")
        elif kind == "agent.message":
            m = (props.get("event") or {}).get("AssistantMessage") or {}
            u = (m.get("usage") or {}).get("tokens") or {}
            cw = m.get("context_window") or {}
            ctx = cw.get("input_tokens") or 0
            if a["msgs"] and ctx < a["msgs"][-1]["ctx"] - 100_000:
                a["comps"] += 1
            a["msgs"].append({"ts": t, "out": u.get("output") or 0,
                              "reason": u.get("reasoning") or 0, "ctx": ctx})
        elif kind == "agent.tool.started":
            tc = (props.get("event") or {}).get("ToolCallStarted") or {}
            a["tools"].append({"ts": t, "end": None,
                               "name": (tc.get("tool_name") or "").replace("mcp__io__", ""),
                               "args": tc.get("arguments") or {}, "id": tc.get("tool_call_id")})
        elif kind == "agent.tool.completed":
            tc = (props.get("event") or {}).get("ToolCallCompleted") or {}
            for tool in reversed(a["tools"]):
                if tool["id"] != tc.get("tool_call_id"):
                    continue
                tool["end"] = t
                out = tc.get("output")
                if tool["name"] == "inputs" and sid not in inputs:
                    inputs[sid] = out if isinstance(out, str) else json.dumps(out or "")
                break
    rows = []
    for a in attempts.values():
        if a["end"] is None:
            continue  # still running, or killed before the stage ended
        if before and a["created"] and a["created"] >= before:
            continue
        a["dur"] = (a["end"] - a["start"]) / 60
        tools = sorted([x for x in a["tools"] if x["end"]], key=lambda x: x["ts"])
        busy = 0.0
        cursor = a["start"]
        for tool in tools:
            start = max(tool["ts"], cursor)
            if tool["end"] > start:
                busy += tool["end"] - start
                cursor = tool["end"]
        a["tool_m"] = busy / 60
        a["model_m"] = a["dur"] - a["tool_m"]
        a["turns"] = len(a["msgs"])
        a["calls"] = len(tools)
        a["reason_tok"] = sum(m["reason"] for m in a["msgs"])
        a["out_tok"] = sum(m["out"] for m in a["msgs"])
        a["peak"] = max((m["ctx"] for m in a["msgs"]), default=0)
        a["slow_gaps"] = sum(1 for i in range(len(a["msgs"]) - 1)
                             if a["msgs"][i + 1]["ts"] - a["msgs"][i]["ts"] > 60)
        checks = [x for x in tools if x["name"] in CHECKS]
        a["checks"] = len(checks)
        a["first_check"] = ((checks[0]["ts"] - a["start"]) / 60) if checks else None
        a["check_min"] = sum(x["end"] - x["ts"] for x in checks) / 60
        edits = [x for x in tools if x["name"] in EDITS]
        last = checks[-1]["ts"] if checks else a["start"]
        a["edits"] = len(edits)
        a["edits_after_check"] = sum(1 for x in edits if x["ts"] > last)
        streak = longest = 0
        for x in tools:
            if x["name"] in CHECKS:
                longest = max(longest, streak)
                streak = 0
            elif x["name"] in EDITS:
                streak += 1
        a["free_streak"] = max(longest, streak)
        task = task_of(inputs.get(a["sid"], ""))
        a["task_files"] = len(task.get("files") or [])
        a["task_acc"] = len(task.get("acceptance") or [])
        a["task_ctx"] = len(task.get("context") or "")
        a["inputs_bytes"] = len(inputs.get(a["sid"], ""))
        rows.append(a)
    return rows


def report(rows: list[dict]) -> None:
    for repo, n in Counter(r["repo"] for r in rows).most_common():
        group = [r for r in rows if r["repo"] == repo]
        failed = [r for r in group if r["status"] == "failed"]
        passed = [r for r in group if r["status"] == "passed"]
        print(f"=== {repo}: {len(group)} attempts, {len(failed)} failed "
              f"({100 * len(failed) / len(group):.1f}%)")
        print(f"  failure actors: {dict(Counter(r['failure'] for r in failed))}")
        print(f"  {'metric':<24s} {'failed p50':>11s} {'passed p50':>11s}")
        for key in METRICS:
            def p50(rs):
                v = [r[key] for r in rs if r.get(key) is not None]
                return pct(v, 0.5)
            print(f"  {key:<24s} {p50(failed)!s:>11s} {p50(passed)!s:>11s}")
        print(f"  attempts that ran >=1 check: failed {sum(1 for r in failed if r['checks'])}"
              f"/{len(failed)}, passed {sum(1 for r in passed if r['checks'])}/{len(passed)}")
        print(f"  attempts that reached 80% context: failed "
              f"{sum(1 for r in failed if r['peak'] >= COMPACT_PEAK)}/{len(failed)}, passed "
              f"{sum(1 for r in passed if r['peak'] >= COMPACT_PEAK)}/{len(passed)}")
        print()
    print("failed share by the longest run of edits with no check in between")
    for lo, hi in ((0, 0), (1, 2), (3, 4), (5, 9), (10, 10 ** 6)):
        group = [r for r in rows if lo <= r["free_streak"] <= hi]
        failed = [r for r in group if r["status"] == "failed"]
        if group:
            print(f"  {lo:2d}-{hi if hi < 10 ** 6 else '+':<4} n={len(group):4d} "
                  f"failed={len(failed):3d} = {100 * len(failed) / len(group):5.1f}%")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Per-attempt metrics for ralph's Iterations from fabro event files.")
    ap.add_argument("--before", default="",
                    help="keep only runs created before this ISO instant")
    ap.add_argument("events", nargs="+", help="one `fabro events --json` file per run")
    args = ap.parse_args(argv)
    if not args.events:
        ap.error("at least one events file is required")
    rows = []
    for name in args.events:
        rows += load(Path(name), args.before)
    if not rows:
        print("no Iteration attempt in these files", file=__import__("sys").stderr)
        return 1
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
