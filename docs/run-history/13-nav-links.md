# Add nav links between the queue and the history

## Tracer-Bullet Outcome
The queue page carries a link to `/history`, and the history page carries one back to the
queue. An operator who lands on either can reach the other without knowing the URL.

## User Story
As the operator, I want to move between "what is happening now" and "what has happened"
without typing a path, so that the history page is something I actually find rather than
something I have to remember exists.

## Description
Add a two-item nav to both templates and the CSS that makes it read as navigation. This is
the task that makes the history page discoverable — until it lands, `/history` is
reachable only by someone who read the ADR.

The two pages are deliberately not identical. The queue page is live and meta-refreshes;
the history page does not. A shared nav partial keeps the markup in one place without
implying the pages share anything else.

## Context Pack
- Source decisions: ADR 0008 — the history page is "a second route, `GET /history`,
  reached from a nav link that `queue.html` does not have yet and will need".
- Repo facts: `queue.html` opens its body with `<h1>Queue</h1>` followed by a
  `<p class="sub">` (`queue.html:66-81`). `history.html` opens with `<h1>Run history</h1>`
  and its own `.sub` (task 11). `_style.html` is the extracted stylesheet both include
  (task 11). Templates resolve from `TEMPLATES_DIR = Path(__file__).parent / "templates"`
  (`app.py:79`), so a new partial beside them is found by name.
- Non-goals: no third page, no breadcrumb, no active-state JavaScript, no change to either
  page's heading or copy.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/templates/_nav.html        (new)
  ops/scheduler/src/fabro_scheduler/templates/_style.html      (add the nav rules)
  ops/scheduler/src/fabro_scheduler/templates/queue.html       (include it)
  ops/scheduler/src/fabro_scheduler/templates/history.html     (include it)
  ops/scheduler/tests/test_app.py                              (extend)
  ```

- Interfaces and names:

  New `templates/_nav.html`. It takes one variable, `here`, which each page passes as a
  literal so the current page is marked rather than linked:

  ```jinja
  {# Two pages, and the current one is text rather than a link: a nav item that
     reloads the page you are on is a small lie about what clicking does. `here` is
     passed as a literal by each includer -- there is no route-introspection here
     because two pages do not need one. #}
  <nav>
    {% if here == 'queue' %}
      <strong>Queue</strong>
    {% else %}
      <a href="/">Queue</a>
    {% endif %}
    <span class="sep">&middot;</span>
    {% if here == 'history' %}
      <strong>Run history</strong>
    {% else %}
      <a href="/history">Run history</a>
    {% endif %}
  </nav>
  ```

  In `queue.html`, immediately after `<body>` and **before** `<h1>Queue</h1>`
  (`queue.html:65-66`):

  ```jinja
  {% include "_nav.html" with context %}
  ```

  and set the variable just above it:

  ```jinja
  {% set here = 'queue' %}
  {% include "_nav.html" with context %}
  ```

  In `history.html`, the same two lines with `{% set here = 'history' %}`.

  `with context` is required: `{% set %}` in the including template is only visible to the
  include when the context is passed. Without it the nav renders both items as links,
  which is wrong but not obviously so — hence the acceptance criterion below.

  Add to `_style.html`:

  ```css
  nav { margin: 0 0 1rem; color: var(--dim); font-size: .9em; }
  nav a { color: var(--accent); text-decoration: none; }
  nav a:hover { text-decoration: underline; }
  nav strong { color: var(--fg); font-weight: 600; }
  nav .sep { margin: 0 .5rem; }
  ```

- Verified external contracts: None.

- Behavior rules:
  - On `/`, "Queue" is bold text and "Run history" is a link to `/history`.
  - On `/history`, the inverse.
  - Both pages render the nav **before** their `<h1>`.
  - The include uses `with context`, or `here` is undefined inside the partial and both
    items render as links.
  - The history nav link is a plain `/history` with no query string, so it always lands on
    the default sort.
  - Still no `<script>` on either page.

- Error and security rules: None. Two internal links.

## Acceptance Criteria
- [ ] `GET /` contains `href="/history"`.
- [ ] `GET /history` contains `href="/"`.
- [ ] `GET /` does **not** contain `href="/"` inside its nav — the current page is
      `<strong>`, not a link.
- [ ] `GET /history` does **not** contain `href="/history"` inside its nav.
- [ ] Both pages still return `200`, and the queue page still carries its meta-refresh.
- [ ] Neither page contains `<script`.

## Test Expectations
Framework: **pytest 8** with FastAPI's `TestClient`. Command:
`cd ops/scheduler && uv run pytest tests/test_app.py`.
Extend `ops/scheduler/tests/test_app.py`, reusing the `client` fixture.

```python
import re


def _nav(body: str) -> str:
    """Just the <nav> block, so an assertion cannot match a link elsewhere on the page."""
    found = re.search(r"<nav>(.*?)</nav>", body, re.DOTALL)
    assert found, "the page has no <nav> block"
    return found.group(1)


def test_the_queue_page_links_to_the_history(client):
    nav = _nav(client.get("/").text)
    assert 'href="/history"' in nav
    assert 'href="/"' not in nav          # the current page is not a link
    assert "<strong>Queue</strong>" in nav


def test_the_history_page_links_back_to_the_queue(client):
    nav = _nav(client.get("/history").text)
    assert 'href="/"' in nav
    assert 'href="/history"' not in nav
    assert "<strong>Run history</strong>" in nav


def test_both_pages_still_render(client):
    assert client.get("/").status_code == 200
    assert client.get("/history").status_code == 200
    assert "http-equiv=\"refresh\"" in client.get("/").text
    assert "http-equiv=\"refresh\"" not in client.get("/history").text
```

The `_nav` helper matters: asserting `'href="/history"' in body` against the whole page
would also pass if the link were anywhere else, and asserting `'href="/"' not in body`
against the whole page would fail on unrelated markup.

## Dependencies
- Blocked by: `Add GET /history and the page`
- Why blocked: supplies `history.html` and `_style.html`, both of which this edits. There
  is nothing to link to before it.
- Blocks: `Deploy and verify on the host`

## Labels
`enhancement`, `scheduler`, `priority:medium`

## Estimate
Small

## Risk
1 - Two template includes and five CSS rules.

## Validator Stopping Point
`cd ops/scheduler && uv run pytest` passes in full.
