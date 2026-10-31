"""The precheck calibration (pre-review-and-calibration, Part A): the scheduler's half.

The live precheck (more-local-inference 07) runs in shadow mode: it posts a verdict and holds
nothing. Its holds (D20) may start only once the verdicts are shown to be right, and live
prechecks answer that slowly — about 17 issue runs a day. The scheduler's DB already labels
every finished run since 2026-09-25 with how it ended, so this module re-checks those issues
with `.fabro/workflows/precheck-calibration/`, which runs the live package's own `prep`,
`check` and `gate` against `main` as it stood when the run was dispatched, and posts nothing.

- **The producer** keeps at most `SCHEDULER_CALIBRATION_QUEUED` jobs queued. It picks finished
  issue runs from `run_history` joined to `run_summary`: a known `terminal_node`, not a launch
  failure or a cancel, since `SCHEDULER_CALIBRATION_SINCE`, newest first, **one per issue** —
  the issue's first finished run, because a later run of the same issue saw a changed issue
  and would measure something else. The job's `target` is the calibrated run id, so the row
  says which run it is about, and its inputs are `issue_number` and `at` (that run's
  `dispatched_at`).
- **The outcome** is read from the calibrated run, never from GitHub: the `record` node's
  `context_updates` in the finished run's event stream. There is no comment to read — the
  package posts none — so this is the one background kind whose outcome is not a marker.
- **The comparison** (`report`) puts each verdict next to how its run ended, as a confusion
  matrix, the outcome mix per verdict, and how many runs the precheck passed (`ready`) that
  ended `stuck`, `returned` or `noop`. That last number, and the precision of each non-`ready`
  verdict, is what D20's holds are waiting for.

**Live and calibration data stay apart.** Both are `background_jobs` rows, but every reader
filters by kind: `run_history.precheck_verdict` is written from `issue-precheck` jobs only
(`store._background_at_dispatch`), so a calibration verdict can never look like the verdict a
coder run was dispatched under, and the pack producer's `latest_background_outcome` asks for
the precheck kind as well.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import BACKGROUND_JOB_PRIORITY, SchedulerConfig
from .store import Store

log = logging.getLogger(__name__)

KIND = "precheck-calibration"

# The whole run, dispatch to finish (C7's `typical_box_minutes`): sandbox start, a clone
# deepened back to `at`, the code index, and one check stage — the live precheck's own shape,
# measured at 0.9-1.8 minutes over its first nine runs. 3 for the same reason: margin over the
# measured run, not a ceiling.
CALIBRATION_TYPICAL_MINUTES = 3

# Terminal nodes that do not describe an ending worth calibrating: the run never got going, or
# was cancelled or lost. `run_summary.status` must be `ok` as well, so a run whose events could
# not be read is left out rather than counted as an ending it did not have.
NOT_AN_ENDING = frozenset({"launch_failed", "cancelled", "lost"})

# The ending classes the comparison uses. The node wins over the PR, except that a merged run
# is `merged`: a run that merged is a run that finished its work, whatever node it passed
# through on the way, and `merged` is the only class that says so from `run_history`.
STUCK_NODES = frozenset({"mark_stuck", "mark_needs_human", "human_rescue"})

# What a non-`ready` verdict expects its run to have ended as, where the two can be compared at
# all. `already_done` is the precheck's own name for `close_noop`, and a `contradiction`, an
# `unclear` or an `unverifiable` criterion is a question for a human, which is what
# `return_to_triage` does. **`blocked` has no expected class**: the issue should not have been
# dispatched at all, so any ending is consistent with holding it, and the report leaves it out
# of `agrees` rather than inventing one.
AGREES: dict[str, frozenset[str]] = {
    "already_done": frozenset({"noop"}),
    "contradiction": frozenset({"returned"}),
    "unclear": frozenset({"returned"}),
    "unverifiable": frozenset({"returned"}),
}

# The classes a `ready` that was wrong can end in, from D20's point of view: work the factory
# did not need to start (`noop`), work it could not do (`stuck`), or work that came back as a
# question (`returned`).
UNPRODUCTIVE = frozenset({"noop", "returned", "stuck"})

OUTCOME = re.compile(
    r"^verdict=(?P<verdict>\S+) raw=(?P<raw>\S+) main=(?P<main>\S+) changed=(?P<changed>\S+)$"
)


@dataclass(frozen=True)
class Recorded:
    """The `record` node's `context_updates`, as the run's events carried them."""

    verdict: str
    raw: str
    rule: str = ""
    main: str = ""
    body_changed: str = ""


def read_record(events: Iterable[Mapping[str, Any]]) -> Recorded | None:
    """The last `record` stage's context updates, or `None` when the run has none.

    `record` is the package's last node, so its `stage.completed` is the last word. Consumed
    once, in order, and never held: the caller hands in `FabroClient.iter_events`, which pages.
    """
    found: Recorded | None = None
    for event in events:
        name = event.get("event") or event.get("type")
        if name != "stage.completed":
            continue
        props = event.get("properties")
        if not isinstance(props, Mapping):
            continue
        if event.get("node_id") != "record":
            continue
        updates = props.get("context_updates")
        if not isinstance(updates, Mapping):
            continue
        verdict = updates.get("verdict")
        if not isinstance(verdict, str) or not verdict:
            continue
        found = Recorded(
            verdict=verdict,
            raw=str(updates.get("raw_verdict") or verdict),
            rule=str(updates.get("rule") or ""),
            main=str(updates.get("main_sha") or ""),
            body_changed=str(updates.get("body_changed") or ""),
        )
    return found


def outcome_for(recorded: Recorded | None) -> str:
    """The job outcome a finished calibration run earned.

    `verdict=<v> raw=<v> main=<sha12> changed=<0|1>`; `succeeded (no verdict)` when the run
    ended without one, which is a run whose `gate` failed and is worth reading. Everything is
    in the outcome on purpose: the report reads this string alone, so it stays true even after
    the run's events are pruned.
    """
    if recorded is None:
        return "succeeded (no verdict)"
    raw = recorded.raw or recorded.verdict
    main = (recorded.main or "unknown")[:12]
    changed = "1" if recorded.body_changed.strip().lower() in ("true", "1", "yes") else "0"
    return f"verdict={recorded.verdict} raw={raw} main={main} changed={changed}"


def read_outcome(events: Iterable[Mapping[str, Any]]) -> str:
    """`outcome_for` of a run's event stream, for the lane's reader hook."""
    return outcome_for(read_record(events))


def parse_outcome(outcome: str | None) -> dict[str, str] | None:
    """The four fields of an outcome string, or `None` for anything else."""
    if not outcome:
        return None
    match = OUTCOME.match(outcome.strip())
    return None if match is None else match.groupdict()


class CalibrationProducer:
    """Keeps a small queue of `precheck-calibration` jobs filled, newest runs first.

    `tick` reads the store only: no GitHub and no fabro call, unlike the precheck's producer,
    because everything it needs was recorded when the runs were archived. With the lane off,
    or `precheck-calibration` absent from `SCHEDULER_BG_KINDS`, it does nothing at all.
    """

    def __init__(self, config: SchedulerConfig, store: Store) -> None:
        self._config = config
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._config.background and KIND in self._config.bg_kinds

    def queued(self) -> int:
        """How many calibration jobs are waiting. Running ones are already at work."""
        return sum(1 for job in self._store.queued_background_jobs() if job["kind"] == KIND)

    def tick(self, now: datetime | None = None) -> list[int]:
        """Queue up to the cap. Returns the new job ids."""
        if not self.enabled:
            return []
        now = now or datetime.now(UTC)
        room = self._config.calibration_queued - self.queued()
        if room <= 0:
            return []
        created: list[int] = []
        for row in self._store.runs_to_calibrate(
            since=self._config.calibration_since, limit=room
        ):
            run_id = str(row["run_id"])
            repo = str(row["repo"])
            # The target is the run, so this both says which run the row is about and makes
            # the producer idempotent: one run never gets two jobs.
            if self._store.background_job_exists(KIND, repo, run_id):
                continue
            job_id = self._store.enqueue_background_job(
                kind=KIND,
                repo=repo,
                target=run_id,
                inputs={"issue_number": int(row["issue_number"]), "at": str(row["dispatched_at"])},
                priority=BACKGROUND_JOB_PRIORITY[KIND],
                typical_box_minutes=CALIBRATION_TYPICAL_MINUTES,
                now=now,
            )
            if job_id is not None:
                created.append(job_id)
                log.info(
                    "calibration: queued job %s for %s#%s, run %s at %s",
                    job_id,
                    repo,
                    row["issue_number"],
                    run_id,
                    row["dispatched_at"],
                )
        return created


# --- the comparison ---------------------------------------------------------------


@dataclass
class VerdictStats:
    """One verdict's rows in the comparison."""

    verdict: str
    runs: int = 0
    agrees: int = 0
    comparable: int = 0
    by_class: Counter[str] = field(default_factory=Counter)
    refuter_unverifiable: int = 0

    def as_json(self) -> dict[str, object]:
        row: dict[str, object] = {
            "verdict": self.verdict,
            "runs": self.runs,
            "by_class": dict(sorted(self.by_class.items())),
        }
        if self.comparable:
            row["agrees"] = self.agrees
            row["comparable"] = self.comparable
        if self.verdict == "unverifiable":
            row["refuter_unverifiable"] = self.refuter_unverifiable
        return row


def outcome_class(*, merged: object, terminal_node: object, pr_number: object) -> str:
    """The ending class of one calibrated run (the plan's table, in its priority order)."""
    if merged == 1:
        return "merged"
    node = str(terminal_node or "")
    if node == "close_noop":
        return "noop"
    if node == "return_to_triage":
        return "returned"
    if node in STUCK_NODES:
        return "stuck"
    if node == "demote_lane":
        return "demoted"
    if pr_number is not None:
        return "held"
    return "other"


def _refuter_unverifiable(refute: object) -> bool:
    """Whether any Refuter pass called its only unmet criteria unverifiable.

    `run_summary.refute` is a JSON array of `{verdict, not_met, unverifiable_only, ci_credited}`
    in order (`summary.RefuteVerdict`). `unverifiable_only` is the field that says a pass found
    nothing it could check, so it is what an `unverifiable` precheck verdict is compared with.
    """
    if not isinstance(refute, str) or not refute:
        return False
    try:
        passes = json.loads(refute)
    except ValueError:
        return False
    if not isinstance(passes, list):
        return False
    return any(isinstance(item, Mapping) and item.get("unverifiable_only") is True
               for item in passes)


def report(rows: Iterable[Mapping[str, Any]]) -> dict[str, object]:
    """The verdict-against-outcome document, from `Store.calibration_rows`.

    One row per calibration job. A job that ended without a verdict is listed with
    `verdict: null` and left out of the matrix and the per-verdict counts: a run whose gate
    failed is a run to read, not a data point.
    """
    calibrated: list[dict[str, object]] = []
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    stats: dict[str, VerdictStats] = {}
    ready_unproductive = 0
    with_verdict = 0
    for row in rows:
        fields = parse_outcome(None if row["outcome"] is None else str(row["outcome"]))
        klass = outcome_class(
            merged=row["merged"],
            terminal_node=row["terminal_node"],
            pr_number=row["pr_number"],
        )
        verdict = None if fields is None else fields["verdict"]
        entry: dict[str, object] = {
            "job_id": int(row["job_id"]),
            "repo": row["repo"],
            "issue_number": row["issue_number"],
            "run_id": row["run_id"],
            "state": row["state"],
            "finished_at": row["last_finished_at"],
            "verdict": verdict,
            "raw_verdict": None if fields is None else fields["raw"],
            "main_sha": None if fields is None else fields["main"],
            "body_changed": None if fields is None else fields["changed"] == "1",
            "outcome_class": klass,
            "terminal_node": row["terminal_node"],
            "pr_number": row["pr_number"],
            "pr_reason": row["pr_reason"],
        }
        calibrated.append(entry)
        if verdict is None:
            continue
        with_verdict += 1
        matrix[verdict][klass] += 1
        stat = stats.get(verdict)
        if stat is None:
            stat = stats[verdict] = VerdictStats(verdict)
        stat.runs += 1
        stat.by_class[klass] += 1
        expected = AGREES.get(verdict)
        if expected:
            stat.comparable += 1
            if klass in expected:
                stat.agrees += 1
        if verdict == "unverifiable" and _refuter_unverifiable(row["refute"]):
            stat.refuter_unverifiable += 1
        if verdict == "ready" and klass in UNPRODUCTIVE:
            ready_unproductive += 1
    return {
        "rows": calibrated,
        "matrix": {verdict: dict(sorted(classes.items()))
                   for verdict, classes in sorted(matrix.items())},
        "verdicts": [stats[key].as_json() for key in sorted(stats)],
        "ready_unproductive": ready_unproductive,
        "counts": {"jobs": len(calibrated), "with_verdict": with_verdict},
    }
