"""The run summarizer (issue #22) over fixture event lines.

Each fixture line is the shape `GET /runs/{id}/events` returned on fabro 0.362 on
2026-10-12, cut down to the fields the summarizer reads plus some it must ignore.
"""

from __future__ import annotations

import json

from fabro_scheduler.summary import PATH_TAIL, RefuteVerdict, summarize

STAGES = [
    {"node_id": "start", "handler": "start", "status": "succeeded", "visit": 1, "wall_time_ms": 0},
    {"node_id": "claim", "handler": "command", "status": "succeeded", "visit": 1, "wall_time_ms": 5407},
    {
        "node_id": "improve", "handler": "agent", "status": "succeeded", "visit": 1,
        "wall_time_ms": 600_000,
        "provider_used": {"mode": "agent", "provider": "box-b", "model": "coders-b"},
    },
    {
        "node_id": "improve", "handler": "agent", "status": "succeeded", "visit": 2,
        "wall_time_ms": 2_700_000,
        "provider_used": {"mode": "agent", "provider": "freellm", "model": "coders-d"},
    },
    {
        "node_id": "spec", "handler": "agent", "status": "succeeded", "visit": 1,
        "wall_time_ms": 120_000,
        "provider_used": {"mode": "agent", "provider": "zai", "model": "glm-5.3"},
    },
    {
        "node_id": "review_merge.review_fix", "handler": "agent", "status": "failed",
        "visit": 1, "wall_time_ms": 900_075,
        "provider_used": {"mode": "agent", "provider": "box-a", "model": "coders-a"},
    },
    {"node_id": "human_rescue", "handler": "human", "status": "succeeded", "visit": 1, "wall_time_ms": 3_600_000},
    {"handler": "command", "status": "succeeded"},  # no node_id: skipped
]

EVENT_LINES = """
{"seq": 1, "event": "run.created", "properties": {"title": "Backlog"}}
{"seq": 2, "event": "stage.started", "node_id": "start", "properties": {"index": 0}}
{"seq": 3, "event": "edge.selected", "properties": {"from_node": "start", "to_node": "claim", "reason": "unconditional", "stage_status": "succeeded", "is_jump": false}}
{"seq": 4, "event": "stage.started", "node_id": "spec", "properties": {"index": 3}}
{"seq": 5, "event": "agent.error", "node_id": "spec", "properties": {"stage": "spec", "visit": 1, "event": {"Error": {"error": {"kind": "llm", "llm_kind": "access_denied", "provider": "kimi"}}}}}
{"seq": 6, "event": "agent.route.failover", "node_id": "spec", "properties": {"stage": "spec", "visit": 1, "event": {"RouteFailover": {"from": "kimi/kimi-k3", "to": "zai/glm-5.3", "attempt": 1, "error": {"kind": "llm", "llm_kind": "access_denied", "provider": "kimi", "status": 403}}}}}
{"seq": 7, "event": "agent.route.failover", "properties": {"stage": "spec", "visit": 2, "event": {"RouteFailover": {"from": "kimi/kimi-k3", "to": "zai/glm-5.3", "error": {"llm_kind": "access_denied"}}}}}
{"seq": 8, "event": "stage.started", "node_id": "review_merge.review_fix", "properties": {"index": 88}}
{"seq": 9, "event": "stage.failed", "node_id": "review_merge.review_fix", "properties": {"index": 88, "failure": {"message": "handler timed out after 900000ms", "category": "transient_infra", "system_actor": "timeout"}, "will_retry": true}}
{"seq": 10, "event": "stage.retrying", "node_id": "review_merge.review_fix", "properties": {"index": 88, "attempt": 1, "max_attempts": 2}}
{"seq": 11, "event": "stage.failed", "node_id": "review_merge.review_fix", "properties": {"index": 88, "failure": {"message": "handler timed out after 900000ms", "category": "transient_infra", "system_actor": "timeout"}, "will_retry": false}}
{"seq": 12, "event": "stage.failed", "node_id": "validate", "properties": {"failure": {"message": "Script failed with exit code: 1", "category": "deterministic"}}}
{"seq": 13, "event": "stage.completed", "node_id": "review_merge.refute_gate", "properties": {"status": "succeeded", "context_updates": {"refute_verdict": "fail", "refute_not_met": true, "refute_fix_attempts": 0}}}
{"seq": 14, "event": "stage.completed", "node_id": "review_merge.refute_gate", "properties": {"status": "succeeded", "context_updates": {"refute_verdict": "pass", "refute_not_met": "false"}}}
{"seq": 15, "event": "stage.completed", "node_id": "review_merge.refute_gate", "properties": {"status": "succeeded", "context_updates": {"refute_verdict": "fail"}}}
{"seq": 16, "event": "stage.completed", "node_id": "review_merge.standards_gate", "properties": {"context_updates": {"refute_verdict": "pass"}}}
{"seq": 17, "event": "stage.started", "node_id": "human_rescue", "properties": {}}
{"seq": 18, "event": "edge.selected", "properties": {"from_node": "review_merge.review_fix", "to_node": "review_merge.mark_needs_human", "reason": "unconditional", "stage_status": "failed"}}
{"seq": 19, "event": "stage.started", "node_id": "exit", "properties": {}}
{"seq": 20, "event": "stage.failed", "node_id": "validate", "properties": "not an object"}
{"seq": 21, "event": "agent.route.failover", "properties": {"stage": "spec", "event": "not an object"}}
{"seq": 22, "event": "edge.selected", "properties": {"from_node": "x"}}
"""


def events():
    return [json.loads(line) for line in EVENT_LINES.strip().splitlines()]


def test_stage_time_and_visits_come_from_the_stage_list():
    summary = summarize("R1", STAGES, events())
    improve = summary.stages["improve"]
    assert improve.visits == 2
    assert improve.wall_ms == 3_300_000
    # Both visits ran on a coder pool, whichever provider backs it.
    assert improve.box_ms == 3_300_000
    assert summary.stages["spec"].box_ms == 0
    assert summary.stages["human_rescue"].handler == "human"
    assert summary.stages["claim"].wall_ms == 5407


def test_every_timed_out_attempt_counts_even_when_retried():
    summary = summarize("R1", STAGES, events())
    review_fix = summary.stages["review_merge.review_fix"]
    assert review_fix.timeouts == 2
    assert review_fix.failures == {"transient_infra": 2}


def test_a_failure_on_a_node_missing_from_the_stage_list_is_still_counted():
    summary = summarize("R1", STAGES, events())
    validate = summary.stages["validate"]
    assert validate.handler == "unknown"
    assert validate.timeouts == 0
    assert validate.failures == {"deterministic": 1}


def test_failovers_are_counted_by_node_route_and_error_kind():
    summary = summarize("R1", STAGES, events())
    assert summary.failovers == {
        ("spec", "kimi/kimi-k3", "zai/glm-5.3", "access_denied"): 2,
    }


def test_path_keeps_the_last_edges_and_skips_incomplete_ones():
    summary = summarize("R1", STAGES, events())
    assert summary.path == [
        "start>claim",
        "review_merge.review_fix>review_merge.mark_needs_human",
    ]


def test_path_is_capped():
    many = [
        {"event": "edge.selected", "properties": {"from_node": f"n{i}", "to_node": f"n{i + 1}"}}
        for i in range(PATH_TAIL + 5)
    ]
    summary = summarize("R1", [], many)
    assert len(summary.path) == PATH_TAIL
    assert summary.path[-1] == f"n{PATH_TAIL + 4}>n{PATH_TAIL + 5}"


def test_terminal_node_is_the_last_node_started_other_than_exit():
    assert summarize("R1", STAGES, events()).terminal_node == "human_rescue"


def test_refute_verdicts_in_order_with_not_met_when_known():
    summary = summarize("R1", STAGES, events())
    assert summary.refute == [
        RefuteVerdict("fail", True),
        RefuteVerdict("pass", False),
        RefuteVerdict("fail", None),
    ]


def test_counts_every_event_and_survives_an_empty_log():
    assert summarize("R1", STAGES, events()).events == 22
    empty = summarize("R1", [], [])
    assert empty.events == 0
    assert empty.stages == {}
    assert empty.terminal_node is None


def test_events_are_consumed_once_from_a_generator():
    summary = summarize("R1", STAGES, (event for event in events()))
    assert summary.events == 22
