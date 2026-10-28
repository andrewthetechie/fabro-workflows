# Task 11: docs

Read `00-overview-and-contracts.md` and ADR 0031. Done with Phase 1 (after task 05). Each later
phase adds its rows in its own commit.

## Steps

1. **`CONTEXT.md`** gains three terms:
   - **Coder loop:** a `backlog` or `ralph` run, or a scheduler-leased `pr-review` run, that
     holds a box's primary lease and has precedence on it.
   - **Background run:** a run of a box-only package, started by the scheduler in a coder
     loop's gap and cancelled by its claim.
   - **Claim:** the `box-claim` hook freeing the box before a box stage.

   Use `_Avoid_` lines as the existing entries do: avoid "job" for a background run in prose
   about fabro, because a job is the scheduler's queue entry.
2. **`AGENTS.md`:**
   - add the background packages to *Layout*;
   - add the new invariant file rows;
   - add the `SCHEDULER_BACKGROUND` switch to *The server*;
   - update node and edge baselines when 09 lands.
3. **`docs/agents/invariants-graph.md`:** rows for R13-R16, the claim hook (C4), and the
   safe-to-cancel rule. **`docs/agents/invariants-host.md`:** the `-bg` rows (task 04).
4. **`ops/README.md`:**
   - the new environment variables;
   - `/api/background`;
   - the `FABRO_BOX_HEALTH` server variable;
   - a runbook section: how to turn the lane off, how to read the *Background* strip, and how
     to see why a box is not filling.
5. **`docs/README.md`:** this series' row, kept current with its status.
6. **ADR 0031:** set to *accepted* only when task 12's Phase 2 exit check passes, in that
   commit.

## Done when

- `make check` passes.
- Commit: `docs(more-local-inference): terms, invariants, runbook (more-local-inference 11)`.
