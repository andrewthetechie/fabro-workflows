# Setting up a repository's Test environment

This is the reference for a target repository's `.fabro/test.toml`: what it gives the
factory, every key it accepts, how each one behaves, and how to check a file before it
lands. The decisions behind it are in ADR 0018 and `00-overview-and-contracts.md`. When
this guide and the contracts in that file disagree, the contracts are correct, and this
guide must be fixed.

## What the file gives you

A repository declares its **Test environment** (the services, variables and preparation its
tests need) and its **Test targets** (named test commands) in one file. Four consumers read it:

| Consumer | What it does with the file |
|---|---|
| `run_tests TARGET [args]` (MCP tool, every agent stage) | prepares the environment if its cached copy is stale, then runs the target's command with the variables applied |
| `fabro-test TARGET [args] [--fresh]` (shell, every profile image) | the same thing from a shell |
| `.fabro/ci.sh`, through `fabro-io test-env` | prepares from scratch and exports the variables, so `ci.sh` keeps no copy of them |
| `baseline_check` with `target` | runs a target against the task base, prepared from the base tree |

Without the file, an agent's narrow test run gets none of this. The case that started the
series: a coder ran `pytest` against a Postgres nobody had started, read the conftest, and
then ran with `-n0`. That skipped the Postgres tests the issue asked for.

The file is optional. A repository without it needs nothing, and `run_tests` tells the agent
to run its narrowest check through the shell. **Declare one anyway**: targets exist only in
this file, and the agents are told to test through targets.

## Quick start

A repository with no services needs only targets:

```toml
version = 1

[targets.backend]
about = "backend pytest; pass test files or node ids"
run = "uv run pytest -q"
flags = ["-x", "-v"]
value_flags = ["-k"]

[targets.frontend]
about = "frontend unit tests; pass test files, relative to frontend/"
cwd = "frontend"
run = "npm test --"
```

A repository whose tests need Postgres adds variables and preparation:

```toml
version = 1

[env]
DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/test"
CI = "1"

[prepare]
run = ["fabro-pg-ensure"]

[targets.backend]
about = "backend pytest; pass test files or node ids"
run = "uv run pytest -q"
flags = ["-x", "-v"]
value_flags = ["-k"]
```

Then put this line in `.fabro/ci.sh`, after `set -euo pipefail` and before the first test
command, and delete whatever the file now declares (the service start, the exports, the
`PATH` changes):

```sh
test_env=$(fabro-io test-env); eval "$test_env"
```

Check it (see *Checking a file*), and land it as a hand-written PR (see *Landing it*).

## Every key

The file is TOML. **An unknown key anywhere is an error**, so a typo fails the check
instead of being silently ignored.

### Top level

| Key | Type | Default | Rules |
|---|---|---|---|
| `version` | integer | required | must be `1` |
| `path` | array of strings | `[]` | directories relative to the checkout root; each is made absolute and put in front of `PATH`, in order. No empty entry, no absolute path, no `..`. **Must come before the first table**: TOML reads a key after `[env]` as part of `[env]`, so a `path` written there is rejected |

### `[env]`

Literal string values, applied to every preparation step and every target, and printed as
`export` lines by `fabro-io test-env`.

| Rule | Why |
|---|---|
| Keys match `^[A-Z_][A-Z0-9_]*$` | they become shell variable names |
| `PATH` is not allowed | use the top-level `path`, which prepends instead of replacing |
| Values are never expanded | `"$HOME/x"` is the literal text `$HOME/x`. Write the value out |
| Values must be strings | write `"5432"`, not `5432` |

Secrets do not belong here. The file is committed, and its values reach every agent.

### `[prepare]`

Optional. Without it, preparation has no steps: a call that finds the cache stale (see
*Caching*) still takes the lock and reports `prepared in 0s`, but runs nothing.

| Key | Type | Default | Rules |
|---|---|---|---|
| `run` | array of strings | `[]` | each entry runs as its own `sh -c` from the checkout root, with `[env]` and `path` applied. They run in order, and the first non-zero exit stops preparation. No entry may be empty |
| `inputs` | array of glob strings | `[]` | files whose content decides whether a cached preparation is still good (see *Caching*). Relative to the checkout root |
| `timeout` | integer seconds | `300` | `1` to `600`, for the whole preparation, all entries together |

Glob syntax: `*` matches within one path segment and never crosses `/`. `**` matches any
number of directories. `?` and `[abc]` work as usual. So `backend/app/alembic/**` is every
file under that directory, and `backend/*.py` is only the top level of `backend/`. An
invalid glob is an error.

Longer preparation logic goes in a script under `.fabro/` that one `run` entry calls, for
example `"sh .fabro/seed.sh"`. That keeps each entry readable in an error message, which
quotes the failing entry's text.

**Do not install toolchains in preparation.** Preparation runs on the first test call in
every fresh sandbox, so a download there is paid on every run. A runtime the tests need
(Node, a compiler, a CLI) belongs in the repository's profile image
(`ops/profile-images/`), where it is built once. womens-fantasy-sports first downloaded
Node 24 in preparation, though `fabro-ts` already ships it.

### `[targets.NAME]`

Zero or more. `NAME` matches `^[a-z0-9-]+$`: it is what the agent types, and the
`run_tests` tool offers the names as a fixed list.

| Key | Type | Default | Rules |
|---|---|---|---|
| `about` | string | required | one non-empty line. Agents see it in the tool description, so say what to pass: "backend pytest; pass test files or node ids" |
| `run` | string | required | the command, not empty. The agent's arguments are appended to it, each one single-quoted. Its stderr is merged into its stdout |
| `cwd` | string | `"."` | directory the command runs in, relative to the checkout root. Not empty, no absolute path, no `..` |
| `flags` | array of strings | `[]` | flags the agent may pass alone, such as `"-x"`. A flag listed here and in `value_flags` counts as a plain flag |
| `value_flags` | array of strings | `[]` | flags that take the next argument as their value, such as `"-k"` |
| `timeout` | integer seconds | `120` | `1` to `600`, for the run alone |

## How it behaves

### Arguments

`run_tests` checks every argument before anything runs:

- An argument that does not start with `-` is accepted (a test path, a node id, a name).
- A `flags` entry is accepted on its own.
- A `value_flags` entry takes the next argument as its value, even one that starts with
  `-`. A value flag with nothing after it is refused.
- Anything else starting with `-` is refused, and the message lists the allowed flags.
  Matching is exact: `-k=foo` is refused when only `-k` is listed.

Accepted arguments are single-quoted and appended to `run`, so `["tests/a.py", "-k",
"x or y"]` against `uv run pytest -q` runs `uv run pytest -q 'tests/a.py' '-k' 'x or y'`.
An argument cannot inject shell.

**Choose flags deliberately.** The check exists so an agent cannot change *how* the
environment runs. womens-fantasy-sports' backend target runs `pytest -n 1`, and it leaves
`-n` out of `flags`. Its session `db` fixture truncates every table on teardown when run
serially, so `-n0` empties the seeded template, and every later run fails. Allow
selection and verbosity flags (`-x`, `-v`, `-k`, `--lf`), not flags that change workers,
configuration or plugins.

`fabro-test` treats `--fresh` anywhere in its arguments as its own option, so a target
cannot receive a literal `--fresh`.

### The environment a command gets

Every preparation step and every target runs with the caller's environment plus:

1. each `[env]` variable;
2. `PATH` set to the `path` entries (absolute, under the checkout root, in order), then
   the caller's `PATH`.

`ci.sh` gets the same thing, because `fabro-io test-env` prints `export NAME='value'` for
each `[env]` key (in name order), then `PATH`.

### Caching

Preparation runs at most once per sandbox until something it depends on changes. After a
successful preparation, a stamp (`/tmp/fabro/test-env.stamp`) records a fingerprint over:

- the checkout root's absolute path,
- the bytes of `.fabro/test.toml`,
- the path and content hash of every file the `prepare.inputs` globs match. Symlinks are
  not followed, and `.git` is skipped.

A `run_tests` or `fabro-test` call prepares first when:

- it passes `fresh: true` (`--fresh`);
- there is no stamp, because nothing prepared yet or the last preparation failed;
- the stamp's fingerprint differs from the current one, because an input file or the
  declaration changed, or `baseline_check` prepared the base tree in the meantime.

So list in `inputs` the files that change what preparation produces: migrations, seed
data, fixtures loaded into the database. Do not list source files the tests read
directly; those need no preparation. A file outside the globs never makes the cache
stale, so a missing input means agents test against a stale database until they pass
`fresh`.

`fabro-io test-env`, the `ci.sh` path, always prepares from scratch, and writes the same
stamp.

**Preparation must be safe to repeat.** It runs again whenever the cache goes stale. Make
every step idempotent, or make it recreate its state (`fabro-pg-ensure` drops and
recreates its databases on every call for exactly this reason).

### One call at a time

Preparation rebuilds shared state, and two test runs against one database collide. So a
call holds an exclusive lock on the sandbox's Test environment (`/tmp/fabro/test-env.lock`)
from before it reads the stamp until its command finishes. A second `run_tests`,
`fabro-test`, `baseline_check` or `fabro-io test-env` waits its turn. That includes a call
from another process. Parallel tool calls therefore run one after another.
`baseline_check` with a command takes the lock only when the repository declares a Test
environment; otherwise there is nothing to share.

The lock belongs to the process that took it. If a `fabro-test` process is itself killed
(for example by a shell tool's timeout), the lock is released at once, while the step or
target it started keeps running in its own process group until it ends. A call made in that
window can prepare under it. Prefer `run_tests`, which the MCP server runs to completion.

### Time budgets

| Call | Budget |
|---|---|
| `run_tests`, `fabro-test` | the target's `timeout`, plus `prepare.timeout` when the call prepares. Never more than 600 s in all, time spent waiting for the lock included |
| `baseline_check` with a target | as `run_tests`, counted from when the target is looked up. Creating the base worktree comes first, uncounted; it takes seconds, inside the 60 s between 600 s and the 660 s tool timeout |
| `fabro-io test-env` | `prepare.timeout` for preparation, after waiting up to as long again for the lock |
| `baseline_check` with a command | the command's `timeout_s`, shortened so that worktree setup, the lock, preparation and command stay under 600 s |

When a budget runs out, the whole process group is killed, children included (`uv`,
`pytest`, `node`, anything a step started in the background). The 600 s ceiling sits under
the MCP `tool_timeout` of 660 s in every `workflow.toml`, so the agent always gets this
module's answer, never fabro's silent timeout.

### What the agent sees

A successful run:

```
phase: run
exit_code: 0
prepared in 41s; cached for later calls
<the last 200 lines of the command's output>
```

The `prepared in` line appears only when the call prepared. Longer output starts with an
`(N earlier lines cut)` note. A failing target reports its exit code the same way. A
target killed at its timeout reports `exit_code: timed out`, and its output is not kept. A failed preparation reports
`phase: prepare`, the exit code, the failing entry as `prepare.run[N] \`text\``, the
last 200 lines of the preparation log, and the log's path. The full preparation output
is always in `/tmp/fabro/test-env.log`, rewritten by each preparation.

The tool's description, and its list of target names, are built from the checkout's
`test.toml` when the agent session starts. The list is fixed for that session, so a target
added to the file mid-session is not offered until the next one.

### `baseline_check` with a target

`baseline_check {"target": "backend", "args": [...]}` runs a target in a worktree at the
task base, to see whether a failure predates the agent's change. It reads the base tree's
`.fabro/test.toml`, falling back to the working tree's when the base has none (the PR that
adds the file). It always prepares from the base worktree, which writes the base's
fingerprint, so the agent's next `run_tests` prepares again. Like `baseline_check` with a
command, it needs a dependency directory in the checkout (`node_modules`, or a `.venv`
beside a `pyproject.toml` or `uv.lock`) to share with the worktree, and refuses otherwise.

## Checking a file

On any machine with `fabro-io` 0.4.1 or later:

```sh
fabro-io test-env --check --root path/to/repo   # parse only; non-zero and names the key on any error
```

In the repository's profile image on the host, before you open the PR:

```sh
docker run --rm -v "$PWD":/work -w /work fabro-python-node:local sh -c '
  git config --global --add safe.directory /work   # the mount belongs to another uid
  set -e
  fabro-io test-env --check
  ./.fabro/setup.sh && ./.fabro/ci.sh
  fabro-test backend tests/some_test.py             # one call per target
  fabro-test backend tests/some_test.py             # again: must not prepare
'
```

`fabro-io test-env` and `fabro-test` find the checkout through `git rev-parse
--show-toplevel`, so the directory must be a git checkout (or pass `--root` to
`test-env`). Inside a container, git also refuses a checkout owned by another uid; git's
"dubious ownership" message is discarded, and you see only `not inside a git checkout`.
The `safe.directory` line handles that. Use the image
the repository runs in (`ops/profile-images/`). Never stop other containers to clean up:
the fabro server and its runs share that Docker host.

`ops/profile-images/build-images.sh` runs `fabro-io test-env --check` against each
repository's file, so a file that does not parse fails the image build rather than every
run.

## Landing it

- **Images first.** A `ci.sh` that calls `fabro-io test-env` fails every `validate` on an
  image without it (`docs/agents/invariants-host.md`). Every profile image has carried
  `fabro-io` 0.4.0 and `fabro-test` since 2026-10-10. Land a repository's file only on
  images with 0.4.1 or later: 0.4.0 does not enforce budgets on `fabro-python-node`,
  which has no `kill` binary, and lets concurrent calls prepare over each other.
- **A hand-written PR, reviewed by a person.** A PR that edits `.fabro/test.toml` or
  `.fabro/ci.sh` trips `config_changed` in the merge phase and is never auto-merged
  (ADR 0018 D7): a branch must not change the rules that run its own tests. A factory run
  that edits either file is held the same way.
- **The `ci.sh` line is exactly** `test_env=$(fabro-io test-env); eval "$test_env"`.
  Never `eval "$(fabro-io test-env)"`: under `set -euo pipefail`, `eval "$(cmd)"` ignores a
  failing `cmd`, so a broken file or a failed preparation would run the checks with no
  environment and no error.
- After merge, confirm the first `backlog` run's `validate` passes.

## Writing good targets

- **One target per test runner**, named for the part of the repository it covers
  (`backend`, `frontend`, `rust`). Agents pick by name and `about`.
- **The narrow form is the point.** `run` should accept test files or ids as trailing
  arguments. A runner that needs `--` before them gets it in `run` (`"npm test --"`).
  Never tell the agent in `about` to pass `--` itself: it starts with `-`, so the
  argument check refuses it.
- **Mirror CI.** The target should run tests the way CI does, under the same Test
  environment, so a pass under `run_tests` predicts a pass in `validate`.
- **Cover every unit-test suite CI runs.** A suite without a target can only be run
  through the shell, outside the Test environment. lawncare-saas has `backend`,
  `frontend` and `frontend-admin`.
- **A narrow run must fail when it matches nothing.** Call the runner directly rather
  than a package script that passes `--passWithNoTests` (or similar), or a mistyped path
  reports a pass. lawncare-saas uses `npx vitest run`, not `npm test`, for this reason.
- **Typecheck and lint are not targets.** The prompts send those through the shell.
- **Raise `timeout` for cold builds.** The 120 s default is short for a first
  `cargo test`; writers-app's `rust` target uses 600.

## Examples from the target repositories

womens-fantasy-sports (Postgres, a migrated and seeded database; Node 24 for vitest comes
from the `fabro-ts` image):

```toml
version = 1

[env]
POSTGRES_SERVER = "localhost"
POSTGRES_PORT = "5432"
POSTGRES_USER = "test"
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "test"
PGCLIENTENCODING = "UTF8"

[prepare]
inputs = ["backend/app/alembic/**", "backend/app/seed/**", "backend/app/initial_data.py", "content/**"]
timeout = 300
run = [
  "fabro-pg-ensure",
  "psql -h localhost -p 5432 -U test -d postgres -v ON_ERROR_STOP=1 -c \"ALTER ROLE test SET client_encoding TO 'UTF8'\"",
  "cd backend && uv run bash scripts/prestart.sh",
]

[targets.backend]
about = "backend pytest; pass test files or node ids"
cwd = "backend"
run = "uv run pytest -n 1 -q"          # never -n0: the session db fixture truncates the template
flags = ["-x", "-v", "--lf"]
value_flags = ["-k"]

[targets.frontend]
about = "frontend vitest unit suite; pass test files, relative to frontend/ (e.g. src/foo.test.tsx)"
run = "bun run --filter frontend test:unit --"
```

lawncare-saas (Postgres with no migrations to cache, and two frontends):

```toml
version = 1

[env]
CI = "1"                    # its conftest starts testcontainers unless CI is set
DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/test"
JWT_ISSUER = "lawncare"
JWT_AUDIENCE = "https://lawncare.example.com"
JWKS_URL = "https://lawncare.example.com/.well-known/jwks.json"
CRON_SECRET = "test-secret"

[prepare]
run = ["fabro-pg-ensure"]

[targets.backend]
about = "backend pytest; pass test files or node ids"
run = "uv run pytest -q"
flags = ["-x", "-v"]
value_flags = ["-k"]

[targets.frontend]
about = "frontend vitest unit tests; pass test files, relative to frontend/ (e.g. src/utils/timezone.test.ts)"
cwd = "frontend"
run = "npx vitest run"
value_flags = ["-t"]
timeout = 300

[targets.frontend-admin]
about = "frontend-admin vitest unit tests; pass test files, relative to frontend-admin/"
cwd = "frontend-admin"
run = "npx vitest run"
value_flags = ["-t"]
```

writers-app (no services; a Rust crate and a frontend):

```toml
version = 1

[targets.rust]
about = "cargo test for the workspace; pass a test name filter"
run = "cargo test --all-features"
timeout = 600

[targets.frontend]
about = "frontend vitest unit suite; pass test files"
run = "npm run test:unit --"
```

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `this repository declares no test targets; run the narrowest check through shell` | no `.fabro/test.toml` in the checkout. Add one |
| `.fabro/test.toml: ...` or a sentence naming a key | the file breaks a rule above; `fabro-io test-env --check` shows the same text |
| `target \`x\` does not allow \`-n0\`; allowed flags: ...` | the flag is not in `flags` or `value_flags`. Add it only if it cannot change how the environment runs |
| `phase: prepare` with `prepare.run[N] ... failed with exit code M` | entry N failed. Read `/tmp/fabro/test-env.log`, and rerun with `fabro-test TARGET --fresh` |
| `... ran past the preparation budget and was killed` | raise `prepare.timeout` (at most 600), or make preparation cheaper |
| `prepare.run[N] ... was not started: the preparation budget is spent` | the entries before N used the whole budget. As above |
| `exit_code: timed out` | the run passed the target's `timeout`. Narrow the arguments, or raise `timeout` |
| `another run_tests, fabro-test, baseline_check or fabro-io test-env call held the Test environment ...` | another call held the lock for this call's whole budget. Retry when it finishes |
| `baseline_check is not available in this repository` | the checkout has no `node_modules` or `.venv` to share with the base worktree |
| `not inside a git checkout; pass --root DIR` | run from inside the repository, or pass `--root` to `test-env` |
