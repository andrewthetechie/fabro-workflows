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
