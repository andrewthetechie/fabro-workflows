# 05 · Repo changes that are valid only on 0.362

## Outcome
The four `workflow.toml` files no longer carry the dead `[run.meta_branch]` table, and
`issue-triage` and `arch-review` no longer create a run branch at all (H5). The sweep scripts'
comments describe 0.362. Every gate passes.

## Why
From 0.355, `[run.meta_branch]` is "accepted but ignored"
(`docs/public/execution/run-configuration.mdx` at 0.362), so its comments now describe
behaviour that does not exist (`docs/research_improvements/03`, item 4). On 0.354 the same
deletion would have **re-enabled** metadata-branch pushes, which is why it waits for task 04
(overview rule 2). H5 is in scope by decision 6: neither package commits anything, so a run
branch is pure overhead.

## Read first
`00-overview-and-contracts.md`: rules 2, 5 and 6, and behaviour change 3. Invoke
`/fabro-workflow` before editing any `workflow.toml`.

## Precondition
`docker exec fabro-fabro-1 fabro --version` on the host prints `0.362.0-nightly.0`. If it does
not, stop. Do not push this task's commit to a host still on 0.354.

## Steps

1. **Delete `[run.meta_branch]`** and the comment block above it from all four files:
   `backlog/workflow.toml` (the block starts at the comment near line 46, "Do not push the run
   metadata branch"), `pr-review/workflow.toml` (~line 36), `issue-triage/workflow.toml`
   (~line 26) and `arch-review/workflow.toml` (~line 21). Do not touch `[run.run_branch]` in
   `backlog` or `pr-review`: `backlog`'s `push = false` is ADR 0011 D1 and stays load-bearing.

2. **H5.** In `issue-triage/workflow.toml` and `arch-review/workflow.toml`, change
   `[run.run_branch]` from `push = false` to `enabled = false`, with a one-line comment:
   *"Nothing in this package commits. No run branch and no checkpoint commits (0.362:
   `enabled = false` also skips checkpoint commits)."* Before committing, confirm that the
   `arch-review` graph needs no commit: `history` runs `git fetch`/`git log`, which need the
   clone and not a run branch. `file_issues` and the triage phase use `gh` only.

3. **Sweep script comments.** `ops/fabro-branch-sweep.sh` (header lines 2–5) says fabro pushes
   `fabro/meta/<id>`. Reword it: *"0.362 no longer creates `fabro/meta/*` branches; this still
   sweeps the ones earlier versions left."* **Do not change its prefixes** (`ops/README.md`
   warns never to widen them). Update the matching row in `ops/README.md`'s contents table.

4. **Gates**, all required:
   - `python3.11 -c 'import tomllib,sys;[tomllib.load(open(p,"rb")) for p in sys.argv[1:]]' .fabro/workflows/*/workflow.toml`
   - `./ops/test-task-gates.sh` (same PASS count as before)
   - `sh -n ops/*.sh`
   - In the container (overview rule 5): `fabro validate` of all four packages equals finding
     R4, and `check-routing-schemas.py` exits 0.

5. **Commit and push** (one commit, `chore(workflows): drop retired meta_branch tables; no run
   branch for triage and arch-review (fabro 0.362)`). The change is live on the next run.

6. **Prove H5 on a real run.** Fire `issue-triage` by hand for one repo. It takes no inputs,
   so the bodyless `POST /api/v1/automations/issue-triage-<repo>/runs` works **if** that row
   has an enabled API trigger (check `GET /api/v1/automations` first). Otherwise wait for the
   next `arch-review` schedule. Then:
   - the run succeeds, or ends with its normal "nothing to triage" exit;
   - `gh api repos/andrewthetechie/<repo>/branches --jq '.[].name' | grep "fabro/run/<run id>"`
     prints nothing, so no run branch was pushed. There never was a push, but confirm no local
     checkpoint error appears in `fabro events <id> -p` either.

## Acceptance criteria
- No `meta_branch` string remains under `.fabro/` (`grep -rn meta_branch .fabro` is empty).
- `issue-triage` and `arch-review` carry `enabled = false`. `backlog` and `pr-review` are
  unchanged apart from the meta table.
- All gates pass, and one `issue-triage` or `arch-review` run completed on 0.362 with H5.

## Report
The gate outputs, and the H5 proof run's id and outcome.
