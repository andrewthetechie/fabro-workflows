"""What became of a factory PR: authorship, the merge queue's markers, the held reason."""

from __future__ import annotations

import httpx
import pytest
import respx

from fabro_scheduler.github import GITHUB_API, GitHubError
from fabro_scheduler.prfacts import (
    fetch_pr_facts,
    is_automation_commit,
    parse_pr_facts,
    report_kind,
)

BOT = "andrews-ai-agent,noreply@fabro.sh"
REPO = "andrewthetechie/lawncare-saas"


def commit(login=None, email="andrews-ai-agent@users.noreply.github.com"):
    return {
        "author": {"login": login} if login else None,
        "commit": {"author": {"email": email}},
    }


PULL_OPEN = {
    "state": "open", "merged_at": None, "created_at": "2026-10-10T01:00:00Z",
    "closed_at": None, "additions": 12, "deletions": 3,
    "labels": [{"name": "agent-authored"}, {"name": "architecture"}],
}
PULL_MERGED = {**PULL_OPEN, "state": "closed", "merged_at": "2026-10-10T07:00:00Z",
               "closed_at": "2026-10-10T07:00:00Z"}

BLOCKED = (
    "## AI PR review complete - risk 2/5\n\nReviewed HEAD: `abc`\n\nsummary\n\n"
    "### Not auto-merged\n\n**Reason:** The refuter could not confirm the work is done: "
    "not_met: it works.\n\nEverything else about this review stands."
)
NEEDS_HUMAN = "## AI PR review stopped - human needed\n\nReviewed HEAD: `abc`\n\n**Reason:** unspecified\n"
QUEUED = (
    "<!-- fabro:merge-queue approved head=" + "a" * 40 + " run=r1 -->\n"
    "## AI PR review complete - queued to merge on green - risk 1/5\n\nReviewed HEAD: `abc`\n"
)
HANDOFF = "<!-- fabro:merge-queue handoff head=" + "a" * 40 + " run=r2 -->\nHanded off: CI is red."


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("## Merged automatically - risk 1/5\n\nx", "merged"),
        (NEEDS_HUMAN, "needs-human"),
        (QUEUED, "queued"),
        (BLOCKED, "blocked"),
        ("## AI PR review complete - risk 1/5\n\nall good", "complete"),
        ("LGTM, merging", None),
        (HANDOFF, None),
        ("", None),
    ],
)
def test_report_kind(body, kind):
    assert report_kind(body) == kind


def test_automation_commit_by_login_or_noreply_email():
    assert is_automation_commit(commit(login=BOT), BOT)
    assert is_automation_commit(commit(login="ANDREWS-AI-AGENT"), BOT)
    assert is_automation_commit(commit(), BOT)  # no linked account, noreply address
    assert is_automation_commit(commit(email="123+andrews-ai-agent@users.noreply.github.com"), BOT)
    assert not is_automation_commit(commit(login="andrewthetechie", email="me@example.com"), BOT)
    assert not is_automation_commit(commit(email="someone@example.com"), BOT)


def test_the_pre_identity_fabro_address_is_the_automation():
    assert is_automation_commit(commit(email="noreply@fabro.sh"), BOT)
    assert is_automation_commit(commit(email="NoReply@Fabro.sh"), BOT)
    assert not is_automation_commit(commit(email="noreply@example.com"), BOT)
    assert not is_automation_commit(commit(email="noreply@fabro.sh"), "andrews-ai-agent")


def test_unconfigured_author_counts_no_operator_commits():
    assert is_automation_commit(commit(email="someone@example.com"), "")


def test_open_pr_held_by_the_refuter():
    facts = parse_pr_facts(
        PULL_OPEN,
        [commit(), commit(login="andrewthetechie", email="me@example.com"), commit()],
        [{"body": "a human note"}, {"body": BLOCKED}],
        BOT,
    )
    assert facts.state == "open"
    assert facts.labels == ("agent-authored", "architecture")
    assert facts.commits == 3
    assert facts.operator_commits == 1
    assert facts.report == "blocked"
    assert facts.reason.startswith("The refuter could not confirm")
    assert not facts.queue_approved
    assert facts.additions == 12


def test_the_last_report_wins_and_a_clean_one_clears_the_reason():
    facts = parse_pr_facts(
        PULL_MERGED, [], [{"body": BLOCKED}, {"body": "## Merged automatically - risk 1/5"}], BOT
    )
    assert facts.state == "merged"
    assert facts.report == "merged"
    assert facts.reason is None


def test_queue_markers_handoff_after_approval():
    facts = parse_pr_facts(PULL_MERGED, [], [{"body": QUEUED}, {"body": HANDOFF}], BOT)
    assert facts.queue_approved and facts.queue_handoff
    assert facts.report == "queued"


def test_a_later_approval_clears_an_earlier_handoff():
    facts = parse_pr_facts(
        PULL_MERGED, [], [{"body": QUEUED}, {"body": HANDOFF}, {"body": QUEUED}], BOT
    )
    assert facts.queue_approved and not facts.queue_handoff


def test_closed_unmerged():
    facts = parse_pr_facts({**PULL_OPEN, "state": "closed"}, [], [], BOT)
    assert facts.state == "closed"
    assert facts.report is None


@respx.mock
def test_fetch_pages_through_commits_and_comments():
    base = f"{GITHUB_API}/repos/{REPO}"
    respx.get(f"{base}/pulls/7").mock(return_value=httpx.Response(200, json=PULL_MERGED))
    # The page-2 route first: respx matches `params` as a subset, so the page-1
    # route would also match page 2.
    respx.get(f"{base}/pulls/7/commits", params={"per_page": "100", "page": "2"}).mock(
        return_value=httpx.Response(200, json=[commit(email="me@example.com")])
    )
    respx.get(f"{base}/pulls/7/commits", params={"per_page": "100"}).mock(
        return_value=httpx.Response(
            200,
            json=[commit()] * 100,
            headers={"link": f'<{base}/pulls/7/commits?per_page=100&page=2>; rel="next"'},
        )
    )
    respx.get(f"{base}/issues/7/comments").mock(
        return_value=httpx.Response(200, json=[{"body": QUEUED}])
    )
    facts = fetch_pr_facts(REPO, 7, "ghp_test", BOT)
    assert facts.commits == 101
    assert facts.operator_commits == 1
    assert facts.queue_approved
    assert facts.state == "merged"


@respx.mock
def test_fetch_raises_on_a_non_200():
    respx.get(f"{GITHUB_API}/repos/{REPO}/pulls/7").mock(
        return_value=httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )
    with pytest.raises(GitHubError) as raised:
        fetch_pr_facts(REPO, 7, "ghp_test", BOT)
    assert raised.value.status_code == 403


def test_fetch_without_a_token_raises():
    with pytest.raises(GitHubError):
        fetch_pr_facts(REPO, 7, " ", BOT)
