# 01 · Scheduler: promote held children, and close finished Split parents

Read `00-overview-and-contracts.md` first. The contracts for this task are C3 and C5. This
task comes first because the scheduler must be deployed **before** any graph can create
a held child (ADR 0015, "Consequences").

## Change

`ops/scheduler/src/fabro_scheduler/`:

1. **`github.py`**:
   - Add `HELD_LABEL = "agent-held"` and `SPLIT_LABEL = "agent-split"`, next to
     `REMAINDER_LABEL`, with the same kind of comment.
   - Generalise `fetch_remainders` into `fetch_labelled(repo, label, token, …)`, and keep
     `fetch_remainders` as a thin wrapper so `remainder.py` and its tests do not change.
   - Add these, with the same error handling and timeout as `fetch_pull_state`:
     - `fetch_issue_state(repo, number, token) -> (state, state_reason)`;
     - `fetch_sub_issues(repo, number, token) -> list[(number, state, state_reason)]`,
       which pages through all results;
     - `comment(repo, number, body, token)`;
     - `close_issue(repo, number, token, reason="completed")`.
2. **`split.py`** (new, beside `remainder.py`, with the same shape and docstring style):
   - `promote_held(repo, token, client=None) -> list[(number, action)]`, with the actions
     `promoted`, `orphaned` and `cleaned`.
   - `close_parents(repo, token, client=None) -> list[(number, action)]`, with the actions
     `closed` and `stuck`.
   - The marker regex is
     `<!-- fabro:split-child parent=(\d+) after=(\d+) -->`.
   - Keep the **order** rule from `remainder.py`: add `priority`, then `agent`, then
     remove `agent-held`. `agent-stuck` on the parent is added only if it is not there
     yet.
3. **`inventory.py`**: call both functions from `_promote`, after `promote_remainders`,
   each in its own `try`, so that one failure does not stop the others. `_promote` never
   raises.

## Tests (`ops/scheduler/tests/test_split.py`)

Mirror `test_remainder.py`'s httpx mocking. Cover these cases:

- predecessor `completed` → promoted, with the label calls in order;
- predecessor `not_planned` → orphaned;
- predecessor open → no call;
- `after=0` → left alone;
- missing marker → left alone, with a warning;
- already `agent` → cleaned only;
- a 5xx on one issue → the next issue is still processed.

Parent cases:

- all children completed → comment, then close;
- one `not_planned` → `agent-stuck` added once, and not added again on the second pass;
- an empty sub-issue list → left alone;
- a 5xx → nothing is closed.

## Acceptance

- `uv run pytest` passes in `ops/scheduler/`. `test_remainder.py` is unchanged and green.
- Read-only check against a real repository:
  `GET /repos/andrewthetechie/jelly-swipe/issues/1/sub_issues` returns 200 with the
  scheduler's token (`~/fabro/scheduler.env`). The token needs `issues: write` for the
  close, and it already has it for `remainder.py`.
- The deploy is task 07's job, not this task's.
