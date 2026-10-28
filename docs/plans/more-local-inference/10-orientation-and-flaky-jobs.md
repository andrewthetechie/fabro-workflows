# Task 10: repo orientation and flaky-test investigation

Read `00-overview-and-contracts.md` first (D9, D10, D11; C6, C7). **Invoke `/fabro-workflow`.**
Read `_io/agent-guide.md` (the `FABRO_AGENT_GUIDE` block and its 3,000-byte cap), and
`docs/scout/` and `docs/flakeradar/` for the flaky issues the scout files.

**Start after task 09, in the same phase.** Both jobs are P2: they start only when no P1 job
(06, 07, 08) fits a gap.

## Goal

Long-horizon box work for the time nothing near-term needs the box:
- **Orientation:** every hosted agent explores less, because the repository has a current map.
- **Flaky investigation:** the run that later takes a scout flaky issue starts with a diagnosis.

## Steps

1. **`.fabro/workflows/repo-orientation/`**, a background package (R16), once per repository
   per night, plus after every 10 merges to `main`.
   - It writes `.fabro/orientation.md` in the target repository: a module map, conventions,
     test layout and the commands to run.
   - It delivers it as a pull request labelled `fast-path`, through the normal merge phase.
     The file is the repository's, so a human can see and edit it.
   - Cap it at 6,000 characters.
2. **Read it.** `backlog`'s and `ralph`'s `prep` copy `.fabro/orientation.md`, when present, to
   `/tmp/fabro/orientation.md`. `decompose`, `plan` and `coder` get it as an optional Stage I/O
   input (manifest entry, `required: false`).
3. **`.fabro/workflows/flaky-investigation/`**, a background package (R16), for each open
   `scout` issue labelled `flaky-test` with no investigation marker.
   - It reproduces the test, reads FlakeRadar's failure details (ADR 0028), and finds a likely
     cause.
   - It posts a marker comment with the cause and a proposed fix.
   - It **does not** push a branch: the backlog run that takes the issue does the fix.
4. **Scheduler producers** for both, at P2 priority.
5. **Tests:** producers, size caps, and the optional-input wiring in the gate tests.

## Done when

- `make check` and `uv run pytest` pass.
- Commit: `feat(background): repo orientation and flaky-test investigation jobs (more-local-inference 10)`.

**Stop here.** Report to the operator, who runs task 12, Phase 4.
