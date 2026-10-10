# Test environment: overview and canonical contracts

**Status:** designed 2026-10-09 (ADR 0018, proposed). Nothing is implemented.

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. **Before you edit a `.fabro` file or a `workflow.toml`, invoke the
`/fabro-workflow` skill. Before you edit Rust under `ops/fabro-io/`, invoke `ms-rust`.**

## What this is

An agent that runs a narrow test in a target repository gets none of the services,
preparation and variables that repository's tests need, because only `.fabro/ci.sh` knows
them. This series moves that knowledge into a declaration the repository owns,
`.fabro/test.toml` (the **Test environment** and its **Test targets**, CONTEXT.md). `ci.sh`
and a new `fabro-io` tool, `run_tests`, both use it.

## The observed case

Run `01M4HVKKAHPFK3X9KJ18D8YDZG`, womens-fantasy-sports, `coder@1` (box-b, `coders-b`), task
`seed-load-prefetch-round-trips`:

| Time (UTC) | What the coder did |
|---|---|
| 03:31:26 | `uv run pytest tests/seed/test_load.py tests/content/test_careers.py -q`: 40 s, then `ConftestImportFailure: OperationalError: connection to server at "127.0.0.1", port 5432 failed: Connection refused` |
| 03:32:23 | "The tests need a running Postgres. Let me check the conftest and how tests are normally run:" reads `backend/conftest.py` (two ranges) and `[tool.pytest.ini_options]` |
| 03:33:45 | adds `-n0`. Without an xdist worker the root conftest skips its clone, and the selected files use `tx_sqlite`, so they pass |
| to 03:47 | never runs `tests/api/routes/test_pwhl.py` or `test_quiz.py`, the Postgres tests the acceptance criteria name |

PostgreSQL was in the image the whole time (`/usr/local/bin/fabro-pg-ensure`).

## Operator decisions (settled; do not change them)

| # | Decision |
|---|---|
| D1 | **The repository declares its Test environment in `.fabro/test.toml`.** The file is optional, and absence means the repository needs nothing. All four target repositories get one anyway, because Test targets exist only in it. Only womens-fantasy-sports and lawncare-saas have a `[prepare]` table. |
| D2 | **One source of truth.** `ci.sh` gets its variables and preparation from `fabro-io test-env` and keeps no copy of them. `ci.sh` depends hard on `fabro-io`: nothing runs it outside a profile image. |
| D3 | **Agents test through named targets.** `run_tests TARGET [args…]` runs the target's own command inside the Test environment. An argument starting with `-` is rejected unless the target allows that flag. There is no free-form command. |
| D4 | **Preparation is lazy and cached per sandbox.** It runs on the first `run_tests` or `baseline_check` call that needs it. It runs again when the fingerprint (C3) changes, or when the call passes `fresh: true`. `fabro-io test-env` (the `ci.sh` path) always prepares from scratch and writes the same stamp. |
| D5 | **`baseline_check` uses the Test environment.** It prepares the shared environment from the base worktree, which writes the base fingerprint. The agent's next `run_tests` sees the mismatch and prepares again. It accepts a target in place of a command. |
| D6 | **`run_tests` is offered to every manifest stage**, like the `code_*` tools and `baseline_check` (one tool vocabulary, ADR 0017). The coder prompt and the Agent guide point to it. |
| D7 | **A PR that edits `.fabro/test.toml` or `.fabro/ci.sh` trips `config_changed`** and is not auto-merged. Today only `.fabro/hygiene.json` does. |
| D8 | **Rollout order.** fabro-io first, then the images, then `min_binary`, then the baseline measurement, then the workflow change, then one target repository at a time, starting with womens-fantasy-sports. A target repository's `ci.sh` change must never land before the images carry the new `fabro-io`. |
| D9 | **Measured like coder-tweaks.** Take a baseline before task 05 and recheck over at least 16 runs after task 07 (task 08). |

## Verified facts (sources)

- **The preparation lives in `ci.sh`.**
  - womens-fantasy-sports' `ci.sh` runs `fabro-pg-ensure`, then `ALTER ROLE test SET client_encoding TO 'UTF8'`. The image's cluster is SQL_ASCII, and psycopg then returns `bytes` and breaks SQLAlchemy.
  - It exports `POSTGRES_SERVER/PORT/USER/PASSWORD/DB` and `PGCLIENTENCODING=UTF8`, downloads Node v24.11.0 into `node_modules/.fabro-node/` and prepends it to `PATH`, and runs `backend/scripts/prestart.sh` (Alembic upgrade, `initial_data`, `python -m app.seed.load`).
  - lawncare-saas' `ci.sh` runs `fabro-pg-ensure` and exports `CI=1` (its conftest otherwise starts testcontainers), `DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test`, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWKS_URL` and `CRON_SECRET`.
  - jelly-swipe and writers-app need no service.
- **`fabro-pg-ensure` recreates its databases on every call** (`DROP DATABASE IF EXISTS test WITH (FORCE)`, then `CREATE DATABASE test OWNER test`), because the suites leave rows and tables behind. `ops/profile-images/fabro-pg-ensure.sh`.
- **A serial womens-fantasy-sports run empties the seeded database.**
  - The session-scoped `db` fixture in `backend/tests/conftest.py` truncates every table on teardown.
  - Under xdist each worker instead clones the template (`CREATE DATABASE test_gw0 TEMPLATE test`, after `DROP … IF EXISTS`), so the template stays untouched (`backend/conftest.py:78-111`).
  - A target that runs `-n 1` therefore keeps the prepared state, and `-n0` does not.
- **`baseline_check` has the same gap.** It runs its command in a separate worktree at the base commit, with no database preparation (`ops/fabro-io/src/gitsafe.rs`). Limits: 120 s default, 600 s maximum, last 200 lines, under the `[run.agent.mcps.io] tool_timeout = "660s"` in each `workflow.toml`.
- **Every manifest stage gets the same `fabro-io` tools.** `list_tools` adds every `code::VERBS` entry and both `gitsafe` tools whenever the stage is in the manifest (`ops/fabro-io/src/serve.rs`).
- **`min_binary` is `0.1.0`** in `_io/manifest.json`, while the shipped binary is 0.3.2. Raising it makes an old image stop every agent stage at `io-stage`. It does not by itself make `run_tests` available.
- **`eval "$(cmd)"` ignores a failing `cmd` under `set -euo pipefail`.** `bash -c 'set -euo pipefail; eval "$(false)"; echo continued'` prints `continued`. `e=$(false); eval "$e"` exits 1. Verified 2026-10-09.
- **`config_changed` matches one file**: `grep -cxF .fabro/hygiene.json` over `git diff --name-only origin/$B...HEAD`, in `_shared/review-merge/` `hygiene`.
- **The Agent guide is 1,606 bytes** against `check`'s 3,000-byte cap (`_io/agent-guide.md`).

## Contracts

### C1. `.fabro/test.toml`, version 1

```toml
version = 1

# Relative to the checkout root. Made absolute and prepended to PATH in order.
# Must come before the first table.
path = ["node_modules/.fabro-node/node-v24.11.0-linux-x64/bin"]

# Literal strings, never expanded. Applied to every prepare entry and target, and printed
# as exports by `fabro-io test-env`.
[env]
POSTGRES_SERVER = "localhost"
POSTGRES_PORT = "5432"

[prepare]
# Globs relative to the checkout root, hashed into the fingerprint (C3).
inputs = ["backend/app/alembic/versions/**", "backend/app/seed/**"]
timeout = 300            # seconds, 1..=600, default 300
# Each entry runs as its own `sh -c` from the checkout root with [env] and path applied.
# The first non-zero exit stops preparation.
run = ["fabro-pg-ensure", "cd backend && uv run bash scripts/prestart.sh"]

[targets.backend]
about = "backend pytest; pass test files or node ids"   # required, one line
cwd = "backend"          # relative to the checkout root, default "."
run = "uv run pytest -n 1 -q"
flags = ["-x", "-v"]     # allowed flags that take no value
value_flags = ["-k"]     # allowed flags that take the next argument as their value
timeout = 300            # seconds, 1..=600, default 120
```

**Rules:**
- `version = 1` is required. An unknown key anywhere is an error.
- `path` is a top-level key and must come before the first table.
- Target names match `^[a-z0-9-]+$`.
- `[prepare]` is optional. Without it nothing is prepared and the fingerprint is constant.
- `[env]` keys match `^[A-Z_][A-Z0-9_]*$` and must not be `PATH` (use `path`).
- Longer preparation logic goes in a script under `.fabro/` that one `run` entry calls.

### C2. `fabro-io test-env` (the `ci.sh` path)

- `fabro-io test-env [--root DIR]` parses `test.toml` and always runs every `prepare`
  entry. It writes the stamp (C3) and prints only `export NAME='value'` lines to stdout:
  each `[env]` key, then `PATH`. Preparation output goes to stderr and to
  `/tmp/fabro/test-env.log`.
- With no `test.toml`, it prints nothing and exits 0.
- A parse or preparation failure exits non-zero with the failing entry's index and text on
  stderr.
- `fabro-io test-env --check [--root DIR]` only parses, and exits non-zero on any C1
  violation.
- The `ci.sh` line is exactly:

  ```sh
  test_env=$(fabro-io test-env); eval "$test_env"
  ```

  Never `eval "$(fabro-io test-env)"` (see *Verified facts*).

### C3. Fingerprint and stamp

- **Fingerprint:** SHA-256 over the checkout root's absolute path, the bytes of
  `test.toml`, and, for each file the `prepare.inputs` globs match (sorted by path), its
  path and content hash.
- **Stamp:** `/tmp/fabro/test-env.stamp`, a JSON object `{"fingerprint": "…", "root": "…",
  "prepared_at": "…", "seconds": N}`. Whoever prepares, `test-env` or a tool, writes it last,
  and only on success. A failed preparation deletes it.

### C4. `run_tests` (MCP) and `fabro-test` (shell)

- **Input:** `{"target": "<name>", "args": ["…"], "fresh": false}`.
- **Description:** built at `list_tools` time from the checkout's `test.toml`. It contains
  each target's name and `about`, and `target` is an enum of the names. With no file, or an
  invalid one, the description says so and `target` is a free string.
- **Argument check:**
  - An argument not starting with `-` is accepted.
  - A `flags` entry is accepted alone.
  - A `value_flags` entry consumes the next argument as its value.
  - Anything else is rejected before anything runs. The message names the allowed flags.
  - Each argument is single-quoted and appended to the target's `run`.
- **Preparation:** prepare first when `fresh` is true, the stamp is missing, or its
  fingerprint differs from the current one.
- **Budget:** the target's `timeout`. When the call prepares, `prepare.timeout` is added.
  The total never exceeds 600 s.
- **Result:**
  - `phase` (`prepare` or `run`) and `exit_code`.
  - One line `prepared in Ns; cached for later calls` when it prepared.
  - The last 200 lines of the failing phase's output, or of the run's output.
  - Full preparation output always goes to `/tmp/fabro/test-env.log`.
- **Errors:**
  - No `test.toml`: `this repository declares no test targets; run the narrowest check through shell`.
  - An invalid file: the parse error.
- **Shell fallback:** `fabro-test TARGET [args…] [--fresh]` behaves identically from the
  shell. It is a `fabro-io run-tests` alias installed in every image beside `fabro-code`.

### C5. `baseline_check` with a target

`baseline_check` gains an optional `target` (with `args`), as an alternative to `command`.
Either way it prepares from the base worktree before it runs:
- It reads the base tree's `test.toml`, falling back to the working tree's when the base
  has none (the rollout PR itself).
- It writes the stamp with the base worktree's fingerprint. The root path is part of the
  fingerprint, so the agent's next `run_tests` always prepares again (D5).

### C6. `config_changed`

The `hygiene` node counts any of `.fabro/hygiene.json`, `.fabro/test.toml` and
`.fabro/ci.sh` in `git diff --name-only origin/$B...HEAD`. The pattern uses bracket
expressions, never a backslash. The review comment's sentence about `config_changed` names
all three files.

### C7. Measurement

`ops/fabro-agent-tools.py` gains two measures over agent sessions:

| Id | Measure | Target |
|---|---|---|
| M-env | sessions with a shell or tool result containing a connection refusal on a port the repository's Test environment declares (5432), or `ConftestImportFailure` | about 0 on womens-fantasy-sports and lawncare-saas |
| M-run | share of test invocations (shell commands matching `pytest`, `vitest`, `npm test`, `bun run … test`, `cargo test`, plus `run_tests`/`fabro-test`) that go through `run_tests` or `fabro-test` | at least 60% |

## Tasks

| # | File | Where it runs |
|---|---|---|
| 01 | `01-measure.md`: M-env and M-run in `ops/fabro-agent-tools.py`; the baseline | this repo |
| 02 | `02-test-env-core.md`: C1 parse, C2, C3 in `fabro-io` | this repo |
| 03 | `03-run-tests-tool.md`: C4, C5; `fabro-test` | this repo |
| 04 | `04-images.md`: `fabro-test` in the images, `--check` in the build gate, `make deploy-images`, `min_binary` | this repo + host |
| 05 | `05-workflow.md`: Agent guide row, coder prompt line, C6 and its fixtures, AGENTS.md invariants | this repo |
| 06 | `06-womens-fantasy-sports.md`: `test.toml` and `ci.sh` | womens-fantasy-sports |
| 07 | `07-other-repos.md`: lawncare-saas, then jelly-swipe and writers-app | three target repos |
| 08 | `08-recheck.md`: recheck over at least 16 runs | this repo + host |

Tasks 01 and 02 are independent. Task 03 needs 02, task 04 needs 03, and task 05 needs 04
and the task 01 baseline. Tasks 06 and 07 need 04. Task 08 needs 05–07.
