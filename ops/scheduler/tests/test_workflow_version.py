"""`build_version_payload` and `clone_fabro_tree`, against real directories.

The payload is the part of the dispatch path that fails at *admission* when it is
wrong, minutes after the run was created, in a message from the server about a
path nobody typed. So the checks here are about the tree's shape rather than
about JSON: which files travel, under which keys, and what is refused outright.

No network. `clone_fabro_tree` is driven against a local repository, which is what
the `remote` parameter is for.
"""

from __future__ import annotations

import importlib.util
import json
import posixpath
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fabro_scheduler.dispatch import choose_workflow
from fabro_scheduler.github import Issue
from fabro_scheduler.workflow_version import (
    BACKLOG_ENTRYPOINT,
    ENTRYPOINTS,
    RALPH_ENTRYPOINT,
    RALPH_V2_ENTRYPOINT,
    read_box_stages,
    MAX_FILE_BYTES,
    VersionCache,
    WorkflowVersionError,
    build_version_payload,
    clone_fabro_tree,
    package_paths,
)

ENTRYPOINT = "workflows/backlog/workflow.fabro"
SHARED = "workflows/_shared/review-merge/review-merge.fabro"

REPO_ROOT = Path(__file__).resolve().parents[3]

# The parity numbers `choose_workflow` splits on, and the shares it accepts.
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

# `ops/check-payload.py`'s budget: the headroom a package keeps under the server's 2 MiB
# version cap, because one package over it stops every workflow's dispatch (ralph-v2 01).
PAYLOAD_BUDGET_BYTES = 1_500_000


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


def test_the_filter_keeps_the_package_and_shared_and_drops_the_rest():
    """A version is one package plus `_shared/`, never the whole `.fabro/` (C9)."""
    paths = [
        "workflows/_io/manifest.json",
        SHARED,
        "workflows/backlog/prompts/coder.md.j2",
        "workflows/backlog/workflow.fabro",
        "workflows/backlogshot/workflow.fabro",
        "workflows/ralph/workflow.fabro",
    ]

    assert package_paths(paths, ENTRYPOINT) == [
        SHARED,
        "workflows/backlog/prompts/coder.md.j2",
        "workflows/backlog/workflow.fabro",
    ]


def test_a_broken_file_in_another_package_does_not_fail_this_dispatch(tmp_path):
    # `_io/` and the other packages are not read at all, so a file that cannot decode there
    # fails that package's dispatch and not this one.
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.fabro").write_text("digraph B {}")
    (tmp_path / "workflows/ralph").mkdir(parents=True)
    (tmp_path / "workflows/ralph/prompts.md.j2").write_bytes(b"\xff\xfe\x00bad")
    (tmp_path / "workflows/_io").mkdir(parents=True)
    (tmp_path / "workflows/_io/broken.json").write_bytes(b"\xff\xfe")

    payload = build_version_payload(tmp_path, ENTRYPOINT)

    assert sorted(payload["files"]) == ["workflows/backlog/workflow.fabro"]


def test_an_entrypoint_outside_workflows_is_refused(tmp_path):
    # The filter reads the package out of the entrypoint, so the shape is checked: anything
    # but `workflows/<package>/<file>` cannot name a package to upload.
    (tmp_path / "prompts").mkdir(parents=True)
    (tmp_path / "prompts/x.md").write_text("hi")
    with pytest.raises(WorkflowVersionError, match="must be workflows/<package>/<file>"):
        build_version_payload(tmp_path, "prompts/x.md")
    for entrypoint in ("workflow.fabro", "workflows/backlog", "workflows/_shared/x/y.fabro"):
        with pytest.raises(WorkflowVersionError, match="must be workflows/<package>/<file>"):
            package_paths([], entrypoint)


def test_every_entrypoint_builds_from_this_repository_under_the_budget():
    """The real tree, as `ops/check-payload.py` measures it in `make check`."""
    fabro = REPO_ROOT / ".fabro"
    for workflow, entrypoint in sorted(ENTRYPOINTS.items()):
        payload = build_version_payload(fabro, entrypoint)
        size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        assert payload["entrypoint"] == entrypoint
        assert size <= PAYLOAD_BUDGET_BYTES, f"{workflow}: {size} bytes"


def test_every_workflow_choose_workflow_returns_has_a_real_entrypoint():
    """`dispatch` looks its workflow up in `ENTRYPOINTS`, so a rule that can answer a
    name with no row is a `KeyError` on the first such issue, in the dispatch thread.

    The table is the module's own, and the answers come from the rule itself over every
    share pair and both parities: a fake keyed by the names this test expects would pass
    while the dispatcher raised.
    """
    answers = {
        choose_workflow(
            Issue("o/a", number, "t", labels, NOW), share, v2_share
        )
        for share in (0, 50, 100)
        for v2_share in (0, 50, 100)
        for number in (8, 9)
        for labels in (frozenset({"agent"}), frozenset({"agent", "fast-path"}))
    }
    assert answers == {"backlog", "ralph", "ralph-v2"}
    assert answers <= set(ENTRYPOINTS)
    assert ENTRYPOINTS["ralph-v2"] == RALPH_V2_ENTRYPOINT


def test_read_box_stages_of_the_real_ralph_v2_package():
    """The generated manifest is what the background lane judges a v2 run by: the first
    reviewer is a box stage, so the lane may not start beside it, and its hosted twin is
    not, so the lane may (task 10)."""
    stages = read_box_stages(REPO_ROOT / ".fabro", "ralph-v2")
    assert stages.get("task_refute") == "true"
    assert "task_refute_fb" not in stages
    assert stages.get("task_review") == "true"
    assert "task_review_fb" not in stages


def test_check_payload_counts_the_bytes_the_scheduler_sends():
    """`ops/check-payload.py` is a stdlib copy of this module's payload, not an import.

    The offline gate and the dispatcher must count the same bytes for the same package
    (the package and `workflows/_shared/`, compact JSON, `ensure_ascii` on), under the
    same budget and per-file limit. A copy that drifts is a green gate over a number
    nobody sends, so the two are held together on this repository's own tree.
    """
    spec = importlib.util.spec_from_file_location(
        "check_payload", REPO_ROOT / "ops" / "check-payload.py"
    )
    check_payload = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_payload)

    assert check_payload.PAYLOAD_BUDGET_BYTES == PAYLOAD_BUDGET_BYTES
    assert check_payload.MAX_FILE_BYTES == MAX_FILE_BYTES
    fabro = REPO_ROOT / ".fabro"
    packages = sorted(path.parent.name for path in (fabro / "workflows").glob("*/workflow.fabro"))
    assert packages
    for package in packages:
        payload = build_version_payload(fabro, f"workflows/{package}/workflow.fabro")
        sent = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        measured = check_payload.payload_bytes(
            package, check_payload.package_files(fabro, package)
        )
        assert measured == sent, f"{package}: check-payload.py says {measured}, dispatch sends {sent}"


def test_every_reference_of_every_entrypoint_graph_travels_with_it():
    """Every `import=` and `prompt=@` a package's graph names is in that package's payload.

    A reference outside the package would resolve from the clone and fail at admission from
    the version, which is the failure the old whole-tree payload could not have.
    """
    fabro = REPO_ROOT / ".fabro"
    missing: list[str] = []
    references = 0
    for workflow, entrypoint in sorted(ENTRYPOINTS.items()):
        files = build_version_payload(fabro, entrypoint)["files"]
        text = (fabro / entrypoint).read_text()
        targets = re.findall(r'import="([^"]+)"', text)
        targets += re.findall(r'prompt="@([^"]+)"', text)
        for target in targets:
            references += 1
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(entrypoint), target)
            )
            if resolved not in files:
                missing.append(f"{workflow}: {entrypoint} needs {resolved}")
    assert references > 0
    assert missing == []


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
    from fabro_scheduler.workflow_version import read_box_stages as read

    source = REPO_ROOT / ".fabro/workflows/_io/manifest.json"
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
    assert "iterate_t1" in got and got["plan"] == "true", \
        "the planner is a plain box stage since the box-plan trial closed"


def test_read_box_stages_returns_empty_for_a_tree_or_block_it_cannot_read(tmp_path):
    assert read_box_stages(tmp_path, "backlog") == {}, "no package at all"
    (tmp_path / "workflows/backlog").mkdir(parents=True)
    (tmp_path / "workflows/backlog/workflow.toml").write_text('name = "backlog"\n')
    assert read_box_stages(tmp_path, "backlog") == {}, "no manifest block"
    write_manifest(tmp_path, "ralph", "not json")
    assert read_box_stages(tmp_path, "ralph") == {}, "an unparsable block is not a crash"