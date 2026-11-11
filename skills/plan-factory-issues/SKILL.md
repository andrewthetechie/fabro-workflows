---
name: plan-factory-issues
description: >
  Turns a design, PRD, issue or request into published Fabro factory issues: grills the open
  decisions, slices the work into tracer bullets sized for one Ralph run, writes acceptance
  criteria that name a `.fabro/ci.sh` route, and publishes Split parents and Child issues with
  their labels. Use when decomposing a design, a PRD or a request into issues for a repository
  that has `.fabro/ci.sh`.
---

# Planning factory issues

You turn a source into issues the factory can implement, prove and merge with no human. The
script holds the mechanical rules; [FORMAT.md](FORMAT.md) is the contract every draft meets.
Your work is the judgment: the grill, the slicing, the drafts.

```sh
PLAN=<this skill's base directory>/scripts/plan_issues.py   # needs uv and gh
```

Run it from the target repository's root. Announce each state as you enter it:

```text
PLAN STATE: [Read Source | Factory Contract | Grill | Shape Graph | Draft | Review | Quiz | Publish]
```

## 1. Read Source

Read the whole source: a design document, a PRD, an issue, or this conversation. An EGM design
maps onto issue sections by [FORMAT.md, *From an EGM design*](FORMAT.md#from-an-egm-design).

**Done when** every claim in the source sits in exactly one of three lists: known facts, safe
assumptions, and material gaps.

## 2. Factory Contract

Run `$PLAN facts`. It stops on a repository with no `.fabro/ci.sh`; tell the operator to onboard
it with the `setup-repo-for-factory` skill, and end here. Otherwise read:

- the routes it prints: every command `ci.sh` reaches, and through what. A **Command criterion**
  names one of these;
- the orientation issue it names, then `CONTEXT.md` and the ADRs, for the repository's vocabulary;
- the code the source touches;
- the open issues: `gh issue list --state open --limit 200 --json number,title,labels`.

Read [FORMAT.md, *The sandbox*](FORMAT.md#the-sandbox) now: what the coder cannot do is what the
issue must say for it.

**Done when** you can name, for every area the source changes, the `ci.sh` route that tests it or
the fact that none does, and every open issue that overlaps the source is on a list.

## 3. Grill

`$PLAN init <slug>` creates the plan directory and prints `plan.json`'s path; `ledger.md` sits
beside it. Ask the operator one question at a time about each decision that would otherwise reach
a coder as a design choice: a data or API shape, a default, a validation or duplicate rule, an
error contract, a migration, a permission, a configuration value. Add the factory's own decisions:

- a check `ci.sh` does not run: rewrite it as a behaviour, or make adding it to `ci.sh` a human action;
- an overlapping open issue: merge with it, order after it, or leave it;
- a question the operator chooses to leave open: that slice takes the `needs-triage` lane.

Each question quotes the source, says what the answer changes, and recommends a default with its
evidence. Settle from the repository whatever the repository answers. Record each answer in
`ledger.md` under decided, default adopted, rejected, assumption, or left open.

**Done when** every remaining uncertainty can be written as an assumption that misleads no one, and
every answer from the grill has a ledger entry.

## 4. Shape Graph

Read [FORMAT.md](FORMAT.md). Cut the work into **tracer bullets** sized for one Ralph run, map their
order onto what the scheduler holds, and choose each slice's lane
([FORMAT.md, *Slices, order and lanes*](FORMAT.md#slices-order-and-lanes)). Write the entries into
`plan.json`.

**Done when** every slice has a key, a lane with its reason, and a one-sentence end-to-end outcome;
every order is an `after` inside one Split parent; and each slice waits on one issue at most.

## 5. Draft

Write `drafts/<key>.md` for each entry, in [the factory issue format](FORMAT.md#the-factory-issue-format).
Then run `$PLAN check <plan.json>`, fix every error, and fix each warning or carry it to the Quiz as
a proposed `waive` entry with its reason.

**Done when** `check` exits 0, every Context fact cites a `path:line` you opened in this session,
and every requirement has a criterion or a non-goal. Then read each criterion as the Refuter would,
seeing only the diff and `ci.sh`'s run, and say how you would mark it met. A criterion you cannot
place on that evidence is not done: rewrite it as a behaviour or a cited route, or make what it
needs a post-deploy check or a non-goal.

## 6. Review

Dispatch the fresh reviews in [REVIEWS.md](REVIEWS.md): one subagent per draft, then one over the
set. A **Goldfish** sees the draft, the repository and `.fabro/ci.sh`, and nothing of the source,
the ledger or this conversation; that blindness is the review. Without subagents, stop and tell the
operator: a review in this context is not a Goldfish review.

Fix each finding you agree with, re-run `check`, and list the rest for the Quiz.

**Done when** every review has returned and each finding is fixed or listed.

## 7. Quiz

Present the graph as a numbered list. For each issue: title, lane and its reason, parent and
`after`, outcome, size (Task map steps, expected files), open findings, proposed waivers and human
actions. Then the **frontier**: what the scheduler can start at once, and what runs in parallel. Ask
whether the slices, the order and the lanes are right. Apply each change, re-run `check`, and
present the graph again.

**Done when** the operator approves the graph as last shown, and `check` exits 0 on it.

## 8. Publish

Run `$PLAN publish <plan.json> --dry-run` and show the operator its output. Ask for an explicit yes,
saying how many issues it creates and that the scheduler can start the frontier within minutes.
On that yes alone, run `$PLAN publish <plan.json> --yes`. If it stops partway, fix the cause and run
the same command again: it finds what it already created.

Then tell the operator: each issue's URL, the frontier, the held children and what each waits on,
every human action and the issue it gates, the post-deploy checks, and every waiver with its reason.

**Done when** it prints a URL for every issue and the operator has that report.

## Guardrails

- Publishing waits for the operator's approval of the graph and a separate yes to publish.
- The target repository stays as it is: a `ci.sh` or `.github/workflows/` change is a human action
  in an issue.

## Changing the rules

The lint's rules live in `scripts/factory_issues/lint.py`, each with its source; the issue corpus in
`fixtures/issues/` is shared with the issue sharpening loop (#68). After any change run `make check`.
