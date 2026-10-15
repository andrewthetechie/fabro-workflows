# Factory retro: overview and canonical contracts

**Status:** Built 2026-10-15 (tasks 01-06, local commits); task 07, the deploy and the one-month check, is the operator's. Issue #33, ADR 0026 (proposed).

**Read this file first.** Every task in this folder assumes the decisions, facts and
contracts below. If a task and this file disagree, this file is correct: stop and report
the conflict. Read `docs/scorecard/README.md` (what the scorecard counts) and ADR 0026
before you start. `CONTEXT.md` defines **Retro** and **Failure class**.

## Hard rule for this series: do not push

The operator reviews before anything reaches `main` (pushing to `main` deploys; see
`AGENTS.md`). Commit each task locally on `main`, one commit per task, Conventional Commit
subjects (`feat(scheduler): ...`, `feat(ops): ...`, `docs(factory-retro): ...`). After each
task, run `uv run pytest` in `ops/scheduler/` and `make check` at the repository root. Both
must pass. **Stop after task 06** and report. Do not run `git push`, `make deploy*`,
`make compose-up`, `make check-host`, `ssh`, `crontab`, `docker`, or any `gh` command that
writes. Task 07 is the operator's.

## What this is

Once a week, the **Retro** reads the previous ISO week's `backlog` runs from the scheduler's
database. It does four things:

1. It computes the week's scorecard and the previous week's, with `scorecard.compute`.
2. It groups the week's runs into **Failure classes** (contract C1).
3. For each class over the threshold (C2), it files an issue in **this** repository, or
   comments on the existing issue for that class (C3, C4).
4. It prints one Discord message (C6). A cron wrapper on the host posts it (C8).

It writes only issues and comments, never PRs: a push to this repository's `main` deploys.
It never names a target repository in anything it writes (C5).

The code lives in the coder scheduler, because the scheduler already holds the data, the
scorecard computation and a GitHub client:

| Piece | Path | Task |
|---|---|---|
| Two GitHub calls | `ops/scheduler/src/fabro_scheduler/github.py` | 01 |
| One shared helper, extracted | `ops/scheduler/src/fabro_scheduler/scorecard.py` | 02 |
| Classes, thresholds, the plan, the text, the guard | `ops/scheduler/src/fabro_scheduler/retro.py` (new, pure) | 02, 03 |
| The `retro` subcommand | `ops/scheduler/src/fabro_scheduler/app.py` | 04 |
| The cron wrapper | `ops/fabro-retro.sh` (new), `ops/tests/test_fabro_retro.py` (new) | 05 |
| Deploy list, runbook, docs | `ops/deploy-host.sh`, `ops/README.md`, `docs/scorecard/README.md`, `docs/README.md` | 05, 06 |

## Decisions (settled; do not re-open)

| # | Decision |
|---|---|
| D1 | The logic is a pure module in the scheduler (`retro.py`), run as `python -m fabro_scheduler retro`, like `resummarize`. A host cron wrapper runs it with `docker exec` and posts to Discord. It is not a Fabro workflow and not a thread in the scheduler process. |
| D2 | A **Failure class** has one of four kinds: `ending`, `held`, `timeout`, `failover` (C1). Its key is built only from enumerable values, never from free text. |
| D3 | GitHub is the only store of known classes. Each retro issue has the label `retro` and a hidden marker `<!-- fabro:retro-class KEY -->`. No database table is added. |
| D4 | A known class that recurs: open issue → comment; closed as completed → a new regression issue that links it; closed as not planned (or duplicate) → no write, counted as suppressed (C4). |
| D5 | A class is over the threshold when it touches ≥ 3 runs or costs ≥ 4 box-hours in the week. At most 3 new issues (new plus regression) per week, ranked by box-hours. Comments are not capped. Each threshold is a CLI flag with these defaults. |
| D6 | The first fire is a dry run. A dry run reads GitHub and writes nothing; its Discord message starts with `DRY RUN`. The operator goes live by adding `DRY_RUN=0` to the cron line. |
| D7 | Cron runs Monday 12:00 UTC (the host clock is UTC) over the previous ISO week, compared with the week before. Runs still leased, or not yet summarized, are reported and not waited for. |
| D8 | One Discord message per week, at most 2000 characters. Never one per run (ADR 0004). |
| D9 | Before every write, the text is checked for target repository names (C5). A match withholds that one write. Nothing is redacted. |
| D10 | Retro issues get the labels `retro` and `bug`. The operator accepts an issue by leaving it open or closing it as completed, and rejects it by closing it as not planned. Nothing measures acceptance automatically. |
| D11 | Only scheduler-dispatched `backlog` runs are covered (the scorecard's scope). The gap is stated in the docs and in every Discord message. |
| D12 | Each class counts the full lease time of every run it touches. A run in several classes counts in each. Box-hours are never added across classes. |
| D13 | `ending` leaves out runs that ended at `close_noop` or `demote_lane`, and runs the operator cancelled. |
| D14 | If the subcommand fails, the wrapper posts one line to Discord: `retro failed: ...`. Writes are made in rank order. A partial failure leaves the earlier writes in place, and next week's run fingerprints them. |

## Facts (verified 2026-10-15)

- `Store.scorecard_rows(start, end)` returns `ScorecardRows(history, summaries, stages,
  failovers, prs, models)` for runs **dispatched** in `[start, end)`. Use it unchanged.
- `run_history` values seen live (`kind`, `reason`, `category`, `pr_lookup`):
  `succeeded/completed/-/found|none`, `failed/workflow_error/deterministic/found|none`,
  `failed/cancelled/-/...`, `failed/terminated/-/...`, `lost/fabro 404/-/none`. `kind` can
  also be `dead`.
- `run_summary.status` is `ok`, `missing` (fabro answered 404; never retried) or `error`.
  `terminal_node` is meaningful only when `status = 'ok'`. Seen live: `review_merge.done`,
  `mark_stuck`, `close_noop`, `improve`, `open_pr_prep`, `decompose`, `validate`, `coder`,
  `return_to_triage`, `review_merge.watch_checks`, `spec`.
- `pr_facts.reason` is often long agent prose. It can contain file paths, issue numbers of
  a target repository (`#1326`) and other repositories' short names (`wfs#2080`). C5's
  sample rules exist because of this.
- `scorecard.held_bucket(fact)` returns one of: the `REASON_BUCKETS` names (`architecture`,
  `do-not-merge`, `refuter`, `diff hygiene`, `risk`, `authorship`, `unfixed findings`,
  `review threads`, `auto-merge off`, `branch moved`, `CI`, `timeout`, `unspecified`), or
  `other`, `not read yet`, `queued`, `queued, merged by hand`, `no report`,
  `no reason recorded`.
- `scorecard._lease_ms(row)` is a run's lease time in ms. `scorecard.ending(row)` is a run's
  ending label (`PR opened`, `no PR`, `cancelled`, `fabro restart`, `failed`, `dead`,
  `lost`, ...). Import both from `retro.py`; do not copy them.
- `Store.lease_rows()` lists the leases currently held, each with `dispatched_at`.
- The scheduler container is `fabro-scheduler`, and it has `GITHUB_TOKEN` in its
  environment. That token is the operator's account (`andrewthetechie`, admin on this
  repository), so retro issues show the operator as their author.
- The Discord webhook exists only in the fabro container, at
  `/storage/secrets/discord_webhook_url`. `ops/fabro-monitor.sh` (`load_webhook`, `send`)
  reads and posts it. Copy that pattern.
- This repository has no `agent` label, and no automation reads its issues. A retro issue
  cannot start a run.
- `github.py` has `comment(...)`, `_write(...)`, `_write_error(...)`, `_next_link_url(...)`
  and `PAGE_SIZE`. `fetch_labelled` is the model for a labelled list read.
- `ops/scheduler/tests/test_scorecard.py` has the fixtures (`store`, `run`, `facts`,
  `summary`) that build window rows. Copy them into `test_retro.py`.

## Contracts

### C1. Failure classes

`retro.classify(rows: ScorecardRows) -> Classified` reads one window and returns every
class in it, with no threshold applied.

A run is **summarized** when its `run_summary` row has `status` `ok` or `missing`. A run
with no summary row, or with `status = 'error'`, is **unsummarized**: it is counted in
`Classified.unsummarized` and is in no class.

**Key charset.** A key is `KIND:PART[:PART...]`. Each part is passed through
`slug(text)`: lowercase, every run of characters outside `[a-z0-9_./-]` becomes one `-`,
leading and trailing `-` removed, empty becomes `unknown`. So `diff hygiene` becomes
`diff-hygiene` and `kimi/kimi-k3` stays `kimi/kimi-k3`. Every key matches
`^(ending|held|timeout|failover)(:[a-z0-9_./-]+)+$`.

| Kind | Which runs | Key |
|---|---|---|
| `ending` | Each summarized `run_history` row where `kind` is `failed`, `dead` or `lost`, or where `kind` is `succeeded` and `pr_lookup` is `none`. Leave out `kind = failed` with `reason = cancelled`. Leave out a run whose node is `close_noop` or `demote_lane`. | `ending:<node>:<detail>`. `node` is `terminal_node` when the summary is `ok`, else `unknown`. `detail`: `terminated` when `reason = terminated`; else `category` when set (`deterministic`); else `reason` when `kind = failed`; `dead`; `lost`; `no-pr` for a succeeded run with no PR. |
| `held` | Each PR the window's runs opened (the scorecard's `pr_runs`) that was **not merged by the workflow** (C1a). Leave out the policy buckets: `architecture`, `do-not-merge`, `auto-merge off`, `queued`, `queued, merged by hand`, `no report`, `not read yet`. | `held:<bucket>`, with `bucket = held_bucket(fact)`. The class's runs are every run that opened the PR. |
| `timeout` | Each `run_stage_stats` row of a summarized run with `timeouts > 0`. | `timeout:<node_id>` |
| `failover` | Each `run_failovers` row of a summarized run. | `failover:<node_id>:<from_model>:<error_kind>` (`to_model` is not in the key) |

`pr_lookup = failed` (the lookup itself failed) is not `none`, so that run is in no
`ending` class.

**C1a. Merged by the workflow.** Task 02 moves the existing test inside `scorecard.compute`
into one public function, and `compute` calls it. Behaviour does not change:

```python
def merged_by_workflow(runs: Sequence[sqlite3.Row], fact: sqlite3.Row | None) -> bool:
    """A release row says the merge phase merged it, or it merged after the merge
    phase queued it and the merge queue did not hand it off."""
```

**The result:**

```python
@dataclass(frozen=True)
class RunRef:
    run_id: str
    ending: str        # scorecard.ending(row)
    lease_ms: int      # scorecard._lease_ms(row)

@dataclass(frozen=True)
class ClassStat:
    key: str
    kind: str                     # "ending" | "held" | "timeout" | "failover"
    runs: tuple[RunRef, ...]      # distinct runs, longest lease first, then run_id
    events: int                   # timeout: summed `timeouts`; failover: summed `count`;
                                  # ending: runs; held: PRs
    samples: tuple[str, ...] = () # held:other and held:no-reason-recorded only (C5)

    @property
    def box_hours(self) -> float: ...   # sum(r.lease_ms for r in runs) / 3_600_000

@dataclass(frozen=True)
class Classified:
    classes: dict[str, ClassStat]  # key -> stat
    unsummarized: int
```

`held:no-reason-recorded` never has a reason, so its samples are always empty. Only
`held:other` gets samples, from the `reason` of its PRs. Keep the `samples` field on both
so the code has one path.

### C2. Threshold and ranking

```python
DEFAULT_MIN_RUNS = 3
DEFAULT_MIN_BOX_HOURS = 4.0
DEFAULT_CAP = 3

def over_threshold(stat: ClassStat, *, min_runs: int, min_box_hours: float) -> bool:
    return len(stat.runs) >= min_runs or stat.box_hours >= min_box_hours
```

Classes over the threshold are ranked by `box_hours` descending, then `key` ascending. The
plan (C4) and all writes follow that order.

### C3. Known classes: what GitHub holds

Task 01 adds:

```python
@dataclass(frozen=True)
class TrackedIssue:
    number: int
    state: str                 # "open" | "closed"
    state_reason: str | None   # "completed" | "not_planned" | "duplicate" | "reopened" | None
    closed_at: str | None      # ISO-8601, as GitHub sends it
    html_url: str
    body: str

def fetch_label_all_states(repo, label, token, *, client=None, timeout=...) -> list[TrackedIssue]
    # GET /repos/{repo}/issues?labels=<label>&state=all&per_page=PAGE_SIZE, every page
    # (follow rel="next" with _next_link_url). Drops pull requests. Raises GitHubError
    # on any non-200.

def create_issue(repo, title, body, labels, token, *, client=None, timeout=...) -> TrackedIssue
    # POST /repos/{repo}/issues {"title", "body", "labels"}. Raises GitHubError on non-2xx.
```

`retro.MARKER = re.compile(r"<!-- fabro:retro-class ([a-z0-9_.:/-]+) -->")`. An issue can
carry more than one marker. `retro.known_classes(issues) -> dict[str, list[TrackedIssue]]`
maps each key to its issues, highest number first.

The retro reads issues in `RETRO_REPO = "andrewthetechie/fabro-workflows"` (the CLI's
`--repo` overrides it) with the label `RETRO_LABEL = "retro"`.

### C4. The plan

```python
@dataclass(frozen=True)
class Action:
    kind: str          # "file" | "regression" | "comment" | "suppressed" | "capped" | "withheld"
    key: str
    stat: ClassStat
    issue: int | None  # comment: the open issue; regression: the closed one; else None
    title: str | None  # file and regression only
    body: str | None   # file, regression, comment; None for the rest
    why: str = ""      # withheld: what C5 matched

def plan(current: Classified, previous: Classified, known: dict[str, list[TrackedIssue]],
         *, week_start: date, repos: Sequence[str], min_runs: int, min_box_hours: float,
         cap: int) -> list[Action]
```

For each class over the threshold, in rank order (C2):

1. If any known issue for the key is `open`: a `comment` on the highest-numbered open one.
2. Else, if a closed one exists: look at the highest-numbered closed issue. If its
   `state_reason` is `not_planned` or `duplicate`: `suppressed`. Otherwise (`completed`,
   `reopened`, `None`): a `regression` of it.
3. Else: `file`.
4. A `file` or `regression` after `cap` of them have been planned becomes `capped`.
5. Render the title and body (C7). If `guard` (C5) matches the title or body, the action
   becomes `withheld` with `why` set. A withheld action does not use up a cap slot.

`previous` gives the "previous week" column in C7. A class that is absent from `previous`
shows `0`.

### C5. The guard and the sample rules

```python
def guard(text: str, repos: Sequence[str]) -> str | None
    """What in `text` names a target repository, or None."""
```

`repos` is every `config.repos[*].name` (`owner/name`). Matching is case-insensitive. It
returns a short description of the first match:

- the full `owner/name` of any configured repository;
- the bare `name` of any configured repository, as a whole word (`\bname\b`);
- `github.com/<owner>/<x>`, where `<x>` is anything except `fabro-workflows`;
- a cross-repository reference `<word>#<digits>` (`wfs#2080`, `repo#12`). A plain `#41`
  preceded by a space, `(` or line start is this repository's issue and is allowed.

**Samples** (only `held:other`) are made safe before they reach a body. They still pass
through `guard` with the rest of the body:

1. Collapse whitespace; truncate to 160 characters, adding `…` if cut.
2. Replace `#<digits>` with `issue <digits>`. A target repository's `#1326` would otherwise
   link to this repository's issue 1326.
3. Replace `@` with `@​` (a zero-width space). No one is mentioned by accident.
4. At most 3 samples per class: the reasons of its PRs in PR order, distinct, first 3.

The run ids, node names, bucket names, counts and box-hours are the only other evidence.
Never write `run_history.repo`, `pr_number` or `pr_url` into a body.

### C6. The Discord message

`retro.discord_text(...) -> str` builds one plain-text message, at most 2000 characters.
Lines, in order (leave out a line whose list is empty):

```
[DRY RUN] Factory retro, week of 2026-10-05 to 2026-10-11 (backlog runs only)
Runs 45 (-3) · PRs 40 (+2) · merged by workflow 12 (30%, +5pp) · box-h per merge 6.1 (-0.8)
Held: refuter 5, CI 3, unspecified 2 · Timeouts: improve 4, review_merge.review_fix 2 · Failovers: 12 runs
Filed: #41 timeout:improve (6 runs, 9.5 box-h)
Regression: #44 of #19 held:unspecified (3 runs, 2.0 box-h)
Commented: #42 failover:spec:kimi/kimi-k3:access_denied (12 runs, 40.1 box-h)
Over threshold, not filed (cap 3): ending:validate:deterministic, held:ci
Suppressed (closed not planned): 1 · Withheld (named a target repository): 1
Not counted: 2 runs still leased, 1 not summarized. Box-hours are per class; never add them.
```

- `[DRY RUN] ` is the prefix only on a dry run, and then `Filed/Regression/Commented`
  become `Would file/Would file regression/Would comment`, with no issue numbers for new
  issues.
- The deltas compare with the previous week's scorecard: counts as signed integers, the
  merge rate in percentage points, box-hours per merge with one decimal. `n/a` when either
  side is `None`.
- `Held:` lists the current week's `card.held`, leaving out the C1 policy buckets, top 5.
  `Timeouts:` is the top 5 of `card.timeouts`. `Failovers:` is the distinct runs in
  `card.failovers`.
- If the text is over 2000 characters, shorten the `Filed`, `Commented`, `Over threshold`
  lists from the end, replacing the dropped items with `…and N more`, until it fits.

On a GitHub failure (C9), the first line is `retro failed: <message>`, and the rest of the
message shows what was written before the failure.

### C7. Issue and comment text

Title: `retro: <key>: <N> runs, <H> box-hours (week of <monday>)`. `H` has one decimal. A
regression's title starts `retro (regression of #M): `.

A one-sentence template for each kind, with the key's parts filled in:

| Kind | Sentence |
|---|---|
| `ending` (`detail` = `no-pr`) | Runs that finished at `<node>` without opening a PR. |
| `ending` (other) | Runs that ended at `<node>` as `<detail>`. |
| `held` | PRs the workflow did not merge, held for the reason bucket `<bucket>` (`scorecard.REASON_BUCKETS`). |
| `timeout` | Stage `<node>` timed out. Every timed-out attempt counts, retried ones included. |
| `failover` | Stage `<node>` failed over from `<from_model>` on `<error_kind>`. |

Body, in this order:

```
<!-- fabro:retro-class KEY -->
**Failure class:** `KEY`

SENTENCE

| | Week of MONDAY | Previous week |
|---|---|---|
| Runs | N | N0 |
| Events | E | E0 |
| Box-hours | H | H0 |

Box-hours are each run's whole lease. A run in several classes counts in each.

**Runs** (longest lease first, at most 10):
- `RUN_ID`: ENDING, LEASE h

**Sample reasons:**          (held:other only)
> SAMPLE

**Look further**
- `ops/fabro-scorecard.sh --from MONDAY --to SUNDAY`
- On the host: `docker compose exec -T fabro fabro events RUN_ID -p`

Filed by the weekly retro (ADR 0026). Close as completed when fixed: a recurrence files a
regression. Close as not planned to silence this class.
```

A regression body adds, after the sentence: `Regression of #M, closed as completed on
DATE.` (`DATE` is `closed_at`'s date, or `an unknown date`).

A comment is the table, the box-hours note and the runs list, under the heading
`**Week of MONDAY**`. It has no marker.

### C8. The cron wrapper

`ops/fabro-retro.sh`, POSIX `sh`, deployed to `~/bin`:

| Env | Default | Meaning |
|---|---|---|
| `DRY_RUN` | `1` | Anything but `0` passes `--dry-run`. The Discord message **is** posted on a dry run; that is how the operator reviews it. |
| `NO_POST` | `0` | `1` prints the message and posts nothing (for tests and by-hand runs). |
| `SCHEDULER_CONTAINER` | `fabro-scheduler` | Where the subcommand runs. |
| `CONTAINER` | `fabro-fabro-1` | Where the webhook is read (the monitor's variable name). |
| `RETRO_ARGS` | empty | Extra subcommand arguments, word-split, for by-hand runs. |

Flow: `out=$(docker exec "$SCHEDULER_CONTAINER" python -m fabro_scheduler retro --last-week [--dry-run] $RETRO_ARGS)`,
with its exit code in `rc`. The subcommand's stderr passes through to the cron log.

- `rc = 0`: post `out`.
- `rc != 0`: post `retro failed: exit <rc>; see ~/.local/state/fabro-retro.log`, and append
  `out` on the next line when it is not empty.
- Truncate the posted text to 2000 characters in `jq` (`{content: ($content[:2000])}`),
  which counts code points. `head -c` counts bytes and would cut the message's `·` and `…`
  in half.
- Print the posted text to stdout too, so it lands in the log.
- Read the webhook and post exactly as `fabro-monitor.sh` does (`jq -n --arg content ...`,
  `curl -fsS -m 10`). If the webhook cannot be read, or the POST fails, print a warning to
  stderr and exit 1.
- Exit with `rc` otherwise (0 on success).

The crontab line (installed by task 07, never by `make deploy`):

```
0 12 * * 1 /home/andrew/bin/fabro-retro.sh >> /home/andrew/.local/state/fabro-retro.log 2>&1
```

Going live adds `DRY_RUN=0 ` before `/home/andrew/bin/fabro-retro.sh`.

### C9. The subcommand

```
python -m fabro_scheduler retro (--last-week | --week-of YYYY-MM-DD) [--dry-run]
    [--repo OWNER/NAME] [--min-runs 3] [--min-box-hours 4] [--cap 3] [--config PATH]
```

- `--last-week` is the previous ISO week: Monday 00:00 UTC to the next Monday 00:00 UTC,
  computed from the current time. `--week-of D` is the ISO week that contains date `D`. The
  previous window is the 7 days before.
- The token is `GITHUB_TOKEN` from the environment. A dry run reads GitHub too.
- stdout carries only the Discord text (C6). Logs go to stderr.
- Exit codes: `0` success; `1` config or database error, or no `GITHUB_TOKEN` (stdout
  empty); `2` bad arguments; `3` a GitHub read or write failed. On `3`, stdout has the C6
  text with the `retro failed:` first line.
- Order: load the config, open the store, read both windows' rows and `lease_rows()`,
  compute both scorecards and both `Classified`, read the known issues (on failure: exit
  3, nothing written), build the plan, then run the actions in order. `comment` uses
  `github.comment`. `file` and `regression` use `github.create_issue` with labels
  `["retro", "bug"]`. A write failure stops the remaining writes; the text then reports
  what was written and `retro failed: <GitHubError>`.
- "Still leased" is the `lease_rows()` whose `dispatched_at` falls in the current window.

## What this does not cover

- Runs outside `run_history`: `pr-review`, `dependabot`, `merge-queue`, `arch-review`,
  hand fires (D11).
- Any decision about a class. The retro reports. The operator decides.
- Revert detection and the other B1 items the scorecard README lists as not built.

## Tasks

| Task | What | Files |
|---|---|---|
| 01 | `fetch_label_all_states`, `create_issue` | `github.py`, `tests/test_github.py` |
| 02 | `merged_by_workflow` extracted; `retro.classify`, `over_threshold` | `scorecard.py`, `retro.py`, `tests/test_retro.py` |
| 03 | `known_classes`, `guard`, samples, `plan`, issue/comment text, `discord_text` | `retro.py`, `tests/test_retro.py` |
| 04 | The `retro` subcommand | `app.py`, `tests/test_app.py` |
| 05 | `ops/fabro-retro.sh`, its tests, the deploy list, the runbook | `ops/fabro-retro.sh`, `ops/tests/test_fabro_retro.py`, `ops/deploy-host.sh`, `ops/README.md` |
| 06 | Docs | `docs/scorecard/README.md`, `docs/README.md`, this file's status |
| 07 | **Operator only.** Push, deploy, label, cron, dry run, go live, the one-month check | host |
