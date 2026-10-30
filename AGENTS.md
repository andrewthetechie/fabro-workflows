# AGENTS.md

Eight Fabro workflows and the ops tooling for the host that runs them.

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
| `.fabro/workflows/<name>/` | **The only tree the automations read.** Twelve packages — the eight the automations fire (`backlog`, `ralph`, `pr-review`, `issue-triage`, `arch-review`, `dependabot`, `merge-queue` and `scout`; `merge-queue` and `scout` have no agent stage), and the four box-only packages the **Scheduler**'s background lane runs in a coder loop's gap (more-local-inference, ADR 0031): `box-noop`, `issue-precheck`, `context-pack`, and `pr-doctor` (retired 2026-10-30; nothing can queue it) — plus two importable graphs with no `workflow.toml`: `_shared/review-merge/`, which `backlog`, `pr-review` and `dependabot` splice in (it has a queue mode that only `backlog`'s fast lane uses), and `_shared/triage/`, which `issue-triage` and `arch-review` splice in. `ralph` imports neither: it copies the shared merge nodes, and `ops/check-graph-invariants.py` R10 fails when a copy drifts. `backlog/scripts/discord-notify.sh` is executed by hooks, so changing it is a deploy. |
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
- `docs/agents/invariants-dependabot.md`: the `dependabot` package: the merge loop's checkout,
  the patch path, and the tracking issue.
- `docs/agents/invariants-merge-queue.md`: the `merge-queue` package: the approval chain, the
  guard, the GitHub API actions, and the monitor exclusion.
- `docs/agents/invariants-ralph.md`: the `ralph` package: the Task list, the loop, the Refute
  rounds, the evidence rule, and what it copies from `backlog` and the shared merge phase.

## Validating

`make check` runs every offline gate that must pass before a push to `main`. The
pre-push hook runs `make check-fast` — the same minus the gate tests (`ops/test-gates.sh`,
pytest in `ops/gate_tests/`), which take ~35s — so a push that fails a fast gate is aborted instead of remembered-to-check.
Run `make check` yourself before a push that changes a command node's shell. `make check-host` runs the gates that need the fabro container over
the LAN and is opt-in. Install the hook once per checkout, then just push:

```sh
make hooks            # git config core.hooksPath ops/hooks; git push now runs make check-fast
make check            # every offline gate (ops/check.sh), ~50s (+~25s when cargo runs)
make check-fast       # all but the gate tests (CHECK_SHELL=never), ~10s
make check-host       # opt-in: needs ssh to andrew@10.10.0.32 (ops/check-host.sh)
```

The offline gates need only jq, awk, git, python3.11, uv (the gate tests' pytest) and,
when `ops/fabro-io/` or `_io/schemas/` changed, cargo. `git push --no-verify` skips the hook. There is no `fabro`
binary on this Mac, so graph validation happens in the container via `make check-host`.

Baselines (fabro 0.362.0-nightly.0, last verified by `make check-host` on 2026-10-13):
`Backlog` was `82 nodes, 210 edges` (with one warning, `issue_number` unbound in `claim`);
`PrReview (39 nodes, 87 edges)` with two warnings, `pr_number` unbound in `validate_input` and `coder_pool` unbound in `claim` (the claim's box file, more-local-inference C2; since 2026-10-28);
`IssueTriage (16 nodes, 36 edges)`, `ArchReview (24 nodes, 54 edges)`,
`Dependabot (51 nodes, 115 edges)`, `MergeQueue (8 nodes, 10 edges)` and `Scout (9 nodes, 11 edges)` clean (Scout verified by `make check-host` on 2026-10-16). `BoxNoop (4 nodes, 3 edges)`, the background lane's box-only package, with one warning, `coder_pool` unbound in `claim` (verified by `make check-host` on 2026-10-28). `PrDoctor (9 nodes, 13 edges)` (more-local-inference task 06) with **one** warning, `coder_pool` unbound in `claim`: `pr_number` is interpolated in the same node's `script`, and fabro emits one undefined-input diagnostic per node *attribute*, so it folds into the first (`docs/agents/validating.md`). Verified by `make check-host` on 2026-10-29; the 13th edge is `claim -> exit`, which the Phase 2 review added so a failed `claim` ends the run failed. `IssuePrecheck (8 nodes, 11 edges)` (more-local-inference task 07) and `ContextPack (8 nodes, 11 edges)` (task 08) each carry the same one warning, `coder_pool` unbound in `claim`, with `issue_number` folded into it (both verified by `make check-host` on 2026-10-30). Every
warning is deliberate: never bind those inputs in `[run.inputs]`. A bound `coder_pool` would pin every hand-fired run to one box and let a fire without one claim nothing. The fast-path series
added `report_queued` to the three importing graphs (+1 node, +3 edges each), and
`route_lane` and `demote_lane` to `backlog`. The gate-relief series (#29) added `refute_fix` and `refute_fix_gate` to the three importing graphs (+2 nodes, +5 edges each). The final-merge fix (2026-10-12) added `final_merge` to `backlog` (+1 node, +4 edges). The base-conflict fix (#37, 2026-10-13) added `resolve_base` and `resolve_base_gate` to the three importing graphs (+2 nodes, +6 edges each). The final-validation fix (#35, 2026-10-13) added `drop_unreviewed` and `final_fix_prep` to `backlog` (+2 nodes, +9 edges). The unmet-criteria series (#17, 2026-10-13) added `return_to_triage` and `task_block_gate` to `backlog` (+2 nodes, +6 edges). The plan trial (#25, 2026-10-13) added `plan` to `backlog` (+1 node, +5 edges); closing it (2026-10-21, ADR 0030) deleted `improve` and the three arm-split edge pairs, so **`Backlog` is expected to be 81 nodes and 205 edges**. `make check-host` confirmed it on 2026-10-22.

`Ralph (51 nodes, 135 edges)`, verified by `make check-host` on 2026-10-22, carries one
deliberate warning like `Backlog`'s, `issue_number` unbound in `claim`, for the same reason:
binding it in `[run.inputs]` would route every hand-fired run to one box.

`docs/agents/validating.md` says what each gate covers and misses, why the warnings are
deliberate, and why `fabro preflight` is not an offline check.

## Deploying

**`fabro-io`'s manifest structs ship before any manifest that uses them** (D18). The
binary's manifest types are `deny_unknown_fields`: a `workflow.toml` carrying a field the
deployed `fabro-io` does not know fails to load, and with it `submit`, `serve`, `guard` and
`git-guard` in every run. So a change to `ops/fabro-io/src/manifest.rs` is pushed **and its
profile images deployed** (`make deploy-images`) before the commit that raises
`min_binary` or adds the field to `_io/manifest.json`. A change to either alone is an
outage, not a half-feature.

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
hand only with `~/bin/fabro-fire-pr-review.sh <owner/repo> <pr>`,
`~/bin/fabro-fire-backlog.sh <owner/repo> <issue>` or
`~/bin/fabro-fire-ralph.sh <owner/repo> <issue>`. The scheduler routes an issue to
`ralph` instead of `backlog` only while `SCHEDULER_RALPH_SHARE=50` (ADR 0029 O2), which
`ops/docker-compose.yaml` sets since the trial's switch on 2026-10-22; 0 is the kill switch. A `dependabot-<repo>` run fires through
its `api:manual` trigger (task 10 records the command). A `merge-queue-<repo>` run fires from its
schedule (every 20 minutes, created disabled) or its `api:manual` trigger; it takes no input. A `scout-<repo>` run fires from its daily schedule (created disabled) or its `api:manual` trigger, and it takes no input. `docs/agents/server.md` says why the
automation endpoint cannot carry the input, and covers the scheduler.

The scheduler's **background lane** fills a coder box's gaps with box-only runs: the issue
precheck (`issue-precheck`, task 07, in shadow mode: it posts a verdict comment and holds
nothing), the context pack (`context-pack`, task 08: a code map of an issue the precheck
passed, which `backlog`'s and `ralph`'s `claim` copy into `background.md` for `decompose` and
`plan`), and `box-noop` by hand. One
switch turns it off: `SCHEDULER_BACKGROUND=off` (the default) in `~/fabro/.env`, applied with
`docker compose up -d --no-deps scheduler`; running background runs finish. Its eight
variables reach the container only through `ops/docker-compose.yaml`'s `environment:` lines
and `~/fabro/scheduler.env`, never `.env` as an env file. `GET :32280/api/background` is the
lane's state and the queue page's *Background* strip is the same document;
`POST /api/background/box-noop` with `{"repo"}` queues a measuring job by hand. The precheck
and pack producers queue jobs for the next `SCHEDULER_PRECHECK_TOP_N` issues in dispatch order.
`ops/README.md` has the runbook.

The operational history is `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac,
mode 600, deliberately outside this public tree. Append a dated section rather than
editing earlier ones; they are a chronological record.

## The two checkouts

`~/.fabro-deploy/fabro-workflows` is the operator's deploy checkout. This one is for
working sessions. They are the same repository, so pull before you start and push when
you finish, or the two will diverge.
