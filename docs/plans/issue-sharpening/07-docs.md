# Task 07: docs

Read `00-overview-and-contracts.md` first and ADR 0033. Read the `writing-for-agents` skill
before editing `AGENTS.md`.

## Steps

1. **`AGENTS.md`**:
   - *Layout*: the package count, with `issue-sharpen` as a **leased box-only package**: a coder
     lease, not a background kind (ADR 0033).
   - *The server*: one paragraph. The scheduler holds `unverifiable` issues as
     `precheck-unverifiable` and sharpens them, one lease at a time, ahead of issue runs, during
     peak hours too; it is always on; the off path is a revert and `make deploy-scheduler`. Name
     the measure section.
   - The `IssueSharpen` baseline row added in task 03 stays marked unverified.
2. **`docs/agents/invariants-graph.md`** (or a new `invariants-sharpen.md`, listed in
   `AGENTS.md`): the rules that pass `fabro validate` and fail at runtime:
   - R17;
   - the lens inputs must stay inside `audit/`;
   - the `recheck` R10 copy;
   - the hash formula is shared by shell and Python, pinned by test;
   - `record`'s `fail` edge is what makes D12 work;
   - `post` never fails the run after an apply.
3. **`docs/agents/invariants-host.md`**: the sharpen run's models are the `-bg` rows, with no
   fallback (D1).
4. **`ops/README.md`** *The coder scheduler*: the sharpen lease, the table, the hold, the
   back-fill, retries, how to cancel one (the pool's cancel button), how to release a stuck hold
   by hand (remove the label and set the row `superseded`), the queue page's *Sharpening* strip,
   and the measure section.
5. **`docs/USER-GUIDE.md`**: under *Writing criteria the factory can verify*, a short paragraph.
   An issue the precheck rates `unverifiable` is rewritten by the factory itself. The original is
   kept in a `fabro:sharpen-original` comment, and the change table is in the `fabro:sharpen`
   comment. Never add `<!-- fabro:sharpen` text by hand.
6. **`docs/README.md`**: set the `docs/plans/issue-sharpening/` row's status to "built, not
   deployed".
7. **`skills/plan-factory-issues/fixtures/README.md`**: one line saying the sharpen gate tests use
   the corpus, and where.

## Done when

- `make check` passes.
- The commit is `docs(issue-sharpening): runbook, invariants and guide (issue-sharpening 07)`.
- Then **stop** and report as the overview's hard rule says.
