# Task 04 — backlog graph: `pr_number` file and the `trigger_review` node

**Depends on:** 02, 03. **Blocks:** 07. **LLM level:** smarter model recommended —
DOT escaping and validation iteration.

Two changes to `.fabro/workflows/backlog/workflow.fabro`. Nothing else in the graph
moves.

## Part 1 — `open_pr` writes the PR number to a file

`open_pr` already has `$PR_URL` in hand and emits `pr_url` through `context_updates`.
The trigger node cannot read that: command `script` substitution supports
`{{ goal }}`, `{{ inputs.* }}` and `{{ vars.* }}` **only** — there is no
`{{ context.* }}` — and `stdin_source` pipes exactly one key.

So use the file-backed contract both workflows are built on. `pr-review` states the
same rule for itself: *"every later stage reads it from here rather than
re-interpolating."*

Add one line to `open_pr`, immediately after `PR_URL` is set:

```sh
echo $PR_URL | sed 's|.*/||' > /tmp/fabro/pr_number
```

Backslashes are illegal in a `.fabro` file, so this `sed` is written with `|`
delimiters and no escapes. Verify it on a real URL before trusting it:
`https://github.com/o/r/pull/123` must yield `123`.

Leave the existing `pr_url` `context_updates` exactly as they are — `discord-notify.sh`
reads that key, and the `discord-complete` hook depends on it.

## Part 2 — the `trigger_review` node

```dot
    trigger_review [label="Trigger PR review", shape=parallelogram,
        output_schema="routing", on_failure="succeed", timeout="10m",
        script="if [ -f /tmp/fabro/review_triggered ]; then
  echo 'a review was already triggered for this run' >&2
  echo '{\"context_updates\":{\"review_run_id\":\"\",\"review_trigger_error\":\"\"}}'
  exit 0
fi
PR=$(cat /tmp/fabro/pr_number 2>/dev/null || echo '')
case \"$PR\" in '' | *[!0-9]*)
  echo '{\"context_updates\":{\"review_run_id\":\"\",\"review_trigger_error\":\"no usable pr_number was recorded by open_pr\"}}'
  exit 0 ;;
esac
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo '')
RID=$(basename $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo ''))
if [ ${#RID} -ne 26 ]; then RID=''; fi
case \"$RID\" in *[!0-9A-Z]*) RID='' ;; esac
rm -rf /tmp/fabro/wfrepo
if ! git clone --depth 1 https://github.com/andrewthetechie/fabro-workflows /tmp/fabro/wfrepo > /dev/null 2>&1; then
  echo '{\"context_updates\":{\"review_run_id\":\"\",\"review_trigger_error\":\"could not clone fabro-workflows to build the pr-review package\"}}'
  exit 0
fi
S=/tmp/fabro/wfrepo/.fabro/workflows/backlog/scripts/fire-pr-review.sh
PARENT_RUN_ID=$RID DRY_RUN=0 sh $S $REPO $PR > /tmp/fabro/trigger_out 2> /tmp/fabro/trigger_err
if [ $? -eq 0 ]; then
  touch /tmp/fabro/review_triggered
  R=$(cat /tmp/fabro/trigger_out | tr -d '\r' | tail -1)
  echo '{\"context_updates\":{\"review_run_id\":\"'$R'\",\"review_trigger_error\":\"\"}}'
else
  E=$(tail -1 /tmp/fabro/trigger_err | tr -d '\r' | cut -c1-200)
  echo '{\"context_updates\":{\"review_run_id\":\"\",\"review_trigger_error\":\"'$E'\"}}'
fi"]
```

### Rules this node is obeying

| Rule | How |
|---|---|
| `output_schema="routing"` on every `context_updates` emitter | present — without it routing goes inert with no error anywhere |
| Write every key on both branches | `review_run_id` and `review_trigger_error` are always both emitted, so a stale key from an earlier visit can never satisfy a condition meant for this one |
| `\"` is the only backslash | no `sed` escapes, no `\n`; `gh repo view --json` replaces URL-mangling with a structured query |
| POSIX `sh` | `case`, `${#VAR}`, `$?`; no `[[ ]]`, no arrays, no `pipefail` |
| Never print a secret | the token is never echoed and never reaches a log; **do not add `set -x`** |
| `#` only inside quoted strings | there is no `#` in this node at all |

### Why `on_failure="succeed"` is right here, and what it costs

The PR already exists when this node runs. A backlog run that produced a correct PR
must not be reported as failed because a downstream trigger could not reach the API.

The cost is that the standard rule — *every command node's unconditional edge lands
on the terminal-failure node* — does not apply, because this node cannot reach a
failed outcome. Its single unconditional edge to `exit` is therefore safe **only for
as long as `on_failure="succeed"` is on it.** If anyone removes that attribute, the
edge must be re-pointed at `human_rescue` in the same change.

It also means `run_failed` never fires for a trigger failure, which is why task 06
exists. Belt and braces: the script's own failures are caught above and turned into
`review_trigger_error` rather than a non-zero node exit, so the normal path emits
context even when the trigger fails.

### The double-fire guard

`/tmp/fabro/review_triggered` is written only after a successful fire. `open_pr` can
be visited twice — `open_pr → human_rescue → [P] Accept partial → open_pr_prep →
open_pr` — and two concurrent reviews on one PR would both
`git push --force-with-lease` to the same head ref: one wins, the other is rejected
and lands an `ai-review-needs-human` label on a PR that reviewed fine.

A marker file was chosen over `max_visits=1` because file-backed contracts are this
deployment's architecture, the skip is visible in the stage log, and `max_visits`
has murky semantics on a failed visit — which is exactly the state `on_failure="succeed"`
puts this node in.

## Part 3 — edges

Replace:

```dot
    open_pr -> exit                 [condition="outcome=succeeded"]
    open_pr -> human_rescue
```

with:

```dot
    open_pr -> trigger_review       [condition="outcome=succeeded"]
    open_pr -> human_rescue
    trigger_review -> exit
```

`human_rescue -> open_pr_prep [label="[P] Accept partial"]` is unchanged, so the
accept-partial path reaches the trigger through `open_pr` like any other.

## Acceptance

- `fabro validate` in the container reports `Backlog (38 nodes, 86 edges)` — one node
  and one edge more than the 2026-09-15 baseline of 37/85 — with no new warnings.
- `sh -n` passes on the node's script extracted to a file.
- Any embedded jq is compiled separately; `sh -n` cannot see inside `jq '...'`.
- `echo https://github.com/o/r/pull/123 | sed 's|.*/||'` prints `123`.
