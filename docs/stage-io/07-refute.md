# 07 · Migrate `refute`: reads, output and Sealed paths

Read `00-overview-and-contracts.md` first. The contracts for this task are C2, C3, C4, C5
and C7. Read ADR 0013 (the Refuter) and `_shared/review-merge/review-merge.fabro`
`refute_prep`, `refute`, `refute_gate`. Invoke `/fabro-workflow` before you edit the graph.

`refute` is first because it is the only stage with Sealed paths, it has one output, and its
gate is new and small. One commit.

## Changes

1. **Manifest.** `review-merge.refute`: `"live": true`. The inputs in this order: `issues`
   (`refute_issues.json`), `pr` (`pr.json`), `diff` (`refute_diff.patch`), `omitted`
   (`refute_omitted.txt`, required, may be empty), `hygiene` (`hygiene.json`). Output
   `/tmp/fabro/review/refute.json`, schema `refute`. Sealed: C2's list. Run
   `ops/fabro-io-manifest.py generate`.
2. **Schema.** `refute.schema.json` expresses everything that `refute_gate`'s first `jq`
   program checks: `summary` string; `criteria` array, `minItems: 1`, each item with
   `criterion` and `evidence` as non-empty strings and `status` in the enum
   `met | not_met | unverifiable`; `defects` array, each item with `file` (a non-empty
   string), `scenario` (a non-empty string) and `line` (integer ≥ 1, or null).
   `additionalProperties: false`. Add a `description` on each field, taken from the prompt:
   the model reads it as the parameter's documentation.
3. **Prompt** (`refute.md.j2`):
   - Replace the "Inputs" table with: "Call `inputs` first. It returns the issues that this
     PR closes, the PR, its diff, the changed files that did not fit in the diff, and the
     diff-hygiene counters. When it ends with `MORE:` lines, request each of those parts
     before you judge. `submit` refuses until you have read every part."
   - Replace the paragraph "**Do not read anything else under `/tmp/fabro/`** ..." with
     one sentence that names no file: "Work only from what `inputs` returns and from the
     checkout. Other agents' opinions about this PR exist, and you must not look for them.
     Your independence from them is the reason you exist."
   - Replace "Output" and its "Rules" with: "Call `submit` with your `summary`, `criteria`
     and `defects`. It checks the shape and tells you what to fix. Do not write the file
     yourself: a result that `submit` did not write is refused." Keep the rules that are
     judgement, not shape: no verdict and no score; `unverifiable` when unsure; do not mark
     a criterion `met` to let the PR pass.
   - If the spike chose `shell`: use `fabro-io inputs` and
     `fabro-io submit --file /tmp/fabro/review/refute.draft.json` instead, and keep the JSON
     shape example.
4. **`refute_prep`.** It already deletes `refute.json`. Also delete `refute.draft.json`.
5. **`refute_gate`.** Put C7's receipt check first, inside the existing repair branch: the
   same `refute_attempts` counter and the same "invalid twice, no verdict" end. Remove the
   first `jq` shape program: the schema now does that, and a valid receipt proves the schema
   passed. Keep the verdict computation and `refute_reason.txt` unchanged.
6. **The guard.** It is already wired (task 06). With `refute` live it now has an entry.

## Tests in `ops/test-task-gates.sh`

Update the `refute_gate` section. Generate the fixtures with the binary (`cargo run --
submit` with `FABRO_IO_ROOT`), and commit them under `ops/fabro-io/tests/fixtures/`. The
section stages `stage.json` with the fixture's visit:

1. A valid receipt, every criterion `met`, no defects → `pass`.
2. A valid receipt, one `not_met` → `fail`, and `refute_reason.txt` names it.
3. The same content with no `_io` (hand-written) → exit 1 with the "submit tool" message;
   the second time → `invalid`.
4. A valid receipt with a different visit in `stage.json` (a stale file) → repair.
5. `stage.json` missing → repair, never `pass`.

Add a section for the guard (skip it when `fabro-io` is not on `PATH`): a hook context for
`read_file` of `/tmp/fabro/review/standards.json` under node `review_merge.refute` is
blocked, and the same call under `review_merge.spec` proceeds.

## Verification

The overview's rules, then all of `11`'s offline checks. After the push, on the first run
that reaches `review_merge.refute`:

- `agent.tools.available` lists `mcp__io__inputs` and `mcp__io__submit`;
- the first tool call is `mcp__io__inputs`, and every `MORE:` part is requested;
- `refute.json` carries `_io`, and `refute_gate` routes on the verdict;
- `ops/fabro-exploration-share.py`: `sealed_reads` is 0, and the guard's `block` count is
  reported. A block is not an error. Read what the Refuter was looking for.
