"""The 60-second inventory loop: GitHub → the ETag-aware cache.

Decision 8: poll GitHub, no push, no webhook. The whole cost of that choice is one
conditional request per repo per minute, and a `304` spends none of the 5,000/hour
budget — so four repos is ~240 requests an hour that are almost all free.

The loop is a single thread, not one per repo, because SQLite has one writer here
and the reads are trivial; a thread per repo would buy nothing and cost a lock
that is harder to reason about. The repos are **staggered**, though: repo *i* runs
at `start + i*3s` and then every 60s, so four requests never land in the same
second. Three seconds and not `interval/n` (which would be 15s) because the page
must be populated on a cold start: a 15s spread means the operator stares at an
empty queue for 45 seconds after every deploy, and the thing the stagger protects
against — a same-second burst — is equally well handled by 3s.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx

from .config import RepoConfig
from .github import REQUEST_TIMEOUT_SECONDS, GitHubError, fetch_issues
from .remainder import promote_remainders
from .store import Store

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60.0

# How far apart the first pass's four requests are. See the module docstring.
DEFAULT_STAGGER_SECONDS = 3.0


class InventoryPoller:
    """Refreshes the cached inventory for a fixed set of repos, forever."""

    def __init__(
        self,
        repos: Sequence[RepoConfig],
        store: Store,
        token: str,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        stagger_seconds: float = DEFAULT_STAGGER_SECONDS,
    ) -> None:
        self._repos = tuple(repos)
        self._store = store
        self._token = token
        self._interval = interval_seconds
        self._stagger = stagger_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: httpx.Client | None = None

    @property
    def repos(self) -> tuple[RepoConfig, ...]:
        return self._repos

    def start(self) -> None:
        """Start polling. Idempotent."""
        if self._thread is not None:
            return
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._thread = threading.Thread(
            target=self._run, name="fabro-scheduler-inventory", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop polling and close the HTTP client. Safe to call twice."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        if self._client is not None:
            self._client.close()
            self._client = None

    def refresh_all(self) -> None:
        """Poll every repo once, in order, without the stagger or the thread.

        The testing seam, and the shape a "refresh now" control would take. It is
        deliberately not wired to an endpoint: forcing a re-poll is a non-goal of
        this draft, and an unauthenticated LAN endpoint that burns rate-limit
        quota on demand is not worth having before something needs it.
        """
        for repo in self._repos:
            self.poll_repo(repo)

    def poll_repo(self, repo: RepoConfig) -> None:
        """One conditional request for one repo, and whatever it implies.

        **Never raises.** A single repo's bad day — a malformed payload, a bug in
        the parse, a full disk — must not stop the loop for the other three, and it
        must not stop this one forever either. The failure is recorded against the
        repo and the page shows it as stale, which is louder than a poller thread
        that died without saying so while `/health` kept answering `ok`.
        """
        try:
            self._poll(repo)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.exception("inventory: %s: unexpected failure", repo.name)
            self._note_failure(repo.name, f"unexpected error: {exc}")
        self._promote(repo)

    def _promote(self, repo: RepoConfig) -> None:
        """Promote held remainder issues (ADR 0011 D6). **Never raises.**

        Deliberately outside `_poll`'s error handling: a promoter failure is logged,
        and it must not mark the repo's inventory stale, because the inventory itself
        is fine. The promotion is retried on the next pass anyway.
        """
        try:
            for number, action in promote_remainders(
                repo.name, self._token, client=self._client
            ):
                log.info("inventory: %s#%s remainder %s", repo.name, number, action)
        except Exception:  # noqa: BLE001 - see the docstring
            log.exception("inventory: %s: remainder promotion failed", repo.name)

    def _poll(self, repo: RepoConfig) -> None:
        state = self._store.fetch_state(repo.name)
        etag = state.etag if state is not None else None

        try:
            issues, new_etag, changed = fetch_issues(
                repo.name, self._token, etag, client=self._client
            )
        except GitHubError as exc:
            # Keep the cached items and the ETag; record the failure so the page
            # can say "stale" rather than "no work".
            self._note_failure(repo.name, str(exc))
            log.warning(
                "inventory: %s fetch failed, keeping cached items: %s", repo.name, exc
            )
            return

        now = datetime.now(UTC)
        if changed:
            self._store.sync_repo(repo.name, issues, new_etag, now)
            log.info(
                "inventory: %s cache replaced with %d queue item(s)", repo.name, len(issues)
            )
        else:
            # The 200/304 line itself is logged by `fetch_issues`, next to the
            # response headers that justify it.
            self._store.note_not_modified(repo.name, new_etag, now)

    def _note_failure(self, repo: str, error: str) -> None:
        """Record a failure, and never let the recording be what stops the loop."""
        try:
            self._store.note_failure(repo, datetime.now(UTC), error)
        except Exception:  # noqa: BLE001 - a broken database must not be fatal here
            log.exception("inventory: %s: could not record the failure", repo)

    # --- the loop ---------------------------------------------------------------

    def _run(self) -> None:
        repos = self._repos
        if not repos:
            return

        start = time.monotonic()
        due = [start + index * self._stagger for index in range(len(repos))]

        while not self._stop.is_set():
            index = min(range(len(repos)), key=lambda i: due[i])
            wait = due[index] - time.monotonic()
            if wait > 0 and self._stop.wait(wait):
                return
            self.poll_repo(repos[index])
            # Anchored on completion, not on the previous due time: a slow GitHub
            # must not queue up a backlog of immediate retries.
            due[index] = time.monotonic() + self._interval
