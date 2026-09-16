# Task 06 — `discord-notify.sh`: the `review-triggered` kind

**Depends on:** 04. **Blocks:** 07. **LLM level:** local is fine.

Add one kind to `.fabro/workflows/backlog/scripts/discord-notify.sh` so a trigger
failure is never silent.

## Constraints that have not changed

The script runs as a hook with `sandbox = false`, i.e. **inside the fabro server
container**. Verified tooling there: `sh`, `wget`, `git`, `tar`, `unzip`. There is
**no `bash`, no `jq`, no `curl`, no `python`, no `gh`.**

That is not a detail — it is why the bridge is a sandbox command node rather than a
hook. The hook's whole job here is to report.

Every existing invariant stays: read the webhook from
`/storage/secrets/discord_webhook_url`, read the API token from
`/storage/server.dev-token`, enrich best-effort, and **always `exit 0`** — a
notification must never fail a run.

## The new kind

`trigger_review` emits two context keys. Read them the way the script already reads
`pr_url` — one streamed pass over `/runs/$run_id/state`, cut short by `head -1` so a
state body that can be megabytes is never buffered:

```sh
review_run_id=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
  | grep -o '"review_run_id":"[^"]*"' | head -1 | cut -d'"' -f4)
review_err=$(wget -q -T 5 -O- --header="$auth" "$api/api/v1/runs/$run_id/state" 2>/dev/null \
  | grep -o '"review_trigger_error":"[^"]*"' | head -1 | cut -d'"' -f4)
```

Message selection, added to the existing `case "$kind" in` block:

```sh
review-triggered)
  if [ -n "$review_err" ]; then
    msg="🔴 fabro could not trigger a PR review${subject}: ${review_err}"
  elif [ -n "$review_run_id" ]; then
    msg="🔎 fabro triggered a PR review${subject}"
  else
    exit 0
  fi
  ;;
```

Three deliberate behaviours:

- **A failure is loud and carries the reason.** The reason is the one line
  `trigger_review` captured from the script's stderr.
- **A success is a short line**, not a second copy of the `discord-complete`
  notification that already fired for the same PR.
- **Both keys empty means the node skipped** — the double-fire guard tripped, or
  there was no usable `pr_number`. Exit silently; nothing happened worth reporting.

When `review_run_id` is set, add the run link to `links` alongside the existing ones,
using the same `$base_url/runs/$id` shape.

## Why the failure path matters more than the success path

This deployment's most expensive bugs have all been silent inert failures — the
missing `output_schema="routing"` that made every gate's routing dead "with no error
reported anywhere", and a correct review whose findings were discarded by a hardcoded
comment. A bridge that quietly stops firing is that bug again, and the symptom is an
empty review queue that nobody notices for weeks.

## Deploying it

The script is tracked here but read from the container, by absolute path. After
merging to `main`:

```sh
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'
```

Then confirm the host matches the repo — this must print nothing:

```sh
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
```

## Acceptance

- `sh -n` passes.
- Invoked by hand with a run id that has `review_trigger_error` set, it posts the
  failure line with the reason.
- Invoked for a run where the node skipped, it posts nothing and exits 0.
- The three existing kinds — `rescue`, `complete`, `failed` — behave exactly as before.
