"""Time-left estimate for a running run (feature: ETA).

`estimate_remaining` is pure, so these pin down the three cases without a host:
subtask-rate projection when decomposition has progress, the repo-average net
against elapsed time before that, and `None` when there is nothing to go on.
"""

from __future__ import annotations

from datetime import timedelta

from fabro_scheduler.app import estimate_remaining


def test_subtask_rate_projects_over_remaining_tasks():
    # 40 minutes elapsed, 2 of 5 tasks done -> 20min/task -> 60min to go.
    remaining = estimate_remaining(
        elapsed=timedelta(minutes=40),
        tasks_completed=2,
        tasks_total=5,
        repo_avg=timedelta(hours=4),
    )
    assert remaining == timedelta(minutes=60)


def test_repo_average_is_used_before_decomposition():
    remaining = estimate_remaining(
        elapsed=timedelta(minutes=30),
        tasks_completed=None,
        tasks_total=None,
        repo_avg=timedelta(hours=2),
    )
    assert remaining == timedelta(hours=1, minutes=30)


def test_repo_average_grids_at_zero_once_past_the_historical_rolling_avg():
    remaining = estimate_remaining(
        elapsed=timedelta(hours=5),
        tasks_completed=None,
        tasks_total=None,
        repo_avg=timedelta(hours=2),
    )
    assert remaining == timedelta(0)


def test_unknown_when_there_is_nothing_to_extrapolate_from():
    assert (
        estimate_remaining(timedelta(minutes=10), None, None, None) is None
    )
    # Zero completed tasks cannot establish a rate even with a total.
    assert (
        estimate_remaining(timedelta(minutes=10), 0, 5, None) is None
    )
