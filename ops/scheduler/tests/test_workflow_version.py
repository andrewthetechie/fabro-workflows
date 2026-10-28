"""`build_version_payload` and `clone_fabro_tree`, against real directories.

The payload is the part of the dispatch path that fails at *admission* when it is
wrong, minutes after the run was created, in a message from the server about a
path nobody typed. So the checks here are about the tree's shape rather than
about JSON: which files travel, under which keys, and what is refused outright.

No network. `clone_fabro_tree` is driven against a local repository, which is what
the `remote` parameter is for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fabro_scheduler.workflow_version import (
    BACKLOG_ENTRYPOINT,
    read_box_stages,
    MAX_FILE_BYTES,
    RALPH_ENTRYPOINT,
    VersionCache,
    WorkflowVersionError,
    build_version_payload,
    clone_fabro_tree,
)

ENTRYPOINT = "workflows/backlog/workflow.fabro"
SHARED = "workflows/_shared/review-merge/review-merge.fabro"


def test_rooted_at_fabro_includes_shared_and_has_no_parent_segments(tmp_path):
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/_shared/review-merge").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "workflows/_shared/review-merge/review-merge.fabro").write_text(
        "digraph S {}"
    )
    payload = build_version_payload(tmp_path, ENTRYPOINT)
    assert payload["entrypoint"] == ENTRYPOINT
    assert SHARED in payload["files"]
    assert not any(".." in k for k in payload["files"])
    assert payload["workflow_dependencies"] == {}


def test_every_file_travels_not_a_hardcoded_list(tmp_path):
    # A prompt added later must reach the version, or the run dies at admission on
    # an unresolved @prompts reference. The enumeration is the guard.
    (tmp_path / "workflows/backlog/prompts").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "workflows/backlog/prompts/added-later.md.j2").write_text("hello")

    payload = build_version_payload(tmp_path, ENTRYPOINT)

    assert sorted(payload["files"]) == [
        "workflows/backlog/prompts/added-later.md.j2",
        "workflows/backlog/workflow.fabro",
    ]
    assert payload["files"]["workflows/backlog/prompts/added-later.md.j2"] == "hello"


def test_an_entrypoint_that_is_not_in_the_tree_is_an_error(tmp_path):
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.toml").write_text('name = "backlog"')

    with pytest.raises(WorkflowVersionError, match="entrypoint"):
        build_version_payload(tmp_path, ENTRYPOINT)


def test_a_non_utf8_file_is_a_hard_error_not_a_skip(tmp_path):
    # The file that fails to decode is exactly the one whose absence breaks a run.
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "workflows/backlog/prompts.md.j2").write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(WorkflowVersionError, match="not valid UTF-8"):
        build_version_payload(tmp_path, ENTRYPOINT)


def test_an_oversized_file_is_refused_before_the_post(tmp_path):
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "workflows/backlog/big.md.j2").write_bytes(b"a" * (MAX_FILE_BYTES + 1))

    with pytest.raises(WorkflowVersionError, match="per-file limit"):
        build_version_payload(tmp_path, ENTRYPOINT)


def test_a_symlink_is_refused(tmp_path):
    # A link out of the tree would put a host file into a workflow version.
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "outside").write_text("not mine")
    (tmp_path / "workflows/backlog/link.md.j2").symlink_to(tmp_path / "outside")

    with pytest.raises(WorkflowVersionError, match="symlink"):
        build_version_payload(tmp_path, ENTRYPOINT)


def test_a_bad_repo_or_ref_is_refused_before_git_runs():
    for repo, ref in (("../escape", "main"), ("o/r", "--upload-pack=x"), ("o/r", "..")):
        with pytest.raises(WorkflowVersionError):
            with clone_fabro_tree(repo, ref):
                pass  # pragma: no cover - the error is raised on entry


def test_clone_fabro_tree_yields_the_fabro_directory_at_the_commit_it_cloned(
    tmp_path,
):
    origin = _origin_repo(tmp_path)
    sha = _git(tmp_path, "-C", str(origin), "rev-parse", "HEAD")

    with clone_fabro_tree("andrewthetechie/fabro-workflows", "main", remote=_url(origin)) as tree:
        assert tree.sha == sha
        assert tree.directory.name == ".fabro"
        assert (tree.directory / ENTRYPOINT).is_file()
        # The version is rooted at `.fabro/`, so nothing outside it travels: the
        # clone's `README.md` is not a file in the payload.
        payload = build_version_payload(tree.directory, ENTRYPOINT)
        assert "README.md" not in payload["files"]
        assert ENTRYPOINT in payload["files"]
        checkout = tree.directory.parent

    assert not checkout.exists()  # the temporary clone is cleaned up


def test_clone_fabro_tree_refuses_a_repo_without_a_fabro_directory(tmp_path):
    origin = tmp_path / "bare-origin"
    origin.mkdir()
    (origin / "README.md").write_text("no workflows here")
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    _git(tmp_path, "-C", str(origin), "add", ".")
    _git(
        tmp_path,
        "-C",
        str(origin),
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "one",
    )

    with pytest.raises(WorkflowVersionError, match=r"no \.fabro/ directory"):
        with clone_fabro_tree("o/r", "main", remote=_url(origin)):
            pass  # pragma: no cover - the error is raised on entry


def test_version_cache_is_keyed_by_sha_and_entrypoint():
    """One commit of `main` is two versions, one per workflow (ADR 0029 O21)."""
    cache = VersionCache()
    sha, other = "a" * 40, "b" * 40
    backlog, ralph = RALPH_ENTRYPOINT, BACKLOG_ENTRYPOINT
    assert cache.get(sha, backlog) is None
    cache.put(sha, backlog, "v1")
    cache.put(sha, ralph, "v2")
    cache.put(other, backlog, "v3")
    assert cache.get(sha, backlog) == "v1"
    assert cache.get(sha, ralph) == "v2"
    assert cache.get(other, backlog) == "v3"
    assert cache.get(other, ralph) is None


# --- helpers -------------------------------------------------------------------


def _origin_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    (origin / ".fabro/workflows/backlog").mkdir(parents=True)
    (origin / ".fabro/workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (origin / "README.md").write_text("not part of the version")
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    _git(tmp_path, "-C", str(origin), "add", ".")
    _git(
        tmp_path,
        "-C",
        str(origin),
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-qm",
        "one",
    )
    return origin


def _url(path: Path) -> str:
    # `file://`, not the bare path: git silently ignores `--depth` on a local
    # path, and the shallow clone is part of what this exercises.
    return f"file://{path}"


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


# --- the box stages the lane reads (more-local-inference C1, C7 step 6) --------------


def write_manifest(root: Path, workflow: str, manifest: dict | str) -> None:
    import json

    (root / f"workflows/{workflow}").mkdir(parents=True, exist_ok=True)
    body = manifest if isinstance(manifest, str) else json.dumps(manifest)
    (root / f"workflows/{workflow}/workflow.toml").write_text(
        "[run.environment.env]\n" + "FABRO_IO_MANIFEST = " + "'''" + body + "'''" + "\n"
    )


def test_read_box_stages_keeps_true_and_plan_arm_and_drops_false(tmp_path):
    write_manifest(tmp_path, "backlog", {
        "version": 1,
        "min_binary": "0.9.0",
        "stages": {
            "coder": {"box": True},
            "plan": {"box": "plan-arm"},
            "review": {"box": False},
            "decompose": {},
        },
    })
    assert read_box_stages(tmp_path, "backlog") == {"coder": "true", "plan": "plan-arm"}


def test_read_box_stages_reads_the_real_generated_block(tmp_path):
    """The block is JSON inside a TOML literal string, exactly as the generator writes
    it — and a mis-parse would leave the lane failing closed and starting nothing."""
    import json

    from fabro_scheduler.workflow_version import read_box_stages as read

    source = Path(__file__).resolve().parents[3] / ".fabro/workflows/_io/manifest.json"
    if not source.exists():  # pragma: no cover - the repo always has it
        pytest.skip("no source manifest")
    generated = json.loads(source.read_text())
    stages = {sid: spec.get("box", False) for sid, spec in
              generated["workflows"]["ralph"]["stages"].items()}
    write_manifest(tmp_path, "ralph", {"version": 1, "min_binary": "0.9.0", "stages": {
        sid: {"box": box} for sid, box in stages.items()
    }})
    got = read(tmp_path, "ralph")
    assert got == {sid: ("true" if box is True else "plan-arm")
                   for sid, box in stages.items() if box is not False}
    assert "iterate_t1" in got and got["plan"] == "plan-arm"


def test_read_box_stages_returns_empty_for_a_tree_or_block_it_cannot_read(tmp_path):
    assert read_box_stages(tmp_path, "backlog") == {}, "no package at all"
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.toml").write_text('name = "backlog"\n')
    assert read_box_stages(tmp_path, "backlog") == {}, "no manifest block"
    write_manifest(tmp_path, "ralph", "not json")
    assert read_box_stages(tmp_path, "ralph") == {}, "an unparsable block is not a crash"