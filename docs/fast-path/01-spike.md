# 01. Spike: confirm the GitHub and fabro facts this design assumes

## Outcome

`docs/fast-path/spike-<date>.txt` records the answer to S1-S9, each with the exact command
and its output (secrets redacted). Where an answer differs from 00, this task edits 00 (the
fact, the contract or the decision that depends on it) in the same commit, and says so at
the top of the spike file.

## Why

ADR 0024 rests on GitHub's mergeability fields, on a rebase through the API with the host
token, and on four fabro behaviours. The fabro facts in 00 were read from `context/fabro` at
`5f82fb086`, which is newer than the 0.362.0-nightly.0 on the host. A wrong fact here
becomes a wrong graph in tasks 05-08, and `fabro validate` cannot see it.

## Change

No code. Run each probe, record the output, and change nothing on the host or in a
repository that the probe does not need. **S2 changes a real repository**: it opens one
scratch PR in jelly-swipe and closes it. Do nothing else that writes.

### S1. `mergeStateStatus` per repository

For each of the four repositories, find an open PR that is behind its base and has no
conflict (or note that there is none), and read:

```sh
gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){mergeable mergeStateStatus headRefOid baseRefName}}}' -F o=andrewthetechie -F r=<repo> -F n=<pr>
```

Expected: `BEHIND` on jelly-swipe (strict protection) and `CLEAN` on the other three. If a
repository has no such PR, S2's scratch PR gives jelly-swipe's answer, and for the others
record "not observed".

### S2. A rebase through the API, with the host token

On the host, as the token the sandbox gets:

1. Create a branch in jelly-swipe from `main~3` with one commit that adds
   `docs/.merge-queue-spike` (author `noreply@fabro.sh`). Open a **draft** PR titled
   `chore: merge-queue spike (do not merge)`.
2. Read `id`, `headRefOid` and `mergeStateStatus`.
3. Run `updatePullRequestBranch(input:{pullRequestId:<id>, expectedHeadOid:<head>,
   updateMethod: REBASE})` and record the returned `headRefOid`.
4. Record the rebased commit's author and committer (`gh api repos/.../commits/<sha>`).
5. Run the mutation again with the **old** `expectedHeadOid`, and record the error text.
   This is the overlapping-sweep case.
6. Close the PR and delete the branch.

Record whether `git log origin/main..<branch>` on `main` since `main~3` touched
`.github/workflows/`. If it did, and step 3 succeeded, the workflow-scope question is
answered. If it did not, run `gh auth status` on the host and record the token scopes. If
`workflow` is absent, the question stays open. Note it in 00's open questions, and task 07
must handle the refusal as a `retryable:` hand-off.

### S3. The inventory query

Run the query that task 06 will use (draft it from 00 C7 and C8) against a repository with
an open agent PR. Confirm each field exists and record its shape:
`statusCheckRollup.contexts` with `CheckRun{name status conclusion detailsUrl}` and
`StatusContext{context state}`, `comments{author{login} body createdAt}`,
`reviewThreads{isResolved}`, `labels`, `mergeStateStatus`, `reviewDecision`. Record how a
failing CheckRun maps to its Actions run id (`detailsUrl` or
`checkSuite.workflowRun.databaseId`), and how to read that run's attempt number
(`gh run view <id> --json attempt`).

### S4. `GET /runs` as the guard needs it

```sh
curl -fsS -H "Authorization: Bearer $TOKEN" "$API/runs?status=running&status=runnable&status=pending&page[limit]=100"
```

Record, for one row, the field paths of: the run id, the workflow slug, the target
repository, `created_at` and the status. Confirm that a run created from an automation
names its automation (or its target repository), so the guard can tell
`merge-queue-jelly-swipe` from `merge-queue-writers-app`. Record whether `GET /runs`
accepts a `workflow=` filter.

### S5. Retention on 0.362

```sh
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro system prune --help'
ssh andrew@10.10.0.32 'cd ~/fabro && docker compose exec -T fabro fabro system prune --workflow dependabot --older-than 30d'
```

The second command must be a dry run (no `--yes`). Record whether `--workflow` exists, what
the dry run lists, and the bytes it reports. If `--workflow` does not exist on 0.362, record
the `DELETE /runs/{id}` response for an unknown id (404 expected) as the fallback.

### S6. The cost of one minimal run

Measure a run that only runs a short command, against one repository per environment
(`python`, `python-node`, `ts`, `rust-node`). If you can run a scratch two-node command graph
in the container without a commit to `main` (for example `fabro run` on a file copied into
the container), do that. Otherwise skip S6 here and say so. Task 05's first hand-fired
`merge-queue` run then makes the measurement. Record, per environment, the time from
creation to the first node, the time to finish, and the size that `fabro system df -v`
reports for that run. Then delete the runs. If the writers-app checkout is the slowest by a
large margin, record it. Task 09 uses the numbers to decide whether the merge queue needs a
lighter environment.

### S7. Host headroom for a cap of 6

During a busy hour (two `backlog` runs live), record `free -g`, `nproc` and `docker stats
--no-stream` on the host. State whether two more sandboxes of the largest environment
(`rust-node`: cpu=4, mem=8GB) fit.

### S8. Schedule overlap on 0.362

Read `automation_scheduler.rs` at the commit that the host binary was built from (`fabro
--version` gives it). If that source is not in `context/fabro`, record the test instead: an
automation with `* * * * *` on a graph that sleeps 150 s creates a second run while the
first is still running. Delete the automation and the runs afterwards.

### S9. GitHub merge queue and auto-merge

Record `gh api repos/andrewthetechie/<repo> --jq '{owner: .owner.type, allow_auto_merge}'`
for the four repositories. `owner.type = User` confirms that GitHub's merge queue is not
available (it needs an organization).

## Acceptance

- The spike file answers S1-S9, each with its command and output.
- 00 agrees with the spike file. Each fact marked † in 00 is either confirmed or corrected.
- The S2 scratch PR is closed and its branch deleted. The S6 runs and any S8 automation are
  deleted.

## Tests

None. This task changes no code.

## Depends on

Nothing.
