# Task 07 — `workflow.toml`, Discord hooks, branch-push policy, dead-file cleanup

**Depends on:** tasks 02–06 (the files this TOML points at must exist).
**Do before:** task 08.
**LLM level:** local is fine — every change is spelled out.

## Goal

Rewrite `workflow.toml` for the redesigned graph, fix the Discord notification hooks
so they stop firing on every empty run, stop fabro leaking a metadata branch into the
target repository on every run, and delete the files the redesign orphaned.

All paths are under
`~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog/` unless stated otherwise.

## Step 1 — Rewrite `workflow.toml`

Replace the whole file with this. It is the current file plus a new fallback table, a
new `[run.meta_branch]` block, and one changed hook.

```toml
_version = 1

[workflow]
graph = "workflow.fabro"

# Model escalation fallbacks. A model that errors or is rate-limited falls
# through to the next one. Order follows the operator's quota policy:
# z.ai before kimi. long-context and kimi-k3 never fall back into a coder
# role, and kimi-k3 is never a primary coder.
#
# Keys containing a dot MUST be quoted or the TOML parse fails with a 422 at
# automation fire time.
[run.model.fallbacks]
coders = ["litellm:glm-4.7"]
"glm-4.7" = ["litellm:kimi-for-coding"]
kimi-for-coding = ["litellm:glm-5.3"]
"glm-5.3" = ["litellm:kimi-k3"]
kimi-k3 = ["litellm:glm-5.3"]
long-context = ["litellm:kimi-k3"]

# Do not push the run metadata branch. Fabro pushes fabro/meta/<run_id> to the
# target repository on every run and never deletes it; nothing reads the pushed
# copy (run metadata is served from the fabro API and the run store). Leaving
# this on accumulates one dead branch per run forever. See task 12.
# run_branch.push stays at its default (true) so in-progress work survives a
# server crash; task 12's sweeper reaps the run branches instead.
[run.meta_branch]
push = false

# The run needs a GitHub token for the gh CLI in acquire/claim/validate/open_pr
# command stages and for agent use of gh. Fabro injects GITHUB_TOKEN into the
# sandbox; these permissions declare what a token-mode run may do.
[run.integrations.github.permissions]
contents = "write"
issues = "write"
pull_requests = "write"

# Discord notifications. Hooks run with sandbox = false, which means they run
# inside the fabro server container (Alpine: /bin/sh, wget, no bash, no jq, no
# curl). The webhook URL is read from /storage/secrets/discord_webhook_url
# inside that container, so no secret enters this repo or this TOML. The script
# is referenced by absolute container path because hook `script` is a shell
# command, not an `@` file import.
[[run.hooks]]
id = "discord-rescue"
event = "stage_start"
matcher = "human_rescue"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh rescue"

# Fires when open_pr's CHECKPOINT is saved — i.e. the run actually produced a
# pull request AND the checkpoint carrying `pr_url` has been persisted, so the
# notification can link it with no race. The composite lifecycle runs
# git -> event -> hook on checkpoint (fabro-workflow lifecycle/mod.rs), so the
# event lifecycle has already written the checkpoint envelope by the time this
# hook runs.
#
# History: this was `run_complete`, which has no matcher support and so fired on
# every run including the ~384/day that quiet-exit at `acquire`; then
# `stage_start`, which fires before the PR exists; then `stage_complete`, which
# races the checkpoint write that publishes `pr_url`.
[[run.hooks]]
id = "discord-complete"
event = "checkpoint_saved"
# Anchor the matcher: fabro_hooks compiles `matcher` as an unanchored regex
# and tests it against node_id, so `open_pr` would ALSO match `open_pr_prep`
# and notify twice. `^open_pr$` matches only the PR-opening stage.
matcher = "^open_pr$"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh complete"

[[run.hooks]]
id = "discord-failed"
event = "run_failed"
blocking = false
sandbox = false
script = "/storage/scripts/discord-notify.sh failed"
```

Notes:

- `#` comments are fine in TOML. They are **only** forbidden in `workflow.fabro`
  (the DOT graph), which uses `//`.
- **Matchers are unanchored regexes tested against five fields** (`node_id`,
  `handler_type`, `edge_to`, `edge_from`, `tool_name`). Anchor any matcher whose
  value is a prefix of another node id. `human_rescue` needs no anchor — no other
  node id contains it — but note it will also match the `stage_start` of whatever
  node a rescue routes **to** (`rework_t4`, `open_pr_prep`, `mark_stuck`), because
  `edge_from` is one of the tested fields. That produces one extra notification
  after a rescue has already fired; anchoring cannot prevent it.
- `run_failed` and `run_complete` have no matcher support, which is why
  `discord-complete` had to move to a `stage_start` matcher. `discord-failed` stays on
  `run_failed` and is filtered inside the script instead (step 2).
- Do **not** add a `[run.pull_request]` block. The workflow opens its own PR in the
  `open_pr` stage with `gh pr create`. Adding `[run.pull_request]` would also make
  `[run.run_branch] push = false` a hard config error, which task 12 may want later.

## Step 2 — Update the Discord notify script

The script is **not** on the Docker host. `/storage` is the named Docker volume
`fabro-storage` mounted into the fabro container; the script exists only at
`/storage/scripts/discord-notify.sh` **inside** the container. It survives
`docker compose restart` and `docker compose up -d --force-recreate` because the
volume is named, but it is not in git.

Two changes:

1. `failed` must exit silently when the run was **cancelled**. A cancelled run is an
   operator action, not a failure worth a notification. The hook's stdin carries the
   `HookContext` JSON (`event`, `run_id`, `status`, `failure_reason`, …). The script
   currently drains stdin to `/dev/null`; capture it instead and skip on a
   case-insensitive `cancelled` match.
2. `complete` now fires at the **start** of `open_pr`, so its wording should say the
   run is opening a PR rather than that it completed.

Apply it like this (from `ssh andrew@10.10.0.32`):

```sh
docker exec fabro-fabro-1 cp /storage/scripts/discord-notify.sh /storage/scripts/discord-notify.sh.prev
docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh > /tmp/discord-notify.sh
# edit /tmp/discord-notify.sh per the two changes below
docker cp /tmp/discord-notify.sh fabro-fabro-1:/storage/scripts/discord-notify.sh
docker exec fabro-fabro-1 chmod +x /storage/scripts/discord-notify.sh
docker exec fabro-fabro-1 chown fabro:fabro /storage/scripts/discord-notify.sh
```

The two edits, against the existing POSIX-sh script:

Replace the stdin drain:

```sh
# was:
cat > /dev/null 2>&1 || true

# becomes:
ctx="$(cat 2>/dev/null || true)"
case "$kind" in
  failed)
    if printf '%s' "$ctx" | grep -qi 'cancel'; then exit 0; fi
    ;;
esac
```

Change the `complete` message:

```sh
complete) msg="✅ fabro run ${run_id} finished its work — opening a PR" ;;
```

Keep everything else: `set -u`, the `[ -f "$url_file" ] || exit 0` guard, the
`curl`-then-`wget` fallback, and the unconditional `exit 0`. The container is Alpine
with busybox — `grep`, `printf`, and `case` are all available; `jq`, `bash`, and
`curl` are not, so do not reach for them.

Verify the script still parses inside the container:

```sh
docker exec fabro-fabro-1 sh -n /storage/scripts/discord-notify.sh && echo "syntax ok"
docker exec fabro-fabro-1 sh -c 'echo "{\"status\":\"cancelled\"}" | /storage/scripts/discord-notify.sh failed' && echo "cancelled path exited 0 (expect no Discord message)"
```

## Step 3 — Delete the orphaned files

The redesign removes the plan stage, the readiness stage, the fixup stage, and every
JSON schema. Delete all of it so nothing in the repo describes a contract that no
longer exists — a future reader (human or model) that finds `verdict.schema.json` will
believe the wrong verdict shape.

```sh
cd ~/.fabro-deploy/fabro-workflows/.fabro/workflows/backlog
rm -f prompts/readiness.md.j2
rm -f prompts/plan.md.j2
rm -f prompts/fixup.md.j2
rm -rf schemas/
rm -f scripts/discord-notify.sh.bak
```

Then confirm exactly the right set remains:

```sh
find . -type f | sort
```

Expected, and nothing else:

```
./prompts/coder.md.j2
./prompts/decompose.md.j2
./prompts/extra_decompose.md.j2
./prompts/improve.md.j2
./prompts/quality.md.j2
./prompts/reviewer.md.j2
./prompts/rework.md.j2
./prompts/spec.md.j2
./prompts/standards.md.j2
./scripts/discord-notify.sh
./workflow.fabro
./workflow.toml
```

`scripts/discord-notify.sh` stays in the repo as the source of record for what is
installed in the container, even though the hook runs the container copy. If you
edited the container copy in step 2, copy the same content back into
`scripts/discord-notify.sh` so the two do not drift.

## Done when

- `workflow.toml` matches step 1 exactly.
- `python3.11 -c 'import tomllib; tomllib.load(open("workflow.toml","rb"))'` exits 0.
  (The Mac's default `python3` is 3.9 and has no `tomllib`; use 3.11+ or run it on the host.)
- `grep -n 'run_complete' workflow.toml` returns nothing outside comments.
- `grep -n 'matcher' workflow.toml` shows `^open_pr$`, not a bare `open_pr`.
- `grep -n 'meta_branch' workflow.toml` shows `push = false`.
- The container script passes `sh -n` and exits 0 on a cancelled-context `failed` call.
- `find . -type f | sort` matches the 12-file list above.
- `grep -rn 'output_schema\|@schemas' .` returns nothing.

## Pitfalls

- **Quote the dotted keys.** `glm-4.7 = [...]` unquoted parses as a nested table
  `glm` → `4` → `7` or fails outright. The automation fire returns 422 and the run
  never starts. `fabro validate` does **not** catch TOML errors — that is why the
  `tomllib` check above is a separate gate.
- `kimi-for-coding` and `long-context` have no dots and must **not** be quoted for
  consistency's sake — either form is valid TOML, but match the block above.
- Do not point a fallback at a model that does not exist yet. Task 01 adds `glm-4.7`
  and `kimi-for-coding`; if task 01 has not run, every fallback edge to them is dead.
- The `[run.meta_branch] push = false` line is a **workflow-level override of a server
  default**. Verify it actually takes effect in task 09 by confirming no
  `fabro/meta/<run_id>` branch appears on the test repository after the E2E run. If it
  is ignored, move the same block into the server's `/storage/.home/settings.toml`
  instead and note it in task 11.
- Deleting `schemas/` is safe **only after** tasks 02–06 have landed, because the old
  `workflow.fabro` and the old prompts reference `@schemas/...`. Do step 3 last.
