"""`ops/fabro-trial-review.py`, the Mac-side trial review's tables (ralph-v2 11, step 5).

The review is a pipeline: `ops/fabro-trial-review.sh` curls the scorecard's split table, then
pipes the `.py` into `docker exec -i fabro-scheduler python -I -`. This file runs the same
script on the Mac against a fixture database, so the tables can be read in a test rather than
on the host.

It lives in the scheduler's suite because the script imports the real
`fabro_scheduler.scorecard` (`merged_by_workflow`, `held_bucket`), so the fixture's merges and
held reasons are the scorecard's own answers, never a stub of them; `make check`'s unittest
discovery has no `httpx` for that import.

The default output is pinned byte for byte: `--arms` defaults to `backlog,ralph`, and the
golden below is what the script printed before task 11 parameterized the pair.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from fabro_scheduler.store import Store

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "fabro-trial-review.py"
SPEC = importlib.util.spec_from_file_location("trial_review", SCRIPT)
trial_review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial_review)

REPO = "andrewthetechie/jelly-swipe"
OTHER = "andrewthetechie/writers-app"


def _insert(store: Store, sql: str, rows: list[tuple]) -> None:
    with store._lock, store._conn:
        store._conn.executemany(sql, rows)


def seed(path: Path) -> Path:
    """One window's worth of rows: both arms, a merge, a hold, a failover and a summary."""
    store = Store(path)
    try:
        _insert(
            store,
            "INSERT INTO run_history (run_id, coder_pool, repo, issue_number, dispatched_at,"
            " finished_at, kind, requeue_attempt, pr_lookup, pr_number, merged, workflow)"
            " VALUES (?, ?, ?, ?, ?, ?, 'succeeded', 0, ?, ?, ?, ?)",
            [
                ("B1", "coders-a", REPO, 11, "2026-09-01T10:00:00+00:00",
                 "2026-09-01T12:00:00+00:00", "found", 21, 1, "backlog"),
                ("B2", "coders-a", OTHER, 12, "2026-09-02T10:00:00+00:00",
                 "2026-09-02T14:00:00+00:00", "found", 22, None, "backlog"),
                ("R1", "coders-a", REPO, 13, "2026-09-03T10:00:00+00:00",
                 "2026-09-03T18:00:00+00:00", "found", 23, 1, "ralph"),
                ("R2", "coders-c", OTHER, 14, "2026-09-04T10:00:00+00:00",
                 "2026-09-04T13:00:00+00:00", "none", None, None, "ralph"),
                ("V1", "coders-a", REPO, 15, "2026-09-05T10:00:00+00:00",
                 "2026-09-05T20:00:00+00:00", "found", 24, None, "ralph-v2"),
                ("V2", "coders-a", REPO, 16, "2026-09-06T10:00:00+00:00",
                 "2026-09-06T20:00:00+00:00", "found", 25, None, "ralph-v2"),
            ],
        )
        _insert(
            store,
            "INSERT INTO run_summary (run_id, status, attempts, summarized_at, terminal_node,"
            " path, refute, events, task_sources, loop_exhausted, post_iterations)"
            " VALUES (?, 'ok', 1, '2026-09-30T00:00:00+00:00', ?, ?, ?, 100, ?, ?, ?)",
            [
                ("B1", "review_merge.done", '["review_merge.refute_gate>review_merge.done"]',
                 '[{"verdict": "pass", "not_met": false}]', "{}", 0, 0),
                ("R1", "open_pr", '["report_queued>learn_prep"]',
                 '[{"verdict": "fail", "not_met": true}, {"verdict": "pass", "not_met": false}]',
                 '{"plan": 3, "refute": 1}', 0, 1),
                ("R2", "mark_stuck", '["mark_stuck>learn_prep"]',
                 '[{"verdict": "fail", "not_met": true}]', '{"plan": 2}', 1, 0),
                ("V1", "open_pr", '["report_queued>learn_prep"]',
                 '[{"verdict": "pass", "not_met": false}]', '{"plan": 4}', 0, 0),
            ],
        )
        _insert(
            store,
            "INSERT INTO run_stage_stats (run_id, node_id, handler, visits, wall_ms, box_ms,"
            " timeouts, failures, input_tokens, output_tokens, cache_read_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?)",
            [
                ("R1", "plan", "agent", 1, 600_000, 0, 0, 1000, 100, 500),
                ("R1", "iterate_t1", "agent", 3, 3_600_000, 3_600_000, 0, 0, 0, 0),
                ("R1", "refute", "agent", 2, 600_000, 0, 0, 200, 20, 50),
                ("R1", "check", "command", 3, 60_000, 0, 0, 0, 0, 0),
                ("R2", "iterate_t1", "agent", 1, 1_800_000, 1_800_000, 1, 0, 0, 0),
                ("V1", "task_plan", "agent", 1, 480_000, 480_000, 0, 0, 0, 0),
            ],
        )
        _insert(
            store,
            "INSERT INTO run_failovers (run_id, node_id, from_model, to_model, error_kind, count)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [("R1", "refute", "kimi/kimi-k3", "zai/glm-5.3", "access_denied", 2)],
        )
        _insert(
            store,
            "INSERT INTO run_model_usage (run_id, provider, model_id, stages, input_tokens,"
            " output_tokens, cache_read_tokens, cache_write_tokens, cost_usd_micros)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("R1", "zai", "glm-5.3", 2, 1200, 120, 550, 0, 4000),
                ("R1", "box-a", "coders-a", 3, 9000, 900, 0, 0, None),
                ("V1", "openai", "gpt-6-luna", 1, 100, 10, 0, 0, None),
            ],
        )
        _insert(
            store,
            "INSERT INTO pr_facts (repo, pr_number, fetched_at, state, created_at, merged_at,"
            " closed_at, labels, additions, deletions, commits, operator_commits, report, reason,"
            " queue_approved, queue_handoff)"
            " VALUES (?, ?, '2026-09-30T00:00:00+00:00', ?, '2026-09-01T12:00:00+00:00', ?, ?,"
            " '[]', 100, 10, 5, ?, 'complete', ?, 0, 0)",
            [
                (REPO, 21, "merged", "2026-09-01T13:00:00+00:00", None, 0, None),
                (OTHER, 22, "open", None, None, 1,
                 "The refuter could not confirm the work is done: not_met: the diff hygiene note is kept."),
                (REPO, 23, "merged", "2026-09-03T19:00:00+00:00", None, 0, None),
                (REPO, 24, "open", None, None, 0, None),
            ],
        )
    finally:
        store.close()
    return path


@pytest.fixture
def db(tmp_path) -> Path:
    return seed(tmp_path / "scheduler.db")


def review(db: Path, *args: str) -> str:
    """The script's stdout for one run, with the argv production would pass it."""
    out = StringIO()
    argv = ["fabro-trial-review.py", "--db", str(db), "--from", "2026-09-01", "--to", "2026-09-30",
            *args]
    old = sys.argv
    sys.argv = argv
    try:
        with redirect_stdout(out):
            trial_review.main()
    finally:
        sys.argv = old
    return out.getvalue()


def test_the_default_arms_print_what_the_review_always_printed(db):
    """No `--arms`: the pair is `backlog,ralph`, and the whole document is byte for byte what
    the script printed before task 11 made the pair an argument."""
    assert review(db) == EXPECTED_DEFAULT


def test_the_ralph_only_sections_run_for_each_ralph_arm(db):
    """`--arms ralph,ralph-v2` reads the ralph-v2 trial: one lease table and one flag list per
    ralph arm, and O3's table still has every arm the window ran."""
    text = review(db, "--arms", "ralph,ralph-v2")
    assert "## ralph runs: where each lease went (minutes)" in text
    assert "## ralph-v2 runs: where each lease went (minutes)" in text
    assert "## Flags: ralph runs to read first" in text
    assert "## Flags: ralph-v2 runs to read first" in text
    # Each table holds its own arm's runs and nobody else's: V1 is in the v2 section only.
    assert text.count("| run | lease | plan |") == 2
    _, after = text.split("## ralph runs: where each lease went (minutes)", 1)
    v1_section, rest = after.split("## ralph-v2 runs", 1)
    assert "| R1 |" in v1_section and "| V1 |" not in v1_section
    assert "| V1 |" in rest.split("## Flags", 1)[0]
    # The per-run table's ralph-only column is filled for both arms.
    assert "/ 0 / plan 4 |" in text


def test_a_pair_with_no_v1_ralph_prints_one_lease_table(db):
    """A pair of `backlog` and `ralph-v2` has one ralph arm, so one lease table and one flag
    list: the sections follow the names, not the constant `ralph`."""
    text = review(db, "--arms", "backlog,ralph-v2")
    assert "## ralph-v2 runs: where each lease went (minutes)" in text
    assert "## ralph runs: where each lease went (minutes)" not in text
    assert "## ralph-v2 runs to read first" not in text
    assert "## Flags: ralph-v2 runs to read first" in text


def test_an_arm_pair_that_is_not_two_names_is_refused(db):
    with pytest.raises(SystemExit) as raised:
        review(db, "--arms", "ralph")
    assert raised.value.code == 2


def test_the_o3_sentence_names_the_guardrail_of_the_trial_the_pair_reads(db):
    """With a clean merge on each ralph arm, O3's sentence states the guardrail of the trial
    the pair reads: the ralph trial's +20% for the default pair, and for `ralph,ralph-v2` the
    ralph-v2 trial's +50% with its stop above 2x (ADR 0034, *The experiment*). Before this
    the sentence said +20% for the v2 trial too, which is not its rule."""
    c = sqlite3.connect(db)
    with c:
        c.execute(
            "INSERT INTO pr_facts (repo, pr_number, fetched_at, state, created_at, merged_at,"
            " closed_at, labels, additions, deletions, commits, operator_commits, report, reason,"
            " queue_approved, queue_handoff)"
            " VALUES (?, 25, '2026-09-30T00:00:00+00:00', 'merged',"
            " '2026-09-06T12:00:00+00:00', '2026-09-06T21:00:00+00:00', NULL, '[]', 100, 10,"
            " 5, 0, 'complete', NULL, 1, 0)", (REPO,))
    c.close()
    text = review(db, "--arms", "ralph,ralph-v2")
    # V2's ten hours over one clean merge against R1's eleven.
    assert ("ralph-v2's box-hours per clean merge are +82% against ralph's "
            "(the guardrail is +50%, and the trial stops above 2x).") in text
    assert "+20%" not in text
    assert "against backlog's (the guardrail is +20%)." in review(db)


# What the script printed for the fixture above before task 11 made the arm pair an
# argument, captured from `git show 67f3f67:ops/fabro-trial-review.py`. `--arms` defaults to
# `backlog,ralph`, so this is still the default output.
EXPECTED_DEFAULT = """# Trial review tables, 2026-09-01 to 2026-09-30

Window: runs dispatched 2026-09-01 to 2026-09-30 (UTC, inclusive), 6 runs. 6 of them were dispatched before the switch (2026-10-22T21:28:06+00:00).

## Runs

| arm | run | repo | issue | pool | dispatched | lease h | ending | PR | fate | held | op commits | refute | ralph: iter / visits / tasks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| backlog | B1 | jelly-swipe | 11 | coders-a | 09-01T10:00 | 2.0 | refute_gate | 21 | merged (workflow) |  | 0 | P |  |
| backlog | B2 | writers-app | 12 | coders-a | 09-02T10:00 | 4.0 | not summarized | 22 | open | refuter | 1 |  |  |
| ralph | R1 | jelly-swipe | 13 | coders-a | 09-03T10:00 | 8.0 | report_queued | 23 | merged (workflow) |  | 0 | FP | 3 (3/0/0/0) / 2 / plan 3, refute 1 |
| ralph | R2 | writers-app | 14 | coders-c | 09-04T10:00 | 3.0 | mark_stuck |  | no PR |  |  | F | 1 (1/0/0/0) / 0 / plan 2 |
| ralph-v2 | V1 | jelly-swipe | 15 | coders-a | 09-05T10:00 | 10.0 | report_queued | 24 | open | no reason recorded | 0 | P |  |
| ralph-v2 | V2 | jelly-swipe | 16 | coders-a | 09-06T10:00 | 10.0 | not summarized | 25 | not read | not read yet |  |  |  |

## O3: the primary metric and the box-hours guardrail

| arm | runs | PRs | merged by the workflow | of which no operator commit | per run | box-h | box-h per clean merge |
|---|---|---|---|---|---|---|---|
| backlog | 2 | 2 | 1 | 1 | 0.50 | 6.0 | 6.00 |
| ralph | 2 | 1 | 1 | 1 | 0.50 | 11.0 | 11.00 |
| ralph-v2 | 2 | 2 | 0 | 0 | 0.00 | 20.0 | — |

ralph's box-hours per clean merge are +83% against backlog's (the guardrail is +20%).

## ralph runs: where each lease went (minutes)

| run | lease | plan | iterations | check | final_check | refute | standards+spec | merge stage | learn | other | timeouts |
|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 | 480 | 10 | 60 | 1 | 0 | 10 | 0 | 0 | 0 | 399 |  |
| R2 | 180 | 0 | 30 | 0 | 0 | 0 | 0 | 0 | 0 | 150 | iterate_t1 1 |

## Flags: ralph runs to read first

- R1 (jelly-swipe#13): 1 post-PR Iteration(s)
- R2 (writers-app#14): iterate_t1 timed out 1x; the loop ran out (O18)

## Stage time per arm: the ten longest nodes

**backlog** (2 runs, 6.0 lease hours)

| node | runs | visits | wall min | per run | on the box |
|---|---|---|---|---|---|

**ralph** (2 runs, 11.0 lease hours)

| node | runs | visits | wall min | per run | on the box |
|---|---|---|---|---|---|
| iterate_t1 | 2 | 4 | 90 | 45.0 | 90 |
| plan | 1 | 1 | 10 | 5.0 | 0 |
| refute | 1 | 2 | 10 | 5.0 | 0 |
| check | 1 | 3 | 1 | 0.5 | 0 |

**ralph-v2** (2 runs, 20.0 lease hours)

| node | runs | visits | wall min | per run | on the box |
|---|---|---|---|---|---|
| task_plan | 1 | 1 | 8 | 4.0 | 8 |

## Provider failovers

| arm | node | from | to | kind | runs | events |
|---|---|---|---|---|---|---|
| ralph | refute | kimi/kimi-k3 | zai/glm-5.3 | access_denied | 1 | 2 |

## Hosted spend (estimate)

| arm | provider | model | runs | input | cache read | cache write | output | USD | priced by |
|---|---|---|---|---|---|---|---|---|---|
| ralph | box-a | coders-a | 1 | 9.0k | 0 | 0 | 900 | 0.00 | local |
| ralph | zai | glm-5.3 | 1 | 1.2k | 550 | 0 | 120 | 0.00 | fabro |
| ralph-v2 | openai | gpt-6-luna | 1 | 100 | 0 | 0 | 10 | 0.00 | ADR 0013 rates |

- backlog: $0.00 over 2 runs, $0.000 a run.
- ralph: $0.00 over 2 runs, $0.002 a run.
- ralph-v2: $0.00 over 2 runs, $0.000 a run.
- Both arms: $0.00 in 30.0 days, about $0 a month at this rate (the guardrail is $100). Runs outside run_history (automation pr-review, dependabot, arch-review, hand fires) are not in it: check the dashboards for the final result.

"""
