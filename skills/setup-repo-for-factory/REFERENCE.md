# Reference

What `onboard.py` cannot decide for you, and the facts behind what it decides.

## Resolving a step

**The gate mirrors the checks a pull request has to pass.** A gate holding more than CI holds every
PR on something CI never asked for. A gate holding less is a hole, and the Refuter reads `ci.sh` to
credit acceptance criteria, so a check missing from it is a criterion it cannot credit.

The sandbox a step has to survive:

| Fact | Consequence for a step |
|---|---|
| No Docker, and no Docker-in-Docker | `docker …` and testcontainers cannot run. Rewrite without Docker, or skip. |
| No service containers. PostgreSQL only, through `fabro-pg-ensure`: superuser and password `test`, database `test`, port 5432, recreated on every call | `init` already rewrites CI's PostgreSQL URLs to this. A suite needing more databases changes the `prepare_run` step to `PG_EXTRA_DBS="…" fabro-pg-ensure`. |
| Size from the environment record (2 CPU / 4 GB; `rust-node` 4 / 8), root, network on, no display | A browser suite rarely fits. Gate it only after `contract-run` proves it does. |
| The toolchain and system packages come from the image | Skip `apt-get`, `rustup` and tool-install steps when the image carries the tool. Otherwise it is a blocker: see below. |
| No GitHub context | `${{ … }}`, `$GITHUB_*` and secrets do not exist. Rewrite with a literal (`edit_reason`), or skip. |

For each `review` row, ask in order:

1. **Would a failure here block a PR on GitHub, and does it check this repository's code?** If not, skip it.
2. **Does it pass on `main` in the sandbox?** Guessing is fine here: `contract-run` is the proof, and a wrong guess comes back as a failure.
3. **Does it install, rather than check?** `setup` is for idempotent dependency installs only. It runs in every run's `prep`.

Every `reason` is printed in `ci.sh` under *Left out of this gate*, so write it for a reader who has
never seen this repository. Every rewritten `run` needs an `edit_reason` naming why it differs from CI.

A hook the suite reads from `CI` deserves a look: `conftest.py` files often start testcontainers unless
`CI` is set. `facts` warns when testcontainers is in the tree. Set the suite's database URL or `CI = "1"`
in the plan's `[env]`, and remember `[env]` reaches every step: flakeradar sets `CI=1` on one command
because its backend behaves differently under it.

## Contract failures

`contract-run` keeps the full log beside the plan. Find the first failing command.

| What failed | What it means | What to do |
|---|---|---|
| exit 127, or a tool is missing | The image lacks it | It is a blocker: see *New image*. `setup.sh` must stay free of system installs. |
| Fails the same way in CI on `main` | The repository is red already | Fix the repository first, or skip the check with a reason the user has agreed to. |
| A wrong version of a runtime | The stack does not match CI | Reconsider the stack with the user; `facts` lists the mismatches. |
| Connection refused, or Docker errors | A service or testcontainers | Point the suite at `fabro-pg-ensure` through `[env]`, or skip it. |
| Passes alone, fails under the size limit | Out of CPU or memory | Skip it with that reason, or discuss a larger environment. |

Fix it in the plan, then `render`, commit, push and re-run. A code fix in the repository lands first on `main`.

## Blockers

| Blocker from `facts` | Why it stops onboarding | Way out |
|---|---|---|
| Default branch is not `main` | Rows, image builds and the merge phase all target `main` | Rename the branch |
| Squash merge off | Every factory merge is `gh pr merge --squash` | Turn it on in the repository settings |
| A service other than PostgreSQL | Fabro discards sandbox sidecars, so nothing can start it | A test double, an in-process alternative, or skip those suites |
| No image carries a runtime | No run can build the repository | *New image*, below |

**Warnings, for the user to weigh:** a version mismatch against the chosen image, Dependabot alerts
off (the `dependabot` workflow stops at its inventory), an owner the factory has not used before
(`build-images.sh` clones from one owner).

**A shared image gets no nightly check.** `build-images.sh` warms and verifies only one repository per
image, named in its `PROFILES`. A repository sharing that image has no warmed cache and no nightly
`setup.sh` check, so `contract-run` is the only proof it works there, and a later image change can
break it silently.

## New image

There is no script for this; it needs the user. In the factory checkout:

1. `ops/profile-images/Dockerfile.<env>`, with versions matching the repository's CI. Every image needs `git`, `jq`, `gh`, `bash`, `rg`, `fabro-code`, `fabro-io` and `fabro-test`. Copy the nearest Dockerfile.
2. A `<env>:<repo>:fabro-<env>:local` line in `PROFILES` in `build-images.sh`.
3. The tables in `ops/profile-images/README.md`, and the comment at the top of `ops/scheduler/repos.toml`.
4. Push, then `make deploy-images`.
5. `POST /api/v1/environments` matching the table in `ops/README.md`, *Server-side state*. Environments are server state, and no script creates them.

Then return to step 1.

## Contract files

They live in the target repository. The workflow graphs read them; the graphs themselves ship from the factory.

| File | Required | Who runs it | Rule |
|---|---|---|---|
| `setup.sh` | yes | `prep`, and before every validate | Idempotent; every run fails at `prep` without it |
| `ci.sh` | yes | Validate and the merge phase, after `setup.sh` | Non-zero on failure; quoted to the Refuter |
| `fix.sh` | no | `autofix`, after every coder or rework stage | Formatters and machine-applicable lint fixes only (ADR 0011 D5). Always exits 0 |
| `test.toml` | no | `fabro-io test-env`, `run_tests` | Without it `run_tests` has no targets |
| `scout.toml` | no | `scout` | Absent or invalid means the run stays dry |
| `hygiene.json` | no | Merge phase, from the base branch | Sets the hygiene gate's test paths, exclude paths and erosion thresholds (ADR 0013); `onboard.py` leaves it out, so the defaults apply |

`fix.sh` is derived from the gates, and it rewrites what they check. On a repository whose `main` is not
clean under its formatter, whole-tree fixes inflate every PR's diff. Check for that, and scope the fix
to changed files if needed: jelly-swipe's `fix.sh` is the worked example.

### Extending `test.toml`

`onboard.py` writes `[env]`, `[prepare]` and `[targets]`. Without Task validation keys, `task-plan` runs
the full suite, which is the safe start. Add them when a full run per task proves too slow. The parser
is `ops/fabro-io/src/testenv.rs`, and every table rejects unknown keys.

| Key | Rule |
|---|---|
| `version` | Must be 1 |
| `[env]` | Keys match `^[A-Z_][A-Z0-9_]*$`; `PATH` is refused (use top-level `path`) |
| `path` | Relative entries inside the checkout |
| `[prepare]` | `inputs` (globs in the cache fingerprint), `timeout` (default 300), `run` (ordered `sh -c` steps) |
| `[targets.<name>]` | Name matches `^[a-z0-9-]+$`. `about` and `run` required. `cwd`, `flags`, `value_flags`, `timeout` (default 120) |
| Target, Task validation | `tests` or `watch`, never both. `max_tests` needs `tests` |
| `[checks.<name>]` | `run` and non-empty `watch` required. `about`, `files`, `cwd`, `timeout` |
| `[task]` | `full` (globs forcing the full suite), `skip` (test files never run) |
| Every `timeout` | 1 to 600 seconds |

`flakeradar`'s `test.toml` on `main` is the worked example of the Task validation keys. Validate with
`onboard.py check`.

### Going beyond a dry scout

`filing = false` posts the plan as a DRY RUN. Set `filing = true` once a dry run reads right, and add
`[[ratchet]]` rows (`tool`, `rule`, `command`, `enforce_in`, `enforce_how`; `tool` is `ruff`, `eslint`
or `clippy`) and `[flakeradar]` where the repository uploads JUnit. `writers-app`'s file is the worked
example. A PR editing it is never auto-merged.

## Automations

Every repository gets all seven rows, and a schedule is the off switch. `register` computes each slot
from the rows already there.

| Row | Created | After onboarding | Why |
|---|---|---|---|
| `pr-review-<repo>` | no schedule | never fired | Holds the per-repo auto-merge switch that `fire-pr-review.sh` and `merge-queue` read. It must exist |
| `backlog-<repo>` | off | stays off | The scheduler is the only producer of `backlog` runs. A schedule here would fight it for the coder boxes. Fire scripts read this row for the image |
| `issue-triage-<repo>` | off | stays off | Off on every repository as of 2026-11-07, and nothing in `ops/` fires it |
| `arch-review-<repo>` | **on** | on | ADR 0012 D7 |
| `dependabot-<repo>` | **on** | on | ADR 0022 D10 |
| `merge-queue-<repo>` | off | on after `smoke` | ADR 0024 |
| `scout-<repo>` | off | on after `smoke` | ADR 0027 D16 |

`ralph` has no row. The scheduler starts it from `repos.toml`'s `environment_id`.

## What `register` edits

The file list came from the commits that added flakeradar: `aa25f1e`, and `83aa074` four days later,
because `make check` does not run the scheduler's tests.

| File | Edit |
|---|---|
| `ops/scheduler/repos.toml` | The `[[repo]]` table |
| `ops/provision-server-state.sh` | Seven rows, each after its workflow's last |
| `ops/fabro-branch-sweep.sh`, `ops/fabro-monitor.sh` | The default `REPOS` lists |
| `ops/README.md` | The two automation counts; the environments table |
| `ops/scheduler/tests/test_config.py`, `test_app.py`, `test_reconcile.py` | The tracked-repository assertions |

Each edit fails with `STOP` when its anchor has moved. The tests catch that against the real files, so
fix the tool rather than the edit.

## Another server

`onboard.py` reads `HOST`, `FABRO_API_URL` and `SCHEDULER_URL` from the factory's `ops/deploy-host.sh`.
For another server, pass `--host`, `--api-url` and `--scheduler-url` to `init`. For another factory
checkout, pass `--factory` or set `FABRO_FACTORY`. The token comes from `FABRO_DEV_TOKEN`, or over ssh
the way `deploy-host.sh` reads it, and is never printed.

## Where things live

| What | Where |
|---|---|
| The plan, its state and the contract log | `<factory>/.scratch/onboard/<owner>--<name>.*`, untracked |
| Label catalog | `data/labels.toml`; a test fails when the graphs use a fixed label it lacks |
| Contract templates | `templates/` |
| The parser binary | `~/.cache/fabro-onboard/target`, built from the factory's `ops/fabro-io` |
