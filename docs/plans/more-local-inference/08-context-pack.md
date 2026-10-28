# Task 08: context packs

Read `00-overview-and-contracts.md` first (D9, D10, D11; C6, C7). **Invoke `/fabro-workflow`.**
Read `backlog`'s `decompose.md.j2` and `plan.md.j2`, and `ralph`'s `plan.md.j2`.

**Start after task 07, in the same phase.**

## Goal

Hosted `decompose` and `plan` spend most of their credits exploring. Cached input was 60% of
z.ai credits in the week the quota ran out, and backlog `plan` alone was 22%. A box-built pack
of the files, symbols, tests and commands an issue touches should let them explore less. The
pack lands as an issue comment, which those stages already read.

## Steps

1. **`.fabro/workflows/context-pack/`**, a background package (R16). Inputs are `issue_number`
   and `coder_pool`.
   - `claim`, then `prep` (checkout at `main`, `fabro-code index`).
   - `gather` (agent, `.bg`, 25m). Writes `/tmp/fabro/pack.md`:
     - files with line ranges and a one-line reason each;
     - the symbols and their callers;
     - the tests that cover them;
     - the commands to run them;
     - a draft `## Task map` when the issue is big enough to split.
   - `gate`: fails a pack over **8,000 characters** (about 2k tokens). Every hosted read of the
     issue pays for the comment, so a long pack costs credits instead of saving them.
   - `post`: the C6 marker comment.
2. **Scheduler producer:**
   - Enqueue a pack for each of the top N (default 10) queued issues whose precheck verdict
     is `ready` and whose pack marker is missing or stale (same staleness rule as 07).
   - `typical_box_minutes` 20.
3. **Prompts.** One sentence each in `decompose.md.j2`, backlog `plan.md.j2` and ralph
   `plan.md.j2`:

   `A comment starting <!-- fabro:context-pack is a local model's map of the code this issue touches; start from it, verify each claim against the checkout, and do not re-explore what it already shows.`

4. **Measurement hook:** `ops/scheduler` records, per `decompose` and `plan` visit, whether the
   issue had a pack when the run started. Task 12 compares credits per visit with a pack and
   without one.
5. **Tests:** the size gate, the producer's ordering after precheck, and staleness.

## Done when

- `make check` and `uv run pytest` pass.
- Commit: `feat(context-pack): box-built code maps for decompose and plan (more-local-inference 08)`.

**Stop here.** Report to the operator, who runs task 12, Phase 3.
