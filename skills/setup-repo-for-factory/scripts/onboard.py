#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Onboard the repository in the current directory into a Fabro factory.

Run it from the target repository's root. `status` names the next command; every command is safe to
re-run. Commands that change something outside this machine act only with --apply or --fire.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from onboarding import ci as ci_mod  # noqa: E402
from onboarding import live  # noqa: E402
from onboarding import plan as plan_mod
from onboarding import render as render_mod
from onboarding.factory import WORKFLOWS, EditError, Factory, ScheduleError, Stack, find_root  # noqa: E402

CARGO_TARGET = Path.home() / ".cache" / "fabro-onboard" / "target"
REQUIRED_CONTRACT = ("setup.sh", "ci.sh")
TERMINAL_RUN = {"succeeded", "failed", "cancelled", "canceled", "removing", "archived"}
SMOKE_WORKFLOWS = ("merge-queue", "scout")
SCHEDULER_TESTS = ("tests/test_config.py", "tests/test_app.py", "tests/test_reconcile.py")


class Stop(RuntimeError):
    """A condition the operator has to see. Printed without a traceback."""


# --- locating things -----------------------------------------------------------------------------


def git(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def target_root() -> Path:
    top = git("rev-parse", "--show-toplevel")
    cwd = Path.cwd().resolve()
    if not top or Path(top).resolve() != cwd:
        raise Stop(f"run this from the target repository's root; cwd is {cwd}, git top level is {top or 'none'}")
    return cwd


def origin_repo(root: Path) -> str:
    url = git("remote", "get-url", "origin", cwd=root)
    match = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    if not match:
        raise Stop(f"origin is not a GitHub repository: {url or 'no origin remote'}")
    return match.group(1)


def factory_from(args: argparse.Namespace) -> Factory:
    given = args.factory or os.environ.get("FABRO_FACTORY")
    root = Path(given).expanduser().resolve() if given else find_root(Path(__file__).resolve().parent)
    if root is None or not (root / "ops" / "scheduler" / "repos.toml").is_file():
        raise Stop("no factory checkout found: pass --factory PATH or set FABRO_FACTORY")
    return Factory(root)


def load_plan(factory: Factory, repo: str) -> tuple[Path, dict[str, Any]]:
    path = plan_mod.plan_path(factory.root, repo)
    if not path.is_file():
        raise Stop(f"no plan at {path}; run `facts`, then `init`")
    return path, plan_mod.load(path)


def state_path(plan_file: Path) -> Path:
    return plan_file.with_suffix(".state.json")


def read_state(plan_file: Path) -> dict[str, Any]:
    path = state_path(plan_file)
    return json.loads(path.read_text()) if path.is_file() else {}


def record_state(plan_file: Path, key: str, value: Any) -> None:
    state = read_state(plan_file)
    state[key] = value
    state_path(plan_file).write_text(json.dumps(state, indent=2) + "\n")


def stack_named(factory: Factory, stack_id: str) -> Stack:
    for stack in factory.stacks():
        if stack.id == stack_id:
            return stack
    raise Stop(f"stack {stack_id!r} has no ops/profile-images/Dockerfile.{stack_id}")


# --- facts ---------------------------------------------------------------------------------------


def repo_needs(root: Path, facts: ci_mod.CiFacts) -> dict[str, set[str]]:
    """The runtimes the repository needs, with the versions it pins where it pins one."""
    needs: dict[str, set[str]] = {tool: set(pins) for tool, pins in facts.toolchains.items()}
    names = {Path(f).name for f in git("ls-files", cwd=root).splitlines()}
    markers = {
        "python": {"pyproject.toml", "uv.lock", "requirements.txt", "requirements-dev.txt", "Pipfile"},
        "node": {"package.json"},
        "bun": {"bun.lock", "bun.lockb"},
        "rust": {"Cargo.toml"},
        "go": {"go.mod"},
    }
    for tool, files in markers.items():
        if names & files:
            needs.setdefault(tool, set())
    pin_files = {".python-version": "python", ".nvmrc": "node", ".node-version": "node", "rust-toolchain": "rust"}
    for name, tool in pin_files.items():
        path = root / name
        if path.is_file():
            needs.setdefault(tool, set()).add(path.read_text().strip())
    toolchain_toml = root / "rust-toolchain.toml"
    if toolchain_toml.is_file():
        channel = tomllib.loads(toolchain_toml.read_text()).get("toolchain", {}).get("channel")
        if channel:
            needs.setdefault("rust", set()).add(str(channel))
    for image in facts.services:
        match = re.match(r"(?:[\w./-]*/)?postgres(?:ql)?:(\d+)", image)
        if match:
            needs.setdefault("postgres", set()).add(match.group(1))
    return needs


def _version_key(tool: str, version: str) -> str | None:
    """The part of a version a mismatch is judged on; None for a pin that names no number."""
    match = re.match(r"v?(\d+)(?:\.(\d+))?", version)
    if not match:
        return None
    if tool == "python":
        return f"{match.group(1)}.{match.group(2)}" if match.group(2) else match.group(1)
    return match.group(1)


def judge_stack(stack: Stack, needs: dict[str, set[str]]) -> dict[str, Any]:
    missing = sorted(tool for tool in needs if tool not in stack.tools)
    mismatches = []
    for tool, pins in sorted(needs.items()):
        have = stack.tools.get(tool)
        if not have:
            continue
        for pin in sorted(pins):
            want = _version_key(tool, pin)
            if want and not (_version_key(tool, have) or "").startswith(want):
                mismatches.append(f"{tool}: repository pins {pin}, image has {have}")
    return {"id": stack.id, "image": stack.image, "tools": stack.tools, "missing": missing,
            "mismatches": mismatches, "warmed_from": stack.warm_repo}


def gather_facts(args: argparse.Namespace) -> dict[str, Any]:
    root = target_root()
    repo = origin_repo(root)
    factory = factory_from(args)
    ci = ci_mod.read_ci(root)
    needs = repo_needs(root, ci)
    judged = sorted((judge_stack(s, needs) for s in factory.stacks()),
                    key=lambda j: (len(j["missing"]), len(j["mismatches"]), j["id"]))
    blockers: list[str] = []
    warnings: list[str] = []

    settings = live.repo_settings(repo)
    if settings.get("default_branch") != "main":
        blockers.append(f"default branch is {settings.get('default_branch')}; rows, images and the merge phase all target main")
    if not settings.get("allow_squash_merge"):
        blockers.append("squash merge is off; every merge the factory makes is `gh pr merge --squash`")
    if not live.dependabot_alerts_readable(repo):
        warnings.append("Dependabot alerts are not readable: the dependabot workflow stops at its inventory until they are on")

    owners = {r["name"].split("/")[0] for r in factory.repos()}
    if repo.split("/")[0] not in owners:
        warnings.append(f"owner differs from the factory's ({', '.join(sorted(owners))}): build-images.sh clones from that owner")

    other_services = [i for i in ci.services if not re.search(r"postgres", i)]
    if other_services:
        blockers.append(f"CI uses service containers no sandbox can start: {', '.join(other_services)} (see REFERENCE.md, Blockers)")
    if not judged or judged[0]["missing"]:
        best = judged[0] if judged else {"id": "none", "missing": sorted(needs)}
        blockers.append(f"no image carries everything this repository needs; closest {best['id']} lacks {', '.join(best['missing'])} (see REFERENCE.md, New image)")
    tracked = git("grep", "-il", "testcontainers", "--", "*.py", "*.toml", "*.ts", "*.json", "*.txt", cwd=root).splitlines()
    if tracked:
        warnings.append(f"testcontainers appears in {', '.join(tracked[:3])}: the sandbox has no Docker, so the suite needs its database URL or CI=1 in [env]")
    if not ci.workflows:
        warnings.append("no workflow runs on pull requests: the plan starts empty, and every gate you add needs edit_reason")

    candidates = [j for j in judged if not j["missing"]]
    recommended = candidates[0]["id"] if candidates and not candidates[0]["mismatches"] and (
        len(candidates) == 1 or candidates[1]["mismatches"]) else None
    fabro_dir = root / ".fabro"
    questions = ["priority: a signed integer, smallest wins. Offer the taken values below.",
                 "aliases: distinct short names agents use for the repository in prose; usually none.",
                 "deploy: `deploy` (run the host deploy) or `stage` (commit and push, hand the deploy over)."]
    if not recommended:
        questions.insert(0, "stack: no single image matches cleanly; show `stacks` with its mismatches and ask.")
    return {
        "repo": repo,
        "already_registered": factory.registered(repo),
        "existing_contract_files": sorted(p.name for p in fabro_dir.iterdir()) if fabro_dir.is_dir() else [],
        "blockers": blockers,
        "warnings": warnings,
        "questions_for_the_user": questions,
        "recommended_stack": recommended,
        "stacks": judged,
        "needs": {tool: sorted(pins) for tool, pins in sorted(needs.items())},
        "ci": {"pull_request_workflows": ci.workflows, "other_workflows": ci.excluded, "services": ci.services},
        "taken": {
            "priorities": {r["name"]: r["priority"] for r in factory.repos()},
            "aliases": sorted(a for r in factory.repos() for a in r.get("aliases", [])),
        },
        "server": factory.server_defaults(),
        "factory": {"path": str(factory.root), **factory.status()},
    }


def cmd_facts(args: argparse.Namespace) -> int:
    print(json.dumps(gather_facts(args), indent=2))
    return 0


# --- init and the plan ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = target_root()
    repo = origin_repo(root)
    factory = factory_from(args)
    path = plan_mod.plan_path(factory.root, repo)
    if path.exists() and not args.force:
        raise Stop(f"{path} already exists and holds decisions; re-running init would lose them. Pass --force to redo it.")
    aliases = args.alias or []
    errors = plan_mod.validate_choices(
        stack=args.stack, known_stacks=[s.id for s in factory.stacks()], aliases=aliases,
        used_aliases={a for r in factory.repos() for a in r.get("aliases", [])},
    )
    if errors:
        raise Stop("\n".join(errors))
    ci = ci_mod.read_ci(root)
    needs = repo_needs(root, ci)
    stack = stack_named(factory, args.stack)
    verdict = judge_stack(stack, needs)
    if verdict["missing"]:
        raise Stop(f"{stack.id} lacks {', '.join(verdict['missing'])}: pick another stack or see REFERENCE.md, New image")
    defaults = factory.server_defaults()
    server = {
        "host": args.host or defaults["host"],
        "api_url": args.api_url or defaults["api_url"],
        "scheduler_url": args.scheduler_url or defaults["scheduler_url"],
    }
    plan = plan_mod.draft(
        repo=repo, stack=stack.id, priority=args.priority, aliases=aliases, deploy=args.deploy, server=server,
        ci=ci, needs_postgres="postgres" in needs,
    )
    plan_mod.write(path, plan, header=plan_mod.HEADER)
    roles: dict[str, int] = {}
    for step in plan["step"]:
        roles[step["role"]] = roles.get(step["role"], 0) + 1
    print(f"plan: {path}")
    print("steps: " + ", ".join(f"{count} {role}" for role, count in sorted(roles.items())))
    if verdict["mismatches"]:
        print("version mismatches you accepted with this stack: " + "; ".join(verdict["mismatches"]))
    print("next: resolve every `review`, then run plan-check")
    return 0


def fill_derived(plan: dict[str, Any], root: Path) -> None:
    """Draft `run_tests` targets and fix.sh steps from the gates, once. Later edits are kept."""
    if plan.get("derived"):
        return
    gates = [SimpleNamespace(run=s["run"], cwd=s.get("cwd", ".")) for s in plan.get("step", []) if s["role"] == "gate"]
    plan["target"] = render_mod.draft_targets(gates)
    plan["fix"] = render_mod.draft_fix(root, gates)
    plan["derived"] = True


def cmd_resolve(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    path, plan = load_plan(factory, origin_repo(root))
    matched = [s for s in plan.get("step", []) if s["role"] == "review" and fnmatch.fnmatchcase(s["source"], args.pattern)]
    if not matched:
        raise Stop(f"no step still in review has a source matching {args.pattern!r}")
    if args.role == "skip" and not args.reason:
        raise Stop("--role skip needs --reason")
    for step in matched:
        step["role"], step["reason"] = args.role, args.reason or ""
    plan_mod.write(path, plan, header=plan_mod.HEADER)
    print(f"{args.role}: {len(matched)} step(s)")
    for step in matched:
        print(f"  {step['source']}")
    return 0


def cmd_plan_check(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    path, plan = load_plan(factory, origin_repo(root))
    ci = ci_mod.read_ci(root)
    found = plan_mod.problems(plan, ci, include_env=False)
    if not found:
        plan_mod.settle_env(plan)
        plan_mod.write(path, plan, header=plan_mod.HEADER)
        found = plan_mod.problems(plan, ci)
    if found:
        print(f"{len(found)} problem(s) in {path}:")
        for problem in found:
            print(f"  - {problem}")
        return 1
    fill_derived(plan, root)
    plan_mod.write(path, plan, header=plan_mod.HEADER)
    print(f"plan ok: {path}")
    gates = [s for s in plan["step"] if s["role"] == "gate"]
    print(f"gates {len(gates)}, setup {sum(s['role'] == 'setup' for s in plan['step'])}, "
          f"targets {len(plan.get('target', []))}, fix steps {len(plan.get('fix', []))}")
    if not plan.get("target"):
        print("note: no test-runner gate was recognised, so test.toml has no targets and run_tests cannot run")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    path, plan = load_plan(factory, origin_repo(root))
    found = plan_mod.problems(plan, ci_mod.read_ci(root))
    if found or not plan.get("derived"):
        raise Stop(f"the plan is not settled ({len(found)} problem(s)); run plan-check until it passes")
    for line in render_mod.write_files(root, render_mod.render_all(plan), force=args.force):
        print(line)
    return 0


# --- check: the repository's side ------------------------------------------------------------------


def fabro_io_binary(factory: Factory) -> Path | None:
    if not shutil.which("cargo"):
        return None
    env = {**os.environ, "CARGO_TARGET_DIR": str(CARGO_TARGET)}
    manifest = factory.root / "ops" / "fabro-io" / "Cargo.toml"
    out = subprocess.run(["cargo", "build", "--quiet", "--locked", "--manifest-path", str(manifest)],
                         env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise Stop(f"could not build fabro-io from the factory checkout:\n{out.stderr[-800:]}")
    return CARGO_TARGET / "debug" / "fabro-io"


def repo_checks(root: Path, factory: Factory, plan: dict[str, Any]) -> list[tuple[str, str]]:
    """(PASS | FAIL | SKIP, what), for everything checkable without the host."""
    results: list[tuple[str, str]] = []

    def check(ok: bool, what: str) -> None:
        results.append(("PASS" if ok else "FAIL", what))

    directory = root / ".fabro"
    for name in REQUIRED_CONTRACT:
        check((directory / name).is_file(), f".fabro/{name} exists (every run fails at prep without it)")
    expected = render_mod.render_all(plan)
    for name in sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []:
        path = directory / name
        if name.endswith(".sh"):
            check(os.access(path, os.X_OK), f".fabro/{name} is executable (run as ./.fabro/{name})")
            index = git("ls-files", "-s", "--", f".fabro/{name}", cwd=root)
            if index:
                check(index.startswith("100755"), f".fabro/{name} is mode 100755 in git")
            syntax = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            check(syntax.returncode == 0, f".fabro/{name} passes bash -n {syntax.stderr.strip()}".rstrip())
        text = path.read_text()
        if name in expected and render_mod.MARKER in text:
            check(text == expected[name], f".fabro/{name} matches the plan (change the plan, then render)")
    ci_sh = directory / "ci.sh"
    if ci_sh.is_file():
        text = ci_sh.read_text()
        check("set -euo pipefail" in text, "ci.sh sets -euo pipefail")
        check('test_env=$(fabro-io test-env); eval "$test_env"' in text,
              'ci.sh loads the Test environment with test_env=$(fabro-io test-env); eval "$test_env"')
        check('eval "$(fabro-io' not in text, "ci.sh avoids eval \"$(...)\", which hides a failure under set -e")
        for step in plan.get("step", []):
            if step["role"] == "gate":
                lines = [line for line in step["run"].splitlines() if line.strip()]
                check(all(line in text for line in lines), f"ci.sh runs gate {step['id']}")
    fix = directory / "fix.sh"
    if fix.is_file():
        text = fix.read_text()
        check(text.rstrip().endswith("exit 0") and "set -e" not in text, "fix.sh always exits 0")
    results += _parser_checks(root, factory, directory)
    return results


def _parser_checks(root: Path, factory: Factory, directory: Path) -> list[tuple[str, str]]:
    binary = fabro_io_binary(factory)
    if binary is None:
        return [("SKIP", "no cargo here to build fabro-io; contract-run still parses test.toml in the image")]
    results = []
    if (directory / "test.toml").is_file():
        out = subprocess.run([str(binary), "test-env", "--check", "--root", str(root)], capture_output=True, text=True)
        results.append(("PASS" if out.returncode == 0 else "FAIL", f"test.toml parses under fabro-io {out.stderr.strip()}".rstrip()))
    scout = directory / "scout.toml"
    if scout.is_file():
        out = subprocess.run([str(binary), "scout", "config", "--file", str(scout)], capture_output=True, text=True)
        report = json.loads(out.stdout or "{}")
        results.append(("PASS" if report.get("valid") else "FAIL", f"scout.toml parses under fabro-io {report.get('error') or ''}".rstrip()))
    return results


def cmd_check(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    _, plan = load_plan(factory, origin_repo(root))
    results = repo_checks(root, factory, plan)
    for status, what in results:
        print(f"{status}  {what}")
    return 1 if any(status == "FAIL" for status, _ in results) else 0


def cmd_labels(args: argparse.Namespace) -> int:
    repo = origin_repo(target_root())
    missing = live.missing_labels(repo)
    if not missing:
        print(f"{repo} has every factory label")
        return 0
    print(f"{repo} lacks {len(missing)} of {len(live.label_catalog())} labels: {', '.join(m['name'] for m in missing)}")
    if not args.apply:
        print("re-run with --apply to create them; existing labels stay as they are")
        return 1
    for label in missing:
        live.create_label(repo, label)
    print(f"created {len(missing)}")
    return 0


# --- contract-run: the proof in the real image ---------------------------------------------------


def cmd_contract_run(args: argparse.Namespace) -> int:
    root = target_root()
    repo = origin_repo(root)
    factory = factory_from(args)
    path, plan = load_plan(factory, repo)
    ref = args.ref or git("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    pushed = subprocess.run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{ref}"],
                            cwd=root, capture_output=True, text=True)
    if pushed.returncode != 0:
        raise Stop(f"branch {ref} is not on origin; the host clones from GitHub, so push it first")
    sha = pushed.stdout.split()[0]
    if git("rev-parse", "HEAD", cwd=root) != sha:
        raise Stop(f"origin/{ref} is {sha[:12]}, not this checkout's HEAD; push first so the run proves what you have")
    stack = stack_named(factory, plan["stack"])
    auth = live.token(plan["host"])
    records = {e["id"]: e for e in live.api_list(plan["api_url"], auth, "/environments")}
    record = records.get(stack.id)
    if record is None:
        raise Stop(f"the server has no environment {stack.id}; see REFERENCE.md, New image")
    image = record["image"]["docker"]
    resources = record["resources"]
    memory = str(resources["memory"]).lower().removesuffix("b")
    log = path.with_suffix(".contract-run.log")
    print(f"running setup.sh and ci.sh for {repo}@{sha[:12]} in {image} ({resources['cpu']} CPU, {resources['memory']}) on {plan['host']}")
    code, output = live.contract_run(host=plan["host"], repo=repo, ref=ref, image=image, cpus=str(resources["cpu"]),
                                     memory=memory, log=log, timeout=args.timeout)
    lines = output.strip().splitlines()
    print("\n".join(lines[-40:] if code != 0 else lines[-3:]))
    print(f"full log: {log}")
    if code != 0:
        print(f"FAIL  exit {code}: see REFERENCE.md, Contract failures")
        return 1
    if f"commit {sha}" not in output:
        raise Stop(f"the host tested a different commit than {sha[:12]}: origin/{ref} moved during the run. Run it again.")
    tree = git("rev-parse", f"{sha}:.fabro", cwd=root)
    record_state(path, "contract_run", {"sha": sha, "tree": tree, "image": image,
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    print(f"PASS  recorded for .fabro tree {tree[:12]}")
    return 0


# --- register: the factory's side ----------------------------------------------------------------


def contract_on_main(repo: str) -> list[str]:
    """What is wrong with the contract on the repository's main branch. Empty means it landed."""
    answer = live.gh_probe(f"repos/{repo}/git/trees/main:.fabro")
    if answer is None:
        return [".fabro/ is not on main"]
    modes = {entry["path"]: entry["mode"] for entry in answer["tree"]}
    return [
        f".fabro/{name} on main is {'missing' if name not in modes else 'mode ' + modes[name]}, not an executable file"
        for name in REQUIRED_CONTRACT if modes.get(name) != "100755"
    ]


def cmd_register(args: argparse.Namespace) -> int:
    repo = origin_repo(target_root())
    factory = factory_from(args)
    _, plan = load_plan(factory, repo)
    landed = contract_on_main(repo)
    if landed:
        raise Stop("land the contract before registering: arch-review and dependabot rows are created with their "
                   "schedules on, and a run with no contract fails at prep.\n" + "\n".join(landed))
    state = factory.status()
    if not state["clean"] or not state["at_origin_main"]:
        raise Stop(f"the factory checkout must be clean under ops/ and at origin/main before this edit: {state}. "
                   f"`git -C {factory.root} pull --ff-only`, or commit what is there.")
    stack_named(factory, plan["stack"])
    edits = factory.registration_edits(repo, plan["stack"], plan["priority"], plan.get("aliases", []))
    for relative, new in edits.items():
        old = (factory.root / relative).read_text()
        sys.stdout.writelines(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{relative}", f"b/{relative}", n=0))
    if not args.apply:
        print("\ndry run: re-run with --apply to write these files")
        return 0
    for relative, new in edits.items():
        (factory.root / relative).write_text(new)
    print(f"\nwrote {len(edits)} files. Next: `onboard.py gate`, then in {factory.root}:")
    print(f"  git add {' '.join(edits)}")
    print(f'  git commit -m "feat(ops): add {repo} as a target repo (priority {plan["priority"]})"')
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    factory = factory_from(args)
    steps = [
        ("provision script parses", ["sh", "-n", "ops/provision-server-state.sh"], factory.root),
        ("scheduler tests that name the tracked repositories",
         ["uv", "run", "--quiet", "pytest", "-q", *SCHEDULER_TESTS], factory.root / "ops" / "scheduler"),
        ("make check-fast", ["make", "check-fast"], factory.root),
    ]
    failed = 0
    for name, command, cwd in steps:
        out = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        print(f"{'PASS' if out.returncode == 0 else 'FAIL'}  {name}")
        if out.returncode != 0:
            failed += 1
            print((out.stdout + out.stderr)[-1500:])
    return 1 if failed else 0


# --- verify, smoke, schedules: the live server ---------------------------------------------------


def expected_rows(factory: Factory, repo: str) -> dict[str, dict[str, str]]:
    return {r["id"]: r for r in factory.rows() if r["repo"] == repo}


def live_checks(factory: Factory, plan: dict[str, Any]) -> list[tuple[str, str]]:
    repo, stack_id = plan["repo"], plan["stack"]
    results: list[tuple[str, str]] = []

    def check(ok: bool, what: str) -> None:
        results.append(("PASS" if ok else "FAIL", what))

    health = live.http_json(f"{plan['scheduler_url']}/health")
    row = next((r for r in health.get("repos", []) if r["name"] == repo), None)
    check(row is not None, "the scheduler lists the repository")
    if row:
        check(row["environment_id"] == stack_id and row["enabled"], f"the scheduler has it enabled in {stack_id}")
    auth = live.token(plan["host"])
    environment = next((e for e in live.api_list(plan["api_url"], auth, "/environments") if e["id"] == stack_id), None)
    stack = stack_named(factory, stack_id)
    check(environment is not None and environment["image"]["docker"] == stack.image,
          f"environment {stack_id} points at {stack.image}")
    rows = {r["id"]: r for r in live.api_list(plan["api_url"], auth, "/automations")}
    expected = expected_rows(factory, repo)
    check(len(expected) == len(WORKFLOWS), f"the provisioning script holds all {len(WORKFLOWS)} rows")
    for row_id, want in sorted(expected.items()):
        got = rows.get(row_id)
        if got is None:
            check(False, f"{row_id} exists on the server (run `make provision`)")
            continue
        drift = row_drift(got, want, repo)
        check(not drift, f"{row_id} matches the provisioning script" + (f": {'; '.join(drift)}" if drift else ""))
    return results


def row_drift(got: dict[str, Any], want: dict[str, str], repo: str) -> list[str]:
    triggers = {t["id"]: t for t in got.get("triggers", [])}
    drift = []
    if got.get("environment_id") != want["env"]:
        drift.append(f"environment is {got.get('environment_id')}, not {want['env']}")
    if got.get("target", {}).get("repo") != repo:
        drift.append(f"target is {got.get('target', {}).get('repo')}")
    if got.get("workflow") != want["workflow"]:
        drift.append(f"workflow is {got.get('workflow')}")
    if triggers.get("manual", {}).get("enabled") is not True:
        drift.append("api:manual is missing or disabled, so it cannot be fired")
    schedule = want["schedule_id"].strip('"')
    if schedule and schedule not in triggers:
        drift.append(f"schedule {schedule} is missing")
    elif schedule and triggers[schedule].get("expression") != want["expr"]:
        drift.append(f"schedule is {triggers[schedule].get('expression')!r}, not {want['expr']!r}")
    return drift


def schedule_states(factory: Factory, plan: dict[str, Any]) -> dict[str, bool | None]:
    auth = live.token(plan["host"])
    rows = {r["id"]: r for r in live.api_list(plan["api_url"], auth, "/automations")}
    states: dict[str, bool | None] = {}
    for row_id in sorted(expected_rows(factory, plan["repo"])):
        schedules = [t for t in rows.get(row_id, {}).get("triggers", []) if t.get("type") == "schedule"]
        states[row_id] = schedules[0].get("enabled") if schedules else None
    return states


def cmd_verify(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    _, plan = load_plan(factory, origin_repo(root))
    results = live_checks(factory, plan)
    for status, what in results:
        print(f"{status}  {what}")
    print("\nschedules:")
    for row_id, enabled in schedule_states(factory, plan).items():
        print(f"  {row_id}: {'none' if enabled is None else 'on' if enabled else 'off'}")
    return 1 if any(status == "FAIL" for status, _ in results) else 0


def smoke_rows(plan: dict[str, Any]) -> list[str]:
    short = plan["repo"].split("/")[1]
    return [f"{workflow}-{short}" for workflow in SMOKE_WORKFLOWS]


def cmd_smoke(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    path, plan = load_plan(factory, origin_repo(root))
    rows = smoke_rows(plan)
    if not args.fire:
        print(f"would fire {', '.join(rows)}: no agent stage, no coder slot, no input. Re-run with --fire.")
        return 1
    auth = live.token(plan["host"])
    runs = {row: live.fire(plan["api_url"], auth, row) for row in rows}
    print("\n".join(f"fired {row}: {run}" for row, run in runs.items()))
    deadline = time.monotonic() + args.timeout
    outcomes: dict[str, str] = {}
    while len(outcomes) < len(runs) and time.monotonic() < deadline:
        for row, run_id in runs.items():
            if row not in outcomes:
                kind, _ = live.run_state(plan["api_url"], auth, run_id)
                if kind in TERMINAL_RUN:
                    outcomes[row] = kind
                    print(f"{row}: {kind}")
        if len(outcomes) < len(runs):
            time.sleep(10)
    if len(outcomes) < len(runs) or any(kind != "succeeded" for kind in outcomes.values()):
        print(f"FAIL  {outcomes or 'no run finished'}; read it with `fabro events <run> -p` on the host (AGENTS.md, The server)")
        return 1
    record_state(path, "smoke", {"runs": runs, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    print("PASS  recorded")
    return 0


def cmd_schedules(args: argparse.Namespace) -> int:
    root = target_root()
    factory = factory_from(args)
    path, plan = load_plan(factory, origin_repo(root))
    if "smoke" not in read_state(path):
        raise Stop("run smoke --fire first: these schedules turn on only after a hand-fired run passes")
    rows = smoke_rows(plan)
    if not args.apply:
        print(f"would turn on {', '.join(rows)}. Re-run with --apply.")
        return 1
    env = {**os.environ, "FABRO_API_URL": plan["api_url"], "FABRO_DEV_TOKEN": live.token(plan["host"])}
    script = factory.root / "ops" / "fabro-automation-schedule.sh"
    for row in rows:
        out = subprocess.run(["sh", str(script), row, "on"], env=env, capture_output=True, text=True)
        print((out.stdout + out.stderr).strip())
        if out.returncode != 0:
            return 1
    return 0


# --- status --------------------------------------------------------------------------------------


def next_step(args: argparse.Namespace) -> tuple[list[str], str | None]:
    """Done lines, and the one thing to do next. None means the scripted part is finished."""
    root = target_root()
    repo = origin_repo(root)
    factory = factory_from(args)
    done: list[str] = []
    path = plan_mod.plan_path(factory.root, repo)
    if not path.is_file():
        return done, "run `facts`, settle `questions_for_the_user` with the user, then `init`"
    plan = plan_mod.load(path)
    done.append(f"plan {path}")
    found = plan_mod.problems(plan, ci_mod.read_ci(root))
    if found:
        return done, f"resolve {len(found)} plan problem(s): see `plan-check`"
    if not plan.get("derived"):
        return done, "run `plan-check` to draft the targets and fix steps"
    done.append("plan resolved")
    results = repo_checks(root, factory, plan) if (root / ".fabro").is_dir() else [("FAIL", "no .fabro/")]
    failing = [what for status, what in results if status == "FAIL"]
    if failing:
        return done, f"run `render`, then clear `check`: {failing[0]}"
    done.append("contract files pass `check`")
    missing = live.missing_labels(repo)
    if missing:
        return done, f"run `labels --apply` ({len(missing)} missing)"
    done.append("labels present")

    state = read_state(path)
    tree = git("rev-parse", "HEAD:.fabro", cwd=root)
    proven = state.get("contract_run", {}).get("tree")
    if not tree or proven != tree:
        return done, "commit .fabro/ on a branch, push it, then run `contract-run` (see SKILL.md, step 5)"
    done.append(f"contract passed in its image at tree {tree[:12]}")
    landed = contract_on_main(repo)
    if landed:
        return done, f"open the contract PR and have a human merge it: {landed[0]}"
    done.append("contract on main")

    registered = factory.registered(repo)
    if not all(registered.values()):
        return done, "run `register`, read the diff, then `register --apply` and `gate`"
    done.append("registered in the factory checkout")
    pushed = git("show", "origin/main:ops/scheduler/repos.toml", cwd=factory.root)
    if f'"{repo}"' not in pushed:
        return done, f"commit the registration and push it from {factory.root} (`gate` must pass)"
    done.append("registration pushed")
    if plan.get("deploy") == "stage":
        return done, f"STOP: deploy bound is `stage`. Hand over `make deploy` from a clean {factory.root} at origin/main."

    failing = [what for status, what in live_checks(factory, plan) if status == "FAIL"]
    if failing:
        return done, f"run `make deploy` in {factory.root}, then `verify`: {failing[0]}"
    done.append("live: scheduler and automation rows verified")
    if "smoke" not in state:
        return done, "ask the user, then run `smoke --fire`"
    done.append("smoke runs succeeded")
    schedules = schedule_states(factory, plan)
    if not all(schedules.get(row) for row in smoke_rows(plan)):
        return done, "ask the user, then run `schedules --apply`"
    done.append("merge-queue and scout schedules on")
    return done, None


def cmd_status(args: argparse.Namespace) -> int:
    done, todo = next_step(args)
    for line in done:
        print(f"done  {line}")
    if todo:
        print(f"NEXT  {todo}")
        return 1
    print("NEXT  the scripted part is finished; run the real-issue check in SKILL.md, step 11")
    return 0


# --- the CLI -------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    top.add_argument("--factory", help="the factory checkout; default: the one this script sits in, or $FABRO_FACTORY")
    sub = top.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="what is done, and the one next step").set_defaults(func=cmd_status)
    sub.add_parser("facts", help="read-only JSON: repo, stacks, blockers, what to ask").set_defaults(func=cmd_facts)

    init = sub.add_parser("init", help="write the plan from CI and the interview's answers")
    init.add_argument("--stack", required=True)
    init.add_argument("--priority", required=True, type=int)
    init.add_argument("--alias", action="append")
    init.add_argument("--deploy", required=True, choices=("deploy", "stage"))
    init.add_argument("--host")
    init.add_argument("--api-url")
    init.add_argument("--scheduler-url")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    resolve = sub.add_parser("resolve", help="give every review step whose source matches a glob one role")
    resolve.add_argument("pattern", help='a glob over the source column, such as "docker-main.yml > *"')
    resolve.add_argument("--role", required=True, choices=plan_mod.ROLES)
    resolve.add_argument("--reason")
    resolve.set_defaults(func=cmd_resolve)

    sub.add_parser("plan-check", help="list what the plan still needs").set_defaults(func=cmd_plan_check)

    render = sub.add_parser("render", help="write .fabro/ from the plan and the templates")
    render.add_argument("--force", action="store_true", help="replace existing files that differ")
    render.set_defaults(func=cmd_render)

    sub.add_parser("check", help="offline gates on .fabro/").set_defaults(func=cmd_check)

    labels = sub.add_parser("labels", help="create the factory labels the repository lacks")
    labels.add_argument("--apply", action="store_true")
    labels.set_defaults(func=cmd_labels)

    contract = sub.add_parser("contract-run", help="run setup.sh and ci.sh in the real image, on the host")
    contract.add_argument("--ref", help="branch to clone; default: the current branch")
    contract.add_argument("--timeout", type=int, default=3600)
    contract.set_defaults(func=cmd_contract_run)

    register = sub.add_parser("register", help="print, or with --apply write, the factory edits")
    register.add_argument("--apply", action="store_true")
    register.set_defaults(func=cmd_register)

    sub.add_parser("gate", help="the factory's gates after register").set_defaults(func=cmd_gate)
    sub.add_parser("verify", help="compare the live server to the factory").set_defaults(func=cmd_verify)

    smoke = sub.add_parser("smoke", help="hand-fire the two agent-free rows and wait")
    smoke.add_argument("--fire", action="store_true")
    smoke.add_argument("--timeout", type=int, default=900)
    smoke.set_defaults(func=cmd_smoke)

    schedules = sub.add_parser("schedules", help="turn on the merge-queue and scout schedules after smoke")
    schedules.add_argument("--apply", action="store_true")
    schedules.set_defaults(func=cmd_schedules)
    return top


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (Stop, EditError, ScheduleError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001  network and subprocess failures, as one line
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        print(f"ERROR: {live.describe_error(error)}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
