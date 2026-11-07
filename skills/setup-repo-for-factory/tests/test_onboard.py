"""Offline tests, run by `make check` and the pre-push hook, so a test here may never depend on which
repositories the factory holds today: onboarding one must not fail its own push.

Two kinds, kept apart. The rules run on `FIVE_REPOS`, a frozen fixture. The edits run on copies of
the real files, and assert only that each edit still applies, so a factory change this tool cannot
follow fails here rather than during someone's onboarding.

    uv run --no-project --python 3.11 --with pytest --with pyyaml pytest skills/setup-repo-for-factory/tests -q
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FACTORY = SKILL.parent.parent
sys.path.insert(0, str(SKILL / "scripts"))

from onboarding import ci, live, plan, render  # noqa: E402
from onboarding.factory import (  # noqa: E402
    ROW,
    WORKFLOWS,
    EditError,
    Factory,
    ScheduleError,
    _image_tools,
    number_word,
    word_number,
)

EXAMPLE = "o/example-repo"
EXAMPLE_SHORT = "example-repo"
FILES = (
    "ops/scheduler/repos.toml", "ops/provision-server-state.sh", "ops/fabro-branch-sweep.sh", "ops/fabro-monitor.sh",
    "ops/README.md", "ops/deploy-host.sh", "ops/scheduler/tests/test_config.py", "ops/scheduler/tests/test_app.py",
    "ops/scheduler/tests/test_reconcile.py",
)

# (workflow, schedule id, the five repositories' expressions in order, what follows the expression),
# as humans wrote them up to 2026-10-23. The last column of each is flakeradar's.
FIVE_REPOS = (
    ("backlog", "every-15m", ("2-59/15 * * * *", "5-59/15 * * * *", "8-59/15 * * * *", "11-59/15 * * * *", "14-59/15 * * * *"), ""),
    ("issue-triage", "hourly", ("30 * * * *", "35 * * * *", "40 * * * *", "45 * * * *", "50 * * * *"), ""),
    ("arch-review", "twice-weekly", ("0 4 * * 1,4", "0 4 * * 2,5", "0 4 * * 3,6", "0 5 * * 0,3", "0 5 * * 1,4"), " true true"),
    ("dependabot", "daily", ("0 5 * * *", "20 5 * * *", "40 5 * * *", "0 6 * * *", "20 6 * * *"), " true true"),
    ("merge-queue", "sweep", ("0-59/20 * * * *", "2-59/20 * * * *", "5-59/20 * * * *", "7-59/20 * * * *", "9-59/20 * * * *"), " true"),
    ("scout", "daily", ("0 2 * * *", "15 2 * * *", "30 2 * * *", "45 2 * * *", "0 3 * * *"), ""),
)
ROW_END = " || FAILED=$((FAILED+1))"


def provision_row(workflow: str, n: int, schedule_id: str, expr: str, tail: str) -> str:
    return f'provision_automation {workflow}-r{n} python o/r{n} {workflow} {schedule_id} "{expr}"{tail}{ROW_END}'


def frozen_factory(root: Path, repos: int) -> Factory:
    """A factory whose only rows are the first `repos` of `FIVE_REPOS`."""
    lines = [f'provision_automation pr-review-r{n} python o/r{n} pr-review "" "" true{ROW_END}' for n in range(repos)]
    for workflow, schedule_id, exprs, tail in FIVE_REPOS:
        lines += [provision_row(workflow, n, schedule_id, exprs[n], tail) for n in range(repos)]
    path = root / "ops" / "provision-server-state.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return Factory(root)


@pytest.fixture
def factory(tmp_path: Path) -> Factory:
    """Copies of the real files. Scout's window before 04:00 is finite, so its rows are thinned here:
    a full window is a `ScheduleError` for the next onboarding, and must not fail every later push."""
    for relative in FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FACTORY / relative, target)
    shutil.copytree(FACTORY / "ops" / "profile-images", tmp_path / "ops" / "profile-images")
    provision = tmp_path / "ops" / "provision-server-state.sh"
    scouts = [m.group(0) for m in ROW.finditer(provision.read_text()) if m.group("workflow") == "scout"]
    text = provision.read_text()
    for row in scouts[1:]:
        text = text.replace(row + "\n", "")
    provision.write_text(text)
    return Factory(tmp_path)


# --- schedules: the staggering rules, on the frozen fixture ---------------------------------------


def test_the_rules_reproduce_the_rows_humans_wrote(tmp_path: Path) -> None:
    rows = frozen_factory(tmp_path, repos=4).new_rows("o/r4", "python")
    assert rows["pr-review"] == f'provision_automation pr-review-r4 python o/r4 pr-review "" "" true{ROW_END}'
    for workflow, schedule_id, exprs, tail in FIVE_REPOS:
        assert rows[workflow] == provision_row(workflow, 4, schedule_id, exprs[4], tail)
    assert set(rows) == set(WORKFLOWS)


def test_the_next_repository_gets_free_slots(tmp_path: Path) -> None:
    rows = frozen_factory(tmp_path, repos=5).new_rows("o/r5", "python")
    assert rows["backlog"].endswith(f'every-15m "3-59/15 * * * *"{ROW_END}')  # 17 % 15 is the first row's 2
    assert '"55 * * * *"' in rows["issue-triage"]
    assert '"0 5 * * 2,5" true true' in rows["arch-review"]
    assert '"40 6 * * *" true true' in rows["dependabot"]
    assert '"11-59/20 * * * *" true' in rows["merge-queue"]
    assert '"15 3 * * *"' in rows["scout"]


def test_scout_refuses_to_cross_into_arch_reviews_hour(tmp_path: Path) -> None:
    factory = frozen_factory(tmp_path, repos=5)
    path = factory.root / "ops" / "provision-server-state.sh"
    path.write_text(path.read_text() + f'provision_automation scout-x python o/x scout daily "45 3 * * *"{ROW_END}\n')
    with pytest.raises(ScheduleError):
        factory.schedule_for("scout")


# --- registration: each edit applied to the real files ---------------------------------------------


def test_registration_adds_a_parseable_repository_everywhere(factory: Factory) -> None:
    readme_before = (factory.root / "ops/README.md").read_text()
    count = word_number(re.search(r"Recreates the ([a-z-]+) \*\*automations\*\*", readme_before).group(1))
    rows_before = len(factory.rows())
    edits = factory.registration_edits(EXAMPLE, "python", 50, ["exmpl"])
    for relative, new in edits.items():
        (factory.root / relative).write_text(new)

    row = next(r for r in factory.repos() if r["name"] == EXAMPLE)
    assert row == {"name": EXAMPLE, "priority": 50, "environment_id": "python", "enabled": True, "aliases": ["exmpl"]}
    assert factory.registered(EXAMPLE) == {"repos_toml": True, "rows": True}
    assert len(factory.rows()) == rows_before + len(WORKFLOWS)
    assert subprocess.run(["sh", "-n", str(factory.root / "ops/provision-server-state.sh")]).returncode == 0
    for script in ("ops/fabro-branch-sweep.sh", "ops/fabro-monitor.sh"):
        assert re.search(rf'^REPOS=".*{EXAMPLE}\}}"$', (factory.root / script).read_text(), re.M)
    readme = (factory.root / "ops/README.md").read_text()
    assert f"Recreates the {number_word(count + len(WORKFLOWS))} **automations**" in readme
    assert re.search(rf"^\| `python` \|.*, {EXAMPLE_SHORT} \|$", readme, re.M)


def test_tests_list_repositories_in_priority_order(factory: Factory) -> None:
    first = min(factory.repos(), key=lambda r: (r["priority"], r["name"]))["name"]
    edits = factory.registration_edits(EXAMPLE, "python", first_priority(factory) - 1, [])
    app = edits["ops/scheduler/tests/test_app.py"]
    assert app.index(EXAMPLE) < app.index(first)
    assert f'"{EXAMPLE}",  # priority {first_priority(factory) - 1}' in edits["ops/scheduler/tests/test_config.py"]
    assert f'("{EXAMPLE}", "python"),' in edits["ops/scheduler/tests/test_config.py"]
    assert f'"{EXAMPLE_SHORT}")' in edits["ops/scheduler/tests/test_reconcile.py"]


def first_priority(factory: Factory) -> int:
    return min(r["priority"] for r in factory.repos())


def test_only_the_tracked_file_assertion_changes_in_test_config(factory: Factory) -> None:
    """test_config.py has another `ordered_repos` assertion over its own fixture. Editing that one
    instead overwrote it, in the first end-to-end run of this tool."""
    before = (factory.root / "ops/scheduler/tests/test_config.py").read_text()
    after = factory.registration_edits(EXAMPLE, "python", 50, [])["ops/scheduler/tests/test_config.py"]
    other = [line for line in before.splitlines() if re.fullmatch(r'\s+"o/\w+",', line)]
    assert other, "the other assertion's fixture has moved; check the anchor in _test_config"
    assert [line for line in after.splitlines() if re.fullmatch(r'\s+"o/\w+",', line)] == other


def test_an_edit_whose_anchor_moved_fails_loudly(factory: Factory) -> None:
    path = factory.root / "ops/fabro-monitor.sh"
    path.write_text(path.read_text().replace("FABRO_MONITOR_REPOS", "SOMETHING_ELSE"))
    with pytest.raises(EditError):
        factory.registration_edits(EXAMPLE, "python", 50, [])


def test_a_registered_repository_is_refused(factory: Factory) -> None:
    with pytest.raises(EditError):
        factory.registration_edits(factory.repos()[0]["name"], "python", 50, [])


def test_number_words_round_trip() -> None:
    assert number_word(35) == "thirty-five"
    assert all(word_number(number_word(n)) == n for n in range(100))


# --- facts the tool reads from the factory ----------------------------------------------------------


def test_every_real_dockerfile_yields_a_stack() -> None:
    stacks = Factory(FACTORY).stacks()
    assert stacks
    for stack in stacks:
        assert stack.image == f"fabro-{stack.id}:local"
        assert stack.warm_repo, f"{stack.id} is not in build-images.sh's PROFILES"
        assert stack.tools, f"no toolchain read from Dockerfile.{stack.id}"


def test_image_tools_prefers_a_pinned_arg_over_the_base_image() -> None:
    tools = _image_tools("FROM rust:1.98.1-trixie AS warm\nFROM rust:1.98.1-trixie\nARG RUST_VERSION=1.99.0\nARG NODE_VERSION=22.23.2\nARG PG_MAJOR=17\n")
    assert tools == {"rust": "1.99.0", "node": "22.23.2", "postgres": "17"}


def test_server_defaults_come_from_the_deploy_script() -> None:
    server = Factory(FACTORY).server_defaults()
    assert "@" in server["host"]
    for url in (server["api_url"], server["scheduler_url"]):
        assert url.startswith(f"http://{server['host'].split('@')[-1]}:")
    assert server["api_url"].endswith("/api/v1")


def test_every_fixed_label_the_factory_uses_is_in_the_catalog() -> None:
    names = {label["name"] for label in live.label_catalog()}
    used: set[str] = set()
    for path in (FACTORY / ".fabro" / "workflows").rglob("*.fabro"):
        used |= set(re.findall(r"gh label create ([A-Za-z][\w-]*)", path.read_text()))
    github_py = (FACTORY / "ops/scheduler/src/fabro_scheduler/github.py").read_text()
    used |= set(re.findall(r'^[A-Z_]+_LABEL = "([\w-]+)"', github_py, re.M))
    precheck = (FACTORY / "ops/scheduler/src/fabro_scheduler/precheck.py").read_text()
    verdicts = re.search(r"HOLD_VERDICTS = \(([^)]+)\)", precheck).group(1)
    used |= {f"precheck-{v.replace('_', '-')}" for v in re.findall(r'"(\w+)"', verdicts)}
    assert sorted(used - names) == []


def test_the_catalog_colors_are_valid_for_github() -> None:
    assert all(re.fullmatch(r"[0-9A-Fa-f]{6}", label["color"]) for label in live.label_catalog())


# --- reading CI ------------------------------------------------------------------------------------


WORKFLOW = """\
name: CI
on:
  pull_request:
env:
  TOP: "1"
jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    env:
      DATABASE_URL: postgresql://app:app@localhost:5432/appdb
      API_KEY: ${{ secrets.API_KEY }}
    services:
      db:
        image: postgres:17
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Install
        run: uv sync --frozen
      - name: Lint
        run: uv run ruff check .
      - name: Unit
        run: uv run pytest
      - name: Mystery
        run: ./scripts/do-thing
      - uses: some/check-action@v1
      - name: Summary
        run: echo "$RESULT" >> $GITHUB_STEP_SUMMARY
      - name: Pinned
        run: ./scripts/check.sh ${{ github.sha }}
  frontend:
    runs-on: ubuntu-latest
    steps:
      - run: npm ci
        working-directory: frontend
      - run: npm test
        working-directory: frontend
      - run: uv run ruff check .
        working-directory: backend
  mac:
    runs-on: macos-latest
    steps:
      - run: npm test
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_text(WORKFLOW)
    (directory / "deploy.yml").write_text("on:\n  push:\njobs:\n  a:\n    runs-on: x\n    steps:\n      - run: make deploy\n")
    return tmp_path


def roles(facts: ci.CiFacts) -> dict[str, str]:
    return {s.source.split(" > ")[-1]: s.role for s in facts.steps}


def test_only_pull_request_workflows_count(repo: Path) -> None:
    facts = ci.read_ci(repo)
    assert facts.workflows == ["ci.yml"]
    assert facts.excluded == ["deploy.yml"]


def test_each_step_gets_a_suggested_role(repo: Path) -> None:
    facts = ci.read_ci(repo)
    backend = {s.source.split(" > ")[-1]: s for s in facts.steps if "> backend >" in s.source}
    assert backend["Install"].role == "setup"
    assert backend["Lint"].role == "gate"
    assert backend["Unit"].role == "gate"
    assert backend["Mystery"].role == "review"
    assert backend["some/check-action@v1"].role == "review"
    assert backend["Summary"].role == "skip"  # only feeds GitHub's step summary
    assert backend["Pinned"].role == "review"  # a GitHub context the sandbox lacks
    assert backend["actions/checkout@v4"].role == "skip"
    assert backend["Lint"].cwd == "backend"


def test_a_repeated_command_is_gated_once(repo: Path) -> None:
    facts = ci.read_ci(repo)
    second = next(s for s in facts.steps if "> frontend >" in s.source and "ruff" in s.run)
    assert second.role == "skip"
    assert second.reason.startswith("duplicate of ci.backend")


def test_a_job_on_another_os_is_skipped_with_its_reason(repo: Path) -> None:
    mac = next(s for s in ci.read_ci(repo).steps if "> mac >" in s.source)
    assert mac.role == "skip"
    assert "macos-latest" in mac.reason


def test_toolchains_and_services_are_read(repo: Path) -> None:
    facts = ci.read_ci(repo)
    assert facts.toolchains["python"] == {"3.13"}
    assert facts.services == ["postgres:17"]


def test_a_local_composite_action_is_expanded(repo: Path) -> None:
    action = repo / ".github" / "actions" / "tests"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "inputs:\n  dir:\n    default: api\nruns:\n  using: composite\n  steps:\n"
        "    - run: cargo test --manifest-path ${{ inputs.dir }}/Cargo.toml\n      shell: bash\n"
    )
    (repo / ".github" / "workflows" / "x.yml").write_text(
        "on: pull_request\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/tests\n"
    )
    step = next(s for s in ci.read_ci(repo).steps if s.id.startswith("x.j.0"))
    assert step.run == "cargo test --manifest-path api/Cargo.toml"
    assert step.role == "gate"


def test_the_environment_splits_three_ways(repo: Path) -> None:
    draft = draft_plan(repo)
    plan.settle_env(draft)
    assert draft["env"]["DATABASE_URL"] == "postgresql://test:test@localhost:5432/test"  # fabro-pg-ensure's credentials
    assert draft["env"]["TOP"] == "1"
    assert [r["key"] for r in draft["env_review"]] == ["API_KEY"]


def test_a_skipped_jobs_variables_stay_out_of_the_sandbox(repo: Path) -> None:
    draft = draft_plan(repo)
    for step in draft["step"]:
        if "> backend >" in step["source"]:
            step["role"], step["reason"] = "skip", "not gated"
    plan.settle_env(draft)
    assert "DATABASE_URL" not in draft["env"]
    assert draft["env_review"] == []


def test_a_value_that_differs_between_steps_becomes_an_export() -> None:
    rows = [
        {"role": "gate", "ci_env": {"MODE": "a"}},
        {"role": "gate", "ci_env": {"MODE": "b"}},
    ]
    shared, _ = ci.draft_env(rows, [])
    assert shared == {}
    assert [r["env"] for r in rows] == [{"MODE": "a"}, {"MODE": "b"}]


def test_an_env_decision_survives_a_recompute(repo: Path) -> None:
    draft = draft_plan(repo)
    plan.settle_env(draft)
    draft["env_review"][0].update(action="set", value="test-key")
    plan.settle_env(draft)
    assert draft["env_review"][0]["action"] == "set"
    assert plan.effective_env(draft)["API_KEY"] == "test-key"


@pytest.mark.parametrize(
    ("name", "run", "role"),
    [
        ("Compute key", 'echo "k=v" >> "$GITHUB_OUTPUT"', "skip"),
        ("POST junit results to FlakeRadar", "curl -X POST $FLKR_URL", "skip"),
        ("Toolchain", "rustup toolchain install 1.90", "skip"),
        ("Tokens", "bash scripts/check_brand_tokens.sh", "gate"),
        ("Browsers", "npx playwright install --with-deps chromium", "review"),
        ("Both", "npm ci && npm test", "review"),
        ("Packages", "sudo apt-get install -y foo", "review"),
        ("Global npm", "npm install -g npm@11.19.1", "review"),
        ("Compose", "docker compose up -d", "review"),
    ],
)
def test_real_ci_shapes_get_the_right_suggestion(name: str, run: str, role: str) -> None:
    step = ci.Step(id="x", source="x", run=run)
    ci._classify(step, "job", name, "", "")
    assert step.role == role


# --- the plan, then the contract --------------------------------------------------------------------


def draft_plan(repo: Path) -> dict:
    facts = ci.read_ci(repo)
    return plan.draft(
        repo="o/n", stack="python-node", priority=5, aliases=[], deploy="stage",
        server={"host": "h", "api_url": "a", "scheduler_url": "s"}, ci=facts, needs_postgres=True,
    )


def resolve(draft: dict) -> dict:
    for step in draft["step"]:
        if step["role"] == "review":
            step["role"], step["reason"] = "skip", "not a check"
    plan.settle_env(draft)
    for row in draft["env_review"]:
        row["action"], row["value"] = "set", "test-key"
    gates = [s for s in draft["step"] if s["role"] == "gate"]
    from types import SimpleNamespace

    views = [SimpleNamespace(run=s["run"], cwd=s["cwd"]) for s in gates]
    draft["target"] = render.draft_targets(views)
    draft["fix"] = render.draft_fix(Path("/nonexistent"), views)
    return draft


def test_a_plan_with_review_rows_is_not_ready(repo: Path) -> None:
    draft = draft_plan(repo)
    assert any("Mystery" in p for p in plan.problems(draft))
    plan.settle_env(draft)
    assert any("API_KEY" in p for p in plan.problems(draft))


def test_a_skip_of_a_suggested_gate_needs_its_reason(repo: Path) -> None:
    draft = resolve(draft_plan(repo))
    lint = next(s for s in draft["step"] if s["run"] == "uv run ruff check ." and s["role"] == "gate")
    lint["role"] = "skip"
    assert any("needs `reason`" in p for p in plan.problems(draft))


def test_an_invented_command_needs_its_reason(repo: Path) -> None:
    draft = resolve(draft_plan(repo))
    next(s for s in draft["step"] if s["role"] == "gate")["run"] = "make everything-green"
    assert any("edit_reason" in p for p in plan.problems(draft))


def test_a_new_ci_step_reopens_the_plan(repo: Path) -> None:
    draft = resolve(draft_plan(repo))
    assert plan.problems(draft) == []
    workflow = repo / ".github" / "workflows" / "ci.yml"
    workflow.write_text(workflow.read_text().replace("      - name: Mystery", "      - run: npm run lint\n      - name: Mystery"))
    assert any("new in the repository's CI" in p for p in plan.problems(draft, ci.read_ci(repo)))


def test_the_plan_round_trips_through_its_file(repo: Path, tmp_path: Path) -> None:
    path = tmp_path / "plan.toml"
    draft = resolve(draft_plan(repo))
    plan.write(path, draft, header=plan.HEADER)
    assert plan.load(path)["step"] == draft["step"]
    assert plan.load(path)["env"] == draft["env"]


def test_rendering_is_deterministic_and_shaped(repo: Path) -> None:
    draft = resolve(draft_plan(repo))
    files = render.render_all(draft)
    assert files == render.render_all(draft)
    ci_sh = files["ci.sh"]
    assert ci_sh.startswith("#!/usr/bin/env bash\n")
    assert 'test_env=$(fabro-io test-env); eval "$test_env"' in ci_sh
    assert "(cd backend && uv run pytest)" in ci_sh
    assert "ci.yml > backend > Mystery: not a check" in ci_sh  # the gap is stated where the Refuter reads
    assert "uv sync --frozen" in files["setup.sh"] and "uv sync" not in ci_sh
    assert "@@" not in "".join(files.values())


def test_rendered_shell_passes_bash_n(repo: Path, tmp_path: Path) -> None:
    for name, content in render.render_all(resolve(draft_plan(repo))).items():
        if name.endswith(".sh"):
            path = tmp_path / name
            path.write_text(content)
            assert subprocess.run(["bash", "-n", str(path)]).returncode == 0, name


def test_test_toml_carries_the_plans_environment(repo: Path) -> None:
    parsed = tomllib.loads(render.render_all(resolve(draft_plan(repo)))["test.toml"])
    assert parsed["version"] == 1
    assert parsed["env"]["DATABASE_URL"] == "postgresql://test:test@localhost:5432/test"
    assert parsed["env"]["API_KEY"] == "test-key"
    assert parsed["prepare"]["run"] == ["fabro-pg-ensure"]
    assert parsed["targets"]["backend"]["run"] == "uv run pytest"
    assert parsed["targets"]["frontend"]["run"] == "npm test --"


def test_generated_test_toml_passes_the_real_parser(repo: Path, tmp_path: Path) -> None:
    cargo = shutil.which("cargo")
    binary = Path.home() / ".cache" / "fabro-onboard" / "target" / "debug" / "fabro-io"
    if not cargo or not binary.exists():
        pytest.skip("run `onboard.py check` once to build fabro-io")
    files = render.render_all(resolve(draft_plan(repo)))
    (tmp_path / ".fabro").mkdir()
    (tmp_path / ".fabro" / "test.toml").write_text(files["test.toml"])
    (tmp_path / ".fabro" / "scout.toml").write_text(files["scout.toml"])
    assert subprocess.run([binary, "test-env", "--check", "--root", tmp_path], capture_output=True).returncode == 0
    report = subprocess.run([binary, "scout", "config", "--file", tmp_path / ".fabro/scout.toml"], capture_output=True, text=True)
    assert '"valid":true' in report.stdout


def test_setup_keeps_cargo_install_idempotent() -> None:
    step = {"run": "cargo install cargo-audit", "cwd": ".", "source": "x", "env": {}}
    assert render._idempotent(step)["run"] == "command -v cargo-audit >/dev/null 2>&1 || cargo install cargo-audit"


def test_a_multiline_step_keeps_its_lines_exact() -> None:
    block = render._block({"run": "cat <<EOF\nhello\nEOF", "cwd": "a b", "source": "x", "env": {"K": "v w"}})
    assert block == "# x\n(\ncd 'a b'\nexport K='v w'\ncat <<EOF\nhello\nEOF\n)"


@pytest.mark.parametrize(
    ("gate", "fix"),
    [
        ("uv run ruff check .", "uv run ruff check --fix ."),
        ("uv run ruff format --check .", "uv run ruff format ."),
        ("cargo fmt --all -- --check", "cargo fmt --all"),
        ("cargo clippy --all-targets -- -D warnings", "cargo clippy --fix --allow-dirty --allow-staged --all-targets"),
        ("npx prettier --check src", "npx prettier --write src"),
        ("npx eslint .", "npx eslint --fix ."),
    ],
)
def test_fix_steps_derive_from_gates(gate: str, fix: str) -> None:
    from types import SimpleNamespace

    assert render.draft_fix(Path("/nonexistent"), [SimpleNamespace(run=gate, cwd=".")]) == [{"cwd": ".", "run": fix}]


def test_a_package_script_is_never_guessed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    (tmp_path / "package.json").write_text('{"scripts": {"format:check": "x", "format": "y", "lint": "tsc"}}')
    gates = [SimpleNamespace(run="npm run format:check", cwd="."), SimpleNamespace(run="npm run lint", cwd=".")]
    assert render.draft_fix(tmp_path, gates) == [{"cwd": ".", "run": "npm run format"}]


def test_a_short_alias_is_refused() -> None:
    errors = plan.validate_choices(stack="python", known_stacks=["python"], aliases=["js"], used_aliases=set())
    assert any("too short" in e for e in errors)
