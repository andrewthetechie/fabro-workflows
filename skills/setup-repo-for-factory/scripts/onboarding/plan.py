"""The onboarding plan: one TOML file holding every decision, read by every later command.

It lives in the factory checkout's untracked `.scratch/onboard/`, so it survives across sessions
and never lands in the target repository.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .ci import CONTEXT, DOCKER, CiFacts, Step, draft_env

ROLES = ("gate", "setup", "skip")
REPO_ALIAS = re.compile(r"^[A-Za-z0-9._-]+$")


def plan_path(factory_root: Path, repo: str) -> Path:
    return factory_root / ".scratch" / "onboard" / f"{repo.replace('/', '--')}.toml"


def load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {toml_value(v)}" for k, v in value.items()) + " }" if value else "{}"
    text = str(value)
    if "\n" in text and "'''" not in text:
        return "'''\n" + text + "'''"
    return json.dumps(text, ensure_ascii=False)


def _key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _table(values: dict[str, Any]) -> list[str]:
    return [f"{_key(k)} = {toml_value(v)}" for k, v in values.items()]


def write(path: Path, plan: dict[str, Any], *, header: str = "") -> None:
    """Write `plan` as TOML, then read it back so a bad write fails here rather than later."""
    scalars = {k: v for k, v in plan.items() if not isinstance(v, dict) and not _is_table_array(v)}
    lines = [header.rstrip()] if header else []
    lines += [*_table(scalars), ""]
    for name, value in plan.items():
        if isinstance(value, dict):
            lines += [f"[{name}]", *_table(value), ""]
    for name, value in plan.items():
        if _is_table_array(value):
            for row in value:
                lines += [f"[[{name}]]", *_table(row), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    tomllib.loads(text)
    path.write_text(text)


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


HEADER = """\
# Onboarding plan, written by `onboard.py init`. Every later command reads this file.
#
# Resolve every step whose role is still "review", then every env_review row plan-check adds:
#   role = "gate"   the check goes in .fabro/ci.sh
#   role = "setup"  the install goes in .fabro/setup.sh
#   role = "skip"   neither; `reason` is required when the suggestion was not already skip
#   action = "set"  with a literal `value`, or "drop" with a `reason`
# Changing `run` requires `edit_reason`. A step you add needs id = "added.<n>" and `edit_reason`.
# Whole workflows: `onboard.py resolve "docker-main.yml > *" --role skip --reason "..."`.
# Run `onboard.py plan-check` until it reports no problems.
"""


def draft(*, repo: str, stack: str, priority: int, aliases: list[str], deploy: str, server: dict[str, str],
          ci: CiFacts, needs_postgres: bool) -> dict[str, Any]:
    return {
        "repo": repo,
        "stack": stack,
        "priority": priority,
        "aliases": aliases,
        "deploy": deploy,
        "host": server["host"],
        "api_url": server["api_url"],
        "scheduler_url": server["scheduler_url"],
        "workflows": ci.workflows,
        "excluded_workflows": ci.excluded,
        "prepare_inputs": [],
        "prepare_timeout": 300,
        "prepare_run": ["fabro-pg-ensure"] if needs_postgres else [],
        "env": {},
        "env_review": [],
        "step": [_step_row(s) for s in ci.steps],
        "target": [],
        "fix": [],
    }


def settle_env(plan: dict[str, Any]) -> None:
    """Recompute `[env]` from the roles as they now stand, keeping every decision already made."""
    plan["env"], plan["env_review"] = draft_env(plan.get("step", []), plan.get("env_review", []))


def _step_row(step: Step) -> dict[str, Any]:
    row = asdict(step)
    row["ci_env"] = row.pop("env")
    row["env"] = {}
    row["suggested"] = step.role
    row["original_run"] = step.run
    row["edit_reason"] = ""
    return row


def problems(plan: dict[str, Any], ci: CiFacts | None = None, *, include_env: bool = True) -> list[str]:
    """Every reason this plan cannot be rendered. Empty means it is ready."""
    found: list[str] = []
    steps = plan.get("step", [])
    for step in steps:
        found += _step_problems(step)
    if include_env:
        for row in plan.get("env_review", []):
            found += _env_problems(row)
    if not any(s.get("role") == "gate" for s in steps):
        found.append('no step has role = "gate": .fabro/ci.sh would check nothing')
    if ci is not None:
        known = {s["id"] for s in steps}
        for missing in (s.id for s in ci.steps if s.id not in known):
            found.append(f"{missing}: new in the repository's CI since init; add it or re-run init --force")
    return found


def _step_problems(step: dict[str, Any]) -> list[str]:
    sid = step.get("id", "?")
    role = step.get("role")
    out: list[str] = []
    if role == "review":
        return [f"{sid}: still role = \"review\" ({step.get('source', '')}). {step.get('note', '')}".rstrip()]
    if role not in ROLES:
        return [f"{sid}: role {role!r} is not one of {', '.join(ROLES)}"]
    added = str(sid).startswith("added.")
    if role == "skip":
        if step.get("suggested") != "skip" and not step.get("reason"):
            out.append(f"{sid}: skipping a suggested {step.get('suggested')} step needs `reason`")
        return out
    run = str(step.get("run") or "")
    if not run.strip():
        out.append(f"{sid}: role = \"{role}\" needs a `run` command")
    if CONTEXT.search(run):
        out.append(f"{sid}: `run` still uses a GitHub context the sandbox lacks")
    if DOCKER.search(run) and not step.get("edit_reason"):
        out.append(f"{sid}: `run` calls docker and the sandbox has none; rewrite it or skip it")
    if (added or run != step.get("original_run", "")) and not step.get("edit_reason"):
        out.append(f"{sid}: `run` is not what CI runs, so it needs `edit_reason`")
    return out


def _env_problems(row: dict[str, Any]) -> list[str]:
    key = row.get("key", "?")
    action = row.get("action")
    if action == "set":
        value = str(row.get("value") or "")
        if not value or "${{" in value:
            return [f"env {key}: action = \"set\" needs a literal `value`"]
        return []
    if action == "drop":
        return [] if row.get("reason") else [f"env {key}: action = \"drop\" needs `reason`"]
    return [f"env {key}: still action = \"review\" (CI has {row.get('seen', '')!r}). {row.get('note', '')}".rstrip()]


def effective_env(plan: dict[str, Any]) -> dict[str, str]:
    env = dict(plan.get("env", {}))
    for row in plan.get("env_review", []):
        if row.get("action") == "set":
            env[row["key"]] = row["value"]
    return dict(sorted(env.items()))


def validate_choices(*, stack: str, known_stacks: list[str], aliases: list[str], used_aliases: set[str]) -> list[str]:
    errors = []
    if stack not in known_stacks:
        errors.append(f"stack {stack!r} has no profile image; known: {', '.join(known_stacks)}")
    for alias in aliases:
        if not REPO_ALIAS.match(alias):
            errors.append(f"alias {alias!r} must match [A-Za-z0-9._-]+")
        if alias in used_aliases:
            errors.append(f"alias {alias!r} is already another repository's")
        if len(alias) < 3:
            errors.append(f"alias {alias!r} is too short: the weekly retro withholds any text that names it (ADR 0026 D9)")
    return errors
