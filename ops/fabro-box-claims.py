#!/usr/bin/env python3.11
"""What the claim, the guard and the background lane did, read from fabro's run events.

more-local-inference task 12 measures the claim (C3) by its `waited=` and `idle=`, and the guard
(C10) by its `clear=`/`action=`. Both hooks print those as one stderr line, and fabro 0.362
records nothing a sandbox hook prints unless the hook blocks (verified 2026-10-28, task 12
step 1: stderr and stdout, JSON or plain, appear in no event, log or dump). So this reads the
same facts from the run event log, which fabro does keep:

- A `stage_start` hook runs between the `edge.selected` into a stage and that stage's
  `stage.started`, so for a coder loop's box stage that gap is the claim's whole wait. A gap
  of 44 s or more is the claim's 45 s deadline, i.e. `idle=false`. A stage retry starts with
  no edge before it, so only the first `stage.started` after each edge is a claim.
- A background run (`labels.lane == "background"`) ends `failed`/`cancelled`. The cancel's
  time is its `run.cancel.requested`. A cancel inside a claim's window on the same box was
  that claim's; a cancel before the run's first `agent.llm.started` is the guard or an
  eviction before it ever reached the box.
- The exit check's safety rule: no coder box stage starts (`stage.started`) while a
  background run on that box has made a request and has not ended yet.

Each coder run is judged by the Stage manifest it was compiled with (its own settings), so a
run from before the claim existed reports no box stage rather than a false wait.

Usage:
    FABRO_API_TOKEN=$(ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/server.dev-token') \\
        ops/fabro-box-claims.py --since 2026-10-28T22:20:00Z [--api http://10.10.0.32:32276/api/v1] [--json]

Standard library only. Prints run ids, node ids, boxes and times; never a token or a prompt.
"""
import argparse
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime

DEFAULT_API = "http://10.10.0.32:32276/api/v1"

# The claim's whole-command deadline (C3) is 45 s; a wait at or past this is the deadline,
# which the hook reports as `idle=false`.
IDLE_FALSE_AT = 44.0


def ts(value: str) -> datetime:
    """An event timestamp (nanosecond RFC 3339, `Z`) as a naive UTC datetime, to the µs."""
    head = value.rstrip("Z")
    if "." in head:
        main, frac = head.split(".", 1)
        head = f"{main}.{frac[:6]}"
    return datetime.fromisoformat(head)


def plan_arm_true(issue: int) -> bool:
    """The box-plan trial's arm, as `fabro-io` and the scheduler compute it."""
    return (issue // 2) % 2 == 1


def box_stages(manifest_raw: str | None, issue: int | None) -> set[str]:
    """The stage ids of a run's manifest that run on its box, for this run's issue."""
    if not manifest_raw:
        return set()
    try:
        stages = json.loads(manifest_raw).get("stages") or {}
    except ValueError:
        return set()
    out = set()
    for node, spec in stages.items():
        mode = (spec or {}).get("box", False)
        if mode is True or (mode == "plan-arm" and issue is not None and plan_arm_true(issue)):
            out.add(node)
    return out


@dataclass
class Claim:
    run_id: str
    box: str
    node: str
    edge_at: datetime
    started_at: datetime
    wait_s: float
    cancelled: list[str] = field(default_factory=list)
    overlapping: list[str] = field(default_factory=list)

    @property
    def idle(self) -> bool:
        return self.wait_s < IDLE_FALSE_AT


@dataclass
class Background:
    run_id: str
    box: str
    job: str
    kind: str
    reason: str
    first_request_at: datetime | None
    cancel_at: datetime | None
    ended_at: datetime | None


def coder_claims(run: dict) -> list[Claim]:
    """Every box-stage visit of one coder run, with the claim's wait before it."""
    labels = run.get("labels") or {}
    issue = labels.get("issue")
    stages = box_stages(run.get("manifest"), int(issue) if str(issue or "").isdigit() else None)
    out: list[Claim] = []
    last_edge = None
    for event in sorted(run["events"], key=lambda e: e.get("seq", 0)):
        if event.get("event") == "edge.selected":
            last_edge = ts(event["ts"])
        elif event.get("event") == "stage.started" and event.get("node_id") in stages and last_edge:
            started = ts(event["ts"])
            out.append(Claim(run["id"], labels.get("box", ""), event["node_id"], last_edge,
                             started, (started - last_edge).total_seconds()))
            # An edge is one claim. A stage retry (`stage.retrying`, then `stage.started`
            # attempt 2) has no edge of its own, so pairing it with this one would count the
            # failed attempt's whole run time as a wait (seen 2026-10-29: 3609 s).
            last_edge = None
    return out


def background_run(run: dict) -> Background:
    labels = run.get("labels") or {}
    first = cancel = ended = None
    for event in sorted(run["events"], key=lambda e: e.get("seq", 0)):
        name = event.get("event")
        if name == "agent.llm.started" and first is None:
            first = ts(event["ts"])
        elif name == "run.cancel.requested" and cancel is None:
            cancel = ts(event["ts"])
        elif name in ("run.failed", "run.completed", "run.succeeded"):
            ended = ts(event["ts"])
    status = run.get("status") or {}
    return Background(run["id"], labels.get("box", ""), labels.get("job", ""),
                      status.get("kind") or "", status.get("reason") or "", first, cancel, ended)


def analyse(runs: list[dict]) -> dict:
    """The claim, guard and overlap facts for a set of runs (coder and background)."""
    claims = [c for r in runs if (r.get("labels") or {}).get("lane") == "coder" for c in coder_claims(r)]
    bgs = [background_run(r) for r in runs if (r.get("labels") or {}).get("lane") == "background"]
    for claim in claims:
        for bg in bgs:
            if bg.box != claim.box:
                continue
            if bg.cancel_at and claim.edge_at <= bg.cancel_at <= claim.started_at:
                claim.cancelled.append(bg.run_id)
            # The safety rule: the box stage began while this background run had already
            # sent a request and had not ended.
            if (bg.first_request_at and bg.first_request_at < claim.started_at
                    and (bg.ended_at is None or bg.ended_at > claim.started_at)):
                claim.overlapping.append(bg.run_id)
    cancelling = sorted(c.wait_s for c in claims if c.cancelled)
    before_request = [b for b in bgs if b.cancel_at and b.first_request_at is None]
    return {
        "claims": len(claims),
        "claims_that_cancelled": len(cancelling),
        "cancel_wait_p50_s": statistics.median(cancelling) if cancelling else None,
        "cancel_wait_p95_s": (cancelling[max(0, -(-len(cancelling) * 95 // 100) - 1)]
                              if cancelling else None),
        "cancel_wait_max_s": cancelling[-1] if cancelling else None,
        "idle_false": [asdict(c) for c in claims if not c.idle],
        "overlaps": [asdict(c) for c in claims if c.overlapping],
        "background_runs": len(bgs),
        "background_by_outcome": _count(f"{b.kind}/{b.reason}" for b in bgs),
        "cancelled_before_first_request": [b.run_id for b in before_request],
        "claim_rows": [asdict(c) for c in claims],
    }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


# --- the API ------------------------------------------------------------------------------


def _get(api: str, token: str, path: str, query: dict | None = None) -> dict:
    url = f"{api.rstrip('/')}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch(api: str, token: str, since: datetime) -> list[dict]:
    """Every lane-labelled run created since `since`, with its events and manifest."""
    runs: list[dict] = []
    offset = 0
    while True:
        page = _get(api, token, "/runs", {"page[limit]": 100, "page[offset]": offset,
                                          "sort": "created_at", "direction": "desc",
                                          "include_archived": "true"})
        rows = page.get("data") or []
        for row in rows:
            created = ts((row.get("timestamps") or {}).get("created_at") or "1970-01-01T00:00:00Z")
            if created < since:
                return runs
            labels = row.get("labels") or {}
            if labels.get("lane") not in ("coder", "background"):
                continue
            runs.append({"id": row["id"], "labels": labels,
                         "status": (row.get("lifecycle") or {}).get("status") or {},
                         "events": _events(api, token, row["id"]),
                         "manifest": _manifest(api, token, row["id"]) if labels.get("lane") == "coder" else None})
        if not (page.get("meta") or {}).get("has_more") or not rows:
            return runs
        offset += len(rows)


def _events(api: str, token: str, run_id: str) -> list[dict]:
    events: list[dict] = []
    since = 1
    while True:
        page = _get(api, token, f"/runs/{run_id}/events", {"since_seq": since, "limit": 1000})
        batch = page.get("data") or []
        events += batch
        if not (page.get("meta") or {}).get("has_more") or not batch:
            return events
        since = batch[-1]["seq"] + 1


def _manifest(api: str, token: str, run_id: str) -> str | None:
    settings = _get(api, token, f"/runs/{run_id}/settings")
    env = ((settings.get("run") or {}).get("environment") or {}).get("env") or {}
    return env.get("FABRO_IO_MANIFEST")


def render(result: dict) -> str:
    def s(v):
        return "-" if v is None else f"{v:.1f}s"
    lines = [
        f"claims (coder box-stage visits): {result['claims']}",
        f"  that cancelled a background run: {result['claims_that_cancelled']}"
        f"  wait p50 {s(result['cancel_wait_p50_s'])}  p95 {s(result['cancel_wait_p95_s'])}"
        f"  max {s(result['cancel_wait_max_s'])}",
        f"  idle=false (wait >= {IDLE_FALSE_AT:.0f}s): {len(result['idle_false'])}",
        f"  box stage began while a background run was mid-request: {len(result['overlaps'])}",
        f"background runs: {result['background_runs']}  {result['background_by_outcome']}",
        f"  cancelled before their first request (guard or eviction): "
        f"{len(result['cancelled_before_first_request'])}",
        "",
        "run                         box       node                         wait   cancelled",
    ]
    for c in result["claim_rows"]:
        lines.append(f"{c['run_id']}  {c['box']:<9} {c['node']:<28} {c['wait_s']:5.1f}s "
                     f"{','.join(c['cancelled']) or '-'}")
    for c in result["overlaps"]:
        lines.append(f"OVERLAP {c['run_id']} {c['node']} on {c['box']}: {','.join(c['overlapping'])}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--since", required=True, help="ISO time, e.g. 2026-10-28T22:20:00Z")
    parser.add_argument("--api", default=os.environ.get("FABRO_API_URL", DEFAULT_API))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    token = os.environ.get("FABRO_API_TOKEN", "").strip()
    if not token:
        print("FABRO_API_TOKEN is not set (see the module docstring)", file=sys.stderr)
        return 64
    result = analyse(fetch(args.api, token, ts(args.since)))
    print(json.dumps(result, default=str, indent=1) if args.json else render(result))
    return 1 if result["overlaps"] or result["idle_false"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
