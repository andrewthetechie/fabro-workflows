# AGENTS.md

Two production Fabro workflows and the ops tooling for the host that runs them.

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
| `.fabro/workflows/<name>/` | **The only tree the automations read.** Two packages: `backlog`, `pr-review`. `backlog/scripts/` is executed by a live stage as well as by hooks, so changing it is a deploy. |
| `ops/` | Host replication: compose, profile images, provisioning, branch sweeper. Start at `ops/README.md`. No automation reads this tree. |
| `docs/pr-review-bridge/` | The task series for the third stage: `backlog` triggers a `pr-review` run on the PR it just opened. `00-overview-and-contracts.md` first. |
| `docs/<workflow>/` | The numbered task series each workflow was built from — operator decisions, file contracts, and the reasoning behind every non-obvious choice. |
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

Baselines as of 2026-09-15: `Backlog (37 nodes, 85 edges)` clean, and
`PrReview (19 nodes, 40 edges)` with exactly one warning — `pr_number` unbound in
`validate_input`. That warning is deliberate. Binding `[run.inputs] pr_number` would
silence it and let a run fired with no input review PR #1 instead of failing at
admission.

`fabro validate` does not parse `workflow.toml` strictly. A dotted model key that loses
its quotes becomes a nested table and the automation fire returns 422, with nothing
reported until then. Check it separately, on 3.11+ — macOS ships 3.9, which has no
`tomllib`:

```sh
python3.11 -c 'import tomllib; print(tomllib.load(open("workflow.toml","rb")))'
```

## Deployment invariants

These pass `fabro validate` and fail at runtime. Both workflows depend on all of them.

| Rule | What happens otherwise |
|---|---|
| `output_schema="routing"` on every command node whose script prints `context_updates` | Fabro never scans that node's stdout. Routing goes inert, the run walks its unconditional edges, and no error appears anywhere. This cost one live debugging round already. |
| Model stylesheet class selectors use `[a-z0-9-]` only — `.rebase-t2`, never `.rebase_t2` | `expected '{' after selector` |
| `\"` is the only backslash in a `.fabro` file | The DOT parser turns `\n` into a real newline, so `printf '%s\n'` becomes `printf '%sn'` and jq's `"\n"` becomes an unterminated string. Get a newline inside an embedded jq program with `([10]\|implode)`. |
| `//` starts a comment; `#` only ever appears inside a quoted string | `#` is a DOT comment. `Resolves #N` and `##` headings inside a string are fine — never strip one to make a grep pass. |
| Inline scripts are POSIX `sh` | No `[[ ]]`, no `pipefail`, no arrays. `sh -n` catches this, but it is blind inside `jq '...'` — compile embedded jq programs separately. |
| Every command node's unconditional edge lands on the workflow's terminal-failure node | A failed node still routes, and it takes its *unconditional* edge. Where that edge is the happy path, fold `\|\| outcome=failed` into the escape edge's condition. |
| Reset per-iteration context keys, and write every key on both branches | A stale key from an earlier loop can satisfy an edge meant for this one. |
| Delete a contract file before the agent that writes it runs | An agent that exits succeeded without writing hands the gate its predecessor's result. |
| Agents do not run `git` | Two deliberate exceptions: `pr-review`'s rebase agent and `backlog`'s `resolve_merge` agent, which need `git add` and `--continue`. |
| A conflicted merge or rebase never crosses a stage boundary | The checkpoint is `git add -A && git commit`, and `git add` marks a conflicted file resolved — so the checkpoint commits conflict markers. The command node aborts to restore a clean tree; a dedicated agent then redoes and resolves the whole thing inside one stage. |
| Anchor hook matchers | They are unanchored regexes tested against `node_id`, `handler_type`, `edge_to`, `edge_from` and `tool_name`. Write `^open_pr$`, not `open_pr`, for any id that prefixes another. |
| `[run.environment.env]` in backlog's `workflow.toml` must never gain an `id` key | It pins all four backlog automations to one environment, and two of the four repos fail CI on the wrong image. It also breaks `fabro validate`, which resolves a non-default id against the CLI's own local catalog and errors. |
| `trigger_review` keeps `on_failure="succeed"` | Its single unconditional edge points at `exit`. A failed node still routes and takes its *unconditional* edge, so without the attribute a trigger failure routes to `exit` instead of to `human_rescue`. |

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

# the operator's manual-fire tool. The trigger node reads its own copy from a fresh
# clone of `main`, so only this host copy needs deploying.
scp .fabro/workflows/backlog/scripts/fire-pr-review.sh \
  andrew@10.10.0.32:~/bin/fabro-fire-pr-review.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-pr-review.sh'

scp ops/docker-compose.yaml andrew@10.10.0.32:~/fabro/docker-compose.yaml
scp ops/fabro-branch-sweep.sh andrew@10.10.0.32:~/bin/fabro-branch-sweep.sh

# automations, when the provisioning script changed or a row is missing
FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh
```

Then confirm the host still matches the repo. Every one of these should print
nothing:

```sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-branch-sweep.sh' | diff - ops/fabro-branch-sweep.sh
ssh andrew@10.10.0.32 'cat ~/fabro/docker-compose.yaml'  | diff - ops/docker-compose.yaml
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-pr-review.sh' \
  | diff - .fabro/workflows/backlog/scripts/fire-pr-review.sh
```

A compose change needs `cd ~/fabro && docker compose up -d` to take effect.

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
