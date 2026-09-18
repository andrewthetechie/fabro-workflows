# Fix `fire-pr-review.sh`: root the workflow version at `.fabro/`

## Tracer-Bullet Outcome
`~/bin/fabro-fire-pr-review.sh andrewthetechie/jelly-swipe <pr>` creates and
starts a real `pr-review` run again. It currently fails with HTTP 422 and creates
nothing.

## User Story
As the operator, I want the manual PR-review tool to work so that I can review a
pull request by hand — and so the scheduler has a proven, hand-runnable reference
for the same version-registration mechanism it will depend on.

## Description
This is a **bug fix that predates the scheduler**, placed first because it proves
the mechanism draft 07 is built on.

`commit 54c21be` gave `pr-review` an import of the shared review-and-merge graph:

```dot
review_merge [import="../_shared/review-merge/review-merge.fabro"]
```

`fire-pr-review.sh` registers a workflow version rooted at the package directory
(line 261, `pkg="$tmp/wf/.fabro/workflows/pr-review"`), so the `files` map has no
`_shared` entry and the `../` import cannot resolve. `WorkflowPath` forbids parent
segments, so the shared graph **cannot** be added at its `../` path — the version
has to be rooted one level up.

## Context Pack
- Source decisions: overview decision 14 (the standalone `pr-review` package
  stays and `fabro-fire-pr-review.sh` keeps being the way it is fired by hand),
  resting on overview **finding 2** — an automation cannot be parameterised, so
  the only route is `POST /workflow-versions` → `POST /runs` →
  `POST /runs/{id}/start`, which is exactly the mechanism draft 07 reuses. Q2(a)
  of the decomposition grilling put this first deliberately.
- Repo facts: the script is tracked at
  `.fabro/workflows/backlog/scripts/fire-pr-review.sh`. **It is not itself
  deployed.** `~/bin/fabro-fire-pr-review.sh` is a read-only wrapper
  (`docs/auto-merge/fabro-fire-pr-review-wrapper.sh`) that `exec`s the tracked
  script as it stands on `origin/main`, so the operator's manual fire and
  `backlog`'s automated one cannot diverge — copying this file over that path is
  the failure the wrapper exists to prevent. `AGENTS.md` carries the drift check
  (`ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-pr-review.sh' | diff - docs/auto-merge/fabro-fire-pr-review-wrapper.sh`).
  Its header documents an output contract that must not break: **the last line on
  stdout is the run id and nothing else; the last line on stderr is a one-line
  reason on every failure path.**
- Non-goals: changing the three-POST sequence, the `args.inputs` shape, the
  auto-merge switch read, or anything about the scheduler. This draft does not
  make the script enqueue (that is draft 13's predecessor work and is out of scope
  here).

## Delivery Strategy
- Shape: Prefactor — it removes the concrete obstacle that a `.fabro`-rooted
  version is unproven, and fixes a live bug on the way.
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files: `.fabro/workflows/backlog/scripts/fire-pr-review.sh` only.
- Interfaces and names: three variables in that script change meaning.
  Current, verbatim:

  ```sh
  ENTRYPOINT="workflow.fabro"                      # line 82
  pkg="$tmp/wf/.fabro/workflows/pr-review"         # line 261
  [ -d "$pkg" ] || die "$WORKFLOWS_REPO at $WORKFLOWS_REF has no .fabro/workflows/pr-review package"
  ```

  Target:

  ```sh
  ENTRYPOINT="workflows/pr-review/workflow.fabro"
  pkg="$tmp/wf/.fabro"
  [ -f "$pkg/$ENTRYPOINT" ] || die "$WORKFLOWS_REPO at $WORKFLOWS_REF has no $ENTRYPOINT"
  ```

  The enumeration below it is already root-relative and needs no change:

  ```sh
  ( cd "$pkg" && find . -type f | sed 's|^\./||' | sort ) > "$tmp/paths"
  ```

  It will now pick up `workflows/_shared/review-merge/review-merge.fabro`, which
  is exactly the point.

- Verified external contracts (all verified live against
  `http://10.10.0.32:32276/api/v1` on 2026-09-18):

  **The entrypoint is the graph, not the TOML.** With `entrypoint: workflow.toml`:

  ```
  422  workflow.toml selects graph `workflow.fabro`, but the version entrypoint is `workflow.toml`
  ```

  **Package-rooted fails** (`entrypoint: workflow.fabro`, files from the package dir):

  ```
  422  invalid import reference in `workflow.fabro`: `../_shared/review-merge/review-merge.fabro`
  ```

  **`.fabro`-rooted succeeds** (`entrypoint: workflows/pr-review/workflow.fabro`,
  26 files from `.fabro/`): `POST /workflow-versions` → `201`, then
  `POST /runs` → `201`, `lifecycle.status.kind == "submitted"`.

  `WorkflowPath` (`docs/public/api-reference/fabro-api.yaml:9296-9306`): paths are
  *"relative, at most 240 bytes and 16 components, and cannot contain empty, dot,
  parent, backslash, control, tilde-root, or drive-letter segments."*

  The `WorkflowVersion` body the script already builds is unchanged in shape:

  ```json
  {"entrypoint": "<path>", "files": {"<relpath>": "<content>"}, "workflow_dependencies": {}}
  ```

  `workflow_dependencies` stays `{}`: it keys **child-workflow version ids** for
  `stack.child_workflow`, not `import=` graphs. The shared graph travels in `files`.

- Behavior rules:
  - The version now carries the whole `.fabro/` tree (26 files today), including
    the `backlog` and `issue-triage` packages. That is accepted: registration is
    content-addressed and idempotent, and the entrypoint selects one graph.
  - A file that is not valid UTF-8 must fail loudly, not be skipped silently.
    The current `jq -Rs` already does this.
- Error and security rules: preserve the output contract exactly — the graph node
  and the operator both `tail -1`. No new secrets.

## Acceptance Criteria
- [ ] `DRY_RUN=1` prints a RunIntent whose `entrypoint` is
      `workflows/pr-review/workflow.fabro`.
- [ ] A real fire against an open PR returns `201` from `/workflow-versions`,
      `201` from `/runs` and `200` from `/runs/{id}/start`.
- [ ] The created run leaves `submitted` and reaches at least `validate_input`.
- [ ] The last stdout line is the bare run id.
- [ ] There is **no** deployed copy to keep in sync, and this criterion is the
      check that it stayed that way. `~/bin/fabro-fire-pr-review.sh` must still be
      the wrapper, not this script:
      `ssh andrew@10.10.0.32 'cat ~/bin/fabro-fire-pr-review.sh' | diff - docs/auto-merge/fabro-fire-pr-review-wrapper.sh` prints nothing.
      The fix reaches the host by being on `origin/main`, which the wrapper reads
      at every fire — confirm with
      `ssh andrew@10.10.0.32 'git -C ~/.fabro-deploy/fabro-workflows fetch -q origin main && git -C ~/.fabro-deploy/fabro-workflows show origin/main:.fabro/workflows/backlog/scripts/fire-pr-review.sh | grep ^ENTRYPOINT='`.

## Test Expectations
No unit-test framework — this is a POSIX `sh` operator script. Two checks:

1. **Static:** `sh -n .fabro/workflows/backlog/scripts/fire-pr-review.sh` exits 0.
2. **Live, concrete:** with `DRY_RUN=1`, against any repo/PR pair:

   ```sh
   DRY_RUN=1 ./fire-pr-review.sh andrewthetechie/jelly-swipe 1 2>/dev/null \
     | jq -r '.entrypoint' 2>/dev/null \
     || DRY_RUN=1 ./fire-pr-review.sh andrewthetechie/jelly-swipe 1 | grep entrypoint
   ```

   Expected literal: `workflows/pr-review/workflow.fabro`.

   Then one real fire against a genuinely open PR, asserting the three status
   codes above.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: "Fabro client: register a `.fabro`-rooted version, create and start a run"

## Labels
`bug`, `workflows/pr-review`, `priority:high`

## Estimate
Small

## Risk
2 - one script, currently broken, so the change cannot regress a working path.
Deploying to `~/bin` is a copy.

## Validator Stopping Point
`sh -n` passes, a real fire reaches `validate_input`, and the `~/bin` drift diff
is empty.
