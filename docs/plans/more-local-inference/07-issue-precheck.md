# Task 07: issue precheck, and the `background.md` input

Read `00-overview-and-contracts.md` first (D9, D10, D11, D19, D20; C6, C7, C9). **Invoke
`/fabro-workflow`.** Read `backlog`'s `claim` node and `decompose` prompt, `ralph`'s `claim`
and `plan` prompt, the `return_to_triage` node, `ops/check-graph-invariants.py` R10,
`ops/scheduler/src/fabro_scheduler/queue.py` and `github.py`, and `docs/unmet-criteria/` (a
Contradiction is issue #17's term, `CONTEXT.md`).

**Start only when the overview's Status line records Phase 2 as passed.**

## Goal

Before a queued issue spends hosted credits in `decompose`, a box check says whether it is
worth running. Since 10-20, 32 of 176 runs ended in `close_noop`, `return_to_triage` or
`mark_stuck`. Each of those spent its hosted stages first.

This task also builds the path every background output takes into a hosted stage:
`/tmp/fabro/background.md` (C9). Issue comments do **not** reach `decompose` or `plan`, because
`issue.json` carries no comments (*Facts*).

## Verdicts

| Verdict | Means | Scheduler does (D20) |
|---|---|---|
| `ready` | Nothing found against running it | nothing |
| `already_done` | The checkout already does what the acceptance criteria ask; it cites file and line | adds `precheck-already-done`; holds the issue |
| `blocked` | It needs another open issue in the same repository first; it cites the number | adds `precheck-blocked`; holds the issue while the blocker is open, then removes the label |
| `contradiction` | A criterion conflicts with a non-goal (#17); it quotes both | adds `precheck-contradiction`; holds the issue |
| `unclear` | No criterion can be checked | adds `precheck-unclear`; holds the issue |

- A held issue is not dispatched while its `precheck-*` label is on it. It is listed on the
  queue page under *Held by precheck*, with the verdict's summary and a link to the comment. A
  human removes the label to release it, and the scheduler then does not hold it again for the
  same `body=` hash.
- A verdict other than `ready` with an empty `evidence` list is treated as `ready`.
- `blocked` that cites an issue in another repository, or one that does not exist, is treated
  as `ready`.
- The scheduler never closes an issue, edits its body, or removes `agent`.

## Steps

1. **`.fabro/workflows/issue-precheck/`**, a background package (R16). Inputs are
   `issue_number` and `coder_pool`.
   - `claim`: write the box file; fetch the issue with its comments (this package's own fetch;
     `gh issue view --json ...,comments`).
   - `prep`: checkout at `main`, `fabro-code index`.
   - `check` (agent, `.bg`, 20m): writes `/tmp/fabro/precheck.json` through `submit`, with the
     schema `precheck` in `_io/schemas/` (`verdict`, `evidence[]`, `summary`, and
     `blocker` (an issue number) when the verdict is `blocked`).
   - `gate`: validates it.
   - `post`: the C6 marker comment with the verdict and evidence (at most 1,500 characters),
     editing the issue's existing precheck comment in place when there is one.
2. **Stage manifest, schema, generator:** the package with `"box": true` on `check`; the
   `precheck` schema in `_io/schemas/` and `fabro-io`. Bump `min_binary` only if a new binary
   is needed, and say so; if so, the operator deploys images before pushing (as D18).
3. **`/tmp/fabro/background.md` (C9).** In `backlog`'s `claim`, after `issue.json` is written,
   add the comment fetch and the file write exactly as C9 gives them. Nothing goes to stdout,
   and no failure fails the node.
   - Make the identical edit to `ralph`'s `claim` (R10).
   - Add `background.md` (`/tmp/fabro/background.md`, `required: false`, with an `about` and
     an `absent` sentence) to the manifest inputs of `backlog` `decompose` and `plan` and
     `ralph` `plan`.
   - Add one sentence to `backlog`'s `decompose.md.j2` input list:

     `background.md: notes local models wrote about this issue (a precheck verdict, a code map, a flaky-test diagnosis). Treat each as a draft: verify it before you rely on it.`

   - Gate tests (`ops/gate_tests/`): `claim` with a stubbed `gh` that returns no marker, one
     marker of each kind, two precheck markers (the newest wins), an oversize pack (cut at the
     cap), and a failing `gh api`. Each: stdout unchanged, routing unchanged.
4. **Scheduler producer:**
   - Enqueue a precheck (priority 2, `typical_box_minutes` 10) for each of the top N (default
     10, `SCHEDULER_PRECHECK_TOP_N`) queued issues whose precheck marker is missing or stale
     (C6 *Staleness*). Compute `body=` the same way in Python and in the package; a test pins
     both to one fixture.
   - `queue.py`: held issues (the table above) leave the ranked queue and appear in a
     *held* list. `github.py` writes and removes the `precheck-*` labels, using the existing
     label-write path.
   - `ENTRYPOINTS` gains `issue-precheck`.
5. **Tests:** the producer's staleness rule (body change, `main` moved within and after 24 h,
   the job's own comment not counting), each verdict's queue effect, the human release, the
   evidence and cross-repo `blocked` rules, and the gate's schema checks.

## Done when

- `make check` and `uv run pytest` pass.
- Commit: `feat(issue-precheck): a box check before decompose spends credits (more-local-inference 07)`.
