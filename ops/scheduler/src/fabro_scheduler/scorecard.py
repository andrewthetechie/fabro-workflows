"""The factory scorecard: one window of runs reduced to the numbers in issue #22.

Pure: rows in (`Store.scorecard_rows`), a `Scorecard` out. The `/metrics` page,
`GET /api/scorecard` and `ops/fabro-scorecard.sh` all read this one computation,
so the page and the weekly table can never disagree.

**A window is the runs dispatched in it.** Each PR belongs to the window of the
first run that opened it, and is described by what GitHub says about it now. So a
recent week's merge rate rises as its PRs merge, and an old week's settles.

Definitions, each the one the 2026-10-11 retro used:

* **Merged by the workflow**: a release row says the merge phase merged it, or the
  PR merged after the merge phase queued it (ADR 0024) and the merge queue did not
  hand it off. Anything else that merged was merged by the operator. `mergedBy`
  cannot tell them apart: both use one account.
* **Held**: every PR not merged by the workflow, by the reason in its last
  merge-phase report.
* **Operator commits**: commits whose author is not the automation.
* **Box-hours**: lease time, dispatch to release. One lease is one coder box.
* **Lease share**: of the leased time of summarized runs, how much a stage spent on
  the run's coder pool (writing code, or `improve`), and where the rest went while
  the box sat idle (hosted agents, `validate`, the merge phase, human gates, other
  command stages, and the overhead between stages: dispatch, sandbox start, queue).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from .store import ScorecardRows
from .usage import LOCAL_PROVIDERS

WEEK = timedelta(days=7)

ARCHITECTURE_LABEL = "architecture"

# The first pattern that matches a held PR's `**Reason:**` text names its bucket.
# Ordered most specific first. Every string the merge gate, the merge phase and the
# merge queue can write is covered (see `_shared/review-merge` and `merge-queue`).
REASON_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("architecture", re.compile(r"Architecture issue|label architecture", re.I)),
    ("do-not-merge", re.compile(r"do-not-merge", re.I)),
    ("refuter", re.compile(r"refuter", re.I)),
    ("diff hygiene", re.compile(r"hygiene", re.I)),
    ("risk", re.compile(r"risk rating", re.I)),
    ("authorship", re.compile(r"not authored by the automation|agent-authored", re.I)),
    ("unfixed findings", re.compile(r"unfixed findings|error-severity finding", re.I)),
    ("review threads", re.compile(r"requested changes|unresolved review", re.I)),
    ("auto-merge off", re.compile(r"auto-merge is (switched )?off", re.I)),
    (
        "branch moved",
        re.compile(
            r"force-with-lease|branch moved|behind the base|head on GitHub|head moved|"
            r"merged by hand|closed before|no longer open",
            re.I,
        ),
    ),
    ("CI", re.compile(r"\bCI\b|checks?\b", re.I)),
    ("timeout", re.compile(r"timed out|did not finish|still running", re.I)),
    ("unspecified", re.compile(r"unspecified|no reason was recorded", re.I)),
)

# Lease-share categories, in display order (the chart's stack order).
BOX_CATEGORIES = ("writing code", "improve")
IDLE_CATEGORIES = (
    "hosted agents",
    "validate",
    "merge phase",
    "human gates",
    "other commands",
    "overhead",
)
LEASE_CATEGORIES = BOX_CATEGORIES + IDLE_CATEGORIES

# Task validation (ADR 0025) runs in these modes. A legacy visit predates the change (or
# published no routing keys); a timeout visit is one whose stage timed out. Rendered in
# this order, so the table reads the same week to week regardless of insertion order.
TASK_VALIDATION_MODES = ("scoped", "full", "legacy", "timeout")


@dataclass(frozen=True)
class Percentiles:
    count: int
    p50: float | None
    p90: float | None


@dataclass(frozen=True)
class RepoLine:
    repo: str
    runs: int
    box_hours: float
    prs: int
    merged_workflow: int


@dataclass(frozen=True)
class FailoverLine:
    node: str
    from_model: str
    to_model: str
    error_kind: str
    runs: int


@dataclass(frozen=True)
class ModelLine:
    """One model's tokens over the window's runs (`run_model_usage`)."""

    provider: str
    model_id: str
    local: bool  # a LAN coder box (`usage.LOCAL_PROVIDERS`), not a metered provider
    runs: int
    stages: int
    input_tokens: int
    output_tokens: int  # output plus reasoning
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    share: float  # of the window's total tokens
    cost_usd: float | None  # None when fabro priced none of it

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model_id}"


@dataclass(frozen=True)
class NoPrLine:
    terminal_node: str
    runs: int
    box_hours: float


@dataclass(frozen=True)
class RefuteLine:
    """The refuter's verdicts over the window's runs (gate relief, task 07)."""

    runs: int = 0  # runs whose refute_gate gave at least one verdict
    first_pass: int = 0
    first_fail_not_met: int = 0  # a fail with a not_met criterion: the fix-turn case
    first_fail_other: int = 0  # unverifiable or defects only
    first_fail_unknown: int = 0  # a fail recorded before refute_not_met existed
    first_invalid: int = 0
    fix_turns: int = 0  # runs with a second verdict after a fix turn
    fix_cleared: int = 0  # ... whose second verdict was pass


@dataclass(frozen=True)
class FinalValidationRow:
    """Final validation (`open_pr_prep`): runs that reached it and their first verdict."""

    reached: int  # runs that recorded a final_route (the verdicts the red rate is over)
    routes: dict[str, int]  # first final_route -> runs, most common first
    red_rate: float | None  # runs whose first final_route is not "pr", over `reached`
    # Runs that visited `open_pr_prep` but recorded no route: every run before #35
    # (2026-10-13 22:13Z), or a visit that failed before routing. Their verdict is unknown,
    # so they are shown apart and never counted as red.
    unrouted: int = 0


@dataclass(frozen=True)
class TaskValidationRow:
    """Task validation (ADR 0025): every `validate` visit in the window, by mode."""

    visits: int
    by_mode: dict[str, int]
    mode_minutes: dict[str, Percentiles]  # mode -> per-visit minutes (p50/p90)
    all_minutes: Percentiles  # every visit together
    full_reasons: dict[str, int]  # full visits by fallback reason class
    red: dict[str, int]  # mode -> visits with ok false
    unchecked: int  # summed task_validation_unchecked over the window's visits
    untested: int = 0  # summed task_validation_untested over the window's visits


@dataclass(frozen=True)
class Scorecard:
    start: datetime
    end: datetime
    runs: int
    runs_summarized: int
    box_hours: float
    endings: dict[str, int]
    prs: int
    merged_workflow: int
    merged_operator: int
    open: int
    closed: int
    unknown: int  # PRs whose facts have not been read yet
    architecture_prs: int
    architecture_merged_workflow: int
    other_prs: int
    other_merged_workflow: int
    held: dict[str, int]
    operator_commit_prs: int
    operator_commits: int
    box_hours_per_merge: float | None
    lease_hours: dict[str, float]  # LEASE_CATEGORIES -> hours, summarized runs only
    lease_hours_summarized: float
    timeouts: dict[str, int]
    failovers: list[FailoverLine]
    no_pr: list[NoPrLine]
    refute: RefuteLine
    time_to_merge_hours: dict[str, Percentiles]  # "architecture" | "other"
    hosted_tokens: int
    hosted_cost_usd: float
    repos: list[RepoLine] = field(default_factory=list)
    models: list[ModelLine] = field(default_factory=list)
    models_runs: int = 0  # runs in the window whose model usage has been read
    task_validation: TaskValidationRow = field(
        default_factory=lambda: TaskValidationRow(0, {}, {}, Percentiles(0, None, None), {}, {}, 0)
    )
    final_validation: FinalValidationRow = field(
        default_factory=lambda: FinalValidationRow(0, {}, None)
    )

    @property
    def merge_rate(self) -> float | None:
        return _rate(self.merged_workflow, self.prs)

    @property
    def other_merge_rate(self) -> float | None:
        return _rate(self.other_merged_workflow, self.other_prs)

    @property
    def architecture_merge_rate(self) -> float | None:
        return _rate(self.architecture_merged_workflow, self.architecture_prs)

    @property
    def operator_commit_rate(self) -> float | None:
        return _rate(self.operator_commit_prs, self.prs)

    def lease_share(self) -> dict[str, float]:
        total = self.lease_hours_summarized
        return {k: (v / total if total else 0.0) for k, v in self.lease_hours.items()}


def compute(
    rows: ScorecardRows, start: datetime, end: datetime, *, repo: str | None = None
) -> Scorecard:
    """The window's scorecard. `repo` narrows every series to one repository.

    Filtering here rather than in SQL keeps one definition of the window and lets
    `trial.compute`, which calls this, ignore the parameter.
    """
    if repo:
        rows = _filter_repo(rows, repo)
    history = rows.history
    summaries = {row["run_id"]: row for row in rows.summaries if row["status"] == "ok"}
    facts = {(row["repo"], row["pr_number"]): row for row in rows.prs}

    stages_by_run: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows.stages:
        stages_by_run[row["run_id"]].append(row)

    lease_ms = {row["run_id"]: _lease_ms(row) for row in history}
    box_hours = sum(lease_ms.values()) / 3_600_000

    # --- PRs: one per (repo, number), owned by the first run that opened it ----
    pr_runs: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in history:
        if row["pr_lookup"] == "found" and row["pr_number"] is not None:
            pr_runs[(row["repo"], row["pr_number"])].append(row)

    merged_workflow = merged_operator = open_ = closed = unknown = 0
    arch = arch_merged = other = other_merged = 0
    op_prs = op_commits = 0
    held: Counter[str] = Counter()
    ttm: dict[str, list[float]] = {"architecture": [], "other": []}
    repo_prs: Counter[str] = Counter()
    repo_merged: Counter[str] = Counter()
    for key, runs in pr_runs.items():
        fact = facts.get(key)
        released_merged = any(run["merged"] for run in runs)
        labels = set(json.loads(fact["labels"])) if fact is not None else set()
        is_arch = ARCHITECTURE_LABEL in labels
        by_workflow = released_merged or (
            fact is not None
            and fact["state"] == "merged"
            and bool(fact["queue_approved"])
            and not fact["queue_handoff"]
        )
        repo_prs[key[0]] += 1
        if is_arch:
            arch += 1
        else:
            other += 1
        if by_workflow:
            merged_workflow += 1
            repo_merged[key[0]] += 1
            if is_arch:
                arch_merged += 1
            else:
                other_merged += 1
        elif fact is None:
            unknown += 1
        elif fact["state"] == "merged":
            merged_operator += 1
        elif fact["state"] == "closed":
            closed += 1
        else:
            open_ += 1
        if not by_workflow:
            held[held_bucket(fact)] += 1
        if fact is not None:
            if fact["operator_commits"]:
                op_prs += 1
                op_commits += fact["operator_commits"]
            hours = _hours_between(fact["created_at"], fact["merged_at"])
            if hours is not None:
                ttm["architecture" if is_arch else "other"].append(hours)

    # --- lease share, from the summarized runs ----------------------------------
    lease_hours = dict.fromkeys(LEASE_CATEGORIES, 0.0)
    summarized_ms = 0
    for run_id, stage_rows in stages_by_run.items():
        if run_id not in summaries or run_id not in lease_ms:
            continue
        total = lease_ms[run_id]
        summarized_ms += total
        stage_ms = 0
        for stage in stage_rows:
            stage_ms += stage["wall_ms"]
            box = stage["box_ms"]
            if box:
                lease_hours["improve" if stage["node_id"] == "improve" else "writing code"] += (
                    box / 3_600_000
                )
            idle = stage["wall_ms"] - box
            if idle > 0:
                lease_hours[idle_category(stage["node_id"], stage["handler"])] += idle / 3_600_000
        if total > stage_ms:
            lease_hours["overhead"] += (total - stage_ms) / 3_600_000

    # --- timeouts and failovers ---------------------------------------------------
    timeouts: Counter[str] = Counter()
    for stage in rows.stages:
        if stage["timeouts"]:
            timeouts[stage["node_id"]] += stage["timeouts"]
    failover_runs: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in rows.failovers:
        key4 = (row["node_id"], row["from_model"], row["to_model"], row["error_kind"])
        failover_runs[key4].add(row["run_id"])
    failovers = sorted(
        (FailoverLine(*key4, runs=len(ids)) for key4, ids in failover_runs.items()),
        key=lambda line: (-line.runs, line.node),
    )

    # --- runs with no PR, by where they stopped ----------------------------------
    no_pr: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    endings: Counter[str] = Counter()
    for row in history:
        endings[ending(row)] += 1
        if row["pr_lookup"] == "found":
            continue
        summary = summaries.get(row["run_id"])
        node = (summary["terminal_node"] if summary is not None else None) or "not summarized"
        no_pr[node][0] += 1
        no_pr[node][1] += lease_ms[row["run_id"]] / 3_600_000
    no_pr_lines = sorted(
        (NoPrLine(node, int(v[0]), v[1]) for node, v in no_pr.items()),
        key=lambda line: (-line.box_hours, line.terminal_node),
    )

    # --- the refuter --------------------------------------------------------------
    refute = _refute_line(summary["refute"] for summary in summaries.values())

    # --- per repo -------------------------------------------------------------------
    repo_runs: Counter[str] = Counter(row["repo"] for row in history)
    repo_hours: Counter[str] = Counter()
    for row in history:
        repo_hours[row["repo"]] += lease_ms[row["run_id"]] / 3_600_000
    repos = sorted(
        (
            RepoLine(repo, repo_runs[repo], repo_hours[repo], repo_prs[repo], repo_merged[repo])
            for repo in repo_runs
        ),
        key=lambda line: (-line.box_hours, line.repo),
    )

    models, models_runs = _model_lines(rows.models, summaries)

    task_validation, final_validation = _task_validation(rows.stages, summaries)

    return Scorecard(
        start=start,
        end=end,
        runs=len(history),
        runs_summarized=sum(1 for row in history if row["run_id"] in summaries),
        box_hours=box_hours,
        endings=dict(endings.most_common()),
        prs=len(pr_runs),
        merged_workflow=merged_workflow,
        merged_operator=merged_operator,
        open=open_,
        closed=closed,
        unknown=unknown,
        architecture_prs=arch,
        architecture_merged_workflow=arch_merged,
        other_prs=other,
        other_merged_workflow=other_merged,
        held=dict(held.most_common()),
        operator_commit_prs=op_prs,
        operator_commits=op_commits,
        box_hours_per_merge=(box_hours / merged_workflow) if merged_workflow else None,
        lease_hours=lease_hours,
        lease_hours_summarized=summarized_ms / 3_600_000,
        timeouts=dict(timeouts.most_common()),
        failovers=failovers,
        no_pr=no_pr_lines,
        refute=refute,
        time_to_merge_hours={k: _percentiles(v) for k, v in ttm.items()},
        hosted_tokens=sum(row["hosted_tokens"] or 0 for row in history),
        hosted_cost_usd=sum(row["hosted_cost_usd_micros"] or 0 for row in history) / 1e6,
        repos=repos,
        models=models,
        models_runs=models_runs,
        task_validation=task_validation,
        final_validation=final_validation,
    )


def _filter_repo(rows: ScorecardRows, repo: str) -> ScorecardRows:
    """The rows for one repository's runs. Every table is keyed off `run_history`."""
    run_ids = {row["run_id"] for row in rows.history if row["repo"] == repo}
    return ScorecardRows(
        history=[row for row in rows.history if row["repo"] == repo],
        summaries=[row for row in rows.summaries if row["run_id"] in run_ids],
        stages=[row for row in rows.stages if row["run_id"] in run_ids],
        failovers=[row for row in rows.failovers if row["run_id"] in run_ids],
        prs=[row for row in rows.prs if row["repo"] == repo],
        models=[row for row in rows.models if row["run_id"] in run_ids],
    )


def _task_validation(
    stages: Iterable[sqlite3.Row], summaries: dict[str, sqlite3.Row]
) -> tuple[TaskValidationRow, FinalValidationRow]:
    """Task validation and Final validation (ADR 0025, C9), both from the summaries.

    `run_summary.task_validations` holds one entry per `validate` visit; a visit with no
    `ok` is not a red one, and a run summarized before the fields existed keeps NULLs,
    which read as no visits.
    """
    visits: list[dict[str, object]] = []
    unchecked = 0
    untested = 0
    routes: Counter[str] = Counter()
    for summary in summaries.values():
        visits.extend(json.loads(summary["task_validations"] or "[]"))
        unchecked += summary["task_validation_unchecked"] or 0
        untested += summary["task_validation_untested"] or 0
        run_routes = json.loads(summary["final_routes"] or "[]")
        if run_routes:
            routes[run_routes[0]] += 1

    by_mode: Counter[str] = Counter()
    minutes: dict[str, list[float]] = defaultdict(list)
    all_minutes: list[float] = []
    full_reasons: Counter[str] = Counter()
    red: Counter[str] = Counter()
    for visit in visits:
        mode = str(visit.get("mode") or "legacy")
        by_mode[mode] += 1
        ms = visit.get("ms")
        value = (ms if isinstance(ms, int) else 0) / 60_000
        minutes[mode].append(value)
        all_minutes.append(value)
        reason = str(visit.get("reason_class") or "")
        if mode == "full" and reason:
            full_reasons[reason] += 1
        if visit.get("ok") is False:
            red[mode] += 1

    visited = sum(
        1
        for row in stages
        if row["node_id"] == "open_pr_prep" and row["run_id"] in summaries
    )
    reached = sum(routes.values())
    red_final = reached - routes.get("pr", 0)
    return (
        TaskValidationRow(
            visits=len(visits),
            by_mode=dict(by_mode.most_common()),
            mode_minutes={mode: _percentiles(values) for mode, values in minutes.items()},
            all_minutes=_percentiles(all_minutes),
            full_reasons=dict(full_reasons.most_common()),
            red=dict(red.most_common()),
            unchecked=unchecked,
            untested=untested,
        ),
        FinalValidationRow(
            reached=reached,
            routes=dict(routes.most_common()),
            red_rate=_rate(red_final, reached),
            unrouted=max(visited - reached, 0),
        ),
    )


def _model_lines(
    rows: Iterable[sqlite3.Row], summaries: dict[str, sqlite3.Row]
) -> tuple[list[ModelLine], int]:
    """The window's `run_model_usage` rows summed per model, most tokens first.

    Also returns how many of the window's runs have had their model usage read, so
    the page can say when the table is still filling.
    """
    sums: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    runs: dict[tuple[str, str], set[str]] = defaultdict(set)
    costs: dict[tuple[str, str], int | None] = {}
    for row in rows:
        key = (row["provider"], row["model_id"])
        runs[key].add(row["run_id"])
        for column in (
            "stages", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
        ):
            sums[key][column] += row[column] or 0
        cost = row["cost_usd_micros"]
        if cost is not None:
            costs[key] = (costs.get(key) or 0) + cost
        else:
            costs.setdefault(key, None)
    totals = {
        key: c["input_tokens"] + c["output_tokens"] + c["cache_read_tokens"] + c["cache_write_tokens"]
        for key, c in sums.items()
    }
    grand = sum(totals.values())
    lines = [
        ModelLine(
            provider=key[0],
            model_id=key[1],
            local=key[0] in LOCAL_PROVIDERS,
            runs=len(runs[key]),
            stages=c["stages"],
            input_tokens=c["input_tokens"],
            output_tokens=c["output_tokens"],
            cache_read_tokens=c["cache_read_tokens"],
            cache_write_tokens=c["cache_write_tokens"],
            total_tokens=totals[key],
            share=totals[key] / grand if grand else 0.0,
            cost_usd=costs[key] / 1e6 if costs.get(key) is not None else None,
        )
        for key, c in sums.items()
    ]
    lines.sort(key=lambda line: (-line.total_tokens, line.name))
    read = sum(
        1 for summary in summaries.values() if summary["model_usage"] in ("ok", "none")
    )
    return lines, read


def held_bucket(fact: sqlite3.Row | None) -> str:
    """Why a PR the workflow did not merge was held, as one short bucket name."""
    if fact is None:
        return "not read yet"
    reason = fact["reason"]
    if reason:
        for name, pattern in REASON_BUCKETS:
            if pattern.search(reason):
                return name
        return "other"
    if fact["report"] == "queued" and not fact["queue_handoff"]:
        return "queued" if fact["state"] == "open" else "queued, merged by hand"
    if fact["report"] is None:
        return "no report"
    return "no reason recorded"


def idle_category(node_id: str, handler: str) -> str:
    """Where a stage's off-box time is counted in the lease share."""
    if node_id.startswith("review_merge."):
        return "merge phase"
    if handler == "human":
        return "human gates"
    if "validate" in node_id:
        return "validate"
    if handler == "agent":
        return "hosted agents"
    return "other commands"


def ending(row: sqlite3.Row) -> str:
    """A run's ending in a few buckets, for the outcome chart."""
    kind, reason = row["kind"], row["reason"]
    if kind == "succeeded":
        if row["pr_lookup"] == "found":
            return "PR opened"
        return "no PR"
    if kind == "failed":
        if reason == "cancelled":
            return "cancelled"
        if reason == "terminated":
            return "fabro restart"
        return "failed"
    return kind or "unknown"


def week_windows(now: datetime, weeks: int) -> list[tuple[datetime, datetime]]:
    """The last `weeks` ISO weeks (Monday 00:00 UTC), oldest first; the last is partial."""
    now = now.astimezone(UTC)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    starts = [monday - WEEK * i for i in range(weeks - 1, -1, -1)]
    return [(start, start + WEEK) for start in starts]


def to_json(card: Scorecard) -> dict[str, object]:
    out = asdict(card)
    out["start"] = card.start.isoformat()
    out["end"] = card.end.isoformat()
    out["merge_rate"] = card.merge_rate
    out["other_merge_rate"] = card.other_merge_rate
    out["architecture_merge_rate"] = card.architecture_merge_rate
    out["operator_commit_rate"] = card.operator_commit_rate
    out["lease_share"] = card.lease_share()
    return out


def to_markdown(card: Scorecard) -> str:
    """The weekly table from issue #22, plus the gate-relief refuter line."""
    share = card.lease_share()
    idle = sum(share[k] for k in IDLE_CATEGORIES)
    lines = [
        f"Factory scorecard, {card.start.date()} to {(card.end - timedelta(seconds=1)).date()}"
        f" ({card.runs} runs, {card.runs_summarized} summarized)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| PRs opened / merged by the workflow | {card.prs} / {card.merged_workflow}"
        f" ({_pct(card.merge_rate)}) |",
        f"| Non-architecture merged by the workflow | {card.other_merged_workflow} of"
        f" {card.other_prs} ({_pct(card.other_merge_rate)}) |",
        f"| Architecture merged by the workflow | {card.architecture_merged_workflow} of"
        f" {card.architecture_prs} ({_pct(card.architecture_merge_rate)}) |",
        f"| Held PRs, by reason | {_counts(card.held) or 'none'} |",
        f"| PRs with operator commits before merge | {card.operator_commit_prs} of {card.prs}"
        f" ({_pct(card.operator_commit_rate)}), {card.operator_commits} commits |",
        f"| Box-hours per merged PR | {_num(card.box_hours_per_merge)}"
        f" ({card.box_hours:.0f} box-hours in all) |",
        f"| Lease share: writing code / improve / idle | {_pct(share['writing code'])} /"
        f" {_pct(share['improve'])} / {_pct(idle)} |",
        "| Idle by phase: hosted agents / validate / merge phase / human gates / other /"
        f" overhead | {' / '.join(_pct(share[k]) for k in IDLE_CATEGORIES)} |",
        f"| Timeouts by node | {_counts(card.timeouts) or 'none'} |",
        "| Provider failovers by node and reason | "
        + (
            ", ".join(
                f"`{line.node}` {line.from_model} -> {line.to_model} {line.error_kind}:"
                f" {line.runs} runs"
                for line in card.failovers
            )
            or "none"
        )
        + " |",
        f"| Runs with no PR, by terminal node | {sum(l.runs for l in card.no_pr)} runs,"
        f" {sum(l.box_hours for l in card.no_pr):.0f} box-hours: "
        + (", ".join(f"`{l.terminal_node}` {l.runs}" for l in card.no_pr) or "none")
        + " |",
        f"| Refuter first verdicts: pass / not_met / other fail / invalid | {card.refute.first_pass} /"
        f" {card.refute.first_fail_not_met} / {card.refute.first_fail_other + card.refute.first_fail_unknown}"
        f" / {card.refute.first_invalid}; fix turns {card.refute.fix_turns},"
        f" cleared {card.refute.fix_cleared} |",
        "| Open to merge, p50 / p90 hours: architecture / other | "
        + " / ".join(
            f"{_num(p.p50)} / {_num(p.p90)} (n={p.count})"
            for p in (card.time_to_merge_hours["architecture"], card.time_to_merge_hours["other"])
        )
        + " |",
    ]
    lines.extend(_task_validation_rows(card))
    lines.extend(["", *_model_table(card)])
    if card.unknown:
        lines.append("")
        lines.append(f"{card.unknown} PR(s) have not been read from GitHub yet.")
    if card.runs_summarized < card.runs:
        lines.append("")
        lines.append(
            f"{card.runs - card.runs_summarized} run(s) are not summarized yet: the lease"
            " share, timeouts, failovers and terminal nodes count only summarized runs."
        )
    return "\n".join(lines) + "\n"


def _task_validation_rows(card: Scorecard) -> list[str]:
    """The Task validation and Final validation rows (ADR 0025, C9).

    Task validation splits every `validate` visit by mode (scoped, full, legacy,
    timeout), with the per-visit minutes and the red count for each, the full visits'
    fallback reasons, and the unchecked- and untested-file counts. Final validation splits
    the first verdict of each run that recorded one at `open_pr_prep`, and names the runs
    that visited it without recording one.
    """
    tv = card.task_validation
    modes = [mode for mode in TASK_VALIDATION_MODES if mode in tv.by_mode]
    modes += [mode for mode in tv.by_mode if mode not in TASK_VALIDATION_MODES]
    by_mode = ", ".join(
        f"{mode} {tv.by_mode[mode]}"
        f" ({_num(tv.mode_minutes[mode].p50)}/{_num(tv.mode_minutes[mode].p90)} min)"
        for mode in modes
    )
    scoped = tv.by_mode.get("scoped", 0)
    unchecked = f"{tv.unchecked}"
    untested = f"{tv.untested}"
    if scoped:
        unchecked += f" ({tv.unchecked / scoped:.1f} per scoped visit)"
        untested += f" ({tv.untested / scoped:.1f} per scoped visit)"
    fv = card.final_validation
    unrouted = f"; {fv.unrouted} more with no route recorded" if fv.unrouted else ""
    routes = ", ".join(f"{name} {count}" for name, count in fv.routes.items()) or "none"
    return [
        f"| Task validation: visits by mode (p50/p90 min) | {by_mode or 'none'};"
        f" all {tv.visits} ({_num(tv.all_minutes.p50)}/{_num(tv.all_minutes.p90)} min) |",
        f"| Task validation: full visits by reason | {_counts(tv.full_reasons) or 'none'} |",
        "| Task validation: red visits by mode | "
        + ", ".join(f"{mode} {tv.red.get(mode, 0)}/{tv.by_mode[mode]}" for mode in modes)
        + ("" if modes else "none")
        + " |",
        f"| Task validation: unchecked files | {unchecked} |",
        f"| Task validation: untested files | {untested} |",
        f"| Final validation: verdicts / first route | {fv.reached} / {routes}"
        f" (red {_pct(fv.red_rate)}){unrouted} |",
    ]


def _model_table(card: Scorecard) -> list[str]:
    """The "Tokens by model" table: one row per provider and model, most tokens first."""
    lines = [
        f"Tokens by model ({card.models_runs} of {card.runs} runs read)",
        "",
        "| Model | Where | Runs | Visits | Input | Output | Cache read | Total | Share | Cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m in card.models:
        lines.append(
            f"| {m.name} | {'local' if m.local else 'hosted'} | {m.runs} | {m.stages} |"
            f" {tokens(m.input_tokens)} | {tokens(m.output_tokens)} |"
            f" {tokens(m.cache_read_tokens + m.cache_write_tokens)} | {tokens(m.total_tokens)} |"
            f" {_pct(m.share)} | {_usd(m.cost_usd)} |"
        )
    if not card.models:
        lines.append("| none | | | | | | | | | |")
    return lines


def tokens(count: int) -> str:
    """A token count as the page shows it: 950, 12.3k, 4.56M, 1.20B."""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if count >= size:
            value = count / size
            return f"{value:.2f}{suffix}" if value < 10 else f"{value:.1f}{suffix}"
    return str(count)


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.2f}"


def _refute_line(raw: Iterable[str | None]) -> RefuteLine:
    counts: Counter[str] = Counter()
    for text in raw:
        verdicts = json.loads(text) if text else []
        if not verdicts:
            continue
        counts["runs"] += 1
        first = verdicts[0]
        if first["verdict"] == "pass":
            counts["first_pass"] += 1
        elif first["verdict"] == "invalid":
            counts["first_invalid"] += 1
        elif first.get("not_met") is True:
            counts["first_fail_not_met"] += 1
        elif first.get("not_met") is False:
            counts["first_fail_other"] += 1
        else:
            counts["first_fail_unknown"] += 1
        # Only a fix turn sends a run back through refute_gate, so a second verdict after a
        # first fail is one. A defect-only fail takes the turn too since #36, with not_met False.
        if len(verdicts) > 1 and first["verdict"] == "fail":
            counts["fix_turns"] += 1
            if verdicts[-1]["verdict"] == "pass":
                counts["fix_cleared"] += 1
    return RefuteLine(**counts)


def _lease_ms(row: sqlite3.Row) -> int:
    hours = _hours_between(row["dispatched_at"], row["finished_at"])
    return int(hours * 3_600_000) if hours is not None else 0


def _hours_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = (b - a).total_seconds()
    return seconds / 3600 if seconds >= 0 else None


def _percentiles(values: Sequence[float]) -> Percentiles:
    if not values:
        return Percentiles(0, None, None)
    ordered = sorted(values)
    return Percentiles(len(ordered), _quantile(ordered, 0.5), _quantile(ordered, 0.9))


def _quantile(ordered: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default)."""
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _counts(counter: dict[str, int]) -> str:
    return ", ".join(f"{name} {count}" for name, count in counter.items())
