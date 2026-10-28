# Task 10: repo orientation and flaky-test investigation

Read `00-overview-and-contracts.md` first (D9, D10, D11, D19, D22; C6, C7, C9). **Invoke
`/fabro-workflow`.** Read `_io/agent-guide.md` (the `FABRO_AGENT_GUIDE` block and its
3,000-byte cap), `ops/check-graph-invariants.py` R10, and `docs/scout/` and `docs/flakeradar/`
for the `flaky-test` issues the scout files.

**Start after task 09, in the same phase.** Both jobs have the lowest real priority (4): they
start only when no PR doctor, precheck or pack job fits a gap.

## Goal

Long-horizon box work for the time nothing near-term needs the box:
- **Orientation:** every hosted agent explores less, because the repository has a current map.
- **Flaky investigation:** the run that later takes a scout flaky issue starts with a diagnosis.

Neither job opens a pull request or calls a hosted model.

## Steps

1. **`.fabro/workflows/repo-orientation/`**, a background package (R16). Input: `coder_pool`.
   - It writes a map of the repository at `main`: modules, conventions, test layout and the
     commands to run. Cap it at 6,000 characters (a `gate` node).
   - It delivers it as the **body of one pinned issue per repository** (D22), titled
     `fabro: repository orientation` and labelled `fabro-orientation` only. The body starts with
     the C6 orientation marker. The job finds the issue by that label; when none exists it
     creates it and pins it. It edits the body in place on every later run.
   - The issue must never carry `agent`, `scout` or any label another automation selects on.
     Check `issue-triage` and `arch-review` selection before you choose the label, and add a
     checker or gate test that no workflow selects `fabro-orientation`.
2. **Read it.** `backlog`'s and `ralph`'s `prep` fetch that issue's body, when it exists, to
   `/tmp/fabro/orientation.md` (one `gh issue list --label fabro-orientation` call, failure
   tolerated, nothing on stdout). Make the identical edit in both (R10). `decompose`, `plan` and
   `coder` get it as an optional Stage I/O input (manifest entry, `required: false`, with an
   `absent` sentence).
3. **`.fabro/workflows/flaky-investigation/`**, a background package (R16). Input:
   `issue_number`, `coder_pool`. For each open issue labelled `flaky-test` with no
   investigation marker:
   - It reproduces the test, reads FlakeRadar's failure details (ADR 0028), and finds a likely
     cause.
   - It posts the C6 marker comment with the cause and a proposed fix (at most 2,500
     characters), editing in place on a rerun.
   - It **does not** push a branch: the backlog run that takes the issue does the fix, and it
     reads the diagnosis through `background.md` (C9 already includes this kind).
4. **Scheduler producers**, priority 4:
   - orientation: once per repository per night, and after every 10 merges to `main` since the
     marker's `main=`; `typical_box_minutes` 30;
   - flaky investigation: as step 3 selects; `typical_box_minutes` 30.
   - `ENTRYPOINTS` gains both packages.
5. **Tests:** producers, size caps, the label rule, and the optional-input wiring in the gate
   tests.

## Done when

- `make check` and `uv run pytest` pass.
- Commit: `feat(background): repo orientation and flaky-test investigation jobs (more-local-inference 10)`.

Then add task 11's Phase 4 rows (both packages in `AGENTS.md` *Layout*, the orientation issue
in `ops/README.md`, the new `Backlog` baseline), in their own commit, and **stop**. Report to the
operator, who runs task 12, Phase 4.
