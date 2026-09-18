# GitHub inventory with ETag caching, and a read-only queue page

## Tracer-Bullet Outcome
`http://10.10.0.32:32280/` shows the real, ranked queue of `agent`-labelled issues
across all four repos, refreshed every 60 seconds, and the logs show `304 Not
Modified` on the refreshes that found nothing new.

## User Story
As the operator, I want to see exactly what work is waiting and in what order, so
that I can tell at a glance whether the factory is starved or backed up — before
anything is dispatched.

## Description
Second vertical slice: GitHub client → SQLite cache → ranking → HTML page. Still
no dispatch, no fabro client, no leases.

Ranking implements overview decisions 4 and 5 in full, so the ordering visible on
this page is the ordering draft 08 will dispatch from.

## Context Pack
- Source decisions: overview decisions 4 (signed int, smallest wins), 5 (strict
  priority with a starvation ceiling `T`, default 4h), 8 (GitHub every 60s,
  conditional), and the Queue item contract.
- Repo facts: `CONTEXT.md` defines **Queue item**, **Starvation** and
  **Starvation ceiling**; use those words in the UI. The four repos and their
  priorities come from `repos.toml` (draft 04).
- Non-goals: dispatch, leases, reordering, drain, any write to GitHub. This page
  is strictly read-only.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/github.py
  ops/scheduler/src/fabro_scheduler/store.py
  ops/scheduler/src/fabro_scheduler/queue.py
  ops/scheduler/src/fabro_scheduler/templates/queue.html
  ops/scheduler/src/fabro_scheduler/app.py          (add GET /, GET /api/queue)
  ops/scheduler/tests/test_github.py
  ops/scheduler/tests/test_queue.py
  ```

- Interfaces and names:

  ```python
  @dataclass(frozen=True)
  class Issue:
      repo: str            # "owner/repo"
      number: int
      title: str
      labels: frozenset[str]
      first_seen: datetime  # when THIS scheduler first saw it; drives the ceiling

  @dataclass(frozen=True)
  class QueueItem:
      issue: Issue
      repo_priority: int
      override_rank: int | None   # set by draft 11; always None here
      waited: timedelta

  def fetch_issues(repo: str, token: str, etag: str | None) -> tuple[list[Issue], str | None, bool]:
      """Returns (issues, new_etag, changed). changed is False on a 304, and
      `issues` is then empty and must not be treated as 'no work'."""

  def rank(items: Sequence[QueueItem], ceiling: timedelta) -> list[QueueItem]: ...
  ```

- Verified external contracts (verified live against
  `https://api.github.com` on 2026-09-18 for `andrewthetechie/jelly-swipe`):

  Request:
  ```
  GET /repos/{owner}/{repo}/issues?labels=agent&state=open&per_page=100
  Authorization: Bearer <token>
  Accept: application/vnd.github+json
  If-None-Match: "<etag>"        (omit on the first call)
  ```

  Observed responses:
  ```
  cold          200   etag: "4d062fc1eae86d12a690af8d7727c7e7b45c914958b67ff5052aab421edc98dd"
                      x-ratelimit-limit: 5000   x-ratelimit-remaining: 4999
  conditional   304   x-ratelimit-remaining: 4999   x-ratelimit-used: 1   (empty body)
  ```

  **A 304 costs zero rate-limit quota** — `x-ratelimit-remaining` was unchanged
  across the pair. The ETag value must be sent back **including its surrounding
  double quotes**, exactly as received.

  Caution: `GET /issues` returns **pull requests as well as issues**. A PR object
  carries a `pull_request` key; an issue does not. Filter on that key's absence,
  not on anything else.

- Behavior rules:
  - Poll every **60s** per repo, staggered so all four do not fire in the same
    second.
  - Eligibility: `state == "open"`, labels contain `agent`, labels contain
    neither `agent-in-progress` nor `agent-stuck`, and the object has no
    `pull_request` key.
  - Ranking, in order:
    1. any item whose `waited > ceiling` (default 4h), oldest `first_seen` first;
    2. then `repo_priority` ascending (**smaller is more urgent**; negative is
       allowed and beats zero);
    3. then issue `number` ascending.
  - `first_seen` is persisted and **never reset** by a refresh, or the ceiling
    could never be reached.
  - A repo whose fetch fails keeps its last known items and is shown as stale on
    the page; it is not silently emptied.
  - `enabled = false` repos are excluded entirely.
  - SQLite at `/data/scheduler.db`. Tables: `issue_cache`, `repo_etag`.
- Error and security rules: the GitHub token comes from `GITHUB_TOKEN` in the
  environment. Never log it, never render it, never store it in SQLite. Log a
  `403` with the `x-ratelimit-remaining` value so a quota exhaustion is
  distinguishable from a permission problem — they look identical otherwise.

## Acceptance Criteria
- [ ] `uv run pytest` passes.
- [ ] `GET /` renders a table: repo, issue number, title, priority, waited.
- [ ] `GET /api/queue` returns the same data as JSON, in ranked order.
- [ ] Two consecutive refreshes with no GitHub change log one `200` then one
      `304`, and `x-ratelimit-remaining` is identical across both.
- [ ] A pull request carrying the `agent` label does **not** appear in the queue.
- [ ] An issue labelled `agent-in-progress` does not appear.
- [ ] An item older than the ceiling sorts above a lower-priority-number item.

## Test Expectations
Framework: **pytest** with `respx` (or `responses`) faking the GitHub HTTP layer —
no live network in tests. `uv run pytest` from `ops/scheduler/`.

Concrete case in `tests/test_queue.py`:

```python
def test_ceiling_beats_priority():
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    urgent_repo = QueueItem(issue=Issue("o/a", 1, "t", frozenset({"agent"}),
                                        now - timedelta(minutes=5)),
                            repo_priority=-99, override_rank=None,
                            waited=timedelta(minutes=5))
    old_low     = QueueItem(issue=Issue("o/b", 2, "t", frozenset({"agent"}),
                                        now - timedelta(hours=5)),
                            repo_priority=7, override_rank=None,
                            waited=timedelta(hours=5))
    assert [i.issue.number for i in rank([urgent_repo, old_low],
                                         ceiling=timedelta(hours=4))] == [2, 1]
```

Concrete case in `tests/test_github.py`: fake a `304` with no body and assert
`fetch_issues` returns `changed=False` and that the caller keeps the previously
cached items rather than emptying the repo.

## Dependencies
- Blocked by: "Scheduler skeleton: container, `repos.toml`, health endpoint"
- Why blocked: needs `RepoConfig`, `SchedulerConfig`, the FastAPI app and the
  `/data` volume mount that draft supplies.
- Blocks: "Lease state machine and dispatch loop"

## Labels
`feature`, `ops/scheduler`, `priority:high`

## Estimate
Medium

## Risk
2 - read-only against GitHub; the worst failure is a stale or empty page.

## Validator Stopping Point
`uv run pytest` green, `/` showing the real ranked queue for four repos, and a
logged `200` → `304` pair with unchanged `x-ratelimit-remaining`.
