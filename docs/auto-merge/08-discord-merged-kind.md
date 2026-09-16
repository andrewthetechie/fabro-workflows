# Task 08 — `discord-notify.sh`: the `merged` kind

**Depends on:** 03. **Blocks:** 09. **LLM level:** local is fine.

One kind, one hook. This is `pr-review`'s **first** `[[run.hooks]]` block.

## Constraints that have not changed

The script runs with `sandbox = false`, inside the fabro server container: `sh`,
`wget`, `git`, `tar`, `unzip`. **No `bash`, no `jq`, no `curl`, no `python`, no `gh`.**

Every existing invariant holds: webhook from `/storage/secrets/discord_webhook_url`,
token from `/storage/server.dev-token`, enrich best-effort, and **always `exit 0`** —
a notification must never fail a run.

## The enrichment needs no changes at all

This is the payoff from task 03's requirement that `report_merged` emit `pr_url` and
`issue_number` as `context_updates`.

The script already does exactly this, for `backlog`:

```sh
issue=$(wget ... "$api/api/v1/runs/$run_id/state" | grep -o '"issue_number":[0-9]*' | head -1 | cut -d: -f2)
pr_url=$(wget ... "$api/api/v1/runs/$run_id/state" | grep -o '"pr_url":"[^"]*"' | head -1 | cut -d'"' -f4)
```

`pr-review` has so far kept both in files only, so those two greps would have come back
empty and the merge notification would have carried nothing but a run link. Publishing
them to context is one line in `report_merged` and zero lines here.

The `links` block then works unchanged: `pr_url` leads, the run link follows, and the
issue link is appended because `repo_url` and `issue` are both populated.

## The message

Added to the existing `case "$kind" in`:

```sh
merged)   msg="🚀 fabro squash-merged a PR${subject}" ;;
```

Placed before the `*)` default. `$subject` is the existing `" on <repo> #<issue>"`
construction and degrades cleanly when either lookup came back empty.

Why a rocket and not a checkmark: `complete` already uses ✅ for "opened a PR". These
two events are one notification apart in a normal chain and must be distinguishable at
a glance in a Discord channel.

## The hook

New in `.fabro/workflows/pr-review/workflow.toml`:

```toml
[[run.hooks]]
id = "discord-merged"
event = "checkpoint_saved"
matcher = "^report_merged$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh merged"
```

Four deliberate choices, each with a history:

- **`checkpoint_saved`, not `stage_complete`.** The composite lifecycle runs
  git → event → hook on checkpoint, so the envelope carrying `pr_url` and
  `issue_number` has already been written when the hook fires. `stage_complete` races
  that write. `discord-complete` in `backlog` was moved here for exactly this reason,
  after first being `run_complete` and then `stage_start`.
- **Anchored matcher.** Matchers are unanchored regexes tested against `node_id`,
  `handler_type`, `edge_to`, `edge_from` and `tool_name`. `report_merged` does not
  prefix another id today, but `report_blocked` shares seven characters with it and
  the next node added might not be so lucky.
- **`blocking = false`.** A Discord outage must not stall a run — and by the time this
  fires the merge has already happened, so stalling would achieve nothing.
- **Merges only.** Operator decision 13. A blocked merge is an ordinary outcome
  reported on the PR; a squash into `main` that deploys `lawncare-saas` with no human
  involved is the event worth interrupting someone for.

## Deploying it

The script is tracked here but read from the container by absolute path:

```sh
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
```

The file lives under `backlog/scripts/` and is now read by a `pr-review` hook too. Do
not move it — `backlog`'s four hooks reference `/storage/scripts/discord-notify.sh` by
absolute path inside the container, and the tracked location is what the deploy and the
verification `diff` both key on. Note the cross-package use in
`ops/README.md` (task 11) so nobody later "tidies" it into `pr-review/scripts/`.

## Acceptance

- `sh -n` passes.
- Invoked by hand with a `pr-review` run id that reached `report_merged`, it posts the
  rocket line with the repo, the issue number, the PR link and the run link.
- The five existing kinds — `rescue`, `complete`, `failed`, `review-triggered`, and the
  `*` default — behave exactly as before.
- **Before deploying the script**, fire a run with the *stale* host copy in place and
  confirm the `*` branch posts `ℹ️ fabro run <id> notification (merged)` rather than
  failing. This is the one file in this change that still has two copies; prove the
  degradation is graceful rather than assuming it.
- `python3.11 -c 'import tomllib; …'` parses `pr-review/workflow.toml` with the new
  `[[run.hooks]]` array.
