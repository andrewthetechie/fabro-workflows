# The Goldfish reviews

Four kinds of fresh review: three per draft, then one over the whole set. Each runs in a subagent
that starts cold. Hand it the brief below with the paths filled in, and nothing else: no source,
no ledger, no summary of the conversation. What the planner knows and the draft does not say is
exactly what these reviews exist to find.

Dispatch the per-draft reviews in parallel: for N drafts, 3N subagents in one message. Then run the
set review. Collect each result as it arrives, and keep it verbatim for the Quiz.

## 1. Starved implementer

```text
You are about to implement a GitHub issue in the repository at <repo root>. The issue is at
<draft path>. You will get nothing else: no design document, no conversation, no one to ask.

First, before you open any other file, write two paragraphs from the issue alone:
  1. What this issue asks you to build, in your own words.
  2. How the current system works in the area it touches, as the issue describes it.

Then open the repository as you need, and list:
  - every handoff you cannot act on: a name with no shape, a "the existing pattern" with no
    excerpt, a behaviour with no rule;
  - every design choice still left to you: a data shape, a default, an error, a boundary;
  - anything in the issue that the code contradicts.

Return the two paragraphs, then the three lists, each item quoting the issue line it is about.
An empty list is "none".
```

A paraphrase that differs from what the planner meant is the finding: the issue says something
other than the plan.

## 2. Grounded audit

```text
Audit the GitHub issue at <draft path> against the repository at <repo root>, read at its current
state. For every current-state claim in it (a path, a line, a symbol, a signature, a type, a
config key, a command, a behaviour of the existing code), open the source and classify the claim:

  Verified      you opened it and it matches
  Contradicted  the source says otherwise (quote both)
  Unsupported   you looked where it would be and found nothing

A file the issue proposes to create is not a claim about the current state; skip it. Commands are
checked against .fabro/ci.sh and what it calls. Return one row per claim: the claim, the class,
and the path:line you read.
```

## 3. Refuter's-eye

```text
You are the reviewer who decides whether a pull request for the issue at <draft path> merges.
When you judge it, you will see only the issue, the diff, and the output of .fabro/ci.sh in the
repository at <repo root>. You cannot run anything else, read the PR description, look at a
deployed site, or ask anyone.

For each acceptance criterion, try to show that you could NOT mark it met from those three
things, however good the code is. Read .fabro/ci.sh and what it calls before you judge a command.

Return one line per criterion:
  checkable: <how you would mark it met: the diff shows X, or ci.sh line N runs Y>
  not checkable: <why, and the smallest rewrite that would make it checkable>
Then list any criterion that needs a surface a non-goal of the same issue excludes.
```

A `not checkable` finding stays open until the planner fixes the criterion or the operator agrees
to drop it. The factory's Refuter has the same no-contest rule.

## 4. The set review

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
