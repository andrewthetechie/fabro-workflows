# Tier 3 — Smaller items

Five independent items. None is urgent; none depends on another. Item 4 is a trap that
only springs on a version bump, so it should be recorded in the upgrade runbook even if
nothing else here is done.

---

## 1. `issue-triage` still creates a run branch

`.fabro/workflows/issue-triage/workflow.toml:21` sets:

```toml
[run.run_branch]
push = false
```

with the comment *"Nothing is committed and nothing is pushed."* The first half is wrong.
`RunBranchSettings::default()` is `enabled: true, push: true`
(`lib/foundation/fabro-types/src/settings/run.rs:1107-1113`), and `push = false` suppresses
only the push. Fabro still creates `fabro/run/<id>` and takes a local checkpoint commit
after every stage.

Cheap because `issue-triage` writes only to `/tmp/fabro`, so `git add -A` finds nothing —
but the branch is still created, and the comment is untrue.

`enabled = false` is the documented setting for a read-only workflow, and the
`fabro-workflow` skill says so explicitly: *"Set `[run.run_branch].enabled = false` and
`[run.pull_request].enabled = false` on read-only workflows so a host project's
`.fabro/project.toml` defaults cannot turn the run into a branch or PR."*

```toml
[run.run_branch]
enabled = false
```

Update the comment to match. Do **not** apply this to `pr-review`, whose
`enabled = true, push = false` is deliberate and load-bearing: checkpoints commit onto the
PR head branch and `deliver` does the pushing.

---

## 2. Give bridge-fired `pr-review` runs a useful title

See `10-gap-run-issue-mapping.md` for why automation-fired runs cannot be fixed. Bridge-
fired runs can, because `fire-pr-review.sh` builds the `RunIntent` itself.

`RunIntent` carries `title: Option<String>` (`lib/foundation/fabro-types/src/run_intent.rs:20`).
When absent, the server generates one with an LLM from the goal and `args.inputs`
(`lib/apps/fabro-server/src/run_title_generation.rs`, prompt at
`lib/apps/fabro-server/src/prompts/run_title.md.j2`).

The script already resolves `ISSUE_NUMBER` and sends it only in `args.labels`, where it is
visible solely in the run **Settings** tab. Two options, either of which is a one-key `jq`
change in the intent builder:

```jq
# deterministic — no LLM call, exact text
title: ("pr-review " + $repo + "#" + $prs + (if $issue == "" then "" else " (issue #" + $issue + ")" end))
```

or feed the generator instead, by moving `issue_number` into `args.inputs` alongside
`pr_number`. Unused inputs are harmless — only *referenced-but-undefined* template
variables are diagnostics — and the title prompt explicitly says *"If the run names an
identifier — work order, ticket, issue, PR number — put it next in canonical uppercase"*.

**Prefer the deterministic title.** It costs no model call, cannot drift, and this
deployment has enough LLM-shaped surprises already.

---

## 3. `[run.artifacts]` is unused and the review evidence dies with the sandbox

Every contract file, diff, verdict and rendered report lives under `/tmp/fabro/`. Artifact
globs are **workspace-relative and reject absolute patterns**
(`docs/public/execution/run-configuration.mdx`, `[run.artifacts]`: *"Absolute patterns and
patterns containing a `..` segment are invalid"*), so none of it is collectible.

After `ops/fabro-sandbox-sweep.sh` reaps the container at `GRACE_HOURS=48`, the only
surviving record of a review is the PR comment. `fabro artifact list` returns nothing for
every run this deployment has ever produced.

The fix pairs two settings:

```toml
[run.checkpoint]
exclude_globs = ["**/.fabro-review/**"]

[run.artifacts]
include = [".fabro-review/*.json", ".fabro-review/*.md", ".fabro-review/*.patch"]
```

with the workflow writing its contracts to a workspace-relative `.fabro-review/` instead
of `/tmp/fabro/`. The exclude glob keeps them out of checkpoint commits and out of the PR
diff; the include glob captures them after each stage.

**Tradeoffs, and they are why this is Tier 3:**

- It moves every path in every prompt and every gate script. That is a large, contract-
  touching change for an observability win.
- A wrong exclude glob commits review scratch into a PR. On `lawncare-saas` a merge is a
  deploy.
- Collection limits are 100 files, 10 MB per file, 50 MB per collection, and it runs
  **after every stage** — `backlog`'s per-task churn would hit that repeatedly.

**Recommendation: `pr-review` only, if at all.** Its contracts are few, its run is
bounded, and its reports are the ones worth reading a week later. Leave `backlog` on
`/tmp/fabro/`.

---

## 4. `[run.meta_branch]` is retired upstream — an upgrade trap

All three `workflow.toml`s set `[run.meta_branch] push = false` with a comment explaining
that nothing reads the pushed metadata branch and it would otherwise accumulate one dead
branch per run forever.

That reasoning was correct and the setting works **on the deployed 0.354.0-nightly.0**.

Metadata branches were removed in commit `ec9ea5c7b` ("Remove Git run metadata branches",
2026-09-05), first shipped in **v0.355.0-nightly.0**. At 0.357 the table is parsed and
discarded — `lib/foundation/fabro-config/src/layers/run.rs:340` is annotated *"Legacy
`[run.meta_branch]` input, accepted and ignored on config load"* — and
`docs/public/execution/run-configuration.mdx` states *"Metadata branches have been
retired… You can remove the table from your configuration."*

**Nothing to change now.** The trap is that the setting becomes inert silently on the
version bump `ops/README.md` is already waiting on, and metadata branches do not come
back — the feature is gone, so there is nothing to leak. The risk is the opposite of what
the comments describe: an operator reading them after the upgrade will believe a suppressed
push is still doing work.

**Action:** one line in `ops/README.md`'s upgrade runbook — *"from 0.355, `[run.meta_branch]`
is accepted and ignored; the tables in all three `workflow.toml`s become dead config and
can be deleted"* — and delete the tables as part of that upgrade, not before.

---

## 5. There is no CI in this repository

`AGENTS.md` opens with *"Pushing to `main` deploys"* and *"Those runs open pull requests
and force-push branches in four real repositories. There is no staging branch, no review
gate, and no rollback other than another commit."*

There is no `.github/` directory. Validation is a manual rsync-into-the-container sequence
documented in `AGENTS.md`, run at the author's discretion.

Two checks already documented there would have caught real, shipped bugs:

1. `fabro validate` on all three `workflow.toml`s in the container.
2. `python3.11 -c 'import tomllib; tomllib.load(open("workflow.toml","rb"))'` — the dotted
   model key that loses its quotes, becomes a nested table, and returns 422 at fire time
   with nothing reported until then.

A third is cheap and not yet documented: `sh -n` over each extracted `script` attribute,
which `AGENTS.md` already names as the POSIX-shell guard but which nothing runs
automatically.

The obstacle is that `fabro validate` needs the container, and the container is on the LAN.
Options, cheapest first:

- A `pre-push` hook on both checkouts running the TOML parse and `sh -n` locally. Catches
  two of three without any network.
- A self-hosted runner on the fabro host. Catches all three, adds a runner to operate.
- A GitHub-hosted job that runs the TOML and shell checks only, and skips `fabro validate`.

**Recommendation: the `pre-push` hook.** It matches how this repo is actually worked — two
checkouts, direct commits to `main` — and needs no new infrastructure. `fabro validate`
stays manual and stays in `AGENTS.md`.
