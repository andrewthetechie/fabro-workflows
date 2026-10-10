# Deploying to the server after a merge to `main`

Moved verbatim from `AGENTS.md` on 2026-10-09. `AGENTS.md`, *Deploying*, has the summary.

`make deploy` does all of it: every step below except the fabro `compose up`, then
the host-matches-repo diffs. It refuses a checkout that is dirty or not at
`origin/main` (`ALLOW_DIRTY=1` overrides). It rebuilds the scheduler and the profile
images only when their inputs changed since the last successful build. That is judged
by an rsync itemize plus a git-tree-hash stamp in `~/.fabro-deploy-stamps/` on the
host, because every scheduler rebuild re-dates the draft-14 window (`FORCE=1`
rebuilds anyway, `SKIP_IMAGES=1` skips the images). Single steps are
`make deploy-scheduler`, `deploy-images`, `deploy-scripts`, `deploy-notify`,
`deploy-compose`, `provision` and `verify-host`. `make compose-up CONFIRM=1` is the
only target that restarts fabro. The logic lives in `ops/deploy-host.sh`. The
commands below are what it runs, and the escape hatch when it cannot.

**Workflow changes need no deploy.** Every automation resolves
`.fabro/workflows/**` from `main` at fire time, so a merged graph or prompt is live
on the next run with nothing to copy.

**`ops/` changes do.** Nothing syncs that tree; the host keeps its own copies. After
a merge that touches `ops/` or `backlog/scripts/`, deploy what changed:

```sh
cd ~/Documents/code/fabro-workflows && git pull --ff-only

# the notify script the backlog hooks call, by absolute path, from the container
scp .fabro/workflows/backlog/scripts/discord-notify.sh andrew@10.10.0.32:/tmp/
ssh andrew@10.10.0.32 'docker cp /tmp/discord-notify.sh \
  fabro-fabro-1:/storage/scripts/discord-notify.sh && rm /tmp/discord-notify.sh'

# the sandbox profile images. Not a file copy: build-images.sh clones each target
# repo on the host, warms that repo's caches from its own lockfiles, and gates the
# result on an offline cache check plus a live run of the repo's .fabro/setup.sh.
# A failed build leaves the previous image tagged and in use. Since ADR 0016 the
# build also runs `cargo test` and the musl `cargo build` for the fabro-io crate,
# which must sit beside the build tree at ~/fabro-io (build-images.sh resolves it as
# $HERE/../fabro-io). Its `cargo test` also compiles every Stage-manifest output
# schema, which must sit beside it at ~/fabro-io-schemas; the build fails without
# them rather than skip the test. All three trees are synced (profile-images first,
# then the crate, because build-images.sh reads it to name the expected fabro-io
# version, then the schemas). `--exclude target` keeps the Mac's build dir behind.
rsync -a --delete ops/profile-images/ andrew@10.10.0.32:~/profile-images-build/
rsync -a --delete --exclude target ops/fabro-io/ andrew@10.10.0.32:~/fabro-io/
rsync -a --delete .fabro/workflows/_io/schemas/ andrew@10.10.0.32:~/fabro-io-schemas/
ssh andrew@10.10.0.32 'cd ~/profile-images-build && ./build-images.sh'

# the coder scheduler. Its compose service builds from ./scheduler, resolved
# relative to the compose file, so the tree has to sit beside it on the host.
# From draft 09 this deploy ARMS the dispatch loop *and* releases leases: the
# container starts taking a box and creating real runs on its own, and a GitHub
# token without `issues: write` is enough to make every dispatch fail. Draft 08's
# "nothing releases a lease until draft 09, so clear the rows first" is spent —
# `reconcile.py` releases on a terminal run and clears stale rows at startup, so
# *Breaking a stuck lease* in ops/README.md is now only the escape hatch. The rsync is
# load-bearing before the build: the host tree is whatever was last copied there,
# and `--build` alone happily rebuilds an older one.
# `up -d scheduler` names one service and never recreates fabro, which matters:
# a fabro restart fails every in-flight run. The tree carries no credential — the
# scheduler reads its own from ~/fabro/scheduler.env (NOT ~/fabro/.env, which
# holds fabro's SESSION_SECRET). Create it once per host, with both variables:
# without GITHUB_TOKEN the queue is empty and the page says so; without
# FABRO_API_TOKEN every dispatch answers 503 and /health reports
# `fabro.configured: false`. The fabro token is the server's own dev token, read
# out of the volume rather than retyped — there is exactly one.
#   ssh andrew@10.10.0.32 'cd ~/fabro && umask 077 && { \
#     printf "GITHUB_TOKEN=%s\n" "$(gh auth token)"; \
#     printf "FABRO_API_TOKEN=%s\n" "$(docker exec fabro-fabro-1 cat /storage/server.dev-token)"; \
#   } > scheduler.env && chmod 600 scheduler.env'
# `--build` is load-bearing: the runtime image carries `git`, which is how the
# scheduler fetches .fabro/ from main at dispatch time. An image built before
# draft 07 has no git and fails inside a dispatch.
rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  ops/scheduler/ andrew@10.10.0.32:~/fabro/scheduler/
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose up -d --build scheduler'

scp ops/docker-compose.yaml andrew@10.10.0.32:~/fabro/docker-compose.yaml
scp ops/fabro-branch-sweep.sh andrew@10.10.0.32:~/bin/fabro-branch-sweep.sh
scp ops/fabro-sandbox-sweep.sh andrew@10.10.0.32:~/bin/fabro-sandbox-sweep.sh
scp ops/fabro-monitor.sh andrew@10.10.0.32:~/bin/fabro-monitor.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-monitor.sh'

# the auto-merge kill switch. It talks to the API over the network and runs fine from
# this checkout, but an incident that starts with an ssh session should not also need
# a git clone, so it is deployed alongside the sweepers.
scp ops/fabro-auto-merge-switch.sh andrew@10.10.0.32:~/bin/fabro-auto-merge-switch.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-auto-merge-switch.sh'

# the operator's manual backlog escape hatch (draft 10). Same reasoning as the
# auto-merge switch: an incident that starts with an ssh session should not also need
# a git clone. Operator-only; no automation runs it, which is why it lives in ops/.
scp ops/fabro-fire-backlog.sh andrew@10.10.0.32:~/bin/fabro-fire-backlog.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-fire-backlog.sh'

# the automation-schedule switch (draft 13). The four backlog-<repo> schedules are off,
# which is what makes the scheduler the only producer of backlog runs; this is the
# switch that keeps that deliberate, and `on` is the only undo. Operator-only, same
# reasoning as the two above. It talks to the API over the network and needs no restart.
scp ops/fabro-automation-schedule.sh andrew@10.10.0.32:~/bin/fabro-automation-schedule.sh
ssh andrew@10.10.0.32 'chmod +x ~/bin/fabro-automation-schedule.sh'

# automations, when the provisioning script changed or a row is missing
FABRO_API_URL=http://10.10.0.32:32276/api/v1 FABRO_DEV_TOKEN=<dev token> \
  ./ops/provision-server-state.sh
```

Then confirm the host still matches the repo. Every one of these should print
nothing:

```sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-branch-sweep.sh' | diff - ops/fabro-branch-sweep.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-sandbox-sweep.sh' | diff - ops/fabro-sandbox-sweep.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-monitor.sh' | diff - ops/fabro-monitor.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-auto-merge-switch.sh' | diff - ops/fabro-auto-merge-switch.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-backlog.sh' | diff - ops/fabro-fire-backlog.sh
ssh andrew@10.10.0.32 'cat ~/bin/fabro-automation-schedule.sh' | diff - ops/fabro-automation-schedule.sh
ssh andrew@10.10.0.32 'cat ~/fabro/docker-compose.yaml'  | diff - ops/docker-compose.yaml
ssh andrew@10.10.0.32 'cat ~/fabro/scheduler/repos.toml' | diff - ops/scheduler/repos.toml
ssh andrew@10.10.0.32 'docker exec fabro-fabro-1 cat /storage/scripts/discord-notify.sh' \
  | diff - .fabro/workflows/backlog/scripts/discord-notify.sh
```

A compose change needs `cd ~/fabro && docker compose up -d` to take effect — or
`docker compose up -d --build scheduler` for a scheduler code or `repos.toml` change,
which names one service and leaves `fabro` alone. The host
auto-merge switch is a server variable, so flipping it needs no deploy and no
restart; it takes effect on the next run:

```sh
# one repo
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 \
  ./ops/fabro-auto-merge-switch.sh andrewthetechie/<repo> off
# all four
FABRO_DEV_TOKEN=<dev token> DRY_RUN=0 \
  ./ops/fabro-auto-merge-switch.sh host off
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro variable get FABRO_AUTO_MERGE'
```

Neither undoes a merge that already happened.

**Upgrading the fabro binary is a separate, deliberate act** — never part of a
routine deploy. The image tag is pinned by `FABRO_VERSION` in `~/fabro/.env`
because an upgrade applies SQLite migrations that the previous binary then refuses
to start against, which makes rollback a database restore rather than a tag change.
`ops/README.md` has the runbook and the current known-bad version.

An upgrade on the 0.354–0.362 line also **drops fabro's run history**: 0.362's
run-history activation rejects every stored run from 0.354 (finding R1), so the
drop and the `codecs` catalog rewrite happen in the cutover. The host reached
0.362.0-nightly.0 on 2026-09-25 (`docs/fabro-upgrade/` holds the series). The next
upgrade must be rehearsed on a copy first — `docs/fabro-upgrade/02-rehearsal.md`
is the template.
