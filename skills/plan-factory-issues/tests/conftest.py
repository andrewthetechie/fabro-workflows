"""Offline tests, run by `make check`: the lint and the plan checks against fixtures, and the
publisher against a fake GitHub. Nothing here touches the network or asserts SKILL.md wording.

    uv run --no-project --python 3.11 --with pytest pytest skills/plan-factory-issues/tests -q
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = SKILL / "fixtures"
REPO = FIXTURES / "repo"
ISSUES = FIXTURES / "issues"
sys.path.insert(0, str(SKILL / "scripts"))

from factory_issues import ci  # noqa: E402


@pytest.fixture(scope="session")
def routes() -> ci.Routes:
    return ci.follow(REPO)


def draft_text(criteria: list[str], title: str = "feat(x): do the thing", extra: str = "") -> str:
    crit = "\n".join(f"- [ ] {c}" for c in criteria)
    return (f"# {title}\n\nA summary.\n\n## Context\n\n- `src/a.py:1` exists.\n\n## Requirements\n\n1. A thing.\n\n"
            f"## Acceptance criteria\n\n{crit}\n\n## Non-goals\n\n- Nothing else.\n\n## Test expectations\n\n- pytest.\n{extra}")


def make_plan(tmp: Path, issues: list[dict], drafts: dict[str, str], plan_id: str = "test-plan") -> Path:
    """A plan directory under `tmp`: each key in `drafts` becomes drafts/<key>.md."""
    d = tmp / plan_id
    (d / "drafts").mkdir(parents=True)
    for key, text in drafts.items():
        (d / "drafts" / f"{key}.md").write_text(text)
    for e in issues:
        e.setdefault("draft", f"drafts/{e['key']}.md")
    path = d / "plan.json"
    path.write_text(json.dumps({"plan": plan_id, "repo": "o/r", "created": "2026-11-10T00:00:00Z", "issues": issues}))
    return path


def corpus(name: str) -> str:
    return (ISSUES / name).read_text()


def copy_repo(tmp: Path) -> Path:
    dst = tmp / "repo"
    shutil.copytree(REPO, dst)
    return dst
