# Task 02 — `fire-pr-review.sh`

**Depends on:** 00. **Blocks:** 03, 04, 09. **LLM level:** smarter model recommended
— the `RunIntent` and `WorkflowVersion` payload shapes need iteration against a live
server.

## Where this file lives, and why

**Canonical path: `.fabro/workflows/backlog/scripts/fire-pr-review.sh`.**

Not `ops/`. Task 04's node executes this script out of the clone it already makes, so
the script is read **by a live automation** — and `ops/` is defined by `AGENTS.md` as
the tree that "no automation reads", which is what makes editing `ops/` safe. Putting
it there would quietly turn every `ops/` edit into a live deploy.

This mirrors the existing precedent exactly: `discord-notify.sh` is tracked under
`.fabro/workflows/backlog/scripts/` and *deployed* elsewhere. For operator use, task
09 documents deploying this one to `~/bin/fabro-fire-pr-review.sh` on the host,
alongside `fabro-branch-sweep.sh`.

Write the script that registers the `pr-review` package as a workflow version and
creates a run against a named PR. It is **two deliverables in one**:

- the operator's manual-fire tool, replacing the recipe in `AGENTS.md` that
  finding 1 disproved, and
- the test harness for task 03, and the reference implementation the graph node in
  task 04 mirrors.

Write this **before** touching any workflow. Every failure mode of the API sequence
surfaces here in seconds instead of forty minutes into a backlog run.

## Usage

```sh
.fabro/workflows/backlog/scripts/fire-pr-review.sh <owner/repo> <pr_number>
# on the host, after task 09's deploy step:
~/bin/fabro-fire-pr-review.sh <owner/repo> <pr_number>
```

Environment:

| Variable | Default | Meaning |
|---|---|---|
| `FABRO_API_URL` | `http://10.10.0.32:32276/api/v1` | includes the `/api/v1` prefix |
| `FABRO_API_TOKEN` | — | required; never echoed |
| `WORKFLOWS_REPO` | `andrewthetechie/fabro-workflows` | source of the `pr-review` package |
| `WORKFLOWS_REF` | `main` | the ref to register |
| `PARENT_RUN_ID` | empty | set by the graph node; omitted on manual fires |
| `DRY_RUN` | `1` | print the payloads and stop, in the house style of `fabro-branch-sweep.sh` |
| `ISSUE_NUMBER` | empty | optional; adds an `issue` label so lineage stays greppable when `parent_id` is omitted. Ignored when non-numeric — a manual fire has no issue |
| `CHECK_PR` | `1` | pre-flight that the PR exists. Only a definitive 404 is fatal; a 403 rate limit, a 5xx or no network warns and continues |

`DRY_RUN=1` by default. This script creates real runs that push to real
repositories; the sweeper's convention is the right one.

## Steps

### 1. Fetch the package

```sh
git clone --depth 1 --branch "$WORKFLOWS_REF" \
  "https://github.com/$WORKFLOWS_REPO" "$tmp/wf"
pkg="$tmp/wf/.fabro/workflows/pr-review"
```

The repo is public, so no credential is involved. Inside a sandbox this also avoids
depending on the run's `GITHUB_TOKEN` scope.

### 2. Build the `WorkflowVersion` payload

```sh
find "$pkg" -type f | sed "s|^$pkg/||" | sort
```

**Enumerate from the directory.** A hardcoded file list silently drops a prompt added
to `pr-review` later, and the run then fails at admission on an unresolved `@prompts`
reference.

Build `files` as a map of canonical path to file contents, with `jq -Rs` doing the
JSON string encoding — never hand-rolled escaping. The package is full of `\"` and
embedded jq programs and will not survive `sed`-based quoting.

```json
{
  "entrypoint": "<canonical entrypoint path>",
  "files": { "workflow.toml": "...", "workflow.fabro": "...", "prompts/spec.md.j2": "..." },
  "workflow_dependencies": {}
}
```

`workflow_dependencies` is `{}`: `pr-review` declares no child workflow.

`POST $FABRO_API_URL/workflow-versions` → `{"workflow_version_id": "..."}`.

The endpoint is content-addressed and idempotent, so re-registering unchanged content
returns the same id. Expect 201; handle 400, 413, 422 and 500 with the response body
in the error message.

> The exact spelling of `entrypoint` and of the `files` keys is the part most likely
> to need a round trip against the server. `WorkflowPath` keys "receive stricter
> domain validation" — if a key is rejected, the 422 body names it.

### 3. Resolve per-repo configuration

```sh
curl -fsS -H "$auth" "$FABRO_API_URL/automations" \
  | jq --arg repo "$1" '.data[]
      | select(.workflow == "pr-review" and .target.repo == $repo)'
```

Take `environment_id` and `target` from that row. Do not hardcode the map: a fifth
repo should need no change here, and a repo with no `pr-review` automation must be
skipped with a clear message rather than defaulting.

`environment_id` is **not** optional in practice — omitting it selects `default`
(`buildpack-deps:noble`), which cannot build two of the four repos.

### 4. Create the run

`POST $FABRO_API_URL/runs` with a `RunIntent`:

```json
{
  "workflow_version_id": "...",
  "target": { "kind": "git", "repo": "andrewthetechie/jelly-swipe", "branch": "main" },
  "args": {
    "inputs": { "pr_number": 123 },
    "labels": { "source": "backlog", "pr": "123" }
  },
  "environment_id": "python",
  "parent_id": "01M2..."
}
```

- `pr_number` is a **JSON number**, not a string. `RunIntentArgs.inputs` values may be
  string, number or integer, and `validate_input` runs `case "$PR" in *[!0-9]*)`.
- Omit `parent_id` entirely when `PARENT_RUN_ID` is empty or does not match
  `^[0-9A-HJKMNP-TV-Z]{26}$`. Sending a malformed one fails the whole create.

### 5. Start the run

**`POST /runs` creates but does not start.** The run comes back `submitted` with zero
stages and stays there forever, reporting nothing. The sequence is three POSTs, and
the third is not optional:

```sh
POST $FABRO_API_URL/runs/$run_id/start     # no request body
```

Expect 200. Handle 404 (unknown id) and 409 (not in `submitted`) distinctly, and say
in the message that the run *was* created — an operator who sees only "could not
start" will otherwise fire again and create a second one.

### 6. Report

Print the created run id, the HTTP status, and `lifecycle.queue_position` — read from
the **start** response, not the create response, because the start call is the first
point the scheduler has seen the run. The queue position is the evidence that settles
whether fabro queues or rejects at `max_concurrent_runs = 3` — it has never been
exercised in this deployment, and nobody will go looking for it later unless it is
already in a log.

## Constraints

- POSIX `sh` is not required — this runs on the host and in the sandbox, both of
  which have `bash`, `jq`, `curl` and `git`. The **server container** has none of
  those, which is precisely why the bridge is not a hook.
- Never `set -x`, and never interpolate the token into a printed string. `curl -H
  "Authorization: Bearer $FABRO_API_TOKEN"` is fine; echoing the command is not.
- No secret, host token or webhook URL enters this file. It is tracked in a public
  repo.
- Exit non-zero with a one-line reason on every failure path; task 04's node turns
  that line into the Discord alert.

## Acceptance

- `DRY_RUN=1` prints both payloads with the token absent from the output.
- Re-running against unchanged package content returns the **same**
  `workflow_version_id`.
- A repo with no `pr-review` automation exits non-zero with a message naming it.
- A `DRY_RUN=0` fire leaves the created run in `runnable`, never `submitted`.

> **Correction, 2026-09-15.** The step list above originally stopped at `POST /runs`,
> which produces review runs that never execute. Task 03's first fire hit it. The
> shipped script does all three calls; see `.scratch/pr-review-bridge-02-evidence.md`.
