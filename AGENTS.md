# AGENTS.md

Four production Fabro workflows and the ops tooling for the host that runs them.

## Pushing to `main` deploys

Every automation resolves `workflow_source` to `andrewthetechie/fabro-workflows@main`
at fire time, so a commit on `main` is live on the next fire. Those runs open pull
requests and force-push branches in four real repositories. There is no staging
branch, no review gate, and no rollback other than another commit. Validate before
every push.

Commit to `main` directly. That is this repo's entire history, and it is the only
branch anything reads.

## This repo is public

No token, key, or webhook URL belongs in a tracked file. Use `${VAR}` and `.env`
indirection and reference secrets by name; `ops/README.md` maps each one to where it
actually lives.

## Layout

| Path | What it is |
|---|---|
| `.fabro/workflows/<name>/` | **The only tree the automations read.** Four runnable packages — `backlog`, `pr-review`, `issue-triage`, `arch-review` — plus two importable graphs with no `workflow.toml`: `_shared/review-merge/`, which `backlog` and `pr-review` splice in, and `_shared/triage/`, which `issue-triage` and `arch-review` splice in. `backlog/scripts/discord-notify.sh` is executed by hooks, so changing it is a deploy. |
| `.fabro/workflows/_io/` | The **Stage manifest** and output schemas (ADR 0016): the only source of every `FABRO_IO_MANIFEST` block, generated into each `workflow.toml`. Never hand-edit the generated blocks; `check` fails on drift. |
| `ops/` | Host replication: compose, the coder scheduler, profile images, provisioning, sweepers, and the offline checkers. No automation reads this tree. `ops/README.md` has its contents table and the runbooks. |
| `docs/` | `docs/adr/` holds the decisions; every other folder is a task series or a set of design records. `docs/README.md` indexes them with their status. |
| `docs/agents/` | Reference for working in this repo: the deployment invariants, why each validation gate exists, deploying, and the server. |
| `.scratch/` | Untracked working notes. |

## Writing, changing, or diagnosing a workflow

**Invoke the `/fabro-workflow` skill first.** It carries the DOT attribute tables,
node types, condition grammar, the transition cascade, and the CLI. Treat it as the
source of truth for how Fabro behaves; this file covers only what is specific to this
deployment.

Then read the ADRs that the graph's comments cite, and the live series for the part you
change (`docs/README.md`). Each workflow is built on
file-backed contracts: an agent writes JSON to a path under `/tmp/fabro/`, and a small
command node validates it with `jq` and emits `context_updates` that decide routing.
Changing a contract's shape without changing the gate that reads it, or the prompt that
writes it, is the most common way to break one of these.

**Read the deployment invariants for the area before you change it.** Each one passes
`fabro validate` and fails at runtime, most of them silently:

- `docs/agents/invariants-graph.md`: any graph, prompt, hook or `workflow.toml` (syntax,
  stylesheets, routing, hooks, timeouts and the breaker, Stage I/O).
- `docs/agents/invariants-backlog.md`: `backlog`'s issue selection, queue labels, Child
  issues, `open_pr`/`open_pr_prep`, and the rescue gate.
- `docs/agents/invariants-merge.md`: the `_shared/review-merge/` phase, its report hooks,
  and the auto-merge switches.
- `docs/agents/invariants-host.md`: stylesheet models and fallbacks, the settings overlay
  and providers, and the profile images.

## Validating

Run these before every push. There is no `fabro` binary on this Mac, so the graphs are
validated in the container (repeat the last line for `backlog`, `issue-triage` and
`arch-review`):

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
scp ops/check-routing-schemas.py andrew@10.10.0.32:/tmp/check-routing-schemas.py
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
  /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/pr-review/workflow.toml'
```

Baselines (as of 2026-10-02, fabro 0.362.0-nightly.0): `Backlog (69 nodes, 162 edges)`
with one warning, `issue_number` unbound in `claim`; `PrReview (34 nodes, 73 edges)` with
one warning, `pr_number` unbound in `validate_input`; `IssueTriage (16 nodes, 36 edges)`
and `ArchReview (24 nodes, 54 edges)` clean. Both warnings are deliberate: never bind
those inputs in `[run.inputs]`.

The offline gates run on the Mac with Python 3.11+ (macOS ships 3.9):

```sh
./ops/test-task-gates.sh      # 814 checks on the Mac, 902 in a profile image
python3.11 ops/fabro-io-manifest.py check
python3.11 ops/check-agent-profiles.py ops/settings.toml.example
python3.11 -m unittest discover -s ops/tests
for f in .fabro/workflows/*/workflow.toml; do python3.11 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$f" || echo "BAD $f"; done
(cd ops/fabro-io && cargo test)   # when ops/fabro-io/ or _io/schemas/ changed
```

`docs/agents/validating.md` says what each gate covers and misses, why the warnings are
deliberate, and why `fabro preflight` is not an offline check.

## Deploying

**Workflow changes need no deploy.** Every automation resolves `.fabro/workflows/**`
from `main` at fire time. **`ops/` and `backlog/scripts/` changes do:** run `make deploy`
from a clean checkout at `origin/main` (single steps: `make deploy-scheduler`,
`deploy-images`, `deploy-scripts`, `deploy-notify`, `deploy-compose`, `provision`,
`verify-host`). `make compose-up CONFIRM=1` is the only target that restarts fabro, and a
restart fails every in-flight run. Upgrading the fabro binary is a separate, rehearsed act
(`docs/fabro-upgrade/`).

`docs/agents/deploying.md` has the manual commands behind each step, the host-matches-repo
diffs, and the auto-merge kill switch.

## The server

`ssh andrew@10.10.0.32`, a trusted single-tenant box. The compose project is in
`~/fabro`, the API is on `:32276`, and the CLI exists only inside the container:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p'
```

`fabro events -p` shows which edge was selected and why, which is where a wrong-route
bug surfaces. The coder scheduler is the only producer of `backlog` runs. Fire a run by
hand only with `~/bin/fabro-fire-pr-review.sh <owner/repo> <pr>` or
`~/bin/fabro-fire-backlog.sh <owner/repo> <issue>`. `docs/agents/server.md` says why the
automation endpoint cannot carry the input, and covers the scheduler.

The operational history is `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac,
mode 600, deliberately outside this public tree. Append a dated section rather than
editing earlier ones; they are a chronological record.

## The two checkouts

`~/.fabro-deploy/fabro-workflows` is the operator's deploy checkout. This one is for
working sessions. They are the same repository, so pull before you start and push when
you finish, or the two will diverge.
