"""Read a repository's GitHub Actions into closed-form steps, one role suggested per step.

The repository's own CI is the source of truth for `.fabro/setup.sh` and `.fabro/ci.sh`. Only
workflows a pull request triggers count, because those are the checks a PR has to pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Provided by the profile image or meaningful only on a GitHub runner.
INFRA_ACTIONS = (
    "actions/checkout", "actions/setup-", "actions/cache", "actions/upload-artifact",
    "actions/download-artifact", "actions/create-github-app-token", "actions/dependency-review",
    "astral-sh/setup-uv", "oven-sh/setup-bun", "dtolnay/rust-toolchain",
    "actions-rust-lang/setup-rust-toolchain", "swatinem/rust-cache", "taiki-e/install-action",
    "pnpm/action-setup", "docker/", "codecov/", "dorny/paths-filter", "tj-actions/changed-files",
    "ruby/setup-ruby", "browser-actions/", "amannn/action-semantic-pull-request",
)

CONTEXT = re.compile(r"\$\{\{|\$GITHUB_|\$\{GITHUB_|\bsecrets\.")
SYSTEM = re.compile(r"\b(apt-get|apt|apk|brew|sudo)\b|\b(npm|pnpm|yarn)\s+(i|install|add)\s+(-g|--global)\b")
DOCKER = re.compile(r"\bdocker(-compose)?\b")
BROWSER = re.compile(r"\b(e2e|playwright|cypress)\b", re.I)
INSTALL = re.compile(
    r"(^|&&\s*|;\s*)\s*("
    r"npm\s+(ci|install)|pnpm\s+install|yarn\s+install|bun\s+install"
    r"|uv\s+(sync|venv|pip\s+install|python\s+install|tool\s+install)"
    r"|pip3?\s+install|python3?\s+-m\s+pip\s+install|poetry\s+install"
    r"|cargo\s+(fetch|install)"
    r")",
    re.M,
)
GATE = re.compile(
    r"\b(test|tests|lint|check|typecheck|type-check|tsc|eslint|prettier|ruff|mypy|pyright|pytest"
    r"|vitest|jest|clippy|fmt|nextest|audit|deny|build|coverage|pre-commit|black|isort"
    r"|shellcheck|actionlint)\b"
    r"|\b(check|test|lint|verify|validate|guard)[_-]\w+"
)
TOOLCHAIN = re.compile(r"\brustup\s+(toolchain|default|component|target|update)\b")
BROWSER_INSTALL = re.compile(r"\bplaywright\s+install\b")
GITHUB_FILES = re.compile(r"\$\{?GITHUB_(OUTPUT|ENV|PATH|STEP_SUMMARY)\b")
REPORT_UPLOAD = re.compile(r"flakeradar|\bFLKR_", re.I)
PRINT_ONLY = re.compile(r"^\s*(echo|mkdir|ls|pwd|cat|env|printenv|date|sleep|cd)\b")
POSTGRES_URL = re.compile(r"(postgres(?:ql)?(?:\+\w+)?://)[^/\s]+/\w+")
ENV_KEY = re.compile(r"^[A-Z_][A-Z0-9_]*$")
GITHUB_ONLY_ENV = {"GITHUB_TOKEN", "GH_TOKEN"}


@dataclass
class Step:
    id: str
    source: str
    cwd: str = "."
    run: str = ""
    uses: str = ""
    role: str = "review"
    reason: str = ""
    note: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class CiFacts:
    steps: list[Step] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    toolchains: dict[str, set[str]] = field(default_factory=dict)
    services: list[str] = field(default_factory=list)


def read_ci(root: Path) -> CiFacts:
    """Every step of every workflow a pull request triggers, in file order."""
    facts = CiFacts()
    directory = root / ".github" / "workflows"
    paths = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]) if directory.is_dir() else []
    for path in paths:
        doc = _load(path)
        triggers = doc.get("on", doc.get(True))
        if not _runs_on_pull_request(triggers):
            facts.excluded.append(path.name)
            continue
        facts.workflows.append(path.name)
        _read_workflow(root, path.name, doc, facts)
    _mark_duplicates(facts.steps)
    return facts


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text())
    return loaded if isinstance(loaded, dict) else {}


def _runs_on_pull_request(triggers: Any) -> bool:
    names: list[str] = []
    if isinstance(triggers, str):
        names = [triggers]
    elif isinstance(triggers, list):
        names = [str(t) for t in triggers]
    elif isinstance(triggers, dict):
        names = [str(t) for t in triggers]
    return any(n in ("pull_request", "pull_request_target") for n in names)


def _read_workflow(root: Path, file: str, doc: dict[str, Any], facts: CiFacts, prefix: str = "") -> None:
    wf_env = _strings(doc.get("env"))
    wf_cwd = _default_cwd(doc)
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        job_key = f"{prefix}{Path(file).stem}.{job_id}"
        uses = str(job.get("uses") or "")
        if uses.startswith("./"):
            called = root / uses.split("@")[0].removeprefix("./")
            if called.is_file():
                _read_workflow(root, called.name, _load(called), facts, prefix=f"{job_key}.")
                continue
        if uses:
            facts.steps.append(Step(
                id=f"{job_key}.reusable", source=f"{file} > {job_id}", uses=uses,
                note="Reusable workflow from another repository: find what it runs, then give this step a role.",
            ))
            continue
        for image in _services(job):
            if image not in facts.services:
                facts.services.append(image)
        env = {**wf_env, **_strings(job.get("env"))}
        matrix = _matrix(job)
        skip_reason = _job_skip_reason(job)
        for index, raw in enumerate(job.get("steps") or []):
            if isinstance(raw, dict):
                _read_step(root, file, job_id, f"{job_key}.{index}", raw, env, _job_cwd(job, wf_cwd),
                           matrix, skip_reason, facts, depth=0)


def _job_cwd(job: dict[str, Any], fallback: str) -> str:
    return _default_cwd(job) if _default_cwd(job) != "." else fallback


def _default_cwd(node: dict[str, Any]) -> str:
    try:
        return str(node["defaults"]["run"]["working-directory"])
    except (KeyError, TypeError):
        return "."


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in value.items()}


def _services(job: dict[str, Any]) -> list[str]:
    images = []
    for service in (job.get("services") or {}).values():
        image = service.get("image") if isinstance(service, dict) else service
        if image:
            images.append(str(image))
    return images


def _matrix(job: dict[str, Any]) -> dict[str, list[str]]:
    matrix = (job.get("strategy") or {}).get("matrix")
    if not isinstance(matrix, dict):
        return {}
    return {str(k): [str(x) for x in v] for k, v in matrix.items() if isinstance(v, list)}


def _job_skip_reason(job: dict[str, Any]) -> str:
    runs_on = str(job.get("runs-on") or "")
    if re.search(r"macos|windows", runs_on, re.I):
        return f"job runs on {runs_on}; the sandbox is Linux"
    condition = str(job.get("if") or "")
    if re.search(r"event_name\s*==\s*'(push|schedule|release|workflow_dispatch)'|refs/tags|github\.ref\s*==", condition):
        return f"job does not run on pull requests: if: {condition}"
    return ""


def _read_step(root: Path, file: str, job_id: str, step_id: str, raw: dict[str, Any], job_env: dict[str, str],
               cwd: str, matrix: dict[str, list[str]], skip_reason: str, facts: CiFacts, depth: int) -> None:
    uses = str(raw.get("uses") or "")
    name = str(raw.get("name") or uses or str(raw.get("run") or "").split("\n")[0])
    source = f"{file} > {job_id} > {name}"
    env = {**job_env, **_strings(raw.get("env"))}
    if uses:
        _record_toolchain(uses, raw.get("with"), env, matrix, facts)
        composite = _composite(root, uses)
        if composite is not None and depth < 3:
            inputs = {**_input_defaults(composite), **_strings(raw.get("with"))}
            for index, inner in enumerate((composite.get("runs") or {}).get("steps") or []):
                if isinstance(inner, dict):
                    _read_step(root, file, job_id, f"{step_id}.{index}", _bind_inputs(inner, inputs), env, cwd,
                               matrix, skip_reason, facts, depth + 1)
            return
    run = str(raw.get("run") or "").strip()
    step_cwd = str(raw.get("working-directory") or cwd)
    step = Step(id=step_id, source=source, cwd=step_cwd, run=run, uses=uses, env=env)
    if not run and not uses:
        return
    _classify(step, job_id, name, str(raw.get("shell") or ""), skip_reason)
    facts.steps.append(step)


def _composite(root: Path, uses: str) -> dict[str, Any] | None:
    if not uses.startswith("./"):
        return None
    for name in ("action.yml", "action.yaml"):
        path = root / uses.removeprefix("./") / name
        if path.is_file():
            return _load(path)
    return None


def _input_defaults(composite: dict[str, Any]) -> dict[str, str]:
    inputs = composite.get("inputs") or {}
    return {str(k): str(v.get("default", "")) for k, v in inputs.items() if isinstance(v, dict)}


def _bind_inputs(step: dict[str, Any], inputs: dict[str, str]) -> dict[str, Any]:
    bound = dict(step)
    if isinstance(bound.get("run"), str):
        bound["run"] = re.sub(
            r"\$\{\{\s*inputs\.([\w-]+)\s*\}\}",
            lambda m: inputs.get(m.group(1), m.group(0)),
            bound["run"],
        )
    return bound


def _classify(step: Step, job_id: str, name: str, shell: str, skip_reason: str) -> None:
    if skip_reason:
        step.role, step.reason = "skip", skip_reason
        return
    if step.uses and not step.run:
        lowered = step.uses.lower()
        if any(lowered.startswith(a) for a in INFRA_ACTIONS):
            step.role, step.reason = "skip", "runner setup; the profile image provides the toolchain"
        else:
            step.note = "An action may hide a check: name the command it runs (edit `run`) or skip it with a reason."
        return
    if shell and shell not in ("bash", "sh"):
        step.note = f"Runs under `{shell}`: rewrite it for bash or skip it with a reason."
    elif REPORT_UPLOAD.search(f"{name} {step.run}"):
        step.role, step.reason = "skip", "uploads a CI report; it checks nothing"
    elif TOOLCHAIN.search(step.run):
        step.role, step.reason = "skip", "installs a toolchain; the profile image provides it"
    elif GITHUB_FILES.search(step.run) and not GATE.search(GITHUB_FILES.sub("", name + " " + step.run)):
        step.role, step.reason = "skip", "feeds GitHub's step outputs; it checks nothing"
    elif BROWSER_INSTALL.search(step.run):
        step.note = "Installs a browser and its system packages: the image must carry them. Skip it unless a browser suite is gated."
    elif CONTEXT.search(step.run):
        step.note = "Uses a GitHub context the sandbox lacks: rewrite `run` without it or skip it with a reason."
    elif SYSTEM.search(step.run):
        step.note = "Installs system packages: the profile image must carry them. Skip it, or block on a new image."
    elif DOCKER.search(step.run):
        step.note = "The sandbox has no Docker. Skip it with a reason, or rewrite it to run without Docker."
    elif INSTALL.search(step.run) and _has_gate_outside_install(step.run):
        step.note = "Mixes an install and a check: split it into a setup step and a gate step."
    elif INSTALL.search(step.run):
        step.role = "setup"
    elif all(PRINT_ONLY.match(line) for line in step.run.splitlines() if line.strip()):
        step.role, step.reason = "skip", "prints or prepares only; it checks nothing"
    elif GATE.search(step.run):
        step.role = "gate"
        if BROWSER.search(f"{job_id} {name} {step.run}"):
            step.role = "review"
            step.note = "Browser suite: gate it only if it finishes inside the sandbox (2 CPU, 4 GB, no display)."
    else:
        step.note = "No known install or check: decide whether it is a check the gate needs."


def _has_gate_outside_install(run: str) -> bool:
    return any(not INSTALL.search(part) and GATE.search(part) for part in re.split(r"&&|;|\n", run))


def _mark_duplicates(steps: list[Step]) -> None:
    seen: dict[tuple[str, str], str] = {}
    for step in steps:
        if step.role not in ("gate", "setup") or not step.run:
            continue
        key = (step.cwd, step.run)
        if key in seen:
            step.role, step.reason = "skip", f"duplicate of {seen[key]}"
        else:
            seen[key] = step.id


def _record_toolchain(uses: str, with_: Any, env: dict[str, str], matrix: dict[str, list[str]], facts: CiFacts) -> None:
    params = _strings(with_)
    keys = {"node-version": "node", "python-version": "python", "bun-version": "bun", "toolchain": "rust"}
    for key, tool in keys.items():
        if key in params:
            facts.toolchains.setdefault(tool, set()).update(_resolve(params[key], env, matrix))
    if uses.startswith("dtolnay/rust-toolchain@"):
        tag = uses.split("@", 1)[1]
        if re.match(r"^\d", tag):
            facts.toolchains.setdefault("rust", set()).add(tag)


def _resolve(value: str, env: dict[str, str], matrix: dict[str, list[str]]) -> list[str]:
    match = re.fullmatch(r"\$\{\{\s*(matrix|env)\.([\w-]+)\s*\}\}", value.strip())
    if not match:
        return [] if "${{" in value else [value.strip()]
    if match.group(1) == "env":
        return [env[match.group(2)]] if match.group(2) in env else []
    return matrix.get(match.group(2), [])


def draft_env(rows: list[dict[str, Any]], prior_review: list[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Split the gated steps' environment into `[env]`, per-step exports, and what needs a decision.

    Runs once the roles are settled, so a skipped job's variables never reach the sandbox. A key
    with one value everywhere goes to `[env]`. A key whose value differs between steps becomes an
    export on each. A value the sandbox cannot supply needs a decision, and a decision already made
    (`prior_review`) is kept. Sets each row's `env`.
    """
    active = [r for r in rows if r.get("role") in ("gate", "setup")]
    values: dict[str, set[str]] = {}
    for row in active:
        for key, value in (row.get("ci_env") or {}).items():
            values.setdefault(key, set()).add(_sandbox_value(value))
    decided = {r["key"]: r for r in prior_review}
    shared: dict[str, str] = {}
    review: list[dict[str, Any]] = []
    per_step: set[str] = set()
    for key, seen in sorted(values.items()):
        if key in GITHUB_ONLY_ENV:
            continue
        unresolved = next((v for v in sorted(seen) if "${{" in v), None)
        if unresolved is not None or not ENV_KEY.match(key) or key == "PATH":
            review.append(decided.get(key) or {
                "key": key, "seen": unresolved or sorted(seen)[0], "action": "review", "value": "", "reason": "",
                "note": "`set` it with a literal test value, or `drop` it with a reason.",
            })
        elif len(seen) == 1:
            shared[key] = next(iter(seen))
        else:
            per_step.add(key)
    for row in rows:
        row["env"] = {k: _sandbox_value(v) for k, v in (row.get("ci_env") or {}).items() if k in per_step}
    return shared, review


def _sandbox_value(value: str) -> str:
    """A PostgreSQL URL in CI names CI's service; the sandbox has fabro-pg-ensure's test database."""
    return POSTGRES_URL.sub(r"\1test:test@localhost:5432/test", value)
