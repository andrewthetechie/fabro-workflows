"""The factory checkout: what it knows about repositories and images, and the edits registering one takes.

Everything is read from the checkout's own files, so a change there changes this tool's answers.
A file whose shape has moved fails loudly rather than getting a wrong edit (`EditError`).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

WORKFLOWS = ("pr-review", "backlog", "issue-triage", "arch-review", "dependabot", "merge-queue", "scout")

ROW = re.compile(
    r'^provision_automation (?P<id>\S+) (?P<env>\S+) (?P<repo>\S+) (?P<workflow>\S+) '
    r'(?P<schedule_id>"[^"]*"|\S+) "(?P<expr>[^"]*)"(?P<rest>[^|]*)\|\| FAILED=\$\(\(FAILED\+1\)\)$',
    re.M,
)
DOW_PAIRS = ((0, 3), (0, 4), (1, 4), (1, 5), (2, 5), (2, 6), (3, 6))
NUMBERS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen"
).split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


class EditError(RuntimeError):
    """A factory file no longer has the shape this tool edits."""


class ScheduleError(RuntimeError):
    """No slot fits the deployment's staggering rule; a human picks one."""


@dataclass(frozen=True)
class Stack:
    id: str
    image: str
    warm_repo: str
    tools: dict[str, str]


def number_word(n: int) -> str:
    if n < 20:
        return NUMBERS[n]
    tens, ones = divmod(n, 10)
    return TENS[tens - 2] + (f"-{NUMBERS[ones]}" if ones else "")


def word_number(word: str) -> int:
    for n in range(100):
        if number_word(n) == word:
            return n
    raise EditError(f"{word!r} is not a number word")


def find_root(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        if (directory / "ops" / "scheduler" / "repos.toml").is_file():
            return directory
    return None


class Factory:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _read(self, relative: str) -> str:
        return (self.root / relative).read_text()

    def repos(self) -> list[dict]:
        return tomllib.loads(self._read("ops/scheduler/repos.toml")).get("repo", [])

    def rows(self) -> list[dict[str, str]]:
        return [m.groupdict() for m in ROW.finditer(self._read("ops/provision-server-state.sh"))]

    def server_defaults(self) -> dict[str, str]:
        text = self._read("ops/deploy-host.sh")
        match = re.search(r"^HOST=\$\{HOST:-([^}]+)\}", text, re.M)
        if not match:
            raise EditError("ops/deploy-host.sh: no HOST default")
        host = match.group(1)
        ip = host.split("@")[-1]
        scheduler = re.search(r"^SCHEDULER_URL=\$\{SCHEDULER_URL:-([^}]+)\}", text, re.M)
        api = re.search(r"^FABRO_API_URL=\$\{FABRO_API_URL:-([^}]+)\}", text, re.M)
        if not scheduler or not api:
            raise EditError("ops/deploy-host.sh: no SCHEDULER_URL or FABRO_API_URL default")
        return {
            "host": host,
            "scheduler_url": scheduler.group(1).replace("$HOST_IP", ip),
            "api_url": api.group(1).replace("$HOST_IP", ip),
        }

    def stacks(self) -> list[Stack]:
        """Each image's toolchain, from its Dockerfile, with the repository `build-images.sh` warms it from."""
        profiles = dict(
            (m.group(1), (m.group(2), m.group(3)))
            for m in re.finditer(r"^([\w-]+):([\w.-]+):(fabro-[\w-]+:\w+)$",
                                 self._read("ops/profile-images/build-images.sh"), re.M)
        )
        stacks = []
        for path in sorted((self.root / "ops" / "profile-images").glob("Dockerfile.*")):
            env_id = path.name.split(".", 1)[1]
            warm_repo, image = profiles.get(env_id, ("", f"fabro-{env_id}:local"))
            stacks.append(Stack(env_id, image, warm_repo, _image_tools(path.read_text())))
        return stacks

    def status(self) -> dict[str, bool]:
        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True).stdout.strip()

        dirty = git("status", "--porcelain", "--", "ops")
        return {"clean": not dirty, "at_origin_main": git("rev-parse", "HEAD") == git("rev-parse", "origin/main")}

    def registered(self, repo: str) -> dict[str, bool]:
        rows = {r["id"] for r in self.rows() if r["repo"] == repo}
        short = repo.split("/")[1]
        return {
            "repos_toml": any(r["name"] == repo for r in self.repos()),
            "rows": rows >= {f"{w}-{short}" for w in WORKFLOWS},
        }

    # --- schedules -------------------------------------------------------------------------

    def schedule_for(self, workflow: str) -> str:
        """The row's arguments after the workflow name, following that workflow's staggering rule."""
        exprs = [r["expr"] for r in self.rows() if r["workflow"] == workflow]
        if workflow == "pr-review":
            return '"" "" true'
        if workflow == "backlog":
            return f'every-15m "{_next_cycle(_starts(exprs), step=3, period=15)}-59/15 * * * *"'
        if workflow == "issue-triage":
            minutes = [int(e.split()[0]) for e in exprs]
            return f'hourly "{_next_cycle(minutes, step=5, period=60)} * * * *"'
        if workflow == "arch-review":
            hour, days = _next_arch_slot(exprs)
            return f'twice-weekly "0 {hour} * * {days[0]},{days[1]}" true true'
        if workflow == "dependabot":
            return f'daily "{_next_daily(exprs, step=20, limit=24 * 60)}" true true'
        if workflow == "merge-queue":
            return f'sweep "{_next_cycle(_starts(exprs), step=2, period=20)}-59/20 * * * *" true'
        if workflow == "scout":
            # Scout runs before arch-review's 04:00 (ADR 0027 D16).
            return f'daily "{_next_daily(exprs, step=15, limit=4 * 60)}"'
        raise EditError(f"no staggering rule for workflow {workflow!r}")

    def new_rows(self, repo: str, env_id: str) -> dict[str, str]:
        short = repo.split("/")[1]
        return {
            w: f"provision_automation {w}-{short} {env_id} {repo} {w} {self.schedule_for(w)} || FAILED=$((FAILED+1))"
            for w in WORKFLOWS
        }

    # --- edits -----------------------------------------------------------------------------

    def registration_edits(self, repo: str, env_id: str, priority: int, aliases: list[str]) -> dict[str, str]:
        """Path to complete new content, for every file registering `repo` touches.

        Computed whole and written by the caller, so one file that cannot be edited leaves all
        of them untouched.
        """
        if any(r["name"] == repo for r in self.repos()):
            raise EditError(f"{repo} is already in ops/scheduler/repos.toml")
        repos = [*self.repos(), {"name": repo, "priority": priority, "environment_id": env_id}]
        ordered = sorted(repos, key=lambda r: (r["priority"], r["name"]))
        edits = {
            "ops/scheduler/repos.toml": self._repos_toml(repo, env_id, priority, aliases),
            "ops/provision-server-state.sh": self._provision(repo, env_id),
            "ops/fabro-branch-sweep.sh": self._repo_list("ops/fabro-branch-sweep.sh", "FABRO_SWEEP_REPOS", repo),
            "ops/fabro-monitor.sh": self._repo_list("ops/fabro-monitor.sh", "FABRO_MONITOR_REPOS", repo),
            "ops/README.md": self._readme(repo, env_id),
            "ops/scheduler/tests/test_config.py": self._test_config(repo, env_id, ordered),
            "ops/scheduler/tests/test_app.py": self._test_app(ordered),
            "ops/scheduler/tests/test_reconcile.py": self._test_reconcile(repo),
        }
        tomllib.loads(edits["ops/scheduler/repos.toml"])
        return edits

    def _repos_toml(self, repo: str, env_id: str, priority: int, aliases: list[str]) -> str:
        lines = [
            "", "[[repo]]", f'name           = "{repo}"', f"priority       = {priority}",
            f'environment_id = "{env_id}"', "enabled        = true",
        ]
        if aliases:
            quoted = ", ".join(f'"{a}"' for a in aliases)
            lines.append(f"aliases        = [{quoted}]")
        return self._read("ops/scheduler/repos.toml").rstrip("\n") + "\n" + "\n".join(lines) + "\n"

    def _provision(self, repo: str, env_id: str) -> str:
        text = self._read("ops/provision-server-state.sh")
        rows = self.new_rows(repo, env_id)
        for workflow, line in rows.items():
            matches = [m for m in ROW.finditer(text) if m.group("workflow") == workflow]
            if not matches:
                raise EditError(f"ops/provision-server-state.sh: no {workflow} rows to place a new one after")
            end = matches[-1].end()
            text = f"{text[:end]}\n{line}{text[end:]}"
        if len(list(ROW.finditer(text))) != len(self.rows()) + len(rows):
            raise EditError("ops/provision-server-state.sh: a new row does not parse as a row")
        return text

    def _repo_list(self, relative: str, variable: str, repo: str) -> str:
        text = self._read(relative)
        new, count = re.subn(rf'^(REPOS="\$\{{{variable}:-[^}}]*)(\}}")', rf"\1 {repo}\2", text, flags=re.M)
        if count != 1:
            raise EditError(f"{relative}: expected one REPOS default using {variable}")
        return new

    def _readme(self, repo: str, env_id: str) -> str:
        text = self._read("ops/README.md")
        added = len(WORKFLOWS)

        def bump(match: re.Match[str]) -> str:
            return f"{match.group(1)}{number_word(word_number(match.group(2)) + added)}{match.group(3)}"

        text, recreates = re.subn(r"(Recreates the )([a-z-]+)( \*\*automations\*\*)", bump, text)
        text, creates = re.subn(r"(This creates the )([a-z-]+)(\s+automations, seven per repository)", bump, text)
        if recreates != 1 or creates != 1:
            raise EditError("ops/README.md: the two automation counts have moved")
        short = repo.split("/")[1]
        text, rows = re.subn(rf"^(\| `{re.escape(env_id)}` \|.*\| )([^|\n]+?)( \|)$", rf"\1\2, {short}\3", text, flags=re.M)
        if rows != 1:
            raise EditError(f"ops/README.md: no environments table row for {env_id}")
        return text

    def _test_config(self, repo: str, env_id: str, ordered: list[dict]) -> str:
        text = self._read("ops/scheduler/tests/test_config.py")
        lines = "\n".join(f'        "{r["name"]}",  # priority {r["priority"]}' for r in ordered)
        text, first = re.subn(
            r"(cfg = load_config\(TRACKED_REPOS, env=\{\}\)\n    assert \[r\.name for r in cfg\.ordered_repos\(\)\] == \[\n).*?(\n    \])",
            lambda m: f"{m.group(1)}{lines}{m.group(2)}", text, count=1, flags=re.S,
        )
        text, second = re.subn(
            r"(assert \{\(r\.name, r\.environment_id\) for r in cfg\.repos\} == \{\n.*?)(\n    \})",
            lambda m: f'{m.group(1)}\n        ("{repo}", "{env_id}"),{m.group(2)}', text, count=1, flags=re.S,
        )
        if first != 1 or second != 1:
            raise EditError("ops/scheduler/tests/test_config.py: the tracked-file assertions have moved")
        return text

    def _test_app(self, ordered: list[dict]) -> str:
        text = self._read("ops/scheduler/tests/test_app.py")
        lines = "\n".join(f'        "{r["name"]}",' for r in ordered)
        text, count = re.subn(
            r'(assert \[r\["name"\] for r in body\["repos"\]\] == \[\n).*?(\n    \])',
            lambda m: f"{m.group(1)}{lines}{m.group(2)}", text, count=1, flags=re.S,
        )
        if count != 1:
            raise EditError("ops/scheduler/tests/test_app.py: the /health order assertion has moved")
        return text

    def _test_reconcile(self, repo: str) -> str:
        text = self._read("ops/scheduler/tests/test_reconcile.py")
        short = repo.split("/")[1]
        text, count = re.subn(r'^(IDLE_SLUGS = \(.*?)(\)\s*)$', rf'\1, "{short}"\2', text, count=1, flags=re.M)
        if count != 1:
            raise EditError("ops/scheduler/tests/test_reconcile.py: IDLE_SLUGS has moved")
        return text


def _image_tools(dockerfile: str) -> dict[str, str]:
    """The last stage's base image, then its pinned ARGs, which override it (rust-node's rustup does)."""
    tools: dict[str, str] = {}
    bases = re.findall(r"^FROM\s+(\S+)", dockerfile, re.M)
    base = bases[-1] if bases else ""
    for prefix, tool in (("python:", "python"), ("node:", "node"), ("oven/bun:", "bun"), ("rust:", "rust")):
        if base.startswith(prefix):
            tools[tool] = base.removeprefix(prefix).split("-")[0]
    for arg, tool in (("NODE_VERSION", "node"), ("PYTHON_VERSION", "python"), ("RUST_VERSION", "rust"), ("PG_MAJOR", "postgres")):
        match = re.search(rf"^ARG {arg}=(\S+)", dockerfile, re.M)
        if match:
            tools[tool] = match.group(1)
    return tools


def _starts(exprs: list[str]) -> list[int]:
    return [int(e.split("-")[0]) for e in exprs]


def _next_cycle(used: list[int], *, step: int, period: int) -> int:
    """The last offset plus `step`, moved forward past any offset the same period already fires on."""
    taken = {u % period for u in used}
    if len(taken) >= period:
        raise ScheduleError(f"every offset in a {period}-minute period is taken")
    candidate = (used[-1] + step) % period
    while candidate in taken:
        candidate = (candidate + 1) % period
    return candidate


def _next_daily(exprs: list[str], *, step: int, limit: int) -> str:
    minutes = [int(e.split()[1]) * 60 + int(e.split()[0]) for e in exprs]
    candidate = minutes[-1] + step
    while candidate in minutes:
        candidate += step
    if candidate >= limit:
        raise ScheduleError(f"no daily slot before {limit // 60:02d}:00 follows {minutes[-1]} minutes; pick one by hand")
    return f"{candidate % 60} {candidate // 60} * * *"


def _next_arch_slot(exprs: list[str]) -> tuple[int, tuple[int, int]]:
    """Two weekdays 3 or 4 days apart, never two repositories in one hour of one day (ADR 0012 D7)."""
    taken: set[tuple[int, int]] = set()
    for expr in exprs:
        fields = expr.split()
        taken |= {(int(fields[1]), int(day)) for day in fields[4].split(",")}
    for hour in range(4, 24):
        for pair in DOW_PAIRS:
            if not any((hour, day) in taken for day in pair):
                return hour, pair
    raise ScheduleError("no twice-weekly arch-review slot is free")
