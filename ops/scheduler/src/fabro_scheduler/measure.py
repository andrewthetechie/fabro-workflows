"""Task 12's Phase 3 and Phase 4 exit numbers, computed from the scheduler's store.

`GET /api/background/measure` serves this. The operator should read a number here instead of
computing it by hand from `fabro events`, and ADR 0031's *Acceptance gate* is decided on Phase
3's, so the arithmetic is in one place with its own tests.

**Every number states its window, its `n`, and what it is compared with.** A figure with no `n`
is a figure nobody can weigh, and the comparison is the point: a busy share means nothing
without Phase 0's, and tokens per `plan` visit mean nothing without the two arms.

Pure, like `calibration.report`: it takes rows and returns a document, so a test seeds a store
and checks every number. The rows come from `Store.measure_rows`.

Two things this cannot see, and says so in a `note` where they would matter:

- **The store keeps no label history.** "How many held issues a human released" is therefore
  read from the two facts it does keep: the issues still carrying a `precheck-*` label, and the
  precheck verdicts that were holdable. A hold never applied (the switch was off) reads the
  same as a release, so the release count is only reported with the switch on, and it is
  labelled as the upper bound it is.
- **The store keeps no issue state.** Which `flaky-test` issues closed is GitHub's, not the
  store's. What it can say is how many diagnosed flaky issues had a run afterwards and how those
  runs ended, which is the number Phase 4 actually wants ("flaky issues closed with the
  investigation's cause").
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# --- the constants every number is measured against ---------------------------------

# Phase 0's measured busy share, 2026-10-25 to 2026-10-27 (`docs/plans/more-local-inference/
# 00-overview-and-contracts.md`, the Facts): box-a 32-39%, box-b 44-48%. Phase 3 asks whether
# the lane moved it, so it is quoted beside the measurement rather than compared silently.
PHASE_0_BUSY_SHARE = {"coders-a": (32, 39), "coders-b": (44, 48)}

# The window the endings are compared with: the 14 days before the precheck went live
# (2026-10-30T21:21Z, docs/plans/more-local-inference/07-issue-precheck.md). Before that, no
# precheck ran on any issue the factory dispatched.
PRECHECK_LIVE_AT = "2026-10-30T21:21:00+00:00"
BASELINE_DAYS = 14

# The first `dispatched_at` whose pre-review arm is a measurement: the deploy of Part 0, which
# made the arm bit 2. Before it the arm was the ralph trial's bit, so a run dispatched then ran
# under an arm that measured the trial instead. The default is `config.py`'s (the commit's
# date); `report` takes the operator's value, because the fix does nothing until it is deployed.
PRE_REVIEW_ARM_FIXED_AT = "2026-11-01T00:00:00+00:00"

# The nodes the endpoints and the endings name.
ENDINGS = ("close_noop", "return_to_triage", "mark_stuck")
PLANNER_NODES = ("decompose", "plan")

# The hosted review nodes the pre-review is meant to make cheaper, per Task: backlog's whole
# branch `standards`, `spec`, `quality` and per-task `review`, and ralph's rung-2+ `rework` (the
# rung-1 `rework_t1` runs on the box, so it costs no credits and is not counted here).
HOSTED_REVIEW_NODES = ("standards", "spec", "quality", "review", "rework_t2", "rework_t3",
                       "rework_t4")
PRE_REVIEW_VERDICTS = (
    "unchanged", "kept", "no_receipt", "forbidden_path", "unscoped", "red", "check_timeout",
    "check_error", "no_base",
)

# z.ai's glm-5.3 rates (`00-overview-and-contracts.md`, the Facts): credits = tokens x rate /
# 10,000, input 6.9, cached input 1.7, output 24, and off-peak usage is billed at 50%. Peak is
# Mon-Fri 14:00-18:00 UTC+8, which `ops/docker-compose.yaml` sets as
# `SCHEDULER_PEAK_HOURS=mon-fri 14:00-18:00 +08:00`.
RATE_INPUT = 6.9
RATE_CACHE_READ = 1.7
RATE_OUTPUT = 24.0
CREDITS_PER = 10_000.0
OFF_PEAK_FACTOR = 0.5
PEAK_UTC_HOURS = (6, 10)  # 14:00-18:00 UTC+8
PEAK_WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Monday to Friday, as `peak.py` reads `mon-fri`

# Which nodes' tokens the credit figure prices, and on which model. `decompose` is hosted in
# both graphs; `plan` is hosted unless the box-plan trial's arm is 1 (`(issue // 2) % 2 == 1`,
# C1's `"plan-arm"`), where it runs on the box and costs no credits. A node outside this table
# is reported as tokens only, because the store keeps no node -> model map.
PRICED = ("decompose", "plan")
PRICED_MODEL = "zai/glm-5.3"


def _peak(dispatched_at: str) -> bool:
    """Whether a run dispatched at `dispatched_at` was billed at the peak rate."""
    try:
        moment = datetime.fromisoformat(dispatched_at)
    except (TypeError, ValueError):
        return False
    return (
        moment.weekday() in PEAK_WEEKDAYS
        and PEAK_UTC_HOURS[0] <= moment.hour < PEAK_UTC_HOURS[1]
    )


def credits(input_tokens: int, cache_read_tokens: int, output_tokens: int, *,
            peak: bool) -> float:
    """z.ai credits for one stage's tokens, at the overview's rates and the off-peak factor."""
    gross = (
        input_tokens * RATE_INPUT
        + cache_read_tokens * RATE_CACHE_READ
        + output_tokens * RATE_OUTPUT
    ) / CREDITS_PER
    return gross if peak else gross * OFF_PEAK_FACTOR


def plan_arm(issue: int) -> int:
    """The box-plan trial's arm: `(issue // 2) % 2` (C1, `docs/plan-box-trial/`)."""
    return (issue // 2) % 2


@dataclass(frozen=True)
class Rows:
    """The rows one measurement is computed from (`Store.measure_rows`)."""

    history: list[Any]  # run_history, in the window and in the baseline
    summaries: list[Any]  # run_summary for those runs
    stages: list[Any]  # run_stage_stats for those runs
    jobs: list[Any]  # background_jobs finished in the window, or still running
    cache: list[Any]  # issue_cache, for the held issues
    prechecks: list[Any]  # done issue-precheck jobs with a verdict, any date


def _per_100(count: int, total: int) -> float | None:
    return None if total == 0 else round(count * 100.0 / total, 1)


def _mean(total: int, n: int) -> float | None:
    return None if n == 0 else round(total / n, 1)


def box_use(
    history: Sequence[Any], stages: Sequence[Any], jobs: Sequence[Any], *, days: int
) -> dict[str, object]:
    """Per box: the coder loop's box-stage minutes plus the lane's minutes, as a share of a day.

    `run_stage_stats.box_ms` is the part of a node's wall time spent on the run's coder pool, so
    summing it over a window and dividing by `days * 24 h` is the box's busy share — the number
    Phase 0 measured (32-48%) and Phase 3 compares. A background job's minutes are its row's
    `last_started_at` to `last_finished_at`, which the lane writes on every start and finish.
    """
    pool_of = {row["run_id"]: str(row["coder_pool"]) for row in history}
    coder_ms: Counter[str] = Counter()
    for row in stages:
        pool = pool_of.get(row["run_id"])
        if pool is not None:
            coder_ms[pool] += int(row["box_ms"] or 0)
    lane_ms: Counter[str] = Counter()
    for job in jobs:
        started = job["last_started_at"]
        finished = job["last_finished_at"]
        if not started or not finished:
            continue
        try:
            span = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        except ValueError:
            continue
        lane_ms[str(job["box"] or "unknown")] += max(0, int(span.total_seconds() * 1000))
    boxes: dict[str, dict[str, object]] = {}
    for pool in sorted(set(coder_ms) | set(lane_ms)):
        coder_minutes = round(coder_ms[pool] / 60_000, 1)
        lane_minutes = round(lane_ms[pool] / 60_000, 1)
        share = round((coder_ms[pool] + lane_ms[pool]) / (days * 24 * 60 * 60_000) * 100, 1)
        boxes[pool] = {
            "coder_box_minutes": coder_minutes,
            "background_minutes": lane_minutes,
            "busy_share_pct": share,
            "phase_0_busy_share_pct": PHASE_0_BUSY_SHARE.get(pool),
        }
    return {
        "window_days": days,
        "unit": "box-stage minutes of 24 h per box, averaged over the window",
        "boxes": boxes,
    }


def endings(history: Sequence[Any], summaries: Sequence[Any]) -> dict[str, object]:
    """Per 100 issue runs: `close_noop`, `return_to_triage`, `mark_stuck`.

    `run_summary.terminal_node` is the last node a run started other than `exit`, so it names
    the ending. A run with no summary yet (`n` counts only the summarized ones) is left out
    rather than counted as an ending it did not have.
    """
    by_run = {row["run_id"]: row["terminal_node"] for row in summaries}
    counts: Counter[str] = Counter()
    total = 0
    for row in history:
        if row["target_kind"] != "issue":
            continue
        node = by_run.get(row["run_id"])
        if node is None:
            continue
        total += 1
        if node in ENDINGS:
            counts[node] += 1
    return {
        "n": total,
        "counts": {name: counts[name] for name in ENDINGS},
        "per_100": {name: _per_100(counts[name], total) for name in ENDINGS},
    }


def planner_visits(
    history: Sequence[Any], stages: Sequence[Any]
) -> dict[str, object]:
    """Tokens and credits per `decompose` and `plan` visit, with and without a pack.

    Grouped by workflow, by whether a context pack existed at dispatch (`had_pack`, D21), and by
    the box-plan trial's arm — a pack changes `plan`'s credits in both arms of that trial, so a
    figure that did not stratify would confound the two.

    Credits are computed per run, at the run's peak or off-peak factor, and only for the two
    nodes in `PRICED`: `decompose` is hosted in both graphs, and `plan` is hosted except in the
    box arm. Every other node is reported as tokens only, because the store keeps no
    node -> model map to price it with.
    """
    by_run = {row["run_id"]: row for row in history}
    groups: dict[tuple[str, str, int, int], dict[str, float]] = defaultdict(
        lambda: {"visits": 0.0, "input": 0.0, "cache_read": 0.0, "output": 0.0, "credits": 0.0}
    )
    for row in stages:
        node = str(row["node_id"])
        if node not in PLANNER_NODES:
            continue
        run = by_run.get(row["run_id"])
        if run is None:
            continue
        workflow = str(run["workflow"] or "backlog")
        pack = int(run["had_pack"] or 0)
        arm = plan_arm(int(run["issue_number"]))
        if node == "plan":
            pass  # the arm already separates the box one from the hosted one
        bucket = groups[(workflow, node, pack, arm)]
        bucket["visits"] += int(row["visits"] or 0)
        bucket["input"] += int(row["input_tokens"] or 0)
        bucket["cache_read"] += int(row["cache_read_tokens"] or 0)
        bucket["output"] += int(row["output_tokens"] or 0)
        if node in PRICED and (node != "plan" or arm == 0):
            bucket["credits"] += credits(
                int(row["input_tokens"] or 0),
                int(row["cache_read_tokens"] or 0),
                int(row["output_tokens"] or 0),
                peak=_peak(str(run["dispatched_at"])),
            )
    out: list[dict[str, object]] = []
    for (workflow, node, pack, arm), values in sorted(groups.items()):
        visits = int(values["visits"])
        priced = node in PRICED and (node != "plan" or arm == 0)
        out.append({
            "workflow": workflow,
            "node": node,
            "had_pack": pack,
            "plan_arm": arm,
            "visits": visits,
            "tokens_per_visit": {
                "input": _mean(int(values["input"]), visits),
                "cache_read": _mean(int(values["cache_read"]), visits),
                "output": _mean(int(values["output"]), visits),
            },
            "credits_per_visit": (
                _mean(int(values["credits"] * 1000), visits * 1000) if priced and visits else None
            ),
            "priced_on": PRICED_MODEL if priced else None,
        })
    return {
        "window": "runs dispatched in the window",
        "note": (
            "credits are tokens x the glm-5.3 rates / 10,000, at 50% off-peak; null means the "
            "node's model is not known to the store (it keeps tokens per node, not a node to "
            "model map), so only tokens are reported"
        ),
        "groups": out,
    }


def precheck_holds(
    cache: Sequence[Any], prechecks: Sequence[Any], *, holds_on: bool,
    hold_verdicts: Sequence[str],
) -> dict[str, object]:
    """Issues carrying a `precheck-*` label, and what the precheck said it would hold.

    The store keeps no label history, so a release cannot be seen directly. What it keeps is
    which issues still carry a label and which precheck jobs recorded a holdable verdict, so
    `released_at_most` is the difference — and it reads the same as a hold that was never
    applied, which is why it is reported only with the switch on.
    """
    held: dict[str, int] = Counter()
    for row in cache:
        labels = json.loads(row["labels"] or "[]")
        for name in labels if isinstance(labels, list) else []:
            if isinstance(name, str) and name.startswith("precheck-"):
                held[name] += 1
    holdable = 0
    by_verdict: Counter[str] = Counter()
    for row in prechecks:
        outcome = str(row["outcome"] or "")
        if not outcome.startswith("verdict="):
            continue
        verdict = outcome.split("=", 1)[1].split()[0]
        by_verdict[verdict] += 1
        if verdict in hold_verdicts:
            holdable += 1
    held_total = sum(held.values())
    return {
        "switch": "on" if holds_on else "off",
        "hold_verdicts": list(hold_verdicts),
        "held_now": dict(sorted(held.items())),
        "held_now_total": held_total,
        "holdable_verdicts_recorded": holdable,
        "verdicts_recorded": dict(sorted(by_verdict.items())),
        "released_at_most": max(0, holdable - held_total) if holds_on else None,
        "note": (
            "the store keeps no label history: `released_at_most` counts issues whose precheck "
            "recorded a holdable verdict and that carry no precheck label now, which a hold "
            "never applied looks identical to. It is null while the switch is off"
        ),
    }


def pre_review(
    history: Sequence[Any], summaries: Sequence[Any], stages: Sequence[Any],
    *, since: str = PRE_REVIEW_ARM_FIXED_AT,
) -> dict[str, object]:
    """The box pre-review, by arm, for runs dispatched after Part 0 fixed the arm.

    Hosted `standards`/`spec`/`quality`/`review`/`rework` visits **per Task**, and how each
    `pre_review_verdict` came out. A run's Task count is its summary's `task_sources` (one entry
    per Task the loop worked); a run whose summary predates that column counts as one Task, and
    the section says how many did.

    A run dispatched before `PRE_REVIEW_ARM_FIXED_AT` ran under the old arm — the ralph trial's
    bit — so it is left out: including it would compare the pre-review with the coin the ralph
    trial was already flipping.
    """
    by_run = {row["run_id"]: row for row in summaries}
    visits: Counter[str] = Counter()
    for row in stages:
        if str(row["node_id"]) in HOSTED_REVIEW_NODES:
            visits[str(row["run_id"])] += int(row["visits"] or 0)
    arms: dict[str, dict[str, object]] = defaultdict(
        lambda: {"runs": 0, "tasks": 0, "hosted_visits": 0, "estimated_tasks": 0,
                 "verdicts": Counter()}
    )
    for row in history:
        if str(row["dispatched_at"]) < since:
            continue
        summary = by_run.get(row["run_id"])
        if summary is None:
            continue
        arm = str(summary["pre_review_arm"] or "unknown")
        bucket = arms[arm]
        tasks = 0
        try:
            sources = json.loads(summary["task_sources"] or "{}")
        except ValueError:
            sources = {}
        if isinstance(sources, dict):
            tasks = sum(int(value) for value in sources.values() if isinstance(value, int))
        if tasks <= 0:
            tasks = 1
            bucket["estimated_tasks"] += 1
        bucket["runs"] += 1
        bucket["tasks"] += tasks
        bucket["hosted_visits"] += visits.get(row["run_id"], 0)
        verdict = summary["pre_review_verdict"]
        if verdict:
            bucket["verdicts"][str(verdict)] += 1
    return {
        "since": since,
        "nodes": list(HOSTED_REVIEW_NODES),
        "note": (
            "runs dispatched before the arm fix are left out: their arm was the ralph trial's "
            "bit, so it measured the trial rather than the pre-review; `estimated_tasks` counts "
            "the runs whose summary carries no task tally, each counted as one Task"
        ),
        "arms": {
            arm: {
                "runs": int(values["runs"]),
                "tasks": int(values["tasks"]),
                "estimated_tasks": int(values["estimated_tasks"]),
                "hosted_visits": int(values["hosted_visits"]),
                "hosted_visits_per_task": _mean(int(values["hosted_visits"]), int(values["tasks"])),
                "verdicts": dict(sorted(values["verdicts"].items())),
            }
            for arm, values in sorted(arms.items())
        },
    }


def orientation(history: Sequence[Any], stages: Sequence[Any]) -> dict[str, object]:
    """Tokens per `decompose` visit with and without the repository orientation (Phase 4)."""
    by_run = {row["run_id"]: row for row in history}
    groups: dict[int, dict[str, float]] = {
        0: {"visits": 0.0, "input": 0.0, "cache_read": 0.0, "output": 0.0},
        1: {"visits": 0.0, "input": 0.0, "cache_read": 0.0, "output": 0.0},
    }
    for row in stages:
        if str(row["node_id"]) != "decompose":
            continue
        run = by_run.get(row["run_id"])
        if run is None:
            continue
        bucket = groups[int(run["had_orientation"] or 0)]
        bucket["visits"] += int(row["visits"] or 0)
        bucket["input"] += int(row["input_tokens"] or 0)
        bucket["cache_read"] += int(row["cache_read_tokens"] or 0)
        bucket["output"] += int(row["output_tokens"] or 0)
    out = {}
    for key, values in groups.items():
        visits = int(values["visits"])
        out["with" if key else "without"] = {
            "visits": visits,
            "tokens_per_visit": {
                "input": _mean(int(values["input"]), visits),
                "cache_read": _mean(int(values["cache_read"]), visits),
                "output": _mean(int(values["output"]), visits),
            },
        }
    return {
        "node": "decompose",
        "groups": out,
        "note": (
            "`had_orientation` is whether a done repo-orientation job finished before the run "
            "was dispatched; a run in neither group predates the column"
        ),
    }


def flaky(
    history: Sequence[Any], summaries: Sequence[Any], jobs: Sequence[Any]
) -> dict[str, object]:
    """Flaky-test issues the lane diagnosed, and how the runs after the diagnosis ended.

    The store keeps no issue labels and no closed state, so this cannot count "flaky-test
    issues closed". What it can count is the number of diagnoses, each one's cause, and how the
    runs of those issues that were dispatched afterwards ended — which is Phase 4's question
    ("flaky issues closed with the investigation's cause") in the form the store can answer.
    """
    causes: Counter[str] = Counter()
    diagnosed: dict[tuple[str, str], str] = {}
    for row in jobs:
        if str(row["kind"]) != "flaky-investigation" or str(row["state"]) != "done":
            continue
        outcome = str(row["outcome"] or "")
        if not outcome.startswith("flaky cause="):
            continue
        cause = outcome.split("cause=", 1)[1].split()[0]
        causes[cause] += 1
        diagnosed[(str(row["repo"]), str(row["target"]))] = str(row["last_finished_at"] or "")
    by_run = {row["run_id"]: row for row in summaries}
    after: Counter[str] = Counter()
    used = 0
    for row in history:
        key = (str(row["repo"]), str(row["issue_number"]))
        finished = diagnosed.get(key)
        if finished is None or str(row["dispatched_at"]) <= finished:
            continue
        used += 1
        summary_row = by_run.get(row["run_id"])
        ending = "unsummarized" if summary_row is None else str(
            summary_row["terminal_node"] or "unsummarized"
        )
        after[ending] += 1
    return {
        "diagnoses": sum(causes.values()),
        "causes": dict(sorted(causes.items())),
        "runs_after_a_diagnosis": used,
        "endings_after_a_diagnosis": dict(sorted(after.items())),
        "note": (
            "the store keeps no issue labels or closed state, so `flaky-test` issues closed is "
            "GitHub's number and not this one; this counts the diagnoses and the runs of those "
            "issues dispatched after one"
        ),
    }


def report(
    rows: Rows,
    *,
    days: int,
    now: datetime | None = None,
    holds_on: bool = False,
    hold_verdicts: Sequence[str] = (),
    pre_review_since: str = PRE_REVIEW_ARM_FIXED_AT,
) -> dict[str, object]:
    """The whole document: Phase 3's numbers, then Phase 4's, each with its window and `n`."""
    now = now or datetime.now(UTC)
    start = now - timedelta(days=days)
    live = datetime.fromisoformat(PRECHECK_LIVE_AT)
    baseline_start = live - timedelta(days=BASELINE_DAYS)
    in_window = [
        row for row in rows.history if str(row["dispatched_at"]) >= start.isoformat()
    ]
    in_baseline = [
        row for row in rows.history
        if baseline_start.isoformat() <= str(row["dispatched_at"]) < live.isoformat()
    ]
    window_ids = {row["run_id"] for row in in_window}
    baseline_ids = {row["run_id"] for row in in_baseline}
    window_summaries = [row for row in rows.summaries if row["run_id"] in window_ids]
    baseline_summaries = [row for row in rows.summaries if row["run_id"] in baseline_ids]
    window_stages = [row for row in rows.stages if row["run_id"] in window_ids]
    return {
        "window": {
            "days": days,
            "from": start.isoformat(),
            "to": now.isoformat(),
            "baseline": {
                "from": baseline_start.isoformat(),
                "to": live.isoformat(),
                "why": "the 14 days before the precheck went live",
            },
        },
        "phase_3": {
            "box_use": box_use(in_window, window_stages, rows.jobs, days=days),
            "endings_per_100": {
                "window": endings(in_window, window_summaries),
                "baseline": endings(in_baseline, baseline_summaries),
            },
            "planner_visits": planner_visits(in_window, window_stages),
            "precheck_holds": precheck_holds(
                rows.cache, rows.prechecks, holds_on=holds_on, hold_verdicts=hold_verdicts
            ),
        },
        "phase_4": {
            "pre_review": pre_review(
                in_window, window_summaries, window_stages, since=pre_review_since
            ),
            "orientation": orientation(in_window, window_stages),
            "flaky": flaky(in_window, window_summaries, rows.jobs),
        },
    }


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def as_markdown(document: Mapping[str, Any]) -> str:
    """The same document as one markdown page, for `?format=md`.

    A table per section, each headed by what it is compared with, so a pasted report still says
    its window and its `n`. Every number comes from `report`; nothing is computed here.
    """
    window = document.get("window", {})
    lines = [
        "# Background work: the Phase 3 and Phase 4 numbers",
        "",
        f"Window: **{window.get('days')} days**, {window.get('from')} to {window.get('to')}.",
        f"Baseline: {window.get('baseline', {}).get('from')} to "
        f"{window.get('baseline', {}).get('to')} — {window.get('baseline', {}).get('why')}.",
        "",
        "## Phase 3",
        "",
        "### Box use",
        "",
        "_Box-stage minutes of 24 h per box, averaged over the window. Phase 0 measured "
        "32-39% on coders-a and 44-48% on coders-b._",
        "",
        "| box | coder box minutes | background minutes | busy share | Phase 0 |",
        "|---|---|---|---|---|",
    ]
    for name, box in (document.get("phase_3", {}).get("box_use", {}).get("boxes") or {}).items():
        lines.append(
            f"| {name} | {_cell(box['coder_box_minutes'])} | {_cell(box['background_minutes'])} "
            f"| {_cell(box['busy_share_pct'])}% | {_cell(box['phase_0_busy_share_pct'])} |"
        )
    lines += [
        "",
        "### Endings per 100 issue runs",
        "",
        "| ending | window (n={}) | baseline (n={}) |".format(
            document.get("phase_3", {}).get("endings_per_100", {}).get("window", {}).get("n"),
            document.get("phase_3", {}).get("endings_per_100", {}).get("baseline", {}).get("n"),
        ),
        "|---|---|---|",
    ]
    window_endings = document.get("phase_3", {}).get("endings_per_100", {}).get("window", {})
    baseline_endings = document.get("phase_3", {}).get("endings_per_100", {}).get("baseline", {})
    for name in ENDINGS:
        lines.append(
            f"| {name} | {_cell(window_endings.get('per_100', {}).get(name))} "
            f"| {_cell(baseline_endings.get('per_100', {}).get(name))} |"
        )
    planner = document.get("phase_3", {}).get("planner_visits", {})
    lines += [
        "",
        "### Tokens per decompose and plan visit",
        "",
        f"_{planner.get('note')}_",
        "",
        "| workflow | node | pack | plan arm | visits | input | cache read | output | credits |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for group in planner.get("groups") or []:
        tokens = group["tokens_per_visit"]
        lines.append(
            f"| {group['workflow']} | {group['node']} | {group['had_pack']} "
            f"| {group['plan_arm']} | {group['visits']} | {_cell(tokens['input'])} "
            f"| {_cell(tokens['cache_read'])} | {_cell(tokens['output'])} "
            f"| {_cell(group['credits_per_visit'])} |"
        )
    holds = document.get("phase_3", {}).get("precheck_holds", {})
    lines += [
        "",
        "### Precheck holds",
        "",
        f"Switch: **{holds.get('switch')}**. Held now: {_cell(holds.get('held_now_total'))} "
        f"({', '.join(sorted((holds.get('held_now') or {}))) or 'no label'}). "
        f"Verdicts recorded: {_cell(holds.get('verdicts_recorded'))}. "
        f"Released at most: {_cell(holds.get('released_at_most'))}.",
        "",
        f"_{holds.get('note')}_",
        "",
        "## Phase 4",
        "",
        "### The box pre-review, by arm",
        "",
        f"_{document.get('phase_4', {}).get('pre_review', {}).get('note')}_",
        "",
        "| arm | runs | tasks | hosted visits | visits per task | verdicts |",
        "|---|---|---|---|---|---|",
    ]
    for arm, values in (document.get("phase_4", {}).get("pre_review", {}).get("arms") or {}).items():
        verdicts = ", ".join(f"{k}={v}" for k, v in values["verdicts"].items()) or "-"
        lines.append(
            f"| {arm} | {values['runs']} | {values['tasks']} | {values['hosted_visits']} "
            f"| {_cell(values['hosted_visits_per_task'])} | {verdicts} |"
        )
    orientation_ = document.get("phase_4", {}).get("orientation", {})
    lines += [
        "",
        "### Tokens per decompose visit, with and without the orientation",
        "",
        f"_{orientation_.get('note')}_",
        "",
        "| group | visits | input | cache read | output |",
        "|---|---|---|---|---|",
    ]
    for name, values in (orientation_.get("groups") or {}).items():
        tokens = values["tokens_per_visit"]
        lines.append(
            f"| {name} | {values['visits']} | {_cell(tokens['input'])} "
            f"| {_cell(tokens['cache_read'])} | {_cell(tokens['output'])} |"
        )
    flaky_ = document.get("phase_4", {}).get("flaky", {})
    lines += [
        "",
        "### Flaky investigations",
        "",
        f"Diagnoses: {_cell(flaky_.get('diagnoses'))} "
        f"({_cell(flaky_.get('causes'))}). Runs after a diagnosis: "
        f"{_cell(flaky_.get('runs_after_a_diagnosis'))} "
        f"({_cell(flaky_.get('endings_after_a_diagnosis'))}).",
        "",
        f"_{flaky_.get('note')}_",
        "",
    ]
    return "\n".join(lines)
