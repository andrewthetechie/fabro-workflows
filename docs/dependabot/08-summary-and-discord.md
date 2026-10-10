# 08. Summary and Discord

## Outcome

Every run ends with one Discord message:

```
🛡️ fabro dependabot on andrewthetechie/jelly-swipe
PRs: merged 4 of 6 (#442 #441 #440 #438); #439 red (test); #437 conflict, rebase requested
Patch: urllib3 in uv.lock (3 alerts) → #445, merged
Left: 2 alerts with no patched version (#12 #15); 1 group skipped (brace-expansion: needs-human)
```

A run that stopped early says where: `Stopped at dep_validate: <reason>`. A quiet day
says `Nothing to do: 0 alerts open, 0 Dependabot PRs open.` The patch PR's own
`merged`, `blocked` and `needs-human` messages come from the shared phase's hooks
(task 07), and they now carry the tracking issue number.

## Why

This is D19 and D24. The operator reads one line per repository per day, and must be
able to tell a quiet day from a broken one without opening fabro.

## Change

Read 00: D19, D24, C5, C9. Read `discord-notify.sh` in full, and in particular how
`arch-summary` reads `arch_summary`: `tail -1`, with quotes and backslashes stripped,
cut to 800 bytes.

### 1. `summarize` (routing, always exits 0)

It builds the four lines above from the files that exist, with one jq program. Every
input is optional:

| Line | Source |
|---|---|
| `PRs:` | `results.json` (C5), merged first, then the rest in queue order, each with its outcome and a short reason (the first 60 characters of `reason`). Omitted when `prs.json` is `[]`. |
| `Patch:` | `group.json`, `pr_url` from `/tmp/fabro/pr_number`, `/tmp/fabro/review_merge_outcome`, and `patch.json`'s status. It also covers the cases with no PR: `blocked: <blocker, 80 chars>`, `red after 3 attempts`, and `no group to patch`. |
| `Left:` | `no_patch.json`, `skipped_groups.json`. |
| `Stopped at` | `/tmp/fabro/dep/stopped_at`, when non-empty, followed by `/tmp/fabro/dep/stop_reason`. Every command node from tasks 02-07 gets a `stop() { printf '%s' "$1" > /tmp/fabro/dep/stop_reason; echo "$1" >&2; exit 1; }` helper, and uses it on each deliberate failure path, so stderr still reaches `fabro events`. Add it in this task. A node that dies outside `stop` leaves only `stopped_at`, which is enough to find the node. |

Rules:

- Join the lines with ` | ` in the context key, because the hook reads one JSON string.
  The notify script turns ` | ` back into `\n`.
- Strip `"` and `\` from the result, and cut it to 1,500 bytes. Publish `dep_summary`
  and `stopped_at`.
- Print the full multi-line summary to stdout as well, so `fabro events` shows it.

### 2. `track_issue` publishes `issue_number`

Give `track_issue` `output_schema="routing"`, and end it with
`{"context_updates":{"issue_number":N}}`. The `merged`, `blocked` and `needs-human`
kinds read `issue_number` from the run state, so their messages then read
`on andrewthetechie/jelly-swipe #412`. Without it they still send, just with no issue
number.

### 3. `discord-notify.sh`: the `dependabot-summary` kind

Mirror `arch-summary` exactly:

- Add it to the usage line.
- Read `dep_summary` with the same `grep -o … | tail -1 | cut -d'"' -f4`. Strip it the
  same way, and cut it at 1,500 bytes.
- Set `issue=""` for it.
- Message: `🛡️ fabro dependabot${subject}\n` followed by `dep_summary`, with ` | `
  replaced by `\n` (`sed 's/ | /\\n/g'`: this file is shell, not `.fabro`, so a
  backslash is fine here).

Changing this file is a deploy (`make deploy-notify`). Task 10 does it before the first
fire.

### 4. The hook

In `dependabot/workflow.toml`, add:

```toml
[[run.hooks]]
id = "discord-dependabot-summary"
event = "stage_start"
matcher = "^finish$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh dependabot-summary"
```

Copy `arch-review`'s comment that explains why the hook is on `finish` and not on
`summarize`.

## Acceptance

- `check-graph-invariants.py` is clean (R5: the matcher is anchored).
- `./ops/test-task-gates.sh` passes the new `summarize` section.
- `sh -n .fabro/workflows/backlog/scripts/discord-notify.sh` passes. The new kind,
  run with a stubbed `wget` that returns a state containing `dep_summary`, prints a
  payload whose message has four lines.

## Tests

Add `dependabot — summarize`.

1. **The full day.** The fixture files that produce the example above: `dep_summary`
   equals the expected string exactly.
2. **A quiet day.** Only `alerts.json` = `[]` and `prs.json` = `[]`: the "Nothing to do"
   line.
3. **Stopped.** `stopped_at = dep_validate` and a `stop_reason`: the line begins
   `Stopped at dep_validate:` and ends with the reason.
4. **Quotes in a reason.** A C5 reason with `"` and `\`: neither is in `dep_summary`,
   and the routing JSON is valid.
5. **Length.** 100 PRs in `results.json`: `dep_summary` is ≤ 1,500 bytes, and the
   routing object is still valid JSON.

Extend the existing notify-script test, if there is one, with the new kind. Otherwise
add the stubbed-`wget` check from *Acceptance* to the same section.

## Depends on

07.
