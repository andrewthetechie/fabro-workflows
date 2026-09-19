"""`fetch_issues` and the two label writes, against a faked GitHub — `respx`, no
live network.

The three things worth pinning on the read path are the three the design leans on:
the ETag goes back verbatim, a `304` is distinguishable from "no work", and a `403`
says whether the quota is gone. On the write path there is one: a `404` from
`DELETE .../labels/{name}` is success, because the rollback path is a retry and a
retry that finds the work already done has not failed.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from fabro_scheduler.github import (
    GitHubError,
    add_label,
    fetch_issues,
    is_eligible,
    remove_label,
)

ISSUES_URL = "https://api.github.com/repos/o/a/issues"
TOKEN = "ghp_not-a-real-token"


def api_issue(number: int, labels: list[str], **extra) -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "state": "open",
        "labels": [{"name": name} for name in labels],
        **extra,
    }


# --- eligibility -----------------------------------------------------------------


def test_an_open_agent_labelled_issue_is_a_queue_item():
    assert is_eligible(api_issue(1, ["agent"])) is True


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(api_issue(1, ["agent"], pull_request={"url": "x"}), id="pull-request"),
        pytest.param(api_issue(1, ["agent", "agent-in-progress"]), id="in-progress"),
        pytest.param(api_issue(1, ["agent", "agent-stuck"]), id="stuck"),
        pytest.param(api_issue(1, ["bug"]), id="no-agent-label"),
        pytest.param(api_issue(1, ["agent"], state="closed"), id="closed"),
    ],
)
def test_these_are_not_queue_items(payload):
    assert is_eligible(payload) is False


def test_a_pull_request_is_excluded_on_the_pull_request_key_alone():
    # `GET /issues` returns pull requests. Nothing else about the object
    # distinguishes one: same `number`, same `title`, same labels.
    pr = api_issue(9, ["agent"], pull_request={"merged_at": None})
    assert is_eligible(pr) is False
    assert "pull_request" not in api_issue(9, ["agent"])


def test_a_string_label_is_still_read():
    # The API sends objects; a fixture or a future shape sending bare strings
    # should not silently look unlabelled, which would drop real work.
    assert is_eligible({"number": 1, "state": "open", "labels": ["agent"]}) is True


# --- the 200 path -----------------------------------------------------------------


@respx.mock
def test_a_200_returns_only_eligible_issues_and_the_etag():
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                api_issue(1, ["agent"]),
                api_issue(2, ["agent"], pull_request={"url": "x"}),
                api_issue(3, ["agent", "agent-in-progress"]),
                api_issue(4, ["agent", "agent-stuck"]),
                api_issue(5, ["agent", "bug"]),
            ],
            headers={"etag": '"etag-abc"', "x-ratelimit-remaining": "4999"},
        )
    )

    issues, etag, changed = fetch_issues("o/a", TOKEN, None)

    assert changed is True
    assert etag == '"etag-abc"'
    assert [issue.number for issue in issues] == [1, 5]
    assert issues[0].repo == "o/a"
    assert issues[0].title == "issue 1"
    assert issues[0].labels == frozenset({"agent"})


@respx.mock
def test_a_200_sends_the_documented_query_and_headers():
    route = respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(200, json=[], headers={"etag": '"x"'})
    )

    fetch_issues("o/a", TOKEN, None)

    request = route.calls[0].request
    assert request.url.params["labels"] == "agent"
    assert request.url.params["state"] == "open"
    assert request.url.params["per_page"] == "100"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/vnd.github+json"
    assert "if-none-match" not in request.headers


@respx.mock
def test_first_seen_is_the_observation_time_not_something_from_the_payload():
    before = datetime.now(UTC)
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[api_issue(1, ["agent"], created_at="2020-01-01T00:00:00Z")],
            headers={},
        )
    )

    issues, _, _ = fetch_issues("o/a", TOKEN, None)

    assert before <= issues[0].first_seen <= datetime.now(UTC)
    assert issues[0].first_seen.utcoffset() == timedelta(0)


@respx.mock
def test_a_200_without_an_etag_returns_none():
    # The next call is then unconditional. Better than inventing one.
    respx.get(ISSUES_URL).mock(return_value=httpx.Response(200, json=[]))
    _, etag, changed = fetch_issues("o/a", TOKEN, None)
    assert changed is True
    assert etag is None


# --- the 304 path -----------------------------------------------------------------


@respx.mock
def test_a_304_is_not_an_empty_queue():
    route = respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            304, headers={"x-ratelimit-remaining": "4999", "x-ratelimit-used": "1"}
        )
    )

    issues, etag, changed = fetch_issues("o/a", TOKEN, '"etag-abc"')

    assert changed is False
    # Empty *and* distinguishable from "no work" by the changed flag alone.
    assert issues == []
    assert etag == '"etag-abc"'
    assert route.calls[0].request.headers["if-none-match"] == '"etag-abc"'


@respx.mock
def test_a_304_whose_response_omits_the_etag_keeps_the_one_we_sent():
    # GitHub normally echoes it. If it ever does not, the one we sent is still
    # current by definition, and dropping it would make the next call
    # unconditional and start burning quota.
    respx.get(ISSUES_URL).mock(return_value=httpx.Response(304))
    _, etag, changed = fetch_issues("o/a", TOKEN, '"etag-abc"')
    assert (etag, changed) == ('"etag-abc"', False)


@respx.mock
def test_a_304_that_echoes_a_different_form_of_the_etag_keeps_the_one_we_sent():
    # Observed live: sending `W/"abc"` comes back as `"abc"`. Both work (GitHub
    # compares weakly), but storing the echo would churn the value for no reason.
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(304, headers={"etag": '"etag-abc"'})
    )
    _, etag, _ = fetch_issues("o/a", TOKEN, 'W/"etag-abc"')
    assert etag == 'W/"etag-abc"'


@respx.mock
def test_a_304_on_an_unconditional_request_uses_the_etag_it_reports():
    # Not reachable in practice — nothing to match against means no 304 — but if it
    # happens, keeping the response's ETag is strictly better than keeping None.
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(304, headers={"etag": '"etag-abc"'})
    )
    _, etag, _ = fetch_issues("o/a", TOKEN, None)
    assert etag == '"etag-abc"'


@respx.mock
def test_the_200_then_304_pair_is_logged_with_the_same_ratelimit_remaining(caplog):
    route = respx.get(ISSUES_URL)
    route.side_effect = [
        httpx.Response(
            200, json=[api_issue(1, ["agent"])], headers={"x-ratelimit-remaining": "4999"}
        ),
        httpx.Response(304, headers={"x-ratelimit-remaining": "4999"}),
    ]

    with caplog.at_level(logging.INFO, logger="fabro_scheduler.github"):
        _, etag, changed = fetch_issues("o/a", TOKEN, None)
        assert changed is True
        _, _, changed = fetch_issues("o/a", TOKEN, etag)
        assert changed is False

    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "fabro_scheduler.github"
    ]
    assert len(lines) == 2
    assert " 200 " in lines[0] and "ratelimit-remaining=4999" in lines[0]
    assert " 304 " in lines[1] and "ratelimit-remaining=4999" in lines[1]


# --- failures ---------------------------------------------------------------------


@respx.mock
def test_a_403_with_no_remaining_says_the_quota_is_gone():
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0"},
        )
    )

    with pytest.raises(GitHubError) as caught:
        fetch_issues("o/a", TOKEN, None)

    assert caught.value.status_code == 403
    assert caught.value.remaining == "0"
    assert "rate limit" in str(caught.value)
    assert "x-ratelimit-remaining=0" in str(caught.value)


@respx.mock
def test_a_403_with_remaining_is_a_permission_problem_not_a_quota_one():
    # They are the same status code and completely different repairs, which is the
    # only reason the header is carried this far.
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(
            403,
            json={"message": "Resource not accessible by integration"},
            headers={"x-ratelimit-remaining": "4971"},
        )
    )

    with pytest.raises(GitHubError) as caught:
        fetch_issues("o/a", TOKEN, None)

    message = str(caught.value)
    assert "not a rate limit" in message
    assert "issues: read" in message
    assert "4971" in message


@respx.mock
def test_a_404_names_the_repo_and_the_status():
    respx.get(ISSUES_URL).mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    with pytest.raises(GitHubError) as caught:
        fetch_issues("o/a", TOKEN, None)
    assert "o/a" in str(caught.value)
    assert "404" in str(caught.value)


@respx.mock
def test_a_connection_error_is_a_GitHubError_not_an_httpx_one():
    respx.get(ISSUES_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(GitHubError, match="request failed"):
        fetch_issues("o/a", TOKEN, None)


@respx.mock
def test_a_missing_token_never_reaches_the_network():
    route = respx.get(ISSUES_URL).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        fetch_issues("o/a", "", None)
    assert not route.called


def test_the_token_never_appears_in_an_error_message():
    # Belt and braces: this string reaches a log and a stderr line, so a token in
    # it would be a token in `docker compose logs`.
    sentinel = "ghp_sentinel-do-not-log"
    with pytest.raises(GitHubError) as caught:
        fetch_issues("o/a", "", None)
    assert sentinel not in str(caught.value)


# --- label writes ------------------------------------------------------------------
#
# The only mutating calls this module makes, and the reason `EXCLUDED_LABELS` and
# `REQUIRED_LABEL` are not just constants: a dispatch adds `agent-in-progress`,
# removes `agent`, and has to be able to undo both without treating the undo as a
# failure when it finds nothing to undo.

MARK_URL = "https://api.github.com/repos/o/a/issues/7/labels"
REMOVE_URL = "https://api.github.com/repos/o/a/issues/7/labels/agent"


@respx.mock
def test_add_label_posts_the_label():
    route = respx.post(MARK_URL).mock(return_value=httpx.Response(200, json=[]))

    add_label("o/a", 7, "agent-in-progress", TOKEN)

    assert route.called
    request = route.calls.last.request
    assert json.loads(request.content) == {"labels": ["agent-in-progress"]}
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
def test_remove_label_treats_a_404_as_success():
    # GitHub answers 404 when the label is not on the issue. The caller's intent is
    # "this label must not be there", which is equally true either way — and the
    # rollback path *is* a retry, so a 404 read as failure would report a failure
    # for work that is already done.
    route = respx.delete(REMOVE_URL).mock(
        return_value=httpx.Response(404, json={"message": "Label does not exist"})
    )

    remove_label("o/a", 7, "agent", TOKEN)  # does not raise

    assert route.called


@respx.mock
def test_remove_label_url_encodes_the_label():
    route = respx.delete(
        "https://api.github.com/repos/o/a/issues/7/labels/a%2Fb%20c"
    ).mock(return_value=httpx.Response(200, json=[]))

    remove_label("o/a", 7, "a/b c", TOKEN)

    assert route.called


@respx.mock
def test_a_refused_label_write_is_a_GitHubError_naming_the_status_and_the_scope():
    respx.post(MARK_URL).mock(
        return_value=httpx.Response(
            403,
            json={"message": "Resource not accessible by integration"},
            headers={"x-ratelimit-remaining": "4971"},
        )
    )

    with pytest.raises(GitHubError) as caught:
        add_label("o/a", 7, "agent-in-progress", TOKEN)

    message = str(caught.value)
    assert "403" in message
    assert "issues: write" in message
    assert "4971" in message


@respx.mock
def test_a_connection_error_on_a_label_write_is_a_GitHubError():
    respx.post(MARK_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(GitHubError, match="request failed|failed"):
        add_label("o/a", 7, "agent-in-progress", TOKEN)


@respx.mock
def test_a_missing_token_never_reaches_the_network_for_a_label_write():
    route = respx.post(MARK_URL).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        add_label("o/a", 7, "agent-in-progress", "")
    assert not route.called


def test_the_token_never_appears_in_a_label_write_error():
    sentinel = "ghp_sentinel-do-not-log"
    with pytest.raises(GitHubError) as caught:
        add_label("o/a", 7, "agent-in-progress", sentinel)
    assert sentinel not in str(caught.value)
