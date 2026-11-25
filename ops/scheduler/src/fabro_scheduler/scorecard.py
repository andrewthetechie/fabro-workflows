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
* **Ruled merge** (#92): a PR the workflow merged that a run of it cleared a diff-hygiene
  hit for, with a refuter ruling the code checked. The scorecard counts them and lists
  every one, because one confirmed bad clear reverts the rulings.
* **Box-hours**: lease time, dispatch to release. One lease is one coder box.
* **Lease share**: of the leased time of summarized runs, how much a stage spent on
  the run's coder pool (writing code, or `improve`), and where the rest went while
  the box sat idle (hosted agents, `validate`, the merge phase, human gates, other
  command stages, and the overhead between stages: dispatch, sandbox start, queue).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta

from .sharpen import UNAPPLIED_STATES
from .store import ScorecardRows
from .summary import FIRST_ROUND_FIELDS, FirstRound, TaskPlans
from .usage import LOCAL_PROVIDERS

WEEK = timedelta(days=7)

ARCHITECTURE_LABEL = "architecture"

# The first pattern that matches a held PR's `**Reason:**` text names its bucket.
# Ordered most specific first. A reason no pattern matches lands in `other`, which the
# retro lists with samples so the text can be given a bucket here (#62). The bucket is
# computed from the stored reason each time, so a new pattern re-classifies past weeks.
#
# `review needs human` is `merge_gate` check 7 (`fix_result.outcome` is not mergeable).
# Its text keeps the reason the review wrote after a colon, so it sits after the buckets
# that reason may name (`refuter`, `unfixed findings`) and before `CI`, whose `checks?`
# would claim prose. `stage failed` is `mark_needs_human`'s "<stage> failed (<category>)"
# without "timed out", which `timeout` claims first.
#
# The counters' own two openings come first, anchored (#92): since the Refuter rules on hits
# their reason can end "(2 hit(s) cleared by refuter rulings)", and it quotes flagged code, so
# any bucket below could claim it. The unanchored `diff hygiene` below keeps the older texts.
REASON_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "diff hygiene",
        re.compile(
            r"^(Diff hygiene blocks the merge|"
            r"The CI fix was not pushed: after it, the diff-hygiene counters block)"
        ),
    ),
    ("architecture", re.compile(r"Architecture issue|label architecture", re.I)),
    ("do-not-merge", re.compile(r"do-not-merge", re.I)),
    ("refuter", re.compile(r"refuter", re.I)),
    ("diff hygiene", re.compile(r"hygiene", re.I)),
    ("risk", re.compile(r"risk rating", re.I)),
    ("authorship", re.compile(r"not authored by the automation|agent-authored", re.I)),
    ("unfixed findings", re.compile(r"unfixed findings|error-severity finding", re.I)),
    ("review needs human", re.compile(r"did not end in a mergeable state", re.I)),
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
    ("stage failed", re.compile(r"\bfailed \([a-z_]+\)", re.I)),
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

# The counts `refute_gate` publishes with each verdict for the refuter's rulings on
# diff-hygiene hits (#92): hits whose final ruling is `legitimate`, `unsure` or `tampering`.
# A verdict from a run whose gate published none has none of the three keys.
HYGIENE_RULINGS = ("hygiene_cleared", "hygiene_unsure", "hygiene_tampering")


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
class RuledMergeRun:
    """One run that cleared a diff-hygiene hit on a PR the workflow merged (#92).

    `cleared` is summed over the run's verdicts that carry the count, as the window's
    totals are. The list is one line per run, so a PR two runs ruled on is found twice,
    each with the run whose report holds its rulings.
    """

    repo: str
    pr_number: int
    run_id: str
    cleared: int

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/pull/{self.pr_number}"


@dataclass(frozen=True)
class RefuteLine:
    """The refuter's verdicts over the window's runs (gate relief, task 07)."""

    runs: int = 0  # runs whose refute_gate gave at least one verdict
    first_pass: int = 0
    first_fail_not_met: int = 0  # a fail with a not_met criterion: the fix-turn case
    first_fail_defects: int = 0  # a fail with no not_met, unverifiable_only False
    first_fail_unverifiable_only: int = 0  # a fail with no not_met and no defect
    first_fail_other_unknown: int = 0  # a fail with no not_met and no unverifiable_only
    first_fail_unknown: int = 0  # a fail recorded before refute_not_met existed
    first_invalid: int = 0
    fix_turns: int = 0  # runs with a second verdict after a fix turn
    fix_cleared: int = 0  # ... whose second verdict was pass
    first_ci_credited: int = 0  # met criteria citing ci.sh, over first verdicts
    first_ci_credited_runs: int = 0  # runs whose first verdict carried a ci_credited count
    # The rulings on diff-hygiene hits (#92), summed over every verdict that carries them.
    # `hygiene_runs` counts the runs that carry the counts at all: a run whose gate
    # published them, which no run from before the rulings does, re-summarized or not.
    hygiene_cleared: int = 0  # hits whose final ruling was `legitimate`
    hygiene_unsure: int = 0  # ... `unsure`
    hygiene_tampering: int = 0  # ... `tampering`
    hygiene_runs: int = 0
    # PRs the workflow merged that a run cleared a hit for, and each of those runs.
    ruled_merges: int = 0
    ruled_merge_runs: list[RuledMergeRun] = field(default_factory=list)


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
    # How many of the window's runs each workflow ran (ADR 0029 C12, O21). One entry
    # for a window before the trial, so nothing else changes shape.
    workflow_runs: dict[str, int] = field(default_factory=dict)
    # Issue sharpening (ADR 0033): rows that ended applied, rows released without an apply, and
    # how the applied issues' PRs ended. Zero before the first one.
    sharpen_applied: int = 0
    sharpen_unapplied: int = 0
    sharpen_prs: int = 0
    sharpen_merged: int = 0

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

    Filtering here rather than in SQL keeps one definition of the window.
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
        by_workflow = merged_by_workflow(runs, fact)
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
    ruled = _ruled_merge_runs(pr_runs, facts, summaries)
    refute = _refute_line(
        (summary["refute"] for summary in summaries.values()),
        ruled_merges=len({(merge.repo, merge.pr_number) for merge in ruled}),
        ruled_merge_runs=ruled,
    )

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

    # --- sharpening: did the rewritten issues' PRs merge (ADR 0033) ----------------
    applied_rows = [row for row in rows.sharpen if row["state"] == "applied"]
    sharpen_pr_keys = {
        key
        for key, runs in pr_runs.items()
        if any(
            run["repo"] == row["repo"] and run["issue_number"] == row["issue_number"]
            and str(run["dispatched_at"]) >= str(row["finished_at"])
            for row in applied_rows
            for run in runs
        )
    }
    sharpen_merged = sum(
        1 for key in sharpen_pr_keys if merged_by_workflow(pr_runs[key], facts.get(key))
    )

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
        workflow_runs=dict(
            sorted(
                Counter(row["workflow"] or "backlog" for row in history).items()
            )
        ),
        sharpen_applied=len(applied_rows),
        sharpen_unapplied=sum(1 for row in rows.sharpen if row["state"] in UNAPPLIED_STATES),
        sharpen_prs=len(sharpen_pr_keys),
        sharpen_merged=sharpen_merged,
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
        sharpen=[row for row in rows.sharpen if row["repo"] == repo],
        task_reviews=[row for row in rows.task_reviews if row["run_id"] in run_ids],
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


def merged_by_workflow(runs: Sequence[sqlite3.Row], fact: sqlite3.Row | None) -> bool:
    """Whether the workflow merged the PR one or more runs opened.

    Two ways, and only two: a release row says the merge phase merged it (the
    operator could not have, because the run held the PR at release time), or it
    merged after the merge phase queued it (`<!-- fabro:merge-queue approved -->`)
    and the merge queue did not hand it off. `mergedBy` cannot separate the two
    accounts because both use `andrewthetechie`.

    Public because the weekly retro's held classes need the same answer
    (ADR 0026 C1a): a PR it counts as merged by the workflow is not held.
    """
    if any(run["merged"] for run in runs):
        return True
    return (
        fact is not None
        and fact["state"] == "merged"
        and bool(fact["queue_approved"])
        and not fact["queue_handoff"]
    )


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
    # A listed ruled merge carries its PR link (#92), as the table and the page do.
    out["refute"]["ruled_merge_runs"] = [
        {**asdict(merge), "url": merge.url} for merge in card.refute.ruled_merge_runs
    ]
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
        f"| Refuter first verdicts: pass / not_met / defects / unverifiable-only / other / invalid | {card.refute.first_pass} /"
        f" {card.refute.first_fail_not_met} / {card.refute.first_fail_defects} / {card.refute.first_fail_unverifiable_only} /"
        f" {card.refute.first_fail_other_unknown + card.refute.first_fail_unknown} / {card.refute.first_invalid};"
        f" fix turns {card.refute.fix_turns}, cleared {card.refute.fix_cleared} |",
        f"| Sharpened | applied {card.sharpen_applied}, released unapplied {card.sharpen_unapplied};"
        f" applied issues' PRs merged {card.sharpen_merged} of {card.sharpen_prs} |",
        f"| Refuter CI-credited criteria (first verdicts) | {card.refute.first_ci_credited} over"
        f" {card.refute.first_ci_credited_runs} runs |",
        "| Refuter hygiene rulings: hits cleared / unsure / tampering |"
        f" {card.refute.hygiene_cleared} / {card.refute.hygiene_unsure} /"
        f" {card.refute.hygiene_tampering} over {card.refute.hygiene_runs} runs that carry them"
        + ("" if card.refute.hygiene_runs else "; no run in this window publishes them")
        + " |",
        f"| Ruled merges | {card.refute.ruled_merges} PR(s) the workflow merged on a cleared hit"
        f" ({len(card.refute.ruled_merge_runs)} run(s)) |",
        "| Open to merge, p50 / p90 hours: architecture / other | "
        + " / ".join(
            f"{_num(p.p50)} / {_num(p.p90)} (n={p.count})"
            for p in (card.time_to_merge_hours["architecture"], card.time_to_merge_hours["other"])
        )
        + " |",
    ]
    lines.extend(_task_validation_rows(card))
    lines.extend(_ruled_merge_table(card))
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


def _ruled_merge_table(card: Scorecard) -> list[str]:
    """The ruled merges (#92): every run that cleared a diff-hygiene hit on a merged PR.

    Each line carries the PR link and the run id, so every merge that relied on a ruling can
    be opened and read. Nothing is printed when there is none.
    """
    merges = card.refute.ruled_merge_runs
    if not merges:
        return []
    return [
        "",
        f"Ruled merges ({card.refute.ruled_merges} PR(s)): open each PR and read its report",
        "",
        "| Repository | PR | Run | Hits cleared |",
        "|---|---|---|---|",
        *(f"| {m.repo} | [#{m.pr_number}]({m.url}) | `{m.run_id}` | {m.cleared} |" for m in merges),
    ]


def tokens(count: int) -> str:
    """A token count as the page shows it: 950, 12.3k, 4.56M, 1.20B."""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if count >= size:
            value = count / size
            return f"{value:.2f}{suffix}" if value < 10 else f"{value:.1f}{suffix}"
    return str(count)


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.2f}"


def _refute_line(
    raw: Iterable[str | None],
    *,
    ruled_merges: int = 0,
    ruled_merge_runs: Sequence[RuledMergeRun] = (),
) -> RefuteLine:
    counts: Counter[str] = Counter()
    for text in raw:
        verdicts = _verdicts(text)
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
            if first.get("unverifiable_only") is True:
                counts["first_fail_unverifiable_only"] += 1
            elif first.get("unverifiable_only") is False:
                counts["first_fail_defects"] += 1
            else:
                counts["first_fail_other_unknown"] += 1
        else:
            counts["first_fail_unknown"] += 1
        # The CI-credited count (#40): summed over first verdicts that carry it.
        cc = first.get("ci_credited")
        if isinstance(cc, int):
            counts["first_ci_credited"] += cc
            counts["first_ci_credited_runs"] += 1
        # Only a fix turn sends a run back through refute_gate, so a second verdict after a
        # first fail is one. A defect-only fail takes the turn too since #36, with not_met False.
        if len(verdicts) > 1 and first["verdict"] == "fail":
            counts["fix_turns"] += 1
            if verdicts[-1]["verdict"] == "pass":
                counts["fix_cleared"] += 1
        # The rulings on diff-hygiene hits (#92) are summed over every verdict that carries
        # them, not the first only: in `ralph` a later round rules again. A verdict whose
        # gate published none adds nothing, and its run is not one of the carrying runs.
        carries = False
        for verdict in verdicts:
            for key in HYGIENE_RULINGS:
                count = _ruling_count(verdict.get(key))
                if count is not None:
                    counts[key] += count
                    carries = True
        if carries:
            counts["hygiene_runs"] += 1
    return RefuteLine(**counts, ruled_merges=ruled_merges, ruled_merge_runs=list(ruled_merge_runs))


def _ruled_merge_runs(
    pr_runs: Mapping[tuple[str, int], Sequence[sqlite3.Row]],
    facts: Mapping[tuple[str, int], sqlite3.Row],
    summaries: Mapping[str, sqlite3.Row],
) -> list[RuledMergeRun]:
    """Every run that cleared a diff-hygiene hit on a PR the workflow merged (#92).

    One line per run, in `(repo, PR, run)` order: a PR two runs ruled on is one ruled merge
    and two lines, so each run's report can be found. A run without the counts, or whose
    hits were all `unsure` or `tampering`, cleared nothing and is not listed.
    """
    lines = []
    for (repo, number), runs in pr_runs.items():
        if not merged_by_workflow(runs, facts.get((repo, number))):
            continue
        for run in runs:
            summary = summaries.get(run["run_id"])
            if summary is None:
                continue
            cleared = 0
            for verdict in _verdicts(summary["refute"]):
                count = _ruling_count(verdict.get("hygiene_cleared"))
                if count is not None:
                    cleared += count
            if cleared > 0:
                lines.append(RuledMergeRun(repo, number, run["run_id"], cleared))
    return sorted(lines, key=lambda line: (line.repo, line.pr_number, line.run_id))


def _verdicts(text: str | None) -> list[dict[str, object]]:
    """A stored `run_summary.refute` row: its verdicts in order, none for an empty row."""
    return json.loads(text) if text else []


def _ruling_count(value: object) -> int | None:
    """A stored ruling count: an int, or None when the gate published none (never read as 0)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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

# --------------------------------------------------------------------------- #
# The trial's two arms (ADR 0029 C12)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StrataRow:
    """One repo x coder-pool cell, for one workflow: what O3 asks to be compared.

    The trial's guardrails are per stratum, not per arm: a merge rate that rises
    because `ralph` ran on the faster box would be an artefact, so the table shows
    the runs, the merges and the box-hours per merge for each cell.
    """

    repo: str
    coder_pool: str
    workflow: str
    runs: int
    prs: int
    merged: int
    box_hours: float
    box_hours_per_merge: float | None


@dataclass(frozen=True)
class RalphLine:
    """The numbers only a `ralph` run has (C12).

    `iterations` and `refute_visits` are per run, so a run that used four Iterations
    and one that used one are both visible. `tasks_by_source` answers "where did the
    work come from": a run whose Tasks are mostly `refute` ones is a run whose plan
    missed. `runs_exhausted` counts the loop running out (O18), `post_iterations`
    the post-PR budget being spent (O13), and `queued_handoffs` the PRs that went to
    merge-on-green instead of merging here (O12).
    """

    runs: int = 0
    iterations: Percentiles = field(default_factory=lambda: Percentiles(0, None, None))
    refute_visits: Percentiles = field(default_factory=lambda: Percentiles(0, None, None))
    tasks_by_source: dict[str, int] = field(default_factory=dict)
    runs_exhausted: int = 0
    post_iterations: int = 0
    queued_handoffs: int = 0
    # The Refuter's tokens, so task 12 can price them at luna's rates: the catalog
    # has no cost rows for OpenAI or z.ai, and the scorecard shows $0 for both.
    refute_input_tokens: int = 0
    refute_cache_read_tokens: int = 0
    refute_output_tokens: int = 0


# --------------------------------------------------------------------------- #
# The ralph-v2 trial (ADR 0034, ralph-v2 11, *The experiment*)
# --------------------------------------------------------------------------- #

# The two arms of the ralph-v2 trial, in the order the comparison is read: v1 first, so
# "v2 - v1" is a subtraction of the second from the first.
TRIAL_ARMS = ("ralph", "ralph-v2")

# A round's verdicts that rejected the Task's work. `missed` does not: a reviewer that
# returned nothing is not a reviewer that refused (E11), and the Task passes on a miss.
REJECTED_VERDICTS = frozenset({"rework", "blocked"})

# The reviewer service a round records (C4). `missed` is the one that counts as a miss.
SERVED_STATES = ("box", "fallback", "missed")
MISSED = "missed"

# The stages the Task review loop added, whose box minutes the scorecard reports per run
# (`box_ms` from `run_stage_stats`, the same clock the lease share uses).
TASK_REVIEW_BOX_NODES = ("task_plan", "task_refute", "task_review", "iterate_t1")

# Below these counts a stop rule cannot have fired yet (the experiment, *Stop early*).
LEASE_STOP_RUNS = 40
MERGE_STOP_RUNS = 60
MISS_STOP_RATE = 0.10


@dataclass(frozen=True)
class ArmLine:
    """One arm of the ralph-v2 trial, in the experiment's own terms (ralph-v2 11).

    `zero_touch` counts the runs whose PR the workflow merged with no operator commit
    (`merged_by_workflow` and the PR's `operator_commits` 0), and it is the only measure of
    "the run delivered": a run with no PR still counts in `runs`, because the experiment
    counts every run (measure 1). `merge_rate` is over the arm's runs and `merge_se` its
    binomial standard error. `quality_*` are over the runs whose `first_round` measured all
    five counts, and `refuter_*` over the three the Refuter alone publishes: n, the mean and
    the **sample** standard deviation, which is None below 2 runs because a spread needs a
    pair. `lease_hours_per_merge` is the guardrail.
    """

    workflow: str
    runs: int = 0
    zero_touch: int = 0
    merge_rate: float | None = None
    merge_se: float | None = None
    quality_n: int = 0
    quality_mean: float | None = None
    quality_sd: float | None = None
    refuter_n: int = 0
    refuter_mean: float | None = None
    refuter_sd: float | None = None
    exhausted_runs: int = 0
    lease_hours: float = 0.0
    lease_hours_per_merge: float | None = None


@dataclass(frozen=True)
class TrialLine:
    """v2 against v1 over one window, and the three stop rules (the experiment).

    `merge_diff` and `quality_diff` are v2 - v1 -- more merges is better, fewer findings is
    better -- each with the standard error of the difference and the verdict the trial's
    2-standard-error rule gives it. `guardrail` is v2's lease-hours per zero-touch merge over
    v1's, against the trial's +50%. The three stop rules are the weekly checks: v2's guardrail
    above 2 after 40 v2 runs, its merge measure 15 points below after 60, and misses above 10%
    of the window's Task review**ers**, two to a round -- the rate `measure.box_health`
    reports, so this rule and C11 read one run the same way. Each is False until its own
    threshold holds, and the numeric fields are None below 2 runs in either arm, where there is
    nothing to compare.
    """

    merge_diff: float | None = None
    merge_se: float | None = None
    merge_verdict: str | None = None  # better, worse or unclear
    quality_diff: float | None = None
    quality_se: float | None = None
    quality_verdict: str | None = None
    guardrail: float | None = None
    lease_stop: bool = False
    merge_stop: bool = False
    miss_stop: bool = False


@dataclass(frozen=True)
class TaskReviewLine:
    """`ralph-v2`'s Task review loop over the window (E7-E13, C5).

    `reviews` is one row per round and a round is two reviewers, so a box-health miss rate is
    over twice it. `rejection_rate` is the share of each round's reviews whose verdict was
    `rework` or `blocked`, keyed by the round number: how often the first review rejected the
    work, and how often the fourth. `misses` counts reviewer visits that returned nothing valid
    (one per reviewer, E13), `refute_served`/`review_served` count where each reviewer answered,
    and `edges_planned`/`edges_covered` are what the plans wrote against what the first review
    of each Task found covered -- the first, because every round restates the same edge cases.
    `box_minutes` is what each of the new stages spent on the box per v2 run, and `plan_types`
    counts the plans `task_plan_gate` accepted.
    """

    reviews: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    rejection_rate: dict[int, float] = field(default_factory=dict)
    blocked: int = 0  # Tasks whose fourth review left an error on the box (E10)
    disputes: int = 0
    withdrawals: int = 0
    refute_served: dict[str, int] = field(default_factory=dict)  # box / fallback / missed
    review_served: dict[str, int] = field(default_factory=dict)
    misses: int = 0
    ms: Percentiles = field(default_factory=lambda: Percentiles(0, None, None))
    edges_planned: int = 0
    edges_covered: int = 0
    box_minutes: dict[str, float] = field(default_factory=dict)
    plan_types: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowsCard:
    """Both arms over one window, side by side (C12, O3).

    `cards` is keyed by workflow and holds an ordinary `Scorecard`, so every number
    the single-arm view has is available per arm with no second definition. `strata`
    is the repo x pool table, and `ralph` is the line only the trial's arm has.
    """

    start: datetime
    end: datetime
    cards: dict[str, Scorecard]
    strata: list[StrataRow] = field(default_factory=list)
    ralph: RalphLine = field(default_factory=RalphLine)
    # --- the ralph-v2 trial (ralph-v2 11, *The experiment*) ------------------------
    # One line per arm (`ralph` and `ralph-v2`), their difference and the stop rules, and
    # the Task review loop's own numbers. All three are empty/None on a window with too
    # little history to compare, which is every window before the switch.
    arms: list[ArmLine] = field(default_factory=list)
    trial: TrialLine = field(default_factory=TrialLine)
    task_reviews: TaskReviewLine = field(default_factory=TaskReviewLine)


def by_workflow(rows: ScorecardRows, start: datetime, end: datetime) -> WorkflowsCard:
    """Split one window's rows by `run_history.workflow` and compute a card each.

    Pure, and deliberately a split of `compute` rather than a second computation:
    the arm's card is the same code over the arm's rows, so the two views cannot
    disagree about what "merged by the workflow" means.
    """
    by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows.history:
        by_name[row["workflow"] or "backlog"].append(row)
    # A window with no run in an arm still gets that arm's column, at zero: a table
    # that loses a column when the arm is idle is a table nobody can read.
    for name in ("backlog", "ralph", "ralph-v2"):
        by_name.setdefault(name, [])

    workflow_of_pr: dict[tuple[str, int], str] = {}
    for row in rows.history:
        if row["pr_lookup"] == "found" and row["pr_number"] is not None:
            workflow_of_pr.setdefault((row["repo"], row["pr_number"]), row["workflow"] or "backlog")

    # A stable column order for every renderer: `backlog` first (the control arm),
    # then `ralph`, then `ralph-v2`, then anything a later series adds.
    order = ["backlog", "ralph", "ralph-v2"] + sorted(
        n for n in by_name if n not in ("backlog", "ralph", "ralph-v2")
    )
    cards: dict[str, Scorecard] = {}
    for name in order:
        history = by_name[name]
        run_ids = {row["run_id"] for row in history}
        cards[name] = compute(
            ScorecardRows(
                history=history,
                summaries=[row for row in rows.summaries if row["run_id"] in run_ids],
                stages=[row for row in rows.stages if row["run_id"] in run_ids],
                failovers=[row for row in rows.failovers if row["run_id"] in run_ids],
                prs=[
                    row
                    for row in rows.prs
                    if workflow_of_pr.get((row["repo"], row["pr_number"])) == name
                ],
                models=[row for row in rows.models if row["run_id"] in run_ids],
            ),
            start,
            end,
        )

    facts = {(row["repo"], row["pr_number"]): row for row in rows.prs}
    pr_runs: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows.history:
        if row["pr_lookup"] == "found" and row["pr_number"] is not None:
            pr_runs[(row["repo"], row["pr_number"])].append(row)

    strata: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows.history:
        key = (row["repo"], row["coder_pool"], row["workflow"] or "backlog")
        cell = strata.setdefault(
            key, {"runs": 0, "prs": 0, "merged": 0, "lease_ms": 0}
        )
        cell["runs"] = int(cell["runs"]) + 1
        cell["lease_ms"] = int(cell["lease_ms"]) + _lease_ms(row)
    for (repo, number), opened in pr_runs.items():
        fact = facts.get((repo, number))
        merged = merged_by_workflow(opened, fact)
        for row in opened:
            key = (row["repo"], row["coder_pool"], row["workflow"] or "backlog")
            cell = strata[key]
            cell["prs"] = int(cell["prs"]) + 1
            if merged:
                cell["merged"] = int(cell["merged"]) + 1

    table = []
    for (repo, pool, name), cell in sorted(strata.items()):
        hours = int(cell["lease_ms"]) / 3_600_000
        merges = int(cell["merged"])
        table.append(
            StrataRow(
                repo=repo,
                coder_pool=pool,
                workflow=name,
                runs=int(cell["runs"]),
                prs=int(cell["prs"]),
                merged=merges,
                box_hours=hours,
                box_hours_per_merge=(hours / merges if merges else None),
            )
        )

    # --- the trial's two arms, their difference and the Task review loop ---------
    arms = []
    for name in TRIAL_ARMS:
        history = by_name[name]
        run_ids = {row["run_id"] for row in history}
        arms.append(
            _arm_line(
                name,
                history,
                {
                    row["run_id"]: row
                    for row in rows.summaries
                    if row["run_id"] in run_ids and row["status"] == "ok"
                },
                facts,
                pr_runs,
            )
        )
    v2_ids = {row["run_id"] for row in by_name["ralph-v2"]}
    task_reviews = _task_review_line(
        [row for row in rows.task_reviews if row["run_id"] in v2_ids],
        {
            row["run_id"]: row
            for row in rows.summaries
            if row["run_id"] in v2_ids and row["status"] == "ok"
        },
        [row for row in rows.stages if row["run_id"] in v2_ids],
        len(v2_ids),
    )

    return WorkflowsCard(
        start=start,
        end=end,
        cards=cards,
        strata=table,
        ralph=_ralph_line(by_name["ralph"], rows),
        arms=arms,
        trial=_trial_line(arms[0], arms[1], task_reviews),
        task_reviews=task_reviews,
    )


def _arm_line(
    name: str,
    history: Sequence[sqlite3.Row],
    summaries: Mapping[str, sqlite3.Row],
    facts: Mapping[tuple[str, int], sqlite3.Row],
    pr_runs: Mapping[tuple[str, int], Sequence[sqlite3.Row]],
) -> ArmLine:
    """One arm's numbers, from its own runs (see `ArmLine`)."""
    lease_ms = 0
    zero_touch = 0
    for row in history:
        lease_ms += _lease_ms(row)
        if row["pr_lookup"] != "found" or row["pr_number"] is None:
            continue
        key = (row["repo"], row["pr_number"])
        fact = facts.get(key)
        # An unread PR has no operator-commit count, so it is not a zero-touch merge yet:
        # the measure waits for GitHub's answer rather than crediting a run blind.
        if fact is None or fact["operator_commits"] != 0:
            continue
        if merged_by_workflow(pr_runs.get(key, ()), fact):
            zero_touch += 1

    quality: list[int] = []
    refuter: list[int] = []
    exhausted = 0
    for run in history:
        summary = summaries.get(run["run_id"])
        if summary is None:
            continue
        if summary["loop_exhausted"]:
            exhausted += 1
        round_one = _first_round(summary["first_round"])
        if round_one is None:
            continue
        total = round_one.total()
        if total is not None:
            quality.append(total)
        refuter_total = round_one.refuter_total()
        if refuter_total is not None:
            refuter.append(refuter_total)

    runs = len(history)
    quality_n, quality_mean, quality_sd = _mean_sd(quality)
    refuter_n, refuter_mean, refuter_sd = _mean_sd(refuter)
    lease_hours = lease_ms / 3_600_000
    rate = _rate(zero_touch, runs)
    return ArmLine(
        workflow=name,
        runs=runs,
        zero_touch=zero_touch,
        merge_rate=rate,
        merge_se=None if rate is None else math.sqrt(rate * (1 - rate) / runs),
        quality_n=quality_n,
        quality_mean=quality_mean,
        quality_sd=quality_sd,
        refuter_n=refuter_n,
        refuter_mean=refuter_mean,
        refuter_sd=refuter_sd,
        exhausted_runs=exhausted,
        lease_hours=lease_hours,
        lease_hours_per_merge=(lease_hours / zero_touch if zero_touch else None),
    )


def _trial_line(v1: ArmLine, v2: ArmLine, reviews: TaskReviewLine) -> TrialLine:
    """v2 - v1 on both co-primary measures, the guardrail and the stop rules."""
    # A review is one reviewer, two to a round (ralph-v2 11 step 4): the population
    # `measure.box_health` divides the same misses by, so this rule and C11 read one run at
    # one rate. `TaskReviewLine.reviews` counts rounds, because rows are what the store holds.
    reviewers = 2 * reviews.reviews
    line = TrialLine(miss_stop=reviewers > 0 and reviews.misses / reviewers > MISS_STOP_RATE)
    if v1.runs < 2 or v2.runs < 2:
        return line  # nothing to compare on one run, and the stop rules above still stand
    if v1.merge_rate is not None and v2.merge_rate is not None:
        diff = v2.merge_rate - v1.merge_rate
        se = math.sqrt(
            v1.merge_rate * (1 - v1.merge_rate) / v1.runs
            + v2.merge_rate * (1 - v2.merge_rate) / v2.runs
        )
        line = replace(
            line,
            merge_diff=diff,
            merge_se=se,
            merge_verdict=_verdict(diff, se, better_high=True),
            merge_stop=diff <= -0.15 and v2.runs >= MERGE_STOP_RUNS,
        )
    if (
        v1.quality_mean is not None
        and v2.quality_mean is not None
        and v1.quality_sd is not None
        and v2.quality_sd is not None
    ):
        diff = v2.quality_mean - v1.quality_mean
        se = math.sqrt(v1.quality_sd**2 / v1.quality_n + v2.quality_sd**2 / v2.quality_n)
        line = replace(
            line,
            quality_diff=diff,
            quality_se=se,
            quality_verdict=_verdict(diff, se, better_high=False),
        )
    if v1.lease_hours_per_merge is not None and v2.lease_hours_per_merge is not None:
        guardrail = v2.lease_hours_per_merge / v1.lease_hours_per_merge
        line = replace(
            line,
            guardrail=guardrail,
            lease_stop=guardrail > 2 and v2.runs >= LEASE_STOP_RUNS,
        )
    return line


def _verdict(diff: float, se: float, *, better_high: bool) -> str:
    """`better`, `worse` or `unclear` by the trial's 2-standard-error rule."""
    if abs(diff) <= 2 * se:
        return "unclear"
    return "better" if (diff > 0) == better_high else "worse"


def _task_review_line(
    reviews: Sequence[sqlite3.Row],
    summaries: Mapping[str, sqlite3.Row],
    stages: Sequence[sqlite3.Row],
    runs: int,
) -> TaskReviewLine:
    """The Task review loop's numbers over the window (see `TaskReviewLine`)."""
    verdicts = Counter(str(row["verdict"]) for row in reviews)
    by_round: dict[int, list[str]] = defaultdict(list)
    refute_served: Counter[str] = Counter()
    review_served: Counter[str] = Counter()
    disputes = withdrawals = misses = edges_covered = 0
    for row in reviews:
        by_round[int(row["round"])].append(str(row["verdict"]))
        refute_served[str(row["refute_served"])] += 1
        review_served[str(row["review_served"])] += 1
        disputes += int(row["disputed"] or 0)
        withdrawals += int(row["withdrawn"] or 0)
        misses += sum(
            1 for served in (row["refute_served"], row["review_served"]) if served == MISSED
        )
        if int(row["round"]) == 1:
            edges_covered += int(row["edges_covered"] or 0)

    plan_types: Counter[str] = Counter()
    edges_planned = 0
    for summary in summaries.values():
        one = _task_plans(summary["task_plans"])
        plan_types.update(one.types)
        edges_planned += one.edges

    box_ms: Counter[str] = Counter()
    for row in stages:
        if row["node_id"] in TASK_REVIEW_BOX_NODES:
            box_ms[str(row["node_id"])] += int(row["box_ms"] or 0)

    return TaskReviewLine(
        reviews=len(reviews),
        verdicts=dict(verdicts.most_common()),
        rejection_rate={
            number: sum(1 for verdict in names if verdict in REJECTED_VERDICTS) / len(names)
            for number, names in sorted(by_round.items())
        },
        blocked=verdicts.get("blocked", 0),
        disputes=disputes,
        withdrawals=withdrawals,
        refute_served={state: refute_served[state] for state in SERVED_STATES},
        review_served={state: review_served[state] for state in SERVED_STATES},
        misses=misses,
        ms=_percentiles([int(row["ms"] or 0) / 60_000 for row in reviews]),
        edges_planned=edges_planned,
        edges_covered=edges_covered,
        box_minutes={
            node: (box_ms[node] / 60_000 / runs if runs else 0.0)
            for node in TASK_REVIEW_BOX_NODES
        },
        plan_types=dict(plan_types.most_common()),
    )


def _first_round(text: str | None) -> FirstRound | None:
    """A stored `run_summary.first_round`; None for NULL (a run summarized before it)."""
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return FirstRound(
        **{
            name: parsed.get(name) if _is_count(parsed.get(name)) else None
            for name in FIRST_ROUND_FIELDS
        }
    )


def _task_plans(text: str | None) -> TaskPlans:
    """A stored `run_summary.task_plans`; empty for NULL (a run summarized before it)."""
    if not text:
        return TaskPlans()
    try:
        parsed = json.loads(text)
    except ValueError:
        return TaskPlans()
    if not isinstance(parsed, dict):
        return TaskPlans()
    types = parsed.get("types")
    return TaskPlans(
        types=Counter({
            str(name): int(count)
            for name, count in (types.items() if isinstance(types, dict) else ())
            if _is_count(count)
        }),
        goals=int(parsed.get("goals") or 0),
        edges=int(parsed.get("edges") or 0),
    )


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mean_sd(values: Sequence[int]) -> tuple[int, float | None, float | None]:
    """n, the mean and the **sample** standard deviation (None below 2 values)."""
    n = len(values)
    if n == 0:
        return 0, None, None
    mean = sum(values) / n
    if n < 2:
        return n, mean, None
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    return n, mean, math.sqrt(variance)



def _ralph_line(history: Sequence[sqlite3.Row], rows: ScorecardRows) -> RalphLine:
    """The ralph-only numbers, from the arm's own rows. Zeroes for an idle arm."""
    if not history:
        return RalphLine()
    run_ids = {row["run_id"] for row in history}
    summaries = {
        row["run_id"]: row
        for row in rows.summaries
        if row["run_id"] in run_ids and row["status"] == "ok"
    }

    iterations: dict[str, int] = defaultdict(int)
    refute_visits: dict[str, int] = defaultdict(int)
    refute_in = refute_cache = refute_out = 0
    for row in rows.stages:
        if row["run_id"] not in run_ids:
            continue
        node = row["node_id"]
        if node in ("iterate_t1", "iterate_t2", "iterate_t3", "iterate_t4"):
            iterations[row["run_id"]] += int(row["visits"])
        elif node == "refute":
            refute_visits[row["run_id"]] += int(row["visits"])
            refute_in += int(row["input_tokens"] or 0)
            refute_cache += int(row["cache_read_tokens"] or 0)
            refute_out += int(row["output_tokens"] or 0)

    sources: Counter[str] = Counter()
    exhausted = post_iterations = queued = 0
    for row in summaries.values():
        raw = row["task_sources"]
        if raw:
            try:
                sources.update(json.loads(raw))
            except ValueError:
                pass
        if row["loop_exhausted"]:
            exhausted += 1
        post_iterations += int(row["post_iterations"] or 0)
        if row["terminal_node"] == "report_queued":
            queued += 1

    return RalphLine(
        runs=len(history),
        iterations=_percentiles([iterations[run_id] for run_id in run_ids]),
        refute_visits=_percentiles([refute_visits[run_id] for run_id in run_ids]),
        tasks_by_source=dict(sorted(sources.items())),
        runs_exhausted=exhausted,
        post_iterations=post_iterations,
        queued_handoffs=queued,
        refute_input_tokens=refute_in,
        refute_cache_read_tokens=refute_cache,
        refute_output_tokens=refute_out,
    )


def workflows_to_json(card: WorkflowsCard) -> dict[str, object]:
    """The JSON `GET /api/scorecard/workflows` returns."""
    return {
        "window": {"from": card.start.isoformat(), "to": card.end.isoformat()},
        "workflows": {name: to_json(one) for name, one in card.cards.items()},
        "strata": [asdict(row) for row in card.strata],
        "ralph": asdict(card.ralph),
        "arms": [asdict(arm) for arm in card.arms],
        "trial": asdict(card.trial),
        "task_reviews": asdict(card.task_reviews),
    }


def workflows_to_markdown(card: WorkflowsCard) -> str:
    """Both arms as one table, then the strata, then the ralph line."""
    names = list(card.cards)
    header = ["", *names]
    summary_rows = [
        ["runs", *[str(card.cards[n].runs) for n in names]],
        ["PRs", *[str(card.cards[n].prs) for n in names]],
        ["merged by the workflow", *[str(card.cards[n].merged_workflow) for n in names]],
        ["merge rate", *[_arm_rate(card.cards[n].merge_rate) for n in names]],
        ["box-hours", *[f"{card.cards[n].box_hours:.1f}" for n in names]],
        [
            "box-hours per merge",
            *[
                "—" if card.cards[n].box_hours_per_merge is None
                else f"{card.cards[n].box_hours_per_merge:.2f}"
                for n in names
            ],
        ],
    ]
    lines = _md_rows(header, summary_rows)
    lines += ["", "## Strata: repo x coder pool", ""]
    lines += _md_rows(
        ["repo", "pool", "workflow", "runs", "PRs", "merged", "box-h", "box-h/merge"],
        [
            [
                row.repo,
                row.coder_pool,
                row.workflow,
                str(row.runs),
                str(row.prs),
                str(row.merged),
                f"{row.box_hours:.1f}",
                "—" if row.box_hours_per_merge is None else f"{row.box_hours_per_merge:.2f}",
            ]
            for row in card.strata
        ],
    )
    r = card.ralph
    lines += ["", "## ralph", ""]
    lines += _md_rows(
        ["number", "value"],
        [
            ["runs", str(r.runs)],
            ["Iterations per run p50 / p90", _arm_pct(r.iterations)],
            ["Refute visits per run p50 / p90", _arm_pct(r.refute_visits)],
            [
                "Tasks by source",
                ", ".join(f"{k} {v}" for k, v in r.tasks_by_source.items()) or "—",
            ],
            ["runs that ran out", str(r.runs_exhausted)],
            ["post-PR Iterations", str(r.post_iterations)],
            ["queued hand-offs", str(r.queued_handoffs)],
            [
                "Refuter tokens (in / cached / out)",
                f"{tokens(r.refute_input_tokens)} / {tokens(r.refute_cache_read_tokens)} / "
                f"{tokens(r.refute_output_tokens)}",
            ],
        ],
    )
    lines += _trial_markdown(card)
    lines += _task_review_markdown(card)
    return "\n".join(lines) + "\n"


def _trial_markdown(card: WorkflowsCard) -> list[str]:
    """The ralph-v2 trial's two arms, their difference and the stop rules.

    One row per measure with an arm column each, so the two arms are read side by side and
    the trial's co-primary comparison is the two lines under the table. Both arms are always
    there (`by_workflow` builds the pair), at zero for a window that ran none of them.
    """
    arms = card.arms
    trial = card.trial
    lines = ["", "## ralph-v2 trial", ""]
    lines += _md_rows(
        ["measure", *[arm.workflow for arm in arms]],
        [
            ["runs", *[str(arm.runs) for arm in arms]],
            ["zero-touch merges", *[str(arm.zero_touch) for arm in arms]],
            ["merge rate (standard error)", *[_se_cell(arm.merge_rate, arm.merge_se) for arm in arms]],
            [
                "quality, sum of the five: n / mean / sd",
                *[_nsd_cell(arm.quality_n, arm.quality_mean, arm.quality_sd) for arm in arms],
            ],
            [
                "quality, the Refuter's three: n / mean / sd",
                *[_nsd_cell(arm.refuter_n, arm.refuter_mean, arm.refuter_sd) for arm in arms],
            ],
            ["runs that ran out", *[str(arm.exhausted_runs) for arm in arms]],
            ["lease-hours", *[f"{arm.lease_hours:.1f}" for arm in arms]],
            ["lease-hours per zero-touch merge", *[_num(arm.lease_hours_per_merge) for arm in arms]],
        ],
    )
    lines += [""]
    lines += _md_rows(
        ["check", "value"],
        [
            ["merge, v2 - v1", _diff_cell(trial.merge_diff, trial.merge_se, trial.merge_verdict)],
            [
                "quality, v2 - v1",
                _diff_cell(trial.quality_diff, trial.quality_se, trial.quality_verdict),
            ],
            [
                "guardrail, v2 / v1",
                "—" if trial.guardrail is None
                else f"{trial.guardrail:.2f}x (the trial is lost above 2.00x)",
            ],
            [
                "stop rules",
                f"lease {'yes' if trial.lease_stop else 'no'},"
                f" merge {'yes' if trial.merge_stop else 'no'},"
                f" misses {'yes' if trial.miss_stop else 'no'}",
            ],
        ],
    )
    return lines


def _task_review_markdown(card: WorkflowsCard) -> list[str]:
    """`ralph-v2`'s Task review loop: what it reviewed, what it found and what it cost."""
    row = card.task_reviews
    rounds = ", ".join(
        f"round {number} {_pct(rate)}" for number, rate in row.rejection_rate.items()
    ) or "—"
    return ["", "## Task reviews", ""] + _md_rows(
        ["measure", "value"],
        [
            ["reviews (rounds)", str(row.reviews)],
            ["verdicts", _counts(row.verdicts) or "—"],
            ["rejection rate by round", rounds],
            ["Tasks blocked", str(row.blocked)],
            ["disputes / withdrawals", f"{row.disputes} / {row.withdrawals}"],
            [
                "served, Refuter: box / fallback / missed",
                " / ".join(str(row.refute_served.get(state, 0)) for state in SERVED_STATES),
            ],
            [
                "served, review: box / fallback / missed",
                " / ".join(str(row.review_served.get(state, 0)) for state in SERVED_STATES),
            ],
            ["misses (a reviewer with no valid result)", str(row.misses)],
            ["reviewer minutes p50 / p90", _arm_pct(row.ms)],
            ["edge cases planned / covered", f"{row.edges_planned} / {row.edges_covered}"],
            [
                "box minutes a run: " + " / ".join(TASK_REVIEW_BOX_NODES),
                " / ".join(f"{row.box_minutes.get(node, 0.0):.1f}" for node in TASK_REVIEW_BOX_NODES),
            ],
            ["plan types", _counts(row.plan_types) or "—"],
        ],
    )


def _se_cell(value: float | None, se: float | None) -> str:
    """A rate with its standard error, `50% (25%)`."""
    return "—" if value is None else f"{_pct(value)} ({_pct(se)})"


def _nsd_cell(n: int, mean: float | None, sd: float | None) -> str:
    return f"{n} / {_num(mean)} / {_num(sd)}"


def _diff_cell(diff: float | None, se: float | None, verdict: str | None) -> str:
    """A difference with its standard error and the rule's verdict, `+0.67 ± 0.22: better`."""
    if diff is None:
        return "—"
    return f"{diff:+.2f} ± {'n/a' if se is None else f'{se:.2f}'}: {verdict or 'unclear'}"


def _arm_rate(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _arm_pct(value: Percentiles) -> str:
    if value.p50 is None or value.p90 is None:
        return "—"
    return f"{value.p50:g} / {value.p90:g}"


def _md_rows(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return out
