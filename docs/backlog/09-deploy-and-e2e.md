# Task 09 — Deploy the workflow and run a live E2E on jelly-swipe

**Depends on:** task 08 (clean validation) **and** task 01 (both new models registered).
**Do before:** tasks 10–12.
**LLM level:** **use the smarter LLM.** This task fires live runs against real
repositories and requires judgment about what a partially-working run means.

## Goal

Push the redesigned workflow to `andrewthetechie/fabro-workflows` and prove, with one
live run on jelly-swipe, that the seven regressions this redesign targets are actually
fixed.

## Stop — three things need the operator's explicit go-ahead

Do not do any of these without asking first. Ask for all three at once, then proceed.

1. **The force-push to `andrewthetechie/fabro-workflows` main.** The new layout
   replaces the old one wholesale, and the staging directory is not currently a git
   repo, so this is a fresh history over the existing main. All four production
   automations pin `andrewthetechie/fabro-workflows@main` and re-read it on their next
   `*/15` fire — the moment this lands, **all four repositories start running the new
   workflow**, not just the test repo.
2. **Re-labelling a jelly-swipe issue** (step 3 — either #347 or a new one).
3. **Firing the manual run.**

## Step 1 — Decide what happens to the other three automations during the test

Because the push changes production for `womens-fantasy-sports`, `lawncare-saas`, and
`writers-app` simultaneously, check whether they can be paused for the duration:

```sh
TOK=<dev token from FABRO-DEPLOYMENT-LOG.md>
curl -fsS -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/automations | python3 -m json.tool
```

Look for an enabled/paused/disabled field and a corresponding PATCH. If one exists,
propose pausing the three non-test automations to the operator and re-enabling them
after the E2E verdict. If none exists, say so and proceed — the blast radius is
"three repositories run the new workflow one cron tick early", which is acceptable
given the old workflow is known-broken, but the operator should hear it stated rather
than discover it.

## Step 2 — Push the workflow repository

```sh
cd ~/.fabro-deploy/fabro-workflows
git init
git add -A
git status --short          # review this: no secrets, no .env, no *.bak, no schemas/
git commit -m "Redesign backlog workflow around file-backed contracts"
git remote add origin git@github.com:andrewthetechie/fabro-workflows.git
git push --force origin HEAD:main
```

Before `git commit`, read `git status --short` yourself. The repository is **public**.
Nothing with a key, token, or webhook URL may be in it. If `git status` shows anything
you did not expect from task 07's 12-file list, stop and ask.

Confirm what actually landed:

```sh
gh api repos/andrewthetechie/fabro-workflows/contents/.fabro/workflows/backlog --jq '.[].name'
```

## Step 3 — Prepare a test issue on jelly-swipe

Issue **#347** is currently labelled `agent-in-progress`, left over from cancelled run
`01M2EDE3KSE2CG36JFEHK0FZY5`. The `acquire` stage skips anything carrying that label,
so as-is it is invisible to the loop.

Pick one:

- **Reuse #347** — swap the label back: remove `agent-in-progress`, add `agent`. Best if
  #347 is a genuinely small, well-specified issue.
- **Create a fresh issue** — better for a first E2E, because you control the scope.
  Make it genuinely small and genuinely real (a one-file change with clear acceptance
  criteria; not a "hello world" no-op, which would exercise `no_work` instead of the
  coding path). Label it `agent`.

Either way, confirm the state before firing:

```sh
gh issue list -R andrewthetechie/jelly-swipe --label agent --state open --json number,title,labels
```

Exactly one issue should be labelled `agent` and **not** `agent-in-progress`, or
`acquire` will pick an issue you did not intend.

## Step 4 — Fire the run and watch it

```sh
TOK=<dev token>
curl -fsS -X POST -H "Authorization: Bearer $TOK" \
  http://10.10.0.32:32276/api/v1/automations/backlog-jelly-swipe/runs
```

No request body: `POST /automations/{id}/runs` declares none and ignores one, and
`backlog` takes no inputs anyway. This endpoint does start the run — unlike a raw
`POST /runs`, which leaves it `submitted`.

Capture the run id from the response. Then poll:

```sh
RUN=<run id>
curl -fsS -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/runs/$RUN/stages | python3 -m json.tool
curl -fsS -H "Authorization: Bearer $TOK" http://10.10.0.32:32276/api/v1/runs/$RUN | python3 -m json.tool
```

Poll the stages endpoint every 60–120s. Do not poll faster; it tells you nothing new and
the run is measured in tens of minutes.

## Step 5 — The seven things this E2E must prove

These are the specific regressions the redesign exists to fix. Check each explicitly
and record the evidence; a run that reaches `open_pr` while silently failing one of
these is **not** a pass.

| # | What to prove | How to see it | Was broken because |
|---|---|---|---|
| 1 | `decompose` succeeds | the `decompose` stage completes and `decompose_gate` emits `decomp_status` | the old schema had an array root, which fabro's object-only JSON extractor can never validate — every run burned ~500s and ~360k tokens on a guaranteed failure |
| 2 | The coder starts working fast | little gap between `improve_gate` and the first coder tool call; no long exploratory phase | the old plan stage's output went to a context key later agents cannot see, so the coder re-planned from scratch (~50 min/run) |
| 3 | Review routes autonomously | an `approved` verdict moves to `integrate`, **not** to `human_rescue` | with `output_schema` set, fabro never applied `context_updates`, and conditions are flat lookups, so every review fell through to rescue |
| 4 | The PR has a real title and body | `gh pr view` shows `agent: <issue title> (#N)` and a body with `## Summary` and `## Validation` | — |
| 5 | The PR carries `agent-authored`, and the issue's label swapped `agent-in-progress` → `Review` with a comment linking the PR | `gh pr view --json labels`, `gh issue view N` | — |
| 6 | Discord fired **once**, at `open_pr` | one message in the channel for this run | `discord-complete` was on `run_complete`, which has no matcher, so it fired on all ~384 quiet-exit runs/day |
| 7 | **No `fabro/meta/<run_id>` branch appeared on jelly-swipe** | the branch count command below, run before and after — it must not increase | fabro pushes a metadata branch per run and never deletes it; task 07 turned the push off, and this is the only place that proves the workflow-level override took effect |

The check-7 command, run once before firing the run in step 4 and once after it finishes:

```sh
gh api 'repos/andrewthetechie/jelly-swipe/branches?per_page=100' \
  --jq '[.[].name | select(startswith("fabro/meta/"))] | length'
```

If the count goes up, `[run.meta_branch] push = false` did not take effect at the
workflow level — move the block into the server's `/storage/.home/settings.toml`
instead, restart the container, and re-test. Record which one worked for task 11.

A `fabro/run/<run_id>` branch **is** expected — it is the PR head. Task 12 handles the
run branches from runs that do not open a PR.

## Step 6 — If it does not get there

The run will probably not be perfect the first time. Triage by where it stopped:

- **Failed at admission, no stages** — a template variable or a prompt path. Re-run
  task 08 check 4 and check 5.
- **422 from the automation POST** — TOML. Re-run task 08 check 3.
- **`decompose_gate` looped twice then routed on `invalid`** — the decompose prompt's
  contract description is not precise enough. Fix `prompts/decompose.md.j2` (task 03),
  push, re-fire.
- **Landed on `human_rescue` from a review** — read `/tmp/fabro/review/verdict.json` via
  the run's stage output. If the verdict was `approved` but the run still went to
  rescue, the routing fix did not take and that is a **stop-and-report**: it means the
  `context_updates` gate mechanism is not behaving as the redesign assumes.
- **Ran out of rework rounds** — that is the loop working as designed on a task that is
  too hard. Pick an easier test issue; do not raise the round cap.

Report what you changed and re-fire. Do not accumulate more than three re-fires without
reporting to the operator — each one is a real run against a real repository.

## Done when

- The workflow repo's main branch holds the 12-file layout from task 07.
- One live run on jelly-swipe reached `open_pr` and opened a PR.
- All seven items in step 5 are checked, with the evidence recorded for task 11.
- The issue's labels and the PR's labels are correct.
- Anything paused in step 1 is un-paused.

## Pitfalls

- **Never copy a token, key, or webhook URL into a file in the workflow repo, a
  commit message, or a plan file.** The repo is public.
- The dev token lives in `FABRO-DEPLOYMENT-LOG.md`. Read it there; do not echo it into
  a command that gets logged, and do not paste it into the run output you report.
- `git push --force` here is intentional and operator-approved — this replaces the
  layout. It is the only force-push in this series.
- A run that reaches `close_noop` proves almost nothing except that `acquire` and
  `decompose` work. If the test issue decomposes to `no_work`, pick a different issue
  and fire again; that is not a passing E2E.
- Watch the wall clock. A full run through decompose → several tasks → extra review →
  PR can take well over an hour. Do not interpret "still running" as "hung" before
  checking that a stage is actively progressing.
