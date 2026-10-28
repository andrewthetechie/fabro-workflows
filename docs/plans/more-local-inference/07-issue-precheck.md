# Task 07: issue precheck

Read `00-overview-and-contracts.md` first (D9, D10, D11; C6, C7). **Invoke `/fabro-workflow`.**
Read `backlog`'s `decompose` prompt and `return_to_triage` node, and `docs/unmet-criteria/` (a
Contradiction is issue #17's term, `CONTEXT.md`).

**Start only after task 12's Phase 2 exit check has passed.**

## Goal

Before a queued issue spends hosted credits in `decompose`, a box check says whether it is
worth running. Since 10-20, 32 of 176 runs ended in `close_noop`, `return_to_triage` or
`mark_stuck`. Each of those spent its hosted stages first.

## Verdicts

| Verdict | Means | Scheduler does |
|---|---|---|
| `ready` | Nothing found against running it | nothing |
| `already_done` | The checkout already does what the acceptance criteria ask; it cites file and line | moves it to the bottom of the queue and labels it `precheck-already-done` for a human look |
| `blocked` | It needs an open issue's work first; it cites the issue | moves it below its blocker |
| `contradiction` | A criterion conflicts with a non-goal (#17); it quotes both | labels it `precheck-contradiction`; triage takes it |
| `unclear` | No criterion can be checked | labels it `precheck-unclear` |

A verdict is advice: the scheduler never closes or edits an issue.

## Steps

1. **`.fabro/workflows/issue-precheck/`**, a background package (R16). Inputs are
   `issue_number` and `coder_pool`.
   - `claim`: write the box file; fetch the issue with its comments.
   - `prep`: checkout at `main`, `fabro-code index`.
   - `check` (agent, `.bg`, 20m): writes `/tmp/fabro/precheck.json` through `submit`, with the
     schema `precheck` in `_io/schemas/` (`verdict`, `evidence[]`, `summary`).
   - `gate`: validates it.
   - `post`: the C6 marker comment with the verdict and evidence (at most 1,500 characters).
2. **Stage manifest, schema, generator:** `fabro-io` gains the `precheck` schema. Bump
   `min_binary` only if a new binary is needed, and say so.
3. **Scheduler producer:**
   - Enqueue a precheck for each of the top N (default 10) queued issues whose newest
     precheck marker is missing, or whose `issue_updated` or `main` SHA has changed.
   - `typical_box_minutes` 10.
   - The queue ordering in `queue.py` reads the verdicts as the table says.
4. **`decompose` reads it for free:** the comment is in `issue.json`'s comments. Add one
   sentence to `decompose.md.j2`:

   `A comment starting <!-- fabro:precheck is a local model's draft check; verify it before you rely on it.`

5. **Tests:** the producer's staleness rule, each verdict's queue effect, and the gate's
   schema checks.

## Done when

- `make check` and `uv run pytest` pass.
- Commit: `feat(issue-precheck): a box check before decompose spends credits (more-local-inference 07)`.
