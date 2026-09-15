# Task 07 — Deploy and run a live E2E on a real PR

**Depends on:** 06 (clean). **Blocks:** 08, 09.
**LLM level:** **use the smarter LLM.** This force-pushes to a real PR.

## Stop — ask the operator first

Do none of these without an explicit go-ahead:

1. **The push to `andrewthetechie/fabro-workflows` main.** It is additive (a new
   `.fabro/workflows/pr-review/` directory), so the four `backlog-*` automations are
   unaffected — verify that with `git diff --stat` before pushing.
2. **Creating the four `pr-review-*` automations.**
3. **Firing a run against a real PR** — it will rewrite that PR's branch.

## Step 1 — push the workflow

```sh
cd ~/.fabro-deploy/fabro-workflows
git status --short          # expect only .fabro/workflows/pr-review/*
git diff --stat             # expect nothing under workflows/backlog/
git add .fabro/workflows/pr-review
git commit -m "Add the pr-review workflow"
git push origin HEAD:main
```

## Step 2 — create the automations

Per task 05. Confirm with `GET /api/v1/automations` that all four exist with the right
`environment_id` and **no schedule trigger**.

## Step 3 — pick the test PR carefully

Not a PR anyone cares about. In order of preference:

1. A throwaway PR you open on jelly-swipe specifically for this: branch off `main`,
   one small real change, open it.
2. PR **#369** — `fabro/run/01M2DSYRX8CY1SGDVDF79TZJC4`, the titleless PR from the old
   broken backlog workflow. It has no value and is a genuine artifact.

**Do not** use #374 or #376 — they are the real output of the backlog E2E and are
evidence.

Record the head SHA before firing, so you can restore it:

```sh
gh pr view <N> -R andrewthetechie/jelly-swipe --json headRefName,headRefOid
```

## Step 4 — fire and watch

```sh
curl -fsS -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"trigger":"manual","inputs":{"pr_number":<N>}}' \
  http://<HOST>:32276/api/v1/automations/pr-review-jelly-swipe/runs
```

Poll `/api/v1/runs/{id}/stages` every 60–120s.

## Step 5 — what this E2E must prove

| # | Claim | How to see it |
|---|---|---|
| 1 | The input reached the workflow | `validate_input` succeeded; `claim` fetched the right PR |
| 2 | The run is on the PR branch, not `fabro/run/<id>` | `claim` ran `gh pr checkout`; the pushed head matches the PR's `headRefName` |
| 3 | The rebase ladder took the right branch | `rebase_state` in context is `not_needed`, `clean` or `conflict` and matches reality |
| 4 | Both reviewers produced valid verdicts | `standards_gate` / `spec_gate` emitted a status without retrying |
| 5 | The fixer read both reviews | `fix_result.json` `changes[].why` references reviewer findings |
| 6 | CI ran and gated | `validate` emitted `ci_ok` |
| 7 | The push was `--force-with-lease` | the PR head SHA changed; PR shows the rebase |
| 8 | Labels and comment landed | PR has `ai-review-complete`; comment lists what was done |
| 9 | **No `fabro/run/<id>` branch was pushed** | `gh api repos/.../branches` count unchanged — proves `run_branch.push = false` |

## Step 6 — triage

- **Failed at admission** — the `inputs` payload shape, or a prompt path. Re-run task 06
  checks 2 and 5.
- **422 from the automation POST** — TOML. Task 06 check 3.
- **Routing did nothing, run walked to `exit`** — a missing `output_schema="routing"`.
  Task 06 check 6.
- **`rebase_gate` rejected twice and went to needs-human** — read
  `rebase_result.json`. If `coders` produced markers, that is the ladder working; if
  `glm-5.3` also failed, the conflict is genuinely hard and the outcome is correct.
- **Force-with-lease rejected** — expected if anything touched the branch. The run
  should have stopped and labelled; confirm it did not force.

## Rollback

The branch's pre-run SHA is from step 3:

```sh
git push --force-with-lease origin <old_sha>:<head_ref>
```

Then remove the labels and the comment.

## Done when

- The workflow is on `fabro-workflows@main` and the backlog tree is untouched.
- Four automations exist, manual-trigger only.
- One live run reached `deliver` on a real PR.
- All nine claims in step 5 checked, with evidence recorded for task 09.
- No new `fabro/run/*` or `fabro/meta/*` branch appeared.

## Pitfalls

- **Never `--force`.** If the lease fails, stop — that is the design.
- Do not test on a PR whose branch you cannot restore.
- A run that ends at `mark_needs_human` is not automatically a failure — read the
  comment. Correctly declining a hard conflict is the system working.
