"""Token usage and cost, split local vs hosted, at run release.

The history page's per-run token/billing column (feature: hosted vs local). The
scheduler's only source of truth is fabro's `GET /runs/{id}/usage`, which rolls
usage up *by model* (`by_model`): each row names the provider a stage ran on and
sums that model's tokens and cost. Classifying a row is then "is this provider
one of the LAN coder boxes or not".

Local means the inference ran on a box on this host's LAN — the providers the
coders resolve to (`box-a`, `box-b`, `spark`). Hosted means it left the LAN for
zai/kimi/moonshot or any other provider. The split is what the operator pays
for differently: the boxes are owned kit, the hosted models are metered.

All costs come from fabro in USD micros (1e-6 dollars) and are kept as integers
here, matching the `run_history` columns, so nothing ever drifts through a float.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# The provider ids that mean "ran on a LAN coder box". These are the providers the
# three pinned coder pools resolve to (deployment invariant; `models.mdx` in the
# fabro checkout). A provider not in this set is hosted.
LOCAL_PROVIDERS = frozenset({"box-a", "box-b", "spark"})


@dataclass(frozen=True)
class UsageSplit:
    """One run's usage rolled up two ways, at a point in time.

    All four fields are `None` when there is no usage answer at all (a run that
    made no model calls, or fabro could not be reached) so the history page can
    render "—" and never invent a zero.
    """

    local_tokens: int | None
    hosted_tokens: int | None
    local_cost_usd_micros: int | None
    hosted_cost_usd_micros: int | None

    @property
    def known(self) -> bool:
        return (
            self.local_tokens is not None and self.hosted_tokens is not None
        )


def split_by_model(
    by_model: object,
    *,
    local_providers: frozenset[str] = LOCAL_PROVIDERS,
) -> UsageSplit:
    """Turn fabro's `by_model` array into a local/hosted `UsageSplit`.

    `by_model` is the `usage.by_model` payload of `GET /runs/{id}/usage`: each row
    is `{model: {provider, model_id, ...}, stages, usage: {tokens: {...},
    cost: {usd_micros, source}}}`. Only rows that actually shaped a model call
    carry tokens; a row with a missing/malformed shape is skipped rather than
    treated as zero, because a run that spent nothing should look like it spent
    nothing on the page, not like an unknown.

    Returns a split whose fields are all `None` when `by_model` is empty or not a
    list — the caller distinguishes "no usage" from "zero usage" by that, and
    stores the distinction.
    """
    if not isinstance(by_model, list):
        return _unknown()

    local_tokens = 0
    hosted_tokens = 0
    local_cost = 0
    hosted_cost = 0
    any_tokens = False

    for row in by_model:
        if not isinstance(row, Mapping):
            continue
        model = row.get("model")
        usage = row.get("usage")
        if not isinstance(model, Mapping) or not isinstance(usage, Mapping):
            continue
        provider = model.get("provider")
        tokens = usage.get("tokens")
        if not isinstance(provider, str) or not isinstance(tokens, Mapping):
            continue

        bucket = _token_sum(tokens)
        # A row with no tokens still contributes no cost; a cost without tokens is
        # meaningless and ignored (fabro only prices what it measured).
        if bucket is None:
            continue
        any_tokens = True

        if provider in local_providers:
            local_tokens += bucket
        else:
            hosted_tokens += bucket

        cost = _cost_usd_micros(usage)
        if cost is not None:
            if provider in local_providers:
                local_cost += cost
            else:
                hosted_cost += cost

    if not any_tokens:
        return _unknown()

    return UsageSplit(
        local_tokens=local_tokens,
        hosted_tokens=hosted_tokens,
        local_cost_usd_micros=local_cost,
        hosted_cost_usd_micros=hosted_cost,
    )


def _unknown() -> UsageSplit:
    return UsageSplit(
        local_tokens=None,
        hosted_tokens=None,
        local_cost_usd_micros=None,
        hosted_cost_usd_micros=None,
    )


def _token_sum(tokens: Mapping[str, object]) -> int | None:
    """The five bucket sum, or `None` if it is not a readable token count.

    fabro's `tokens` is `{input, output, reasoning, cache_read, cache_write}`, all
    non-negative ints (lithos-llm's `TokenCounts`, per the events docs). The plain
    sum is the total; any bucket that is not an integer keeps the whole row from
    contributing, the same way a half-parsed event is dropped.
    """
    total = 0
    for value in tokens.values():
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        total += value
    return total


def _cost_usd_micros(usage: Mapping[str, object]) -> int | None:
    """`usage.cost.usd_micros` as an int, or `None` when absent/unpriced.

    fabro's `cost` is optional and never zero: a usage that was not priced has no
    cost at all. Absence here contributes nothing to either side (a metered run on
    a box the catalog does not price shows its tokens and no dollar figure).
    """
    cost = usage.get("cost")
    if not isinstance(cost, Mapping):
        return None
    micros = cost.get("usd_micros")
    if isinstance(micros, bool) or not isinstance(micros, int):
        return None
    return micros
