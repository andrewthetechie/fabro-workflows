# Task 05 — `workflow.toml` and the four automations

**Depends on:** 01–04. **Blocks:** 06, 07. **LLM level:** local is fine.

## Part 1 — `workflow.toml`

Write `.fabro/workflows/pr-review/workflow.toml`:

```toml
_version = 1

[workflow]
graph = "workflow.fabro"

# One model for review and fixes; coders only for the first rebase attempt.
# Quoted keys are mandatory where the name contains a dot, or TOML builds a
# nested table and the automation fire returns 422.
[run.model.fallbacks]
"glm-5.3" = ["litellm:kimi-k3"]
coders = ["litellm:glm-5.3"]
kimi-k3 = ["litellm:glm-5.3"]

# The run works on the PR head branch, not on fabro/run/<id>.
#   enabled = true  -> checkpoints still commit between stages, onto the PR branch
#   push    = false -> fabro never pushes the stale fabro/run/<id>; `deliver` pushes
# Legal only because this workflow declares no pull-request block. Do not add one.
[run.run_branch]
enabled = true
push = false

# Nothing reads a pushed metadata branch; leaving it on leaks one branch per run.
[run.meta_branch]
push = false

# Full history. `depth` defaults to 100 (RunCloneSettings::DEFAULT_DEPTH), and
# `depth = 0`
# means unlimited. A shallow clone breaks every git operation this workflow is built
# on: `git diff origin/<base>...HEAD` dies with `fatal: no merge base` when the branch
# point is behind the graft, `git log origin/<base>..HEAD` lists commits that are
# already on the base branch, `merge-base --is-ancestor` answers no when the answer is
# yes, and `git rebase` silently drops commits as "patch contents already upstream".
# `claim` also runs `git fetch --unshallow` as a belt-and-braces guard; keep both.
[run.clone]
depth = 0

# `issues` must be "write". `claim` runs `gh label create` for the two terminal
# labels, and repository labels are the Issues permission in GitHub's model; with
# "read" every label call fails, and since they are all `|| true` the run still
# reports success while the terminal state degrades to comment-only.
[run.integrations.github.permissions]
contents = "write"
issues = "write"
pull_requests = "write"
```

The key is `depth`, not `n`. `RunCloneLayer` is `#[serde(deny_unknown_fields)]` with
fields `enabled` and `depth`, so `n = 0` fails outright:
`unknown field `n`, expected `enabled` or `depth``. There is a test named
`zero_clone_depth_requests_full_history`.

**Do not bind `[run.inputs] pr_number`.** It would clear task 06's validation warnings
but would also let a run fired with no input silently review PR #1. Unset means a hard
error at run admission, which is the behaviour we want.

No hooks in v1. The Discord notifier is wired to backlog's node ids
(`^open_pr$`, `human_rescue`) and none of them exist here; adding notifications is a
follow-up once the workflow has run a few times.

Verify with a 3.11+ interpreter — macOS ships 3.9, which has no `tomllib`:

```sh
python3.11 -c 'import tomllib; d=tomllib.load(open("workflow.toml","rb")); print(sorted(d["run"]["model"]["fallbacks"]))'
```

Expect `['coders', 'glm-5.3', 'kimi-k3']`. Also check the clone depth:

```sh
python3.11 -c 'import tomllib; print(tomllib.load(open("workflow.toml","rb"))["run"]["clone"])'
```

Expect `{'depth': 0}`.

## Part 2 — four automations

One per repo, mirroring `backlog-*`, so each PR review runs in an image that can build
that repo. A single automation on `default` (`buildpack-deps:noble`) has no cargo and no
bun and would fail CI on two of the four.

| id | target repo | environment_id |
|---|---|---|
| `pr-review-jelly-swipe` | `andrewthetechie/jelly-swipe` | `python` |
| `pr-review-lawncare-saas` | `andrewthetechie/lawncare-saas` | `python-node` |
| `pr-review-womens-fantasy-sports` | `andrewthetechie/womens-fantasy-sports` | `ts` |
| `pr-review-writers-app` | `andrewthetechie/writers-app` | `rust-node` |

Create them with `POST /api/v1/automations` (`operationId: createAutomation`). Each:

```json
{
  "id": "pr-review-<repo>",
  "name": "pr-review-<repo>",
  "environment_id": "<env>",
  "target": {"kind": "git", "repo": "andrewthetechie/<repo>", "branch": "main"},
  "workflow": "pr-review",
  "workflow_source": {"repo": "andrewthetechie/fabro-workflows", "branch": "main"},
  "triggers": [{"type": "api", "id": "manual", "enabled": true}]
}
```

**No schedule trigger.** This workflow is fired against a named PR; there is nothing
for a cron to poll — that is exactly the Sandcastle behaviour being dropped.

`ops/provision-server-state.sh` does not exist yet — create it, covering both the four
`pr-review-*` and the four `backlog-*` automations, so a rebuilt host gets both sets.

The `backlog-*` rows are reconstructed, not read back from the live server, so before
trusting the script to rebuild a host, diff it against production:

```sh
curl -sS -H "Authorization: Bearer $TOK" "$API/automations" \
  | jq -r '.data[] | "\(.id)\t\(.environment_id)\t\([.triggers[]|"\(.type):\(.id):\(.enabled)"]|join(","))"'
```

The script creates backlog schedules **disabled**. If production's are enabled, a host
rebuilt from this script comes back with backlog not running.

## Firing a run

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"trigger":"manual","inputs":{"pr_number":376}}' \
  http://<HOST>:32276/api/v1/automations/pr-review-jelly-swipe/runs
```

## Done when

- `workflow.toml` exists and parses on 3.11+ with the three expected fallback keys.
- `grep -c 'run.inputs' workflow.toml` is 0.
- `grep -cE '^\s*\[run\.pull_request\]' workflow.toml` is 0. Anchored to the table
  header: an unanchored `grep -c 'run.pull_request'` also matches the prose explaining
  why there is no such block, so it cannot be satisfied while keeping the comment
  readable.
- All four automations exist with the right `environment_id`, an enabled `api` trigger,
  and no schedule trigger.
- `ops/provision-server-state.sh` covers all eight, reports `environment_id` drift
  instead of silently passing, and exits non-zero when it cannot list automations.

## Pitfalls

- `"glm-5.3"` must be quoted. Unquoted it becomes a nested `glm.5.3` table and the
  automation fire returns 422 — and `fabro validate` does **not** check TOML.
- Do not copy backlog's `[[run.hooks]]` across. Its matchers name nodes that do not
  exist in this graph, and an unanchored matcher is how the Discord spam bug happened.
- Environment ids must already exist on the server. Check
  `GET /api/v1/environments` before creating automations that reference them.
- The provisioning script is a bootstrap, not a reconciler. "Exists" is not "correct":
  it must at least report a row whose `environment_id` does not match, or this task's
  done-when ("with the right `environment_id`") is unverifiable.
- Do not pipe `curl` straight into `jq` to test for existence. The pipeline takes jq's
  status, so an API outage reads as "not present" and the script POSTs into a
  misleading 409.
