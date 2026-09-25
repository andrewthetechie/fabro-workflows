# An architecture review fills the backlog twice a week per repository

**Status:** accepted (2026-09-25). Replaces ADR 0002 for `issue-triage`. ADR 0002 still
applies to `backlog`'s `human_rescue` gate.

The `backlog` scheduler works only issues that carry the `agent` label. Humans file those
issues today, and when nobody files work the factory stops (**Starvation**). We add a
fourth workflow package, `arch-review`, to supply work. One **Architecture review** scans
one repository for **Deepening candidates** and files the strongest as **Architecture
issues**. Then it triages every waiting issue in that repository toward the `agent`
label. The goal is issues that are ready to work. An issue that needs a human is the
exception.

## D1. A new package, `arch-review`, with a headless version of the skill

The package name is `arch-review`, not "project improvement". `issue-triage` has an
`improve` node and `backlog` has an `improve` stage. A third use of the word would make
run logs and Discord messages ambiguous.

The scan prompt comes from the `improve-codebase-architecture` skill and the vocabulary
of the `codebase-design` skill. Both skills are on the operator's Mac, not in the sandbox,
so the prompt copies their text into `arch-review/prompts/`. The skill expects a human in
the loop, so the prompt changes four things:

1. The prompt writes no HTML report. The agent writes a candidates file under `/tmp/fabro/`,
   and a command node validates it with `jq`, as every other contract in this repository does.
2. The prompt does not ask "which would you like to explore?". The filing rule in D4
   selects the candidates.
3. The prompt runs no grilling session. Triage (D3) takes its place.
4. The agent does not edit `CONTEXT.md` or write an ADR. The run token has `contents: read`.

An issue body keeps the fields of the skill's report card: files, problem, solution,
benefits, a before and after Mermaid diagram, and the recommendation strength. GitHub
renders Mermaid in an issue body.

The skill finds hot spots in recent git history. The sandbox clone is shallow and has
only `main`. A command node runs `git fetch --shallow-since="90 days ago" origin main`
before the scan. Ninety days covers approximately 26 runs.

## D2. Triage is a shared phase, and `issue-triage` becomes a wrapper

The triage nodes move to `_shared/triage/`, which uses the same `import=` pattern as
`_shared/review-merge/`. `arch-review` imports the phase and runs it once for each issue
in a queue, as `backlog`'s task queue does. `issue-triage` also imports it and stays as a
one-issue package that the operator can fire manually.

We rejected two alternatives:

- `arch-review` fires one `issue-triage` run for each issue. One review then becomes as
  many as 19 runs, and it brings back the cross-run firing that the retired **Bridge** did.
- `arch-review` copies the triage nodes. The `triage_gate` contract is several hundred
  bytes of `jq`, and two copies of it will drift.

## D3. Triage never blocks on a human

The shared phase has no `ask_human` gate. When triage has questions that it cannot answer,
the run does these things for that issue:

1. It posts the questions as a comment.
2. It labels the issue `needs-info`.
3. It sends one Discord message with the issue link and the question text.
4. It continues to the next issue.

One review triages as many as 18 issues (D5). A 30-minute gate for each of them could
hold the run for nine hours. ADR 0002 rejected "no gate at all" because a person at the
keyboard can answer in the same minute. That benefit is small when the run fires at
04:00 and has 17 more issues to triage. This decision also removes the gate from
`issue-triage` (D2), which is why this ADR replaces ADR 0002 for that package.

A human answers in a new comment on the issue. The next review finds the answered
`needs-info` issue and triages it again. With the schedule in D7, an answer waits 3 or 4
days at most. The operator can fire `issue-triage` to get a faster result.

## D4. Triage decides what the repository can decide

Human escalation stays rare because triage makes the decisions that do not need a human.
The rule depends on who wrote the issue:

- **Architecture issue.** A question that has a recommended answer, and whose choice is
  internal and reversible, gets a decision. Seam placement and adapter shape are examples.
- **Human-filed issue.** The repository must support the recommended answer. The code,
  `CONTEXT.md` or an ADR can give that support. Without it, the question goes to a human,
  because an internal choice can reflect intent that the reporter did not write down.

Triage writes each decision into the issue body, under a heading "Decisions made during
triage", where a human can read it and reverse it. Triage always escalates questions
about product behaviour, external contracts, data, or facts that only a human knows.

## D5. What a review files and what it triages

**Filing.** A review files only `Strong` and `Worth exploring` candidates, at most 8 in
one run. Zero is a valid result. Each issue gets the labels `architecture`, `needs-triage`
and `ai-generated`. The last label matches what `file_remainder` applies to the issues
that automation files.

**Deduplication.** Before the scan, the run reads every `architecture` issue, open and
closed. A candidate that an existing issue already covers is not filed. A candidate whose
issue a human closed as "not planned" counts as rejected, and no later review files it again. This
is the headless form of the skill's "record an ADR so that future reviews do not suggest
it again". The run reads the list again immediately before each `gh issue create`, so a
second review that overlaps this one does not file the same issue twice.

**Triage queue.** The queue has two budgets:

1. Every issue that this run filed, at most 8.
2. At most 10 other issues. Answered `needs-info` issues come first, then `needs-triage`
   issues, oldest first in each group.

A worst case of 18 triages on hosted models is acceptable. A run that leaves its own
Architecture issue on `needs-triage` has not finished its work. We rejected one shared
cap of 10. Eight new issues would take almost all of it, and human-filed issues would
then wait another 3 or 4 days.

**Scan failure.** If the scan times out, or writes an invalid candidates file twice, the
run files nothing and still triages budget 2. The two halves are independent. A stalled
hosted model in the scan must not stop a week of triage.

## D6. Claims expire after 24 hours

Triage claims an issue with `triage-in-progress`, and a review skips an issue that
carries a claim. A run that a fabro restart, a sandbox failure or the stall watchdog stops
never reaches `release`, so its claim stays on the issue. ADR 0002 recovered these claims
because only one triage run could exist. That guarantee went away with the capacity gate
(ADR 0005).

A claim is stale when the `labeled` event for it on the issue timeline is more than 24
hours old. A review then claims the issue again. Twenty-four hours is much longer than
the longest run, which is 18 triages that each have a bounded node timeout. We use no
lock other than the label. A duplicate issue that gets past D5's second read costs one
manual close. A lock that leaks costs more.

## D7. Hosted models, outside the scheduler, on an enabled cron

Every stage resolves to `high-reasoning`, which is `glm-5.3` with a fallback to
`kimi:kimi-k3`, the same as triage today. No stage uses a **Coder instance**, so a review
takes no **Coder lease** and the **Scheduler** does not admit it. This follows the
precedent of scheduler decision 13: work that does not use a coder box stays outside the
scheduler. We rejected the coder boxes because a scan would compete with `backlog` for
the two single-slot instances. We rejected `long-context` (`spark`) because the hosted
models need no new capacity.

Each repository gets one `arch-review-<repo>` automation with an enabled schedule on two
fixed weekdays. The gap between runs is 3 or 4 days, and no two repositories fire at the
same hour on the same day:

| Repository | Days | Expression |
|---|---|---|
| jelly-swipe | Mon, Thu | `0 4 * * 1,4` |
| lawncare-saas | Tue, Fri | `0 4 * * 2,5` |
| womens-fantasy-sports | Wed, Sat | `0 4 * * 3,6` |
| writers-app | Sun, Wed | `0 5 * * 0,3` |

We rejected `*/3` in the day-of-month field because it gives an uneven gap at the end of
each month. The operator starts an on-demand review with the automation's `api:manual`
trigger. That endpoint drops a request body, so the workflow takes no inputs, and a run
cannot be pointed at one area of a repository. The scan selects its own area from git
history.

## D8. An Architecture issue is never merged automatically

An Architecture issue goes to `agent` like any other triaged issue. Without a gate, no human
would take part between the proposal and the squash-merge. An LLM proposes the refactor,
triage accepts it, `backlog` implements it, and `review_merge` merges it. On
`lawncare-saas` that merge is also a deploy. The human step is one click to merge a PR
that already exists and that the reviewers approved.

The `architecture` label controls this in three places:

1. `open_pr` copies `architecture` from the issue to the PR, next to `agent-authored`.
2. `merge_gate` in `_shared/review-merge/` reads the PR labels in the same check that looks
   for `agent-authored`. It blocks the merge when `architecture` is present and writes the
   reason. The run reports `blocked`, and the existing `report_blocked` Discord hook becomes
   the request to merge. Because the check reads the PR, it also works for a `pr-review` run.
3. `file_remainder` copies `architecture` from the parent issue to the **Remainder issue**.
   Without this step, the remainder of an Architecture issue would merge automatically.

We rejected a label other than `agent` that a human must promote. That label would stop
the issues before the backlog, and the goal is issues in the backlog. We rejected
`auto_merge=0` at fire time because the scheduler fires `backlog` runs and would need new
code to read issue labels. The label is also the easiest control to relax later.

## D9. One summary for each run

Each review sends one Discord summary, for example "jelly-swipe: 5 filed, 12 triaged, 10
`agent`, 1 `needs-info`, 1 not actionable". It is the only evidence that a scheduled run
happened. A repository with no new work still reports "0 filed".

## Consequences

- These are the first enabled schedules in this deployment. `AGENTS.md` and
  `ops/README.md` both state that nothing fires on a cron, and both need an update.
- `fabro-monitor.sh` condition C3 alarms when any schedule is enabled and the newest run
  is more than 4 hours old. The `arch-review` schedules make C3 active again, and with
  them it alarms whenever `backlog` is idle for 4 hours. C3 must use the expected
  interval of each enabled schedule, or exclude `arch-review`, before the schedules are
  enabled.
- The change to `merge_gate` is in the shared phase, so it changes `backlog` and
  `pr-review` together. `ops/test-task-gates.sh` covers `merge` and `file_remainder`,
  and it needs cases for the new label rules.
- `provision-server-state.sh` creates four new automations. Their schedules are enabled,
  unlike every other schedule row that the script creates.
- The scheduler queue gets as many as 64 new issues per week (8 per run, 8 runs). The scheduler ranks by
  repository priority, then by issue number, so Architecture issues come after older
  human-filed issues unless an issue has the `priority` label.
