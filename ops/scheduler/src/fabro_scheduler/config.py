"""Load and validate the scheduler's configuration.

`repos.toml` is the operator-editable half: one `[[repo]]` table per target
repository. Everything else is deployment-shaped — the fabro API base URL, the
port it listens on — and comes from the environment instead, for the same reason
the compose project already passes `${VAR}` rather than literals: this repository
is public, and a host-specific value belongs in `.env`.

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
from dataclasses import dataclass
from pathlib import Path

# "owner/repo". Permissive about the characters GitHub allows, strict about the
# shape: exactly one slash, neither side empty. A name that fails this cannot be
# passed to the GitHub API as an owner/repo pair.
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# The LiteLLM model groups that address one coder instance each — decision 12.
# `coders`, the load-balanced group over both boxes, is deliberately absent: the
# scheduler exists precisely to stop relying on the load balancer's choice. It
# stays defined in LiteLLM as the fallback for unpinned and hand-fired runs.
DEFAULT_CODER_POOLS = ("coders-a", "coders-b")

DEFAULT_FABRO_API_URL = "http://10.10.0.32:32276/api/v1"
DEFAULT_PORT = 32280

# Every key a `[[repo]]` table may carry. Unknown keys are errors rather than
# warnings: `priorty = 0` would otherwise be silently ignored and the row would
# sort by whatever the default turned out to be.
_REPO_KEYS = frozenset({"name", "priority", "environment_id", "enabled"})


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


@dataclass(frozen=True)
class SchedulerConfig:
    """Everything the service needs to start."""

    repos: tuple[RepoConfig, ...]
    coder_pools: tuple[str, ...]
    fabro_api_url: str
    port: int = DEFAULT_PORT

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

    return RepoConfig(
        name=name,
        priority=priority,
        environment_id=environment_id,
        enabled=enabled,
    )


def _env_str(environ: Mapping[str, str], key: str, default: str) -> str:
    value = environ.get(key, "").strip()
    return value or default


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
