# Task 05 — `workflow.toml`: permissions, branches, variables, hooks

**Depends on:** 03. **Blocks:** 08. **LLM level:** ordinary.

Create `.fabro/workflows/issue-triage/workflow.toml`.

```toml
_version = 1

[workflow]
graph = "workflow.fabro"

# No [run.model.fallbacks]. `high-reasoning` resolves in LiteLLM, which owns the
# overflow to kimi-k3; a second chain here would race it and make the effective route
# unknowable from either side. See docs/adr/0003-high-reasoning-alias.md.

# Triage reads code and writes issues. It never writes code, so `contents` is "read".
# `issues` must be "write": the run creates labels (needs-info, triage-in-progress,
# agent), edits titles and bodies, and comments. Repository labels are the Issues
# permission in GitHub's model.
[run.integrations.github.permissions]
contents = "read"
issues = "write"

# Nothing is committed and nothing is pushed. Leaving either push on leaks one dead
# branch per run into the target repository, and a push would fail anyway under a
# contents: read token.
[run.run_branch]
push = false

[run.meta_branch]
push = false

# The capacity check calls the fabro API. Server variable, not ${X}: shell-style
# interpolation is NOT performed here and arrives as literal text — the failure that
# pinned the auto-merge switch off forever. An unset variable fails the RunIntent at
# compile time, so both must be provisioned before the first fire (task 07).
[run.environment.env]
FABRO_API_URL   = "{{ vars.FABRO_API_URL }}"
FABRO_API_TOKEN = "{{ secrets.FABRO_API_TOKEN }}"

# Discord. Hooks run with sandbox = false, inside the fabro server container (Alpine:
# /bin/sh, wget, no bash, no jq, no curl), and read the webhook URL from
# /storage/secrets/discord_webhook_url. The script is referenced by absolute container
# path because hook `script` is a shell command, not an `@` file import.
#
# stage_start on the gate fires just BEFORE it blocks, which is what makes the ping
# useful: it lands as the question opens. There is no hook event for run.blocked —
# HookEvent has sixteen variants and none of them is blocked, unblocked or interview.
#
# Anchored matchers: they are unanchored regexes tested against node_id, handler_type,
# edge_to, edge_from and tool_name. `^human$` would match every gate by handler_type.
[[run.hooks]]
id = "discord-triage-question"
event = "stage_start"
matcher = "^ask_human$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh triage-question"

# The terminal-failure path. Not run_failed: `release` exits zero and routes to exit,
# mirroring backlog's mark_stuck, so the run ends succeeded and run_failed never fires.
[[run.hooks]]
id = "discord-triage-failed"
event = "stage_start"
matcher = "^release$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh triage-failed"

# Belt and braces for a failure that never reaches `release` at all — a sandbox that
# fails to start, a compile error, a token rejected at admission.
[[run.hooks]]
id = "discord-failed"
event = "run_failed"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh failed"
```

## What is deliberately absent

- **No `[run.environment] id`.** `[run.environment.*]` is a sparse override on the
  environment the automation already selected. Setting `id` here would pin all four
  automations to one image and break the per-repo mapping, and it also fails `fabro
  validate`, which resolves a non-default id against the CLI's own local catalog.
- **No `[run.clone] depth`.** The default of 100 is enough for triage, which reads code
  and recent history rather than doing branch-point arithmetic. If a triage pass is
  visibly hampered by a truncated `git log`, `depth = 0` is the one-line change — but it
  is not free on four repositories every hour.
- **No `[run.inputs]`.** The workflow takes none; it selects its own issue.

## Acceptance

- `python3.11 -c 'import tomllib; print(tomllib.load(open(".fabro/workflows/issue-triage/workflow.toml","rb")))'`
  parses, and the three hook tables come back as a list of three. `fabro validate` does
  not parse this file strictly; a key that loses its quotes becomes a nested table and
  the automation fire returns 422 with nothing reported until then.
- `fabro preflight .fabro/workflows/issue-triage/workflow.toml` resolves the model, the
  environment and both `[run.environment.env]` values against the live server.
- A run fired before `FABRO_API_URL` exists as a server variable fails at compile time
  with "Run config variable interpolation failed" and creates nothing. Confirm that is
  the failure mode, not a run that starts and quietly cannot tell whether the host is
  busy.
