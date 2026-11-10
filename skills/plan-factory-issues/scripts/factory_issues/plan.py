"""The plan file: the approved issue graph the publisher creates.

    {
      "plan": "invoice-export",            # the plan id; scopes the publisher's hidden marker
      "repo": "owner/name",
      "created": "2026-11-10T15:00:00Z",
      "issues": [
        {"key": "export", "role": "parent", "draft": "drafts/export.md"},
        {"key": "csv", "draft": "drafts/csv.md", "lane": "agent", "parent": "export"},
        {"key": "pdf", "draft": "drafts/pdf.md", "lane": "agent", "parent": "export", "after": "csv"},
        {"key": "typo", "draft": "drafts/typo.md", "lane": "fast-path",
         "labels": ["architecture"], "waive": {"L9": "the operator accepted 7 files: 5 are fixtures"}}
      ]
    }

`role` is `issue` (the default) or `parent`, a **Split parent**. An issue with `parent` is a
**Child issue**; `after` names the one earlier child of the same parent it waits on. `lane` is
`agent`, `fast-path` (`agent` + `fast-path`) or `needs-triage`. The publisher adds `number`,
`linked` and `labelled` to each entry, and `published` to the plan, as it goes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import ci, draft, lint
from .lint import Finding

LANES = ("agent", "fast-path", "needs-triage")
QUEUE_LABELS = {
    "agent", "agent-held", "agent-split", "agent-in-progress", "agent-stuck", "agent-remainder", "agent-noop",
    "needs-triage", "needs-info", "triage-in-progress", "fast-path", "merge-on-green",
}
KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
PLAN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,60}$")


@dataclass
class Plan:
    path: Path
    data: dict

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def issues(self) -> list[dict]:
        return self.data.get("issues") or []

    def by_key(self) -> dict[str, dict]:
        return {i["key"]: i for i in self.issues if isinstance(i, dict) and "key" in i}

    def draft_path(self, entry: dict) -> Path:
        return (self.dir / entry["draft"]).resolve()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n")
        tmp.replace(self.path)

    def predecessors(self, entry: dict) -> list[str]:
        after = entry.get("after")
        if after is None:
            return []
        return list(after) if isinstance(after, list) else [after]

    def ancestors(self, key: str) -> set[str]:
        by, out = self.by_key(), set()
        cur = by.get(key)
        while cur is not None:
            preds = self.predecessors(cur)
            if len(preds) != 1 or preds[0] in out:
                break
            out.add(preds[0])
            cur = by.get(preds[0])
        return out


def load(path: Path) -> Plan:
    return Plan(path.resolve(), json.loads(path.read_text()))


def check(p: Plan, routes: ci.Routes | None) -> list[Finding]:
    """Every finding for the plan: the plan's own structure, then each draft's lint, then L11.
    Waivers in the plan mark a finding waived; L6-L8 cannot be waived."""
    out: list[Finding] = []
    name = str(p.path)

    def plan_err(rule: str, msg: str, sev: str = "error") -> None:
        out.append(Finding(name, 1, rule, sev, msg))

    if not PLAN_ID.match(str(p.data.get("plan", ""))):
        plan_err("PLAN", "`plan` must be a slug of 3-61 characters: a-z, 0-9 and -")
    if not re.match(r"^[\w.-]+/[\w.-]+$", str(p.data.get("repo", ""))):
        plan_err("PLAN", "`repo` must be owner/name")
    if not p.issues:
        plan_err("PLAN", "`issues` is empty")

    seen: dict[str, dict] = {}
    children: dict[str, list[str]] = {}
    for entry in p.issues:
        if not isinstance(entry, dict) or not KEY.match(str(entry.get("key", ""))):
            plan_err("PLAN", f"every issue needs a `key` slug: {json.dumps(entry)[:80]}")
            continue
        key, role = entry["key"], entry.get("role", "issue")
        if key in seen:
            plan_err("PLAN", f"`{key}` appears twice")
        if role not in ("issue", "parent"):
            plan_err("PLAN", f"`{key}`: role must be `issue` or `parent`")
        if not entry.get("draft") or not p.draft_path(entry).is_file():
            plan_err("PLAN", f"`{key}`: no draft at {entry.get('draft')!r}")
        bad = set(entry.get("labels") or []) & QUEUE_LABELS
        if bad:
            plan_err("PLAN", f"`{key}`: {sorted(bad)} come from the lane, never from `labels`")
        for rule, reason in (entry.get("waive") or {}).items():
            if rule in lint.UNWAIVABLE or rule not in lint.SOURCES:
                plan_err("PLAN", f"`{key}`: {rule} cannot be waived")
            elif not str(reason).strip():
                plan_err("PLAN", f"`{key}`: the {rule} waiver needs a reason")
        if role == "parent":
            for f in ("lane", "after", "parent"):
                if f in entry:
                    plan_err("L8", f"`{key}` is a Split parent: it takes no `{f}`, because it is never worked")
            children.setdefault(key, [])
        else:
            if entry.get("lane") not in LANES:
                plan_err("PLAN", f"`{key}`: lane must be one of {', '.join(LANES)}")
            parent = entry.get("parent")
            preds = p.predecessors(entry)
            if parent is not None:
                if parent not in seen or seen[parent].get("role") != "parent":
                    plan_err("L8", f"`{key}`: parent `{parent}` must be a `role: parent` entry listed before it")
                else:
                    children[parent].append(key)
                if entry.get("lane") == "needs-triage":
                    plan_err("L8", f"`{key}`: a Child issue is created ready; `needs-triage` could split or promote it "
                             "before its predecessor lands. Use `agent`, or take it out of the chain")
            if len(preds) > 1:
                plan_err("L8", f"`{key}` waits on {len(preds)} issues; a Child issue names one predecessor. Merge the "
                         "predecessors, re-order them into a chain, or hold this one for the operator")
            for pred in preds[:1]:
                if parent is None:
                    plan_err("L8", f"`{key}`: `after` needs a `parent`: only a Child issue's marker holds the scheduler")
                elif pred not in seen:
                    plan_err("L8", f"`{key}`: `after: {pred}` must name an issue listed before it")
                elif seen[pred].get("parent") != parent:
                    plan_err("L8", f"`{key}`: `after: {pred}` must be a child of the same parent `{parent}`")
        seen[key] = entry
    for parent, kids in children.items():
        if not kids:
            plan_err("L8", f"Split parent `{parent}` has no children")
        elif len(kids) == 1:
            plan_err("L8", f"Split parent `{parent}` has one child; publish that child as an ordinary issue", "warning")

    drafts: dict[str, draft.Draft] = {}
    for entry in p.issues:
        if not isinstance(entry, dict) or entry.get("key") not in seen or not entry.get("draft"):
            continue
        dp = p.draft_path(entry)
        if not dp.is_file():
            continue
        d = draft.parse(dp)
        drafts[entry["key"]] = d
        found = lint.lint_draft(d, routes, lane=entry.get("lane"), role=entry.get("role", "issue"))
        out += _waive(found, entry)

    # L11: two drafts outside one chain that expect the same file will collide when the scheduler
    # runs them on two boxes at once.
    keys = [e["key"] for e in p.issues if isinstance(e, dict) and e.get("key") in drafts and e.get("role", "issue") == "issue"]
    by = p.by_key()
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a in p.ancestors(b) or b in p.ancestors(a):
                continue
            shared = drafts[a].expected_files() & drafts[b].expected_files()
            if shared:
                f = Finding(name, 1, "L11", "warning",
                            f"`{a}` and `{b}` can run in parallel and both expect {', '.join(sorted(shared))}: chain them, "
                            "merge them, or accept parallel overlap")
                waiver = (by[a].get("waive") or {}).get("L11") or (by[b].get("waive") or {}).get("L11")
                if waiver:
                    f.waived = waiver
                out.append(f)
    return out


def _waive(found: list[Finding], entry: dict) -> list[Finding]:
    waive = entry.get("waive") or {}
    for f in found:
        if f.rule in waive and f.rule not in lint.UNWAIVABLE:
            f.waived = str(waive[f.rule])
    return found


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error" and not f.waived]
