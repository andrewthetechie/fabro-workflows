# AGENTS.md

Three production Fabro workflows and the ops tooling for the host that runs them.

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
| `.fabro/workflows/<name>/` | **The only tree the automations read.** Three runnable packages — `backlog`, `pr-review`, `issue-triage` — plus `_shared/review-merge/`, an importable graph with no `workflow.toml` that both `backlog` and `pr-review` splice in with `import=`. `backlog/scripts/discord-notify.sh` is executed by hooks, so changing it is a deploy. |
| `ops/` | Host replication: compose, the coder scheduler, profile images, provisioning, branch sweeper. Start at `ops/README.md`. No automation reads this tree. |
| `docs/pr-review-bridge/` | The task series for the third stage: `backlog` triggers a `pr-review` run on the PR it just opened. `00-overview-and-contracts.md` first. |
| `docs/auto-merge/` | A cross-cutting series for the fourth stage (the squash-merge), spanning `backlog` and `pr-review`: kill switches, Conventional-Commits titles, the merge graph, `ci_fix`. `00-overview-and-contracts.md` first. |
| `docs/issue-triage/` | The task series for the front of the chain: triage a `needs-triage` issue, ask the human only what the repository cannot answer, and promote it to the `agent` label `backlog` acquires from. |
| `docs/<workflow>/` | The numbered task series each workflow was built from — operator decisions, file contracts, and the reasoning behind every non-obvious choice. |
| `ops/check-routing-schemas.py` | Catches command-node routing-schema mismatches that `fabro validate` accepts and fabro only reports at runtime. Reads the parsed AST, not the DOT text. |
| `ops/test-task-gates.sh` | Runs `backlog`'s task-queue gates (`decompose_gate`, `improve_gate`, `next_task`) against fixtures, extracted verbatim from the graph. Offline; no host or container. |
| `ops/fabro-run-status.sh` | LLM-free health check for an in-flight run: alive, where in the graph, making progress, and whether a compaction says the decomposition was oversized. |
| `docs/scheduler/` | The coder scheduler: an external service that owns admission to the two llama.cpp instances, because fabro's own queue is FIFO-on-creation with no priority and no per-pool concurrency. `00-overview-and-contracts.md` first — it carries 19 settled operator decisions and the 11 source findings behind them, then 14 numbered task drafts. Each draft is written to be implementable from the overview plus its own file plus this repository. **Drafts 01–06 are built and deployed**; 07 onward are still design. The service itself is `ops/scheduler/`. |
| `docs/perf/` | Why a run takes four hours, measured from the run store rather than guessed. `00-overview-and-measurements.md` first — it is also where the source-verified list of what fabro's docker provider **cannot** do lives (no mounts, no service provisioning). `01` is what was applied, `02` is what needs an operator decision, `03` is the per-repository `.fabro/ci.sh` work, `04` is why compaction is a sizing alarm rather than a cost and what the task-size work changed. |
| `docs/research_improvements/` | An audit of all three packages against the Fabro source: what we hand-roll that Fabro already does, four operator-observed gaps traced to Fabro lines, and nine settled dead ends. A plan, not a changelog — nothing in it has been applied. `00-overview.md` first. |
| `.scratch/` | Untracked working notes. |

## Writing, changing, or diagnosing a workflow

**Invoke the `/fabro-workflow` skill first.** It carries the DOT attribute tables,
node types, condition grammar, the transition cascade, and the CLI. Treat it as the
source of truth for how Fabro behaves; this file covers only what is specific to this
deployment.

Then read `docs/<workflow>/00-overview-and-contracts.md`. Each workflow is built on
file-backed contracts: an agent writes JSON to a path under `/tmp/fabro/`, and a small
command node validates it with `jq` and emits `context_updates` that decide routing.
Changing a contract's shape without changing the gate that reads it, or the prompt that
writes it, is the most common way to break one of these.

## Validating

There is no `fabro` binary on this Mac, and the host CLI resolves `@prompts`
differently from the server. Validate in the container:

```sh
rsync -a --delete ~/Documents/code/fabro-workflows/.fabro/ andrew@10.10.0.32:/tmp/check/
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 rm -rf /tmp/check && docker cp /tmp/check fabro-fabro-1:/tmp/check'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro validate /tmp/check/workflows/pr-review/workflow.toml'
```

Baselines as of 2026-09-18 (review+merge shared and imported):
`Backlog (60 nodes, 141 edges)` clean, `PrReview (30 nodes, 65 edges)` with exactly
one warning — `pr_number` unbound in `validate_input` — and
`IssueTriage (14 nodes, 32 edges)` clean. Backlog and PrReview both include the ~21
nodes of `_shared/review-merge/`, which `fabro validate` splices in; `fabro parse`
shows the unexpanded placeholder instead, so node counts only match after validate. That warning is deliberate. Binding
`[run.inputs] pr_number` would silence it and let a run fired with no input review PR
#1 instead of failing at admission. (`auto_merge` is another unbound `{{ inputs.* }}` in the
same node, and it is *not* silenced by being supplied at fire time — `pr_number` is
supplied that way too and still warns. fabro emits one undefined-input diagnostic per
node **attribute**, and both references live in `validate_input`'s `script`, so the
second is folded into the first. Prove it exists by validating a scratch copy with
`pr_number` literalised: it then warns about `auto_merge`.)

`fabro validate` does NOT catch a routing-schema mismatch on a command node, in
either direction, and both fail only at runtime. Run the checker as well:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
  /tmp/check/workflows/*/workflow.fabro /tmp/check/workflows/_shared/*/*.fabro'
```

`ops/check-routing-schemas.py`, deployed alongside the graphs the same way. It exits
non-zero on any mismatch, so it drops into a pre-push hook. A node that declares
`output_schema="routing"` and prints no routing object fails deterministically with no
retry — that cost run `01M2R057XAWN8ZG0A7ZV7YPJXK` at `pr_handoff`, with the PR
already open.

Neither gate runs the command nodes' shell. `backlog`'s task queue is several hundred
bytes of `jq` spread across `decompose_gate`, `improve_gate` and `next_task`, and a
cursor off-by-one there silently skips a task rather than failing — the same class of
bug, one layer down. `ops/test-task-gates.sh` extracts those `script` attributes from
the graph verbatim, rebases `/tmp/fabro` onto a scratch directory and runs them against
fixtures:

```sh
./ops/test-task-gates.sh      # 71 checks, offline — no host, container or network
```

It needs only `jq` and `python3`, so it belongs in the same pre-push hook. It covers
queue mechanics only — selection, the split splice, the cursor, the guards — and says
nothing about whether an agent fills a contract correctly.

`fabro validate` does not parse `workflow.toml` strictly. A dotted model key that loses
its quotes becomes a nested table and the automation fire returns 422, with nothing
reported until then. Check it separately, on 3.11+ — macOS ships 3.9, which has no
`tomllib`:

```sh
python3.11 -c 'import tomllib; print(tomllib.load(open("workflow.toml","rb")))'
```

`fabro preflight` is **not** an offline check: its `LLM` check makes a real completion —
its detail line reads `Probe: basic generation` — so it fails with `server request timed
out after 30s` whenever a coder box is busy, which on this host is most of the time. The
summary it prints for that check is the model it probed, so preflight *is* the way to see
which model a stylesheet rule resolved to at real run time. Read a timeout there as
congestion, not as a graph fault; `fabro validate` and the routing checker are the gates
that need no box, and they are the ones to run before every push. Two gotchas when
preflighting `pr-review`: supply `-I pr_number=1` or it stops on the deliberate unbound
`pr_number`, and `-I auto_merge=0` as well or it then stops on the second unbound input in
the same `script` attribute.

## Deployment invariants

These pass `fabro validate` and fail at runtime. All three workflows depend on all of
them, except where a rule names one by id.

| Rule | What happens otherwise |
|---|---|
| `output_schema="routing"` on every command node whose script prints `context_updates` | Fabro never scans that node's stdout. Routing goes inert, the run walks its unconditional edges, and no error appears anywhere. This cost one live debugging round already. |
| Model stylesheet class selectors use `[a-z0-9-]` only — `.rebase-t2`, never `.rebase_t2` | `expected '{' after selector` |
| A per-run model choice goes in the root graph's `model_stylesheet` as `{{ inputs.X \| default('value') }}` — with single quotes — and `X` is never bound in `[run.inputs]` | The root stylesheet is the only per-run routing lever fabro has: node `model` attributes are literal text and `args.model` loses to any explicit stylesheet assignment. The render is **strict**, so an unbound input does not render empty, it fails the whole run at compile with `422 run_compile_invalid` — hence `default()`. A double quote terminates the enclosing DOT attribute string, so single quotes are the form that works; `\"` also parses but depends on the one allowed backslash. Binding `X` in `[run.inputs]` instead is the `pr_number` trap: it turns "no input supplied" into one specific answer, silently sending every hand-fired run to one box. |
| A model named by a stylesheet must also exist in `[llm.providers.litellm.models.*]` in the server's settings overlay | LiteLLM serving the name is not enough: fabro resolves a node's model against its own catalog and fails before any call is made. `coders-a`/`coders-b` exist in LiteLLM *and* in the overlay. `fabro validate` does not check the catalog, so a green validation says nothing here. Unlike `max_concurrent_runs`, catalog entries take effect with **no restart** — `replace_runtime_settings` (the 5s poll) rebuilds the catalog; only the cap is copied out once at startup. |
| `\"` is the only backslash in a `.fabro` file | The DOT parser turns `\n` into a real newline, so `printf '%s\n'` becomes `printf '%sn'` and jq's `"\n"` becomes an unterminated string. Get a newline inside an embedded jq program with `([10]\|implode)`. |
| `//` starts a comment; `#` only ever appears inside a quoted string | `#` is a DOT comment. `Resolves #N` and `##` headings inside a string are fine — never strip one to make a grep pass. |
| Inline scripts are POSIX `sh` | No `[[ ]]`, no `pipefail`, no arrays. `sh -n` catches this, but it is blind inside `jq '...'` — compile embedded jq programs separately. |
| Every command node's unconditional edge lands on the workflow's terminal-failure node | A failed node still routes, and it takes its *unconditional* edge. Where that edge is the happy path, fold `\|\| outcome=failed` into the escape edge's condition. |
| Reset per-iteration context keys, and write every key on both branches | A stale key from an earlier loop can satisfy an edge meant for this one. |
| Delete a contract file before the agent that writes it runs | An agent that exits succeeded without writing hands the gate its predecessor's result. |
| Agents do not run `git` | Two deliberate exceptions: `pr-review`'s rebase agent and `backlog`'s `resolve_merge` agent, which need `git add` and `--continue`. |
| A conflicted merge or rebase never crosses a stage boundary | The checkpoint is `git add -A && git commit`, and `git add` marks a conflicted file resolved — so the checkpoint commits conflict markers. The command node aborts to restore a clean tree; a dedicated agent then redoes and resolves the whole thing inside one stage. |
| `acquire` and `claim` are not atomic; `claim` arbitrates with a run-id marker and the lowest ULID wins | Two backlog runs whose `acquire` stages both finish before either `claim` lands will both claim the same issue — `gh issue edit` is idempotent, so the second succeeds silently and two runs implement it on separate branches. Observed on jelly-swipe#356. The 20s settle in `claim` must stay above the widest observed gap between two claims (4s), or both runs can conclude they won. |
| Anchor hook matchers | They are unanchored regexes tested against `node_id`, `handler_type`, `edge_to`, `edge_from` and `tool_name`. Write `^open_pr$`, not `open_pr`, for any id that prefixes another. |
| `[run.environment.env]` in backlog's `workflow.toml` must never gain an `id` key | It pins all four backlog automations to one environment, and two of the four repos fail CI on the wrong image. It also breaks `fabro validate`, which resolves a non-default id against the CLI's own local catalog and errors. |
| `trigger_review` keeps `on_failure="succeed"` | Its single unconditional edge points at `exit`. A failed node still routes and takes its *unconditional* edge, so without the attribute a trigger failure routes to `exit` instead of to `human_rescue`. |
| Both auto-merge switches fail closed on a value that is **present and not exactly the enabled one** — empty, `false`, `0`, `TRUE`, malformed. **Absence is not that case**: an absent per-repo `auto_merge` token means **on** (decision 10), and an absent `FABRO_AUTO_MERGE` variable means no run is created at all | A `gh` call that returns nothing, or a broken injection read as "not disabled", merges an unreviewed PR into `main`. On `lawncare-saas` that is also a deploy. The inverse error is just as costly: reading "fails closed" as "an untouched row is disarmed" is wrong — an untouched `pr-review-<repo>` row is **armed**, and three of the four are untouched. |
| `[run.environment.env]` interpolates `{{ vars.X }}` only — never `${X}`, never `{{ env.X }}` | `${X}` reaches the sandbox as literal text. A kill switch spelled that way is pinned off forever, and the shakedown cannot tell it from a correctly disarmed one. |
| The `FABRO_AUTO_MERGE` server variable must exist | An unset `{{ vars.X }}` fails the RunIntent at compile time, so no `pr-review` run is created at all. `provision-server-state.sh` creates it; `off` writes `0` and never deletes. |
| `POST /variables` upserts, so `provision_variable` is create-if-absent | A re-provision that POSTs unconditionally silently re-arms a switch an operator killed mid-incident. It reports drift instead. |
| A profile image must satisfy its repository's `.fabro/ci.sh`, not just its `setup.sh` | jelly-swipe's `ci.sh` gained `cd frontend && npm ci` while `fabro-python:local` had no npm. Under `set -euo pipefail` that is exit 127, `validate` exits 1, and every task walks the four-tier rework ladder at 45 minutes a tier against a failure no code change can fix. `ops/profile-images/build-images.sh` gates on this; a hand-built image does not. |
| `risk` is gate-enforced in `fix_gate`, not merely documented | Without the check the field is optional in practice, PRs quietly stop auto-merging, and the report still says "complete". |
| The merge node re-reads PR `state` from GitHub immediately before merging | The bridge's double-fire marker is per-run and does not stop a second review fired by hand. Two runs reach `merge`; the loser must report "already handled", not fail. |
| `gh pr merge` never passes `--delete-branch` (the merge node passes `--delete-branch=false`) | It deletes the **local** head branch as well as the remote one, and the merge is not the run's last stage: fabro's checkpoint publishes the run branch after every later stage, so the next publish fails with `src refspec … does not match any`, retries three times, and reports `failed/publish_failed`. A run whose PR merged and whose issue closed is then recorded as a failure — cost run `01M2TFEHPYXXTQST6VPXREA854` its status on 2026-09-18, eight seconds after PR #1206 merged. Reaping is `fabro-branch-sweep.sh`'s job. |
| Every merge-phase command node's unconditional edge lands on `mark_needs_human`; expected blocks are the *conditional* edges | A broken merge — a token without `contents: write`, an API 5xx — otherwise lands in the same quiet "not auto-merged" bucket as a risk-4 PR and stays invisible. |
| Commit-body markers are `:start` / `:end`, never `<!-- /fabro:commit-body -->` | A closing marker with a slash forces `\/` into the extraction pattern, and `\"` is the only backslash a `.fabro` file may contain. |
| The subject is the live PR title; the body comes from the marker block | `release-please` turns an agent's `feat:`/`fix:` subject into a release on `jelly-swipe`/`lawncare-saas`, and the derivation never emits `!` or `BREAKING CHANGE:`. |
| A `settings.toml` value that is read at startup is verified by the container's start time, never by `GET /settings` | fabro polls the overlay every 5s and republishes the *reported* settings, but `max_concurrent_runs` is copied out once at startup and read only by the admission loop — so after an in-place edit `/settings` answers the new value while the server keeps enforcing the old one, and the endpoint you would check with is the one endpoint that cannot tell you. Check that `docker inspect -f '{{.State.StartedAt}}' fabro-fabro-1` is later than the overlay's mtime; `ops/README.md` carries the command. |

## Deploying to the server after a merge to `main`

Manual today; automating it is the plan.

**Workflow changes need no deploy.** Every automation resolves
`.fabro/workflows/**` from `main` at fire time, so a merged graph or prompt is live
on the next run with nothing to copy.

**`ops/` changes do.** Nothing syncs that tree; the host keeps its own copies. After
a merge that touches `ops/` or `backlog/scripts/`, deploy what changed:

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only

# the notify script the backlog hooks call, by absolute path, from the container
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'

# the operator's manual-fire tool is a read-only wrapper; there is exactly one copy
# of fire-pr-review.sh, on main. Install the wrapper once; it is not re-deployed.
scp docs/auto-merge/fabro-fire-pr-review-wrapper.sh \
  andrew@10.10.0.32:~/bin/fabro-fire-pr-review.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-pr-review.sh'

# the sandbox profile images. Not a file copy: build-images.sh clones each target
# repo on the host, warms that repo's caches from its own lockfiles, and gates the
# result on an offline cache check plus a live run of the repo's .fabro/setup.sh.
# A failed build leaves the previous image tagged and in use.
rsync -a --delete ops/profile-images/ andrew@10.10.0.32:~/profile-images-build/
ssh andrew@10.10.0.32 'cd ~/profile-images-build && ./build-images.sh'

# the coder scheduler. Its compose service builds from ./scheduler, resolved
# relative to the compose file, so the tree has to sit beside it on the host.
# `up -d scheduler` names one service and never recreates fabro, which matters:
# a fabro restart fails every in-flight run. The tree carries no credential — the
# scheduler reads its own from ~/fabro/scheduler.env (NOT ~/fabro/.env, which
# holds fabro's SESSION_SECRET). Create it once per host, with both variables:
# without GITHUB_TOKEN the queue is empty and the page says so; without
# FABRO_API_TOKEN every dispatch answers 503 and /health reports
# `fabro.configured: false`. The fabro token is the server's own dev token, read
# out of the volume rather than retyped — there is exactly one.
#   ssh andrew@10.10.0.32 'cd ~/fabro && umask 077 && { \
#     printf "GITHUB_TOKEN=%s\n" "$(gh auth token)"; \
#     printf "FABRO_API_TOKEN=%s\n" "$(docker exec fabro-fabro-1 cat /storage/server.dev-token)"; \
#   } > scheduler.env && chmod 600 scheduler.env'
# `--build` is load-bearing: the runtime image carries `git`, which is how the
# scheduler fetches .fabro/ from main at dispatch time. An image built before
# draft 07 has no git and fails inside a dispatch.
rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'

scp ops/docker-compose.yaml andrew@10.10.0.32:~/fabro/docker-compose.yaml
scp ops/fabro-branch-sweep.sh andrew@10.10.0.32:~/bin/fabro-branch-sweep.sh
scp ops/fabro-sandbox-sweep.sh andrew@10.10.0.32:~/bin/fabro-sandbox-sweep.sh
scp ops/fabro-monitor.sh andrew@10.10.0.32:~/bin/fabro-monitor.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-monitor.sh'

# the auto-merge kill switch. It talks to the API over the network and runs fine from
# this checkout, but an incident that starts with an ssh session should not also need
# a git clone, so it is deployed alongside the sweepers.
scp ops/fabro-auto-merge-switch.sh andrew@10.10.0.32:~/bin/fabro-auto-merge-switch.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-auto-merge-switch.sh'

# automations, when the provisioning script changed or a row is missing
FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh
```

Then confirm the host still matches the repo. Every one of these should print
nothing:

```sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-branch-sweep.sh' | diff - ops/fabro-branch-sweep.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-sandbox-sweep.sh' | diff - ops/fabro-sandbox-sweep.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-monitor.sh' | diff - ops/fabro-monitor.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-auto-merge-switch.sh' | diff - ops/fabro-auto-merge-switch.sh
ssh andrew@10.10.0.32 'cat ~/fabro/docker-compose.yaml'  | diff - ops/docker-compose.yaml
ssh andrew@10.10.0.32 'cat ~/fabro/scheduler/repos.toml' | diff - ops/scheduler/repos.toml
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-pr-review.sh' \
  | diff - docs/auto-merge/fabro-fire-pr-review-wrapper.sh
```

A compose change needs `cd ~/fabro && docker compose up -d` to take effect — or
`docker compose up -d --build scheduler` for a scheduler code or `repos.toml` change,
which names one service and leaves `fabro` alone. The host
auto-merge switch is a server variable, so flipping it needs no deploy and no
restart; it takes effect on the next run:

```sh
# one repo
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 \
  ./ops/fabro-auto-merge-switch.sh andrewthetechie/<repo> off
# all four
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 \
  ./ops/fabro-auto-merge-switch.sh host off
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable get FABRO_AUTO_MERGE'
```

Neither undoes a merge that already happened.

**Upgrading the fabro binary is a separate, deliberate act** — never part of a
routine deploy. The image tag is pinned by `FABRO_VERSION` in `~/fabro/.env`
because an upgrade applies SQLite migrations that the previous binary then refuses
to start against, which makes rollback a database restore rather than a tag change.
`ops/README.md` has the runbook and the current known-bad version.

## The server

`ssh andrew@10.10.0.32` — a trusted single-tenant box. The compose project is in
`~/fabro`, the API is on `:32276`, and the CLI exists only inside the container:

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro events <run> -p'
```

`fabro events -p` shows which edge was selected and why, which is where a wrong-route
bug surfaces. Fire a `pr-review` run against a named PR:

```sh
~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe 123
```

That helper registers the `pr-review` package, creates the run and **starts** it —
three calls, because `POST /runs` creates a `submitted` run that never executes and
never reports anything.

The obvious-looking curl does **not** work and must not be reconstructed from the API
surface: it was `POST /automations/pr-review-<repo>/runs` with a JSON body carrying
`inputs.pr_number`. That endpoint declares **no request body** — it fires the
automation's enabled API trigger and drops whatever is sent. The `inputs` never
arrive, so compilation then fails on `{{ inputs.pr_number }}` in `validate_input`
and the call returns `422 run_compile_invalid` having created nothing. That is exactly
what the node's deliberate "`pr_number` unbound" validation warning exists to catch.
Binding `[run.inputs] pr_number` would silence the warning and turn a no-input fire
into a review of PR #1. Send no body to that endpoint, or use the helper above.

Firing `backlog` this way *is* correct, because `backlog` takes no inputs, and it does
start the run:

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOK" \
  http://10.10.0.32:32276/api/v1/automations/backlog-<repo>/runs
```

`ops/README.md` has the environments and automations tables, which schedules are
enabled, host rebuild, the profile images, and the branch sweeper.

The operational history — every deployment, each E2E result and what it did and did not
prove, and the bugs found along the way — is `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`
on the Mac, mode 600, deliberately outside this public tree. Append a dated section
rather than editing earlier ones; they are a chronological record.

## The two checkouts

`~/.fabro-deploy/fabro-workflows` is the operator's deploy checkout. This one is for
working sessions. They are the same repository, so pull before you start and push when
you finish, or the two will diverge.
