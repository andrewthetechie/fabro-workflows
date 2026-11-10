"""Publish an approved plan: create the issues, link the children, label them last.

The order is the safety. The scheduler dispatches an open `agent` issue within minutes and
promotes an `agent-held` child once its predecessor closes (ADR 0015 D4, `split.py`), so:

1. Parents, then children and independent issues, are created **unlabelled**: invisible to the
   queue. A child's first line is `<!-- fabro:split-child parent=<P> after=<N> -->`, and `N`
   (0 for a chain's head) is always known because the plan lists a predecessor first.
2. Children are linked to their parent as GitHub sub-issues, and the parent body gets its
   `## Split into` checklist. The scheduler closes a parent from that sub-issue list.
3. Labels go on last: every extra label, `agent-split` and `agent-held` first, then `agent` or
   `needs-triage`. Nothing is queueable until everything that holds it is in place.

Every created number is written to the plan file before the next call, and each issue body ends
with a hidden plan marker. A re-run after an interruption finds what exists through the plan
file, then through the marker in the repository's issue list (the REST list, not search, which
is eventually consistent), and creates nothing twice.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from .plan import Plan

MARKER = "<!-- plan-factory-issues plan={plan} key={key} -->"
MARKER_RE = re.compile(r"<!-- plan-factory-issues plan=([a-z0-9-]+) key=([a-z0-9-]+) -->")
CHILD = "<!-- fabro:split-child parent={parent} after={after} -->"


class GitHubError(RuntimeError):
    pass


class GitHub(Protocol):
    def labels(self) -> set[str]: ...
    def issues_since(self, since: str) -> list[tuple[int, str]]: ...
    def create_issue(self, title: str, body: str) -> int: ...
    def edit_body(self, number: int, body: str) -> None: ...
    def issue_id(self, number: int) -> int: ...
    def sub_issues(self, parent: int) -> list[int]: ...
    def link(self, parent: int, child_id: int) -> None: ...
    def add_label(self, number: int, label: str) -> None: ...


class Gh:
    """`gh api`, one call per step, so the order above is the order GitHub sees."""

    def __init__(self, repo: str):
        self.repo = repo

    def _api(self, *args: str, payload: dict | None = None) -> str:
        cmd = ["gh", "api", *args]
        if payload is not None:
            cmd += ["--input", "-"]
        r = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise GitHubError(f"gh api {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
        return r.stdout

    def labels(self) -> set[str]:
        return set(self._api("--paginate", f"repos/{self.repo}/labels", "--jq", ".[].name").split("\n")) - {""}

    def issues_since(self, since: str) -> list[tuple[int, str]]:
        out = self._api("--paginate", f"repos/{self.repo}/issues?state=all&since={since}&per_page=100",
                        "--jq", ".[] | select(.pull_request == null) | {number, body}")
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
        return [(r["number"], r.get("body") or "") for r in rows]

    def create_issue(self, title: str, body: str) -> int:
        return int(self._api("-X", "POST", f"repos/{self.repo}/issues", "--jq", ".number",
                             payload={"title": title, "body": body}).strip())

    def edit_body(self, number: int, body: str) -> None:
        self._api("-X", "PATCH", f"repos/{self.repo}/issues/{number}", "--jq", ".number", payload={"body": body})

    def issue_id(self, number: int) -> int:
        return int(self._api(f"repos/{self.repo}/issues/{number}", "--jq", ".id").strip())

    def sub_issues(self, parent: int) -> list[int]:
        out = self._api("--paginate", f"repos/{self.repo}/issues/{parent}/sub_issues", "--jq", ".[].number")
        return [int(x) for x in out.split()]

    def link(self, parent: int, child_id: int) -> None:
        self._api("-X", "POST", f"repos/{self.repo}/issues/{parent}/sub_issues", "-F", f"sub_issue_id={child_id}")

    def add_label(self, number: int, label: str) -> None:
        self._api("-X", "POST", f"repos/{self.repo}/issues/{number}/labels", payload={"labels": [label]})


def labels_for(entry: dict, held: bool) -> list[str]:
    """An entry's labels in the order they go on; the queueing label is always last."""
    if entry.get("role") == "parent":
        return [*entry.get("labels", []), "agent-split"]
    out = list(entry.get("labels", []))
    if entry["lane"] == "fast-path":
        out.append("fast-path")
    if held:
        out.append("agent-held")
    elif entry["lane"] == "needs-triage":
        out.append("needs-triage")
    else:
        out.append("agent")
    return out


def publish(p: Plan, gh: GitHub, *, write: bool, say: Callable[[str], None] = print) -> None:
    """Create every issue in the plan. With `write=False` it reads GitHub and prints each write
    it would make, changing nothing (the plan file included)."""
    data = p.data if write else copy.deepcopy(p.data)
    issues = data["issues"]
    by = {e["key"]: e for e in issues}
    repo = data["repo"]

    def save() -> None:
        if write:
            p.save()

    def held(e: dict) -> bool:
        return bool(e.get("after"))

    if data.get("published"):
        say(f"already published at {data['published']}")
        _summary(issues, repo, say)
        return

    # Refuse before any write when a label the plan needs does not exist in the repository.
    needed = {lbl for e in issues for lbl in labels_for(e, held(e))}
    missing = sorted(needed - gh.labels())
    if missing:
        raise GitHubError(f"{repo} lacks the labels {', '.join(missing)}; run setup-repo-for-factory's "
                          "`labels --apply` (factory labels) or create the others, then re-run")

    # Find what an interrupted publish already created.
    if any("number" not in e for e in issues):
        created = data.get("created") or (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        since = (datetime.fromisoformat(created.replace("Z", "+00:00")) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for number, body in gh.issues_since(since):
            for plan_id, key in MARKER_RE.findall(body):
                if plan_id == data["plan"] and key in by and "number" not in by[key]:
                    by[key]["number"] = number
                    say(f"found #{number} [{key}], created by an earlier publish")
        save()

    def num(e: dict) -> str:
        return str(e["number"])

    # 1. Create, parents first, unlabelled.
    order = [e for e in issues if e.get("role") == "parent"] + [e for e in issues if e.get("role") != "parent"]
    for e in order:
        if "number" in e:
            continue
        title, body = _compose(p, data, e, by)
        if write:
            e["number"] = gh.create_issue(title, body)
            save()
            say(f"created #{e['number']} {title} [{e['key']}]")
        else:
            e["number"] = f"<{e['key']}>"
            say(f"would create `{title}` [{e['key']}], unlabelled")
            for line in body.splitlines()[:1]:
                if line.startswith("<!-- fabro:"):
                    say(f"    first line: {line}")

    # 2. Link children, and write each parent's `## Split into`.
    for parent in [e for e in issues if e.get("role") == "parent"]:
        kids = [e for e in issues if e.get("parent") == parent["key"]]
        existing = set(gh.sub_issues(parent["number"])) if write or isinstance(parent["number"], int) else set()
        for k in kids:
            if k.get("linked"):
                continue
            if k["number"] not in existing:
                if write:
                    try:
                        gh.link(parent["number"], gh.issue_id(k["number"]))
                    except GitHubError:
                        if k["number"] not in gh.sub_issues(parent["number"]):
                            raise
                say(f"{'linked' if write else 'would link'} #{num(k)} as a sub-issue of #{num(parent)}")
            k["linked"] = True
            save()
        if not parent.get("body_written"):
            if write:
                gh.edit_body(parent["number"], _parent_body(p, data, parent, kids))
            say(f"{'wrote' if write else 'would write'} #{num(parent)}'s `## Split into`: "
                + ", ".join(f"#{num(k)}" for k in kids))
            parent["body_written"] = True
            save()

    # 3. Labels: holds and extras everywhere first, queueing labels last.
    holding = [e for e in issues if e.get("role") == "parent" or held(e)]
    queueing = [e for e in issues if e not in holding]
    for e in queueing:  # extras only: the queueing label waits for every hold
        for lbl in labels_for(e, False)[:-1]:
            if not e.get("labelled"):
                _label(gh, e, lbl, write, say)
    for e in holding:
        if not e.get("labelled"):
            for lbl in labels_for(e, held(e)):
                _label(gh, e, lbl, write, say)
            e["labelled"] = True
            save()
    for e in queueing:
        if not e.get("labelled"):
            _label(gh, e, labels_for(e, False)[-1], write, say)
            e["labelled"] = True
            save()

    if write:
        data["published"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save()
        _summary(issues, repo, say)
    else:
        say("dry run: nothing was written. Re-run with --yes after the operator's explicit yes.")


def _label(gh: GitHub, e: dict, label: str, write: bool, say: Callable[[str], None]) -> None:
    if write:
        gh.add_label(e["number"], label)
    say(f"{'labelled' if write else 'would label'} #{e['number']} `{label}`")


def _compose(p: Plan, data: dict, e: dict, by: dict[str, dict]) -> tuple[str, str]:
    from . import draft

    d = draft.parse(p.draft_path(e))
    body = d.body()
    if e.get("parent"):
        parent = by[e["parent"]]
        after = by[e["after"]]["number"] if e.get("after") else 0
        body = f"{CHILD.format(parent=parent['number'], after=after)}\n\nPart of #{parent['number']}.\n\n{body}"
    return d.title or e["key"], f"{body.rstrip()}\n\n{MARKER.format(plan=data['plan'], key=e['key'])}\n"


def _parent_body(p: Plan, data: dict, parent: dict, kids: list[dict]) -> str:
    from . import draft

    d = draft.parse(p.draft_path(parent))
    lines = [d.body().rstrip(), "", "## Split into", ""]
    for k in kids:
        lines.append(f"- [ ] #{k['number']} {draft.parse(p.draft_path(k)).title}")
    lines += ["", MARKER.format(plan=data["plan"], key=parent["key"]), ""]
    return "\n".join(lines)


def _summary(issues: list[dict], repo: str, say: Callable[[str], None]) -> None:
    for e in issues:
        n = e.get("number")
        what = "split parent" if e.get("role") == "parent" else (
            f"held behind [{e['after']}]" if e.get("after") else e.get("lane", ""))
        say(f"#{n} [{e['key']}] {what}: https://github.com/{repo}/issues/{n}")
