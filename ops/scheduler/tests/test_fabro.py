"""The fabro client: the three calls, their error shapes, and one dispatch.

HTTP is faked with `respx`; the `.fabro` tree is the `fake_checkout` fixture, so
nothing here reaches GitHub, git or the live server. Everything asserted about a
request body or an error message was read off the live API on 2026-09-18 and is
recorded in `docs/scheduler/07-fabro-client.md`.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx

from fabro_scheduler.fabro import (
    DEFAULT_BRANCH,
    FabroClient,
    FabroError,
    RunNotStarted,
    build_run_intent,
    create_run,
    get_run,
    register_version,
    start_run,
    status_kind,
)

API = "http://10.10.0.32:32276/api/v1"
TOKEN = "dev-token-do-not-echo"
VERSION_ID = "f" * 64


def intent(issue_number: int = 7) -> dict:
    return build_run_intent(
        workflow_version_id=VERSION_ID,
        repo="andrewthetechie/jelly-swipe",
        issue_number=issue_number,
        coder_pool="coders-a",
        environment_id="python",
    )


def client(fake_checkout) -> FabroClient:
    return FabroClient(API, TOKEN, clone=fake_checkout.clone)


def start_ok(kind: str = "starting") -> httpx.Response:
    return httpx.Response(200, json={"id": "R", "lifecycle": {"status": {"kind": kind}}})


# --- register_version ---------------------------------------------------------------


@respx.mock
def test_register_version_returns_the_id_fabro_computed():
    route = respx.post(f"{API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )

    version_id = register_version(API, TOKEN, {"entrypoint": "x", "files": {}})

    assert version_id == VERSION_ID
    assert route.called
    assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
def test_register_version_without_an_id_in_the_body_is_an_error():
    respx.post(f"{API}/workflow-versions").mock(return_value=httpx.Response(201, json={}))

    with pytest.raises(FabroError, match="no workflow_version_id"):
        register_version(API, TOKEN, {"entrypoint": "x", "files": {}})


# --- create_run ---------------------------------------------------------------------


@respx.mock
def test_a_bad_environment_id_surfaces_fabros_422_detail():
    # The acceptance criterion for this draft: the actionable sentence, not the
    # status line. fabro answers `{"errors":[{"detail": ...}]}`.
    respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(
            422,
            json={
                "errors": [
                    {
                        "status": "422",
                        "title": "Unprocessable Entity",
                        "detail": "unknown environment id `nope`",
                    }
                ]
            },
        )
    )

    with pytest.raises(FabroError) as caught:
        create_run(API, TOKEN, intent() | {"environment_id": "nope"})

    assert "unknown environment id" in str(caught.value)
    assert caught.value.status_code == 422
    assert caught.value.detail == "unknown environment id `nope`"


@respx.mock
def test_a_flat_detail_body_is_understood_too():
    respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(400, json={"detail": "missing field `cwd`"})
    )

    with pytest.raises(FabroError, match=re.escape("missing field `cwd`")):
        create_run(API, TOKEN, intent())


@respx.mock
def test_an_html_error_page_is_quoted_rather_than_replaced_with_a_shrug():
    respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(502, text="<html>bad gateway</html>")
    )

    with pytest.raises(FabroError, match="bad gateway"):
        create_run(API, TOKEN, intent())


@respx.mock
def test_issue_number_is_sent_as_a_json_number_and_coder_pool_as_an_input():
    route = respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(
            201, json={"id": "01M2", "lifecycle": {"status": {"kind": "submitted"}}}
        )
    )

    assert create_run(API, TOKEN, intent(issue_number=7)) == "01M2"

    sent = json.loads(route.calls.last.request.content)
    assert sent["target"] == {
        "kind": "git",
        "repo": "andrewthetechie/jelly-swipe",
        "branch": DEFAULT_BRANCH,
    }
    assert sent["environment_id"] == "python"
    assert sent["args"]["inputs"] == {"issue_number": 7, "coder_pool": "coders-a"}
    # A JSON number, not the string "7": the graph guards it with a POSIX `case`.
    assert route.calls.last.request.content
    assert '"issue_number":7' in route.calls.last.request.content.decode()
    # Every label value is a string.
    assert sent["args"]["labels"] == {"source": "scheduler", "issue": "7"}


@respx.mock
def test_create_run_without_a_run_id_in_the_body_is_an_error():
    respx.post(f"{API}/runs").mock(return_value=httpx.Response(201, json={"nope": 1}))

    with pytest.raises(FabroError, match="no run id"):
        create_run(API, TOKEN, intent())


def test_build_run_intent_refuses_a_non_integer_issue_number():
    for bad in ("7", 7.0, True, 0, -1):
        with pytest.raises(ValueError):
            intent(issue_number=bad)  # type: ignore[arg-type]


# --- start_run and get_run ----------------------------------------------------------


@respx.mock
def test_start_run_returns_the_status_kind_from_the_response():
    respx.post(f"{API}/runs/R1/start").mock(return_value=start_ok("runnable"))

    assert start_run(API, TOKEN, "R1") == "runnable"


@respx.mock
def test_start_run_on_a_run_that_is_not_submitted_carries_the_409():
    respx.post(f"{API}/runs/R1/start").mock(
        return_value=httpx.Response(409, json={"detail": "run is not submitted"})
    )

    with pytest.raises(FabroError) as caught:
        start_run(API, TOKEN, "R1")

    assert caught.value.status_code == 409
    assert "not submitted" in str(caught.value)


@respx.mock
def test_get_run_returns_the_projection_unreduced():
    body = {"id": "R1", "lifecycle": {"status": {"kind": "failed", "reason": "cancelled"}}}
    respx.get(f"{API}/runs/R1").mock(return_value=httpx.Response(200, json=body))

    assert get_run(API, TOKEN, "R1") == body


def test_status_kind_reads_the_nested_kind_and_tolerates_a_bare_run():
    assert status_kind({"lifecycle": {"status": {"kind": "dead"}}}) == "dead"
    assert status_kind({"id": "R1"}) is None
    assert status_kind({}) is None


# --- errors never carry the credential ----------------------------------------------


@respx.mock
def test_the_token_is_not_in_an_error_message():
    respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(401, json={"errors": [{"detail": "Authentication required."}]})
    )

    with pytest.raises(FabroError) as caught:
        create_run(API, TOKEN, intent())

    assert TOKEN not in str(caught.value)


def test_a_credential_in_the_api_url_is_redacted_out_of_a_transport_error():
    # `FABRO_API_URL` is allowed to carry userinfo, and an httpx transport error
    # quotes the URL it failed on. The message ends up in an HTTP response body.
    error = FabroError("POST /runs failed: http://user:sekrit@fabro.test/api/v1")
    assert "sekrit" not in str(error)
    assert "***@fabro.test" in str(error)


@respx.mock
def test_an_unset_token_is_refused_before_a_request_is_made():
    route = respx.post(f"{API}/runs").mock(return_value=httpx.Response(201, json={"id": "x"}))

    with pytest.raises(FabroError, match="FABRO_API_TOKEN is not set"):
        create_run(API, "", intent())

    assert not route.called


# --- one dispatch, end to end over the three calls ----------------------------------


@respx.mock
def test_dispatch_registers_the_version_then_creates_and_starts_the_run(fake_checkout):
    register = respx.post(f"{API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    create = respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(201, json={"id": "R1", "lifecycle": {"status": {"kind": "submitted"}}})
    )
    respx.post(f"{API}/runs/R1/start").mock(return_value=start_ok("runnable"))

    result = client(fake_checkout).dispatch(
        repo="andrewthetechie/jelly-swipe",
        issue_number=7,
        coder_pool="coders-a",
        environment_id="python",
    )

    assert result.run_id == "R1"
    assert result.status == "runnable"
    assert result.workflow_version_id == VERSION_ID
    assert result.commit_sha == fake_checkout.sha
    assert result.version_reused is False
    assert register.call_count == 1
    assert create.call_count == 1

    # The register call carried the whole `.fabro` tree, entrypoint included.
    payload = json.loads(register.calls.last.request.content)
    assert payload["entrypoint"] == "workflows/backlog/workflow.fabro"
    assert "workflows/_shared/review-merge/review-merge.fabro" in payload["files"]


@respx.mock
def test_two_dispatches_on_an_unchanged_main_register_the_version_once(fake_checkout):
    register = respx.post(f"{API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    ids = iter(["R1", "R2"])
    create = respx.post(f"{API}/runs").mock(
        side_effect=lambda request: httpx.Response(
            201, json={"id": next(ids), "lifecycle": {"status": {"kind": "submitted"}}}
        )
    )
    starts = respx.post(url__regex=rf"{re.escape(API)}/runs/R[12]/start").mock(
        return_value=start_ok("runnable")
    )

    fabro = client(fake_checkout)
    first = fabro.dispatch(
        repo="andrewthetechie/jelly-swipe", issue_number=1, coder_pool="coders-a", environment_id="python"
    )
    second = fabro.dispatch(
        repo="andrewthetechie/writers-app", issue_number=2, coder_pool="coders-b", environment_id="rust-node"
    )

    assert register.call_count == 1  # the cached id was reused, not re-POSTed
    assert create.call_count == 2
    assert starts.call_count == 2
    assert (first.run_id, second.run_id) == ("R1", "R2")
    assert first.version_reused is False
    assert second.version_reused is True
    # The clone still runs every time: the sha is the cache key, so it has to be
    # read from the tree the files came from.
    assert len(fake_checkout.clones) == 2


@respx.mock
def test_a_moved_main_registers_a_new_version(fake_checkout):
    register = respx.post(f"{API}/workflow-versions").mock(
        side_effect=[
            httpx.Response(201, json={"workflow_version_id": "a" * 64}),
            httpx.Response(201, json={"workflow_version_id": "b" * 64}),
        ]
    )
    respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(201, json={"id": "R1", "lifecycle": {"status": {"kind": "submitted"}}})
    )
    respx.post(f"{API}/runs/R1/start").mock(return_value=start_ok())

    fabro = client(fake_checkout)
    first = fabro.dispatch(
        repo="o/r", issue_number=1, coder_pool="coders-a", environment_id="python"
    )
    fake_checkout.sha = "b" * 40
    second = fabro.dispatch(
        repo="o/r", issue_number=2, coder_pool="coders-a", environment_id="python"
    )

    assert register.call_count == 2
    assert first.workflow_version_id == "a" * 64
    assert second.workflow_version_id == "b" * 64
    assert second.version_reused is False


@respx.mock
def test_a_start_failure_names_the_run_it_left_in_submitted(fake_checkout):
    respx.post(f"{API}/workflow-versions").mock(
        return_value=httpx.Response(201, json={"workflow_version_id": VERSION_ID})
    )
    respx.post(f"{API}/runs").mock(
        return_value=httpx.Response(201, json={"id": "R1", "lifecycle": {"status": {"kind": "submitted"}}})
    )
    respx.post(f"{API}/runs/R1/start").mock(
        return_value=httpx.Response(409, json={"detail": "run was not in submitted"})
    )

    with pytest.raises(RunNotStarted) as caught:
        client(fake_checkout).dispatch(
            repo="o/r", issue_number=1, coder_pool="coders-a", environment_id="python"
        )

    assert caught.value.run_id == "R1"
    assert "R1" in str(caught.value)
    assert "submitted" in str(caught.value)
    assert caught.value.status_code == 409


def test_configured_reflects_the_token_without_ever_reporting_it(fake_checkout):
    assert FabroClient(API, TOKEN, clone=fake_checkout.clone).configured is True
    assert FabroClient(API, "  ", clone=fake_checkout.clone).configured is False
