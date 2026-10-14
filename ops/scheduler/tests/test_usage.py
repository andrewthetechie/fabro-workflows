"""The hosted-vs-local token/cost split (feature: billing on the history page).

`split_by_model` turns fabro's `/runs/{id}/usage` `by_model` payload into two
columns. The three things worth pinning down: local (the LAN box providers) vs
hosted classification lands on the right side, cost follows the same side, and a
run with no measurable usage comes back as *unknown* (all `None`) rather than a
made-up zero — so the page can tell "spent nothing" from "could not tell".
"""

from __future__ import annotations

from fabro_scheduler.usage import ModelUsage, model_rows, split_by_model, UsageSplit


def _row(
    provider: str,
    *,
    input: int = 0,
    output: int = 0,
    reasoning: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cost: int | None = None,
) -> dict:
    tokens = {
        "input": input,
        "output": output,
        "reasoning": reasoning,
        "cache_read": cache_read,
        "cache_write": cache_write,
    }
    usage = {"tokens": tokens}
    if cost is not None:
        usage["cost"] = {"usd_micros": cost, "source": "catalog"}
    return {"model": {"provider": provider, "model_id": "x"}, "stages": 1, "usage": usage}


def test_local_providers_are_the_lan_boxes():
    split = split_by_model(
        [_row("box-a", input=1000, output=500, cost=42_000)]
    )
    assert split.local_tokens == 1500
    assert split.hosted_tokens == 0
    assert split.local_cost_usd_micros == 42_000
    assert split.hosted_cost_usd_micros == 0


def test_unknown_providers_are_hosted():
    split = split_by_model(
        [_row("zai", input=2000, output=500, cost=90_000),
         _row("kimi", input=300, cost=5_000)]
    )
    assert split.local_tokens is not None and split.local_tokens == 0
    assert split.hosted_tokens == 2800
    assert split.local_cost_usd_micros == 0
    assert split.hosted_cost_usd_micros == 95_000


def test_a_mixed_run_splits_both_sides():
    split = split_by_model(
        [_row("box-b", input=1000, output=1000, cost=10_000),
         _row("moonshot", input=500, output=500, cost=20_000)]
    )
    assert split.local_tokens == 2000
    assert split.hosted_tokens == 1000
    assert split.local_cost_usd_micros == 10_000
    assert split.hosted_cost_usd_micros == 20_000


def test_token_sum_is_the_five_bucket_total():
    # reasoning + cache are part of the total, not separately counted.
    split = split_by_model(
        [_row("zai", input=10, output=20, reasoning=5, cache_read=25, cache_write=40, cost=1)]
    )
    assert split.hosted_tokens == 100


def test_empty_by_model_is_unknown_not_zero():
    split = split_by_model([])
    assert split.known is False
    assert split.local_tokens is None and split.hosted_tokens is None


def test_non_list_by_model_is_unknown():
    assert split_by_model(None).known is False
    assert split_by_model({"usage": {}}).known is False


def test_a_row_with_no_readable_tokens_is_dropped_not_zero():
    # A model row with malformed/absent tokens contributes nothing (unknown), and
    # certainly does not count as a zero-cost zero-token row.
    bad = [_row("box-a", cost=1)]  # cost only, no tokens keys set — still has keys
    # Remove the tokens so the row cannot be summed.
    bad[0]["usage"]["tokens"] = {"input": "nope"}
    split = split_by_model(bad)
    assert split.known is False


def test_unpriced_usage_keeps_its_tokens_and_no_cost():
    split = split_by_model([_row("zai", input=5000)])  # no cost -> absent
    assert split.hosted_tokens == 5000
    assert split.hosted_cost_usd_micros == 0


def _model(provider, model_id, *, speed=None, **kw):
    row = _row(provider, **kw)
    row["model"] = {"provider": provider, "model_id": model_id, "speed": speed}
    return row


def test_model_rows_keep_each_model_and_count_reasoning_as_output():
    rows = model_rows([
        _model("zai", "glm-5.3", input=100, output=10, reasoning=5, cache_read=7, cost=900),
        _model("box-a", "deepseek", input=1000, output=50),
    ])
    assert rows == [
        ModelUsage("box-a", "deepseek", 1, 1000, 50, 0, 0, None),
        ModelUsage("zai", "glm-5.3", 1, 100, 15, 7, 0, 900),
    ]
    assert rows[1].total_tokens == 122


def test_model_rows_merge_speed_tiers_of_one_model():
    rows = model_rows([
        _model("openai", "gpt-6-luna", speed="fast", input=10, cost=5),
        _model("openai", "gpt-6-luna", input=20),
    ])
    assert rows == [ModelUsage("openai", "gpt-6-luna", 2, 30, 0, 0, 0, 5)]


def test_model_rows_skip_unreadable_rows():
    bad_tokens = _model("zai", "glm-5.3", input=1)
    bad_tokens["usage"]["tokens"]["output"] = "lots"
    no_id = _row("zai", input=1)
    del no_id["model"]["model_id"]
    assert model_rows([bad_tokens, no_id, "junk"]) == []
    assert model_rows(None) == []
