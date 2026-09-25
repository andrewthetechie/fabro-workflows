# 06 · Documentation, the dead-end re-check, and the canary watch

## Outcome
The repository describes the host as it now is (0.362), the new invariants are in
`AGENTS.md`, `docs/research_improvements/04` is re-checked against 0.362, the deployment log
has its dated entry, and the first two `backlog` runs on each coder box (the canaries,
decision 3) have been watched to their end, with any fabro-caused failure fixed forward.

## Read first
`00-overview-and-contracts.md`, all of it. The reports of tasks 01–05.

## Part A: `AGENTS.md`

Edit in place. Keep each invariant's "what happens otherwise" column concrete, the way the
existing rows are.

1. **Version.** Every place that says the host runs 0.354 (the `docs/` rows, the settings-
   overlay invariants, "Upgrading the fabro binary is a separate, deliberate act") now says
   0.362 and points to `docs/fabro-upgrade/` for how it got there.
2. **Validating.** The baselines are unchanged (finding R4), so say "re-confirmed on 0.362,
   2026-09-xx". The `fabro preflight` paragraph stays true.
3. **New or changed rows in *Deployment invariants*:**
   - **Catalog shape.** "A provider table uses `codecs = [...]` (default `["openai-chat"]`),
     never `codec` or a protocol adapter id (`openai-compatible`, `openai`, `anthropic`,
     `gemini`)." Otherwise: a cold start refuses to boot (finding R2), and a hot reload is
     rejected and the old catalog kept, the silent 2026-09-20 failure. Keep the
     `Rejected reloaded` check in the existing `display_name` row.
   - **Built-in duplicates of our model ids.** "Do not store a key for `openrouter`,
     `fireworks`, `vercel` or `venice` without re-running `fabro model test` for every
     stylesheet id." Otherwise: those built-ins also carry `glm-5.3`/`kimi-k3` (finding R5),
     and a configured built-in wins over an overlay provider. That is the `moonshot` lesson
     again. Link it to the existing "must resolve to the intended provider" row.
   - **Commit identity.** "`[run.git.author]` is set in the server overlay
     (`andrews-ai-agent`)." Otherwise: fabro derives the identity from the token user and fails
     the run at setup if the lookup fails (behaviour change 4), and factory commits become
     indistinguishable from the operator's.
   - **Checkpoint commit failures stop the run** (behaviour change 2). Add this to the existing
     "A conflicted merge or rebase never crosses a stage boundary" row: on 0.362 a failed
     checkpoint commit ends the run, so that rule is now also a run-killer.
   - **Upgrades drop run history on the 0.357–0.362 line** (finding R1). One row, or a line in
     the upgrade paragraph: the next upgrade must be rehearsed on a copy first
     (`docs/fabro-upgrade/02-rehearsal.md` is the template).
4. **Remove or reword** every sentence that relies on metadata branches or on the 0.357 pin
   ("Known bad: 0.357"). Search: `grep -n 'meta\|0\.357\|0\.354' AGENTS.md`.
5. **Deploy section.** The scheduler no longer mounts the Docker socket (task 03). Check the
   scheduler deploy block and the "confirm the host still matches the repo" list. Add
   `ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/.home/settings.toml' | diff - ops/settings.toml.example`
   **only if** the example is meant to be byte-identical (it is not today: it is a template
   with comments). Otherwise leave the list alone and say so in your report.

## Part B: `ops/README.md` "Upgrading the server"
- Replace "Known bad: nightly 0.357.x …" with the actual history: 0.357 failed on
  2026-09-15, and 0.362 was reached on <date> by dropping run history and rewriting the
  catalog (`docs/fabro-upgrade/`).
- Add a short "Before any upgrade" list: rehearse on a copy (task 02 is the template), take a
  stopped-state tarball (task 04 step 4), diff the catalog schema, and check that the new
  version's startup activation passes on the copy.
- Keep the existing rollback block. It is still correct, and `REVERSAL.md` on the host is the
  worked example.

## Part C: `docs/research_improvements/04-confirmed-dead-ends.md` on 0.362
Check out the tag in the source checkout (`git -C context/fabro fetch --tags && git -C
context/fabro checkout v0.362.0-nightly.0`; it is untracked context, not this repo). For each
of the ten entries, re-read the code or doc it cites **at 0.362** and add one line under its
heading: `**0.362 (2026-09-xx):** still holds — <file:line>` or `**0.362:** changed — <what>`.
Entries most likely to have moved, so check these first:
- #3 `fabro_tools` / `fabro_run_create`: the run tools were rebuilt on workflow versions in
  this range (`c67c60eeb`, `d5dec0fff`).
- #9 built-in PR creation, and #10 the empty checkpoint commit: checkpointing was reworked
  (behaviour changes 2 and 3).
- #8 `[[run.prepare.steps]]`: the run-creation layering changed (the server-configuration
  doc's new paragraph on `RunIntent` precedence).
Update the file's header line ("Checked against … 0.357") and the note added on 2026-09-25.
Also update `docs/research_improvements/00-overview.md`'s 2026-09-25 table: 03.4 is now
**applied** (task 05), and the dead-ends row records the re-check.

## Part D: the roadmap
- `docs/factory-roadmap/B8-fabro-upgrades.md`: Status becomes "step 1 **applied** on <date>".
- `docs/factory-roadmap/H-housekeeping.md`: mark H5 and H6 **applied** (tasks 05 and 03).

## Part E: the canary watch (host and GitHub)
The first two `backlog` runs dispatched on **each** coder box after task 04 step 11 (four
runs, ids in task 04's report). For each run, until it ends:
- Follow it with `ops/fabro-run-status.sh <id>` and `fabro events <id> -p`.
- **Pass:** it reaches `open_pr` and either merges, or is blocked for a reason that is not
  fabro (red CI, the `architecture` label, a risk rating, the task budget), and its PR's
  commits show `andrews-ai-agent` as author (`gh pr view <n> --json commits`).
- **Fabro-caused failure** (examples: an agent stage dying on a tool or protocol error, a
  checkpoint commit failure, a model that resolves differently from R5, a hook that no longer
  fires): diagnose with `/fabro-workflow`, fix forward in this repo or the overlay, and record
  the finding. A fix that changes a graph gets its gate test and a validate run.
- Watch the new agent runtime specifically (behaviour change 1): the local coders on the
  OpenAI profile, and loop-detection events. Compare one canary's coder stage duration with the
  pre-upgrade median (`runs-export.json` from task 01 holds the old timing).

## Part F: the deployment log
Append one dated section to `~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md` on the Mac (mode 600;
append, never edit earlier sections): the version change, findings R1–R6, what was dropped and
where the archive is, the cancelled-run list, the canary results, and any fix-forward. **No
credentials.**

## Part G: close out
- Pull `~/.fabro-deploy/fabro-workflows` so the operator's checkout matches `main`.
- Run the "confirm the host still matches the repo" list from `AGENTS.md`. Every diff is empty.
- Leave `~/fabro-archive/0354/` in place. It is the only copy of the pre-upgrade run history.
  Deleting it later is the operator's call.

## Acceptance criteria
- Parts A–D committed (one commit per part is fine). The offline gates still pass.
- Four canary runs ended, each a pass or a documented fabro-caused failure that has been fixed
  forward. A fix re-runs the watch on the next run of that box.
- The deployment-log section exists.

## Report
The canary table (run id, box, repo, issue, end state, PR, author check), every fix-forward,
and the dead-end re-check results.
