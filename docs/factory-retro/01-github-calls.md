# Task 01: two GitHub calls

Read `00-overview-and-contracts.md` first (contract C3).

## Goal

The retro needs to read every `retro`-labelled issue in this repository, open and closed,
and to create issues. `github.py` has neither call.

## Steps

1. In `ops/scheduler/src/fabro_scheduler/github.py`, add the `TrackedIssue` dataclass from
   C3 next to `Issue` and `PullRequest`.
2. Add `fetch_label_all_states(repo, label, token, *, client=None, timeout=REQUEST_TIMEOUT_SECONDS)`.
   Model it on `fetch_labelled`: the same headers, the same empty-token check, the same
   `GitHubError` shape on a non-200 (`_error_message`). Differences:
   - `params = {"labels": label, "state": "all", "per_page": PAGE_SIZE}`;
   - follow every page: after each response, `url = _next_link_url(response)`, stop when it
     is `None`. Pass `params` on the first request only, because the next URL already
     carries them;
   - drop items with a `pull_request` key, items without an int `number`, and items that do
     not carry `label`;
   - `state_reason`, `closed_at`, `html_url` and `body` may be missing or `null`: store
     `None` (and `""` for `body`).
3. Add `create_issue(repo, title, body, labels, token, *, client=None, timeout=REQUEST_TIMEOUT_SECONDS) -> TrackedIssue`.
   Use `_write("POST", f"/repos/{repo}/issues", token, json={"title": ..., "body": ..., "labels": list(labels)}, ...)`.
   A non-2xx raises `_write_error(path, "create issue", response)`. Build the returned
   `TrackedIssue` from the response JSON. Log `github: <repo>#<n> issue created` at info.
   Never log the token or the body.
4. Update the module docstring's list of GitHub writes, and `ops/README.md`'s
   `scheduler/src/fabro_scheduler/github.py` row: it now also creates issues in this
   repository, for the retro only.

## Tests (`ops/scheduler/tests/test_github.py`, `respx`)

- Two pages: the first answers with a `link: <...page=2>; rel="next"` header; both pages'
  issues are returned, and a pull request on page 2 is dropped.
- A closed issue with `state_reason: "not_planned"` and a `null` body parses to
  `state_reason="not_planned"`, `body=""`.
- A `403` raises `GitHubError` with `status_code == 403`.
- `create_issue` sends the title, body and both labels, and returns the number from the
  response. A `422` raises `GitHubError`.
- An empty token raises `GitHubError` before any request (`respx` records no call).

## Done when

`uv run pytest` in `ops/scheduler/` and `make check` pass. One commit:
`feat(scheduler): read issues in every state and create issues (factory-retro 01, #33)`.
