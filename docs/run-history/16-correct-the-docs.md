# Correct the docs and accept ADR 0008

## Tracer-Bullet Outcome
Someone opening this repository cold learns that the scheduler keeps a run history, where
its series lives, what the term means, and that the decision behind it is accepted and
applied — without reading any code.

## User Story
As the next person in this repository, I want the record to match the host, so that I do
not plan work against a page that already exists or trust an ADR that says `proposed`
about something that has been live for a week.

## Description
Four edits across three tracked files, all of them after task 15 has proved the feature on
the host. Nothing here is speculative: every claim these edits make was verified during the
deploy.

## Context Pack
- Source decisions: ADR 0008 Q9 — the ADR stays standalone and the series cites it, not the
  reverse. So `docs/run-history/00-overview-and-contracts.md` already points at ADR 0008
  and the ADR gains no pointer back.
- Repo facts: `AGENTS.md` carries a **Layout** table, one row per tracked tree, each row
  saying what the tree is and where to start. `CONTEXT.md` carries the domain **Language**
  section, one bold term per entry with an `_Avoid_:` line naming the words that must not
  be used for it. ADR 0007 is the closest precedent for a status line that records partial
  application: `**Status:** accepted (2026-09-20), partly applied`.
- Non-goals: no change to `docs/USER-GUIDE.md`, which is the public secrets-free manual and
  is the operator's own call. No new ADR. No edit to `docs/scheduler/`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  docs/adr/0008-scheduler-run-history-page.md     (status line, closing paragraph)
  AGENTS.md                                       (Layout table row)
  CONTEXT.md                                      (one Language entry)
  ```

- Interfaces and names:

  **1. ADR 0008's status line.** It currently reads:

  ```md
  **Status:** proposed (2026-09-20)
  ```

  Change it to, with the real deploy date from task 15:

  ```md
  **Status:** accepted (2026-09-20), applied <YYYY-MM-DD>
  ```

  **2. ADR 0008's closing paragraph.** It currently reads:

  ```md
  Nothing here is built. This ADR records the decision; the implementation is a separate,
  deliberate act, and deploying it restarts the scheduler container and so re-dates draft
  14's shakedown window.
  ```

  Replace it with a statement of what actually shipped — the work, and the one thing the
  deploy taught that the ADR could not know. Name no path: decision 9 is that the ADR
  stays standalone, and the grep below enforces it, so "seventeen tasks" is as close as
  this paragraph may come to citing the series.

  ```md
  Built and deployed on <YYYY-MM-DD>, as seventeen tasks. The deploy restarted the
  scheduler container and so re-dated draft 14's shakedown window, to
  `<the StartedAt captured in task 15>`.
  ```

  If task 15 surfaced anything the ADR got wrong, correct it here in the same edit rather
  than leaving the ADR and the host disagreeing.

  **3. `AGENTS.md`'s Layout table.** Add one row, after the `docs/perf/` row and before
  `docs/research_improvements/`, matching the register of its neighbours:

  ```md
  | `docs/run-history/` | The task series for the scheduler's run-history page: a `run_history` row written inside the lease-release transaction, and `GET /history` to read it. `00-overview-and-contracts.md` first — it carries the canonical schema, the five release paths and the verified GitHub contract. ADR 0008 holds the decision. **Applied <YYYY-MM-DD>.** |
  ```

  **4. `CONTEXT.md`'s Language section.** Add one entry. Place it after **Coder lease**,
  which is the term it depends on:

  ```md
  **Run history**:
  One row per released **Coder lease**, in the scheduler's own `run_history` table: which
  box ran it, which issue, how it ended, and whether its PR had merged at that instant.
  Written inside the transaction that deletes the lease, so it survives the crash that
  would otherwise lose both. A point-in-time record — it says what the ending *was*, not
  what became of the PR later — and it covers scheduler-dispatched `backlog` runs only, so
  a **Manual fire** appears nowhere in it. Read at `GET /history`. See ADR 0008.
  _Avoid_: audit log, run log (fabro has its own), archive
  ```

- Verified external contracts: none. Documentation only.

- Behavior rules:
  - Every `<YYYY-MM-DD>` and `<the StartedAt ...>` placeholder is filled with the real
    value captured during task 15. Do not leave a placeholder and do not guess a date.
  - The ADR gains **no** link to `docs/run-history/`. Decision 9 is that the series cites
    the ADR and not the reverse, and a link the other way is the dangling reference that
    decision exists to avoid.
  - The `CONTEXT.md` entry carries an `_Avoid_:` line. Every entry in that file has one.
  - No IP, path or token enters `docs/USER-GUIDE.md`; this task does not edit it at all.
  - Keep the existing prose register: terse, declarative, and saying *why* rather than
    *what* wherever the two differ.

- Error and security rules: these are tracked files in a public repository. The
  `CONTEXT.md` and `AGENTS.md` edits carry no credential and no host address; the ADR
  already carries none. The scheduler's LAN address appears elsewhere in `AGENTS.md`
  already and needs no repetition here.

## Acceptance Criteria
- [ ] ADR 0008's status line reads `accepted`, with a real applied date.
- [ ] ADR 0008's closing paragraph no longer says "Nothing here is built".
- [ ] ADR 0008 contains no link to `docs/run-history/`.
- [ ] `AGENTS.md`'s Layout table has a `docs/run-history/` row naming
      `00-overview-and-contracts.md` as the entry point and ADR 0008 as the decision.
- [ ] `CONTEXT.md` has a **Run history** entry with an `_Avoid_:` line, placed after
      **Coder lease**.
- [ ] No `<YYYY-MM-DD>` or other placeholder remains in any of the three files.
- [ ] `git diff --stat` shows exactly three files changed.

## Test Expectations
There is no test runner for a documentation change. These greps are the check, and each
must print what is stated:

```sh
# the ADR is accepted and applied, and says nothing about being unbuilt
grep -n "^\*\*Status:\*\*" docs/adr/0008-scheduler-run-history-page.md
grep -c "Nothing here is built" docs/adr/0008-scheduler-run-history-page.md   # -> 0
grep -c "docs/run-history" docs/adr/0008-scheduler-run-history-page.md        # -> 0

# the layout table and the glossary both carry it
grep -n "docs/run-history/" AGENTS.md                                         # -> 1 row
grep -n -A 9 "^\*\*Run history\*\*:" CONTEXT.md                               # -> the entry

# no placeholder survived
grep -rn "YYYY-MM-DD" docs/adr/0008-scheduler-run-history-page.md AGENTS.md CONTEXT.md
# -> no output

# exactly three files
git diff --stat
```

## Dependencies
- Blocked by: `Deploy and verify on the host`
- Why blocked: supplies the applied date, the scheduler's new `StartedAt`, and the
  confirmation that the ADR's claims are true on the host. An ADR marked `accepted` before
  the deploy would be the exact drift this task exists to remove.
- Blocks: None

## Labels
`docs`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
1 - Documentation only. Nothing here is read by an automation: `AGENTS.md` and
`CONTEXT.md` are for humans and agents reading the repository, and the ADR is a record.

## Validator Stopping Point
The greps above print what is stated, `git diff --stat` shows three files, and the
repository's account of the scheduler matches the host.
