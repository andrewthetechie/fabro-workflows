"""The weekly retro: one window's runs grouped into **failure classes** (issue #33).

The 2026-10-11 retro found three problems that had cost runs for weeks -- the
workflow-scope push refusal, merge-phase timeouts that recorded no reason, and the
Kimi weekly quota -- and all three were visible in the scorecard's numbers. This
module is the part that reads them: it takes one window's `ScorecardRows`
(`Store.scorecard_rows`) and returns every class of failure in it, with the runs,
counts and box-hours that make it a decision rather than a graph.

Pure, like `scorecard.py`: rows in, dataclasses out, no file, network, clock or
environment. The dates, the GitHub issues and the CLI all live elsewhere (`app.py`'s
`_retro`); ADR 0026 is the design.

A class has one of four **kinds**, each keyed only from enumerable values so next
week's class matches this week's (`docs/factory-retro/00-overview-and-contracts.md`,
C1):

* `ending:<node>:<detail>` -- a run that ended without shipping, by where and why.
* `held:<bucket>` -- PRs the workflow did not merge, by `held_bucket`'s reason.
* `timeout:<node>` -- stage timeouts; every timed-out attempt counts.
* `failover:<node>:<from_model>:<error_kind>` -- provider failovers.

A run in several classes counts in each; box-hours are counted per class and are
never added across them (ADR 0026 D12).
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from . import scorecard
from .store import ScorecardRows

KINDS = ("ending", "held", "timeout", "failover")

# Runs that stopped at these nodes are deliberate non-shipping decisions -- the
# issue was already fixed or is no longer worth doing -- not failures. Counting
# them would file an issue for every triage close (ADR 0026 D13).
EXCLUDED_ENDING_NODES = frozenset({"close_noop", "demote_lane"})

# The `held_bucket` answers that are a policy or a bookkeeping state, not a
# failure: architecture waits for the operator by design, `queued` is waiting its
# turn, and the rest mean the reason has not been read yet (C1).
POLICY_HELD_BUCKETS = frozenset(
    {
        "architecture",
        "do-not-merge",
        "auto-merge off",
        "queued",
        "queued, merged by hand",
        "no report",
        "not read yet",
    }
)

DEFAULT_MIN_RUNS = 3
DEFAULT_MIN_BOX_HOURS = 4.0
DEFAULT_CAP = 3

# The key grammar (C1): the kind, then one or more slug parts. The marker regex in
# the filed issue has to read every key this module produces back, so the two
# charsets are the same one.
KEY_PATTERN = re.compile(r"^(ending|held|timeout|failover)(:[a-z0-9_./-]+)+$")

_SLUG_REPLACE = re.compile(r"[^a-z0-9_./-]+")


def slug(text: str) -> str:
    """One key part: lowercase, every run outside the safe set becomes one `-`.

    `"diff hygiene"` -> `diff-hygiene`, `"kimi/kimi-k3"` unchanged. Empty (or
    nothing but unsafe characters) becomes `unknown` rather than the empty string,
    because an empty part would produce a key that reads as a different class and
    would never match its own marker.
    """
    cleaned = _SLUG_REPLACE.sub("-", text.lower()).strip("-")
    return cleaned or "unknown"


@dataclass(frozen=True)
class RunRef:
    """One run in a class, with what the class's evidence needs from it."""

    run_id: str
    ending: str  # scorecard.ending(row)
    lease_ms: int  # scorecard._lease_ms(row)


@dataclass(frozen=True)
class ClassStat:
    """One failure class in one window.

    `runs` are distinct runs, longest lease first; `events` is the class's own unit
    (timeouts and failovers sum, `ending` counts runs, `held` counts PRs), so it can
    differ from `len(runs)` in both directions: one PR can be opened by two runs,
    and one run can time out a stage three times.
    """

    key: str
    kind: str  # "ending" | "held" | "timeout" | "failover"
    runs: tuple[RunRef, ...]
    events: int
    # Raw reasons, `held:other` only, made safe at render time (task 03 / C5).
    samples: tuple[str, ...] = ()

    @property
    def box_hours(self) -> float:
        return sum(run.lease_ms for run in self.runs) / 3_600_000


@dataclass(frozen=True)
class Classified:
    """One window: every class in it, and how many runs could not be classified."""

    classes: dict[str, ClassStat]  # key -> stat
    unsummarized: int


def classify(rows: ScorecardRows) -> Classified:
    """Group one window's rows into every failure class, with no threshold applied.

    A run's summary decides whether it is looked at at all: `ok` and `missing` are
    summarized (`missing` is fabro's 404, never retried, and its terminal node is
    unknowable, so the ending key says `unknown:...`), and a run with no summary row
    or an `error` is counted in `unsummarized` and in no class. A class built from
    rows that say something false about a run would be worse than a quiet week.
    """
    summaries = {row["run_id"]: row for row in rows.summaries}
    history = {row["run_id"]: row for row in rows.history}

    def summarized(run_id: str) -> bool:
        summary = summaries.get(run_id)
        return summary is not None and summary["status"] in ("ok", "missing")

    kind_of: dict[str, str] = {}
    runs_of: dict[str, dict[str, RunRef]] = defaultdict(dict)
    events_of: dict[str, int] = defaultdict(int)
    samples_of: dict[str, list[str]] = defaultdict(list)

    def add(
        key: str,
        kind: str,
        runs: Iterable[RunRef],
        events: int,
        samples: Iterable[str] = (),
    ) -> None:
        """One class's contribution from one row. A key that does not match the
        grammar would never be found by its own marker, so it is a bug, not data."""
        assert KEY_PATTERN.match(key), key
        kind_of[key] = kind
        for run in runs:
            runs_of[key][run.run_id] = run
        events_of[key] += events
        samples_of[key].extend(samples)

    def ref(row: sqlite3.Row) -> RunRef:
        return RunRef(
            run_id=row["run_id"],
            ending=scorecard.ending(row),
            lease_ms=scorecard._lease_ms(row),
        )

    # --- ending -------------------------------------------------------------------
    unsummarized = 0
    for row in rows.history:
        run_id = row["run_id"]
        if not summarized(run_id):
            unsummarized += 1
            continue
        kind = _value(row, "kind")
        reason = _value(row, "reason")
        category = _value(row, "category")
        if kind == "failed" and reason == "cancelled":
            continue  # the operator stopped it; not a factory failure (D13)
        if kind == "succeeded":
            if row["pr_lookup"] != "none":
                continue  # it shipped, or the lookup failed: not an ending class
            detail = "no-pr"
        elif kind in ("failed", "dead", "lost"):
            if reason == "terminated":
                detail = "terminated"  # a fabro restart, not the workflow's fault
            elif category:
                detail = category
            elif kind == "failed":
                detail = reason or "failed"
            else:
                detail = kind  # dead, lost
        else:
            continue  # an ending shape this module does not classify
        summary = summaries[run_id]
        node = summary["terminal_node"] if summary["status"] == "ok" else None
        node = node or "unknown"
        if node in EXCLUDED_ENDING_NODES:
            continue
        add(f"ending:{slug(node)}:{slug(detail)}", "ending", [ref(row)], 1)

    # --- held ---------------------------------------------------------------------
    facts = {(row["repo"], row["pr_number"]): row for row in rows.prs}
    pr_runs: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows.history:
        if row["pr_lookup"] == "found" and row["pr_number"] is not None:
            pr_runs[(row["repo"], row["pr_number"])].append(row)

    for key, opened in pr_runs.items():
        fact = facts.get(key)
        if scorecard.merged_by_workflow(opened, fact):
            continue
        bucket = scorecard.held_bucket(fact)
        if bucket in POLICY_HELD_BUCKETS:
            continue
        counted = [row for row in opened if summarized(row["run_id"])]
        if not counted:
            continue  # nothing knowable about it: not counted, not filed
        class_key = f"held:{slug(bucket)}"
        reasons = []
        if class_key == "held:other":
            reason = fact["reason"] if fact is not None else None
            # raw here; made safe by task 03
            reasons = [reason] if reason else []
        add(class_key, "held", [ref(row) for row in counted], 1, reasons)

    # --- timeouts and failovers ---------------------------------------------------
    for row in rows.stages:
        if not row["timeouts"] or not summarized(row["run_id"]):
            continue
        history_row = history.get(row["run_id"])
        if history_row is None:
            continue
        add(
            f"timeout:{slug(row['node_id'])}",
            "timeout",
            [ref(history_row)],
            int(row["timeouts"]),
        )

    for row in rows.failovers:
        if not summarized(row["run_id"]):
            continue
        history_row = history.get(row["run_id"])
        if history_row is None:
            continue
        add(
            f"failover:{slug(row['node_id'])}:{slug(row['from_model'])}:{slug(row['error_kind'])}",
            "failover",
            [ref(history_row)],
            int(row["count"]),
        )

    classes = {
        key: ClassStat(
            key=key,
            kind=kind_of[key],
            runs=tuple(
                sorted(runs_of[key].values(), key=lambda run: (-run.lease_ms, run.run_id))
            ),
            events=events_of[key],
            samples=tuple(samples_of[key]),
        )
        for key in runs_of
    }
    return Classified(classes=classes, unsummarized=unsummarized)


def over_threshold(stat: ClassStat, *, min_runs: int, min_box_hours: float) -> bool:
    """Whether a class is big enough to write about this week (C2, ADR 0026 D5).

    Either bar is enough: three runs is three runs whatever they cost, and four
    box-hours is half a coder day even if only one run spent it.
    """
    return len(stat.runs) >= min_runs or stat.box_hours >= min_box_hours


def ranked(classes: Iterable[ClassStat]) -> list[ClassStat]:
    """Classes most expensive first, ties by key (C2).

    The order is the write order, so the cap keeps the biggest classes and two runs
    of the same week file the same issues.
    """
    return sorted(classes, key=lambda stat: (-stat.box_hours, stat.key))


def _value(row: sqlite3.Row, column: str) -> str:
    """A `run_history` text column, with fabro's `-` placeholder read as absent."""
    raw = row[column]
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    return "" if text in ("", "-") else text
