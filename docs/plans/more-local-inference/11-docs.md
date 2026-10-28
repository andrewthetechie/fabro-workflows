# Task 11: docs

Read `00-overview-and-contracts.md` and ADR 0031. **The last task of Phase 1**, done after
task 05 and before Phase 1 stops. Each later phase adds its rows in its own commit before that
phase stops (tasks 06, 08 and 10 say which).

## Steps

1. **`CONTEXT.md`** gains four terms:
   - **Coder loop:** a `backlog` or `ralph` run, or a scheduler-leased `pr-review` run, that
     holds a box's primary lease and has precedence on it.
   - **Background run:** a run of a box-only package, started by the scheduler in a coder
     loop's gap and cancelled by its claim, the scheduler's eviction, or its own guard.
   - **Claim:** the `box-claim` hook freeing the box before a coder loop's box stage.
   - **Guard:** the `box-guard` hook that stops a background run before one of its agent
     stages when the box's coder loop is in a box stage.

   Use `_Avoid_` lines as the existing entries do: avoid "job" for a background run in prose
   about fabro, because a job is the scheduler's queue entry.
2. **`AGENTS.md`:**
   - add `box-noop` to *Layout* (later phases add their packages);
   - add the new invariant rows' files, if any are new;
   - add the `SCHEDULER_BACKGROUND` switch to *The server*, saying it is set in
     `~/fabro/.env` and applied with `docker compose up -d --no-deps scheduler`;
   - say that a change to `fabro-io`'s manifest structs ships before the manifest that uses it
     (D18).
3. **`docs/agents/invariants-graph.md`:** rows for R13-R16, the claim hook (C4), the guard
   hook (C10), the binary-before-data rule (D18), and the safe-to-cancel rule.
   **`docs/agents/invariants-host.md`:** the `-bg` rows (task 04), and the `FABRO_BOX_HEALTH`
   server variable (unset fails every coder-loop run).
4. **`ops/README.md`:**
   - the new environment variables and their compose lines;
   - `/api/background` and `POST /api/background/box-noop`;
   - the `FABRO_BOX_HEALTH` server variable and its provisioning;
   - a runbook section: how to turn the lane off, how to read the *Background* strip, how to
     see why a box is not filling, and how to read evictions and guard lines.
5. **`docs/README.md`:** this series' row, kept current with its status.
6. **ADR 0031:** set to *accepted* only when task 12's Phase 2 exit check passes, in that
   commit (the operator's).

## Done when

- `make check` passes.
- Commit: `docs(more-local-inference): terms, invariants, runbook (more-local-inference 11)`.

**Stop here.** Phase 1 is built. Report to the operator, who runs task 12, Phase 1.
