"""The hosted-vs-local token/cost split (feature: billing on the history page).

`split_by_model` turns fabro's `/runs/{id}/usage` `by_model` payload into two
columns. The three things worth pinning down: local (the LAN box providers) vs
hosted classification lands on the right side, cost follows the same side, and a
run with no measurable usage comes back as *unknown* (all `None`) rather than a
made-up zero — so the page can tell "spent nothing" from "could not tell".
"""

from __future__ import annotations

from fabro_scheduler.usage import split_by_model, UsageSplit


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
