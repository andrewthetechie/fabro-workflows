"""One run's event log reduced to the few facts the scorecard needs (issue #22).

A run's raw events are 5 to 25 MB. The 2026-10-11 retro exported them to the Mac
and filled its disk, so the scheduler now streams them page by page on the host
and keeps only what this module returns, a few hundred bytes per run.

Two sources, each used for what only it answers well:

* **`GET /runs/{id}/stages`** gives every stage visit with its node, handler,
  status, `wall_time_ms` and `provider_used`. Stage time and visits come from
  here, because the event log repeats them across several event kinds.
* **`GET /runs/{id}/events`** (streamed) gives what the stage list hides. A
  timed-out stage that was retried, or promoted by `on_failure`, can still end
  `succeeded` in the stage list; only its `stage.failed` event says
  `system_actor: "timeout"`. Provider failovers (`agent.route.failover`), the
  edge path into the end of the run (`edge.selected`) and the refuter's verdicts
  (`review_merge.refute_gate`'s `context_updates`) exist only here.

Event shapes, verified live on fabro 0.362 (2026-10-12):

* the discriminator is `event`; the node is the event's top-level `node_id`, or
  `properties.stage` on agent events;
* `stage.failed`: `properties.failure.{message, category, system_actor}`; a
  timeout is `system_actor == "timeout"` (`handler timed out after 900000ms`);
* `agent.route.failover`: `properties.event.RouteFailover.{from, to, error.llm_kind}`,
  e.g. `kimi/kimi-k3` -> `zai/glm-5.3`, `access_denied` on the Kimi weekly quota;
* `edge.selected`: `properties.{from_node, to_node}`;
* `stage.completed`: `properties.context_updates` holds a command node's routing
  keys.

Pure: no I/O, so the parsing is tested with fixture event lines.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# A stage is coder-box work when it ran on the run's own coder pool. Every pool is
# a LiteLLM-style group named `coders-<x>`, so the model name says it without a
# provider list that goes stale each time a pool is added (coders-c is `spark`,
# coders-d is `freellm`).
CODER_MODEL_PREFIX = "coders-"

# How many edges of the run's end to keep. Enough to show how a run with no PR
# got where it stopped (`... -> human_rescue -> exit`), small enough to store.
PATH_TAIL = 8

REFUTE_GATE = "review_merge.refute_gate"


@dataclass
class StageStat:
    """One node's totals across every visit in one run."""

    node_id: str
    handler: str
    visits: int = 0
    wall_ms: int = 0
    # The part of `wall_ms` spent on the run's coder pool (see CODER_MODEL_PREFIX).
    box_ms: int = 0
    timeouts: int = 0
    failures: Counter[str] = field(default_factory=Counter)  # category -> count


@dataclass(frozen=True)
class RefuteVerdict:
    """One pass of `refute_gate`, in order. `not_met` is None before gate relief."""

    verdict: str
    not_met: bool | None


@dataclass
class RunSummary:
    run_id: str
    stages: dict[str, StageStat]
    # (node, from model, to model, error kind) -> times it happened in this run
    failovers: Counter[tuple[str, str, str, str]]
    path: list[str]  # the last PATH_TAIL edges, "from>to"
    terminal_node: str | None  # the last node the run started, other than exit
    refute: list[RefuteVerdict]
    events: int  # how many events were read; 0 means the log was empty


def summarize(
    run_id: str, stages: Iterable[Mapping[str, object]], events: Iterable[Mapping[str, object]]
) -> RunSummary:
    """Reduce one run's stage list and event stream to a `RunSummary`.

    `events` is consumed once, in order, and never held: the caller passes a
    generator that pages through the API, so memory stays at one page.
    Malformed rows are skipped rather than raised on. A summary missing one
    event is still worth having, and a raise would retry the whole run forever.
    """
    stats: dict[str, StageStat] = {}
    for stage in stages:
        node = _str(stage.get("node_id"))
        if not node:
            continue
        stat = stats.get(node)
        if stat is None:
            stat = stats[node] = StageStat(node, _str(stage.get("handler")) or "unknown")
        stat.visits += 1
        wall = _int(stage.get("wall_time_ms"))
        stat.wall_ms += wall
        provider = stage.get("provider_used")
        if isinstance(provider, Mapping) and _str(provider.get("model")).startswith(
            CODER_MODEL_PREFIX
        ):
            stat.box_ms += wall

    failovers: Counter[tuple[str, str, str, str]] = Counter()
    path: list[str] = []
    terminal: str | None = None
    refute: list[RefuteVerdict] = []
    count = 0

    for event in events:
        count += 1
        name = event.get("event") or event.get("type")
        props = event.get("properties")
        if not isinstance(props, Mapping):
            props = {}
        node = _str(event.get("node_id")) or _str(props.get("stage"))

        if name == "stage.started":
            if node and node != "exit":
                terminal = node
        elif name == "stage.failed":
            failure = props.get("failure")
            if node and isinstance(failure, Mapping):
                stat = stats.get(node)
                if stat is None:
                    stat = stats[node] = StageStat(node, "unknown")
                if failure.get("system_actor") == "timeout":
                    stat.timeouts += 1
                stat.failures[_str(failure.get("category")) or "unknown"] += 1
        elif name == "agent.route.failover":
            inner = props.get("event")
            route = inner.get("RouteFailover") if isinstance(inner, Mapping) else None
            if isinstance(route, Mapping):
                error = route.get("error")
                kind = _str(error.get("llm_kind")) if isinstance(error, Mapping) else ""
                failovers[
                    (node or "unknown", _str(route.get("from")), _str(route.get("to")), kind or "unknown")
                ] += 1
        elif name == "edge.selected":
            src, dst = _str(props.get("from_node")), _str(props.get("to_node"))
            if src and dst:
                path.append(f"{src}>{dst}")
                if len(path) > PATH_TAIL:
                    del path[0]
        elif name == "stage.completed" and node == REFUTE_GATE:
            updates = props.get("context_updates")
            if isinstance(updates, Mapping):
                verdict = _str(updates.get("refute_verdict"))
                if verdict:
                    not_met = updates.get("refute_not_met")
                    refute.append(
                        RefuteVerdict(
                            verdict,
                            not_met if isinstance(not_met, bool) else _bool_text(not_met),
                        )
                    )

    return RunSummary(
        run_id=run_id,
        stages=stats,
        failovers=failovers,
        path=path,
        terminal_node=terminal,
        refute=refute,
        events=count,
    )


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _bool_text(value: object) -> bool | None:
    """Context values can arrive as the strings "true"/"false"."""
    if value == "true":
        return True
    if value == "false":
        return False
    return None
