"""Load and validate the scheduler's configuration.

`repos.toml` is the operator-editable half: one `[[repo]]` table per target
repository. Everything else is deployment-shaped — the fabro API base URL, the
port it listens on, where its SQLite file lives — and comes from the environment
instead, for the same reason the compose project already passes `${VAR}` rather
than literals: this repository is public, and a host-specific value belongs in
`.env`.

**Credentials are deliberately not in `SchedulerConfig`.** `GITHUB_TOKEN` (draft
06) and `FABRO_API_TOKEN` (draft 07) are read at the point of use and never land
in a dataclass that gets logged, rendered into `/health` or passed around the app.
A config object that cannot hold a secret is one fewer thing to audit.

`load_config` is pure apart from the environment mapping handed to it, so the
whole of its behaviour is testable without a container, a network or a server.
It never falls back to a default repo list: a missing or empty set of `[[repo]]`
tables is an error, so a file with a typo in it cannot look like a scheduler that
has deliberately been given nothing to do.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from .disk import (
    DEFAULT_DISPATCH_DISK_HYSTERESIS_GB,
    DEFAULT_DISPATCH_DISK_PATHS,
    DEFAULT_DISPATCH_MIN_FREE_GB,
)
from .peak import PeakConfigError, PeakHours, parse_peak_hours

# "owner/repo". Permissive about the characters GitHub allows, strict about the
# shape: exactly one slash, neither side empty. A name that fails this cannot be
# passed to the GitHub API as an owner/repo pair.
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# One short name for a repository, as agents write it (`wfs#2080`).
REPO_ALIAS = re.compile(r"^[A-Za-z0-9._-]+$")

# The LiteLLM model groups that address one coder instance each — decision 12.
# `coders`, the load-balanced group over both boxes, is deliberately absent: the
# scheduler exists precisely to stop relying on the load balancer's choice. It
# stays defined in LiteLLM as the fallback for unpinned and hand-fired runs.
DEFAULT_CODER_POOLS = ("coders-a", "coders-b", "coders-c", "coders-d")

DEFAULT_FABRO_API_URL = "http://10.10.0.32:32276/api/v1"
DEFAULT_PORT = 32280

# The compose `scheduler-data` volume. Created in the image so the named volume
# seeds from it with uid 1000; see the Dockerfile.
DEFAULT_DB_PATH = "/data/scheduler.db"

# Decision 5's `T`. Anything that has waited strictly longer than this jumps the
# front of the queue regardless of repo priority.
DEFAULT_STARVATION_CEILING_SECONDS = 4 * 60 * 60

# Decision 8: GitHub every 60s, conditionally.
DEFAULT_GITHUB_POLL_SECONDS = 60

# `ralph`'s share of the queue (ADR 0029 D2, O2, O21, O24). Only 0, 50 and 100 are
# allowed: 0 is the kill switch (every issue to `backlog`), 50 is the trial's issue
# parity, and 100 is the trial's result (2026-11-02): every issue to `ralph` except a
# `fast-path` one, which is `backlog`'s fast lane at every share. A percentage is not a
# knob: a value in between would make the arm a coin toss that no run's history records.
DEFAULT_RALPH_SHARE = 0
RALPH_SHARES = (0, 50, 100)

# The provider's peak hours (`peak.py`): no new dispatch from `lead` minutes before a
# window opens until it closes. Off when unset, so a test or a fresh host never blocks
# by surprise; compose sets z.ai's window. The lead is 15 minutes by default.
DEFAULT_PEAK_LEAD_MINUTES = 15

# --- the background lane (more-local-inference C7, D4, D7, D8, D12) -----------------
#
# One switch turns the lane off with no other deploy. It is `off` by default **in code
# and in `ops/docker-compose.yaml`**: the operator turns it on in `~/fabro/.env`.
DEFAULT_BACKGROUND = False

# How long strix-infer must report a box idle before a background run may start on it.
DEFAULT_BG_GAP_SECONDS = 30

# Run slots fabro must have free before a background run starts: `max_concurrent_runs`
# minus every run in an active status, `pending` included (D8).
DEFAULT_BG_SLOT_RESERVE = 3

# `pool=url` pairs, the same format `fabro-io box-claim` reads (C3). Empty means no box
# is in the lane and the lane does nothing at all.
DEFAULT_BOX_HEALTH = ""

# fabro's own `server.scheduler.max_concurrent_runs`. Keep the two equal: the scheduler
# reads it only to count free slots, and a value above fabro's would hand background
# runs slots that do not exist.
DEFAULT_MAX_CONCURRENT_RUNS = 8

# The job kinds the lane knows, in D10's value order: the lowest number is picked first.
# Task 05 added only `box-noop`, which exists to measure cancels. Tasks 07, 08 and 10 add their
# own kinds here as they build their producers. Priority 1 was task 06's `pr-doctor`, retired on
# 2026-10-30 and removed on 2026-10-31: its run held a box for ~40 minutes, and a coder run's
# gaps reach that length unpredictably, so its one queued job waited 23 hours and never started.
BACKGROUND_JOB_PRIORITY = {
    "issue-precheck": 2,
    "context-pack": 3,
    # Task 10's flaky investigation sits with the pack (task 10 moved the two below it down by
    # two): both feed an issue that is already in the queue, and what they write is read by the
    # run that takes it.
    "flaky-investigation": 3,
    # The repository orientation (task 10): no queued run waits for it, but every planner that
    # reads it explores less, so it comes after the two that feed one issue and before the
    # measurement.
    "repo-orientation": 4,
    # The precheck calibration (pre-review-and-calibration, Part A): after everything that
    # feeds a queued issue, because it measures a package that is already running while the
    # other three change what a run does. Queued rows keep the priority they were enqueued
    # with; `ops/README.md` has the SQL that moves a deployment's waiting rows.
    "precheck-calibration": 5,
    "box-noop": 6,
}

# How many of the next issues in dispatch order, **in each repository**, the precheck producer
# keeps checked (more-local-inference 07), and the context-pack producer keeps mapped once the
# precheck passed them (08). Per repository because the boxes take the diversity pass
# (`dispatch.choose_next`): each box runs the head of a different repository, so the head of
# the whole ranked queue is one repository's issues and misses half the dispatches. A window
# of 10 over the whole queue did exactly that on 2026-10-30/31: every job went to writers-app,
# and lawncare-saas, 7 of the 11 overnight dispatches, got none. The head of each repository is
# what its next runs take, so a job further down would mostly go stale before its issue runs.
# 0 queues none of either.
DEFAULT_PRECHECK_PER_REPO = 3

# --- the precheck's holds (more-local-inference D20) ---------------------------------
#
# A verdict of `already_done`, `contradiction`, `unclear` or `blocked` may keep an issue out of
# dispatch until a human releases it (or, for `blocked`, until the cited blocker closes). It is
# **off by default, in code and in compose**: the precheck has run in shadow mode since
# 2026-10-30, and a hold is only turned on per verdict once the calibration
# (`GET /api/background/calibration`) shows that verdict is right. A hold that is wrong keeps
# good work out of the queue, which is worse than a run that was not worth starting.
DEFAULT_PRECHECK_HOLDS = False

# The verdicts whose hold is applied when the switch is on. `unverifiable` is accepted and not
# in the default: a criterion the pipeline cannot check is a question for the merge phase, not a
# reason to keep the work from starting.
DEFAULT_PRECHECK_HOLD_VERDICTS = ("already_done", "contradiction", "unclear", "blocked")

# What `SCHEDULER_PRECHECK_HOLD_VERDICTS` may name. `ready` is **refused**, not ignored: it is
# the absence of a hold, and a list that named it would take the whole queue out at once — the
# one setting here that could stop all work with no error anywhere.
PRECHECK_HOLDABLE = ("already_done", "blocked", "contradiction", "unclear", "unverifiable")

# The label prefix a held issue carries: `precheck-already-done`, `precheck-blocked`, and so on
# (C6). Any label with this prefix keeps an issue out of the queue **whatever the switch says**,
# because a label a human left on is still a hold.
PRECHECK_HOLD_PREFIX = "precheck-"

# The first `dispatched_at` whose pre-review arm is a measurement (`GET /api/background/measure`,
# Phase 4). The arm was the ralph trial's bit until the 2026-10-31 fix, so a run dispatched
# before this ran under the old arm and is left out of the pre-review figures. The default is
# the fix's own date; the operator sets it to the deploy's timestamp, because the fix does
# nothing until it is pushed and `main` deploys every workflow at fire time.
DEFAULT_PRE_REVIEW_ARM_SINCE = "2026-11-01T00:00:00+00:00"

# How many precheck-calibration jobs the producer keeps queued (pre-review-and-calibration,
# Part A). The lane runs one background job at a time per box, so a deeper queue only ever
# means the next job is ready when a gap opens; 4 is a day or two of gaps. The oldest runs'
# issues are the ones whose verdicts are least like a live precheck's (the code has moved on),
# so the producer takes the newest first and this cap keeps it from hoarding them.
DEFAULT_CALIBRATION_QUEUED = 4

# The oldest dispatched_at the calibration producer reaches back to. 2026-09-25 is where the
# scheduler's `run_history` starts holding labelled runs (its first archived run), which is
# what makes this measurement possible without waiting for live prechecks. An ISO date: it is
# compared as a string prefix against `dispatched_at`, which is ISO-8601 UTC.
DEFAULT_CALIBRATION_SINCE = "2026-09-25"

# Gap prediction (C7 step 9): the coder loop's median remaining gap once a node starts, in
# minutes, keyed by `(workflow, node id)`. **Measured** 2026-10-30 over the 164 released
# `backlog` and `ralph` issue runs on coders-a/b since 2026-10-21: for every visit of a non-box
# node, the gap's end (the next box-stage start, or the lease's release) minus the visit's
# *start*, median per node, floored to whole minutes. That is what the lane sees: it decides
# while the node is running, usually in its first minute. Nodes with fewer than 5 visits or a
# median under a minute are left out and predict 0, and so are `plan`, a box stage in one arm
# of the box-plan trial, and ralph's `resolve_merge`, a box stage in backlog (n=5). The first table (2026-10-28) measured from a visit's *end*.
#
# A median, not a low percentile, on purpose: a background run cancelled by the coder loop
# costs only its own box time, which is free, and the claim's wait (4-6 s, measured in
# Phase 1). A job that fits a node's median finishes at least half the time it starts there.
# `.scratch/more-local-inference/gap-analysis.md` has the per-node odds for each job length.
# Re-measure when the graphs change: the scripts are next to that file.
BACKGROUND_GAP_MINUTES: dict[tuple[str, str], int] = {
    ("backlog", "autofix"): 14,  # n=259
    ("backlog", "claim"): 12,  # n=97
    ("backlog", "decompose"): 11,  # n=94
    ("backlog", "extra_gate"): 17,  # n=93
    ("backlog", "extra_prep"): 17,  # n=135
    ("backlog", "file_remainder"): 24,  # n=84
    ("backlog", "final_merge"): 32,  # n=73
    ("backlog", "human_rescue"): 240,  # n=16
    ("backlog", "integrate"): 9,  # n=285
    ("backlog", "next_task"): 6,  # n=422
    ("backlog", "open_pr"): 25,  # n=84
    ("backlog", "open_pr_prep"): 32,  # n=73
    ("backlog", "partial_remainder"): 39,  # n=8
    ("backlog", "pr_handoff"): 24,  # n=84
    ("backlog", "prep"): 12,  # n=97
    ("backlog", "prep_review"): 13,  # n=291
    ("backlog", "quality"): 5,  # n=114
    ("backlog", "rescue_brief"): 240,  # n=16
    ("backlog", "review"): 12,  # n=296
    ("backlog", "review_gate"): 9,  # n=297
    ("backlog", "review_merge.ci_fix_gate"): 1,  # n=6
    ("backlog", "review_merge.ci_fix_t1"): 10,  # n=5
    ("backlog", "review_merge.fix_gate"): 3,  # n=84
    ("backlog", "review_merge.hygiene"): 1,  # n=119
    ("backlog", "review_merge.prep_review"): 24,  # n=84
    ("backlog", "review_merge.refute"): 1,  # n=119
    ("backlog", "review_merge.refute_fix_gate"): 5,  # n=36
    ("backlog", "review_merge.refute_prep"): 1,  # n=119
    ("backlog", "review_merge.review_fix"): 12,  # n=86
    ("backlog", "review_merge.rm_setup"): 24,  # n=84
    ("backlog", "review_merge.spec"): 19,  # n=81
    ("backlog", "review_merge.spec_gate"): 13,  # n=81
    ("backlog", "review_merge.standards"): 24,  # n=84
    ("backlog", "review_merge.standards_gate"): 19,  # n=81
    ("backlog", "review_merge.validate"): 3,  # n=116
    ("backlog", "review_merge.watch_checks"): 9,  # n=35
    ("backlog", "route_lane"): 11,  # n=97
    ("backlog", "spec"): 9,  # n=116
    ("backlog", "spec_gate"): 5,  # n=115
    ("backlog", "standards"): 15,  # n=119
    ("backlog", "standards_gate"): 9,  # n=115
    ("backlog", "task_block_gate"): 14,  # n=260
    ("backlog", "validate"): 13,  # n=291
    ("ralph", "autofix"): 2,  # n=209
    ("ralph", "check"): 1,  # n=270
    ("ralph", "claim"): 5,  # n=67
    ("ralph", "final_check"): 13,  # n=93
    ("ralph", "final_merge"): 13,  # n=93
    ("ralph", "hygiene"): 11,  # n=92
    ("ralph", "prep"): 5,  # n=67
    ("ralph", "refute"): 11,  # n=92
    ("ralph", "refute_gate"): 10,  # n=92
    ("ralph", "refute_prep"): 11,  # n=92
    ("ralph", "review_prep"): 11,  # n=65
    ("ralph", "spec"): 4,  # n=65
    ("ralph", "standards"): 11,  # n=65
    ("ralph", "task_block_gate"): 2,  # n=210
    ("ralph", "watch_checks"): 2,  # n=23
}

# Any other node of a coder loop, and any other workflow, predicts 0 (C7: "an unknown key
# predicts 0"): an unmeasured gap is not a gap to commit a job to.
BACKGROUND_GAP_MINUTES_DEFAULT = 0

# The loops the table covers today. `pr-review` leases a box too (task 06), but no gap
# after its stages has been measured yet, so it predicts 0.
BACKGROUND_GAP_WORKFLOWS = ("backlog", "ralph")

# The workflows whose box stages a background run must never overlap (D1). A coder loop
# with no current box stage leaves its whole remaining gap to the lane.
CODER_LOOP_WORKFLOWS = ("backlog", "ralph", "pr-review")

# The run probe's beat: how often the background thread refreshes the cached
# per-lease task count and current stage that the page renders. Stages last tens
# of seconds to minutes each, so 30s keeps the display visibly current while
# making at most ~2 fabro API calls + ~2 sandbox reads per beat (one per leased
# coder instance, and there are at most four). `probe.py` owns the reasoning;
# this is the handle an operator thins it with.
DEFAULT_RUN_PROBE_SECONDS = 30

# The scorecard collector's beat (issue #22, `collector.py`): each tick summarizes
# a couple of released runs and reads up to ten PRs.
DEFAULT_SCORECARD_SECONDS = 30

# The identities the factory commits as, comma-separated: GitHub logins, or whole
# email addresses. A PR commit by anyone else is an operator commit on the
# scorecard (`prfacts.is_automation_commit`). Runs before the run identity was set
# committed as `Fabro <noreply@fabro.sh>`.
DEFAULT_AUTOMATION_AUTHOR = "andrews-ai-agent,noreply@fabro.sh"

# Every key a `[[repo]]` table may carry. Unknown keys are errors rather than
# warnings: `priorty = 0` would otherwise be silently ignored and the row would
# sort by whatever the default turned out to be.
_REPO_KEYS = frozenset({"name", "priority", "environment_id", "enabled", "aliases"})


class ConfigError(ValueError):
    """A configuration that cannot be used.

    The message always names the offending key, so the operator has the line to
    look at without reading a traceback.
    """


def predicted_gap_minutes(workflow: str, node: str) -> int:
    """The coder loop's predicted remaining gap after `(workflow, node)`, in minutes.

    An unknown key predicts 0, so only a job of 5 minutes or less can start on it: with
    no measurement, starting a long job is a guess that costs the coder loop its box.
    """
    known = BACKGROUND_GAP_MINUTES.get((workflow, node))
    if known is not None:
        return known
    if workflow in BACKGROUND_GAP_WORKFLOWS:
        return BACKGROUND_GAP_MINUTES_DEFAULT
    return 0


def parse_box_health(raw: str) -> dict[str, str]:
    """`SCHEDULER_BOX_HEALTH` into `{pool: health url}` (C3's format).

    Comma-separated `pool=url` pairs. Whitespace around a pair, around either side of
    the `=`, and around a trailing comma are tolerated; a pair without an `=` or with an
    empty side is skipped rather than guessed at.
    """
    pairs: dict[str, str] = {}
    for entry in raw.split(","):
        pool, sep, url = entry.partition("=")
        if not sep:
            continue
        pool, url = pool.strip(), url.strip()
        if pool and url:
            pairs[pool] = url
    return pairs


def box_siblings(box_health: Mapping[str, str], pool: str) -> list[str]:
    """The pools that share `pool`'s health URL, `pool` included (two-slot boxes C1).

    Two pools on one URL *is* the declaration that they are one box (D1), so this is the
    scheduler's whole notion of a box and the same function `ops/fabro-io/src/boxclaim.rs`
    implements in Rust. Values are compared as `parse_box_health` stored them, which is
    already stripped. Sorted, and empty for a pool the map does not name; a pool nobody
    shares is a one-slot box, which is the layout the lane shipped with.
    """
    url = box_health.get(pool)
    if url is None:
        return []
    return sorted(other for other, other_url in box_health.items() if other_url == url)


def box_host(box_health: Mapping[str, str], pool: str) -> str | None:
    """The host of the local inference box `pool` runs on, from its health URL, or None.

    For the queue page's *Box* column: `coders-a` and `coders-b` both read `10.10.0.29` once
    they share a URL (two-slot boxes D1). A pool outside `SCHEDULER_BOX_HEALTH` has no box the
    scheduler knows of, so None (rendered "—").
    """
    url = box_health.get(pool)
    if url is None:
        return None
    return urlsplit(url).hostname or None


def boxes(box_health: Mapping[str, str]) -> list[tuple[str, list[str]]]:
    """The lane's boxes as `(first pool, siblings)`, ordered by first pool (two-slot boxes C2).

    The background lane iterates this instead of `box_health`'s keys, so it decides once per
    box: one `/health` read per URL per tick, one background run per box (D3). The first
    pool is the box's name and the pool its background run is dispatched under, so the
    grouping is stable however the variable is ordered.
    """
    out: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for pool in sorted(box_health):
        if pool in seen:
            continue
        siblings = box_siblings(box_health, pool)
        seen.update(siblings)
        out.append((pool, siblings))
    return out


@dataclass(frozen=True)
class RepoConfig:
    """One target repository."""

    name: str  # "owner/repo"
    priority: int  # signed; smaller is more urgent
    environment_id: str
    enabled: bool = True
    # Short names agents use for the repo in prose. Nothing schedules by them; the
    # weekly retro's guard withholds any write that names one (ADR 0026 D9).
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchedulerConfig:
    """Everything the service needs to start.

    Holds no credential — see the module docstring.
    """

    repos: tuple[RepoConfig, ...]
    coder_pools: tuple[str, ...]
    fabro_api_url: str
    port: int = DEFAULT_PORT
    db_path: Path = field(default_factory=lambda: Path(DEFAULT_DB_PATH))
    starvation_ceiling: timedelta = field(
        default_factory=lambda: timedelta(seconds=DEFAULT_STARVATION_CEILING_SECONDS)
    )
    github_poll_seconds: int = DEFAULT_GITHUB_POLL_SECONDS
    # Cached per-lease task count and current stage; see `probe.py`.
    run_probe_seconds: int = DEFAULT_RUN_PROBE_SECONDS
    scorecard_seconds: int = DEFAULT_SCORECARD_SECONDS
    automation_author: str = DEFAULT_AUTOMATION_AUTHOR
    # 0, 50 or 100; see `DEFAULT_RALPH_SHARE`. Read by `choose_workflow`.
    ralph_share: int = DEFAULT_RALPH_SHARE
    # When dispatch pauses for the provider's peak hours; see `peak.py`. Off by default.
    peak_hours: PeakHours = field(default_factory=PeakHours)
    # The background lane (C7, D12). `off` means no new background run starts; running
    # ones finish.
    background: bool = DEFAULT_BACKGROUND
    bg_gap_seconds: int = DEFAULT_BG_GAP_SECONDS
    bg_slot_reserve: int = DEFAULT_BG_SLOT_RESERVE
    # `{pool: /health url}`, from `SCHEDULER_BOX_HEALTH`. Empty = no box in the lane.
    box_health: dict[str, str] = field(default_factory=dict)
    max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS
    # Which job kinds' producers may enqueue. Defaults to every kind the lane knows.
    bg_kinds: tuple[str, ...] = field(
        default_factory=lambda: _live_kinds()
    )
    # The precheck and pack producers' reach: the next N issues of each repository.
    precheck_per_repo: int = DEFAULT_PRECHECK_PER_REPO
    # The calibration producer: how many jobs it keeps queued, and how far back it looks.
    calibration_queued: int = DEFAULT_CALIBRATION_QUEUED
    calibration_since: str = DEFAULT_CALIBRATION_SINCE
    # The precheck's holds (D20). Off by default; a held issue carries `precheck-<verdict>`,
    # and the queue skips any issue carrying a `precheck-` label whether or not this is on.
    precheck_holds: bool = DEFAULT_PRECHECK_HOLDS
    precheck_hold_verdicts: tuple[str, ...] = DEFAULT_PRECHECK_HOLD_VERDICTS
    # The first dispatched_at whose pre-review arm is a measurement (Phase 4).
    pre_review_arm_since: str = DEFAULT_PRE_REVIEW_ARM_SINCE
    # The dispatch disk gate (issue #51): no new run starts below this much free space,
    # and dispatch resumes above the minimum plus the hysteresis band.
    dispatch_min_free_gb: int = DEFAULT_DISPATCH_MIN_FREE_GB
    dispatch_disk_hysteresis_gb: int = DEFAULT_DISPATCH_DISK_HYSTERESIS_GB
    dispatch_disk_paths: tuple[str, ...] = DEFAULT_DISPATCH_DISK_PATHS

    def ordered_repos(self) -> list[RepoConfig]:
        """*Every* loaded repo, ordered: priority ascending, then name ascending.

        The file's own order is preserved on `repos` — it is what the operator
        wrote, and useful when reading the config back — so ordering is applied
        here rather than at parse time.

        **Disabled rows are included**, because this answers "what did I load?"
        and `/health` reports the flag. It is not the list to schedule from: use
        `schedulable_repos`. The two are separate methods rather than one with a
        flag so that reaching for the wrong one is visible at the call site.
        """
        return sorted(self.repos, key=lambda repo: (repo.priority, repo.name))

    def schedulable_repos(self) -> list[RepoConfig]:
        """The repos work may be dispatched for, in scheduling order.

        `enabled = false` keeps the row and takes the repo out of all scheduling
        (decision 4's companion rule), so every consumer that picks what to run
        next reads this, never `ordered_repos`.
        """
        return [repo for repo in self.ordered_repos() if repo.enabled]

    def repo_named(self, name: str) -> RepoConfig | None:
        """The row for `name`, matched the way duplicates were detected.

        Case-insensitively, because GitHub resolves `o/Repo` and `o/repo` to one
        repository and `load_config` already refuses to let two rows exist for
        it — so a caller asking for the other spelling means this row. Returns
        `None` for a repo that is not configured at all; whether that is an error
        is the caller's decision, and `enabled` is reported on the row rather than
        filtered here so the two cases stay tellable apart.
        """
        wanted = name.strip().casefold()
        for repo in self.repos:
            if repo.name.casefold() == wanted:
                return repo
        return None


def load_config(path: Path, env: Mapping[str, str] | None = None) -> SchedulerConfig:
    """Read `path` and the environment into a validated `SchedulerConfig`.

    Raises `ConfigError` for anything wrong. Everything it raises for names the
    key at fault.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    data = _read_toml(path)
    return SchedulerConfig(
        repos=_parse_repos(data),
        coder_pools=DEFAULT_CODER_POOLS,
        fabro_api_url=_env_str(environ, "FABRO_API_URL", DEFAULT_FABRO_API_URL),
        port=_env_port(environ, "SCHEDULER_PORT", DEFAULT_PORT),
        db_path=Path(_env_str(environ, "SCHEDULER_DB", DEFAULT_DB_PATH)),
        starvation_ceiling=timedelta(
            seconds=_env_positive_int(
                environ,
                "SCHEDULER_CEILING_SECONDS",
                DEFAULT_STARVATION_CEILING_SECONDS,
            )
        ),
        github_poll_seconds=_env_positive_int(
            environ, "SCHEDULER_GITHUB_POLL_SECONDS", DEFAULT_GITHUB_POLL_SECONDS
        ),
        run_probe_seconds=_env_positive_int(
            environ, "SCHEDULER_RUN_PROBE_SECONDS", DEFAULT_RUN_PROBE_SECONDS
        ),
        scorecard_seconds=_env_positive_int(
            environ, "SCHEDULER_SCORECARD_SECONDS", DEFAULT_SCORECARD_SECONDS
        ),
        automation_author=_env_str(
            environ, "SCHEDULER_AUTOMATION_AUTHOR", DEFAULT_AUTOMATION_AUTHOR
        ),
        ralph_share=_env_ralph_share(environ),
        peak_hours=_env_peak_hours(environ),
        background=_env_background(environ),
        bg_gap_seconds=_env_int_at_least(
            environ, "SCHEDULER_BG_GAP_S", DEFAULT_BG_GAP_SECONDS, 0
        ),
        bg_slot_reserve=_env_int_at_least(
            environ, "SCHEDULER_BG_SLOT_RESERVE", DEFAULT_BG_SLOT_RESERVE, 0
        ),
        box_health=parse_box_health(
            _env_str(environ, "SCHEDULER_BOX_HEALTH", DEFAULT_BOX_HEALTH)
        ),
        max_concurrent_runs=_env_int_at_least(
            environ, "SCHEDULER_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS, 1
        ),
        bg_kinds=_env_bg_kinds(environ),
        precheck_per_repo=_env_int_at_least(
            environ, "SCHEDULER_PRECHECK_PER_REPO", DEFAULT_PRECHECK_PER_REPO, 0
        ),
        calibration_queued=_env_int_at_least(
            environ, "SCHEDULER_CALIBRATION_QUEUED", DEFAULT_CALIBRATION_QUEUED, 0
        ),
        calibration_since=_env_str(
            environ, "SCHEDULER_CALIBRATION_SINCE", DEFAULT_CALIBRATION_SINCE
        ),
        precheck_holds=_env_switch(environ, "SCHEDULER_PRECHECK_HOLDS", DEFAULT_PRECHECK_HOLDS),
        precheck_hold_verdicts=_env_verdicts(environ),
        pre_review_arm_since=_env_str(
            environ, "SCHEDULER_PRE_REVIEW_ARM_SINCE", DEFAULT_PRE_REVIEW_ARM_SINCE
        ),
        dispatch_min_free_gb=_env_int_at_least(
            environ,
            "SCHEDULER_DISPATCH_MIN_FREE_GB",
            DEFAULT_DISPATCH_MIN_FREE_GB,
            0,
        ),
        dispatch_disk_hysteresis_gb=_env_int_at_least(
            environ,
            "SCHEDULER_DISPATCH_DISK_HYSTERESIS_GB",
            DEFAULT_DISPATCH_DISK_HYSTERESIS_GB,
            0,
        ),
        dispatch_disk_paths=_env_dispatch_disk_paths(environ),
    )


def _read_toml(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: no such file") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read: {exc}") from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path}: not valid UTF-8") from exc

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    return data


def _parse_repos(data: Mapping[str, object]) -> tuple[RepoConfig, ...]:
    rows = data.get("repo")
    if rows is None:
        raise ConfigError(
            "repo: no [[repo]] blocks; refusing to start with an empty work list"
        )
    if not isinstance(rows, list):
        raise ConfigError("repo: expected one or more [[repo]] tables")

    repos: list[RepoConfig] = []
    # casefolded name -> (index, name as written), for the duplicate check
    first_seen: dict[str, tuple[int, str]] = {}
    for index, row in enumerate(rows):
        repos.append(_parse_repo(row, index, first_seen))

    if not repos:
        raise ConfigError(
            "repo: no [[repo]] blocks; refusing to start with an empty work list"
        )

    return tuple(repos)


def _parse_repo(
    row: object, index: int, first_seen: dict[str, tuple[int, str]]
) -> RepoConfig:
    where = f"repo[{index}]"

    if not isinstance(row, dict):
        raise ConfigError(f"{where}: expected a table")

    unknown = sorted(set(row) - _REPO_KEYS)
    if unknown:
        raise ConfigError(f"{where}.{unknown[0]}: unknown key")

    name = row.get("name")
    if not isinstance(name, str) or not REPO_NAME.match(name):
        raise ConfigError(
            f"{where}.name: {name!r} is not an owner/repo name"
        )
    # Compared case-insensitively, stored as written. GitHub resolves
    # `o/Repo` and `o/repo` to one repository, so two rows differing only in case
    # are one repo with two queue entries — and the rule that there is at most one
    # in-flight run per repo (decision 6) is enforced per row. Keeping the
    # operator's spelling matters because it is what the GitHub API is called with.
    key = name.casefold()
    if key in first_seen:
        first_index, first_name = first_seen[key]
        also = "" if first_name == name else f" (spelled {first_name!r} there)"
        raise ConfigError(
            f"{where}.name: duplicate repo {name!r}, already declared at "
            f"repo[{first_index}]{also}"
        )
    first_seen[key] = (index, name)

    # `bool` is a subclass of `int`, so `priority = true` would otherwise parse
    # as 1 and quietly sort the repo ahead of everything at priority 2.
    priority = row.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ConfigError(
            f"{where}.priority: {priority!r} is not an integer "
            "(required; smallest wins, so negative values are valid)"
        )

    environment_id = row.get("environment_id")
    if not isinstance(environment_id, str) or not environment_id.strip():
        raise ConfigError(f"{where}.environment_id: must be a non-empty string")

    enabled = row.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}.enabled: {enabled!r} is not a boolean")

    aliases = row.get("aliases", [])
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and REPO_ALIAS.match(alias) for alias in aliases
    ):
        raise ConfigError(
            f"{where}.aliases: {aliases!r} is not a list of short names ([A-Za-z0-9._-]+)"
        )

    return RepoConfig(
        name=name,
        priority=priority,
        environment_id=environment_id,
        enabled=enabled,
        aliases=tuple(aliases),
    )


def _env_str(environ: Mapping[str, str], key: str, default: str) -> str:
    value = environ.get(key, "").strip()
    return value or default


def _env_ralph_share(environ: Mapping[str, str]) -> int:
    """`SCHEDULER_RALPH_SHARE`: 0 (the default, and the kill switch), 50 or 100.

    Anything else — `25`, `yes`, `-1`, an empty-but-present value — is a
    `ConfigError` naming the key: nothing in a run's history says which share
    produced it, so the arm must follow from the issue alone.
    """
    key = "SCHEDULER_RALPH_SHARE"
    raw = environ.get(key, "").strip()
    if not raw:
        return DEFAULT_RALPH_SHARE
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{key}: {raw!r} is not an integer; use 0 (off), 50 (the trial) or 100"
        ) from exc
    if value not in RALPH_SHARES:
        raise ConfigError(
            f"{key}: {value} is not one of {', '.join(str(v) for v in RALPH_SHARES)}"
        )
    return value


def _env_background(environ: Mapping[str, str]) -> bool:
    """`SCHEDULER_BACKGROUND`: `on` or `off` (C7, D12).

    Anything else is a `ConfigError`, not `off`: a typo would otherwise read as "the
    operator turned the lane off", which is the one answer they would believe.
    """
    key = "SCHEDULER_BACKGROUND"
    raw = environ.get(key, "").strip().lower()
    if not raw:
        return DEFAULT_BACKGROUND
    if raw == "on":
        return True
    if raw == "off":
        return False
    raise ConfigError(f"{key}: {raw!r} is not 'on' or 'off'")


def _live_kinds() -> tuple[str, ...]:
    """Every kind the lane knows, in D10's value order."""
    return tuple(BACKGROUND_JOB_PRIORITY)


def _env_bg_kinds(environ: Mapping[str, str]) -> tuple[str, ...]:
    """`SCHEDULER_BG_KINDS`: the job kinds whose producers run (C7).

    Default is every live kind. An unknown kind is a `ConfigError`: a name that matches
    no producer would silently turn nothing on.
    """
    key = "SCHEDULER_BG_KINDS"
    raw = environ.get(key, "").strip()
    if not raw:
        return _live_kinds()
    kinds = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [kind for kind in kinds if kind not in BACKGROUND_JOB_PRIORITY]
    if unknown:
        raise ConfigError(
            f"{key}: {unknown[0]!r} is not a job kind; expected one of "
            f"{', '.join(BACKGROUND_JOB_PRIORITY)}"
        )
    return kinds


def _env_switch(environ: Mapping[str, str], key: str, default: bool) -> bool:
    """An `on`/`off` switch. Anything else is a `ConfigError`, as `SCHEDULER_BACKGROUND` is.

    A typo must not read as "the operator turned it off": that is the answer they would
    believe, and the one that hides a hold they meant to apply (or an outage they meant to
    stop).
    """
    raw = environ.get(key, "").strip().lower()
    if not raw:
        return default
    if raw == "on":
        return True
    if raw == "off":
        return False
    raise ConfigError(f"{key}: {raw!r} is not 'on' or 'off'")


def _env_verdicts(environ: Mapping[str, str]) -> tuple[str, ...]:
    """`SCHEDULER_PRECHECK_HOLD_VERDICTS`: which verdicts hold an issue (D20).

    An empty value is the default set, not "none": use `SCHEDULER_PRECHECK_HOLDS=off` to turn
    the holds off. An unknown verdict is a `ConfigError`, because a name that matches no verdict
    would silently hold nothing.
    """
    key = "SCHEDULER_PRECHECK_HOLD_VERDICTS"
    raw = environ.get(key, "").strip()
    if not raw:
        return DEFAULT_PRECHECK_HOLD_VERDICTS
    verdicts = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [verdict for verdict in verdicts if verdict not in PRECHECK_HOLDABLE]
    if unknown:
        raise ConfigError(
            f"{key}: {unknown[0]!r} is not a verdict that can hold an issue; expected one of "
            f"{', '.join(PRECHECK_HOLDABLE)} (ready is the absence of a hold)"
        )
    return verdicts


def _env_int_at_least(
    environ: Mapping[str, str], key: str, default: int, minimum: int
) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}: {raw!r} is not an integer") from exc
    if value < minimum:
        raise ConfigError(f"{key}: {value} is below the minimum of {minimum}")
    return value


def _env_dispatch_disk_paths(environ: Mapping[str, str]) -> tuple[str, ...]:
    """`SCHEDULER_DISPATCH_DISK_PATHS`: comma-separated paths the gate samples.

    An empty value is the default pair, not "sample nothing": a gate with no path
    cannot see the disk it exists to protect. A present-but-empty list (` , `) is a
    `ConfigError`, naming the key.
    """
    key = "SCHEDULER_DISPATCH_DISK_PATHS"
    raw = environ.get(key, "").strip()
    if not raw:
        return DEFAULT_DISPATCH_DISK_PATHS
    paths = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not paths:
        raise ConfigError(f"{key}: no paths in {raw!r}")
    return paths


def _env_peak_hours(environ: Mapping[str, str]) -> PeakHours:
    """`SCHEDULER_PEAK_HOURS` and `SCHEDULER_PEAK_LEAD_MINUTES`; see `peak.py`.

    A value that does not parse is a `ConfigError`, not "off": a typo would otherwise
    quietly put dispatch back into the window it was set to avoid.
    """
    key = "SCHEDULER_PEAK_LEAD_MINUTES"
    raw = environ.get(key, "").strip()
    lead = DEFAULT_PEAK_LEAD_MINUTES
    if raw:
        try:
            lead = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{key}: {raw!r} is not an integer") from exc
        if lead < 0:
            raise ConfigError(f"{key}: {lead} is negative")
    try:
        return parse_peak_hours(environ.get("SCHEDULER_PEAK_HOURS", ""), lead)
    except PeakConfigError as exc:
        raise ConfigError(f"SCHEDULER_PEAK_HOURS: {exc}") from exc


def _env_positive_int(environ: Mapping[str, str], key: str, default: int) -> int:
    """A strictly positive integer from the environment.

    Zero is rejected rather than clamped: `SCHEDULER_GITHUB_POLL_SECONDS=0` means
    "poll as fast as possible", which would spend the rate limit in minutes, and
    a zero-second starvation ceiling would put every item in the starved tier and
    make repo priority dead configuration. A typo'd `false` is the same class of
    mistake, and `_env_port` already refuses those for the same reason.
    """
    raw = environ.get(key, "").strip()
    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}: {raw!r} is not an integer") from exc

    if value <= 0:
        raise ConfigError(f"{key}: {value} is not a positive number of seconds")

    return value


def _env_port(environ: Mapping[str, str], key: str, default: int) -> int:
    raw = environ.get(key, "").strip()
    if not raw:
        return default

    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key}: {raw!r} is not an integer") from exc

    if not 1 <= port <= 65535:
        raise ConfigError(f"{key}: {port} is not a TCP port")

    return port
