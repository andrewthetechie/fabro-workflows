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

# The `ralph` trial's share of the queue (ADR 0029 D2, O2, O21). Only 0 and 50 are
# allowed: 0 is the kill switch, 50 is the trial's issue parity. A percentage is not
# a knob to tune mid-trial, because every value in between changes what the two arms
# are compared on. When `ralph` wins the trial the share becomes a lane decision of
# its own series, not a third value here.
DEFAULT_RALPH_SHARE = 0
RALPH_SHARES = (0, 50)

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
# Task 05 added only `box-noop`, which exists to measure cancels; task 06 adds `pr-doctor`
# at 1, because turning hosted spend already made into a merge is the lane's highest-value
# work. Tasks 07, 08 and 10 add their own kinds here as they build their producers.
BACKGROUND_JOB_PRIORITY = {
    "pr-doctor": 1,
    "box-noop": 5,
}

# Kinds the lane still knows how to finish, but that no producer or endpoint may queue any
# more. `pr-doctor` was retired on 2026-10-30 (operator decision): its run holds a box for
# ~40 minutes, and a coder run's gaps reach that length unpredictably (no stage outside a
# rescue gives even odds of 41 free minutes; `.scratch/more-local-inference/gap-analysis.md`),
# so a doctor job waited 23 hours for a box and never started. Naming it in
# `SCHEDULER_BG_KINDS` is a `ConfigError`, and it is not in the default kinds.
RETIRED_JOB_KINDS = frozenset({"pr-doctor"})

# How long a `pr-doctor` job is expected to hold its box, whole run, dispatch to finish
# (C7's `typical_box_minutes`): the checkout, a rebase attempt, one 45-minute fix stage, the
# repository's CI, and the push. The gap table's median is ~11 minutes, so a 40-minute job
# mostly starts on a box with no coder loop — overnight, or during the z.ai peak-hours pause
# (D7). Task 12 measures the real number; do not lower it to make jobs fit.
PR_DOCTOR_TYPICAL_MINUTES = 40

# The most follow-up `pr-review` runs the doctor may dispatch in one UTC day, across every
# repository (operator decision 3). A review spends hosted credits, and the point of the
# cap is that a bad day cannot spend a week's budget in an hour. A pushed PR over the cap
# waits in the pending-review state and goes first the next day, still ahead of the issue
# queue. A review that failed to dispatch does not count.
DEFAULT_PR_DOCTOR_REVIEWS_PER_DAY = 6

# Gap prediction (C7 step 9): the coder loop's median remaining gap after a node, in
# minutes, keyed by `(workflow, node id)`. **Measured** (task 12 Phase 0 step 2,
# 2026-10-28): every visit of a non-box node in the 170 released `backlog` and `ralph`
# runs since 2026-10-21, gap = the next box-stage visit's start minus this visit's end (or
# the run's end), median per node, floored to whole minutes. Nodes with fewer than 5 visits
# or a median under a minute are left out and predict 0. Measured from a visit's *end*, a
# long node such as `watch_checks` reads short (the run usually ends after it): the safe
# direction, since the lane cannot see how much of the node is left. Re-measure when the
# graphs change.
BACKGROUND_GAP_MINUTES: dict[tuple[str, str], int] = {
    ("backlog", "autofix"): 13,  # n=305
    ("backlog", "claim"): 15,  # n=101
    ("backlog", "decompose"): 5,  # n=98
    ("backlog", "decompose_gate"): 5,  # n=98
    ("backlog", "extra_gate"): 20,  # n=106
    ("backlog", "extra_prep"): 16,  # n=132
    ("backlog", "file_remainder"): 26,  # n=83
    ("backlog", "final_merge"): 32,  # n=83
    ("backlog", "integrate"): 9,  # n=281
    ("backlog", "next_task"): 7,  # n=424
    ("backlog", "open_pr"): 27,  # n=83
    ("backlog", "open_pr_prep"): 27,  # n=83
    ("backlog", "partial_remainder"): 38,  # n=10
    ("backlog", "pr_handoff"): 26,  # n=83
    ("backlog", "prep"): 14,  # n=101
    ("backlog", "prep_review"): 13,  # n=305
    ("backlog", "rescue_brief"): 240,  # n=21
    ("backlog", "review"): 9,  # n=303
    ("backlog", "review_gate"): 9,  # n=303
    ("backlog", "review_merge.ci_fix_gate"): 2,  # n=6
    ("backlog", "review_merge.ci_fix_t1"): 2,  # n=5
    ("backlog", "review_merge.fix_gate"): 5,  # n=82
    ("backlog", "review_merge.hygiene"): 1,  # n=117
    ("backlog", "review_merge.prep_review"): 26,  # n=83
    ("backlog", "review_merge.refute_fix_gate"): 7,  # n=40
    ("backlog", "review_merge.refute_prep"): 1,  # n=117
    ("backlog", "review_merge.review_fix"): 4,  # n=83
    ("backlog", "review_merge.rm_setup"): 26,  # n=83
    ("backlog", "review_merge.spec"): 14,  # n=80
    ("backlog", "review_merge.spec_gate"): 14,  # n=80
    ("backlog", "review_merge.standards"): 19,  # n=83
    ("backlog", "review_merge.standards_gate"): 20,  # n=80
    ("backlog", "review_merge.validate"): 1,  # n=119
    ("backlog", "rework_t2"): 28,  # n=7
    ("backlog", "route_lane"): 14,  # n=101
    ("backlog", "spec"): 6,  # n=113
    ("backlog", "spec_gate"): 6,  # n=112
    ("backlog", "standards"): 9,  # n=119
    ("backlog", "standards_gate"): 9,  # n=113
    ("backlog", "task_block_gate"): 14,  # n=307
    ("backlog", "validate"): 12,  # n=305
    ("ralph", "autofix"): 1,  # n=268
    ("ralph", "claim"): 11,  # n=69
    ("ralph", "final_check"): 11,  # n=96
    ("ralph", "final_merge"): 16,  # n=96
    ("ralph", "hygiene"): 11,  # n=93
    ("ralph", "iterate_t2"): 11,  # n=8
    ("ralph", "prep"): 9,  # n=69
    ("ralph", "refute"): 10,  # n=92
    ("ralph", "refute_gate"): 10,  # n=92
    ("ralph", "refute_prep"): 11,  # n=92
    ("ralph", "review_prep"): 11,  # n=66
    ("ralph", "standards"): 4,  # n=66
    ("ralph", "task_block_gate"): 1,  # n=274
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
    # 0 or 50; see `DEFAULT_RALPH_SHARE`. Read by `choose_workflow`.
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
    # The doctor's daily follow-up-review cap (operator decision 3), per UTC day.
    pr_doctor_reviews_per_day: int = DEFAULT_PR_DOCTOR_REVIEWS_PER_DAY

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
        pr_doctor_reviews_per_day=_env_int_at_least(
            environ,
            "SCHEDULER_PR_DOCTOR_REVIEWS_PER_DAY",
            DEFAULT_PR_DOCTOR_REVIEWS_PER_DAY,
            0,
        ),
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
    """`SCHEDULER_RALPH_SHARE`: 0 (the default, and the kill switch) or 50.

    Anything else — `25`, `yes`, `-1`, an empty-but-present value — is a
    `ConfigError` naming the key. A share the trial never validated is worse than no
    trial at all: the two arms would stop being comparable, and nothing in a run's
    history says which share produced it.
    """
    key = "SCHEDULER_RALPH_SHARE"
    raw = environ.get(key, "").strip()
    if not raw:
        return DEFAULT_RALPH_SHARE
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"{key}: {raw!r} is not an integer; use 0 (off) or 50 (the trial)"
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
    """Every kind the lane knows that is not retired, in D10's value order."""
    return tuple(kind for kind in BACKGROUND_JOB_PRIORITY if kind not in RETIRED_JOB_KINDS)


def _env_bg_kinds(environ: Mapping[str, str]) -> tuple[str, ...]:
    """`SCHEDULER_BG_KINDS`: the job kinds whose producers run (C7).

    Default is every live kind. An unknown kind is a `ConfigError`: a name that matches
    no producer would silently turn nothing on. So is a retired one (`RETIRED_JOB_KINDS`).
    """
    key = "SCHEDULER_BG_KINDS"
    raw = environ.get(key, "").strip()
    if not raw:
        return _live_kinds()
    kinds = tuple(part.strip() for part in raw.split(",") if part.strip())
    retired = [kind for kind in kinds if kind in RETIRED_JOB_KINDS]
    if retired:
        raise ConfigError(
            f"{key}: {retired[0]!r} was retired on 2026-10-30 and can no longer be queued"
        )
    unknown = [kind for kind in kinds if kind not in BACKGROUND_JOB_PRIORITY]
    if unknown:
        raise ConfigError(
            f"{key}: {unknown[0]!r} is not a job kind; expected one of "
            f"{', '.join(BACKGROUND_JOB_PRIORITY)}"
        )
    return kinds


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
