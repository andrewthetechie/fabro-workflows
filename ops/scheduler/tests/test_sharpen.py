"""The sharpen queue's scheduler half (issue-sharpening 04, ADR 0033).

An issue the precheck rates `unverifiable` is held, with the label first and the row second, until
the factory has sharpened its criteria. These tests pin the body hash to the shell pipeline the
precheck package runs, the precheck per text, the hold rule (C7), the supersede and release, the
start-up back-fill (D17), and the table's one-open-row rule.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from fabro_scheduler.config import ConfigError, load_config
from fabro_scheduler.github import GitHubError, Issue, _to_issue, body_hash
from fabro_scheduler.precheck import KIND, PrecheckProducer
from fabro_scheduler.queue import build_queue
from fabro_scheduler.sharpen import LABEL, hold_reason, parse_outcome, reconcile_repo
from fabro_scheduler.store import Store

from test_background import BOXES, NOW, TRACKED_REPOS
from test_precheck import JELLY, config, ranked

NUMBER = 7


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def put(store: Store, number: int = NUMBER, *, text: str = "aaaaaaaaaaaa", labels=("agent",)) -> None:
    store.upsert_issue(Issue(JELLY, number, f"issue {number}", frozenset(labels), NOW, text))


def precheck_done(store: Store, number: int = NUMBER, *, verdict: str = "unverifiable",
                  text: str | None = "aaaaaaaaaaaa", queued_for: str | None = "aaaaaaaaaaaa") -> int:
    job_id = store.enqueue_background_job(
        kind=KIND, repo=JELLY, target=str(number), inputs={"issue_number": number}, priority=2,
        typical_box_minutes=3, now=NOW, target_hash=queued_for,
    )
    store.start_background_job(job_id, run_id=f"R{job_id}", box="coders-a", now=NOW)
    outcome = f"verdict={verdict}" + (f" body={text}" if text else "")
    store.finish_background_job(job_id, state="done", outcome=outcome, now=NOW)
    return job_id


class Labels:
    """Records label writes, and fails the ones a test asks it to."""

    def __init__(self, fail: bool = False) -> None:
        self.added: list[tuple[str, int, str]] = []
        self.removed: list[tuple[str, int, str]] = []
        self.fail = fail

    def add(self, repo: str, number: int, label: str) -> None:
        if self.fail:
            raise GitHubError("502")
        self.added.append((repo, number, label))

    def remove(self, repo: str, number: int, label: str) -> None:
        if self.fail:
            raise GitHubError("502")
        self.removed.append((repo, number, label))


def pass_(store: Store, labels: Labels, *, backfill: bool = False, now: datetime = NOW):
    return reconcile_repo(JELLY, store, labels.add, labels.remove, backfill=backfill, now=now)


# --- the body hash --------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is how the package computes the hash")
@pytest.mark.parametrize("title,body", [
    ("feat: a title", "## Acceptance criteria\n\n- [ ] one\n"),
    ("feat: no body", None),
    ("feat: trailing newlines", "text\n\n\n"),
    ("feat: non-ASCII — é", "naïve body\n"),
    ("", ""),
])
def test_the_python_hash_is_the_shell_pipelines(title, body):
    """`issue-precheck`'s `claim`: `{ jq -j .title; echo; jq -j .body; } | sha256sum | cut -c1-12`."""
    import json

    payload = json.dumps({"title": title, "body": body})
    script = (
        "if command -v sha256sum >/dev/null; then SUM=sha256sum; else SUM='shasum -a 256'; fi; "
        "{ printf '%s' \"$P\" | jq -j '.title // \"\"'; echo; printf '%s' \"$P\" | jq -j '.body // \"\"'; } "
        "| $SUM | cut -c1-12"
    )
    shell = subprocess.run(["sh", "-c", script], env={"P": payload, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
                           capture_output=True, text=True, check=True).stdout.strip()
    assert body_hash(title, body) == shell
    assert len(shell) == 12


def test_a_listed_issue_carries_its_hash():
    issue = _to_issue(JELLY, {"number": 4, "title": "t", "body": "b", "labels": []}, NOW)
    assert issue.body_hash == body_hash("t", "b")
    assert _to_issue(JELLY, {"number": 4, "title": "t", "body": None, "labels": []}, NOW).body_hash == body_hash("t", "")


def test_the_cache_keeps_the_hash_and_a_pre_column_database_migrates(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE issue_cache (repo TEXT NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL,"
        " labels TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,"
        " PRIMARY KEY (repo, number));"
        "INSERT INTO issue_cache VALUES ('o/r', 1, 't', '[\"agent\"]', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00');"
        "CREATE TABLE background_jobs (job_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, repo TEXT NOT NULL,"
        " target TEXT NOT NULL DEFAULT '', inputs_json TEXT NOT NULL DEFAULT '{}', priority INTEGER NOT NULL,"
        " typical_box_minutes INTEGER NOT NULL, created_at TEXT NOT NULL, state TEXT NOT NULL);"
    )
    old.commit()
    old.close()
    migrated = Store(path)
    try:
        assert migrated.get_issue("o/r", 1).body_hash == "", "an old row has none until the next poll"
        put(migrated, 2, text="bbbbbbbbbbbb")
        assert migrated.get_issue(JELLY, 2).body_hash == "bbbbbbbbbbbb"
        put(migrated, 2, text="cccccccccccc")
        assert migrated.get_issue(JELLY, 2).body_hash == "cccccccccccc", "a poll refreshes the hash"
    finally:
        migrated.close()


# --- the precheck per text ------------------------------------------------------------


def test_a_new_text_gets_a_new_precheck_and_the_same_text_does_not(store):
    cfg = config()
    put(store, 3, text="aaaaaaaaaaaa")
    producer = PrecheckProducer(cfg, store)
    assert len(producer.tick(ranked(store, cfg), now=NOW)) == 1
    assert producer.tick(ranked(store, cfg), now=NOW) == [], "the same text, once"
    store.finish_background_job(1, state="done", outcome="verdict=ready body=aaaaaaaaaaaa", now=NOW)
    assert producer.tick(ranked(store, cfg), now=NOW) == [], "and a finished job still counts"
    put(store, 3, text="bbbbbbbbbbbb")
    assert len(producer.tick(ranked(store, cfg), now=NOW)) == 1, "an edited body is checked again"
    assert store.background_job(2)["target_hash"] == "bbbbbbbbbbbb"


def test_a_text_the_sharpening_already_rechecked_is_not_prechecked_again(store):
    cfg = config()
    put(store, 3, text="aaaaaaaaaaaa")
    row = store.sharpen_enqueue(JELLY, 3, "aaaaaaaaaaaa", NOW)
    store.sharpen_claim(row, "RUN", NOW)
    store.sharpen_finish(row, state="applied", now=NOW, applied_hash="bbbbbbbbbbbb", recheck="ready")
    put(store, 3, text="bbbbbbbbbbbb")
    assert PrecheckProducer(cfg, store).tick(ranked(store, cfg), now=NOW) == []
    row2 = store.sharpen_enqueue(JELLY, 3, "zzzzzzzzzzzz", NOW)
    store.sharpen_claim(row2, "RUN2", NOW)
    store.sharpen_finish(row2, state="applied", now=NOW, applied_hash="cccccccccccc", recheck=None)
    put(store, 3, text="cccccccccccc")
    assert len(PrecheckProducer(cfg, store).tick(ranked(store, cfg), now=NOW)) == 1, "no recheck, so the lane judges it"


def test_an_issue_with_no_hash_yet_keeps_the_one_precheck_per_issue_rule(store):
    cfg = config()
    put(store, 3, text="")
    producer = PrecheckProducer(cfg, store)
    assert len(producer.tick(ranked(store, cfg), now=NOW)) == 1
    store.finish_background_job(1, state="done", outcome="verdict=ready", now=NOW)
    assert producer.tick(ranked(store, cfg), now=NOW) == []


def test_the_outcome_reads_back_in_both_forms():
    assert parse_outcome("verdict=unverifiable body=abc123") == ("unverifiable", "abc123")
    assert parse_outcome("verdict=ready") == ("ready", "")
    assert parse_outcome("succeeded (no marker)") == ("", "")
    assert parse_outcome(None) == ("", "")


# --- the table ------------------------------------------------------------------------


def test_one_open_row_per_issue_and_the_lifecycle(store):
    first = store.sharpen_enqueue(JELLY, NUMBER, "aaaaaaaaaaaa", NOW)
    assert first and store.sharpen_enqueue(JELLY, NUMBER, "bbbbbbbbbbbb", NOW) is None, "one non-terminal row"
    assert store.sharpen_enqueue(JELLY, 8, "aaaaaaaaaaaa", NOW), "another issue has its own"
    assert store.sharpen_claim(first, "RUN1", NOW) is True
    assert store.sharpen_claim(first, "RUN2", NOW) is False, "only a queued row is claimed"
    row = store.sharpen_row(first)
    assert (row["state"], row["attempts"], row["run_id"]) == ("running", 1, "RUN1")
    retry_at = NOW + timedelta(minutes=30)
    store.sharpen_retry(first, next_eligible_at=retry_at, rule="the box faulted")
    row = store.sharpen_row(first)
    assert (row["state"], row["run_id"], row["next_eligible_at"], row["rule"]) == (
        "queued", None, retry_at.isoformat(), "the box faulted")
    assert store.sharpen_claim(first, "RUN2", NOW) and store.sharpen_row(first)["attempts"] == 2
    store.sharpen_finish(first, state="capped", now=NOW, round_=3, rule="round 3 was not clean",
                         transformations="to_post_deploy=2", box_minutes=41.5)
    row = store.sharpen_row(first)
    assert (row["state"], row["round"], row["transformations"], row["box_minutes"]) == (
        "capped", 3, "to_post_deploy=2", 41.5)
    assert store.sharpen_live_row(JELLY, NUMBER) is None
    assert store.sharpen_enqueue(JELLY, NUMBER, "bbbbbbbbbbbb", NOW), "a terminal row frees the issue"
    with pytest.raises(ValueError):
        store.sharpen_finish(first, state="running", now=NOW)


def test_only_a_queued_row_is_superseded(store):
    queued = store.sharpen_enqueue(JELLY, NUMBER, "aaaaaaaaaaaa", NOW)
    assert store.sharpen_supersede(queued, NOW) is True
    assert store.sharpen_row(queued)["state"] == "superseded"
    running = store.sharpen_enqueue(JELLY, 8, "aaaaaaaaaaaa", NOW)
    store.sharpen_claim(running, "R", NOW)
    assert store.sharpen_supersede(running, NOW) is False
    assert store.sharpen_row(running)["state"] == "running"


def test_a_row_names_both_its_hashes(store):
    row = store.sharpen_enqueue(JELLY, NUMBER, "aaaaaaaaaaaa", NOW)
    store.sharpen_claim(row, "R", NOW)
    store.sharpen_finish(row, state="applied", now=NOW, applied_hash="bbbbbbbbbbbb")
    assert store.sharpen_names_hash(JELLY, NUMBER, "aaaaaaaaaaaa")
    assert store.sharpen_names_hash(JELLY, NUMBER, "bbbbbbbbbbbb")
    assert not store.sharpen_names_hash(JELLY, NUMBER, "cccccccccccc")
    assert not store.sharpen_names_hash(JELLY, 99, "aaaaaaaaaaaa")


# --- the hold (C7) --------------------------------------------------------------------


def test_an_unverifiable_issue_is_held_label_first_then_the_row(store):
    put(store)
    precheck_done(store)
    labels = Labels()
    order: list[str] = []
    real_enqueue = store.sharpen_enqueue
    store.sharpen_enqueue = lambda *a, **k: order.append("row") or real_enqueue(*a, **k)
    labels_add = labels.add
    labels.add = lambda *a: order.append("label") or labels_add(*a)
    assert pass_(store, labels) == [(NUMBER, "held for sharpening")]
    assert order == ["label", "row"], "the label goes on before the row exists"
    assert labels.added == [(JELLY, NUMBER, LABEL)] and LABEL == "precheck-unverifiable"
    row = store.sharpen_live_row(JELLY, NUMBER)
    assert (row["state"], row["body_hash"]) == ("queued", "aaaaaaaaaaaa")
    assert LABEL in store.get_issue(JELLY, NUMBER).labels, "the queue sees the hold before the next poll"
    assert build_queue(config().schedulable_repos(), store, NOW) == [], "and drops the issue"
    assert pass_(store, labels) == [], "idempotent"


def test_the_hold_does_not_read_the_precheck_switches(store):
    put(store)
    precheck_done(store)
    cfg = config(SCHEDULER_PRECHECK_HOLDS="off")
    assert cfg.precheck_holds is False
    assert pass_(store, Labels()) == [(NUMBER, "held for sharpening")]


def test_a_ready_or_older_precheck_holds_nothing(store):
    put(store, 3)
    precheck_done(store, 3, verdict="ready")
    put(store, 4, text="bbbbbbbbbbbb")
    precheck_done(store, 4, text="aaaaaaaaaaaa", queued_for="aaaaaaaaaaaa")
    put(store, 5)
    labels = Labels()
    assert pass_(store, labels) == []
    assert labels.added == []


def test_a_text_a_sharpen_row_names_is_never_held_again(store):
    for number, names in ((3, "body"), (4, "applied")):
        put(store, number)
        precheck_done(store, number)
        row = store.sharpen_enqueue(JELLY, number, "aaaaaaaaaaaa" if names == "body" else "zzzzzzzzzzzz", NOW)
        store.sharpen_claim(row, "R", NOW)
        store.sharpen_finish(row, state="capped" if names == "body" else "applied", now=NOW,
                             applied_hash="aaaaaaaaaaaa" if names == "applied" else None)
    labels = Labels()
    assert pass_(store, labels) == []
    assert labels.added == []


def test_a_failed_label_write_leaves_no_row_and_the_next_pass_retries(store):
    put(store)
    precheck_done(store)
    assert pass_(store, Labels(fail=True)) == []
    assert store.sharpen_live_row(JELLY, NUMBER) is None, "never a row without its label"
    assert pass_(store, Labels()) == [(NUMBER, "held for sharpening")]


def test_a_queued_row_whose_text_changed_is_superseded_and_released(store):
    put(store)
    precheck_done(store)
    pass_(store, Labels())
    put(store, text="bbbbbbbbbbbb", labels=("agent", LABEL))
    labels = Labels()
    assert pass_(store, labels) == [(NUMBER, "superseded: the text changed while it was queued")]
    assert labels.removed == [(JELLY, NUMBER, LABEL)]
    assert store.sharpen_rows(JELLY, NUMBER)[0]["state"] == "superseded"
    put(store, text="bbbbbbbbbbbb")
    assert pass_(store, Labels()) == [], "the label is gone, and nothing holds the new text until it is prechecked"


def test_a_running_row_is_left_to_its_run(store):
    put(store)
    precheck_done(store)
    pass_(store, Labels())
    row = store.sharpen_live_row(JELLY, NUMBER)
    store.sharpen_claim(int(row["id"]), "RUN", NOW)
    put(store, text="bbbbbbbbbbbb", labels=("agent", LABEL))
    labels = Labels()
    assert pass_(store, labels) == []
    assert (labels.added, labels.removed) == ([], [])
    assert store.sharpen_live_row(JELLY, NUMBER)["state"] == "running"


def test_a_terminal_row_releases_the_label_it_left_behind(store):
    put(store, labels=("agent", LABEL))
    row = store.sharpen_enqueue(JELLY, NUMBER, "aaaaaaaaaaaa", NOW)
    store.sharpen_claim(row, "R", NOW)
    store.sharpen_finish(row, state="failed", now=NOW)
    labels = Labels()
    assert pass_(store, labels) == [(NUMBER, "released: its sharpening is over")]
    assert labels.removed == [(JELLY, NUMBER, LABEL)]


def test_a_failed_release_is_retried(store):
    put(store, labels=("agent", LABEL))
    row = store.sharpen_enqueue(JELLY, NUMBER, "aaaaaaaaaaaa", NOW)
    store.sharpen_claim(row, "R", NOW)
    store.sharpen_finish(row, state="capped", now=NOW)
    assert pass_(store, Labels(fail=True)) == []
    assert pass_(store, Labels()) == [(NUMBER, "released: its sharpening is over")]


# --- the back-fill (D17) --------------------------------------------------------------


def test_the_back_fill_holds_every_queued_unverifiable_issue_once(store):
    put(store, 3, text="aaaaaaaaaaaa")
    precheck_done(store, 3, text=None, queued_for=None)
    put(store, 4, text="cccccccccccc")
    precheck_done(store, 4, text="bbbbbbbbbbbb", queued_for="bbbbbbbbbbbb")
    put(store, 5)
    precheck_done(store, 5, verdict="ready")
    labels = Labels()
    assert pass_(store, labels) == [], "outside a start-up pass an older text is not trusted"
    done = pass_(store, labels, backfill=True)
    assert sorted(done) == [(3, "held for sharpening (back-fill)"), (4, "held for sharpening (back-fill)")]
    assert store.sharpen_live_row(JELLY, 3)["body_hash"] == "aaaaaaaaaaaa", "the current text, not the old one"
    assert store.sharpen_live_row(JELLY, 4)["body_hash"] == "cccccccccccc"
    assert store.sharpen_live_row(JELLY, 5) is None
    assert pass_(store, labels, backfill=True) == [], "a second start finds the rows"


def test_the_back_fill_skips_an_issue_that_already_has_a_row(store):
    put(store, 3)
    precheck_done(store, 3, text=None, queued_for=None)
    row = store.sharpen_enqueue(JELLY, 3, "zzzzzzzzzzzz", NOW)
    store.sharpen_claim(row, "R", NOW)
    store.sharpen_finish(row, state="cannot_sharpen", now=NOW)
    labels = Labels()
    assert pass_(store, labels, backfill=True) == [], "its earlier sharpening decided it"
    assert labels.added == []


# --- the page's reason ----------------------------------------------------------------


def test_the_queue_pages_reason_is_the_sharpening(store):
    put(store, labels=("agent", LABEL))
    assert hold_reason(store, JELLY, NUMBER) is None
    row = store.sharpen_enqueue(JELLY, NUMBER, "aaaaaaaaaaaa", NOW)
    assert hold_reason(store, JELLY, NUMBER) == "being sharpened (queued)"
    store.sharpen_claim(row, "R", NOW)
    assert hold_reason(store, JELLY, NUMBER) == "being sharpened (running)"
    assert [issue.number for issue in store.held_issues()] == [NUMBER]


def test_naming_unverifiable_as_a_holdable_verdict_is_an_error():
    with pytest.raises(ConfigError, match="ADR 0033"):
        load_config(TRACKED_REPOS, env={"SCHEDULER_BACKGROUND": "on", "SCHEDULER_BOX_HEALTH": BOXES,
                                        "SCHEDULER_PRECHECK_HOLD_VERDICTS": "blocked,unverifiable"})
