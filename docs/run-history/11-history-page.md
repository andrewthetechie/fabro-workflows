# Add `GET /history` and the page

## Tracer-Bullet Outcome
Open `http://<host>:32280/history` in a browser and see every released run in a table —
coder instance, repo, issue, run, dispatched, finished, how long it took, how it ended and
its PR — newest first. No JavaScript, and no meta-refresh.

## User Story
As the operator, I want to see what the factory has been doing without an ssh session, so
that "which box ran that, and how long did it take" is a page rather than a reconstruction
from fabro's store and the deployment log.

## Description
Add one route, one template, and one small shared-CSS extraction so the new page does not
duplicate sixty lines of the queue page's stylesheet.

Two deliberate differences from `queue.html`, both from ADR 0008 decision 5.

**No meta-refresh.** `queue.html:8` carries `<meta http-equiv="refresh" content="{{
poll_seconds }}">` because its data is polled on that beat. History has no live data — a
released run never changes — so the refresh would be pure loss, and once task 12 adds
sortable headers it would also discard the operator's chosen sort every 60 seconds.

**No JavaScript.** The queue page's established contract is that it works with scripting
off — every control is a real form POST with a `<noscript>` fallback
(`queue.html:254-313`). History has no controls at all, so it needs no script whatsoever.

## Context Pack
- Source decisions: ADR 0008 decision 5 — server-side rendering, no meta-refresh on
  `/history`, no JavaScript.
- Repo facts: templates are Jinja2 via
  `templates = Jinja2Templates(directory=str(TEMPLATES_DIR))` with
  `TEMPLATES_DIR = Path(__file__).parent / "templates"` (`app.py:79, 188`). One filter is
  registered on the env: `templates.env.filters["duration"] = humanise_duration`
  (`app.py:189`), which renders a `timedelta` as `3h 12m` / `2m 05s` / `4s`. The existing
  page route is `queue_page` (`app.py:627-652`) and returns
  `templates.TemplateResponse(request, "queue.html", {...})`.
- Non-goals: sortable headers (task 12), nav links between the pages (task 13), derived
  final-state labels (task 14). This page prints the raw `kind`/`reason` for now.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/app.py                      (_history_view, history_page)
  ops/scheduler/src/fabro_scheduler/templates/_style.html       (new, extracted)
  ops/scheduler/src/fabro_scheduler/templates/queue.html        (use the include)
  ops/scheduler/src/fabro_scheduler/templates/history.html      (new)
  ops/scheduler/tests/test_app.py                               (extend)
  ```

- Interfaces and names:

  **Step 1 — extract the stylesheet.** Move the entire contents of `queue.html`'s
  `<style> ... </style>` block (currently `queue.html:10-63`) into a new
  `templates/_style.html`, **including** the `<style>` and `</style>` tags. Replace it in
  `queue.html` with:

  ```jinja
  {% include "_style.html" %}
  ```

  Nothing else in `queue.html` changes. The rendered output must be byte-identical apart
  from whitespace, which is what the existing `test_app.py` page tests will confirm.

  **Step 2 — the view helper.** Add to `app.py` at module level:

  ```python
  @dataclass(frozen=True)
  class HistoryView:
      """One `run_history` row, with the two things the template cannot compute.

      `row` is passed through whole rather than unpacked, because the template
      reads it by column name and a second field list here would be one more thing
      to keep in step with a schema that cannot change.
      """

      row: sqlite3.Row
      elapsed: timedelta | None      # finished_at - dispatched_at, when both parse


  def _history_view(row: sqlite3.Row) -> HistoryView:
      """Attach the run's elapsed time. Never raises on a malformed timestamp."""
      try:
          started = datetime.fromisoformat(row["dispatched_at"])
          ended = datetime.fromisoformat(row["finished_at"])
      except (TypeError, ValueError):
          return HistoryView(row=row, elapsed=None)
      return HistoryView(row=row, elapsed=ended - started)
  ```

  `app.py` already imports `from datetime import datetime, timedelta` and
  `from dataclasses import dataclass`; add `sqlite3` if task 10 has not.

  **Step 3 — the route.** Add inside `build_app`, beside `queue_page`:

  ```python
      @app.get("/history", response_class=HTMLResponse)
      def history_page(
          request: Request,
          sort: str | None = None,
          dir: str | None = None,
          limit: str | None = None,
      ) -> HTMLResponse:
          """Released runs, newest finished first.

          Deliberately carries no `poll_seconds`: unlike the queue page this one has
          no live data and no meta-refresh (ADR 0008). A released run never changes.
          """
          column, descending, capped = _history_query(sort, dir, limit)
          rows = store.history_rows(sort=column, descending=descending, limit=capped)
          return templates.TemplateResponse(
              request,
              "history.html",
              {
                  "views": [_history_view(row) for row in rows],
                  "sort": column,
                  "descending": descending,
                  "limit": capped,
              },
          )
  ```

  `_history_query` is task 10's shared parser:

  ```python
  def _history_query(
      sort: str | None, direction: str | None, limit: str | None
  ) -> tuple[str, bool, int | None]: ...
  ```

  **Step 4 — the template.** New `templates/history.html`. Note: **no**
  `<meta http-equiv="refresh">`, **no** `<script>`.

  ```jinja
  <!doctype html>
  <html lang="en">
  <head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {# No meta-refresh, unlike queue.html: a released run never changes, so a reload
     would discard the operator's sort for nothing. ADR 0008. #}
  <title>fabro coder scheduler &middot; run history</title>
  {% include "_style.html" %}
  </head>
  <body>

  <h1>Run history</h1>
  <p class="sub">
    One row per released coder lease, written when the run reached a terminal state.
    This is a point-in-time record: it says what each run's ending <em>was</em>, not
    what became of its PR afterwards. In-flight runs are not here &mdash; they are on
    the queue page.
    {% if limit %}Showing at most {{ limit }} rows.{% else %}Showing every row.{% endif %}
  </p>

  {% if not views %}
  <div class="banner">
    <p>No released runs yet. A row appears here when a scheduler-dispatched run
       reaches a terminal state and its coder instance is freed.</p>
  </div>
  {% else %}
  <table>
    <thead>
      <tr>
        <th>Coder instance</th>
        <th>Repo</th>
        <th class="num">Issue</th>
        <th>Run</th>
        <th class="num">Dispatched</th>
        <th class="num">Finished</th>
        <th class="num">Took</th>
        <th>Ending</th>
        <th class="num">Requeues</th>
        <th>PR</th>
      </tr>
    </thead>
    <tbody>
    {% for view in views %}
      {% set row = view.row %}
      <tr>
        <td>{{ row.coder_pool }}</td>
        <td>{{ row.repo }}</td>
        <td class="num">{{ row.issue_number }}</td>
        <td><code>{{ row.run_id }}</code></td>
        <td class="num">{{ row.dispatched_at }}</td>
        <td class="num">{{ row.finished_at }}</td>
        <td class="num">{% if view.elapsed %}{{ view.elapsed|duration }}{% else %}&mdash;{% endif %}</td>
        <td>{{ row.kind }}{% if row.reason %}/{{ row.reason }}{% endif %}</td>
        <td class="num">{{ row.requeue_attempt }}</td>
        <td>
          {% if row.pr_lookup == 'found' and row.pr_url %}
            <a href="{{ row.pr_url }}">#{{ row.pr_number }}</a>
            {% if row.merged %}<span class="muted">merged</span>{% endif %}
          {% elif row.pr_lookup == 'failed' %}
            <span class="muted" title="GitHub could not be reached at release">unknown</span>
          {% else %}
            <span class="muted">&mdash;</span>
          {% endif %}
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  <footer>
    Written by the scheduler when it releases a coder lease. Manual fires and
    hand-fired <code>pr-review</code> runs take no lease and are not recorded here.
  </footer>

  </body>
  </html>
  ```

  A `sqlite3.Row` supports `row["name"]` but **not** `row.name` in Python. Jinja's
  `row.coder_pool` works anyway because Jinja falls back from attribute access to
  `__getitem__`. If that fallback ever surprises you, `row['coder_pool']` is the explicit
  form and is equally valid in a template.

- Verified external contracts: None. No outbound call.

- Behavior rules:
  - `/history` returns `200` with a "no released runs yet" banner on an empty table, never
    a `404`.
  - The rendered HTML contains **no** `http-equiv="refresh"` and **no** `<script>`.
  - `pr_lookup == "failed"` renders the word `unknown`, not a blank and not a dash. The
    three states must be visually distinct — that is the column's entire purpose.
  - The PR link is `row.pr_url` verbatim. Do not construct a URL from repo and number.
  - `queue.html`'s rendered output is unchanged by the `_style.html` extraction.

- Error and security rules: no auth, consistent with decision 16 — "LAN-only, no auth".
  The page prints only scheduler-owned facts and public GitHub URLs: no token, no host
  path, no IP. `pr_url` comes from GitHub's `html_url` and is escaped by Jinja's
  autoescaping, which `Jinja2Templates` enables by default for `.html`.

## Acceptance Criteria
- [ ] `GET /history` returns `200` and `text/html` on an empty table, containing the
      "No released runs yet" text.
- [ ] With rows, the response body contains each run's `run_id`, `repo`, `issue_number`
      and `coder_pool`.
- [ ] Rows appear newest-`finished_at` first.
- [ ] The response body contains no `http-equiv="refresh"` and no `<script`.
- [ ] A row with `pr_lookup="found"` renders a link to its `pr_url`.
- [ ] A row with `pr_lookup="failed"` renders the word `unknown`.
- [ ] A row with `pr_lookup="none"` renders neither.
- [ ] `?limit=all` and `?dir=asc` are honoured by the page, not only by the JSON route.
- [ ] `GET /` still returns `200` and still contains its meta-refresh — the extraction did
      not change the queue page.

## Test Expectations
Framework: **pytest 8** with FastAPI's `TestClient`. Command:
`cd ops/scheduler && uv run pytest tests/test_app.py`.
Extend `ops/scheduler/tests/test_app.py`, reusing its `client` and `store` fixtures
(`tests/test_app.py:45-54`) and the `_archive` helper from task 10.

```python
def test_history_page_is_empty_and_friendly(client):
    response = client.get("/history")
    assert response.status_code == 200
    assert "No released runs yet" in response.text


def test_history_page_lists_released_runs_newest_first(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00", number=9)
    _archive(store, "B", finished="2026-09-20T12:00:00+00:00", number=350)

    body = client.get("/history").text

    assert body.index("B") < body.index("A")
    assert "andrewthetechie/jelly-swipe" in body
    assert "coders-a" in body
    assert "350" in body


def test_history_page_carries_no_refresh_and_no_script(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    body = client.get("/history").text
    assert "http-equiv=\"refresh\"" not in body
    assert "<script" not in body


def test_history_page_distinguishes_the_three_pr_states(client, store):
    _archive(store, "A", finished="2026-09-20T12:00:00+00:00",
             pr_lookup="found", pr_number=388,
             pr_url="https://github.com/andrewthetechie/jelly-swipe/pull/388",
             merged=True)
    _archive(store, "B", finished="2026-09-20T11:00:00+00:00", pr_lookup="failed")
    _archive(store, "C", finished="2026-09-20T10:00:00+00:00", pr_lookup="none")

    body = client.get("/history").text

    assert "https://github.com/andrewthetechie/jelly-swipe/pull/388" in body
    assert "#388" in body
    assert "unknown" in body


def test_the_queue_page_still_refreshes(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "http-equiv=\"refresh\"" in response.text
```

## Dependencies
- Blocked by: `Add Store.history_rows: sorted, limited reads`
- Why blocked: supplies the read method. `_history_query` comes from task 10, which is
  earlier in the sequence — if the tasks are taken out of order, lift `_history_query`
  from task 10's contract.
- Blocks: `Add sortable header links`, `Add nav links between the queue and the history`,
  `Add derived final-state labels`

## Labels
`feature`, `scheduler`, `priority:medium`

## Estimate
Medium

## Risk
2 - A new read-only page plus a mechanical CSS extraction. The extraction is the only part
that can affect existing behavior, and the existing queue-page tests cover it.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full, including every pre-existing
`test_app.py` queue-page case.
