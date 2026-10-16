# Task 06: docs

Read `00-overview-and-contracts.md` first (D10, D14) and ADR 0027.

## Goal

Every document that describes the packages, the lanes or the protected config files says
what changed. No code changes except one comment in `backlog`'s graph.

## Steps

1. **`docs/adr/0023-fast-path-lane.md`**: add a section at the end, `## Amendment (2026-10-16, ADR 0027): the Scout selects the lane for a Ratchet rule`.
   D1 said only a human adds `fast-path`. From now on the **Scout** adds it to a Ratchet rule
   issue too, because that issue is generated from a template, is bounded by ADR 0027 D3, and
   its checks run in `ci.sh`. A flaky-job issue does not get it. Triage still never adds it. Do
   not edit D1's own text: the ADR is a record.
2. **`.fabro/workflows/backlog/workflow.fabro`**, the comment above the lane routing (around
   line 331, "An issue labelled `fast-path` beside `agent` was chosen by a human..."): add one
   sentence saying the Scout also adds the label to Ratchet rule issues (ADR 0027). Change
   comments only. The script must not change: `git diff` shows `//` lines only.
3. **`AGENTS.md`**:
   - the opening line: seven runnable packages (count them, and say "Five production" only
     if it is still true; it is not, so make the line correct);
   - the Layout table's first row: add `scout` to the list of runnable packages, with a short
     clause saying it has no agent stage, like `merge-queue`;
   - the Baselines paragraph: add `Scout (9 nodes, 11 edges)` as **unverified** ("counted by
     hand; `make check-host` confirms it in task 07 of `docs/scout/`");
   - the Server section: one sentence. A `scout-<repo>` run fires from its daily schedule
     (created disabled) or its `api:manual` trigger, and it takes no input.
4. **`docs/agents/invariants-merge.md`**: wherever `config_changed`'s three files are listed,
   add `.fabro/scout.toml` (ADR 0027 D14). Run `grep -rn 'test.toml' docs/agents` to find every
   other mention of the list, and update each.
5. **`CONTEXT.md`**: the **Automation** entry says "Four per repo, sixteen total". Make it
   correct (seven per repo, twenty-eight), and add `scout` to the list of enabled-schedule
   exceptions only after task 07 enables them. For now, say the `scout` schedules are created
   disabled.
6. **`docs/README.md`**: the `docs/scout/` row exists. Change its status to
   **"Tasks 01-06 done <date>; task 07 (deploy, dry runs, go live, measure) is the operator's."**
7. **This folder's `00-overview-and-contracts.md`**: update the Status line the same way.

## Done when

- `make check` passes.
- `git diff --stat` touches only the files above.
- One commit: `docs(scout): ADR 0023 amendment, AGENTS.md, invariants, glossary, index (scout 06, #32)`.
- **Stop here and report**, with the 9/11 count and anything in tasks 01-06 that disagreed with
  `00-overview-and-contracts.md`.
