# Resolve the PR at release

## Tracer-Bullet Outcome
A backlog run whose PR auto-merged reaches a terminal state, and its history row records
`pr_number`, `pr_url` and `merged = 1`. A run that failed before opening a PR records
`pr_lookup = "none"`. A run released while GitHub is unreachable records
`pr_lookup = "failed"` and **still releases its box**.

## User Story
As the operator, I want the history to say whether each run's PR merged, because "the run
succeeded" and "the change shipped" are different facts and only one of them is why the
factory exists.

## Description
Add one helper to `reconcile.py` that turns a lease into PR fields, and call it from the
three terminal branches when building the outcome. The 404 path does **not** call it: a
run fabro has lost never reached `open_pr` in any way the scheduler can rely on, and the
lookup is not worth a GitHub call on a path that already knows nothing.

Three properties matter more than the happy path.

**It never raises.** A GitHub miss must not block a lease release. The scarcest resource
in this deployment is a coder instance, and holding one because `api.github.com` had a bad
minute would be a self-inflicted outage.

**It is honest about not knowing.** `pr_lookup` distinguishes `"found"`, `"none"` and
`"failed"`, because a null `pr_number` is reachable two ways — the run opened no PR, or we
could not ask — and those are opposite conclusions for an operator.

**It carries its own 5s timeout**, not the module's 15s, because it runs inside the
15-second release poll and a call that consumes a whole tick delays the release of the
*other* instance's lease.

## Context Pack
- Source decisions: ADR 0008 — merged state is a snapshot taken at release; the call is
  best-effort and never raises; `pr_lookup` is tri-state; the timeout is 5s.
- Repo facts: the run branch is `fabro/run/<run_id>` and the lease carries `run_id`, so
  the branch needs no lookup. `_requeue`'s label writes are already best-effort with the
  same shape — `try` / `except GitHubError` / `log.error` and carry on (`reconcile.py:355-365`).
- Non-goals: no lookup on the 404 path, no re-fetch on page load, no `merged` refresh
  after the fact. The record is point-in-time by decision.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/reconcile.py     (add _pr_fields, use it in 3 branches)
  ops/scheduler/tests/test_reconcile.py              (extend)
  ```

- Interfaces and names:

  Add to `reconcile.py`, beside `_outcome_from_run`:

  ```python
  # The branch fabro's checkpoint publishes for every run, and the only thing
  # needed to find that run's PR. `open_pr`'s own push is always
  # `Everything up-to-date`.
  RUN_BRANCH_PREFIX = "fabro/run/"


  def _pr_fields(lease: Lease, github_token: str) -> dict[str, object]:
      """The PR half of a `RunOutcome`, resolved from the run branch. Never raises.

      Returns the keyword arguments `RunOutcome` takes, so the caller splices it in
      with `**`. Three answers, and the caller must be able to tell them apart:

      * `pr_lookup="found"` -- there is a PR, and `merged` says whether it had
        merged at this instant;
      * `pr_lookup="none"` -- GitHub answered, and there is no PR on that branch.
        A run that failed before `open_pr` is the normal case;
      * `pr_lookup="failed"` -- GitHub could not answer. NOT the same as "none",
        and collapsing the two would tell the operator a run opened no PR when it
        opened one we failed to see.

      A GitHub miss must never block a lease release: the coder instance is the
      scarcest thing here, and holding one because api.github.com had a bad minute
      would be a self-inflicted outage. So every failure is caught, logged and
      turned into `"failed"`.
      """
      try:
          found = fetch_pull_for_branch(
              lease.repo, f"{RUN_BRANCH_PREFIX}{lease.run_id}", github_token
          )
      except GitHubError as exc:
          log.error(
              "reconcile: %s#%s run %s: could not resolve the PR, recording it as "
              "unknown: %s",
              lease.repo, lease.issue_number, lease.run_id, exc,
          )
          return {"pr_lookup": "failed"}

      if found is None:
          return {"pr_lookup": "none"}
      return {
          "pr_lookup": "found",
          "pr_number": found.number,
          "pr_url": found.url,
          "merged": found.merged,
      }
  ```

  Widen the existing github import in `reconcile.py` — it currently reads:

  ```python
  from .github import (
      IN_PROGRESS_LABEL,
      REQUIRED_LABEL,
      GitHubError,
      add_label,
      fetch_in_progress,
      remove_label,
  )
  ```

  Add `fetch_pull_for_branch` to it.

  **The three call sites**, as task 04 left them. Each builds an outcome; splice the PR
  fields into each with `**`:

  ```python
          attempts = store.requeue_count(lease.repo, lease.issue_number)
          spent = attempts >= MAX_REQUEUES
          # One lookup per release, shared by whichever branch is taken below.
          pr = _pr_fields(lease, github_token)
          if should_requeue(classified) and not spent:
              attempt = store.bump_requeue_count(lease.repo, lease.issue_number)
              _requeue(
                  store, leases, lease, github_token,
                  outcome=_outcome_from_run(classified, requeue_attempt=attempt, **pr),
              )
              # ... actions.append / log.info unchanged ...
          elif should_requeue(classified):
              store.forget_issue(lease.repo, lease.issue_number)
              leases.archive_and_release(
                  lease.run_id,
                  _outcome_from_run(classified, requeue_attempt=attempts, **pr),
              )
              # ... unchanged ...
          else:
              store.forget_issue(lease.repo, lease.issue_number)
              leases.archive_and_release(
                  lease.run_id,
                  _outcome_from_run(classified, requeue_attempt=attempts, **pr),
              )
              store.clear_requeue_count(lease.repo, lease.issue_number)
              # ... unchanged ...
  ```

  `_outcome_from_run` therefore grows the PR keywords. Its signature becomes:

  ```python
  def _outcome_from_run(
      run: Mapping[str, object],
      *,
      requeue_attempt: int = 0,
      pr_lookup: str = "none",
      pr_number: int | None = None,
      pr_url: str | None = None,
      merged: bool | None = None,
  ) -> RunOutcome:
  ```

  and passes them straight through to `RunOutcome`. Every existing call and test that
  omits them keeps working, because the defaults match `RunOutcome`'s own.

  The function from task 07:

  ```python
  def fetch_pull_for_branch(
      repo: str, branch: str, token: str, *,
      client: httpx.Client | None = None, timeout: float = PR_LOOKUP_TIMEOUT_SECONDS,
  ) -> PullRequest | None: ...


  @dataclass(frozen=True)
  class PullRequest:
      number: int
      url: str
      merged: bool
  ```

- Verified external contracts: as task 07, verified live on 2026-09-20. A merged PR is
  `state: "closed"` with a non-null `merged_at`; with `state=all` the head filter returns
  PR 388 and without it, zero results.

- Behavior rules:
  - **One lookup per release**, taken before the branch so all three share it. Not one per
    branch, and not one inside `_requeue`.
  - The **404 path does not call `_pr_fields`**. Task 05's lost-run outcome keeps its
    default `pr_lookup="none"`. Do not add a lookup there.
  - `_pr_fields` catches `GitHubError` only. A `GitHubError` is what task 07 raises for
    every transport, auth and status failure, so a bare `except Exception` would only hide
    a programming error.
  - On failure, `pr_number`, `pr_url` and `merged` are all left unset, so they land `NULL`.
  - The log line on failure is `log.error`, matching `_requeue`'s best-effort label writes.
  - Never log the token.

- Error and security rules: the release must complete whatever GitHub does. If
  `_pr_fields` can ever raise, the lease is held forever and a coder instance is lost until
  someone breaks the lease by hand.

## Acceptance Criteria
- [ ] A terminal run whose branch has a merged PR records `pr_lookup="found"`,
      `pr_number`, `pr_url` and `merged=1`.
- [ ] A terminal run whose branch has an open PR records `merged=0`.
- [ ] A terminal run whose branch has no PR records `pr_lookup="none"` and `merged IS NULL`.
- [ ] A terminal run released while the PR endpoint returns `500` records
      `pr_lookup="failed"`, `merged IS NULL`, **and still releases the lease**.
- [ ] The lookup is called with `head={owner}:fabro/run/{run_id}` and `state=all`.
- [ ] Exactly one PR request is made per release, not three.
- [ ] The 404 lost-run path makes **no** PR request and still records `pr_lookup="none"`.
- [ ] Every pre-existing `test_reconcile.py` case passes unchanged.

## Test Expectations
Framework: **pytest 8** with **respx**. Command:
`cd ops/scheduler && uv run pytest tests/test_reconcile.py`.
Extend `ops/scheduler/tests/test_reconcile.py`, reusing `_history` from task 04.

```python
PULLS = re.compile(rf"{re.escape(GITHUB_API)}/repos/[^/]+/[^/]+/pulls$")

MERGED_PR = [{
    "number": 388,
    "html_url": "https://github.com/andrewthetechie/jelly-swipe/pull/388",
    "merged_at": "2026-09-20T18:03:22Z",
}]


@respx.mock
def test_a_merged_pr_is_recorded(config, store, leases, fabro):
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json={
        "id": "R1", "lifecycle": {"status": {"kind": "succeeded"}},
    }))
    pulls = respx.get(url__regex=PULLS).mock(
        return_value=httpx.Response(200, json=MERGED_PR)
    )

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["pr_lookup"] == "found"
    assert row["pr_number"] == 388
    assert row["pr_url"] == "https://github.com/andrewthetechie/jelly-swipe/pull/388"
    assert row["merged"] == 1
    assert pulls.call_count == 1
    params = pulls.calls.last.request.url.params
    assert params["head"] == "andrewthetechie:fabro/run/R1"
    assert params["state"] == "all"


@respx.mock
def test_no_pr_is_recorded_as_none(config, store, leases, fabro):
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json={
        "id": "R1", "lifecycle": {"status": {"kind": "failed", "reason": "stage_failed"}},
    }))
    respx.get(url__regex=EVENTS).mock(return_value=httpx.Response(200, json={"data": []}))
    respx.get(url__regex=PULLS).mock(return_value=httpx.Response(200, json=[]))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["pr_lookup"] == "none"
    assert row["pr_number"] is None
    assert row["merged"] is None


@respx.mock
def test_a_github_failure_records_unknown_and_still_releases(config, store, leases, fabro):
    leases.acquire(_lease())
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(200, json={
        "id": "R1", "lifecycle": {"status": {"kind": "succeeded"}},
    }))
    respx.get(url__regex=PULLS).mock(return_value=httpx.Response(500, json={}))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    (row,) = _history(store)
    assert row["pr_lookup"] == "failed"
    assert row["merged"] is None
    assert leases.active() == []          # the box is free; this is the point


@respx.mock
def test_the_lost_run_path_makes_no_pr_request(config, store, leases, fabro):
    leases.acquire(_lease())
    _install_labels()
    respx.get(url__regex=RUNS).mock(return_value=httpx.Response(404, json={}))
    pulls = respx.get(url__regex=PULLS).mock(return_value=httpx.Response(200, json=[]))

    reconcile_leases(config, store, leases, fabro, GH_TOKEN)

    assert pulls.call_count == 0
    (row,) = _history(store)
    assert row["kind"] == "lost"
    assert row["pr_lookup"] == "none"
```

`GITHUB_API` and `EVENTS` are already module constants in this file
(`tests/test_reconcile.py:38, 45`). `_lease()`'s `run_id` defaults to `"R1"`, which is
where the expected `fabro/run/R1` branch comes from.

## Dependencies
- Blocked by: `Write a row on every terminal release`, `Add fetch_pull_for_branch to github.py`
- Why blocked: the first supplies the three call sites and `_outcome_from_run`; the second
  supplies the lookup this wraps.
- Blocks: `Deploy and verify on the host`

## Labels
`feature`, `scheduler`, `priority:high`

## Estimate
Medium

## Risk
3 - Adds a network call to the release path. The failure mode if `_pr_fields` can raise is
a permanently held coder lease, which is the worst outcome in this deployment — hence the
explicit "still releases" acceptance criterion.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full, including every pre-existing
`test_reconcile.py` case.
