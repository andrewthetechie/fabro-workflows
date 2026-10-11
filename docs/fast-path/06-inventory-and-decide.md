# 06. `merge-queue`: `inventory` and `decide`

## Outcome

`inventory` reads every open `merge-on-green` PR in the repository from GitHub and writes
one normalised record per PR to `/tmp/fabro/mq/prs.json`. `decide` turns `prs.json` and
`env.json` into `/tmp/fabro/mq/plan.json` (C8), with one action per PR. `decide` makes no
network call, so its whole rule table is tested with fixtures.

## Why

ADR 0024 D3, D4 and D5. Every safety rule of the queue is a row in C8. Put all of them in
one pure step that is tested offline, and keep the network in `inventory` and `act`. Then a
wrong decision is a failing fixture, not a wrong merge.

## Change

Read 00: C1, C7, C8, P5-P7, rules 3 and 5. Read the spike file, sections S1 and S3, for
the field names. Read `watch_checks` in `review-merge.fabro`, the model for the check
buckets.

### `inventory` (no routing schema, `timeout="3m"`)

1. Query with `gh api graphql --paginate` (or a cursor loop of at most 4 pages of 50):
   `repository.pullRequests(states: OPEN, labels: ["merge-on-green"], first: 50,
   orderBy: {field: CREATED_AT, direction: ASC})`, with these fields per PR: `number`, `id`,
   `title`, `body`, `isDraft`, `headRefOid`, `mergeable`, `mergeStateStatus`,
   `reviewDecision`, `labels(first: 50) { name }`, `reviewThreads(first: 100) { isResolved }`,
   `comments(last: 100) { author { login } body createdAt }`, and the head commit's
   `statusCheckRollup.contexts(first: 100)` with S3's `CheckRun` and `StatusContext`
   fields.
2. Normalise with one `jq` program (written to `/tmp/fabro/mq/norm.jq` by a heredoc, as
   `rm_setup` writes `render.jq`) into:

   ```json
   {"number": 412, "id": "PR_…", "title": "fix: …", "head": "<sha>", "draft": false,
    "merge_state": "CLEAN", "review_decision": "APPROVED", "labels": ["agent-authored", "merge-on-green"],
    "unresolved_threads": 0, "body_block": "<text between fabro:commit-body:start and :end>",
    "markers": [{"kind": "approved", "head": "<sha>", "at": "<iso>"},
                {"kind": "rebased", "from": "<sha>", "to": "<sha>", "at": "<iso>"}],
    "checks": {"total": 3, "fail": [{"name": "test", "run_id": 123, "attempt": null}],
               "pending": ["lint"], "pass": 1}}
   ```

   - `markers` contains only comments whose `author.login` equals `env.json`'s `login`, and
     whose **first line** matches C4 or C5. A handoff marker (C6) is not a link in the chain.
   - Check buckets follow `watch_checks`. A `CheckRun` with a status other than `COMPLETED`
     is pending. Conclusion `SUCCESS` is pass, and `NEUTRAL` or `SKIPPED` is skipping. Every
     other conclusion is fail. A `StatusContext` in `SUCCESS` is pass, `PENDING` or
     `EXPECTED` is pending, and `FAILURE` or `ERROR` is fail.
   - `run_id` is the GitHub Actions run id from S3's mapping, or `null` for a check that
     is not an Actions run.
3. For each `fail` entry with a numeric `run_id`, set `attempt` from
   `gh run view <run_id> --json attempt --jq .attempt`. A failed call leaves `null`, which
   `decide` treats as "cannot be rerun".
4. A failed query writes `stop_reason` (`inventory: could not read the merge-on-green PRs`)
   and exits 1. Zero PRs is a valid result: `[]`.

### `decide` (routing, `timeout="1m"`)

One `jq` program, heredoc-written, run as
`jq -n --slurpfile prs … --slurpfile env … -f /tmp/fabro/mq/decide.jq > plan.json`.
It applies C8's rows in order. The first row that matches wins. Details the table leaves out:

- **The chain (C7).** Find the newest `approved` marker. Starting from its `head`, follow
  each later `rebased` marker whose `from` equals the current link. The expected head is
  the last link, and `approved_at` is the approved marker's `at`. The "pending for 2 hours"
  clock starts at the newest marker in the chain.
- **Conventional Commit** uses `merge_gate` check 11's regex. The body check is `body_block`
  non-empty and containing a line that starts with `Resolves #` (check 12).
- **A rebase in flight** is any PR whose head equals the `to` of its newest `rebased` marker
  and whose checks have a pending entry. When one exists, every `BEHIND` PR gets `wait`
  with reason `another rebase is waiting for CI`. Otherwise the oldest `BEHIND` PR (by
  `approved_at`) gets `rebase`, and the rest get `wait`.
- **Rerun** only when `fail` is non-empty, every `fail` entry has a numeric `run_id`, and
  every `attempt` is `1`. `rerun_ids` lists the unique run ids.
- **The merge cap.** After all rows, keep `merge` for the first `merge_cap` entries in
  `approved_at` order. The rest become `wait` with reason `merge cap of 5 per tick reached`.
- `subject` is the title. `act` re-reads it before it merges.

Print `{"context_updates":{"plan_merge":<n>,"plan_rebase":<n>,"plan_rerun":<n>,"plan_handoff":<n>,"plan_wait":<n>}}`.
These counts go into the run's events, so a recheck can read them.

## Acceptance

- `make check` passes.
- The fixtures below pass, and each C8 row is covered by at least one fixture.

## Tests

Add `merge-queue — decide` to `ops/test-task-gates.sh`. Each case is a `prs.json` fixture plus
an expected `plan.json` subset (`number`, `action`, the reason's prefix):

1. Approved head, green, `CLEAN`: `merge`.
2. No approval marker: `handoff`, not retryable.
3. Approval marker written by another login: `handoff` (no approval).
4. The head differs from the approved head, with no rebase marker: `handoff`, "head moved".
5. Approved at A, rebased A→B, the head is B, green, `CLEAN`: `merge`.
6. Approved at A, rebased A→B, the head is C: `handoff`.
7. Two approvals: the older one at A, the newer one at B, and the head is B: `merge`. A
   newer review supersedes the older one.
8. Each of draft, `architecture`, `ai-review-needs-human`, no `agent-authored`,
   `CHANGES_REQUESTED`, and 1 unresolved thread: `handoff`.
9. Title `Fix the thing`: `handoff`. A body block without `Resolves #`: `handoff`.
10. `DIRTY`: `handoff`, starting with `retryable: `.
11. `UNKNOWN`: `wait`.
12. Two `BEHIND` PRs, none in flight: the older one is `rebase`, the newer one is `wait`.
13. Two `BEHIND` PRs, plus a third PR in flight (head = its rebase `to`, a check pending):
    both `BEHIND` PRs `wait`.
14. Red, Actions, attempt 1: `rerun` with its run id. Red, attempt 2: `handoff`,
    `retryable: CI failed after one rerun`. Red, `run_id` null: `handoff`, not retryable.
15. Pending, newest marker 1h59m old: `wait`. 2h01m old: `handoff`, retryable.
16. No checks at all, marker 10 minutes old: `wait`.
17. Green, `BLOCKED`: `handoff`. Green, `HAS_HOOKS`: `merge`.
18. Seven green `CLEAN` PRs: the five oldest are `merge`, the other two are `wait` (cap).
19. `[]`: `[]`, and every count is 0.

## Depends on

05, and 01 (S1 and S3).
