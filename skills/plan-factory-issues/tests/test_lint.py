"""The lint: the shared corpus, one positive and one negative case per rule, and ci.sh routes."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from conftest import ISSUES, draft_text

from factory_issues import ci, draft, lint


def run(text: str, routes, **kw) -> list[lint.Finding]:
    return lint.lint_draft(draft.parse(Path("d.md"), text), routes, **kw)


def rules(findings, severity="error") -> set[str]:
    return {f.rule for f in findings if f.severity == severity}


# --- the shared corpus (#68 reads the same files) --------------------------------------------------

EXPECTED = tomllib.loads((ISSUES / "EXPECTED.toml").read_text())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_corpus(name, routes):
    found = lint.lint_draft(draft.parse(ISSUES / name), routes)
    assert rules(found) == set(EXPECTED[name]["errors"]), [str(f) for f in found]
    assert rules(found, "warning") == set(EXPECTED[name]["warnings"]), [str(f) for f in found]


def test_every_fixture_has_an_expectation():
    assert {p.name for p in ISSUES.glob("*.md")} == set(EXPECTED)


def test_every_rule_has_a_bad_fixture():
    covered = {r for v in EXPECTED.values() for r in v["errors"] + v["warnings"]}
    assert covered >= {"L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"}


# --- per rule: (criterion, rule it must raise) and (criterion, rule it must not raise) --------------

POSITIVE = [
    ("The PR description lists before/after coverage.", "L1"),
    ("Every branch is covered, or the PR explains why it was left.", "L1"),
    ("Bundle size delta reported in the PR.", "L1"),
    ("`cargo test` passes.", "L2"),
    ("`npm run format:check` passes.", "L2"),
    ("The `e2e` CI job passes on the PR.", "L2"),
    ("All required checks are green.", "L2"),
    ("Gold-set Hit@5 does not drop.", "L3"),
    ("Line coverage must not drop below the current value.", "L3"),
    ("`.github/workflows/release.yml` has a `macos` job.", "L4"),
    ("Manual check: the dialog opens.", "L5"),
    ("A screenshot of the page is attached.", "L5"),
    ("Underlines render on macOS and Linux.", "L5"),
    ("The banner no longer appears on the deployed site.", "L5"),
    ("Running the sync on staging changes no totals.", "L5"),
]
NEGATIVE = [
    ("A code comment at the guard says why it is unreachable.", "L1"),
    ("The PR branch name is irrelevant here; the function returns 3.", "L1"),
    ("Rather than changing behaviour in this PR, a test pins it.", "L1"),
    ("`uv run pytest -q tests/` passes (`.fabro/ci.sh:7`).", "L2"),
    ("`npm run lint` passes (`.fabro/ci.sh:12`).", "L2"),
    ("`eslint src` passes via `frontend/package.json` scripts.lint.", "L2"),
    ("`mypy src` passes (`Makefile:4`).", "L2"),
    ("`alembic check` passes (`scripts/check-migrations.sh`, `.fabro/ci.sh:8`).", "L2"),
    ("`rg -n stillOpen src/` finds nothing, and the suite passes.", "L2"),
    ("`now = 2026-12-04T11:59:59Z` returns 0 and the test passes.", "L2"),
    ("The doc links to the CI runs it is based on.", "L2"),
    ("`fail_under` in `pyproject.toml` is raised to 87, rounded down.", "L3"),
    ("The login flow returns 200 for a valid token.", "L3"),
    ("Nothing under `.github/workflows/` changes.", "L4"),
    ("`actionlint .github/workflows/release.yml` passes (`.fabro/ci.sh:6`).", "L4"),
    ("A manual retry resets `retry_count` to 0.", "L5"),
    ("The manual snapshot button writes the file.", "L5"),
    ("No production code outside `src/jobs/` changes.", "L5"),
    ("`admin.py` no longer compares ids by hand.", "L5"),
]


@pytest.mark.parametrize("criterion,rule", POSITIVE)
def test_rule_fires(criterion, rule, routes):
    assert rule in rules(run(draft_text([criterion]), routes))


@pytest.mark.parametrize("criterion,rule", NEGATIVE)
def test_rule_stays_quiet(criterion, rule, routes):
    found = run(draft_text([criterion]), routes)
    assert rule not in rules(found) | rules(found, "warning"), [str(f) for f in found]


def test_l2_uncited_route_is_a_warning(routes):
    found = run(draft_text(["`uv run pytest -q tests/` passes."]), routes)
    assert rules(found) == set()
    assert "L2" in rules(found, "warning")
    assert ".fabro/ci.sh:7" in next(f.message for f in found if f.rule == "L2")


def test_l2_names_the_closest_command(routes):
    found = run(draft_text(["`npm run format:check` passes."]), routes)
    msg = next(f.message for f in found if f.rule == "L2")
    assert "closest" in msg


def test_l2_partial_route_is_a_warning(tmp_path):
    (tmp_path / ".fabro").mkdir()
    (tmp_path / ".fabro/ci.sh").write_text("#!/usr/bin/env bash\njust ci\n")
    found = run(draft_text(["`cargo test` passes."]), ci.follow(tmp_path))
    assert "L2" not in rules(found)
    assert "L2" in rules(found, "warning")


def test_l4_expected_files_always_count(routes):
    found = run(draft_text(["The job builds."], extra="\n## Expected files\n\n- `.github/workflows/ci.yml`\n"), routes)
    assert "L4" in rules(found)


def test_l4_human_actions_are_fine(routes):
    found = run(draft_text(["The job builds."], extra="\n## Human actions\n\n- Add `.github/workflows/ci.yml`.\n"), routes)
    assert "L4" not in rules(found)


def test_l6_post_deploy_heading_and_items(routes):
    found = run(draft_text(["The page returns 200."], extra="\n### Post-deploy checks\n\n- [ ] Looks right.\n"), routes)
    assert any(f.rule == "L6" and "exactly" in f.message for f in found)
    found = run(draft_text(["The page returns 200."], extra="\n## Post-deploy checks\n\n* Looks right.\n"), routes)
    assert any(f.rule == "L6" and "- [ ]" in f.message for f in found)
    found = run(draft_text(["The page returns 200."], extra="\n## Post-deploy checks\n\n- [ ] Looks right.\n"), routes)
    assert "L6" not in rules(found)


def test_l6_duplicate_heading(routes):
    found = run(draft_text(["The page returns 200."], extra="\n## Non-goals\n\n- Again.\n"), routes)
    assert any(f.rule == "L6" and "twice" in f.message for f in found)


@pytest.mark.parametrize("title,ok", [
    ("feat(invoices): export CSV", True), ("fix: a thing", True), ("refactor(a/b)!: drop x", True),
    ("style(ui): format", False), ("Add CSV export", False), ("feat(Invoices): x", False), ("feat:x", False),
])
def test_l7_title(title, ok, routes):
    assert ("L7" not in rules(run(draft_text(["The page returns 200."], title=title), routes))) is ok


def test_l8_marker_and_free_form_block(routes):
    found = run(draft_text(["The page returns 200."], extra="\n<!-- fabro:split-child parent=1 after=0 -->\n"), routes)
    assert "L8" in rules(found)
    found = run(draft_text(["The page returns 200."], extra="\nBlocked by #12.\n"), routes)
    assert "L8" in rules(found)
    found = run(draft_text(["The page returns 200."], extra="\nAdded after #12 merged.\n"), routes)
    assert "L8" not in rules(found)


def test_l9_thresholds(routes):
    steps = "".join(f"{i}. Step {i}.\n" for i in range(1, lint.MAX_TASK_MAP_STEPS + 1))
    assert "L9" not in rules(run(draft_text(["Returns 200."], extra=f"\n## Task map\n\n{steps}"), routes), "warning")
    steps += f"{lint.MAX_TASK_MAP_STEPS + 1}. One more.\n"
    assert "L9" in rules(run(draft_text(["Returns 200."], extra=f"\n## Task map\n\n{steps}"), routes), "warning")
    files = "".join(f"- `src/f{i}.py`\n" for i in range(lint.MAX_EXPECTED_FILES + 1))
    assert "L9" in rules(run(draft_text(["Returns 200."], extra=f"\n## Expected files\n\n{files}"), routes), "warning")


def test_l10_fast_path(routes):
    text = draft_text(["Returns 200."], extra="\n## Task map\n\n1. One.\n2. Two.\n")
    assert "L10" in rules(run(text, routes, lane="fast-path"), "warning")
    assert "L10" not in rules(run(text, routes, lane="agent"), "warning")


def test_parent_role_needs_only_title_and_summary(routes):
    assert rules(run("# feat(x): a feature\n\nWhat it delivers.\n", routes, role="parent")) == set()


# --- ci.sh routes ------------------------------------------------------------------------------------


@pytest.mark.parametrize("command,status,where", [
    ("uv run ruff check .", "found", ".fabro/ci.sh:6"),  # direct, other arguments
    ("pytest", "found", ".fabro/ci.sh:7"),
    ("alembic check", "found", "scripts/check-migrations.sh:3"),  # through a shell script
    ("mypy src", "found", "Makefile:4"),  # through a Makefile prerequisite
    ("make typecheck", "found", ".fabro/ci.sh:9"),
    ("npx eslint src", "found", "frontend/package.json scripts.lint"),  # through a package.json script
    ("pnpm lint", "found", ".fabro/ci.sh:12"),  # another runner, the same script
    ("npm test", "found", ".fabro/ci.sh:13"),
    ("npx vitest run", "found", "frontend/package.json scripts.test"),
    ("npm run format:check", "none", None),  # defined, never called
    ("cargo test", "none", None),
    ("tsc --noEmit", "none", None),
])
def test_routes(command, status, where, routes):
    m = ci.find(routes, command)
    assert m.status == status
    if where:
        assert m.via is not None and m.via.where == where


def test_route_through_cd_and_subshell(tmp_path):
    (tmp_path / ".fabro").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "web/package.json").write_text('{"scripts": {"check": "tsc --noEmit"}}')
    (tmp_path / ".fabro/ci.sh").write_text("#!/usr/bin/env bash\n(cd web && npm run check)\n")
    r = ci.follow(tmp_path)
    assert ci.find(r, "tsc --noEmit").status == "found"
    assert not r.unresolved


def test_route_through_precommit(tmp_path):
    (tmp_path / ".fabro").mkdir()
    (tmp_path / ".fabro/ci.sh").write_text("#!/usr/bin/env bash\nuv run pre-commit run --all-files\n")
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: local\n    hooks:\n      - id: mypy\n        entry: uv run mypy src\n")
    assert ci.find(ci.follow(tmp_path), "mypy src").status == "found"


def test_route_through_docker_compose_exec(tmp_path):
    (tmp_path / ".fabro").mkdir()
    (tmp_path / "backend/scripts").mkdir(parents=True)
    (tmp_path / "backend/scripts/test.sh").write_text("#!/usr/bin/env bash\npytest tests/\n")
    (tmp_path / ".fabro/ci.sh").write_text("#!/usr/bin/env bash\ndocker compose exec -T backend bash scripts/test.sh\n")
    r = ci.follow(tmp_path)
    assert ci.find(r, "pytest").status == "found"
    assert not r.unresolved


def test_missing_script_is_unresolved(tmp_path):
    (tmp_path / ".fabro").mkdir()
    (tmp_path / ".fabro/ci.sh").write_text("#!/usr/bin/env bash\nbash scripts/gone.sh\n$RUNNER test\n")
    r = ci.follow(tmp_path)
    assert {u.why.split(":")[0] for u in r.unresolved} == {"no such file", "the command is a variable"}


def test_assignments_and_echo_are_not_commands(tmp_path):
    (tmp_path / ".fabro").mkdir()
    (tmp_path / ".fabro/ci.sh").write_text(
        '#!/usr/bin/env bash\nx=$(git rev-parse HEAD)\narr=(a b)\necho "pytest"\nCI=1 uv run pytest\n')
    r = ci.follow(tmp_path)
    assert [c.key for c in r.commands] == ["pytest"]


def test_no_ci_sh(tmp_path):
    with pytest.raises(FileNotFoundError):
        ci.follow(tmp_path)
