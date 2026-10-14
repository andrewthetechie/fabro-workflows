# 02: `return_to_triage`, and `decompose` hands a Contradiction back (U7-U10, C3, C4)

## Changes

1. `backlog/workflow.fabro`: new command node `return_to_triage` (C4), declared next to
   `mark_stuck`, with a `//` comment saying why it exists and why it ends succeeded. The issue
   number is read the way `mark_stuck` reads it (`issue.json`, then `issue_number`). Send `gh`
   output to `/tmp/fabro/return.log`, never to stdout. Build the routing object with
   `jq -nc --arg`. The comment body is built by jq from `return.json` into a file.
   Edges: `return_to_triage -> exit [condition="outcome=succeeded"]` and
   `return_to_triage -> mark_stuck`.
2. `mark_stuck`: its `stuck_reason.md` prefix says "The decomposer reported:". Make it
   neutral ("The run reported:") now that `return_to_triage` also writes that file.
3. `_io/schemas/decomposition.schema.json`: add `contradiction` to the `status` enum, and the
   optional `contradictions` array (C1 entry shape). Regenerate the manifest.
4. `decompose_gate`: accept `contradiction` (C3). Valid only with `issues: []` and a non-empty,
   well-formed `contradictions`. On it, write `return.json` with `source: "decompose"` and
   `why` taken from `summary`. Publish `triage_questions` (one `- criterion / non-goal` line per
   item, `"` removed, cut to 1200 bytes, as `triage_gate` does). Edge:
   `decompose_gate -> return_to_triage [condition="context.decomp_status=contradiction"]`.
   Every routing object `decompose_gate` prints keeps a `triage_questions` key (empty when not
   on this path), so a stale value never reaches the hook.
5. `backlog/prompts/decompose.md.j2`: a Contradiction (criterion against non-goal) is the
   status `contradiction` with the `contradictions` array. It is not `needs_human_review`,
   which stays for every other reason a human is needed (for example an unlanded `Blocked by`).
6. `backlog/workflow.toml`: the `discord-triage-question` hook (C4).

## Tests (`ops/test-task-gates.sh`)

- `decompose_gate`: `contradiction` with an entry gives `decomp_status=contradiction` and
  writes `return.json`. `contradiction` with issues, or with no entries, is refused.
  `needs_human_review` still writes `stuck_reason.md`.
- `return_to_triage` with `gh` stubbed: the comment's first line is the marker, and the
  label call removes `agent` and `agent-in-progress` and adds `needs-info`.
  `return_reason` is one line of at most 300 bytes and survives a reason holding braces and
  quotes. With `gh` failing, the node exits non-zero and `stuck_reason.md` exists.
- The parsed edges: `return_to_triage`'s only edges are to `exit` (succeeded) and
  `mark_stuck`.

## Done when

`make check` passes.
