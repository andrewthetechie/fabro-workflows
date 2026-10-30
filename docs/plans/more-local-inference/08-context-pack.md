# Task 08: context packs

Read `00-overview-and-contracts.md` first (D9, D10, D11, D19, D21; C6, C7, C9). **Invoke
`/fabro-workflow`.** Read `backlog`'s `decompose.md.j2` and `plan.md.j2`, `ralph`'s
`plan.md.j2`, and `docs/plan-box-trial/`.

**Start after task 07, in the same phase.** Task 07 built `background.md`, which carries the
pack.

**Status (2026-10-30):** built and deployed, cut to fit the gaps (operator direction: the
precheck's 1.6-minute first run showed how much smaller a box job can be). `gather` is bounded
at about five minutes and twenty tool calls (timeout 15m), and the job is budgeted at 5
minutes, not 30 (the first took 3.7). Built here instead of in task 07: step 3's `background.md` in `claim`, and its
manifest input. The producer queues one pack per issue, ever (no staleness re-queue), in the
precheck's `SCHEDULER_PRECHECK_TOP_N` window. Step 5 records `had_pack`, `pack_run` and
`precheck_verdict` when the run is archived, from the job rows that finished before dispatch.

## Goal

Hosted `decompose` and `plan` spend most of their credits exploring. Cached input was 60% of
z.ai credits in the week the quota ran out, and backlog `plan` alone was 22%. A box-built pack
of the files, symbols, tests and commands an issue touches should let them explore less. The
pack is posted as an issue comment, and the `claim` node puts it into `background.md`.

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
   - `gate`: fails a pack over **8,000 characters** (about 2k tokens). Every `decompose` and
     `plan` visit reads the pack through `background.md`, so a long pack costs credits instead
     of saving them.
   - `post`: the C6 marker comment, edited in place when one exists.
2. **Stage manifest:** the package, with `"box": true` on `gather`; `BACKGROUND_PACKAGES`,
   `WF_ORDER`, `ENTRYPOINTS`.
3. **Scheduler producer:**
   - Enqueue a pack (priority 3, `typical_box_minutes` 30, which includes the index) for each
     of the top N (default 10) queued issues whose precheck verdict is `ready` and whose pack
     marker is missing or stale (C6 *Staleness*).
4. **Prompts.** `decompose.md.j2` already lists `background.md` (task 07). Add one sentence to
   backlog `plan.md.j2` and ralph `plan.md.j2`, in their input lists:

   `background.md may hold a code map a local model built for this issue; start from it, verify each claim against the checkout, and do not re-explore what it already shows.`

5. **Measurement record (D21).** At each issue dispatch, the scheduler records on the
   `run_history` row whether the issue had a fresh pack marker (`had_pack` 0 or 1), and the
   pack marker's `run=`. Task 12 joins it with the per-node tokens the scheduler already stores
   (`run_stage_stats`: `input_tokens`, `output_tokens`, `cache_read_tokens`) to compare credits per `decompose` and `plan` visit with a pack and
   without, stratified by the box-plan trial's arm.
6. **Tests:** the size gate, the producer's ordering after precheck, staleness, and the
   `had_pack` record.

## Done when

- `make check` and `uv run pytest` pass.
- Commit: `feat(context-pack): box-built code maps for decompose and plan (more-local-inference 08)`.

Then add task 11's Phase 3 rows (the two packages in `AGENTS.md` *Layout*, `background.md` and
the precheck holds in `docs/agents/invariants-backlog.md`, the held list in `ops/README.md`), in
their own commit, and **stop**. Report to the operator, who runs task 12, Phase 3.
