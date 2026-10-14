"""The plan trial (#25, docs/plan-trial/): one window's scorecard, split by arm.

`backlog` plans each task on one of two nodes, chosen once per run by `route_lane`
from the issue number's parity: `plan` (hosted glm-5.3 writes the dossier and an edit
plan) or `improve` (the run's coder box writes the dossier, as before). A run's arm
is the node that actually ran, read from its stage rows, so a run summarized before
the trial counts as `improve` and a run that never reached either node (a fast-path
run, a decompose that found no work) is in neither arm.

Each arm gets the whole scorecard (`scorecard.compute` over that arm's rows), which
carries the merge rate, held reasons, operator commits, box-hours per merged PR and
the refuter's verdicts, plus a `TrialLine` with the per-task numbers the trial
compares: planning cost, coder box time, rework, `validate` failures, coder timeouts
and the coder's time to first edit.

Pure, like `scorecard`: rows in, numbers out. Delete with the trial.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from . import scorecard
from .store import ScorecardRows

ARMS = ("plan", "improve")
CODER = "coder"
REWORK_PREFIX = "rework_t"
VALIDATE = "validate"
# A rework tier above t1 leaves the coder box for a hosted model (the stylesheet's
# `.coder-t2..t4`): the escalation the plan is meant to make rarer.
HOSTED_REWORK = ("rework_t2", "rework_t3", "rework_t4")


@dataclass(frozen=True)
class TrialLine:
    """One arm's per-task numbers. Tokens count only runs summarized with them."""

    runs: int
    tasks: int  # coder visits
    planner_visits: int
    planner_minutes: float
    planner_minutes_per_run: scorecard.Percentiles
    planner_box_minutes: float  # the planner's time on the coder box (improve's arm)
    planner_input_tokens: int
    planner_cache_read_tokens: int
    planner_output_tokens: int
    planner_token_runs: int  # runs whose planner tokens were recorded
    coder_box_minutes_per_task: float | None
    coding_minutes_per_task: float | None  # coder + every rework tier, wall clock
    rework_per_task: float | None
    hosted_rework_per_task: float | None
    validate_failures_per_task: float | None
    coder_timeouts: int
    first_edit_minutes: scorecard.Percentiles
    dispositions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ArmResult:
    arm: str
    card: scorecard.Scorecard
    line: TrialLine


def arm_of(stage_rows: list[sqlite3.Row]) -> str | None:
    """The arm a run was in: the planning node that ran, `plan` first."""
    nodes = {row["node_id"] for row in stage_rows}
    for arm in ARMS:
        if arm in nodes:
            return arm
    return None


def split_by_arm(rows: ScorecardRows) -> dict[str, ScorecardRows]:
    """The window's rows partitioned by arm; runs in neither arm are dropped."""
    stages_by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows.stages:
        stages_by_run[row["run_id"]].append(row)
    arm_by_run = {run_id: arm_of(stage_rows) for run_id, stage_rows in stages_by_run.items()}
    out: dict[str, ScorecardRows] = {}
    for arm in ARMS:
        ids = {run_id for run_id, a in arm_by_run.items() if a == arm}
        history = [row for row in rows.history if row["run_id"] in ids]
        keys = {(row["repo"], row["pr_number"]) for row in history if row["pr_number"] is not None}
        out[arm] = ScorecardRows(
            history=history,
            summaries=[row for row in rows.summaries if row["run_id"] in ids],
            stages=[row for row in rows.stages if row["run_id"] in ids],
            failovers=[row for row in rows.failovers if row["run_id"] in ids],
            prs=[row for row in rows.prs if (row["repo"], row["pr_number"]) in keys],
        )
    return out


def compute(rows: ScorecardRows, start: datetime, end: datetime) -> list[ArmResult]:
    """Both arms of one window, `plan` first."""
    return [
        ArmResult(arm, scorecard.compute(arm_rows, start, end), trial_line(arm, arm_rows))
        for arm, arm_rows in split_by_arm(rows).items()
    ]


def trial_line(arm: str, rows: ScorecardRows) -> TrialLine:
    history_ids = {row["run_id"] for row in rows.history}
    summaries = [
        row for row in rows.summaries if row["status"] == "ok" and row["run_id"] in history_ids
    ]
    stages = [row for row in rows.stages if row["run_id"] in history_ids]

    planner = [row for row in stages if row["node_id"] == arm]
    per_run_minutes: Counter[str] = Counter()
    for row in planner:
        per_run_minutes[row["run_id"]] += row["wall_ms"] / 60_000
    tokened = [row for row in planner if row["input_tokens"] is not None]

    def total(node_test, column: str) -> int:
        return sum(row[column] for row in stages if node_test(row["node_id"]))

    tasks = total(lambda n: n == CODER, "visits")
    rework = total(lambda n: n.startswith(REWORK_PREFIX), "visits")
    hosted_rework = total(lambda n: n in HOSTED_REWORK, "visits")
    coding_ms = total(lambda n: n == CODER or n.startswith(REWORK_PREFIX), "wall_ms")
    validate_failures = sum(
        sum(json.loads(row["failures"] or "{}").values())
        for row in stages
        if row["node_id"] == VALIDATE
    )

    first_edit: list[float] = []
    dispositions: Counter[str] = Counter()
    for row in summaries:
        first_edit.extend(ms / 60_000 for ms in json.loads(row["first_edit_ms"] or "[]"))
        dispositions.update(json.loads(row["dispositions"] or "{}"))

    return TrialLine(
        runs=len(rows.history),
        tasks=tasks,
        planner_visits=sum(row["visits"] for row in planner),
        planner_minutes=sum(per_run_minutes.values()),
        planner_minutes_per_run=scorecard._percentiles(list(per_run_minutes.values())),
        planner_box_minutes=sum(row["box_ms"] for row in planner) / 60_000,
        planner_input_tokens=sum(row["input_tokens"] for row in tokened),
        planner_cache_read_tokens=sum(row["cache_read_tokens"] or 0 for row in tokened),
        planner_output_tokens=sum(row["output_tokens"] or 0 for row in tokened),
        planner_token_runs=len({row["run_id"] for row in tokened}),
        coder_box_minutes_per_task=_per(total(lambda n: n == CODER, "box_ms") / 60_000, tasks),
        coding_minutes_per_task=_per(coding_ms / 60_000, tasks),
        rework_per_task=_per(rework, tasks),
        hosted_rework_per_task=_per(hosted_rework, tasks),
        validate_failures_per_task=_per(validate_failures, tasks),
        coder_timeouts=total(lambda n: n == CODER, "timeouts"),
        first_edit_minutes=scorecard._percentiles(first_edit),
        dispositions=dict(dispositions.most_common()),
    )


def to_json(results: list[ArmResult]) -> list[dict[str, object]]:
    return [
        {"arm": r.arm, "line": asdict(r.line), "scorecard": scorecard.to_json(r.card)}
        for r in results
    ]


def to_markdown(results: list[ArmResult], start: datetime, end: datetime) -> str:
    """The two arms side by side, one row per metric."""
    by_arm = {r.arm: r for r in results}
    plan, improve = by_arm["plan"], by_arm["improve"]

    def row(name: str, fn) -> str:
        return f"| {name} | {fn(plan)} | {fn(improve)} |"

    def tokens(r: ArmResult) -> str:
        line = r.line
        if not line.planner_token_runs:
            return "n/a"
        n = line.planner_token_runs
        return (
            f"{_mtok(line.planner_input_tokens / n)} in / {_mtok(line.planner_cache_read_tokens / n)}"
            f" cached / {_mtok(line.planner_output_tokens / n)} out (n={n})"
        )

    lines = [
        f"Plan trial (#25), {start.date()} to {(end - timedelta(seconds=1)).date()}",
        "",
        "| Metric | plan (hosted glm-5.3) | improve (coder box) |",
        "|---|---|---|",
        row("Runs / tasks coded", lambda r: f"{r.line.runs} / {r.line.tasks}"),
        row(
            "PRs opened / merged by the workflow",
            lambda r: f"{r.card.prs} / {r.card.merged_workflow} ({scorecard._pct(r.card.merge_rate)})",
        ),
        row(
            "Non-architecture merged",
            lambda r: f"{r.card.other_merged_workflow} of {r.card.other_prs}"
            f" ({scorecard._pct(r.card.other_merge_rate)})",
        ),
        row("Held PRs, by reason", lambda r: scorecard._counts(r.card.held) or "none"),
        row(
            "PRs with operator commits",
            lambda r: f"{r.card.operator_commit_prs} ({scorecard._pct(r.card.operator_commit_rate)})",
        ),
        row("Box-hours per merged PR", lambda r: scorecard._num(r.card.box_hours_per_merge)),
        row(
            "Planner minutes per run, p50 / p90",
            lambda r: f"{scorecard._num(r.line.planner_minutes_per_run.p50)} /"
            f" {scorecard._num(r.line.planner_minutes_per_run.p90)}",
        ),
        row("Planner box-minutes", lambda r: f"{r.line.planner_box_minutes:.0f}"),
        row("Planner tokens per run", tokens),
        row("Coder box-minutes per task", lambda r: scorecard._num(r.line.coder_box_minutes_per_task)),
        row(
            "Coder + rework minutes per task",
            lambda r: scorecard._num(r.line.coding_minutes_per_task),
        ),
        row(
            "Time to first edit, p50 / p90 min",
            lambda r: f"{scorecard._num(r.line.first_edit_minutes.p50)} /"
            f" {scorecard._num(r.line.first_edit_minutes.p90)} (n={r.line.first_edit_minutes.count})",
        ),
        row(
            "Rework visits per task (hosted tiers)",
            lambda r: f"{_ratio(r.line.rework_per_task)} ({_ratio(r.line.hosted_rework_per_task)})",
        ),
        row("validate failures per task", lambda r: _ratio(r.line.validate_failures_per_task)),
        row("Coder timeouts", lambda r: str(r.line.coder_timeouts)),
        row(
            "Refuter first verdict pass",
            lambda r: f"{r.card.refute.first_pass} of {r.card.refute.runs}",
        ),
        row("Planner dispositions", lambda r: scorecard._counts(r.line.dispositions) or "none"),
        row("Planner timeouts", lambda r: str(r.card.timeouts.get(r.arm, 0))),
    ]
    return "\n".join(lines) + "\n"


def _per(part: float, whole: int) -> float | None:
    return part / whole if whole else None


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _mtok(value: float) -> str:
    return f"{value / 1e6:.2f}M" if value >= 1e5 else f"{value / 1e3:.0f}k"
