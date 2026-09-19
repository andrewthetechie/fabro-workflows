"""The fabro API client: register a version, create a run, start it.

Three calls, and the third is not optional. `POST /runs` creates a run in
`submitted` and does **not** start it; the automation path auto-starts, the intent
path does not, and a `submitted` run sits there indefinitely reporting nothing.
Verified against `0.354.0-nightly.0` by leaving one alone for 90 seconds. So
"dispatch" here is `register_version` → `create_run` → `start_run`, in that order,
with `run_id` only meaningful after the third.

Everything in this module was read out of the live API on 2026-09-18
(`http://10.10.0.32:32276/api/v1`, `0.354.0-nightly.0`) and is recorded in
`docs/scheduler/07-fabro-client.md`. The two shapes most likely to be got wrong:

* **`args.inputs` values are scalars only** — string, number, integer, boolean.
  `RunIntentArgs.inputs` is `additionalProperties: {anyOf: [string, number,
  integer, boolean]}`; anything else is `422 run_intent_invalid`. A count that
  reaches a POSIX `case` guard inside the graph must additionally be a JSON
  **number**, not a string, which is why `issue_number` is validated as an `int`
  here rather than passed through as whatever the caller had.
* **`args.labels` values are strings.** Always stringified, never assumed.

Every non-2xx raises `FabroError` carrying the HTTP status and the first error
`detail`, because fabro's 422s put the actionable sentence there
(`{"errors":[{"detail":"unknown environment id 'nope'"}]}`) and the status line
alone does not say which field was wrong.

**The token lives here and nowhere else.** `FabroClient` is a plain class, not a
dataclass, so its `repr` is the class name and an address and cannot print the
credential; and every message this module builds passes through `_redact`, so a
`FABRO_API_URL` carrying `user:password@` cannot leak through an error either.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass

import httpx

from .workflow_version import (
    BACKLOG_ENTRYPOINT,
    WORKFLOWS_REF,
    WORKFLOWS_REPO,
    FabroTree,
    VersionCache,
    build_version_payload,
    clone_fabro_tree,
)

log = logging.getLogger(__name__)

# Long enough for a 2 MiB registration over a LAN, short enough that a wedged
# fabro does not hold a request thread for a minute. `create_run` and `start_run`
# are small JSON calls and get the shorter of the two.
DEFAULT_TIMEOUT_SECONDS = 30.0
REGISTER_TIMEOUT_SECONDS = 90.0

# Every repo in `repos.toml` targets `main` today, as does every automation on the
# server. The branch is not in `repos.toml` because draft 04 fixed that schema and
# nothing since has needed it; a repo with a different default branch would need
# the row to carry one.
DEFAULT_BRANCH = "main"

USERINFO = re.compile(r"(?<=://)[^/@\s]+:[^/@\s]+@")


class FabroError(RuntimeError):
    """A fabro API call that did not succeed.

    Carries the HTTP status and fabro's own `detail` when there was one. The
    message is what a caller logs or returns; nothing in it is ever the token.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(_redact(message))
        self.status_code = status_code
        self.detail = None if detail is None else _redact(detail)


class RunNotStarted(FabroError):
    """The run was created and then could not be started.

    The one failure in the sequence that leaves something behind. A run in
    `submitted` never executes and never reports anything, so `run_id` is carried
    out to the caller: it is the handle needed to cancel it, and the thing draft
    09's recovery has to reconcile.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        status_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, detail=detail)
        self.run_id = run_id


def register_version(
    api: str,
    token: str,
    payload: Mapping[str, object],
    *,
    client: httpx.Client | None = None,
    timeout: float = REGISTER_TIMEOUT_SECONDS,
) -> str:
    """`POST /workflow-versions` → the workflow version id.

    Content-addressed and idempotent: identical bytes return the identical id, so
    a redundant call costs one round trip and creates nothing. `201` is the
    documented success; any other 2xx is accepted rather than second-guessed.
    """
    body = _request(
        "POST",
        api,
        "/workflow-versions",
        token,
        json=payload,
        client=client,
        timeout=timeout,
    )
    version_id = body.get("workflow_version_id")
    if not isinstance(version_id, str) or not version_id:
        raise FabroError(
            "POST /workflow-versions returned no workflow_version_id: "
            f"{_summarise(body)}"
        )
    return version_id


def create_run(
    api: str,
    token: str,
    intent: Mapping[str, object],
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """`POST /runs` → the new run id. Does **not** start it."""
    body = _request(
        "POST", api, "/runs", token, json=intent, client=client, timeout=timeout
    )
    run_id = body.get("id")
    if not isinstance(run_id, str) or not run_id:
        raise FabroError(f"POST /runs returned no run id: {_summarise(body)}")
    return run_id


def start_run(
    api: str,
    token: str,
    run_id: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """`POST /runs/{id}/start` → the run's status kind as fabro reports it.

    Read back out of the response rather than assumed: it is the first evidence
    the scheduler has that the run left `submitted`.
    """
    body = _request(
        "POST",
        api,
        f"/runs/{run_id}/start",
        token,
        client=client,
        timeout=timeout,
    )
    kind = status_kind(body)
    if kind is None:
        raise FabroError(
            f"POST /runs/{run_id}/start returned no lifecycle.status.kind: "
            f"{_summarise(body)}"
        )
    return kind


def get_run(
    api: str,
    token: str,
    run_id: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """`GET /runs/{id}` → the run projection, as a dict.

    Returned whole rather than reduced to a status: the useful fields differ by
    caller — `.lifecycle.status.kind` for terminal detection,
    `.lifecycle.status.reason` for failure classification — and projecting here
    would be a second definition of the run shape to keep in step with fabro's.
    """
    return _request("GET", api, f"/runs/{run_id}", token, client=client, timeout=timeout)


def status_kind(run: Mapping[str, object]) -> str | None:
    """`.lifecycle.status.kind` from a run projection, or `None` if it is absent.

    The terminal kinds are exactly `succeeded`, `failed` and `dead` — eleven
    variants exist and `is_terminal()` matches those three. There is no
    `cancelled` and no `errored` kind: cancelling a run produces
    `{"kind":"failed","reason":"cancelled"}`.
    """
    lifecycle = run.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        return None
    status = lifecycle.get("status")
    if not isinstance(status, Mapping):
        return None
    kind = status.get("kind")
    return kind if isinstance(kind, str) and kind else None


def last_failure_category(
    api: str,
    token: str,
    run_id: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """The failure `category` of a terminal run, or `None` if it cannot be read.

    The category is **not** in the run projection (finding 10); it lives only in
    the `run.failed` event, at `properties.failure.detail.category`. Draft 09's
    requeue predicate keys on it, so a classification step has to read it from
    the tail: `GET /runs/{id}/events` with `order=desc` so the failure (second
    from last) is in the first page.

    Returns `None` for a run with no `run.failed` event or no readable category
    — the requeue rule treats a missing category as *not* infra-shaped, which is
    the fail-toward-dropping direction (Risk 4).
    """
    body = _request(
        "GET",
        api,
        f"/runs/{run_id}/events",
        token,
        params={"limit": 100, "order": "desc"},
        client=client,
        timeout=timeout,
    )
    events = body.get("data") if isinstance(body, Mapping) else body
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "run.failed":
            continue
        properties = event.get("properties")
        if not isinstance(properties, Mapping):
            continue
        failure = properties.get("failure")
        if not isinstance(failure, Mapping):
            continue
        detail = failure.get("detail")
        if isinstance(detail, Mapping):
            category = detail.get("category")
            if isinstance(category, str) and category:
                return category
        category = failure.get("category")
        if isinstance(category, str) and category:
            return category
    return None


# The `status` filter on `GET /runs` takes `BoardColumn`, which is not the status
# *kind*: eleven kinds fold onto nine columns (`submitted` arrives under `pending`,
# verified live on 2026-09-19; `starting` under `initializing`, `paused` under
# `blocked`). These are the columns a run that is still holding work can be in.
# `succeeded` and `failed` are the terminal two; `dead` has no column at all, which
# is harmless because it is terminal too. `removing` is in the list on purpose — it
# is not in `is_terminal`'s set, so a run in it still counts as live, and every
# reading here fails toward "someone is working this".
ACTIVE_BOARD_COLUMNS = (
    "pending",
    "runnable",
    "initializing",
    "running",
    "blocked",
    "removing",
)

# `GET /runs` clamps `page[limit]` at 100 and ignores a bare `limit` (finding 11).
# With `max_concurrent_runs` at 4 plus whatever sits on a human gate, one page is
# already generous; the cap is here so a server that answers `has_more` forever
# cannot spin this call.
RUNS_PAGE_SIZE = 100
MAX_ACTIVE_RUN_PAGES = 10


def list_active_runs(
    api: str,
    token: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict]:
    """Every run fabro currently considers live, across every repo.

    `GET /runs?status=...` filtered to `ACTIVE_BOARD_COLUMNS`. Paged with the
    **bracketed** `page[limit]`/`page[offset]` that this endpoint honours — a bare
    `limit=100` is silently clamped to 20 and the response then looks like a
    20-run store (finding 11) — and continued while `meta.has_more` says to.
    """
    runs: list[dict] = []
    offset = 0
    for _ in range(MAX_ACTIVE_RUN_PAGES):
        body = _request(
            "GET",
            api,
            "/runs",
            token,
            params=[
                *(("status", column) for column in ACTIVE_BOARD_COLUMNS),
                ("page[limit]", RUNS_PAGE_SIZE),
                ("page[offset]", offset),
            ],
            client=client,
            timeout=timeout,
        )
        page = body.get("data")
        if not isinstance(page, list):
            raise FabroError(
                f"GET /runs returned no `data` array: {_summarise(body)}"
            )
        runs.extend(item for item in page if isinstance(item, dict))

        meta = body.get("meta")
        if not (isinstance(meta, Mapping) and meta.get("has_more")):
            return runs
        offset += len(page)
        if not page:
            # `has_more` with an empty page would otherwise loop to the cap.
            return runs

    log.warning(
        "fabro: stopped listing active runs after %d pages; treating the first "
        "%d as the whole set",
        MAX_ACTIVE_RUN_PAGES,
        len(runs),
    )
    return runs


def issue_claim(run: Mapping[str, object]) -> tuple[str, int] | None:
    """`(repo, issue_number)` the run says it is working, or `None`.

    Read from `labels.issue` and `repository.name`, both of which are in the list
    projection (verified live on 2026-09-19 against run
    `01M2VHAGXNM9JNEY71085HV82Y`: `labels` is `{"issue": "350", "source":
    "scheduler"}`). `args.labels` values are strings, so the number is parsed back.

    Every path that creates a `backlog` run stamps that label — the scheduler with
    `source: scheduler` and `ops/fabro-fire-backlog.sh` with `source: manual` — so a
    run without one is not attributable to an issue and returns `None` rather than
    a guess.
    """
    repository = run.get("repository")
    repo = repository.get("name") if isinstance(repository, Mapping) else None
    labels = run.get("labels")
    number = labels.get("issue") if isinstance(labels, Mapping) else None
    if not isinstance(repo, str) or not repo:
        return None
    try:
        return repo, int(str(number))
    except (TypeError, ValueError):
        return None


def build_run_intent(
    *,
    workflow_version_id: str,
    repo: str,
    issue_number: int,
    coder_pool: str,
    environment_id: str,
    branch: str = DEFAULT_BRANCH,
    labels: Mapping[str, str] | None = None,
) -> dict:
    """The `RunIntent` body for one `backlog` run.

    `issue_number` is checked to be a real `int` here rather than left to whatever
    the caller had. The graph guards it with a POSIX `case "$N" in *[!0-9]*)`, so
    a string would either fail that guard or, worse, match it by accident; a JSON
    number is the shape that cannot carry shell syntax.
    """
    if not isinstance(issue_number, int) or isinstance(issue_number, bool):
        raise ValueError(
            f"issue_number must be an integer, got {type(issue_number).__name__}"
        )
    if issue_number <= 0:
        raise ValueError(f"issue_number must be positive, got {issue_number}")

    sent_labels = {"source": "scheduler", "issue": str(issue_number)}
    if labels:
        sent_labels.update({str(key): str(value) for key, value in labels.items()})

    return {
        "workflow_version_id": workflow_version_id,
        "target": {"kind": "git", "repo": repo, "branch": branch},
        "args": {
            "inputs": {
                "issue_number": issue_number,
                "coder_pool": coder_pool,
            },
            "labels": sent_labels,
        },
        "environment_id": environment_id,
    }


@dataclass(frozen=True)
class DispatchResult:
    """What one successful dispatch produced."""

    run_id: str
    workflow_version_id: str
    commit_sha: str
    version_reused: bool
    status: str  # lifecycle.status.kind, as of the start call


class FabroClient:
    """Registers the workflow version, then creates and starts one run.

    Holds the API token and the version cache, and nothing else. One instance per
    process is the intent: the cache is what makes "register once per commit of
    `main`" true across dispatches.
    """

    def __init__(
        self,
        api: str,
        token: str,
        *,
        workflows_repo: str = WORKFLOWS_REPO,
        workflows_ref: str = WORKFLOWS_REF,
        entrypoint: str = BACKLOG_ENTRYPOINT,
        branch: str = DEFAULT_BRANCH,
        register_timeout: float = REGISTER_TIMEOUT_SECONDS,
        call_timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clone: Callable[[str, str], AbstractContextManager[FabroTree]] = clone_fabro_tree,
    ) -> None:
        self._api = api
        self._token = token
        self._workflows_repo = workflows_repo
        self._workflows_ref = workflows_ref
        self._entrypoint = entrypoint
        self._branch = branch
        self._register_timeout = register_timeout
        self._call_timeout = call_timeout
        # Injectable so a test can drive the whole dispatch path without a git
        # binary or a network. Production passes `clone_fabro_tree`.
        self._clone = clone
        self._versions = VersionCache()

    @property
    def configured(self) -> bool:
        """Whether this client can call fabro at all. Never the token itself."""
        return bool(self._token.strip())

    def ensure_version(self) -> tuple[str, str, bool]:
        """`(workflow_version_id, commit_sha, reused)` for `main` right now.

        Clones first and consults the cache afterwards, which sounds backwards and
        is not: the cache key *is* the commit sha, and the only honest source for
        it is the tree the files came from. A `git ls-remote` first would save a
        ~1s shallow clone of a 300 KB repository on a path that runs once per
        dispatch — not worth a second subprocess and a second way to fail.
        """
        with self._clone(self._workflows_repo, self._workflows_ref) as tree:
            cached = self._versions.get(tree.sha)
            if cached is not None:
                log.info(
                    "fabro: %s@%s unchanged, reusing workflow version %s",
                    self._workflows_repo,
                    tree.sha[:12],
                    cached[:12],
                )
                return cached, tree.sha, True

            payload = build_version_payload(tree.directory, self._entrypoint)
            version_id = register_version(
                self._api,
                self._token,
                payload,
                timeout=self._register_timeout,
            )
            self._versions.put(tree.sha, version_id)
            log.info(
                "fabro: registered workflow version %s for %s@%s (%d file(s))",
                version_id[:12],
                self._workflows_repo,
                tree.sha[:12],
                len(payload["files"]),
            )
            return version_id, tree.sha, False

    def dispatch(
        self,
        *,
        repo: str,
        issue_number: int,
        coder_pool: str,
        environment_id: str,
    ) -> DispatchResult:
        """Create and start one `backlog` run for one issue on one coder pool.

        Raises `FabroError` before `run_id` exists (nothing was created), and
        `RunNotStarted` after it (a run is in `submitted` and needs cancelling).
        """
        version_id, sha, reused = self.ensure_version()
        intent = build_run_intent(
            workflow_version_id=version_id,
            repo=repo,
            issue_number=issue_number,
            coder_pool=coder_pool,
            environment_id=environment_id,
            branch=self._branch,
        )
        run_id = create_run(self._api, self._token, intent, timeout=self._call_timeout)
        try:
            status = start_run(
                self._api, self._token, run_id, timeout=self._call_timeout
            )
        except FabroError as exc:
            raise RunNotStarted(
                f"run {run_id} was created but could not be started, so it sits in "
                f"`submitted` and will never execute: {exc}",
                run_id=run_id,
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc

        log.info(
            "fabro: dispatched %s#%s -> run %s status=%s pool=%s env=%s version=%s reused=%s",
            repo,
            issue_number,
            run_id,
            status,
            coder_pool,
            environment_id,
            version_id[:12],
            reused,
        )
        return DispatchResult(
            run_id=run_id,
            workflow_version_id=version_id,
            commit_sha=sha,
            version_reused=reused,
            status=status,
        )

    def get_run(self, run_id: str) -> dict:
        """The run projection, for draft 09's reconciliation.

        Thin wrapper so `reconcile` talks to a client rather than to the API
        internals. Raises `FabroError` on a non-2xx; a `404` (status_code 404) is
        the "fabro never heard of this run" case that recovery treats as lost.
        """
        return get_run(self._api, self._token, run_id, timeout=self._call_timeout)

    def active_issue_claims(self) -> set[tuple[str, int]]:
        """`(repo, issue_number)` for every issue a live fabro run is working.

        Draft 09's recovery asks this before it un-labels anything. Its GitHub pass
        repairs "a receipt with no lease", and the premise underneath that phrase —
        every live run holds a lease — is false by design for
        `ops/fabro-fire-backlog.sh`, the escape hatch for when the scheduler is
        down. A hand fire takes no lease on purpose (draft 10), its run's `claim`
        writes `agent-in-progress` anyway, and the scheduler coming back up is
        exactly when recovery runs. Without this the pass would strip that
        receipt and dispatch a second run onto an issue already being implemented.

        Raises `FabroError` rather than returning an empty set when fabro cannot
        answer: an empty set reads as "nothing is live", which is the answer that
        un-labels everything. The caller fails closed on the raise.
        """
        claims = set()
        for run in list_active_runs(self._api, self._token, timeout=self._call_timeout):
            claim = issue_claim(run)
            if claim is not None:
                claims.add(claim)
        return claims

    def last_failure_category(self, run_id: str) -> str | None:
        """The failure `category` of a run, or `None` if it has none readable.

        Draft 09's requeue predicate keys on it, and its one classification step
        in `reconcile.py` reads it here rather than holding API internals.
        """
        return last_failure_category(
            self._api, self._token, run_id, timeout=self._call_timeout
        )


def _request(
    method: str,
    api: str,
    path: str,
    token: str,
    *,
    json: Mapping[str, object] | None = None,
    # A sequence of pairs as well as a mapping: `GET /runs` takes a repeated
    # `status` key, which a dict cannot express.
    params: Mapping[str, object] | Sequence[tuple[str, object]] | None = None,
    client: httpx.Client | None = None,
    timeout: float,
) -> dict:
    """One JSON request, or a `FabroError` naming what went wrong.

    The URL is built from `api` and `path`; `path` is what every message quotes,
    never the whole URL, so host-specific detail stays out of a response body the
    unauthenticated LAN can read.
    """
    if not token or not token.strip():
        raise FabroError(
            "FABRO_API_TOKEN is not set; the scheduler cannot call the fabro API"
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if json is not None:
        headers["Content-Type"] = "application/json"

    owned = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.request(
            method,
            f"{api.rstrip('/')}{path}",
            headers=headers,
            json=json,
            params=params,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise FabroError(f"{method} {path} failed: {exc}") from exc
    finally:
        if owned:
            http.close()

    if not 200 <= response.status_code < 300:
        detail = _error_detail(response)
        raise FabroError(
            f"{method} {path} -> HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
            detail=detail,
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise FabroError(
            f"{method} {path} -> HTTP {response.status_code} with a body that is not JSON"
        ) from exc

    if not isinstance(body, dict):
        raise FabroError(
            f"{method} {path} -> HTTP {response.status_code} with a "
            f"{type(body).__name__} body, expected an object"
        )
    return body


def _error_detail(response: httpx.Response) -> str:
    """The actionable sentence out of a fabro error body.

    `{"errors":[{"detail": ...}]}` is the shape fabro actually returns
    (`title`, `status` and `code` sit beside `detail`); a flat `{"detail": ...}`
    is accepted too because not every route is the same handler. Falling back to
    the raw body matters: a proxy's HTML error page is more useful than the word
    "error".
    """
    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, Mapping):
        errors = body.get("errors")
        if isinstance(errors, list):
            for entry in errors:
                if isinstance(entry, Mapping):
                    found = _first_string(entry, ("detail", "title", "code"))
                    if found is not None:
                        return found
        found = _first_string(body, ("detail", "message", "error", "code"))
        if found is not None:
            return found

    text = response.text.strip() if response.text else ""
    return text[:400] if text else "no detail in the response body"


def _first_string(source: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _summarise(body: Mapping[str, object]) -> str:
    keys = ", ".join(sorted(body)) or "no keys"
    return f"body has keys: {keys}"


def _redact(text: str) -> str:
    """Strip a `user:password@` from any URL in `text`.

    `FABRO_API_URL` is allowed to carry one (a URL that can hold a credential is
    treated as one), an httpx transport error quotes the URL it failed on, and
    this module's messages end up in an HTTP response body. One choke point, in
    the exception constructor, is what makes that a rule rather than a habit.
    """
    return USERINFO.sub("***@", text)
