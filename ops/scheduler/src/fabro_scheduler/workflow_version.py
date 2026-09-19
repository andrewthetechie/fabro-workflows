"""Turn a checkout of `.fabro/` into a `WorkflowVersion` payload.

`POST /workflow-versions` is how the scheduler creates a run. Automations cannot
be parameterised (overview finding 2: the endpoint takes no request body), so the
only route to a run carrying `issue_number` and `coder_pool` is the three-POST
sequence `fire-pr-review.sh` already uses: register a version, create the run,
start it. This module is the first of those three.

**The version is rooted at `.fabro/` and the entrypoint is the graph.** Both are
forced, and both are verified failures otherwise — the reasoning and the exact
422s are in `docs/scheduler/02-fix-fire-pr-review-rooting.md`:

* rooted at the package (`entrypoint = "workflow.fabro"`), the `files` map has no
  `_shared` entry, so `import="../_shared/review-merge/review-merge.fabro"` cannot
  resolve, and it cannot be added at that name either because `WorkflowPath`
  forbids parent segments;
* naming `workflow.toml` returns 422 `workflow.toml selects graph workflow.fabro,
  but the version entrypoint is workflow.toml`.

The cost is that a version carries all three packages (26 files, ~290 KB today),
not just the one being run. That is accepted: registration is content-addressed
and idempotent, so the same bytes return the same id and nothing accumulates.

`workflow_dependencies` stays `{}`. It keys **child-workflow version ids** for
`stack.child_workflow`, which no graph here uses; `import=` graphs travel in
`files`.

**Enumerate the directory, never a hardcoded file list.** A prompt added to a
package later would otherwise silently fail to register, and the run would then
die at admission on an unresolved `@prompts` reference — a failure that costs a
real run and names a file that exists on `main`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import REPO_NAME

log = logging.getLogger(__name__)

# The workflow repository is public and read anonymously, exactly as
# `fire-pr-review.sh` reads it: no credential, no token in argv, nothing to rotate.
WORKFLOWS_REPO = "andrewthetechie/fabro-workflows"
WORKFLOWS_REF = "main"

# The graph a `backlog` run runs. Relative to `.fabro/`, and the key it must
# appear under in `files`.
BACKLOG_ENTRYPOINT = "workflows/backlog/workflow.fabro"

# The server's limits, from the `WorkflowVersion` schema: at most 512 files, 512
# KiB per file, and 2 MiB of compact canonical JSON. Checked here rather than
# discovered as a 413/422 from fabro, and checked *before* the POST — the payload
# is up to 2 MiB, and a refusal that names the offending file is worth a round
# trip saved.
MAX_FILES = 512
MAX_FILE_BYTES = 512 * 1024
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024

# `WorkflowPath`: at most 240 bytes and 16 components, no empty, dot, parent,
# backslash, control, tilde-root or drive-letter segments.
MAX_PATH_BYTES = 240
MAX_PATH_COMPONENTS = 16

GIT_TIMEOUT_SECONDS = 120.0
GIT_ERROR_CHARS = 300

# A branch or tag name. The leading-character rule and the explicit leading-dash
# check in `clone_fabro_tree` are what keep a hostile value out of `git`'s argv
# rather than merely out of the URL.
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class WorkflowVersionError(RuntimeError):
    """The tree could not be fetched, read or turned into a version payload.

    Not `FabroError`: nothing has been sent to fabro when this is raised, so no
    run exists and no run was attempted.
    """


@dataclass(frozen=True)
class FabroTree:
    """A checkout of the workflow repository, at one commit.

    `directory` is the `.fabro` subdirectory — the version's root — not the
    checkout root. Nothing outside `.fabro/` is ever sent: `ops/`, `docs/` and the
    three published packages' prose are all irrelevant to a run.
    """

    directory: Path
    sha: str


def build_version_payload(fabro_dir: Path, entrypoint: str) -> dict:
    """The `WorkflowVersion` body for `fabro_dir`, rooted there.

    `entrypoint` is relative to `fabro_dir`, e.g.
    `workflows/backlog/workflow.fabro`. Every file under `fabro_dir` is read as
    UTF-8 and keyed by its POSIX relative path; the entrypoint must be one of
    them.

    Raises `WorkflowVersionError` — naming the path at fault — for a tree that
    cannot be registered: a non-UTF-8 file, a path the server's `WorkflowPath`
    rejects, or a file count or size over the documented limits. Non-UTF-8 is a
    hard error rather than a skip, because the file that fails to decode is
    exactly the one whose absence breaks a run at admission.
    """
    if not fabro_dir.is_dir():
        raise WorkflowVersionError(f"{fabro_dir}: not a directory")

    files: dict[str, str] = {}
    for relative in _relative_paths(fabro_dir):
        if len(files) >= MAX_FILES:
            raise WorkflowVersionError(
                f"{fabro_dir}: more than {MAX_FILES} files; the server accepts at most that"
            )
        files[relative] = _read_text(fabro_dir / relative, relative)

    if entrypoint not in files:
        _check_path(entrypoint)
        raise WorkflowVersionError(
            f"{fabro_dir}: entrypoint {entrypoint!r} is not in the tree"
        )

    payload = {
        "entrypoint": entrypoint,
        "files": files,
        "workflow_dependencies": {},
    }

    # The server measures the compact canonical JSON. `ensure_ascii` is left on
    # (the default) so this is the larger of the two possible encodings — a guard
    # that fires early is worth more than one that fires on the exact byte.
    size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise WorkflowVersionError(
            f"{fabro_dir}: payload is {size} bytes, over the {MAX_PAYLOAD_BYTES}-byte "
            "canonical-JSON limit"
        )

    log.info(
        "workflow-version: %d file(s), %d bytes, entrypoint=%s",
        len(files),
        size,
        entrypoint,
    )
    return payload


@contextmanager
def clone_fabro_tree(
    repo: str = WORKFLOWS_REPO,
    ref: str = WORKFLOWS_REF,
    *,
    timeout: float = GIT_TIMEOUT_SECONDS,
    remote: str | None = None,
) -> Iterator[FabroTree]:
    """Shallow-clone `repo` at `ref` and yield its `.fabro` directory.

    `fire-pr-review.sh`'s mechanism, in Python: one `git clone --depth 1`. The
    clone lands in a fresh temporary directory that is removed on the way out,
    including on an exception, so a failed dispatch leaves nothing behind.

    The commit sha is read back with `rev-parse HEAD` rather than taken from the
    caller. It is the cache key for the registered version, and the only sha that
    is safe to key on is the one the files came from.

    `remote` overrides the clone URL so a test can drive this against a local
    repository. `repo` is still validated either way: it is what the default URL
    is built from, and nothing in production passes `remote`.
    """
    if not REPO_NAME.match(repo):
        raise WorkflowVersionError(f"{repo!r} is not an owner/repo name")
    if not _REF.match(ref) or ref.startswith("-"):
        raise WorkflowVersionError(f"{ref!r} is not a usable branch or tag name")

    workdir = Path(tempfile.mkdtemp(prefix="fabro-scheduler-wf-"))
    try:
        checkout = workdir / "wf"
        _git(
            [
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--no-tags",
                "--single-branch",
                "--branch",
                ref,
                remote or f"https://github.com/{repo}.git",
                str(checkout),
            ],
            timeout=timeout,
        )
        sha = _git(["-C", str(checkout), "rev-parse", "HEAD"], timeout=timeout).strip()
        if not sha:
            raise WorkflowVersionError(f"{repo}@{ref}: could not read the checked-out commit")

        fabro_dir = checkout / ".fabro"
        if not fabro_dir.is_dir():
            raise WorkflowVersionError(f"{repo}@{ref} has no .fabro/ directory")

        yield FabroTree(fabro_dir, sha)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class VersionCache:
    """`commit sha` → `workflow_version_id`.

    Process-local and deliberately not persisted. Registration is content
    addressed and idempotent, so the only thing this buys is one POST per commit
    of `main` instead of one per dispatch; a restart that lost it would re-POST
    identical bytes and get the identical id back.

    No lock. Two dispatches racing on a cold cache both clone and both register,
    which is idempotent and harmless — unlike a lock held across a `git clone`,
    which is a way to make one slow dispatch block every other request thread.
    """

    def __init__(self) -> None:
        self._by_sha: dict[str, str] = {}

    def get(self, sha: str) -> str | None:
        return self._by_sha.get(sha)

    def put(self, sha: str, version_id: str) -> None:
        self._by_sha[sha] = version_id


def _relative_paths(root: Path) -> list[str]:
    """Every file under `root`, as a sorted POSIX relative path.

    A symlink is an error, not a file to follow: the tree is cloned from a public
    repository, and a link out of it would put a host file into a workflow
    version. `rglob` does not descend into a symlinked directory, so refusing
    them outright is also what keeps that rule explicit rather than incidental.
    """
    paths: list[str] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise WorkflowVersionError(
                f"{path.relative_to(root)}: symlinks are not allowed in a workflow version"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise WorkflowVersionError(
                f"{path.relative_to(root)}: not a regular file"
            )
        relative = path.relative_to(root).as_posix()
        _check_path(relative)
        paths.append(relative)
    return sorted(paths)


def _check_path(relative: str) -> None:
    """Enforce `WorkflowPath`'s rules on one key.

    The walk cannot produce most of these — it starts at a real directory — but a
    key is also caller-supplied when it arrives as an `entrypoint`, and the server
    rejects the whole payload for one bad key.
    """
    if relative.startswith("/") or "\\" in relative:
        raise WorkflowVersionError(f"{relative!r}: not a relative POSIX path")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise WorkflowVersionError(
            f"{relative!r}: empty, dot and parent segments are not allowed"
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in relative):
        raise WorkflowVersionError(f"{relative!r}: control characters are not allowed")
    if len(relative.encode("utf-8")) > MAX_PATH_BYTES:
        raise WorkflowVersionError(
            f"{relative!r}: over the {MAX_PATH_BYTES}-byte path limit"
        )
    if len(parts) > MAX_PATH_COMPONENTS:
        raise WorkflowVersionError(
            f"{relative!r}: over the {MAX_PATH_COMPONENTS}-component path limit"
        )


def _read_text(path: Path, relative: str) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise WorkflowVersionError(
            f"{relative}: {len(raw)} bytes, over the {MAX_FILE_BYTES}-byte per-file limit"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowVersionError(f"{relative}: not valid UTF-8: {exc}") from exc


def _git(args: list[str], *, timeout: float) -> str:
    """Run one git command and return its stdout.

    `GIT_TERMINAL_PROMPT=0` is the important part: the repository is public, so
    there is no credential to give, and without it a server that answered `401`
    would leave git waiting on a prompt nobody can see — in a container, forever.
    """
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorkflowVersionError(
            "git is not on PATH; the workflow tree cannot be fetched"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkflowVersionError(
            f"git {args[0]} timed out after {timeout:.0f}s"
        ) from exc

    if completed.returncode != 0:
        detail = " ".join(completed.stderr.split())[:GIT_ERROR_CHARS]
        raise WorkflowVersionError(
            f"git {args[0]} failed (exit {completed.returncode}): {detail or 'no output'}"
        )
    return completed.stdout
