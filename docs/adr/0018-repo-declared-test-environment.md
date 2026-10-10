# Each repository declares its Test environment, and agents test through it

**Status:** proposed (2026-10-09). It becomes accepted when the `docs/test-env/` task 08
recheck shows M-env near zero on womens-fantasy-sports and lawncare-saas. The plan is
`docs/test-env/`.

The services, preparation and variables a repository's tests need live only inside its
`.fabro/ci.sh`. `ci.sh` starts the image's PostgreSQL (`fabro-pg-ensure`), fixes the role's
client encoding, exports `POSTGRES_*` or `DATABASE_URL`, provisions Node 24, and runs
migrations and the seed. The coder prompt tells agents to leave the full suite to
`validate` and run "the narrowest useful check", so an agent's narrow run gets none of that.
In run `01M4HVKKAHPFK3X9KJ18D8YDZG`, `coder@1` ran pytest, hit `connection to server at
"127.0.0.1", port 5432 failed: Connection refused` in conftest import, read the conftest,
added `-n0` and ran only the SQLite-backed tests. The Postgres tests its acceptance
criteria named first ran in `validate`. `baseline_check` has the same gap: its worktree has
no database either, so it reports a connection refusal as a pre-existing failure.

We decided that **each target repository declares its Test environment once, in
`.fabro/test.toml`**, and that both validation and agents use that one declaration:

- `ci.sh` starts with `test_env=$(fabro-io test-env); eval "$test_env"`, which prepares
  from scratch and prints the `export` lines. The exports in `ci.sh` are deleted. (`eval
  "$(fabro-io test-env)"` is wrong: under `set -e` a failure inside it is ignored.)
- Agents get `run_tests TARGET [paths…]` from `fabro-io`, with `fabro-test` as the shell
  fallback. The repository names its **Test targets** and their commands. The agent adds
  only paths and node ids, plus flags the target allows.
- Preparation runs lazily, the first time a sandbox needs it, and is cached under a
  fingerprint of the files it reads. `baseline_check` prepares from the base tree and
  accepts targets too.
- A PR that edits `.fabro/test.toml` or `.fabro/ci.sh` trips `config_changed`, so it is
  never auto-merged: it would loosen the rules that judge it (ADR 0013 D2).

## Considered options

- **Tell the coder to run `fabro-pg-ensure`** (a prompt line or an Agent guide row).
  Rejected: the steps are per repository (womens-fantasy-sports needs a role encoding fix,
  the seed and `-n 1`; lawncare-saas needs `CI=1`), and each agent would rediscover them
  from conftest files. The knowledge belongs in the repository.
- **A free-form `run_tests COMMAND`.** Rejected: an agent that adds `-n0` truncates the
  seeded template database (womens-fantasy-sports' session `db` fixture truncates every
  table on teardown), and nothing could stop it. Named targets with a flag allowlist let
  the repository decide what a narrow run may change.
- **A shell file (`.fabro/test-env.sh`) sourced by both callers.** Rejected: a tool can
  read a strict TOML schema, list its targets in a tool description and fingerprint its
  inputs. It cannot do the same from a script.
- **A separate database per caller for `baseline_check`** (`POSTGRES_DB = "test_{instance}"`).
  Rejected: every repository's config would need to know about instances, and
  womens-fantasy-sports' conftest already uses the database name as its clone template.
  `baseline_check` instead re-prepares the shared environment, and the fingerprint
  mismatch makes the agent's next run re-prepare.
- **Preparing at every entry node.** Rejected: arch-review and issue-triage never run a
  test, and a slow seed would delay the first agent of every run.

## Consequences

- `ci.sh` depends on `fabro-io` and fails loudly without it. Nothing runs `ci.sh` outside a
  profile image, so the "outside that image, provide a PostgreSQL…" comments go away.
  A repository's `ci.sh` change must therefore land only after the images carry the new
  `fabro-io`, or `validate` fails on every run.
- `test.toml` is a file format four repositories depend on. It is versioned (`version = 1`)
  and parsed strictly, and the image build runs `fabro-io test-env --check` against each
  repository's file.
- `validate` always prepares from scratch. Only agent calls use the cache, so a dirtied
  database can mislead an agent but never the gate.
