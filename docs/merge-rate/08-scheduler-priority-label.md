# Scheduler: a `priority` label puts an issue next in the queue

## Tracer-Bullet Outcome
An open `agent` issue that also carries the GitHub label `priority` is dispatched before
every other queued issue except one the operator bumped in the web UI (an Override).
Among several `priority` issues, the one the scheduler saw first goes first. The operator
can add the label on GitHub to have an issue worked next, and task 09's remainder promoter
uses the same label.

## User Story
As the operator, I want to label an issue `priority` on GitHub and have the scheduler
work it next, so that I can steer the queue without opening the scheduler page, and so
that an automatically filed remainder issue is picked up next.

## Description
Two source edits and one test addition, all under `ops/scheduler/` in fabro-workflows:

1. `src/fabro_scheduler/github.py`: add a `PRIORITY_LABEL` constant.
2. `src/fabro_scheduler/queue.py`: add the tier to `rank()` and to the module docstring.
3. `tests/test_queue.py`: add five tests.

No database change, no template change (the queue page already shows every label), no
API change.

## Context Pack
- Source decisions: overview decision 13. ADR 0011 D6, "`priority` label".
- Glossary (`CONTEXT.md`): **Override** is the operator's web-UI "dispatch next" click,
  stored in the `overrides` table. **Repo priority** is the integer per repository in
  `repos.toml`. **Priority label** is this new label, and is distinct from both.
- Repo facts:
  - Label constants in `github.py` today, verbatim:
    ```python
    REQUIRED_LABEL = "agent"

    # `agent-stuck` is what `mark_stuck` leaves on an issue a human has to re-arm;
    # `agent-in-progress` is the scheduler's own durable handoff receipt. A queue item
    # is neither (CONTEXT.md, **Queue item**).
    IN_PROGRESS_LABEL = "agent-in-progress"
    STUCK_LABEL = "agent-stuck"
    EXCLUDED_LABELS = frozenset({IN_PROGRESS_LABEL, STUCK_LABEL})
    ```
  - `Issue` (in `github.py`) already carries the labels, and the store persists them:
    ```python
    @dataclass(frozen=True)
    class Issue:
        repo: str  # "owner/repo"
        number: int
        title: str
        labels: frozenset[str]
        first_seen: datetime
    ```
  - `QueueItem` (in `queue.py`):
    ```python
    @dataclass(frozen=True)
    class QueueItem:
        """One issue, with the two facts that decide where it sorts."""

        issue: Issue
        repo_priority: int
        override_rank: int | None  # the operator's "next" mark, or None
        waited: timedelta
    ```
  - `queue.py` imports `from .github import Issue`. `rank()` today, verbatim:
    ```python
def rank(items: Sequence[QueueItem], ceiling: timedelta) -> list[QueueItem]:
    """Return `items` in dispatch order. Does not mutate its argument."""
    overridden = sorted(
        (item for item in items if item.override_rank is not None),
        key=lambda item: (item.override_rank, item.issue.number),
    )
    starved = sorted(
        (
            item
            for item in items
            if item.override_rank is None and item.waited > ceiling
        ),
        key=lambda item: (item.issue.first_seen, item.issue.number),
    )
    waiting = sorted(
        (
            item
            for item in items
            if item.override_rank is None and item.waited <= ceiling
        ),
        key=lambda item: (item.repo_priority, item.issue.number),
    )
    return overridden + starved + waiting
    ```
  - `build_queue()` and the dispatcher both call `rank()`, so the new tier applies to the
    page, `GET /api/queue` and dispatch alike. There is exactly one definition of
    "next".
  - Test helpers already in `tests/test_queue.py`:
    ```python
    NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    CEILING = timedelta(hours=4)

    def issue(repo="o/a", number=1, *, labels=("agent",), first_seen=NOW) -> Issue: ...
    def item(repo="o/a", number=1, *, priority=0, waited=timedelta(0), first_seen=None, override=None) -> QueueItem: ...
    def repo(name, priority, enabled=True) -> RepoConfig: ...
    # fixture `store` -> a Store on tmp_path; store.upsert_issue(issue) caches one issue
    ```
- Non-goals: do not remove the label on dispatch (the issue leaves the queue anyway when
  `agent` becomes `agent-in-progress`). No UI marker. No change to the Override
  mechanism.

## Delivery Strategy
- Shape: Normal tracer bullet
- Valid-state scope: Default branch after this draft

## Implementation Contract
- Expected files:
  ```
  ops/scheduler/src/fabro_scheduler/github.py
  ops/scheduler/src/fabro_scheduler/queue.py
  ops/scheduler/tests/test_queue.py
  ```
- **`github.py`:** directly after the line `STUCK_LABEL = "agent-stuck"`, add:
  ```python

  # An issue the operator (or the remainder promoter) wants dispatched next. Ranks
  # after an Override and before every other tier (queue.py). It is NOT repo
  # priority, the `repos.toml` integer; and it does not make an issue a queue item
  # -- only REQUIRED_LABEL does that. ADR 0011.
  PRIORITY_LABEL = "priority"
  ```
- **`queue.py`:**
  - Change `from .github import Issue` to `from .github import PRIORITY_LABEL, Issue`.
  - In the module docstring, directly after the paragraph that starts `1. **Override**`
    and before `2. **Starvation ceiling**`, insert:
    ```
    1b. **`priority` label** — an item carrying the GitHub label `priority`, oldest
       `first_seen` first (ADR 0011). Unlike an Override it lives on the issue, so it is
       visible on GitHub, survives a scheduler rebuild, and can be set by the remainder
       promoter as well as by the operator. It is not repo priority.
    ```
  - Replace `rank()` with exactly:
    ```python
def rank(items: Sequence[QueueItem], ceiling: timedelta) -> list[QueueItem]:
    """Return `items` in dispatch order. Does not mutate its argument."""
    overridden = sorted(
        (item for item in items if item.override_rank is not None),
        key=lambda item: (item.override_rank, item.issue.number),
    )
    rest = [item for item in items if item.override_rank is None]
    prioritized = sorted(
        (item for item in rest if PRIORITY_LABEL in item.issue.labels),
        key=lambda item: (item.issue.first_seen, item.issue.number),
    )
    rest = [item for item in rest if PRIORITY_LABEL not in item.issue.labels]
    starved = sorted(
        (item for item in rest if item.waited > ceiling),
        key=lambda item: (item.issue.first_seen, item.issue.number),
    )
    waiting = sorted(
        (item for item in rest if item.waited <= ceiling),
        key=lambda item: (item.repo_priority, item.issue.number),
    )
    return overridden + prioritized + starved + waiting
    ```
- **`tests/test_queue.py`:** append at the end of the file:
  ```python
# --- ranking: the `priority` label tier (ADR 0011) ------------------------------------


def labelled(
    repo: str,
    number: int,
    labels: tuple[str, ...],
    *,
    priority: int = 0,
    waited: timedelta = timedelta(0),
    override: int | None = None,
) -> QueueItem:
    return QueueItem(
        issue=issue(repo, number, labels=labels, first_seen=NOW - waited),
        repo_priority=priority,
        override_rank=override,
        waited=waited,
    )


def test_the_priority_label_outranks_the_ceiling_and_repo_priority():
    flagged = labelled("o/b", 5, ("agent", "priority"), priority=7, waited=timedelta(minutes=1))
    starved = item("o/c", 3, priority=-99, waited=timedelta(hours=9))
    waiting = item("o/a", 1, priority=-99, waited=timedelta(minutes=1))
    assert [i.issue.number for i in rank([starved, waiting, flagged], CEILING)] == [5, 3, 1]


def test_an_override_outranks_the_priority_label():
    flagged = labelled("o/a", 1, ("agent", "priority"), waited=timedelta(hours=1))
    bumped = item("o/b", 2, override=-1)
    assert [i.issue.number for i in rank([flagged, bumped], CEILING)] == [2, 1]


def test_two_priority_items_go_oldest_first_not_by_repo_priority_or_number():
    older = labelled("o/a", 9, ("agent", "priority"), priority=50, waited=timedelta(hours=2))
    newer = labelled("o/b", 1, ("agent", "priority"), priority=-99, waited=timedelta(hours=1))
    assert [i.issue.number for i in rank([newer, older], CEILING)] == [9, 1]


def test_an_overridden_priority_item_appears_once():
    both = labelled("o/a", 1, ("agent", "priority"), override=-1)
    assert [i.issue.number for i in rank([both], CEILING)] == [1]


def test_build_queue_reads_the_priority_label_from_the_cache(store):
    store.upsert_issue(issue("o/a", 1, first_seen=NOW - timedelta(minutes=5)))
    store.upsert_issue(issue("o/b", 2, labels=("agent", "priority"), first_seen=NOW))
    items = build_queue([repo("o/a", -99), repo("o/b", 50)], store, NOW, CEILING)
    assert [i.issue.number for i in items] == [2, 1]
  ```
- Behavior rules:
  - Tier order: Override, then `priority` label, then starvation, then repo priority.
  - An item with both an Override and the label appears once, in the Override tier.
  - The `priority` tier sorts by `(first_seen, number)`.
  - The label is matched exactly (`"priority"`, case-sensitive).
- Error and security rules: None.

## Acceptance Criteria
- [ ] `PRIORITY_LABEL = "priority"` exists in `github.py`.
- [ ] `rank()` returns `overridden + prioritized + starved + waiting`.
- [ ] `cd ops/scheduler && uv run pytest -q` passes with 5 more tests than before (389
      before this series).

## Test Expectations
- Framework: pytest (the scheduler's existing suite). Command:
  `cd ops/scheduler && uv run pytest -q tests/test_queue.py`, then the full
  `uv run pytest -q`.
- The five tests above, with their literal expectations:
  `[5, 3, 1]`, `[2, 1]`, `[9, 1]`, `[1]`, and `[2, 1]` from `build_queue`.
- Run against a scratch copy of `ops/scheduler` on 2026-09-24: `394 passed`.

## Dependencies
- Blocked by: None
- Why blocked: N/A
- Blocks: 09 (the remainder promoter adds this label and relies on it meaning "next")

## Labels
`enhancement`, `scheduler`, `priority:high`

## Estimate
Small

## Risk
2 - This changes dispatch order, and only for issues carrying a label that exists in no
repository today. Deploying it is a scheduler rebuild
(`docker compose up -d --build scheduler`).

## Validator Stopping Point
`cd ops/scheduler && uv run pytest -q` passes.
