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
  keys;
* `agent.tool.started`: `properties.event.ToolCallStarted.tool_name`, with the event's
  top-level `ts`.

The plan trial (#25) added three facts. Each stage visit in the stage list carries
`usage.tokens.{input, output, reasoning, cache_read}`, summed per node here, so the
cost of `plan` (hosted) and `improve` (the box) can be compared. `GET /runs/{id}/usage`
has the same numbers already summed per node, but the stage list is fetched anyway.
The coder's time to first edit is `stage.started` of `coder` to its first
`edit_file`/`write_file`, once per attempt. `improve_gate`'s `task_disposition` is
counted from its `stage.completed`.

Task validation (ADR 0025) added three more. Every `validate` visit is one
`run_summary.task_validations` entry `{mode, reason_class, ok, ms}`: a completed visit
carries the routing keys from its `stage.completed` (`task_validation_mode` and friends),
and a visit with none -- a run from before the change, or a `validate` that failed --
records `legacy`, or `timeout` when its `stage.failed` has `system_actor: "timeout"`. `ms`
is the visit's `properties.timing.wall_time_ms`, the same clock `run_stage_stats` uses.
A red visit with no `stage.completed` is recorded from its `stage.failed`; without that,
the baseline would count only green visits. `task_validation_unchecked` and
`task_validation_untested` are summed into the `RunSummary` fields of the same names.
Each `open_pr_prep` visit appends its `final_route` to `RunSummary.final_routes`, in
order.

Pure: no I/O, so the parsing is tested with fixture event lines.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

# A stage is coder-box work when it ran on the run's own coder pool. Every pool is
# a LiteLLM-style group named `coders-<x>`, so the model name says it without a
# provider list that goes stale each time a pool is added (coders-c is `spark`,
# coders-d is `freellm`).
CODER_MODEL_PREFIX = "coders-"

# How many edges of the run's end to keep. Enough to show how a run with no PR
# got where it stopped (`... -> human_rescue -> exit`), small enough to store.
PATH_TAIL = 8

@dataclass(frozen=True)
class WorkflowNodes:
    """The node ids one workflow's summary reads (ADR 0029 C12).

    Two workflows share this module and none of these ids: `backlog`'s refute gate
    is the imported phase's `review_merge.refute_gate`, and `ralph`'s is its own
    `refute_gate`; `backlog`'s coder attempts start at `coder`, `ralph`'s at any of
    the four `iterate_t*` rungs. A summary that guessed would read zeroes and look
    like a healthy run, so the ids are a table rather than a constant.
    """

    refute_gate: str
    # Every node that opens a coder attempt. A first edit is measured per attempt,
    # so a rung-2 Iteration counts exactly as a rung-1 one does.
    coder_nodes: frozenset[str]
    validate: str
    final_validation: str
    # `backlog`'s decomposer gate publishes the disposition; `ralph` has no
    # per-task decomposition, so it publishes nothing and this is empty.
    disposition_gate: str


NODES: dict[str, WorkflowNodes] = {
    "backlog": WorkflowNodes(
        refute_gate="review_merge.refute_gate",
        coder_nodes=frozenset({"coder"}),
        validate="validate",
        final_validation="open_pr_prep",
        disposition_gate="improve_gate",
    ),
    "ralph": WorkflowNodes(
        refute_gate="refute_gate",
        coder_nodes=frozenset({"iterate_t1", "iterate_t2", "iterate_t3", "iterate_t4"}),
        validate="check",
        final_validation="final_check",
        disposition_gate="",
    ),
}

# The node every terminal `ralph` node routes through before `exit` (C9). The edge
# INTO it names the ending: `report_queued -> learn_prep` says the run handed its PR
# to the merge queue, which is the fact the Retro's ending key wants. Without this
# every ralph run would be classed by `learn_post`, the last node it started.
LEARN_PREP_NODE = "learn_prep"

# The plan trial's coder measurements (#25). Superseded by `NODES`, kept because the
# docstrings and the tests below name them.
EDIT_TOOLS = frozenset({"edit_file", "write_file"})


@dataclass(frozen=True)
class TaskValidation:
    """One `validate` visit (ADR 0025): how it ran and how long it took.

    `mode` is `scoped`, `full`, `legacy` (a run summarized before the change, or a
    visit whose `stage.completed` carried no routing keys) or `timeout`. `ok` is
    None when neither a context update nor the stage's own status could say.
    """

    mode: str
    reason_class: str  # the reason text before the first ":", or ""
    ok: bool | None
    ms: int


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
    input_tokens: int = 0
    output_tokens: int = 0  # output plus reasoning
    cache_read_tokens: int = 0


@dataclass(frozen=True)
class RefuteVerdict:
    """One pass of `refute_gate`, in order. `not_met` is None before gate relief."""

    verdict: str
    not_met: bool | None
    unverifiable_only: bool | None = None
    ci_credited: int | None = None


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
    # Per coder attempt that made an edit, ms from its start to the first edit, in order.
    first_edit_ms: list[int] = field(default_factory=list)
    dispositions: Counter[str] = field(default_factory=Counter)  # improve_gate's verdicts
    # Task validation (ADR 0025): every `validate` visit in order, the summed counts of
    # unchecked and untested files, and every `open_pr_prep` visit's final route in order.
    task_validations: list[TaskValidation] = field(default_factory=list)
    task_validation_unchecked: int = 0
    task_validation_untested: int = 0
    final_routes: list[str] = field(default_factory=list)
    # --- ralph only (ADR 0029 C12). Zero/empty for a backlog run. ---------------
    # Iterations that ran after `pr_handoff`: the loop's post-PR budget is 2 (O13),
    # and how often it is spent is a trial number.
    post_iterations: int = 0
    # Whether the loop ever published `loop=exhausted`: the run ran out of rungs,
    # rounds or budget rather than finishing.
    loop_exhausted: bool = False
    # Task id -> `source` (`plan`, `refute`, `review`, `final_fix`, `ci`). Filled by
    # the collector, which is the only reader of the run's `tasks.json`.
    task_sources: Counter[str] = field(default_factory=Counter)


def summarize(
    run_id: str,
    stages: Iterable[Mapping[str, object]],
    events: Iterable[Mapping[str, object]],
    workflow: str = "backlog",
) -> RunSummary:
    """Reduce one run's stage list and event stream to a `RunSummary`.

    `events` is consumed once, in order, and never held: the caller passes a
    generator that pages through the API, so memory stays at one page.
    Malformed rows are skipped rather than raised on. A summary missing one
    event is still worth having, and a raise would retry the whole run forever.
    """
    nodes = NODES.get(workflow, NODES["backlog"])
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
        usage = stage.get("usage")
        tokens = usage.get("tokens") if isinstance(usage, Mapping) else None
        if isinstance(tokens, Mapping):
            stat.input_tokens += _int(tokens.get("input"))
            stat.output_tokens += _int(tokens.get("output")) + _int(tokens.get("reasoning"))
            stat.cache_read_tokens += _int(tokens.get("cache_read"))

    failovers: Counter[tuple[str, str, str, str]] = Counter()
    path: list[str] = []
    terminal: str | None = None
    refute: list[RefuteVerdict] = []
    first_edit: list[int] = []
    coder_started: datetime | None = None  # the open coder attempt that has not edited yet
    dispositions: Counter[str] = Counter()
    task_validations: list[TaskValidation] = []
    task_validation_unchecked = 0
    task_validation_untested = 0
    final_routes: list[str] = []
    post_iterations = 0
    loop_exhausted = False
    phase = "pre"
    ending: str | None = None
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
            if node in nodes.coder_nodes:
                coder_started = _ts(event.get("ts"))
                if phase == "post":
                    post_iterations += 1
        elif name == "agent.tool.started" and node in nodes.coder_nodes and coder_started is not None:
            inner = props.get("event")
            call = inner.get("ToolCallStarted") if isinstance(inner, Mapping) else None
            if isinstance(call, Mapping) and _str(call.get("tool_name")) in EDIT_TOOLS:
                at = _ts(event.get("ts"))
                if at is not None and at >= coder_started:
                    first_edit.append(int((at - coder_started).total_seconds() * 1000))
                coder_started = None
        elif name == "stage.failed":
            failure = props.get("failure")
            if node and isinstance(failure, Mapping):
                stat = stats.get(node)
                if stat is None:
                    stat = stats[node] = StageStat(node, "unknown")
                timeout = failure.get("system_actor") == "timeout"
                if timeout:
                    stat.timeouts += 1
                stat.failures[_str(failure.get("category")) or "unknown"] += 1
                if node == nodes.validate:
                    # A red legacy visit has no `stage.completed` (verified live on 0.362);
                    # recording it here keeps the baseline's visit count whole.
                    task_validations.append(
                        TaskValidation(
                            mode="timeout" if timeout else "legacy",
                            reason_class="",
                            ok=False,
                            ms=_timing_ms(props),
                        )
                    )
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
                if dst == LEARN_PREP_NODE and src:
                    ending = src
        elif name == "stage.completed" and node == "pick":
            # `ralph`'s loop head: it publishes the phase it is about to run in and
            # whether the loop has ever run out. Both are read here rather than from
            # a file, because a summary must come from the run's own event stream.
            updates = props.get("context_updates")
            if isinstance(updates, Mapping):
                here = _str(updates.get("phase"))
                if here in ("pre", "post"):
                    phase = here
                exhausted = updates.get("loop_exhausted")
                if exhausted is True or _bool_text(exhausted) is True:
                    loop_exhausted = True
        elif name == "stage.completed" and node == nodes.disposition_gate and nodes.disposition_gate:
            updates = props.get("context_updates")
            if isinstance(updates, Mapping):
                disposition = _str(updates.get("task_disposition"))
                if disposition:
                    dispositions[disposition] += 1
        elif name == "stage.completed" and node == nodes.validate:
            updates = props.get("context_updates")
            if not isinstance(updates, Mapping):
                updates = {}
            mode = _str(updates.get("task_validation_mode")) or "legacy"
            ok = updates.get("task_validation_ok")
            if not isinstance(ok, bool):
                ok = _bool_text(ok)
            if ok is None:
                # Before the change `validate` published nothing; its own status is left.
                ok = _str(props.get("status")) == "succeeded"
            task_validations.append(
                TaskValidation(
                    mode=mode,
                    reason_class=_reason_class(_str(updates.get("task_validation_reason"))),
                    ok=ok,
                    ms=_timing_ms(props),
                )
            )
            task_validation_unchecked += _int(updates.get("task_validation_unchecked"))
            task_validation_untested += _int(updates.get("task_validation_untested"))
        elif name == "stage.completed" and node == nodes.final_validation:
            updates = props.get("context_updates")
            if isinstance(updates, Mapping):
                route = _str(updates.get("final_route"))
                if route:
                    final_routes.append(route)
        elif name == "stage.completed" and node == nodes.refute_gate:
            updates = props.get("context_updates")
            if isinstance(updates, Mapping):
                verdict = _str(updates.get("refute_verdict"))
                if verdict:
                    not_met = updates.get("refute_not_met")
                    unverifiable_only = updates.get("refute_unverifiable_only")
                    ci_credited = updates.get("refute_ci_credited")
                    refute.append(
                        RefuteVerdict(
                            verdict,
                            not_met if isinstance(not_met, bool) else _bool_text(not_met),
                            unverifiable_only if isinstance(unverifiable_only, bool) else _bool_text(unverifiable_only),
                            ci_credited if isinstance(ci_credited, int) else _int_or_none(ci_credited),
                        )
                    )

    return RunSummary(
        run_id=run_id,
        stages=stats,
        failovers=failovers,
        path=path,
        # A `ralph` run's last started node is always `learn_post`; the ending is the
        # node that routed into `learn_prep` (see LEARN_PREP_NODE).
        terminal_node=ending or terminal,
        refute=refute,
        events=count,
        first_edit_ms=first_edit,
        dispositions=dispositions,
        task_validations=task_validations,
        task_validation_unchecked=task_validation_unchecked,
        task_validation_untested=task_validation_untested,
        final_routes=final_routes,
        post_iterations=post_iterations,
        loop_exhausted=loop_exhausted,
    )


def _timing_ms(props: Mapping[str, object]) -> int:
    """A stage event's `properties.timing.wall_time_ms`, the clock `run_stage_stats` uses."""
    timing = props.get("timing")
    return _int(timing.get("wall_time_ms")) if isinstance(timing, Mapping) else 0


def _reason_class(reason: str) -> str:
    """A fallback reason's class: the text before the first `:` (`full_glob:path`)."""
    return reason.split(":", 1)[0]


def _ts(value: object) -> datetime | None:
    """An event's ISO-8601 `ts`. fabro writes nanoseconds, which 3.11 accepts and truncates."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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


def _int_or_none(value: object) -> int | None:
    """A context value that is an int or a digit string; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
