# Add sortable header links to `/history`

## Tracer-Bullet Outcome
Click a column header on `/history` and the table re-sorts by that column. Click it again
and the direction flips. The URL carries the sort, so it survives a reload, a bookmark,
and a paste into the deployment log — and there is still no JavaScript on the page.

## User Story
As the operator, I want to sort the history by how long a run took or by which box ran it,
so that "what is slow" and "is one box worse than the other" are one click rather than a
SQL prompt.

## Description
Turn eight of the ten column headers into links that carry `?sort=` and `?dir=`, and mark
the active one. Everything server-side; the sort itself is already implemented.

The important property is stated in ADR 0008 and is easy to lose: **the sort is applied in
SQL before the limit**. `Store.history_rows` already does that in one statement. Do not
re-sort `views` in the route or the template — that would silently answer "the longest of
the most recent 200" while looking correct.

Two columns are deliberately not sortable. `Run` is a ULID with no useful order beyond
`finished_at`, and `PR` is three different facts in one cell (`pr_lookup`, `pr_number`,
`merged`) with no single sensible ordering — sorting by `merged` is the useful half and it
already has its own header.

## Context Pack
- Source decisions: ADR 0008 decision 5 and decision 8 — server-side sort, header links,
  sort in SQL before the limit.
- Repo facts: `HISTORY_SORT_COLUMNS` is the whitelist in `store.py` and already contains
  exactly the eight sortable columns. `history_page` already receives `sort`, `dir` and
  `limit` and already passes `sort` and `descending` into the template context (task 11).
- Non-goals: no new sortable column, no multi-column sort, no JavaScript, no change to
  `Store.history_rows`.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/app.py                    (_sort_link)
  ops/scheduler/src/fabro_scheduler/templates/history.html    (the <thead>)
  ops/scheduler/tests/test_app.py                             (extend)
  ```

- Interfaces and names:

  Add to `app.py` at module level:

  ```python
  def _sort_link(column: str, *, active: str, descending: bool, limit: int | None) -> str:
      """The href for one sortable header.

      Clicking the column that is already active flips the direction; clicking any
      other column starts it descending, which is the useful default for every one
      of them -- newest, longest, most requeues first.

      `?limit=all` -- `limit=None` here -- is the one value carried through, because
      an operator who asked for every row still means it after a re-sort. A numeric
      limit is deliberately dropped: the default is the default, and a link that
      pinned a page size would make `?limit=5` sticky with nothing on the page to
      undo it.
      """
      flip = not descending if column == active else True
      query = f"sort={column}&dir={'desc' if flip else 'asc'}"
      if limit is None:
          query += "&limit=all"
      return f"/history?{query}"
  ```

  Register it as a Jinja global inside `build_app`, immediately after the existing filter
  registration (`app.py:188-189`):

  ```python
      templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
      templates.env.filters["duration"] = humanise_duration
      # A global rather than a filter: it takes the page's whole sort state, not a
      # value being formatted.
      templates.env.globals["sort_link"] = _sort_link
  ```

  **Replace `history.html`'s `<thead>`** — task 11 left it as plain `<th>` text:

  ```jinja
    <thead>
      <tr>
        {% for column, heading, numeric in [
            ('coder_pool',      'Coder instance', false),
            ('repo',            'Repo',           false),
            ('issue_number',    'Issue',          true),
            (none,              'Run',            false),
            ('dispatched_at',   'Dispatched',     true),
            ('finished_at',     'Finished',       true),
            (none,              'Took',           true),
            ('kind',            'Ending',         false),
            ('requeue_attempt', 'Requeues',       true),
            ('merged',          'PR',             false),
        ] %}
        <th{% if numeric %} class="num"{% endif %}>
          {% if column %}
            <a href="{{ sort_link(column, active=sort, descending=descending, limit=limit) }}">
              {{ heading }}{% if column == sort %}{{ ' ▾' if descending else ' ▴' }}{% endif %}
            </a>
          {% else %}
            {{ heading }}
          {% endif %}
        </th>
        {% endfor %}
      </tr>
    </thead>
  ```

  The two `none` entries are Jinja's `none` literal (lowercase) and render as plain,
  unlinked headings. `▾` is `▾` and `▴` is `▴`; write the characters directly in
  the template rather than the escapes.

  Add to `_style.html` (the file task 11 extracted), so the links read as headers rather
  than as body links:

  ```css
  th a { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--line); }
  th a:hover { color: var(--accent); border-bottom-color: var(--accent); }
  ```

  The context `history_page` already provides (task 11):

  ```python
  {"views": [...], "sort": column, "descending": descending, "limit": capped}
  ```

  The whitelist these eight columns must match, from task 09:

  ```python
  HISTORY_SORT_COLUMNS = frozenset({
      "finished_at", "dispatched_at", "repo", "issue_number",
      "coder_pool", "kind", "merged", "requeue_attempt",
  })
  ```

- Verified external contracts: None.

- Behavior rules:
  - Every linked column must be in `HISTORY_SORT_COLUMNS`. A header linking to a column
    outside it silently renders the default order, which looks like a broken link.
  - Clicking the **active** column flips the direction. Clicking any **other** column
    starts descending.
  - `?limit=all` is carried through every header link; a numeric limit is not, because the
    default is the default.
  - The active column shows a direction marker; the others show none.
  - Do not sort `views` anywhere in Python or Jinja. The order arrives correct from SQL.
  - Still no `<script>` on the page.

- Error and security rules: the `href` is built from a hardcoded column name in the
  template loop, never from user input, so nothing reflects a query parameter back into
  the page.

## Acceptance Criteria
- [ ] `GET /history` renders a link for each of the eight sortable columns and plain text
      for `Run` and `Took`.
- [ ] The default page marks `Finished` as active and descending.
- [ ] `GET /history?sort=repo` renders rows ordered by repo, and marks `Repo` active.
- [ ] On `?sort=repo&dir=desc`, the `Repo` header's own link points at `dir=asc`.
- [ ] On `?sort=repo&dir=desc`, the `Issue` header's link points at `sort=issue_number&dir=desc`.
- [ ] `?limit=all` is preserved in every header link; a numeric `?limit=5` is not.
- [ ] Every linked column name is a member of `HISTORY_SORT_COLUMNS`.
- [ ] The sort is applied before the limit: with three rows and `?limit=2&sort=issue_number&dir=asc`,
      the two lowest issue numbers are shown — not the two most recently finished.
- [ ] The page still contains no `<script`.

## Test Expectations
Framework: **pytest 8** with FastAPI's `TestClient`. Command:
`cd ops/scheduler && uv run pytest tests/test_app.py`.
Extend `ops/scheduler/tests/test_app.py`, reusing `client`, `store` and `_archive`.

```python
import re

from fabro_scheduler.store import HISTORY_SORT_COLUMNS


def test_every_linked_column_is_sortable(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    body = client.get("/history").text

    # Jinja autoescapes `&` in an attribute value, so the rendered href reads
    # `/history?sort=repo&amp;dir=desc`. Match the entity form, not the raw `&`.
    linked = set(re.findall(r"/history\?sort=([a-z_]+)&amp;", body))
    assert linked == HISTORY_SORT_COLUMNS


def test_the_active_column_link_flips_direction(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")

    body = client.get("/history?sort=repo&dir=desc").text

    assert "/history?sort=repo&amp;dir=asc" in body          # the active one flips
    assert "/history?sort=issue_number&amp;dir=desc" in body  # the others start descending


def test_limit_all_is_carried_through_the_links(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")

    everything = client.get("/history?limit=all").text
    assert "&amp;limit=all" in everything

    paged = client.get("/history?limit=5").text
    assert "&amp;limit=" not in paged


def test_the_sort_is_applied_before_the_limit(client, store):
    # C finished most recently but has the highest issue number. Sorting by issue
    # number ascending with limit=2 must show 9 and 10 -- not the two newest.
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00", number=9)
    _archive(store, "B", finished="2026-09-20T11:00:00+00:00", number=10)
    _archive(store, "C", finished="2026-09-20T12:00:00+00:00", number=350)

    body = client.get("/history?sort=issue_number&dir=asc&limit=2").text

    assert "<code>A</code>" in body
    assert "<code>B</code>" in body
    assert "<code>C</code>" not in body


def test_the_page_still_has_no_script(client, store):
    _archive(store, "A", finished="2026-09-20T10:00:00+00:00")
    assert "<script" not in client.get("/history?sort=kind").text
```

The first test is the one that matters most: it fails the moment a header links to a
column the whitelist does not carry, which is the failure that silently renders the
default order.

## Dependencies
- Blocked by: `Add GET /history and the page`
- Why blocked: supplies the template, the route and the `sort`/`descending`/`limit`
  context these headers read.
- Blocks: `Deploy and verify on the host`

## Labels
`enhancement`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
1 - Template and one pure helper. No data path changes.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full.
