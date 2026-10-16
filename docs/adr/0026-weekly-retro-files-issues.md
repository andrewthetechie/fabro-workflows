# A weekly retro files issues for new failure classes, and GitHub holds its state

**Status:** proposed (2026-10-15). Every decision below is the operator's, from the
planning session for issue #33. The task series is `docs/factory-retro/`. It becomes
accepted after the one-month check (task 07).

The 2026-10-11 retro found three problems that had cost runs for weeks: the workflow-scope
push refusal, merge-phase timeouts that recorded no reason, and the Kimi weekly quota
running out mid-week. All three were visible in the events, and nobody was looking. The
scorecard (#22) now computes the numbers continuously, but someone still has to read them.

## D1. A weekly job in the scheduler, not a workflow and not a monitor condition

The **Retro** is `python -m fabro_scheduler retro`, run from host cron every Monday at
12:00 UTC by `ops/fabro-retro.sh`, over the previous ISO week of scheduler-dispatched
`backlog` runs. It is in the scheduler because the scheduler already holds the run
summaries, `scorecard.compute` and a GitHub client.

Rejected:
- **A Fabro workflow.** It would put the code on the deploy-on-push path, need the sandbox
  to reach the scheduler over the LAN, and take a run slot for a job that uses no model.
- **A thread in the scheduler.** A restart at the wrong moment would skip a week, and the
  scheduler would need the Discord webhook, which today only the fabro container holds.

**Relation to ADR 0004.** ADR 0004 keeps run-failure alerting in-band, so that one failure
never pings twice. The retro does not alert on runs. It posts one message a week about
classes of failure, with counts across many runs. A hook still reports each failure as it
happens, and the monitor stays health-only.

## D2. A failure class is a key built from enumerable values

A **Failure class** has one of four kinds, `ending`, `held`, `timeout` and `failover`. Its
key (`timeout:improve`, `failover:kimi/kimi-k3:access_denied`) is built only from node
names, fabro's categories, the scorecard's held-reason buckets and model ids. Never from
free text, because the key has to be the same the next week.

The issue proposed one tuple: terminal node, failure category and block reason. That tuple
misses two of the retro's three examples. Kimi failovers and most merge-phase timeouts
happen in runs that still end with a PR, so those runs have no failure ending at all.

Amended 2026-10-16, after the first dry run: a failover key leaves out the stage. The Kimi
quota refused the same 38 runs at `spec` and at `extra_decompose`, which as two keys was one
cause filed twice, two of the week's three slots. The stages are reported inside the class.

## D3. GitHub is the store of known classes

Each retro issue has the label `retro` and a hidden marker
`<!-- fabro:retro-class KEY -->`. Each week the retro lists every `retro` issue, open and
closed, and matches markers. The operator's own actions on an issue are the decisions:

- **open**: the class is known. The retro comments with the week's evidence.
- **closed as completed**: the class was fixed. A recurrence files a new regression issue
  that links the old one. A close inside the reported week is not a recurrence yet: that
  week still holds the runs from before the fix, so the class waits for the next week.
- **closed as not planned** (or duplicate): the operator rejected it. The class is
  suppressed, and no write is made.

Rejected: a table in the scheduler's database. Every scorecard table is documented as
derived data that can be dropped and rebuilt, and a table of filed classes could not be
rebuilt from anything. It would also put a second copy of the operator's decision beside the
issue's own state, and the two could disagree.

## D4. Only issues, in this repository, and never a target repository's name

The retro writes issues and comments in this repository, never PRs: a push to `main`
deploys. This repository is public, so the text is checked before each write for every
configured repository's name, `github.com` URLs to other repositories, and cross-repository
references like `x#12`. A match withholds that write and reports it in Discord. Nothing is
redacted, because a redaction that misses once publishes the name. The evidence is run ids,
counts, box-hours, node names and bucket names.

## D5. Bounded volume

A class counts in a week when it touches at least 3 runs or costs at least 4 box-hours. At
most 3 new issues are filed each week, ranked by cost, and comments on known classes
are not capped. Cost is box-hours, but for a `timeout` or `failover` class only the runs
that ended without a PR count (amended 2026-10-16): a stage that failed over usually still
ships, and the first dry run ranked 38 Kimi runs that mostly opened PRs above 15 PRs the
Refuter held, and passed a single 15-hour run that timed out once and shipped. The first live fire comes only after a dry run that the operator has read in
Discord. These limits keep the issue list at a rate a human will read, which is the same
reason ADR 0004 forbids double pings.

## Consequences

- An issue filed by the retro is authored by the operator's account, because the
  scheduler's token is the operator's. The `retro` label, not the author, marks the issue
  as the retro's.
- Coverage is the scorecard's: `pr-review`, `dependabot`, `merge-queue`, `arch-review` and
  hand fires are not seen. Every message says so.
- Box-hours are counted per class, and one run can be in several classes. They are never
  added across classes.
