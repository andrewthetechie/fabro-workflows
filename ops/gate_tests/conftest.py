"""Session setup and the `ck` fixture.

Each test is one section of named checks. `ck(name, expected, actual)` records a check and
carries on, as the shell suite did, so one report lists every failure in the section;
the test fails at teardown if any check did.

GATE_TESTS_CHECK_LOG=<file> appends one JSON line per check (section, name, expected,
actual, ok): the record the port from ops/test-task-gates.sh was verified against.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

import harness


def _repo_state() -> str:
    head = subprocess.run(["git", "-C", str(harness.REPO_ROOT), "rev-parse", "HEAD"],
                          capture_output=True, text=True, env=harness.env_for()).stdout.strip()
    status = subprocess.run(["git", "-C", str(harness.REPO_ROOT), "status", "--porcelain"],
                            capture_output=True, text=True, env=harness.env_for()).stdout
    return f"{head}|{status}"


def pytest_configure(config):
    # Every fixture builds a scratch repository and runs git in it, `push` included. Prove a
    # fresh scratch repository resolves to itself before any test runs: that catches a leaked
    # repository variable the scrub in harness.base_env does not name.
    with tempfile.TemporaryDirectory() as canary:
        subprocess.run(["git", "-C", canary, "init", "-q"], env=harness.env_for(), check=True)
        got = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=canary,
                             capture_output=True, text=True, env=harness.env_for()).stdout.strip()
        want = str(Path(canary).resolve() / ".git")
        if got != want:
            raise pytest.UsageError(
                f"git in a scratch directory resolves to {got!r}, not to the scratch repository. "
                "The fixtures would commit and push in that repository; refusing.")

    # The profile images are Debian, where /bin/sh is dash. On the Mac `sh` is bash in POSIX
    # mode, which accepts bashisms dash rejects at parse time. When dash is installed, every
    # `sh` a test runs is dash.
    shbin = Path(tempfile.mkdtemp(prefix="gate-tests-shbin-"))
    dash = harness.shutil.which("dash", path=harness.BASE_ENV["PATH"])
    if dash:
        (shbin / "sh").symlink_to(dash)
    else:
        print("WARNING: dash is not installed; the gates run under this machine's sh, "
              "which may accept bashisms")
    harness.set_shbin(shbin, Path(tempfile.mkdtemp(prefix="gate-tests-cwd-")))
    for tool in ("jq", "git", "awk", "sed", "grep"):
        harness.which(tool)

    if not hasattr(config, "workerinput"):
        config._gate_repo_state = _repo_state()


def pytest_sessionfinish(session, exitstatus):
    # A gate that runs git must run in a scratch directory, never in this checkout
    # (2026-10-01: two unwrapped runs reverted the repo's own HEAD). Only the controller
    # compares; xdist workers have no snapshot.
    before = getattr(session.config, "_gate_repo_state", None)
    if before is None or os.environ.get("GATE_TESTS_SKIP_REPO_CHECK"):
        return
    after = _repo_state()
    if after != before:
        print("\nFAIL the suite changed this checkout's HEAD or working tree")
        print(f"  before: {before.splitlines()[0] if before else ''} …")
        print(f"  after:  {after.splitlines()[0] if after else ''} …")
        session.exitstatus = 1


class Checks:
    def __init__(self, section: str):
        self.section = section
        self.failures: list[str] = []
        self.n = 0
        self.log = os.environ.get("GATE_TESTS_CHECK_LOG")

    def __call__(self, name: str, expected, actual) -> bool:
        e, a = str(expected), str(actual)
        ok = e == a
        self.n += 1
        if not ok:
            self.failures.append(f"FAIL {name}\n       expected: {e}\n       actual:   {a}")
        if self.log:
            with open(self.log, "a") as f:
                f.write(json.dumps({"section": self.section, "name": name, "expected": e,
                                    "actual": a, "ok": ok}) + "\n")
        return ok

    def fail(self, what: str, detail: str = "") -> None:
        self.failures.append(f"FAIL {what}" + ("\n" + "\n".join(
            "       " + l for l in detail.rstrip("\n").split("\n")) if detail else ""))


@pytest.fixture
def ck(request):
    c = Checks(request.node.name)
    yield c
    if c.failures:
        pytest.fail(f"{len(c.failures)} of {c.n} checks failed\n" + "\n".join(c.failures),
                    pytrace=False)
