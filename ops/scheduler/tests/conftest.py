"""Shared test fixtures.

One fixture only, and it exists because two test modules need the same thing: a
dispatch that reaches the end of the three-POST sequence without a git binary, a
network or a real fabro. `FakeCheckout.clone` is the callable `FabroClient` takes
in place of `workflow_version.clone_fabro_tree`.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from fabro_scheduler.workflow_version import FabroTree

SHA = "a" * 40


@dataclass
class FakeCheckout:
    """A minimal `.fabro` tree, plus the clone callable that yields it.

    `sha` is writable so a test can move `main` between two dispatches, which is
    the only way to tell "re-registered" from "reused the cached id".
    """

    directory: Path
    sha: str = SHA
    clones: list[tuple[str, str]] = field(default_factory=list)

    def clone(self, repo: str, ref: str):
        self.clones.append((repo, ref))
        return nullcontext(FabroTree(self.directory, self.sha))


@pytest.fixture
def fake_checkout(tmp_path: Path) -> FakeCheckout:
    root = tmp_path / "wf" / ".fabro"
    (root / "workflows/backlog").mkdir(parents=True)
    (root / "workflows/ralph").mkdir(parents=True)
    (root / "workflows/_shared/review-merge").mkdir(parents=True)
    (root / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (root / "workflows/backlog/workflow.toml").write_text('name = "backlog"\n')
    # Both entrypoints are in every checkout: a version is the whole `.fabro/`
    # tree plus one graph, so the trial's second graph travels in the same files
    # (ADR 0029 O21).
    (root / "workflows/ralph/workflow.fabro").write_text("digraph R {}")
    (root / "workflows/ralph/workflow.toml").write_text('name = "ralph"\n')
    (root / "workflows/_shared/review-merge/review-merge.fabro").write_text(
        "digraph S {}"
    )
    # Task 06's two packages. The doctor's follow-up review is a leased `pr-review` run
    # (D14), so that graph travels in the same version as the doctor's own.
    for workflow in ("pr-review", "pr-doctor"):
        (root / f"workflows/{workflow}").mkdir(parents=True)
        (root / f"workflows/{workflow}/workflow.fabro").write_text("digraph P {}")
        (root / f"workflows/{workflow}/workflow.toml").write_text("name = \"\"\n")
    return FakeCheckout(directory=root)
