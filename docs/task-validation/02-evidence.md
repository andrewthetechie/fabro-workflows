# Evidence: `fabro-code tests --json`, `list-tests`, and the later container checks

Appended by each task that runs a check in the `fabro-python-node` container: 02 (C2),
04 (C4) and 05 (C5). Everything below was run on the host `andrew@10.10.0.32` unless it
says otherwise.

## Setup (task 02)

- Clone: `~/womens-fantasy-sports` on the host, pulled to `main` at `c2a4be0`
  (2026-10-14, "refactor(frontend): remove unsafe type assertions in (#1594) (#2077)").
- Image: `fabro-python-node:local`.
- The in-tree script is mounted over the image's, because the images are not rebuilt
  until task 06: `/tmp/fabro-code-new` (this repo's `ops/profile-images/fabro-code`) and
  `/tmp/fabro-code-old` (`git show HEAD:...` before task 02) are mounted at
  `/usr/local/bin/fabro-code-new` and `-old`.
- `git config --global --add safe.directory /src` is needed only because the test mounts a
  host-owned clone into a root container; a real fabro sandbox owns its clone.

## Task 02 acceptance

```
# fabro-code index
exit=0

# tests --json backend/app/api/routes/waitlist.py
{
  "seeds": {
    "backend/app/api/routes/waitlist.py": {
      "indexed": true,
      "tests": [
        "backend/tests/api/routes/test_waitlist.py"
      ],
      "conftests": []
    }
  },
  "conftest_tests": {}
}
exit=0

# tests --json frontend/src/components/Admin/Moderation/ActionBar.tsx
{
  "seeds": {
    "frontend/src/components/Admin/Moderation/ActionBar.tsx": {
      "indexed": true,
      "tests": [
        "frontend/src/components/Admin/Moderation/ActionBar.test.tsx",
        "frontend/src/components/Admin/Moderation/ReportDetail.test.tsx"
      ],
      "conftests": []
    }
  },
  "conftest_tests": {}
}
exit=0

# tests --json README.md
{
  "seeds": {
    "README.md": {
      "indexed": false,
      "tests": [],
      "conftests": []
    }
  },
  "conftest_tests": {}
}
exit=0

# list-tests | wc -l
357
# git ls-files | grep -Ec <TEST_RE>
357

# plain form old vs new (waitlist)
IDENTICAL

# with .codegraph removed
no code index; use grep
exit=2
```

The conftest rule (D8) also resolves: `tests --json backend/app/core/db.py` reaches
`backend/tests/conftest.py` (127 tests under its directory) plus the nested
`backend/tests/api/routes/conftest.py` (27), `backend/tests/content/conftest.py` (7),
`backend/tests/models/conftest.py` (1) and `backend/tests/replay/conftest.py` (1).

`leagues.py` was the first route tried; on the current index it has no importing test
(`fabro-code tests backend/app/api/routes/leagues.py` prints
`no test files import: ...`), and `--json` agrees. `waitlist.py` is the acceptance seed.

`make check` (task 02): green; `ops/check-graph-invariants.py` 0 violations.

## Task 04 acceptance

The task 02 clone, indexed in a fresh `fabro-python-node:local` container with the in-tree
`fabro-code` and a `fabro-io` 0.5.0-dev musl binary built from this checkout
(`rust:1.98.1-trixie`, the same image `ops/profile-images/build-images.sh` uses; all 18
`taskplan_tests` and the rest of `cargo test` passed there). The task 09 draft
`test.toml` (`[task]`, `tests`, `[checks.prek]`) was committed locally as a throwaway
commit so it is part of the base, then discarded; the clone is back at `c2a4be0`.

```
=== backend route (waitlist.py) ===
{"mode":"scoped","reason":"","checks":["prek"],"targets":[{"name":"backend","args":["tests/api/routes/test_waitlist.py"],"whole":false}],"untested":[],"unchecked":[],"skipped":[]}
=== bun.lock (built-in **/bun.lock) ===
{"mode":"full","reason":"full_glob:bun.lock"}
=== frontend component ===
{"mode":"scoped","reason":"","targets":[{"name":"frontend","args":["src/components/Admin/Moderation/ActionBar.test.tsx","src/components/Admin/Moderation/ReportDetail.test.tsx"],"whole":false}]}
```

The backend args are relative to `backend/` and the frontend args to `frontend/`, as C4
step 7 requires.

Difference to report: the task file's example is `backend/uv.lock`, and
womens-fantasy-sports has no `backend/uv.lock` (it is not tracked, and never has been). The
real tracked dependency files are `bun.lock` (root) and `backend/pyproject.toml`; the
`bun.lock` run shows the built-in `**/bun.lock` glob forcing the full suite, which is what
the acceptance is about.

Noted, not a bug in the plan: `fabro-code index` writes `.codegraph/`, whose
`.codegraph/.gitignore` (`*`, `!.gitignore`) is itself untracked, so it appears in
`changed` on every indexed run. It matches no `full` glob, so it never escalates; the
`prek` check's `watch = ["**"]` covers it, which is why `unchecked` is empty here.

## Task 05 acceptance

Same container and clone, with the task 09 draft `test.toml` committed locally (throwaway
commit, then discarded) and `origin/main` pointed at it so `fabro-test --task` resolves the
base the way a run after the task 09 merge does. Binaries: the in-tree `fabro-test`, and a
`fabro-io` 0.5.0 musl build from this checkout (all `cargo test` green there, including
`taskcheck_tests`).

```
call1 {"prepared_seconds":7,"prepare":{"exit_code":0}}
call2 {"prepared_seconds":4,"prepare":{"exit_code":0}}
call3 {"prepared_seconds":0,"prepare":null}
gate  {"prepared_seconds":4,"prepare":{"exit_code":0}}
results: [{"kind":"check","name":"prek","exit_code":0},{"kind":"target","name":"backend","exit_code":0}]
```

`fabro-test --task` runs the `prek` check and the selected `backend` tests (the acceptance
edit was to `backend/app/api/routes/waitlist.py`), prints `ok: true`, and exits 0. `--gate`
prepares again, as C5 requires.

**Off by one on a fresh checkout, reported not hidden.** The task file says "a second call
does not prepare again", and the run above shows the second call preparing. The cause is
in ADR 0018's fingerprint, not in this task: `prepare.inputs` for this repository covers
`backend/app/seed/**` and `backend/app/alembic/**`, and the preparation imports those
modules, so Python writes `__pycache__/*.pyc` inside the globs. The stamp records the
fingerprint taken *before* the steps, so the next call sees the new `.pyc` and prepares
once more; from the third call the fingerprint is stable and `prepare` is null. With the
`.pyc` files already present (the common case after any earlier prepare), the second call
is cached, which a run with the `__pycache__` directories left in place confirmed. Caching
itself is correct: `task_check` prepares only on a fingerprint mismatch (C5, D9).
