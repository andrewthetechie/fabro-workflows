#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Plan factory issues for the repository in the current directory.

    facts                     the factory contract: what .fabro/ci.sh runs, the orientation issue, labels
    init <slug>               create a plan directory (plan.json, drafts/, ledger.md) and print its path
    lint <draft.md>...        lint drafts on their own (--lane, --parent)
    check <plan.json>         lint every draft in a plan, and check its graph
    publish <plan.json>       --dry-run prints every GitHub call; --yes makes them

Run it from the target repository's root. Exit 0 = clean, 1 = findings or a refusal, 2 = usage.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from factory_issues import ci, draft, lint  # noqa: E402
from factory_issues import plan as plan_mod  # noqa: E402
from factory_issues import publish as pub  # noqa: E402

SKILL_LABELS = ("agent", "agent-held", "agent-split", "fast-path", "needs-triage")


class Stop(RuntimeError):
    """A condition the operator has to see. Printed without a traceback."""


def run(*cmd: str) -> str | None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def target_root(given: str | None = None) -> Path:
    if given:
        cwd = Path(given).resolve()
    else:
        top = run("git", "rev-parse", "--show-toplevel")
        cwd = Path.cwd().resolve()
        if not top or Path(top).resolve() != cwd:
            raise Stop("run this from the target repository's root")
    if not (cwd / ci.CI).is_file():
        raise Stop("this repository has no .fabro/ci.sh, so the factory cannot run its work. Onboard it first "
                   "with the setup-repo-for-factory skill")
    return cwd


def repo_name() -> str:
    name = run("gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    if not name:
        raise Stop("`gh repo view` failed; check `gh auth status`")
    return name


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "plan-factory-issues"


# --- commands --------------------------------------------------------------------------------------


def cmd_facts(_: argparse.Namespace) -> int:
    root = target_root()
    repo = repo_name()
    routes = ci.follow(root)
    print(f"repo: {repo}")
    print("\nWhat .fabro/ci.sh runs (a command criterion must name one of these, and cite its route):")
    for c in routes.commands:
        if c.key:
            print(f"  {c.key:<28} {c.route()}")
    print("\nWhat the lint could not follow (a command that runs only in these gets a partial-route warning):")
    for u in routes.unresolved or []:
        print(f"  {u.where}: `{u.text}` ({u.why})")
    if not routes.unresolved:
        print("  nothing")
    orient = run("gh", "issue", "list", "--label", "fabro-orientation", "--state", "open",
                 "--json", "number,title", "--limit", "5")
    rows = json.loads(orient) if orient else []
    print("\norientation: " + (", ".join(f"#{r['number']} {r['title']}" for r in rows) if rows else
          "none (no open `fabro-orientation` issue; read the code instead)"))
    labels = run("gh", "label", "list", "--limit", "500", "--json", "name", "-q", ".[].name")
    have = set((labels or "").split("\n"))
    missing = [lbl for lbl in SKILL_LABELS if lbl not in have]
    print("labels: " + ("all present" if not missing else "MISSING " + ", ".join(missing)
                         + " (setup-repo-for-factory's `labels --apply` creates them)"))
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    target_root()
    repo = repo_name()
    if not plan_mod.PLAN_ID.match(a.slug):
        raise Stop("the slug must be 3-61 characters of a-z, 0-9 and -")
    d = Path(a.dir) if a.dir else state_dir() / repo.replace("/", "-") / a.slug
    if (d / "plan.json").exists():
        print(d / "plan.json")
        print("exists; continuing that plan", file=sys.stderr)
        return 0
    (d / "drafts").mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (d / "plan.json").write_text(json.dumps({"plan": a.slug, "repo": repo, "created": created, "issues": []}, indent=2) + "\n")
    (d / "ledger.md").write_text(f"# Decision ledger: {a.slug}\n\n## Decided\n\n## Defaults adopted\n\n"
                                 "## Rejected\n\n## Assumptions\n\n## Left open (needs-triage)\n")
    print(d / "plan.json")
    return 0


def _print(findings: list[lint.Finding]) -> int:
    for f in sorted(findings, key=lambda f: (f.path, f.line, f.rule)):
        print(f)
    errs = plan_mod.errors(findings)
    warns = [f for f in findings if f.severity == "warning" and not f.waived]
    waived = [f for f in findings if f.waived]
    print(f"\n{len(errs)} error(s), {len(warns)} warning(s), {len(waived)} waived")
    return 1 if errs else 0


def cmd_lint(a: argparse.Namespace) -> int:
    routes = ci.follow(target_root(a.root))
    findings: list[lint.Finding] = []
    for p in a.drafts:
        path = Path(p)
        files = sorted(path.glob("*.md")) if path.is_dir() else [path]
        for f in files:
            findings += lint.lint_draft(draft.parse(f.resolve()), routes, lane=a.lane,
                                        role="parent" if a.parent else "issue")
    return _print(findings)


def cmd_check(a: argparse.Namespace) -> int:
    routes = ci.follow(target_root(a.root))
    return _print(plan_mod.check(plan_mod.load(Path(a.plan)), routes))


def cmd_publish(a: argparse.Namespace) -> int:
    root = target_root()
    p = plan_mod.load(Path(a.plan))
    repo = repo_name()
    if repo != p.data.get("repo"):
        raise Stop(f"the plan is for {p.data.get('repo')}, but this checkout is {repo}")
    if plan_mod.errors(plan_mod.check(p, ci.follow(root))):
        raise Stop(f"the plan has errors; run `check {a.plan}` and fix them first")
    if not a.yes and not a.dry_run:
        print("publish writes to GitHub, and the scheduler can dispatch an `agent` issue within minutes.\n"
              "Showing the dry run. Re-run with --yes only after the operator's explicit yes.\n")
    pub.publish(p, pub.Gh(repo), write=bool(a.yes and not a.dry_run))
    return 0 if a.yes or a.dry_run else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("facts").set_defaults(fn=cmd_facts)
    s = sub.add_parser("init")
    s.add_argument("slug")
    s.add_argument("--dir", help="where to put the plan (default: $XDG_STATE_HOME/plan-factory-issues/<repo>/<slug>)")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("lint")
    s.add_argument("drafts", nargs="+")
    s.add_argument("--lane", choices=plan_mod.LANES)
    s.add_argument("--parent", action="store_true", help="lint as a Split parent")
    s.add_argument("--root", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_lint)
    s = sub.add_parser("check")
    s.add_argument("plan")
    s.add_argument("--root", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("publish")
    s.add_argument("plan")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--yes", action="store_true", help="the operator said yes: write to GitHub")
    s.set_defaults(fn=cmd_publish)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (Stop, pub.GitHubError) as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
