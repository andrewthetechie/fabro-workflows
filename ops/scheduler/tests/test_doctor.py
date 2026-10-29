"""The PR doctor's reason table, its marker, and its eligibility rules
(more-local-inference 06, D13).

Everything here is pure: a reason string in, a category or a verdict out. The cases worth
having are the ones whose failure is silent in production -- a reason that maps to no category
(a PR that never gets its one attempt), a `retryable:` reason read as fixable (an attempt spent
on an infrastructure stop), and a marker that stops being recognised (a second attempt, which
D13 forbids).

The live texts in `LIVE_REASONS` are copied from `pr_facts` on 2026-10-29, with their counts:
20 blocked PRs and 1 needs-human across the four repositories.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fabro_scheduler.config import load_config
from fabro_scheduler.doctor import (
    CI,
    FINDINGS,
    HANDOFF_COMMENT,
    HANDOFF_NO_REVIEW_COMMENT,
    HYGIENE,
    KIND,
    NONE_REASON_PREFIXES,
    REFUTER,
    REVIEW,
    PrView,
    classify,
    eligibility,
    format_marker,
    ineligible,
    is_config_change,
    is_retryable,
    is_stale,
    parse_marker,
    view_from_facts,
)
from fabro_scheduler.prfacts import PrFacts, report_has_items

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"

# --- the reason table ---------------------------------------------------------------

# Every text a live blocked PR carried on 2026-10-29, with the category the doctor gives it.
# Seven refuter blocks are the "mostly coverage claims" shape; the rest are one each.
LIVE_REASONS = [
    (
        "The refuter could not confirm the work is done: unverifiable: the coverage of the "
        "new branch is not shown; unverifiable: npm run test:coverage passes.",
        REFUTER,
    ),
    (
        "The refuter could not confirm the work is done: not_met: the endpoint rejects an "
        "empty body.",
        REFUTER,
    ),
    ("There are unfixed findings; a human must merge this PR.", FINDINGS),
    ("The review did not end in a mergeable state.", REVIEW),
    (
        "CI is still failing after two fix attempts (test, lint); a human must merge this PR.",
        CI,
    ),
    (
        "Diff hygiene blocks the merge: asserts_removed=1. Samples: src/app.test.ts:41 "
        "asserts_removed `expect(x).toBe(1)`",
        HYGIENE,
    ),
    # Not in the table: no fix the doctor could make.
    (
        "This PR implements an Architecture issue (label architecture). ADR 0012 requires a "
        "human to merge it.",
        None,
    ),
    ("The PR title is not a Conventional Commit, so it cannot be auto-merged.", None),
    ("The PR carries the do-not-merge label, so a human merges it.", None),
    ("Reviewers have requested changes on this PR.", None),
    ("There are unresolved review conversations on this PR.", None),
    (
        "A commit on this PR was not authored by the automation; a human must merge it.",
        None,
    ),
    ("The risk rating is missing or above 3; a human must merge this PR.", None),
    (
        "The PR description has no valid fabro commit-body block; a human must merge this PR.",
        None,
    ),
    ("The refuter did not return a verdict, so the merge is refused.", None),
    (
        "The diff-hygiene result is missing or invalid, so the merge is refused.",
        None,
    ),
    (
        "The code changed after the diff-hygiene counters ran (a later stage, such as the "
        "refuter, edited the checkout), so the reviewed diff is not the one on the PR and the "
        "merge is refused.",
        None,
    ),
    ("CI is still red after the maximum number of fix attempts.", CI),
    ("CI failed and a fix retry was in progress when the run stopped.", None),
    (
        "The validate stage did not finish: ./.fabro/setup.sh and ./.fabro/ci.sh were still "
        "running when it was stopped, most likely on the stage timeout.",
        None,
    ),
    (
        "CI passed, but the run stopped after validate and before the review was delivered.",
        None,
    ),
    (
        "The review was delivered and the merge decision did not complete; a human should "
        "finish this PR.",
        None,
    ),
    (
        "The branch is still behind the base after a re-merge attempt; a human must merge this "
        "PR.",
        None,
    ),
    (
        "PR 12 was no longer open at merge time; assuming it was handled elsewhere. No failure "
        "to report.",
        None,
    ),
    ("The squash merge was rejected and this run could not complete it.", None),
    ("A conflict appeared after the review; the base moved substantially.", None),
    (
        "The review reported an error-severity finding of its own that its record does not "
        "account for, so whether it was fixed cannot be told from the report; a human must "
        "merge this PR.",
        FINDINGS,
    ),
    ("retryable: standards failed (transient_infra)", None),
]


@pytest.mark.parametrize("reason,category", LIVE_REASONS)
def test_every_live_reason_text_maps_to_its_category(reason, category):
    assert classify(reason) == category


def test_the_reason_table_covers_every_block_writer_in_the_merge_phase():
    """The table's prefixes come from the graphs, so they must still be in the graphs.

    A `fail '...'` or a `block '...'` that is renamed in `_shared/review-merge` would
    silently make that PR ineligible: no error, no job, and nothing in the store to notice.
    """
    graph = (
        Path(__file__).resolve().parents[3]
        / ".fabro/workflows/_shared/review-merge/review-merge.fabro"
    ).read_text()
    for prefix, _ in (
        ("There are unfixed findings; a human must merge this PR.", FINDINGS),
        ("The review did not end in a mergeable state.", REVIEW),
        ("CI is still red after the maximum number of fix attempts.", CI),
        ("CI is still failing after two fix attempts (", CI),
        ("Diff hygiene blocks the merge: ", HYGIENE),
        ("The refuter could not confirm the work is done: ", REFUTER),
    ):
        assert prefix in graph, prefix


# The block writers in both merge-phase graphs: `fail '...'`, `block '...'`, `blocked '...'`
# (single-quoted, or double-quoted with a `$(...)` tail cut off), and a literal echoed into
# either reason file. A text built from a variable is not a literal and is not listed.
_WRITERS = (
    re.compile(r"\b(?:fail|block|blocked) '([A-Z][^']+)"),
    re.compile(r'\b(?:fail|block|blocked) \\"([A-Z][^"\\$]+)'),
    re.compile(r"echo '([A-Z][^']+)' > [^\n]*(?:merge_block_reason|needs_human_reason)"),
)


def _block_texts() -> set[str]:
    root = Path(__file__).resolve().parents[3] / ".fabro/workflows"
    texts: set[str] = set()
    for graph in ("_shared/review-merge/review-merge.fabro", "ralph/workflow.fabro"):
        source = (root / graph).read_text()
        for pattern in _WRITERS:
            texts.update(match.group(1).strip() for match in pattern.finditer(source))
    return texts


def test_every_block_text_in_both_graphs_is_mapped_or_listed_as_none():
    """F6 of the Phase 2 review: two CI texts fell through to `none` because nothing listed
    them. Every literal block text is either in the reason table or in
    `NONE_REASON_PREFIXES`, so a new one fails here until someone decides which."""
    texts = _block_texts()
    assert len(texts) > 30, texts  # the extractor still finds the writers
    unlisted = sorted(
        text
        for text in texts
        if classify(text) is None
        and not any(text.startswith(prefix) for prefix in NONE_REASON_PREFIXES)
    )
    assert unlisted == []


def test_no_none_prefix_is_dead_or_also_mapped():
    texts = _block_texts()
    for prefix in NONE_REASON_PREFIXES:
        assert any(text.startswith(prefix) for text in texts), prefix
        assert classify(prefix) is None, prefix


def test_the_two_ci_texts_the_first_table_missed_are_ci():
    texts = _block_texts()
    assert "CI is red after 2 post-PR Iterations" in texts
    assert "The CI-fix agent could not make the failing check pass." in texts
    assert classify("CI is red after 2 post-PR Iterations") == CI
    assert classify(
        "The CI-fix agent could not make the failing check pass. pytest still fails"
    ) == CI


def test_a_retryable_reason_is_excluded_before_the_table():
    text = "retryable: standards failed (transient_infra)"
    assert is_retryable(text) is True
    assert classify(text) is None
    assert is_retryable("There are unfixed findings; a human must merge this PR.") is False


def test_a_hygiene_block_naming_config_changed_is_not_fixable():
    text = (
        "Diff hygiene blocks the merge: config_changed=1. Samples: .fabro/ci.sh "
        "config_changed `npm test`"
    )
    assert classify(text) == HYGIENE
    assert is_config_change(text) is True
    assert is_config_change("Diff hygiene blocks the merge: asserts_removed=1.") is False


# --- the marker ---------------------------------------------------------------------


def test_a_marker_round_trips_and_defaults_review_to_fire():
    line = format_marker(head="a" * 40, pushed="pending", review="skip", run="01MABC")
    assert line == (
        "<!-- fabro:pr-doctor head=" + "a" * 40 + " pushed=pending review=skip run=01MABC -->"
    )
    marker = parse_marker(line)
    assert marker is not None
    assert (marker.head, marker.pushed, marker.review, marker.run) == (
        "a" * 40,
        "pending",
        "skip",
        "01MABC",
    )
    assert marker.wants_review is False
    assert marker.pushed_nothing is False


def test_a_marker_without_review_reads_as_fire():
    """Decision 2: a marker written before the field existed must not disable the review."""
    marker = parse_marker("<!-- fabro:pr-doctor head=abc pushed=" + "d" * 40 + " run=R -->")
    assert marker is not None
    assert marker.review == "fire"
    assert marker.wants_review is True


def test_a_marker_with_an_unreadable_review_value_reads_as_fire():
    marker = parse_marker("<!-- fabro:pr-doctor head=abc pushed=none review=maybe run=R -->")
    assert marker is not None
    assert marker.review == "fire"


def test_a_pushed_none_marker_is_recognised():
    marker = parse_marker("<!-- fabro:pr-doctor head=abc pushed=none review=skip run=R -->")
    assert marker is not None
    assert marker.pushed_nothing is True


@pytest.mark.parametrize(
    "line",
    [None, "", "<!-- fabro:precheck verdict=done -->", "not a marker at all"],
)
def test_anything_but_a_doctor_marker_parses_to_none(line):
    assert parse_marker(line) is None


# --- eligibility --------------------------------------------------------------------


def view(**overrides) -> PrView:
    base = dict(
        repo="o/r",
        number=7,
        state="open",
        report="blocked",
        reason="There are unfixed findings; a human must merge this PR.",
        labels=(),
        marker=None,
        report_at="2026-10-29T10:00:00Z",
        report_items=True,
        head_committed_at="2026-10-29T09:59:50Z",
    )
    base.update(overrides)
    return PrView(**base)


ACK_REVIEW = (
    "The review did not end in a mergeable state."
)


def test_a_blocked_pr_with_a_fixable_reason_is_eligible():
    verdict = eligibility(view())
    assert verdict == eligibility(view())  # frozen dataclass, same inputs
    assert verdict.eligible is True
    assert verdict.category == FINDINGS


@pytest.mark.parametrize(
    "overrides,why",
    [
        ({"state": "merged"}, "the PR is merged, not open"),
        ({"state": "closed"}, "the PR is closed, not open"),
        ({"marker": "<!-- fabro:pr-doctor head=a pushed=none review=skip run=R -->"},
         "it already has a doctor marker, so its one attempt is spent"),
        ({"report": "queued"}, "its last report is queued"),
        ({"report": None}, "its last report is missing"),
        ({"labels": ("do-not-merge",)}, "it carries do-not-merge"),
        ({"reason": None}, "its report carries no **Reason:** line"),
        ({"reason": "retryable: standards failed (transient_infra)"},
         "the reason is retryable: it wants a re-review, not a fix"),
        ({"reason": "The PR title is not a Conventional Commit, so it cannot be auto-merged."},
         "the reason is one the doctor has no fix for"),
        ({"reason": "Diff hygiene blocks the merge: config_changed=1."},
         "the diff-hygiene block is config_changed, which a human merges by design"),
        ({"reason": ACK_REVIEW, "report_items": False},
         "the review's report lists no concrete leftover to fix"),
        ({"reason": ACK_REVIEW, "report_items": None},
         "the review's report lists no concrete leftover to fix"),
        ({"head_committed_at": "2026-10-29T10:00:01Z"},
         "the head was committed after the report, so someone pushed since"),
    ],
)
def test_every_exclusion_has_its_reason(overrides, why):
    verdict = eligibility(view(**overrides))
    assert not verdict.eligible
    assert verdict.why == why


def test_a_doctor_job_that_already_exists_blocks_a_second_one():
    """D13's one attempt, at the queue level: a run that died before it could post a marker
    still does not come back."""
    verdict = eligibility(view(), has_job=True)
    assert not verdict.eligible
    assert "a doctor job already exists" in verdict.why


def test_needs_human_is_eligible_too():
    """The merge phase labels every needs-human PR `ai-review-needs-human`, and that label
    must not exclude it (task 06, *Rules*)."""
    verdict = eligibility(
        view(report="needs-human", labels=("ai-review-needs-human",))
    )
    assert verdict.eligible is True


def test_the_review_category_needs_a_concrete_leftover():
    assert eligibility(view(reason=ACK_REVIEW, report_items=True)).eligible is True
    assert eligibility(view(reason=ACK_REVIEW, report_items=False)).eligible is False


def test_no_age_limit():
    """Decision 7: there is no age limit on a PR. A year-old blocked PR is eligible."""
    ancient = eligibility(
        view(
            report_at="2025-01-01T00:00:00Z",
            head_committed_at="2025-01-01T00:00:00Z",
            reason="There are unfixed findings; a human must merge this PR.",
        )
    )
    assert ancient.eligible is True


def test_eligibility_built_from_stored_facts_matches_the_row_form():
    facts = PrFacts(
        state="open",
        created_at="2026-10-29T09:00:00Z",
        merged_at=None,
        closed_at=None,
        labels=("ai-review-needs-human",),
        additions=1,
        deletions=1,
        commits=2,
        operator_commits=0,
        report="blocked",
        reason="There are unfixed findings; a human must merge this PR.",
        queue_approved=False,
        queue_handoff=False,
        head_sha="a" * 40,
        head_committed_at="2026-10-29T09:59:50Z",
        report_at="2026-10-29T10:00:00Z",
        report_items=True,
        doctor_marker=None,
    )
    from_facts = view_from_facts("o/r", 7, facts)
    from_row = view_from_facts(
        "o/r",
        7,
        PrFacts(
            **{
                **facts.__dict__,
                "labels": ("ai-review-needs-human",),
            }
        ),
    )
    assert from_facts == from_row


# --- staleness (decision 6) ---------------------------------------------------------


def test_the_head_committed_before_the_report_is_current():
    assert is_stale(view(head_committed_at="2026-10-29T09:59:52Z")) is False


def test_the_head_committed_after_the_report_is_stale():
    assert is_stale(view(head_committed_at="2026-10-29T10:00:01Z")) is True


def test_an_unreadable_timestamp_is_not_stale():
    """Refusing to run because GitHub omitted a date would hide every PR."""
    assert is_stale(view(head_committed_at=None)) is False
    assert is_stale(view(report_at=None)) is False
    assert is_stale(view(head_committed_at="not a date")) is False


def test_staleness_never_compares_shas():
    """The report's `Reviewed HEAD:` is a sandbox commit GitHub does not have, so only time
    decides. This test is the contract: two different head SHAs with a fresh report pass."""
    assert is_stale(view(head_committed_at="2026-10-29T09:59:50Z")) is False


# --- the report's concrete items (decision 1) ---------------------------------------


def test_a_report_with_findings_has_items():
    body = (
        "## AI PR review\n\n### Findings\n\n"
        "- **[error] STD-001** - `src/a.ts:12` - the guard is inverted.\n\n"
        "### Diff hygiene\n\nNo counter fired.\n"
    )
    assert report_has_items(body) is True


def test_a_report_whose_every_section_says_none_has_no_items():
    body = (
        "## AI PR review\n\n"
        "### Unmet criteria\n\n_None: the Refuter found every criterion met._\n\n"
        "### Findings\n\n_None recorded._\n\n"
        "### Fixes applied\n\n_None recorded._\n\n"
        "### Not fixed and why\n\n_None recorded._\n\n"
        "### Refuter\n\n- **met** - the endpoint works - src/a.ts:3\n"
    )
    assert report_has_items(body) is False


def test_a_task_the_loop_ran_out_on_counts_as_an_item():
    """ralph writes unpassed Tasks into `fix_result.not_fixed`, which renders under
    `### Not fixed and why`."""
    body = (
        "## AI PR review\n\n### Not fixed and why\n\n"
        "- **T3: add the retry** - not passed when the loop ran out\n"
    )
    assert report_has_items(body) is True


def test_a_refuter_defect_counts_as_an_item():
    body = (
        "## AI PR review\n\n### Unmet criteria\n\n_None: the Refuter found every criterion met._\n\n"
        "### Refuter\n\n- **defect** - src/a.ts:9 - an empty body is accepted\n"
    )
    assert report_has_items(body) is True


# --- the producer's gates (D12, C7) -------------------------------------------------


class CountingStore:
    """A store that counts every method call the producer could make.

    "Off means off" is a claim about calls, not about behaviour: the producer must not
    reach the store either, because the store is where a job would be written.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.jobs: list[dict] = []

    def background_job_exists(self, kind, repo, target) -> bool:
        self.calls.append("exists")
        return False

    def enqueue_background_job(self, **kwargs):
        self.calls.append("enqueue")
        self.jobs.append(kwargs)
        return 1


def doctor_config(**env):
    return load_config(
        TRACKED_REPOS, env={"SCHEDULER_BACKGROUND": "on", **env}
    )


def eligible_facts() -> PrFacts:
    return PrFacts(
        state="open",
        created_at="2026-10-29T09:00:00Z",
        merged_at=None,
        closed_at=None,
        labels=(),
        additions=1,
        deletions=1,
        commits=2,
        operator_commits=0,
        report="blocked",
        reason="There are unfixed findings; a human must merge this PR.",
        queue_approved=False,
        queue_handoff=False,
        head_committed_at="2026-10-29T09:59:50Z",
        report_at="2026-10-29T10:00:00Z",
        report_items=True,
    )


def test_the_switch_off_enqueues_nothing_and_touches_no_store_method():
    from fabro_scheduler.doctor import PrDoctorProducer

    store = CountingStore()
    producer = PrDoctorProducer(
        load_config(TRACKED_REPOS, env={"SCHEDULER_BACKGROUND": "off"}), store
    )
    assert producer.consider("o/r", 7, eligible_facts()) is None
    assert store.calls == []
    assert store.jobs == []


def test_a_kind_that_is_not_enabled_enqueues_nothing():
    """C7: `SCHEDULER_BG_KINDS` gates producers, so a kind left out does nothing at all."""
    from fabro_scheduler.doctor import PrDoctorProducer

    store = CountingStore()
    producer = PrDoctorProducer(
        doctor_config(SCHEDULER_BG_KINDS="box-noop"), store
    )
    assert producer.consider("o/r", 7, eligible_facts()) is None
    assert store.calls == []
    assert store.jobs == []


def test_an_enabled_producer_queues_one_priority_one_job():
    from fabro_scheduler.doctor import PrDoctorProducer

    store = CountingStore()
    producer = PrDoctorProducer(doctor_config(), store)
    assert producer.consider("o/r", 7, eligible_facts(), datetime(2026, 11, 2, tzinfo=UTC)) == 1
    assert store.jobs == [
        {
            "kind": KIND,
            "repo": "o/r",
            "target": "7",
            "inputs": {"pr_number": 7},
            "priority": 1,
            "typical_box_minutes": 40,
            "now": datetime(2026, 11, 2, tzinfo=UTC),
        }
    ]


def test_the_handoff_texts_are_the_task_strings():
    assert HANDOFF_COMMENT == (
        "The PR doctor and one follow-up review did not get this merged; "
        "a human needs to take it from here."
    )
    # A distinct sentence for the no-review case: the one above names a review that did not
    # run, and a report that describes a stopping point the run passed is the failure the
    # invariants-merge *Reporting* rows exist to prevent.
    assert HANDOFF_NO_REVIEW_COMMENT != HANDOFF_COMMENT
    assert "no follow-up review" in HANDOFF_NO_REVIEW_COMMENT
    assert ineligible("x").eligible is False
