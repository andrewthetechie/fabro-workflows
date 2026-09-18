# What was changed, and what it is worth

Everything here is committed and validated. It is the subset that does not change
how the workflow works — no review was added or removed, and no task routes to a
coder differently. `02-needs-your-decision.md` holds the rest.

Read `00-overview-and-measurements.md` first; the numbers below come from it.

## 1. Review-stage timeouts: 30m → 15m

`improve`, `review`, `standards`, `spec`, `quality`, `extra_decompose`, and the
merge phase's `standards`, `spec`, `review_fix`.

The 30m was never sized — it is the graph `stall_timeout` default written out by
hand. Across 29 executions in three runs the slowest review stage ever recorded
is 384s, so 30m was between 5× and 26× the real tail. That is not headroom; it is
the price of a stall. Both of run `01M2RX2SGJJT09WK5FS8GYJCDE`'s 30-minute losses
were a review stage sitting on a hung provider until its own timeout fired.

`max_retries=1` stays on every one of them, so the failure mode of a too-tight
timeout is a retry, not a failed run. `coder` (90m, max observed 2110s),
`decompose` (30m, max 1009s), `rework_t1..t4` (45m, never yet executed) and
`resolve_merge` (30m, n=1) keep theirs — real tails, or no evidence.

**Worth:** halves the cost of a stall on the stages where stalls have actually
happened. On run 1 that is 30 of the 60 lost minutes.

## 2. `ci_fix_t1` / `ci_fix_t2` lose `max_retries=1`

The only way those nodes fail is the 20m timeout — a `cannot_fix` verdict is a
*success* that `ci_fix_gate` reads. So the retry always re-runs a stage that has
just proved it cannot finish in 20 minutes.

It also breaks the 60-minute merge budget `watch_checks` enforces, which assumes
one agent attempt per tier; the arithmetic is written into that node already
("1800 + agent + 900 + agent exceeds 3600"). With a retry, tier 1 alone can take
40 minutes and tier 2 becomes unreachable — which is exactly what happened.

**Worth:** 20 minutes on run 1, 13 on run 2, on every PR that ends up blocked.
Routing is unchanged: a failed tier still takes its unconditional edge to
`ci_fix_gate`.

## 2b. `coder` gets 180m and `allow_partial`; `human_rescue` gets a 4h fuse

From run `01M2SBJG92TX0DBRKY5881VM34` — 10h07m on womens-fantasy-sports, of which
3h00m was a coder that never finished and 5h31m was a human gate nobody answered.

**The coder was not stuck.** Task 2 of 3 was
`refactor(squads): one module resolving a squad's Season, actionable gameweek and
typed opening-build state` — a cross-cutting refactor. It ran 90 minutes, was
retried, ran 90 more, and was working throughout: 55 then 77 tool calls, 43 then
67 assistant turns, context grown to 158,725 tokens. The last event before the
first timeout, **11 seconds before it fired**, was `edit_file -> "Successfully
edited"`. Task 1 of the same run (`map NoActionableGameweekError to 422`) landed
in 24 minutes.

**Why Sandcastle did not have this, and it is not the model.** Sandcastle's coder
was `strix/qwen3.6-35b-a3b-8bit` — the same local box behind fabro's `coders`
pool. Its coder had **no wall clock at all**. It had `--idle-timeout` (default
1800s, which fails only when stdout is *silent*) and
`agent-invocation-livelock.mts`, which aborts after **5 consecutive identical
tool calls with an unchanged worktree snapshot**. Both measure progress. Neither
would have stopped this agent: it was never silent, and 13 consecutive reads of
13 *different* files are not identical calls. Fabro has no per-node equivalent —
`stall_timeout` is graph-level and cancels the whole run.

`reasoning_effort` is *not* the difference either. It defaults to `high` in fabro
and the coder spent 41,493 reasoning tokens against 16,448 output tokens, but
Sandcastle ran high/default reasoning on everything too.

**The change that matters is not the bigger number.** 180m is still an arbitrary
wall clock. `allow_partial=true` is the fix: `finalize_retries_exhausted`
promotes an exhausted stage to `partially_succeeded` rather than `failed`
(`node_handler.rs:170`), and a new edge
`coder -> prep_review [condition="outcome=partially_succeeded"]` carries it into
review instead of letting it fall through the unconditional edge to
`human_rescue`.

That matters because **a timed-out coder produces no review verdict, and the
escalation ladder is driven by review verdicts.** Task 1 of that run went
`coder -> review -> rework_t1 -> review -> rework_t2 -> review -> rework_t2 ->
review -> integrate` and shipped. Task 2 reached none of it — `rework_t2/t3/t4`,
which run *different models* precisely for a task tier 1 cannot land, were never
tried. The partial work is already on disk in the run's own sandbox, so
`prep_review` picks it up, `validate` catches anything left broken, and the
reviewer's verdict drives the ladder. That is Sandcastle's shape.

**`human_rescue` now carries `timeout="4h", human.default_choice="mark_stuck"`.**
The gate held one of the server's two scheduler slots for 5h31m — half the
factory's capacity parked on a question nobody was at the keyboard for. Every
notification worked: the `discord-rescue` hook fired at `stage_start`
(`hooks_matched=1`) and the monitor's C4 stuck-run condition alerted at ~07:22 and
re-alerted at 11:22. A gate that nobody answers now abandons itself, labels the
issue `agent-stuck`, and releases the slot.

Two details worth keeping. `timeout` on a **human** node does bound the wait,
despite the attribute table's "waiting on human input doesn't consume it" — that
sentence describes `timeout_excluding_interview_wait`, which pauses *other* nodes'
timeouts while an interview blocks; a human node is handler-managed and copies its
own timeout onto the question (`handler/human.rs:122`). And `human.default_choice`
is a **node id**, not an edge label — `make_choice_outcome` puts it straight into
`suggested_next_ids`.

**Known interaction:** `max_retries=1` survives, so the worst case is 180m + 180m
= **6 hours** before the ladder engages. Dropping the retry would make it 3h and
is arguably strictly better now — `rework_t1` is the same model continuing *with
the reviewer's feedback*, where a blind retry restarts with a fresh context — but
that was not part of what was approved, so the retry stays.

## 3. The profile images

`ops/profile-images/` — four Dockerfiles, `build-images.sh`, `fabro-pg-ensure.sh`,
`warm-build-backend.sh`. `ops/profile-images/README.md` is the detail.

### 3a. jelly-swipe had no node, and its `ci.sh` calls `npm ci`

This one is not an optimisation, it is a live landmine. jelly-swipe's
`.fabro/ci.sh` gained a frontend half on `main @ 842df8a` on 2026-09-17:

```sh
cd frontend && npm ci && npm run typecheck && npm run lint && npm test
```

`fabro-python:local` had no node and no npm. Under `set -euo pipefail` that
`ci.sh` exits 127, `validate` exits 1, and **every task of every jelly-swipe run
enters the four-tier rework ladder at 45 minutes a tier**, against a failure no
code change can fix. The last completed run cloned `main` before that commit —
its `ci.sh` stopped at pytest, which is why it passed and why nobody has hit it.

Verified in the rebuilt image, offline: the full `ci.sh` — 661 backend tests plus
the 356 frontend tests that had no npm to run them — passes in 43s.

### 3b. Version drift against each repository's own CI

| image | was | now | the repo's CI says |
|---|---|---|---|
| `fabro-python` | no node | node 20.19.5 | `node-version: 20` |
| `fabro-python-node` | no python at all | baked CPython 3.13 | `python-version: "3.13"` |
| `fabro-ts` | no python, no uv | uv + baked CPython 3.14 | `python-version: "3.14"` |
| `fabro-rust-node` | node 20.19.2, npm 9.2.0 | node 22.23.2 | `node-version: 22` |

writers-app's `ci.sh` has been mirroring a CI that pins node 22 while running on
node 20. `npm 9.2.0` against node 20 is not a pairing npm ever shipped — it came
from Debian's package split.

### 3c. PostgreSQL in the two images whose tests need one

`fabro-pg-ensure` starts a cluster that is `initdb`'d at image build time. It
gives exactly what a `postgres:N` GitHub Actions service gives a job, because
that is what the repositories' `conftest.py` files are written against.

Verified offline in `fabro-python-node:local`: cluster ready in 1s, then
**2430 passed, 4 skipped in 135s** of the lawncare-saas backend suite that its
`ci.sh` currently excludes with the comment "pytest needs Postgres".

### 3d. Warmed caches

Baked layers at fixed paths with fixed `ENV` variables. `build-images.sh` warms
them from each repository's real lockfiles nightly and gates the result on two
checks — an offline cache check and an online run of the repo's own `setup.sh`.

**Be honest about what this is worth.** Measured in the built image on
jelly-swipe: a *cold* install with the network up is 1s for `uv sync` and 4s for
`npm ci`. Warm, about a second each.

**Dependency caching on this host is worth seconds per run, not hours.** The
Sandcastle result does not transfer, and the reason is structural rather than a
missing feature: Sandcastle made a git worktree per attempt, so every attempt
paid a cold install, while a fabro run gets **one sandbox for its whole life** and
`setup.sh` is idempotent — only the first install of a run is ever cold. The
cache Sandcastle needed is one fabro gets for free by not throwing the workspace
away.

What it does buy, and why it is worth keeping now that it is built:

- every install is network-independent, so a PyPI or npm blip can no longer fail
  `validate` and push a task into the rework ladder;
- about 140 MB per run stops crossing the wire, and 1.2 GB of cargo registry for
  writers-app, where it *is* also real time;
- the two build-time gates are the real product. They have already caught four
  things a green `docker build` did not: the missing npm, an empty cargo
  registry, an unwarmed root uv project in womens-fantasy-sports quietly pulling
  a 60 MB manylinux zizmor wheel every run, and a `--no-install-project` that
  skips only the root of a uv workspace.

## Deploying this

Workflow changes are live on `main` with nothing to copy. The images are not:

```sh
ssh andrew@10.10.0.32
mkdir -p ~/profile-images-build
# from the Mac:
rsync -a --delete ops/profile-images/ andrew@10.10.0.32:~/profile-images-build/
ssh andrew@10.10.0.32 'cd ~/profile-images-build && ./build-images.sh'
```

Then install the nightly cron (`23 3 * * *`), clear of the schedules. The
environment records do not change — same four tags.
