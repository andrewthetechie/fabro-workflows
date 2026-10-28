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
