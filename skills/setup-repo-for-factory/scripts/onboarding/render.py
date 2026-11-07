"""Turn a resolved plan into the repository's `.fabro/` contract files, from templates only."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from .plan import effective_env, toml_value

MARKER = "Onboarded by setup-repo-for-factory"
TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"
EXECUTABLE = {"setup.sh", "ci.sh", "fix.sh"}

RUNNER = re.compile(
    r"\b(pytest|vitest|jest|cargo nextest run|cargo test|(?:npm|pnpm|yarn|bun)(?: run)? test|bun test|go test)\b"
)
NEEDS_DASHES = re.compile(r"^(npm|pnpm|yarn|bun)( run)? test\b(?!.* -- )")


def _fill(template: str, **values: str) -> str:
    text = (TEMPLATES / template).read_text()
    for name, value in values.items():
        text = text.replace(f"@@{name}@@", value)
    leftover = re.search(r"@@[A-Z_]+@@", text)
    if leftover:
        raise ValueError(f"{template}: {leftover.group(0)} was not filled")
    return text


def _block(step: dict[str, Any]) -> str:
    """One step as shell. Lines stay exact, so a heredoc in CI keeps working here."""
    run = step["run"].rstrip()
    cwd = step.get("cwd") or "."
    env = step.get("env") or {}
    comment = f"# {step['source']}".replace("\n", " ")
    if cwd == "." and not env and "\n" not in run:
        return f"{comment}\n{run}"
    if not env and "\n" not in run:
        return f"{comment}\n(cd {shlex.quote(cwd)} && {run})"
    lines = [comment, "("]
    if cwd != ".":
        lines.append(f"cd {shlex.quote(cwd)}")
    lines += [f"export {key}={shlex.quote(str(value))}" for key, value in sorted(env.items())]
    lines += [run, ")"]
    return "\n".join(lines)


def _idempotent(step: dict[str, Any]) -> dict[str, Any]:
    """Every run's prep runs setup.sh, and `cargo install` spends a resolve even when nothing changes."""
    match = re.fullmatch(r"cargo install\s+(?:--locked\s+)?([\w-]+)(.*)", step["run"].strip())
    if not match:
        return step
    return {**step, "run": f"command -v {match.group(1)} >/dev/null 2>&1 || {step['run'].strip()}"}


def _sources(plan: dict[str, Any]) -> str:
    return ", ".join(f".github/workflows/{w}" for w in plan.get("workflows", [])) or "(no pull request workflow)"


def _skipped(plan: dict[str, Any]) -> str:
    """What this gate leaves out and why. The Refuter reads ci.sh, so a gap is stated in it."""
    reasons = [
        f"#   {step['source']}: {step['reason']}".replace("\n", " ")
        for step in plan.get("step", [])
        if step.get("role") == "skip" and step.get("suggested") != "skip"
    ]
    return "\n".join(reasons) if reasons else "#   nothing beyond runner setup"


def render_all(plan: dict[str, Any]) -> dict[str, str]:
    """Filename under `.fabro/` to exact content. Same plan, same bytes."""
    steps = plan.get("step", [])
    files = {
        "setup.sh": _fill(
            "setup.sh.tmpl", MARKER=MARKER, SOURCES=_sources(plan),
            STEPS="\n\n".join(_block(_idempotent(s)) for s in steps if s["role"] == "setup") or ": # nothing to install",
        ),
        "ci.sh": _fill(
            "ci.sh.tmpl", MARKER=MARKER, SOURCES=_sources(plan), SKIPPED=_skipped(plan),
            STEPS="\n\n".join(_block(s) for s in steps if s["role"] == "gate"),
        ),
        "test.toml": _test_toml(plan),
        "scout.toml": _fill("scout.toml.tmpl", MARKER=MARKER),
    }
    if plan.get("fix"):
        files["fix.sh"] = _fill(
            "fix.sh.tmpl", MARKER=MARKER,
            STEPS="\n".join(f"(cd {shlex.quote(f['cwd'])} && {f['run']}) || true" for f in plan["fix"]),
        )
    return files


def _test_toml(plan: dict[str, Any]) -> str:
    sections: list[str] = []
    env = effective_env(plan)
    if env:
        sections.append("[env]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in env.items()))
    if plan.get("prepare_run"):
        sections.append(
            f"[prepare]\ninputs = {toml_value(plan.get('prepare_inputs', []))}\n"
            f"timeout = {plan.get('prepare_timeout', 300)}\nrun = {toml_value(plan['prepare_run'])}"
        )
    for target in plan.get("target", []):
        lines = [f"[targets.{target['name']}]", f"about = {json.dumps(target['about'])}"]
        if target.get("cwd", ".") != ".":
            lines.append(f"cwd = {json.dumps(target['cwd'])}")
        lines += [f"run = {json.dumps(target['run'])}", f"timeout = {target.get('timeout', 300)}"]
        sections.append("\n".join(lines))
    return _fill("test.toml.tmpl", MARKER=MARKER, SECTIONS="\n\n".join(sections))


def draft_targets(gate_steps: list[Any]) -> list[dict[str, Any]]:
    """One `run_tests` target per test-runner gate. Task validation keys stay off: full suite."""
    targets: list[dict[str, Any]] = []
    used: set[str] = set()
    for step in gate_steps:
        run = step.run.strip()
        if "\n" in run or "--doc" in run or not RUNNER.search(run):
            continue
        cwd = step.cwd
        inline = re.match(r"^cd\s+(\S+)\s*&&\s*(.+)$", run)
        if inline:
            cwd = inline.group(1) if cwd == "." else f"{cwd}/{inline.group(1)}"
            run = inline.group(2)
        if not RUNNER.search(run):
            continue
        runner = RUNNER.search(run).group(1)
        base = re.sub(r"[^a-z0-9-]+", "-", (Path(cwd).name if cwd != "." else runner.split()[0]).lower()).strip("-") or "unit"
        name, suffix = base, 2
        while name in used:
            name, suffix = f"{base}-{suffix}", suffix + 1
        used.add(name)
        if NEEDS_DASHES.search(run):
            run = f"{run} --"
        about = f"{runner} in {cwd}; pass test files relative to {cwd}" if cwd != "." else f"{runner}; pass test files"
        targets.append({"name": name, "about": about, "cwd": cwd, "run": run, "timeout": 300})
    return targets


FIX_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bruff format --check\b"), "ruff format"),
    (re.compile(r"\bruff check\b(?!.*--fix)"), "ruff check --fix"),
    (re.compile(r"\bcargo fmt\b(.*?)\s*(?:--\s*)?--check\b"), r"cargo fmt\1"),
    (re.compile(r"\bcargo clippy\b(.*?)(?:\s+--\s+.*)?$"), r"cargo clippy --fix --allow-dirty --allow-staged\1"),
    (re.compile(r"\bprettier\b(.*?)\s(?:--check|-c)\b"), r"prettier\1 --write"),
    (re.compile(r"\beslint\b(?!.*--fix)"), "eslint --fix"),
    (re.compile(r"\bblack --check\b"), "black"),
    (re.compile(r"\bisort --check(?:-only)?\b"), "isort"),
]
PKG_RUN = re.compile(r"^(?:npm|pnpm|yarn|bun) run ([\w:-]+)$")


def draft_fix(root: Path, gate_steps: list[Any]) -> list[dict[str, str]]:
    """Machine-applicable fixes only, each derived from a check the gate already runs."""
    fixes: list[dict[str, str]] = []
    for step in gate_steps:
        run = step.run.strip()
        if "\n" in run:
            continue
        fixed = _package_fix(root / step.cwd, run)
        if fixed is None:
            fixed = next((p.sub(r, run) for p, r in FIX_REWRITES if p.search(run)), None)
        if fixed and fixed != run:
            row = {"cwd": step.cwd, "run": fixed}
            if row not in fixes:
                fixes.append(row)
    return fixes


def _package_fix(cwd: Path, run: str) -> str | None:
    """`npm run lint` has a fix only when package.json declares one; never guess a script name."""
    match = PKG_RUN.match(run)
    if not match:
        return None
    try:
        scripts = json.loads((cwd / "package.json").read_text()).get("scripts", {})
    except (OSError, ValueError):
        return None
    tool = run.split()[0]
    name = match.group(1)
    for candidate in (f"{name}:fix", "format" if name in ("format:check", "fmt:check") else ""):
        if candidate and candidate in scripts:
            return f"{tool} run {candidate}"
    if name == "lint" and "eslint" in str(scripts.get("lint", "")):
        return f"{tool} run lint -- --fix" if tool != "bun" else f"{tool} run lint --fix"
    return ""


def write_files(root: Path, files: dict[str, str], *, force: bool) -> list[str]:
    """Write what is missing; report the rest. An existing file changes only with `force`."""
    report = []
    directory = root / ".fabro"
    directory.mkdir(exist_ok=True)
    for name, content in files.items():
        path = directory / name
        if path.exists() and path.read_text() == content:
            report.append(f"unchanged  .fabro/{name}")
        elif path.exists() and not force:
            report.append(f"KEPT       .fabro/{name} differs from the plan; compare it, or re-run with --force")
        else:
            verb = "replaced " if path.exists() else "wrote    "
            path.write_text(content)
            report.append(f"{verb}  .fabro/{name}")
        if name in EXECUTABLE and path.exists():
            path.chmod(0o755)
    return report
