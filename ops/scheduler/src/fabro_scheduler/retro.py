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
* `failover:<from_model>:<error_kind>` -- provider failovers, every stage together:
  one model refusing is one cause wherever it was called (the stages are kept on
  the class as `stages`, not in the key).

A run in several classes counts in each; box-hours are counted per class and are
never added across them (ADR 0026 D12).
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from . import scorecard
from .github import TrackedIssue
from .scorecard import Scorecard
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

# The retro files issues only in this repository (ADR 0026 D4): a PR here would
# reach `main`, and a push to `main` deploys. The label marks the retro's issues;
# the marker in the body is what survives a title edit (C3).
RETRO_REPO = "andrewthetechie/fabro-workflows"
RETRO_LABEL = "retro"
ISSUE_LABELS = ("retro", "bug")

MARKER = re.compile(r"<!-- fabro:retro-class ([a-z0-9_.:/-]+) -->")

# A cross-repository reference is a word run immediately before `#` and digits:
# `wfs#2080`, `repo#12`. A bare `#41` (preceded by a space, `(`, a backtick, or
# the start of a line) is this repository's issue number and is allowed.
_CROSS_REF = re.compile(r"[A-Za-z0-9_.-]+#\d+")
# The `#<digits>` a sample rewrites: only one that `_CROSS_REF` would not match.
_BARE_REF = re.compile(r"(?<![A-Za-z0-9_.-])#(\d+)")
# `github.com/<owner>/<repo>` -- blocked unless the repository is this one.
_GITHUB_URL = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)

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
    # `held:other` only: each PR's reason, already through `safe_sample` (C5).
    samples: tuple[str, ...] = ()
    # `failover` only: `(node_id, events)` per stage, most events first.
    stages: tuple[tuple[str, int], ...] = ()

    @property
    def box_hours(self) -> float:
        return sum(run.lease_ms for run in self.runs) / 3_600_000

    @property
    def cost_hours(self) -> float:
        """The box-hours this class plausibly cost: what the threshold and rank use.

        For `ending` and `held` the whole run is the loss (it shipped nothing, or a
        PR nobody merged), so it is `box_hours`. A stage that timed out or failed
        over often still ends in a PR, so for those it is only the runs that ended
        without one: a 15-hour run that timed out once and opened a PR cost little.
        """
        if self.kind in ("ending", "held"):
            return self.box_hours
        return sum(run.lease_ms for run in self.runs if run.ending != "PR opened") / 3_600_000


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
    stages_of: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

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
            reasons = [safe_sample(reason)] if reason else []
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
        key = f"failover:{slug(row['from_model'])}:{slug(row['error_kind'])}"
        add(key, "failover", [ref(history_row)], int(row["count"]))
        stages_of[key][row["node_id"]] += int(row["count"])

    classes = {
        key: ClassStat(
            key=key,
            kind=kind_of[key],
            runs=tuple(
                sorted(runs_of[key].values(), key=lambda run: (-run.lease_ms, run.run_id))
            ),
            events=events_of[key],
            samples=_samples(samples_of[key]),
            stages=tuple(
                sorted(stages_of[key].items(), key=lambda item: (-item[1], item[0]))
            ) if key in stages_of else (),
        )
        for key in runs_of
    }
    return Classified(classes=classes, unsummarized=unsummarized)


def over_threshold(stat: ClassStat, *, min_runs: int, min_box_hours: float) -> bool:
    """Whether a class is big enough to write about this week (C2, ADR 0026 D5).

    Either bar is enough: three runs is three runs whatever they cost, and four
    box-hours is half a coder day even if only one run spent it. The hours are
    `cost_hours`, so one long run that timed out once and still shipped is not a
    class.
    """
    return len(stat.runs) >= min_runs or stat.cost_hours >= min_box_hours


def ranked(classes: Iterable[ClassStat]) -> list[ClassStat]:
    """Classes most expensive (`cost_hours`) first, ties by key (C2).

    The order is the write order, so the cap keeps the biggest classes and two runs
    of the same week file the same issues.
    """
    return sorted(classes, key=lambda stat: (-stat.cost_hours, stat.key))


def _value(row: sqlite3.Row, column: str) -> str:
    """A `run_history` text column, with fabro's `-` placeholder read as absent."""
    raw = row[column]
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    return "" if text in ("", "-") else text


def known_classes(issues: Iterable[TrackedIssue]) -> dict[str, list[TrackedIssue]]:
    """Which failure classes GitHub already knows, per key, highest number first.

    An issue carries its classes as hidden `<!-- fabro:retro-class KEY -->` markers,
    and it may carry more than one (a comment on a known class does not restate
    them; a merged issue keeps both). GitHub is the only store (ADR 0026 D3), so
    this is the whole of the retro's memory: nothing is written to the database.

    Highest number first because the caller wants the *newest* decision on a class,
    and a later issue supersedes an earlier one about the same key.
    """
    found: dict[str, list[TrackedIssue]] = defaultdict(list)
    for issue in issues:
        for key in MARKER.findall(issue.body):
            found[key].append(issue)
    for entries in found.values():
        entries.sort(key=lambda issue: -issue.number)
    return dict(found)


def guard(text: str, repos: Sequence[str], aliases: Sequence[str] = ()) -> str | None:
    """What in `text` names a target repository, or `None` (C5, ADR 0026 D4).

    The repository is public and the issue body is written by the operator's own
    token, so a target repository's name -- or a link that points at it -- would
    publish which repositories the factory works on. Nothing is redacted: a
    redaction that misses once has already published the name, so the whole write
    is withheld and reported instead.

    `aliases` are the repositories' short names from `repos.toml` (`wfs`), matched
    as whole words like a bare name: an agent's prose uses them as often as the
    real name, and nothing else would catch `the wfs build` without a `#`.

    The description itself is safe: it goes to Discord (already private) and never
    to GitHub. Case is ignored throughout, because a link and a path differ in case
    often enough to matter.
    """
    for repo in repos:
        if repo and re.search(re.escape(repo), text, re.IGNORECASE):
            return f"repository name '{repo}'"
    for match in _GITHUB_URL.finditer(text):
        if match.group(2).lower() != "fabro-workflows":
            return f"GitHub URL to another repository '{match.group(0)}'"
    for repo in repos:
        name = repo.split("/", 1)[-1]
        if name and re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return f"bare repository name '{name}'"
    for alias in aliases:
        if alias and re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
            return f"repository alias '{alias}'"
    match = _CROSS_REF.search(text)
    if match:
        return f"cross-repository reference '{match.group(0)}'"
    return None


def safe_sample(reason: str) -> str:
    """One PR's reason, made safe to quote in a body (C5).

    The reason is agent prose about a **target** repository. Three things are done
    to it, in this order, and each one is a way the text could leak or misfire:

    1. whitespace collapsed, and cut to at most 160 characters with `…` -- a reason
       is often several paragraphs. The cut falls on a space, so it never halves a
       repository name into a prefix `guard` would not recognise;
    2. a bare `#<digits>` becomes `issue <digits>`, because GitHub would otherwise
       render it as a link to *this* repository's issue of that number. A
       cross-repository `wfs#2080` is left whole: rewritten to `wfsissue 2080` it
       would slip past `guard`'s cross-reference rule and publish the short name;
    3. `@` becomes `@` (a zero-width space), so nobody is mentioned by accident.

    A target repository's own name is not touched here; the finished body goes
    through `guard`, which withholds the whole write when it finds one.
    """
    text = " ".join(reason.split())
    if len(text) > 160:
        cut = text.rfind(" ", 0, 161)
        text = text[: cut if cut > 0 else 160] + "…"
    text = _BARE_REF.sub(r"issue \1", text)
    return text.replace("@", "@\u200b")


def issue_title(stat: ClassStat, week_start: date, regression_of: int | None = None) -> str:
    """The issue title (C7). A regression names the issue it supersedes."""
    prefix = f"retro (regression of #{regression_of})" if regression_of is not None else "retro"
    return (
        f"{prefix}: {stat.key}: {len(stat.runs)} runs, {stat.box_hours:.1f} box-hours"
        f" (week of {week_start.isoformat()})"
    )


def issue_body(
    stat: ClassStat,
    prev_stat: ClassStat | None,
    week_start: date,
    regression: TrackedIssue | None = None,
) -> str:
    """The issue body (C7).

    The marker is the first line, so `MARKER` finds the class again next week.
    `regression` is the closed issue this one replaces; its date is the operator's
    act of closing it, and `an unknown date` when GitHub did not say.
    """
    sunday = week_start + timedelta(days=6)
    first_run = stat.runs[0].run_id if stat.runs else "RUN_ID"
    lines = [
        f"<!-- fabro:retro-class {stat.key} -->",
        f"**Failure class:** `{stat.key}`",
        "",
        _sentence(stat),
    ]
    outcome = _outcome(stat)
    if outcome:
        lines.append(outcome)
    if regression is not None:
        closed = _closed_date(regression)
        lines.append(
            f"Regression of #{regression.number}, closed as completed on {closed}."
        )
    lines += [
        "",
        _table(stat, prev_stat, week_start),
        "",
        "Box-hours are each run's whole lease. A run in several classes counts in each.",
        "",
        *_runs_list(stat),
    ]
    if stat.kind == "held" and stat.key == "held:other" and stat.samples:
        lines += ["", "**Sample reasons:**"]
        lines += [f"> {sample}" for sample in stat.samples]
    lines += [
        "",
        "**Look further**",
        f"- `ops/fabro-scorecard.sh --from {week_start.isoformat()} --to {sunday.isoformat()}`",
        f"- On the host: `docker compose exec -T fabro fabro events {first_run} -p`",
        "",
        "Filed by the weekly retro (ADR 0026). Close as completed when fixed: a recurrence files a",
        "regression. Close as not planned to silence this class.",
    ]
    return "\n".join(lines)


def comment_body(stat: ClassStat, prev_stat: ClassStat | None, week_start: date) -> str:
    """A comment on a known class's open issue (C7): this week's table and runs."""
    return "\n".join(
        [
            f"**Week of {week_start.isoformat()}**",
            "",
            _table(stat, prev_stat, week_start),
            "",
            "Box-hours are each run's whole lease. A run in several classes counts in each.",
            "",
            *_runs_list(stat),
        ]
    )


@dataclass(frozen=True)
class Action:
    """One thing the retro decided to do about one class (C4).

    `kind` is `file`, `regression`, `comment`, `suppressed`, `recent`, `capped` or
    `withheld`. Only the first three carry a body and reach GitHub. `recent` is a
    class whose issue was closed as completed during the reported week (`issue` is
    that issue). `title` and `body` are kept
    on a withheld action **for the stderr log only** -- the operator has to be able
    to see what was suppressed without it reaching a public issue.
    """

    kind: str
    key: str
    stat: ClassStat
    issue: int | None  # comment: the open issue; regression, suppressed, recent: the closed one
    title: str | None  # file and regression only
    body: str | None  # file, regression, comment; None for the rest
    why: str = ""  # withheld: what `guard` matched


def plan(
    current: Classified,
    previous: Classified,
    known: dict[str, list[TrackedIssue]],
    *,
    week_start: date,
    repos: Sequence[str],
    min_runs: int,
    min_box_hours: float,
    cap: int,
    aliases: Sequence[str] = (),
) -> list[Action]:
    """Decide every write for one week, in rank order (C4).

    GitHub's own state decides: an open issue for a key takes a comment, a closed
    one takes a regression or, when it was closed as not planned or duplicate, is
    left alone as the operator's rejection. A completed issue closed inside the
    reported week is left alone too: that week's runs predate the fix. At most `cap` new issues are planned;
    a comment is never capped, and a write `guard` withholds does not use a slot --
    otherwise a withheld class would silently cost the week one of its three.
    """
    actions: list[Action] = []
    filed = 0
    eligible = [
        stat
        for stat in current.classes.values()
        if over_threshold(stat, min_runs=min_runs, min_box_hours=min_box_hours)
    ]
    for stat in ranked(eligible):
        prev_stat = previous.classes.get(stat.key)
        issues = known.get(stat.key, [])
        open_issues = [issue for issue in issues if issue.state == "open"]
        action: Action
        if open_issues:
            issue = open_issues[0]
            action = Action(
                kind="comment",
                key=stat.key,
                stat=stat,
                issue=issue.number,
                title=None,
                body=comment_body(stat, prev_stat, week_start),
            )
        else:
            closed = [issue for issue in issues if issue.state == "closed"]
            if closed:
                issue = closed[0]
                if issue.state_reason in ("not_planned", "duplicate"):
                    action = Action("suppressed", stat.key, stat, issue.number, None, None)
                elif _closed_on_or_after(issue, week_start):
                    # Closed as fixed inside the week being reported: the week still
                    # holds the runs from before the fix, so they are no regression.
                    # Next week's window starts after the close and decides.
                    action = Action("recent", stat.key, stat, issue.number, None, None)
                elif filed >= cap:
                    action = Action("capped", stat.key, stat, None, None, None)
                else:
                    action = Action(
                        kind="regression",
                        key=stat.key,
                        stat=stat,
                        issue=issue.number,
                        title=issue_title(stat, week_start, regression_of=issue.number),
                        body=issue_body(stat, prev_stat, week_start, regression=issue),
                    )
            elif filed >= cap:
                action = Action("capped", stat.key, stat, None, None, None)
            else:
                action = Action(
                    kind="file",
                    key=stat.key,
                    stat=stat,
                    issue=None,
                    title=issue_title(stat, week_start),
                    body=issue_body(stat, prev_stat, week_start),
                )

        if action.body is not None:
            # C5: every written text is checked. Title first, then body, so the
            # report names the more visible leak when both match.
            why = next(
                (match for text in (action.title, action.body) if text
                 and (match := guard(text, repos, aliases))),
                None,
            )
            if why is not None:
                action = Action(
                    kind="withheld",
                    key=stat.key,
                    stat=stat,
                    issue=action.issue,
                    title=action.title,
                    body=action.body,
                    why=why,
                )
            elif action.kind in ("file", "regression"):
                filed += 1
        actions.append(action)
    return actions


# What each held bucket means, for the Discord list (`scorecard.REASON_BUCKETS`,
# slugged). A bucket missing here is shown by its name.
_HELD_BECAUSE = {
    "refuter": "the Refuter said the work is not done",
    "diff-hygiene": "the diff-hygiene gate found test tampering or erosion",
    "risk": "the risk rating was too high",
    "authorship": "they carry commits the automation did not author",
    "unfixed-findings": "review findings were left unfixed",
    "review-threads": "changes were requested or review threads left open",
    "branch-moved": "the branch or its base moved",
    "ci": "of CI checks",
    "timeout": "the merge phase timed out",
    "unspecified": "the merge phase gave no reason",
    "other": "of a reason that fits no bucket",
    "no-reason-recorded": "no reason was recorded",
}

# An `ending` key's detail, in words (C1's detail values).
_ENDED = {
    "no-pr": "stopped without a PR",
    "terminated": "cut off by a fabro restart",
    "dead": "died",
    "lost": "lost (fabro has no record)",
}

DISCORD_LIMIT = 2000


def discord_text(
    card: Scorecard,
    prev_card: Scorecard,
    actions: Sequence[Action],
    *,
    week_start: date,
    dry_run: bool,
    still_leased: int,
    unsummarized: int,
    previous: Classified | None = None,
    failure: str | None = None,
    done: set[int] | None = None,
    issued: dict[int, int] | None = None,
    cap: int = DEFAULT_CAP,
    repo: str = RETRO_REPO,
) -> str:
    """The week's one Discord message (C6, ADR 0026 D8).

    Discord markdown, at most 2000 characters: the scorecard line, then one numbered
    line per class over the threshold, in rank order, each saying what went wrong in
    words, how big it was against last week, and what the retro did about it.
    `previous` gives the last-week numbers. `done` is the indexes actually written
    and `issued` maps an action index to the number GitHub returned; both exist for
    the partial-failure report. On a failure the first line is `retro failed: ...`.
    When the lines do not fit, the ones that wrote nothing go first, from the end.
    """
    issued = issued or {}
    sunday = week_start + timedelta(days=6)
    head: list[str] = []
    if failure:
        head.append(f"retro failed: {failure}")
    mode = " · **DRY RUN**: nothing was filed or commented" if dry_run else ""
    head += [
        f"**Factory retro, {week_start.isoformat()} to {sunday.isoformat()}**{mode}",
        "Week over week: " + _card_line(card, prev_card),
        "",
        f"**{len(actions)} failure classes over the threshold**, costliest first"
        f" (at most {cap} new issues a week):"
        if actions
        else "Nothing was planned: the retro stopped before it could."
        if failure
        else "No failure class was over the threshold.",
    ]

    lines: list[tuple[str, bool]] = []
    for index, action in enumerate(actions):
        prev_stat = previous.classes.get(action.key) if previous is not None else None
        result = _result(
            action, index, dry_run=dry_run, done=done, issued=issued, cap=cap, repo=repo
        )
        lines.append(
            (
                f"{index + 1}. {_what(action.stat, prev_stat)} → {result}",
                action.kind in ("file", "regression", "comment"),
            )
        )

    counted = []
    if still_leased:
        counted.append(f"{still_leased} runs still leased")
    if unsummarized:
        counted.append(f"{unsummarized} not yet summarized")
    foot = [
        "",
        "Backlog runs only"
        + (f"; not counted: {', '.join(counted)}" if counted else "")
        + ". Box-h is the lease of the runs a line counts, and one run can be in"
        " several lines: never add them.",
    ]

    def assemble(kept: list[str], dropped: int) -> str:
        more = [f"…and {dropped} more, all in the cron log"] if dropped else []
        return "\n".join(head + kept + more + foot)

    kept = [text for text, _ in lines]
    text = assemble(kept, 0)
    # Shorten: the lines that wrote nothing go first, from the end; then the rest.
    order = [i for i, (_, wrote) in reversed(list(enumerate(lines))) if not wrote]
    order += [i for i, (_, wrote) in reversed(list(enumerate(lines))) if wrote]
    gone: set[int] = set()
    for index in order:
        if len(text) <= DISCORD_LIMIT:
            break
        gone.add(index)
        kept = [line for i, (line, _) in enumerate(lines) if i not in gone]
        text = assemble(kept, len(gone))
    if len(text) > DISCORD_LIMIT:
        # Only the fixed lines are left. Cut rather than fail: the writes have been
        # made, and this message is their only report.
        text = text[: DISCORD_LIMIT - 1] + "…"
    return text


def _what(stat: ClassStat, prev: ClassStat | None) -> str:
    """One class in words, with its cost and last week's size (the Discord list)."""
    parts = stat.key.split(":")
    runs = len(stat.runs)
    before_runs = len(prev.runs) if prev else 0
    if stat.kind == "ending":
        node, detail = parts[1], parts[2]
        how = _ENDED.get(detail, f"failed ({detail})")
        return (
            f"Runs {how} at `{node}`: {_n(runs, 'run')}, {stat.cost_hours:.1f} box-h;"
            f" last week {before_runs}"
        )
    if stat.kind == "held":
        because = _HELD_BECAUSE.get(parts[1], f"of `{parts[1]}`")
        before = prev.events if prev else 0
        return (
            f"PRs held because {because}: {_n(stat.events, 'PR')}, {stat.cost_hours:.1f}"
            f" box-h; last week {before}"
        )
    lost = f"{_no_pr(stat)} got no PR ({stat.cost_hours:.1f} box-h)"
    if stat.kind == "timeout":
        return (
            f"Stage `{parts[1]}` timed out {stat.events}× in {_n(runs, 'run')}, {lost};"
            f" last week {before_runs}"
        )
    stages = ", ".join(f"`{node}`" for node, _ in stat.stages[:3])
    if len(stat.stages) > 3:
        stages += f" +{len(stat.stages) - 3}"
    return (
        f"`{parts[1]}` failing over on {parts[2]}: {stat.events}× in {_n(runs, 'run')}"
        + (f" at {stages}" if stages else "")
        + f", {lost}; last week {before_runs}"
    )


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _result(
    action: Action,
    index: int,
    *,
    dry_run: bool,
    done: set[int] | None,
    issued: dict[int, int],
    cap: int,
    repo: str,
) -> str:
    """What the retro did, or would do, about one class."""

    def link(number: int | None) -> str:
        # `<url>` keeps Discord from unfurling a preview for every issue.
        return f"[#{number}](<https://github.com/{repo}/issues/{number}>)"

    kind = action.kind
    if kind in ("file", "regression", "comment") and not dry_run:
        if done is not None and index not in done:
            return "not written: the run stopped first"
    if kind == "file":
        return "**would file**" if dry_run else f"**filed {link(issued.get(index))}**"
    if kind == "regression":
        of = f"a regression of {link(action.issue)}"
        if dry_run:
            return f"**would file** {of}"
        return f"**filed {link(issued.get(index))}**, {of}"
    if kind == "comment":
        return f"{'would comment' if dry_run else 'commented'} on {link(action.issue)}"
    if kind == "capped":
        return "over the cap"
    if kind == "suppressed":
        return f"silenced: {link(action.issue)} was closed as not planned"
    if kind == "recent":
        return f"not re-filed: {link(action.issue)} was closed as fixed this week"
    if kind == "withheld":
        return "withheld: its text named a target repository (see the cron log)"
    return kind


def _samples(samples: Iterable[str]) -> tuple[str, ...]:
    """Distinct samples, at most three, in PR order (C5 step 4)."""
    seen: list[str] = []
    for sample in samples:
        if sample not in seen:
            seen.append(sample)
        if len(seen) == 3:
            break
    return tuple(seen)


def _sentence(stat: ClassStat) -> str:
    """The one-sentence description of a class, from its key's parts (C7)."""
    parts = stat.key.split(":")
    kind = parts[0]
    if kind == "ending":
        node, detail = parts[1], parts[2]
        if detail == "no-pr":
            return f"Runs that finished at `{node}` without opening a PR."
        return f"Runs that ended at `{node}` as `{detail}`."
    if kind == "held":
        return (
            f"PRs the workflow did not merge, held for the reason bucket `{parts[1]}`"
            " (`scorecard.REASON_BUCKETS`)."
        )
    if kind == "timeout":
        return (
            f"Stage `{parts[1]}` timed out. Every timed-out attempt counts, retried"
            " ones included."
        )
    where = ", ".join(f"`{node}` ({count})" for node, count in stat.stages)
    return (
        f"Calls to `{parts[1]}` failed with `{parts[2]}` and fell back to another model"
        + (f", at these stages (failovers each): {where}." if where else ".")
    )


def _no_pr(stat: ClassStat) -> int:
    """How many of the class's runs ended without opening a PR."""
    return sum(1 for run in stat.runs if run.ending != "PR opened")


def _outcome(stat: ClassStat) -> str:
    """For a timeout or failover class, what became of its runs.

    A stage can time out or fail over and the run still ship, so the class's
    box-hours say how big the runs were, not what the failure cost. This line is
    the part that says whether it cost the run.
    """
    if stat.kind not in ("timeout", "failover"):
        return ""
    return (
        f"{_no_pr(stat)} of these {len(stat.runs)} runs ended without a PR"
        f" ({stat.cost_hours:.1f} box-hours); the rest opened one. The retro ranks this"
        " class by those runs' hours."
    )


def _table(stat: ClassStat, prev_stat: ClassStat | None, week_start: date) -> str:
    """The two-column evidence table every body and comment carries (C7)."""
    runs0 = len(prev_stat.runs) if prev_stat else 0
    events0 = prev_stat.events if prev_stat else 0
    hours0 = prev_stat.box_hours if prev_stat else 0.0
    return "\n".join(
        [
            f"| | Week of {week_start.isoformat()} | Previous week |",
            "|---|---|---|",
            f"| Runs | {len(stat.runs)} | {runs0} |",
            f"| Events | {stat.events} | {events0} |",
            f"| Box-hours | {stat.box_hours:.1f} | {hours0:.1f} |",
        ]
    )


def _runs_list(stat: ClassStat) -> list[str]:
    """The evidence list: at most the ten longest-leased runs."""
    lines = ["**Runs** (longest lease first, at most 10):"]
    for run in stat.runs[:10]:
        lines.append(f"- `{run.run_id}`: {run.ending}, {run.lease_ms / 3_600_000:.1f} h")
    return lines


def _closed_on(issue: TrackedIssue) -> date | None:
    """A closed issue's UTC date, or `None` when GitHub did not give a usable one."""
    if not issue.closed_at:
        return None
    try:
        return date.fromisoformat(issue.closed_at[:10])
    except ValueError:
        return None


def _closed_date(issue: TrackedIssue) -> str:
    """A closed issue's date, or the string that says GitHub did not give one."""
    closed = _closed_on(issue)
    return closed.isoformat() if closed else "an unknown date"


def _closed_on_or_after(issue: TrackedIssue, day: date) -> bool:
    """Whether the issue was closed on `day` or later. An unknown date is not."""
    closed = _closed_on(issue)
    return closed is not None and closed >= day


def _card_line(card: Scorecard, prev: Scorecard) -> str:
    """The week-over-week numbers (C6): counts, merge rate in pp, box-h per merge."""
    rate = "n/a" if card.merge_rate is None else f"{card.merge_rate * 100:.0f}%"
    per_merge = (
        "n/a" if card.box_hours_per_merge is None else f"{card.box_hours_per_merge:.1f}"
    )
    return " · ".join(
        [
            f"Runs {card.runs} ({_signed(card.runs - prev.runs)})",
            f"PRs {card.prs} ({_signed(card.prs - prev.prs)})",
            f"merged by the workflow {card.merged_workflow} = {rate} ({_pp(card.merge_rate, prev.merge_rate)})",
            f"box-hours per merge {per_merge} ({_delta(per_merge, card, prev)})",
        ]
    )


def _delta(formatted: str, card: Scorecard, prev: Scorecard) -> str:
    if formatted == "n/a" or prev.box_hours_per_merge is None:
        return "n/a"
    assert card.box_hours_per_merge is not None
    return f"{card.box_hours_per_merge - prev.box_hours_per_merge:+.1f}"




def _signed(value: int) -> str:
    return f"{value:+d}"


def _pp(current: float | None, previous: float | None) -> str:
    """A merge-rate change in percentage points, or `n/a` (C6)."""
    if current is None or previous is None:
        return "n/a"
    return f"{(current - previous) * 100:+.0f}pp"
