# The Goldfish reviews

Two fresh reviews: one per draft, then one over the whole set. Each runs in a subagent that starts
cold. Hand it the brief below with the paths filled in, and nothing else: no source, no ledger, no
summary of the conversation. What the planner knows and the draft does not say is exactly what these
reviews exist to find.

Dispatch the per-draft reviews in parallel: for N drafts, N subagents in one message. Then run the
set review. Collect each result as it arrives, and keep it verbatim for the Quiz.

## 1. Blind read and claim audit

```text
You are about to implement a GitHub issue in the repository at <repo root>. The issue is at
<draft path>. You will get nothing else: no design document, no conversation, no one to ask.

First, before you open any other file, write two paragraphs from the issue alone:
  1. What this issue asks you to build, in your own words.
  2. How the current system works in the area it touches, as the issue describes it.

Then open the repository, and return these three lists:
  - every handoff you cannot act on: a name with no shape, a "the existing pattern" with no
    excerpt, a behaviour with no rule;
  - every design choice still left to you: a data shape, a default, an error, a boundary;
  - one row per current-state claim the issue makes at <date> on main: the claim, then Verified,
    Contradicted or Unsupported (quote both), then the path:line you read. A file the issue
    proposes to create is not a claim; skip it. Check a command against .fabro/ci.sh and what it
    calls.

Each item quotes the issue line it is about. An empty list is "none".
```

A paraphrase that differs from what the planner meant is the finding: the issue says something
other than the plan.

## 2. The set review

```text
Read every issue draft in <plan directory>/drafts/ and the plan at <plan.json>. The plan's
`after` field orders Child issues; issues outside one chain may run at the same time in two
checkouts of the repository at <repo root>.

Check the set, not each issue:
  - a data shape or type named in two drafts is defined the same way in both;
  - each producer's contract matches what its consumer expects;
  - no capability appears in two drafts;
  - two drafts outside one chain do not expect the same file;
  - a draft that changes a contract has every draft that depends on it ordered after it.

Return findings per pair of drafts, each quoting the lines from both. "None" if the set is clean.
```

## Using the findings

- Fix a finding you agree with in the draft, then run `$PLAN check` again.
- A finding you disagree with goes to the Quiz with your reason; the operator decides.
- A finding the operator accepts as it stands becomes a `waive` entry only when it is a lint rule;
  otherwise it is recorded in the ledger.
