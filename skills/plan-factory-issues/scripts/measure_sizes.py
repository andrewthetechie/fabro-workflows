#!/usr/bin/env python3.11
"""Re-derive L9's size thresholds (lint.py) from finished `ralph` runs.

    python3.11 scripts/measure_sizes.py --host <user@factory-host>

Reads the scheduler's store read-only, inside its container (as ops/fabro-trial-review.sh does),
then fetches each run's issue with `gh` and prints two tables: outcome by planned Task count
(`run_summary.task_sources["plan"]`), and outcome by the number of distinct file paths the issue
body names in backticks. Outcomes: auto-merged (`run_history.merged`), PR merged by anyone
(`pr_facts.state`), and no PR at all.

The 2026-11-10 run (205 ralph runs; 81 with a planned Task count):
  Tasks 1-4: n=61, 36% auto-merged, 10% no PR.   5 or more: n=20, 15% auto-merged, 15% no PR.
  Paths 0-6: n=105, 42% auto-merged, 10% no PR.  7 or more: n=100, 23% auto-merged, 22% no PR.
  Criteria counts 0-9 showed no trend (n=201); only 3 issues had more.
The paths count is every backticked path in the body, Context included, so it runs higher than an
issue's expected files: L9's 6 is the looser reading of it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict

QUERY = r'''
import json, sqlite3
c = sqlite3.connect("file:/data/scheduler.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
rows = c.execute("""
SELECT h.repo, h.issue_number, h.merged, h.pr_number, s.task_sources, p.state
FROM run_history h
JOIN run_summary s ON s.run_id = h.run_id AND s.status = 'ok'
LEFT JOIN pr_facts p ON p.repo = h.repo AND p.pr_number = h.pr_number
WHERE h.workflow = 'ralph' AND h.target_kind = 'issue'""").fetchall()
print(json.dumps([dict(r) for r in rows]))
'''
PATH = re.compile(r"`((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6})(?::\d+)?`")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="ssh target of the factory host")
    a = ap.parse_args()
    out = subprocess.run(["ssh", a.host, "docker exec -i fabro-scheduler python -I -"], input=QUERY,
                         capture_output=True, text=True, check=True).stdout
    runs = json.loads(out)
    bodies: dict[tuple[str, int], str] = {}
    for r in runs:
        key = (r["repo"], r["issue_number"])
        if key not in bodies:
            got = subprocess.run(["gh", "api", f"repos/{key[0]}/issues/{key[1]}", "--jq", ".body // \"\""],
                                 capture_output=True, text=True)
            bodies[key] = got.stdout if got.returncode == 0 else ""
    for r in runs:
        r["tasks"] = json.loads(r["task_sources"] or "{}").get("plan", 0)
        r["paths"] = len(set(PATH.findall(bodies[(r["repo"], r["issue_number"])])))
    table("planned Tasks", [r for r in runs if r["tasks"] > 0], "tasks", [(1, 4), (5, 99)])
    table("paths named", runs, "paths", [(0, 3), (4, 6), (7, 10), (11, 999)])
    return 0


def table(title: str, rows: list[dict], key: str, buckets: list[tuple[int, int]]) -> None:
    print(f"\n{title}: bucket | n | auto-merged | PR merged | no PR")
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for r in rows:
        for lo, hi in buckets:
            if lo <= r[key] <= hi:
                groups[(lo, hi)].append(r)
    for (lo, hi), rs in sorted(groups.items()):
        n = len(rs)
        auto = sum(r["merged"] == 1 for r in rs) / n
        anym = sum(r["state"] == "merged" for r in rs) / n
        nopr = sum(not r["pr_number"] for r in rs) / n
        print(f"  {lo}-{hi} | {n} | {auto:.0%} | {anym:.0%} | {nopr:.0%}")


if __name__ == "__main__":
    sys.exit(main())
