# Task 05 — backlog `workflow.toml`: env injection and the notify hook

**Depends on:** 01, 04. **Blocks:** 07. **LLM level:** local is fine.

Two additions to `.fabro/workflows/backlog/workflow.toml`.

## Part 1 — inject the API token

```toml
# The trigger_review stage calls the fabro API to create a pr-review run for the PR
# this run just opened. The token is a vault entry resolved at launch; only its name
# appears here, because this repository is public.
#
# [run.environment.*] is a SPARSE OVERRIDE on the environment the automation already
# selected. Do NOT add `[run.environment] id = "..."` — that would pin all four
# backlog automations to one environment and break the per-repo image mapping
# (jelly-swipe needs python, writers-app needs rust-node, and buildpack-deps:noble
# fails CI on two of the four).
[run.environment.env]
FABRO_API_TOKEN = "{{ secrets.FABRO_API_TOKEN }}"
```

That `id` warning is the main trap in this task. `RunEnvironmentLayer` carries `id`,
`image`, `resources`, `network`, `lifecycle`, `labels` and `env`; every one of them
is a sparse override, and `env` merges by key across layers. Setting only `env`
leaves the automation's `environment_id` selection intact.

`{{ secrets.* }}` resolves from the server vault immediately before the worker
executes, fails closed when the entry is missing or is not a token, and the resolved
value is never persisted into the run definition. Task 01 proves this reaches a
command stage.

**This goes in backlog's `workflow.toml` only.** `pr-review` is not modified by this
stage; it has no reason to hold an API token.

## Part 2 — report the trigger outcome

```toml
# trigger_review emits review_run_id / review_trigger_error and cannot fail the run
# (on_failure="succeed"), so `run_failed` will never fire for a trigger problem.
# Without this hook a bridge that has quietly stopped firing is invisible until
# someone notices an empty review queue.
#
# Anchor the matcher: fabro_hooks compiles `matcher` as an unanchored regex and tests
# it against node_id, handler_type, edge_to, edge_from and tool_name.
[[run.hooks]]
id = "discord-review-triggered"
event = "checkpoint_saved"
matcher = "^trigger_review$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh review-triggered"
```

`checkpoint_saved`, not `stage_complete`: the composite lifecycle runs
git → event → hook on checkpoint, so the checkpoint envelope carrying
`review_run_id` has already been written by the time the hook runs. This is the same
race the `discord-complete` hook was moved to `checkpoint_saved` to avoid — it went
`run_complete` → `stage_start` → `stage_complete` → `checkpoint_saved` before landing
correctly. Do not re-walk that path.

`blocking = false` so a Discord outage cannot stall a run. `sandbox = false` so it
runs in the server container, where the webhook URL and dev token already live.

## Verifying the TOML

`fabro validate` does **not** parse `workflow.toml` strictly. A dotted key that loses
its quotes becomes a nested table and the automation fire returns 422 with nothing
reported until then. Check separately, on 3.11+ — macOS ships 3.9, which has no
`tomllib`:

```sh
python3.11 -c 'import tomllib; print(tomllib.load(open(".fabro/workflows/backlog/workflow.toml","rb")))'
```

Confirm in the parsed output that `run.environment.env.FABRO_API_TOKEN` is a string
and that there is **no** `run.environment.id` key.

## Acceptance

- `tomllib` parses the file and shows `run.environment.env` with one key.
- No `run.environment.id`.
- Four hooks total: `discord-rescue`, `discord-complete`, `discord-failed`,
  `discord-review-triggered`.
- The literal token value appears nowhere in the repository.
