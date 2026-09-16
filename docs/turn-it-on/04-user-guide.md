# Task 04 — `docs/USER-GUIDE.md`

**Depends on:** 01 (the guide documents the monitor's alert vocabulary). **Blocks:** 05.
**LLM level:** yes.

Write the public operating manual. Three questions organize it — they are the ones the
operator actually asks, and they came from the operator: **how do I add work, how do I
tell it's running and when it'll be done, how do I get notified when something needs
attention.**

Hard constraints:

- **No secrets, no IPs, no host paths.** That is the runbook's job (task 05). The guide
  may say "the fabro web UI" and "the host"; it may not say `10.10.0.32`. This is what
  keeps the file in a public repo. Refer to the runbook by name ("see the operator
  runbook") without reproducing it.
- **Every mechanic is verified, every duration is measured.** Mechanics come from the
  graphs and `ops/README.md`; durations come from the deployment log
  (`~/.fabro-deploy/docs/FABRO-DEPLOYMENT-LOG.md`), cited as ranges and marked as early
  data. Finding 3: the API has almost no run history — do not pretend otherwise.
- Terse. This is an operator doc, not marketing. Match the register of
  `ops/README.md`.

## Sections

### What this factory does

One short section, no more: three workflows, four repos, one pipeline.
`needs-triage` → (issue-triage improves, may ask questions, promotes) → `agent` →
(backlog decomposes, implements, opens PR) → bridge → (pr-review rebases, reviews,
fixes, and squash-merges when eligible) → issue closed. Auto-merge is armed; a PR can
go from issue label to merged with no human. Say that plainly — it is the fact that
shapes every other section.

### Adding work

- The taught path: file an issue on any of the four repos, label it **`needs-triage`**.
  No required format — triage improves under-specified issues, retitles them to
  Conventional Commits, and either promotes them or asks the blocking questions.
- The expert shortcut: label **`agent`** directly when you already know exactly what
  you want. backlog acquires the lowest-numbered open `agent` issue, one per run per
  repo.
- What the labels mean once work is moving: `agent-in-progress` (claimed, do not
  touch), `agent-authored` (the PR is automation's; human commits on it veto
  auto-merge), `ai-review-complete`, `ai-review-needs-human`, `agent-stuck`.
- The human edit that is supported mid-flight: editing inside the commit-body marker
  block in a PR description corrects the commit message before the merge fires; a bad
  title is fixed by editing the PR title itself. Both are read live at merge time.

### Telling it's running, and when it'll be done

- Discord is the primary signal — table of the seven hook kinds and the monitor's
  conditions, with emoji, meaning, and what to do. (The monitor's six conditions are
  the C1–C6 table from `docs/turn-it-on/00-overview-and-contracts.md`, rendered for a
  reader.)
- The web UI for watching a live run; `fabro events <run> -p` for why a run routed the
  way it did (point at AGENTS.md for the incantation — no host details here).
- GitHub itself: `gh issue list --label agent-in-progress`, open `agent-authored` PRs.
- Durations: mine the deployment log for the measured runs and present them as ranges
  with the caveat that the fleet is young. Structure it as expectations, not promises:
  a triage run is minutes plus up to a 30-minute question gate; a backlog run is tens
  of minutes; a pr-review run is minutes plus up to a 60-minute CI/merge budget. One
  issue per run per repo, backlog fires per repo every 15 minutes, three runs
  concurrent host-wide, and queued runs wait — so "when will my issue be done" is
  answered in terms of queue position and cadence, not a stopwatch.

### Getting notified when something needs attention

- What needs a human *fast*: `❓` triage questions (30-minute gate, then the questions
  post to the issue), `🟡` rescue gates (the run holds at the web UI).
- What needs a human *eventually*: `🔴` failures, monitor alerts, `agent-stuck`.
- What is informational: `✅` PR opened, `🔎` review triggered, `🚀` merged, monitor
  resolved messages.
- The silence signals: no run in 2h while schedules are enabled (monitor C3), no work
  anywhere (C5), and no heartbeat (the host itself is down — check it).

### Turning things off

Pointers, not duplication: the auto-merge kill switches and the schedule toggles live
in `ops/README.md`; link the sections. One line on the bluntest switch (the
`FABRO_API_TOKEN` vault entry fails every backlog run closed) so an operator in a
panic knows it exists, with the pointer for how.

## Acceptance

- A fresh reader can add work, find a running item, estimate its completion, and
  respond to every notification the system produces, using only this file plus the
  pointers it gives.
- `git grep -E '10\.10\.0\.32|server\.dev-token|discord_webhook' docs/USER-GUIDE.md`
  is empty.
- Every duration cites the deployment log; every mechanic matches the graphs as
  validated today (backlog 38 nodes, pr-review 28, issue-triage 15).
- AGENTS.md's layout table gains the file — but that lands in task 09, not here.
