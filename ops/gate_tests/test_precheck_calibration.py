"""The precheck calibration's command nodes: claim and record.

`fabro validate` proves the graph parses. It never runs a node's script, and this package's
whole contract is in two scripts:

* `claim` must check out `main` **as it stood at `at`**, rebuild the issue text as it was then
  (body from the GraphQL edit history, comments created before `at`), write the files `prep`,
  `check` and `gate` already know, and refuse a malformed `at` or issue number before it runs
  anything at all. Nothing it does may write to GitHub.
* `record` is the last node. It writes nothing outside `/tmp/fabro` and prints one
  `context_updates` object carrying the five keys the scheduler reads.
* The package contains no writing command anywhere, and its GitHub permissions are `read`.
"""

from __future__ import annotations

import json

from harness import (ORIG_PATH, WF, cat, count, edges, extract, git, jq, lastjson, present,
                     rebase, sh, stub, write)

GRAPH = WF / "precheck-calibration" / "workflow.fabro"
TOML = WF / "precheck-calibration" / "workflow.toml"

# A `gh` that answers every call the calibration makes and records each one, in order.
GH_STUB = """#!/bin/sh
log="${CAL_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
case "$1 $2" in
  "issue view") cat "${CAL_ISSUE_JSON:-/dev/null}" ;;
  "issue list") cat "${CAL_ISSUES_JSON:-/dev/null}" ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *"comments?per_page="*) cat "${CAL_COMMENTS_JSON:-/dev/null}" ;;
    *graphql*)              cat "${CAL_EDITS_JSON:-/dev/null}" ;;
  esac
fi
exit 0
"""

ISSUE = {
    "number": 42,
    "title": "Add a dark mode toggle",
    "body": "## Acceptance criteria\n\n- A toggle in settings switches the theme.\n",
    "state": "OPEN",
    "url": "https://github.com/o/r/issues/42",
    "labels": [{"name": "agent"}],
}

# The run this job calibrates was dispatched here. `+00:00` with microseconds is what
# `datetime.isoformat()` writes into `run_history.dispatched_at`.
AT_RAW = "2026-10-20T12:00:00.123456+00:00"
AT = "2026-10-20T12:00:00Z"
BEFORE = "2026-10-19T12:00:00Z"
AFTER = "2026-10-21T12:00:00Z"

SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b-bg"),
    ("{{ inputs.at }}", AT_RAW),
)


def comment(number: int, created_at: str, body: str) -> dict:
    return {
        "id": 100 + number,
        "user": {"login": "someone"},
        "created_at": created_at,
        "body": body,
    }


COMMENTS = [
    comment(0, BEFORE, "This is the first note, written before the run."),
    comment(1, BEFORE, "<!-- fabro:precheck verdict=ready body=x main=y at=z run=r -->\n\nold"),
    comment(2, AFTER, "This note arrived after the run started."),
]

# GitHub's `userContentEdits`: one entry per body edit, whose `diff` is the whole body as that
# edit left it, newest first (read 2026-10-31 against writers-app#1274).
EDITED = {"data": {"repository": {"issue": {"userContentEdits": {"nodes": [
    {"createdAt": AFTER, "diff": "the body after the run started"},
    {"createdAt": BEFORE, "diff": "the body the run saw"},
]}}}}}
UNEDITED = {"data": {"repository": {"issue": {"userContentEdits": {"nodes": []}}}}}

ISSUES = [
    {"number": 42, "title": "Add a dark mode toggle", "state": "OPEN", "closedAt": None},
    {"number": 40, "title": "Add a settings page", "state": "OPEN", "closedAt": None},
    {"number": 39, "title": "Closed before the run", "state": "CLOSED",
     "closedAt": BEFORE},
    {"number": 41, "title": "Closed after the run", "state": "CLOSED",
     "closedAt": AFTER},
    {"number": 43, "title": "Opened after the run", "state": "OPEN", "closedAt": None},
]


class Calibration:
    """A scratch checkout with two commits around `at`, a stub `gh`, a per-test `/tmp/fabro`."""

    def __init__(self, ck, tmp_path, name: str, *, issue: dict | None = None,
                 edits: dict | None = None, comments: list | None = None):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.fabro / "precheck", self.fabro / ".io", self.bin, self.repo):
            d.mkdir(parents=True, exist_ok=True)
        write(self.repo / "src.txt", "base\n")
        git("init", "-q", "-b", "main", self.repo)
        self.before = self.commit("before", BEFORE)
        write(self.repo / "src.txt", "later\n")
        self.after = self.commit("after", AFTER)
        # `origin/main` is what a fabro sandbox clone has: a single remote-tracking ref.
        git("update-ref", "refs/remotes/origin/main", self.after, cwd=self.repo)
        stub(self.bin, "gh", GH_STUB)
        self.log = self.T / "gh.log"
        write(self.log, "")
        self.issue_json = self.T / "issue.json"
        self.comments_json = self.T / "comments.json"
        self.edits_json = self.T / "edits.json"
        self.issues_json = self.T / "issues.json"
        write(self.issue_json, json.dumps(issue or ISSUE))
        write(self.comments_json, json.dumps(comments if comments is not None else COMMENTS))
        write(self.edits_json, json.dumps(UNEDITED if edits is None else edits))
        write(self.issues_json, json.dumps(ISSUES))
        self.nodes = {}
        for node in ("claim", "record"):
            script = extract(GRAPH, node)
            for token, value in SUBSTITUTIONS:
                script = script.replace(token, value)
            self.nodes[node] = rebase(script, self.fabro)
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", self.nodes[node]))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)

    def commit(self, message: str, when: str) -> str:
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message,
            cwd=self.repo, env=env)
        return git("rev-parse", "HEAD", cwd=self.repo)

    def run(self, node: str, **env: str):
        base = {
            "CAL_GH_LOG": str(self.log),
            "CAL_ISSUE_JSON": str(self.issue_json),
            "CAL_COMMENTS_JSON": str(self.comments_json),
            "CAL_EDITS_JSON": str(self.edits_json),
            "CAL_ISSUES_JSON": str(self.issues_json),
        }
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base,
                  stderr="keep")

    def gh(self) -> str:
        return cat(self.log)


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


# --- claim: main as it stood at `at` ------------------------------------------------


def test_claim_checks_out_the_commit_before_at(ck, tmp_path):
    C = Calibration(ck, tmp_path, "claim-sha")
    r = C.run("claim")
    ck("claim: routes to prep", "true", routed(r, "calibration_go"))
    ck("claim: main_sha is the last commit before at", C.before, cat(C.fabro / "precheck/main_sha"))
    ck("claim: and the checkout is on it", C.before, git("rev-parse", "HEAD", cwd=C.repo))
    ck("claim: the tree is that commit's", "base", cat(C.repo / "src.txt"))
    ck("claim: box written without the -bg suffix", "coders-b", cat(C.fabro / "box"))
    ck("claim: no GitHub write", "0", count("issue comment|-X PATCH|-X POST|--method", C.gh()))


def test_claim_rebuilds_the_issue_as_it_was(ck, tmp_path):
    C = Calibration(ck, tmp_path, "claim-text", edits=EDITED)
    r = C.run("claim")
    ck("claim: routes to prep", "true", routed(r, "calibration_go"))
    issue_md = cat(C.fabro / "precheck/issue.md")
    ck("claim: the body is the one the run saw", "1", count("the body the run saw", issue_md))
    ck("claim: not the body it has now", "0", count("the body after the run started", issue_md))
    ck("claim: body_changed is recorded", "true", cat(C.fabro / "precheck/body_changed"))
    ck("claim: a comment from before at is kept", "1",
       count("This is the first note", issue_md))
    ck("claim: a comment from after at is left out", "0",
       count("This note arrived after", issue_md))
    ck("claim: and a fabro marker is left out", "0", count("fabro:precheck", issue_md))
    ck("claim: the title is the current one", "1",
       count("^# #42: Add a dark mode toggle$", issue_md))


def test_claim_falls_back_to_the_current_text_when_no_body_was_edited(ck, tmp_path):
    C = Calibration(ck, tmp_path, "claim-unedited")
    C.run("claim")
    ck("claim: the current body is used", "1",
       count("A toggle in settings switches the theme", cat(C.fabro / "precheck/issue.md")))
    ck("claim: body_changed is false", "false", cat(C.fabro / "precheck/body_changed"))


def test_claim_lists_the_issues_open_at_at(ck, tmp_path):
    C = Calibration(ck, tmp_path, "claim-open")
    C.run("claim")
    md = cat(C.fabro / "precheck/open_issues.md")
    ck("claim: an issue open now is listed", "1", count("^#40 Add a settings page$", md))
    ck("claim: an issue closed before at is not", "0", count("Closed before the run", md))
    ck("claim: an issue closed after at is", "1", count("Closed after the run", md))
    ck("claim: the issue itself is not", "0", count("^#42 ", md))
    # `gate` reads the same list as JSON to test a `blocked` verdict's blocker.
    numbers = jq("[.[].number] | sort", cat(C.fabro / "precheck/open_issues.json"),
                 args=["-c"])
    ck("claim: gate's list carries the numbers", "[40,41,43]", numbers)


# --- claim: the guards -------------------------------------------------------------


def test_claim_refuses_a_malformed_at_before_any_command_runs(ck, tmp_path):
    for name, at in (
        ("not-a-date", "yesterday"),
        ("date-only", "2026-10-20"),
        ("shell", "2026-10-20T12:00:00Z; rm -rf /"),
        ("offset", "2026-10-20T12:00:00+02:00"),
    ):
        C = Calibration(ck, tmp_path, f"at-{name}")
        C.nodes["claim"] = C.nodes["claim"].replace(f"AT_RAW='{AT_RAW}'", f"AT_RAW='{at}'")
        r = C.run("claim")
        ck(f"claim {name}: fails the node", "1", str(r.rc))
        ck(f"claim {name}: no routing object", "", lastjson(r.out))
        ck(f"claim {name}: no gh call", "", C.gh())
        ck(f"claim {name}: nothing staged", "absent",
           present(C.fabro / "precheck/main_sha"))


def test_claim_refuses_a_non_numeric_issue_number(ck, tmp_path):
    C = Calibration(ck, tmp_path, "claim-nan")
    C.nodes["claim"] = C.nodes["claim"].replace("N='42'", "N='42;rm -rf /'")
    r = C.run("claim")
    ck("claim: a non-numeric issue_number fails the node", "1", str(r.rc))
    ck("claim: and writes no routing object", "", lastjson(r.out))
    ck("claim: and makes no gh call", "", C.gh())


def test_claim_fails_when_main_has_nothing_before_at(ck, tmp_path):
    C = Calibration(ck, tmp_path, "claim-early")
    C.nodes["claim"] = C.nodes["claim"].replace(f"AT_RAW='{AT_RAW}'", "AT_RAW='2026-01-01T00:00:00Z'")
    r = C.run("claim")
    ck("claim: a run from before main's history fails", "1", str(r.rc))
    ck("claim: and stages no main_sha", "absent", present(C.fabro / "precheck/main_sha"))


# --- record ------------------------------------------------------------------------


def test_record_publishes_the_five_keys_and_writes_nothing(ck, tmp_path):
    C = Calibration(ck, tmp_path, "record")
    P = C.fabro / "precheck"
    write(P / "verdict", "ready")
    write(P / "raw_verdict", "unclear")
    write(P / "rule", "it cited no evidence")
    write(P / "main_sha", C.before)
    write(P / "body_changed", "true")
    before = {p.name for p in C.fabro.rglob("*")}
    r = C.run("record")
    ck("record: succeeds", "0", str(r.rc))
    ck("record: the verdict", "ready", routed(r, "verdict"))
    ck("record: the model's own", "unclear", routed(r, "raw_verdict"))
    ck("record: the rule", "it cited no evidence", routed(r, "rule"))
    ck("record: the commit", C.before, routed(r, "main_sha"))
    ck("record: body_changed", "true", routed(r, "body_changed"))
    ck("record: one routing object", "1", count("^{", r.out))
    ck("record: nothing new outside /tmp/fabro",
       str(before), str({p.name for p in C.fabro.rglob("*")}))
    ck("record: no gh call", "", C.gh())


def test_record_says_unknown_rather_than_guessing(ck, tmp_path):
    C = Calibration(ck, tmp_path, "record-partial")
    write(C.fabro / "precheck/verdict", "ready")
    write(C.fabro / "precheck/raw_verdict", "ready")
    r = C.run("record")
    ck("record: a missing main_sha reads unknown", "unknown", routed(r, "main_sha"))
    ck("record: a missing body_changed reads unknown", "unknown", routed(r, "body_changed"))


# --- the package cannot write to GitHub ---------------------------------------------


WRITES = ("issue comment", "issue edit", "issue close", "pr create", "pr edit", "pr merge",
          "-X PATCH", "-X POST", "-X PUT", "-X DELETE", "--method")


def test_no_script_in_the_package_can_write_to_github(ck):
    """The scripts, not the comments: a `//` comment may describe a write it forbids."""
    scripts = "\n".join(extract(GRAPH, node) for node in ("claim", "prep", "gate", "record"))
    ck("every command node extracts", "true", "true" if scripts.strip() else "false")
    for needle in WRITES:
        ck(f"no write command: {needle}", "0", count(needle, scripts, flags="F"))


def test_the_package_permissions_are_read_only(ck):
    toml = TOML.read_text()
    ck("toml: contents read", "1", count('contents = "read"', toml, flags="xF"))
    ck("toml: issues read", "1", count('issues = "read"', toml, flags="xF"))
    ck("toml: no write permission anywhere", "0", count('= "write"', toml, flags="F"))
    ck("toml: no run branch push", "1", count("enabled = false", toml, flags="xF"))


def test_no_stage_manifest_input_carries_the_verdict(ck):
    """Only `check`'s own inputs and output exist: nothing downstream reads the verdict."""
    import re

    block = re.search(r"FABRO_IO_MANIFEST = '''(.*?)'''", TOML.read_text(), re.S).group(1)
    manifest = json.loads(block)
    ck("manifest: one stage", "check", ",".join(manifest["stages"]))
    ck("manifest: it is a box stage", "true", str(manifest["stages"]["check"]["box"]).lower())


# --- routing -----------------------------------------------------------------------


def test_every_failure_ends_the_run_and_only_gate_reaches_record(ck):
    e = edges(GRAPH)
    for src in ("claim", "prep", "gate", "record"):
        ck(f"edge: {src} has an unconditional edge to exit", "1",
           count(f"{src}->exit ", e, flags="xF"))
    ck("edge: record is reached only from gate", "1", count("->record", e, flags="F"))
    ck("edge: and only when gate succeeded", "1",
       count("gate->record outcome=succeeded", e, flags="xF"))
    ck("edge: check always goes to gate", "1", count("check->gate ", e, flags="xF"))
    ck("edge: there is no stop node", "0", count("->stop", e, flags="F"))
    ck("edge: and no post node", "0", count("->post", e, flags="F"))
