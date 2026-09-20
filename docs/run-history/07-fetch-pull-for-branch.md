# Add `fetch_pull_for_branch` to `github.py`

## Tracer-Bullet Outcome
Ask `github.py` for the pull request on a run branch and get back its number, URL and
whether it merged — or `None` when there is no PR, or a `GitHubError` when GitHub could
not be reached. Three distinguishable answers. Nothing calls it yet.

## User Story
As the operator, I want the scheduler able to tell whether a run's PR merged, so that the
history page can answer "did the factory actually ship anything" rather than only "did
the run exit cleanly".

## Description
Add one read function to `github.py`, beside the existing `fetch_issues` and
`fetch_in_progress`, plus a small frozen dataclass for its result and one timeout
constant.

**There is no `gh` in the scheduler image.** The runtime stage installs `ca-certificates`
and `git` and nothing else (`ops/scheduler/Dockerfile:49`), confirmed live with
`docker exec fabro-scheduler sh -c 'command -v gh'` → `NO_GH`. Do not shell out. The
GitHub half of this service is `httpx` end to end and this function follows it exactly.

The one detail that decides whether this feature works at all is `state=all`. Both
`gh pr list --head` and this endpoint default to `state=open`, and since `54c21be`
review-and-merge runs *inside* the backlog run — so a PR that auto-merged is already
closed before the run reaches a terminal state and the lease is released. Defaulting to
open returns nothing for exactly the runs the column exists to record. This is verified
below against the live API, not asserted.

## Context Pack
- Source decisions: ADR 0008 — the enrichment ships in v1, by REST, with `state=all`;
  `pr_lookup` is tri-state so "no PR" and "GitHub was unreachable" never read alike.
- Repo facts: `github.py`'s module docstring states its blast radius — "Nothing here
  closes an issue, comments, or touches anything else — the worst thing a bug in the read
  path can do is show the operator a stale queue." This function keeps that true: it is a
  read. `GitHubError` already carries `status_code` and `remaining`.
- Non-goals: no caller, no `reconcile.py` change, no ETag caching. This runs once per
  release — roughly a dozen times a day — not on a poll, so none of `fetch_issues`'s
  conditional-request machinery applies.

## Delivery Strategy
- Shape: Prefactor
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/github.py     (add PullRequest, fetch_pull_for_branch)
  ops/scheduler/tests/test_github.py              (extend)
  ```

- Interfaces and names:

  ```python
  # The PR lookup's own timeout, deliberately shorter than this module's 15s
  # default. It runs inside the 15-second release poll
  # (`DEFAULT_RELEASE_INTERVAL_SECONDS`), and a call that can consume a whole tick
  # delays the release of the OTHER coder instance's lease -- the scarcest resource
  # the scheduler has. Five seconds is long enough for a healthy api.github.com and
  # short enough that a sick one costs a third of one tick.
  PR_LOOKUP_TIMEOUT_SECONDS = 5.0


  @dataclass(frozen=True)
  class PullRequest:
      """The PR on a run branch, as the history record needs it.

      `merged` is a point-in-time snapshot: it is whether the PR had merged when
      this was read, not a promise about what it becomes later.
      """

      number: int
      url: str
      merged: bool


  def fetch_pull_for_branch(
      repo: str,
      branch: str,
      token: str,
      *,
      client: httpx.Client | None = None,
      timeout: float = PR_LOOKUP_TIMEOUT_SECONDS,
  ) -> PullRequest | None:
      """The pull request whose head is `branch`, or `None` if there is none.

      `repo` is `"owner/repo"`; `branch` is a bare ref such as
      `"fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR"`. The `head` filter wants the
      `owner:ref` form, so the owner is taken from `repo` and prefixed here rather
      than by the caller.

      **`state=all` is load-bearing.** The endpoint defaults to `state=open`, and
      a backlog run's PR has usually already merged by the time its lease is
      released -- so the default returns nothing for exactly the runs worth
      recording. Verified live on 2026-09-20: with `state=all` this filter returns
      PR 388; without it, zero results.

      Raises `GitHubError` for anything that is not a `200`, so the caller can tell
      "there is no PR" (`None`) from "GitHub could not answer" (the raise). The two
      are opposite conclusions for an operator and must never collapse into one.
      """
  ```

  **The function to copy**, verbatim from `github.py:227-295` — same header block, same
  `owned`/`http` lifecycle, same error construction. Only the URL, the params and the
  payload parsing differ:

  ```python
      if not token or not token.strip():
          raise GitHubError(
              "GITHUB_TOKEN is not set; the GitHub inventory cannot be refreshed"
          )

      headers = {
          "Authorization": f"Bearer {token}",
          "Accept": "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
      }
      params = {"labels": IN_PROGRESS_LABEL, "state": "open", "per_page": PAGE_SIZE}

      owned = client is None
      http = client or httpx.Client(timeout=timeout)
      try:
          response = http.get(
              f"{GITHUB_API}/repos/{repo}/issues", params=params, headers=headers
          )
      except httpx.HTTPError as exc:
          raise GitHubError(f"{repo}: GitHub request failed: {exc}") from exc
      finally:
          if owned:
              http.close()

      if response.status_code != 200:
          remaining = response.headers.get("x-ratelimit-remaining")
          raise GitHubError(
              _error_message(repo, response, remaining),
              status_code=response.status_code,
              remaining=remaining,
          )
  ```

  For this function the request and the parsing are:

  ```python
      owner = repo.split("/", 1)[0]
      params = {"head": f"{owner}:{branch}", "state": "all", "per_page": 1}
      # ... same try/except/finally, but against:
      response = http.get(f"{GITHUB_API}/repos/{repo}/pulls", params=params, headers=headers)
      # ... same non-200 raise ...

      payload = response.json()
      if not isinstance(payload, list):
          raise GitHubError(
              f"{repo}: expected a list of pulls, got {type(payload).__name__}"
          )
      if not payload:
          return None
      item = payload[0]
      if not isinstance(item, Mapping) or not isinstance(item.get("number"), int):
          return None
      return PullRequest(
          number=item["number"],
          url=str(item.get("html_url") or ""),
          merged=item.get("merged_at") is not None,
      )
  ```

  Existing module constants this uses, already defined (`github.py:47-58`):

  ```python
  GITHUB_API = "https://api.github.com"
  REQUEST_TIMEOUT_SECONDS = 15.0
  PAGE_SIZE = 100
  ```

- Verified external contracts:

  **Verified live on 2026-09-20** against `andrewthetechie/jelly-swipe` with
  `gh api`. Two real backlog PRs, unfiltered:

  ```json
  {"number": 388, "html_url": "https://github.com/andrewthetechie/jelly-swipe/pull/388",
   "merged_at": "2026-09-20T18:03:22Z", "state": "closed",
   "head": {"ref": "fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR",
            "label": "andrewthetechie:fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR"}}
  {"number": 387, "html_url": "https://github.com/andrewthetechie/jelly-swipe/pull/387",
   "merged_at": "2026-09-20T15:55:05Z", "state": "closed",
   "head": {"ref": "fabro/run/01M2X5G600S0T1C5XMN0HFEB5G", ...}}
  ```

  **The `state=all` proof**, both run live on 2026-09-20:

  ```
  GET /repos/andrewthetechie/jelly-swipe/pulls
      ?head=andrewthetechie:fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR&state=all&per_page=1
      -> 1 result, number 388, merged_at 2026-09-20T18:03:22Z

  GET /repos/andrewthetechie/jelly-swipe/pulls
      ?head=andrewthetechie:fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR&per_page=1
      -> 0 results
  ```

  A merged PR is `state: "closed"` with a non-null `merged_at`. There is **no**
  `"merged"` boolean on the list payload — that field exists only on
  `GET /pulls/{number}`, a second call this function does not make. `merged_at is not
  None` is the discriminator.

  The head branch is `fabro/run/<run_id>`, on the remote because fabro's checkpoint
  publishes it after every stage; `open_pr`'s own `git push -u origin HEAD` is always
  `Everything up-to-date` (`.fabro/workflows/backlog/workflow.fabro:800-802`).

  Token scope: this adds `pull_requests: read`. The scheduler's `GITHUB_TOKEN` comes from
  `gh auth token`, which covers it.

- Behavior rules:
  - Empty array → `None`. This is "no PR", and it is a legitimate answer for a run that
    failed before `open_pr`.
  - Non-200 → raise `GitHubError`. Never return `None` for a transport or auth failure;
    the caller's whole job is to tell the two apart.
  - A network exception from `httpx` → `GitHubError`, chained with `from exc`.
  - `merged` is `merged_at is not None`. Do not read a `merged` key; the list payload has
    none.
  - `per_page=1`. One run branch has at most one PR, and the caller wants the first.
  - Never log the token. `GitHubError`'s docstring already records that "Nothing in here
    ever holds the token."

- Error and security rules: `GitHubError` carries `status_code` and
  `x-ratelimit-remaining` through the existing `_error_message(repo, response, remaining)`
  helper (`github.py:387`), because a `403` from a spent quota and a `403` from a token
  without `pull_requests: read` are told apart by nothing else.

## Acceptance Criteria
- [ ] A `200` with one PR returns `PullRequest(number=388, url=..., merged=True)` when
      `merged_at` is a timestamp.
- [ ] A `200` with one PR whose `merged_at` is `null` returns `merged=False`.
- [ ] A `200` with `[]` returns `None`.
- [ ] A `404`, `403` or `500` raises `GitHubError` carrying that `status_code`.
- [ ] An `httpx` transport error raises `GitHubError`.
- [ ] An empty or whitespace token raises `GitHubError` before any request is made.
- [ ] The request URL is `/repos/{repo}/pulls` with `head={owner}:{branch}`,
      `state=all` and `per_page=1`.
- [ ] `PR_LOOKUP_TIMEOUT_SECONDS == 5.0` and is the function's default `timeout`.

## Test Expectations
Framework: **pytest 8** with **respx**. Command:
`cd ops/scheduler && uv run pytest tests/test_github.py`.
Extend `ops/scheduler/tests/test_github.py`.

```python
import httpx
import pytest
import respx

from fabro_scheduler.github import (
    PR_LOOKUP_TIMEOUT_SECONDS,
    GitHubError,
    PullRequest,
    fetch_pull_for_branch,
)

FF = "andrewthetechie/jelly-swipe"
BRANCH = "fabro/run/01M2ZQG1AGP23KERJF6FKHHMCR"
PULLS = "https://api.github.com/repos/andrewthetechie/jelly-swipe/pulls"
MERGED = [{
    "number": 388,
    "html_url": "https://github.com/andrewthetechie/jelly-swipe/pull/388",
    "merged_at": "2026-09-20T18:03:22Z",
    "state": "closed",
}]


@respx.mock
def test_a_merged_pr_is_found_and_reported_merged():
    route = respx.get(PULLS).mock(return_value=httpx.Response(200, json=MERGED))

    found = fetch_pull_for_branch(FF, BRANCH, "ghp_test")

    assert found == PullRequest(
        number=388,
        url="https://github.com/andrewthetechie/jelly-swipe/pull/388",
        merged=True,
    )
    request = route.calls.last.request
    assert request.url.params["head"] == f"andrewthetechie:{BRANCH}"
    assert request.url.params["state"] == "all"      # the regression this guards
    assert request.url.params["per_page"] == "1"


@respx.mock
def test_an_open_pr_is_found_and_reported_unmerged():
    respx.get(PULLS).mock(return_value=httpx.Response(200, json=[
        {"number": 389, "html_url": "https://example.invalid/389", "merged_at": None}
    ]))
    found = fetch_pull_for_branch(FF, BRANCH, "ghp_test")
    assert found is not None and found.merged is False


@respx.mock
def test_no_pr_returns_none():
    respx.get(PULLS).mock(return_value=httpx.Response(200, json=[]))
    assert fetch_pull_for_branch(FF, BRANCH, "ghp_test") is None


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 500])
def test_a_non_200_raises_with_its_status(status):
    respx.get(PULLS).mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(GitHubError) as caught:
        fetch_pull_for_branch(FF, BRANCH, "ghp_test")
    assert caught.value.status_code == status


@respx.mock
def test_a_transport_error_raises():
    respx.get(PULLS).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(GitHubError):
        fetch_pull_for_branch(FF, BRANCH, "ghp_test")


def test_an_empty_token_raises_before_any_request():
    with pytest.raises(GitHubError):
        fetch_pull_for_branch(FF, BRANCH, "   ")


def test_the_lookup_timeout_is_five_seconds():
    import inspect

    assert PR_LOOKUP_TIMEOUT_SECONDS == 5.0
    assert (
        inspect.signature(fetch_pull_for_branch).parameters["timeout"].default
        == PR_LOOKUP_TIMEOUT_SECONDS
    )
```

## Dependencies
- Blocked by: None
- Why blocked: N/A — this touches only `github.py` and depends on nothing else in the
  series.
- Blocks: `Resolve the PR at release`

## Labels
`feature`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
2 - A new read function with no caller. The only way it can hurt production is if someone
wires it in wrong, which is the next task.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full. The repository is valid: the new
function has no callers.
