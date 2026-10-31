"""The flaky investigation's nodes (more-local-inference 10): claim, stop, prep, gate and post.

`fabro validate` proves the graph parses. It never runs a node's script, and every rule that
matters here is a runtime one:

* `claim` must find the subject the scout named (the C3 fingerprint on the issue body's first
  line), read FlakeRadar for it, write `/tmp/fabro/box` (R16), and route a closed issue, one
  without `flaky-test`, or one whose current text already has a diagnosis, to `stop` **without
  touching GitHub**. A FlakeRadar read that fails, or an empty `FLAKERADAR_URL`, must not fail
  the run: the diagnosis then rests on the issue and the code.
* `gate` must refuse a diagnosis with no Input receipt or an unknown cause, drop every cited
  `file:line` that is not in the checkout, fail one with no evidence left, and hold the
  rendered comment to 2,500 characters.
* `post` is the only GitHub writer: the marker first, edited in place on a rerun, redacted.

The nodes are run **in the graph's order** (`claim` → `stop` → `prep` → `investigate` (a
fixture) → `gate` → `post`) over one `/tmp/fabro`, because a node that deletes a file a later
node reads passes every per-node test and fails live.
"""

from __future__ import annotations

import json

from harness import (ORIG_PATH, WF, cat, count, edges, extract, git, jq, lastjson, present,
                     rebase, sh, stub, write)

GRAPH = WF / "flaky-investigation" / "workflow.fabro"

# A `gh` that answers every call the package makes and records each one, in order. GraphQL is
# the issue's edit history: `last_edit` is the newest `editedAt`, which is what the staleness
# rule compares the marker's `at=` with.
GH_STUB = """#!/bin/sh
log="${FI_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
case "$1 $2" in
  "issue view")    cat "${FI_ISSUE_JSON:-/dev/null}" ;;
  "issue comment") cat "$5" > "${FI_POSTED:?}"; echo comment ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *"-X PATCH"*) cat > "${FI_POSTED:?}" ;;
    *"comments?per_page="*) cat "${FI_COMMENTS_JSON:-/dev/null}" ;;
    *graphql*) cat "${FI_EDITS_JSON:-/dev/null}" ;;
  esac
fi
exit 0
"""

# A `curl` that answers FlakeRadar's four endpoints from files and records each URL. A file
# named `absent` makes the answer 404, which is how a read that fails is driven.
CURL_STUB = """#!/bin/sh
log="${FI_CURL_LOG:?}"
url=''
out=''
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2 ;;
    http*) url=$1; shift ;;
    *) shift ;;
  esac
done
printf '%s\\n' "$url" >> "$log"
case "$url" in
  */api/tests\?*)    f="${FI_TESTS_JSON:-/dev/null}" ;;
  */api/tests/*/history\?*) f="${FI_HISTORY_JSON:-/dev/null}" ;;
  */api/jobs/*/history) f="${FI_JOB_HISTORY_JSON:-/dev/null}" ;;
  */api/jobs\?*)     f="${FI_JOBS_JSON:-/dev/null}" ;;
  *) f=/dev/null ;;
esac
if [ ! -s "$f" ]; then
  [ -n "$out" ] && : > "$out"
  printf '404'
  exit 0
fi
[ -n "$out" ] && cp "$f" "$out"
printf '200'
exit 0
"""

TEST_FP = "flaky:test-backend:d7078fbe152e911131050e33c3b1ea088e484c27"
JOB_FP = "flaky:.github/workflows/ci.yml:ui-end-to-end-tauri-pilot"


def issue(*, number: int = 42, state: str = "OPEN", labels: tuple[str, ...] = ("agent", "scout", "flaky-test"),
          body: str, title: str = "scout: flaky test \"x\" in backend") -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "url": f"https://github.com/o/r/issues/{number}",
        "labels": [{"name": name} for name in labels],
    }


def scout_body(fp: str) -> str:
    return (f"<!-- fabro:scout fp={fp} -->\nFound by the scout (ADR 0027, ADR 0028) in "
            "FlakeRadar. The test `x` fails at random.\n\n## Recent failures\n\nnothing\n")


def comments(*bodies: str) -> str:
    return json.dumps([
        {"id": 100 + i, "user": {"login": "someone"}, "created_at": "2026-10-30T10:00:00Z",
         "body": body}
        for i, body in enumerate(bodies)
    ])


def marker(at: str = "2026-11-01T10:00:00Z", *, test: str = "9354", cause: str = "timing",
           run: str = "R1") -> str:
    return (f"<!-- fabro:flaky-investigation test={test} cause={cause} at={at} run={run} -->\n"
            "**Flaky investigation.** an older diagnosis")


EDITS = {"data": {"repository": {"issue": {"userContentEdits": {"nodes": [
    {"editedAt": "2026-10-20T09:00:00Z"},
]}}}}}
EDITS_AFTER_MARKER = {"data": {"repository": {"issue": {"userContentEdits": {"nodes": [
    {"editedAt": "2026-10-20T09:00:00Z"},
    {"editedAt": "2026-11-02T09:00:00Z"},
]}}}}}

TESTS = {"items": [{"id": 9354, "fingerprint": "d7078fbe152e911131050e33c3b1ea088e484c27",
                    "project": "backend", "name": "x"}], "total": 1}
HISTORY = {
    "test": {"id": 9354, "project": "backend", "suite": "pytest",
             "classname": "tests.test_migration.TestPaymentMethodsColumn",
             "fingerprint": "d7078fbe152e911131050e33c3b1ea088e484c27", "name": "x",
             "file": "tests/test_migration.py", "line": 630, "flakiness_score": 0.6,
             "tier": "flaky", "confirmed_flake_count": 1, "failure_category": "timing",
             "quarantined": False, "last_status": "failed",
             "last_seen_at": "2026-10-18T12:00:00.123456Z"},
    "location": {"path": "tests/test_migration.py", "line": 630, "url": None},
    "last_failing_sha": "d7078fbe152e911131050e33c3b1ea088e484c27",
    "last_failing_branch": "main",
    "executions": [
        {"status": "failed", "message": "RuntimeError: withdrew twice",
         "details": "tests/test_migration.py:630: in test_x\nE RuntimeError",
         "created_at": "2026-10-18T12:00:00.500Z", "commit_sha": "d7078fbe", "branch": "main",
         "ci_run_id": "39100158758", "attempt": 1},
        {"status": "passed", "message": "", "details": "", "created_at": "2026-10-18T13:00:00Z",
         "commit_sha": "d7078fbe", "branch": "main", "ci_run_id": "39100158759", "attempt": 1},
        {"status": "failed", "message": "RuntimeError: again", "details": "",
         "created_at": "2026-10-17T12:00:00Z", "commit_sha": "aaaa", "branch": "main",
         "ci_run_id": "39100000000", "attempt": 1},
    ],
}
JOBS = {"items": [{"id": 30, "pipeline": ".github/workflows/ci.yml",
                   "name": "UI end-to-end (tauri-pilot)", "tier": "flaky",
                   "flakiness_score": 0.6, "confirmed_flake_count": 1, "last_status": "passed",
                   "last_seen_at": "2026-10-31T20:16:14.686581Z",
                   "github_issue_number": 42}], "total": 1}
JOB_HISTORY = {
    "job": {"id": 30, "pipeline": ".github/workflows/ci.yml",
            "name": "UI end-to-end (tauri-pilot)", "tier": "flaky", "flakiness_score": 0.6,
            "confirmed_flake_count": 1, "last_status": "passed",
            "last_seen_at": "2026-10-31T20:16:14.686581Z"},
    "unexplained_failures": 2,
    "explained_failures": 9,
    "executions": [
        {"status": "failed", "outcome": "failed", "commit_sha": "dcb71637aaaa",
         "branch": "fabro/run/01M", "ci_run_id": "41232784902", "ci_run_attempt": 1,
         "runner_name": "writers-app-7xm8l-runner-s4qhh", "runner_labels": ["writers-app"],
         "explained_by": []},
        {"status": "failed", "outcome": "explained", "commit_sha": "48848100bbbb",
         "branch": "main", "ci_run_id": "41235846381", "ci_run_attempt": 1,
         "runner_name": "writers-app-7xm8l-runner-fr8sk", "runner_labels": ["writers-app"],
         "explained_by": [{"name": "rewrite pane shows the loading state"}]},
    ],
}

SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b-bg"),
)


class Flaky:
    """A scratch checkout with a stub `gh` and `curl`, and one `/tmp/fabro` for the whole run."""

    def __init__(self, ck, tmp_path, name: str, *, body: str | None = None,
                 issue_record: dict | None = None):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.fabro / "flaky", self.fabro / ".io", self.bin, self.repo / "tests"):
            d.mkdir(parents=True, exist_ok=True)
        write(self.repo / "tests/test_migration.py", "def test_x():\n    pass\n")
        git("init", "-q", "-b", "main", self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base",
            cwd=self.repo)
        stub(self.bin, "gh", GH_STUB)
        stub(self.bin, "curl", CURL_STUB)
        self.log = self.T / "gh.log"
        self.curl_log = self.T / "curl.log"
        write(self.log, "")
        write(self.curl_log, "")
        self.posted = self.T / "posted.md"
        self.nodes = {}
        for node in ("claim", "stop", "prep", "gate", "post"):
            script = extract(GRAPH, node)
            for token, value in SUBSTITUTIONS:
                script = script.replace(token, value)
            self.nodes[node] = rebase(script, self.fabro)
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", self.nodes[node]))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)
        self.files = {
            "FI_ISSUE_JSON": json.dumps(issue_record or issue(body=body or scout_body(TEST_FP))),
            "FI_COMMENTS_JSON": comments(),
            "FI_EDITS_JSON": json.dumps(EDITS),
            "FI_TESTS_JSON": json.dumps(TESTS),
            "FI_HISTORY_JSON": json.dumps(HISTORY),
            "FI_JOBS_JSON": json.dumps(JOBS),
            "FI_JOB_HISTORY_JSON": json.dumps(JOB_HISTORY),
        }
        for name, value in self.files.items():
            write(self.T / f"{name}.json", value)

    def set(self, name: str, value: str) -> None:
        write(self.T / f"FI_{name}.json", value)

    def run(self, node: str, **env: str):
        base = {
            "FI_GH_LOG": str(self.log),
            "FI_CURL_LOG": str(self.curl_log),
            "FI_POSTED": str(self.posted),
            "FLAKERADAR_URL": "https://flakes.example",
        }
        for name in self.files:
            base[name] = str(self.T / f"{name}.json")
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base,
                  stderr="keep")

    def gh(self) -> str:
        return cat(self.log)

    def urls(self) -> str:
        return cat(self.curl_log)


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


def test_claim_reads_flakeradar_by_the_fingerprint_the_scout_wrote(ck, tmp_path):
    F = Flaky(ck, tmp_path, "claim-test")
    r = F.run("claim")
    ck("claim: routes to prep", "true", routed(r, "flaky_go"))
    ck("claim: box written without the -bg suffix", "coders-b", cat(F.fabro / "box"))
    ck("claim: the repository comes from the issue url", "o/r", cat(F.fabro / "flaky/repo"))
    ck("claim: the fingerprint is staged", TEST_FP, cat(F.fabro / "flaky/fp"))
    ck("claim: FlakeRadar resolved the test id", "9354", cat(F.fabro / "flaky/entity_id"))
    ck("claim: and names the entity", "tests", cat(F.fabro / "flaky/entity"))
    ck("claim: the list is read once, by fingerprint", "1", count("/api/tests?", F.urls()))
    ck("claim: the history is read for the id", "1", count("/api/tests/9354/history", F.urls()))
    record = cat(F.fabro / "flaky/flakeradar.md")
    ck("claim: the record names the test", "1", count("test id \\*\\*9354\\*\\*", record, flags="E"))
    ck("claim: and the newest traceback, once", "1", count("^E RuntimeError$", record, flags="E"))
    ck("claim: and only three executions, so a long history stays cheap", "2",
       count("^### 2026-10-1[78]T", record, flags="E"))
    ck("claim: no GitHub write", "0", count("issue comment|-X PATCH", F.gh()))
    issue_md = cat(F.fabro / "flaky/issue.md")
    ck("claim: issue.md carries the title", "1", count("^# #42: scout: flaky test", issue_md))
    ck("claim: and the labels", "1", count("Labels: agent, scout, flaky-test", issue_md))
    ck("claim: and the scout fingerprint", "1", count(f"Scout fingerprint: {TEST_FP}", issue_md))
    ck("claim: stdout is one routing object", "1", count("^{", r.out))


def test_claim_finds_a_job_issue_by_the_issue_flakeradar_linked(ck, tmp_path):
    F = Flaky(ck, tmp_path, "claim-job", body=scout_body(JOB_FP))
    r = F.run("claim")
    ck("claim: routes to prep", "true", routed(r, "flaky_go"))
    ck("claim: the job list is filtered to linked jobs first", "1",
       count("/api/jobs?repo=o/r&include_stable=true&has_issue=true", F.urls()))
    ck("claim: the job id is resolved", "30", cat(F.fabro / "flaky/entity_id"))
    record = cat(F.fabro / "flaky/flakeradar.md")
    ck("claim: the record names the job", "1", count("UI end-to-end \\(tauri-pilot\\)", record, flags="E"))
    ck("claim: and the unexplained failures", "1", count("it could not", record))
    ck("claim: and the runner", "1", count("writers-app-7xm8l-runner-s4qhh", record))
    ck("claim: and which test explained a red run", "1",
       count("explained by: rewrite pane shows the loading state", record))


def test_claim_stops_on_a_closed_issue_a_relabelled_one_or_a_current_diagnosis(ck, tmp_path):
    for name, record, note, go in (
        ("closed", issue(state="CLOSED", body=scout_body(TEST_FP)), None, "false"),
        ("unlabelled", issue(labels=("agent", "scout"), body=scout_body(TEST_FP)), None, "false"),
        ("current", issue(body=scout_body(TEST_FP)), marker("2026-11-01T10:00:00Z"), "false"),
    ):
        F = Flaky(ck, tmp_path, f"claim-{name}", issue_record=record)
        if note is not None:
            F.set("COMMENTS_JSON", comments(note))
        r = F.run("claim")
        ck(f"claim {name}: flaky_go", go, routed(r, "flaky_go"))
        ck(f"claim {name}: no GitHub write", "0", count("issue comment|-X PATCH", F.gh()))


def test_claim_diagnoses_again_when_the_issue_was_edited_after_its_diagnosis(ck, tmp_path):
    F = Flaky(ck, tmp_path, "claim-edited")
    F.set("COMMENTS_JSON", comments(marker("2026-11-01T10:00:00Z")))
    F.set("EDITS_JSON", json.dumps(EDITS_AFTER_MARKER))
    r = F.run("claim")
    ck("claim: a later edit re-opens it", "true", routed(r, "flaky_go"))


def test_claim_reads_the_edit_history_not_updated_at(ck, tmp_path):
    """The package's own comment bumps `updated_at`, so a rule keyed on it would re-diagnose
    every issue on every run."""
    F = Flaky(ck, tmp_path, "claim-edits")
    F.set("COMMENTS_JSON", comments(marker("2026-11-01T10:00:00Z")))
    F.run("claim")
    ck("claim: the issue's edits are read", "1",
       count("api graphql -f query=.*userContentEdits", F.gh(), flags="E"))
    ck("claim: and updated_at never is", "0", count("updatedAt|updated_at", F.gh()))


def test_claim_survives_a_flakeradar_read_that_fails(ck, tmp_path):
    for name, env in (
        ("404", {"FI_TESTS_JSON": "/nonexistent.json"}),
        ("no-url", {"FLAKERADAR_URL": ""}),
        ("bad-json", {"FI_TESTS_JSON": "/nonexistent.json"}),
    ):
        F = Flaky(ck, tmp_path, f"claim-{name}")
        r = F.run("claim", **env)
        ck(f"claim {name}: the run goes on", "true", routed(r, "flaky_go"))
        ck(f"claim {name}: with no record, so the diagnosis rests on the issue", "absent",
           present(F.fabro / "flaky/flakeradar.md"))
        ck(f"claim {name}: and no id to name", "absent", present(F.fabro / "flaky/entity_id"))


def test_claim_survives_a_repository_flakeradar_does_not_know(ck, tmp_path):
    F = Flaky(ck, tmp_path, "claim-uncovered")
    F.set("TESTS_JSON", json.dumps({"items": [], "total": 0}))
    r = F.run("claim")
    ck("claim: still routes to prep", "true", routed(r, "flaky_go"))
    ck("claim: with no record", "absent", present(F.fabro / "flaky/flakeradar.md"))
    ck("claim: and says the fingerprint was not found", "1",
       count("no test with fingerprint", r.err or ""))


def test_claim_refuses_a_non_numeric_issue_number(ck, tmp_path):
    F = Flaky(ck, tmp_path, "claim-nan")
    F.nodes["claim"] = F.nodes["claim"].replace("N='42'", "N='42;rm -rf /'")
    r = F.run("claim")
    ck("claim: a non-numeric issue_number fails the node", "1", str(r.rc))
    ck("claim: and writes no routing object", "", lastjson(r.out))


def test_claim_template_values_are_the_ones_the_graph_names(ck):
    script = extract(GRAPH, "claim")
    ck("claim: interpolates issue_number once", "1", count(r"{{ inputs.issue_number }}", script))
    ck("claim: interpolates coder_pool once", "1", count(r"{{ inputs.coder_pool }}", script))
    ck("claim: quotes issue_number before the guard", "1",
       count(r"N='{{ inputs.issue_number }}'", script))
    # docs/flakeradar C4b's read idiom: `-o` a file, the code on stdout, stdin closed, never a
    # pipe (a pipe loses curl's exit code and the code with it).
    ck("claim: the reads are the flakeradar idiom", "1", count("curl -sS -m 15 -o", script, flags="F"))
    ck("claim: stdin is closed", "1", count("< /dev/null", script, flags="F"))
    # Exactly one curl in the script: every read goes through that wrapper, so the idiom and
    # the timeout cannot drift apart one call at a time.
    ck("claim: one curl, in one wrapper", "1", count("curl -sS", script, flags="F"))
    ck("claim: and every read goes through it", "5", count("$(fr_get ", script, flags="F"))


# --- prep, in the order the graph runs it -------------------------------------------


def test_the_run_in_order_keeps_every_file_the_next_node_reads(ck, tmp_path):
    """`claim` stages the issue and the record, `prep` indexes the checkout, `gate` reads both
    and renders the comment, and `post` publishes it: one `/tmp/fabro`, the graph's order."""
    F = Flaky(ck, tmp_path, "order")
    ck("run: claim", "true", routed(F.run("claim"), "flaky_go"))
    r = F.run("prep", FABRO_AGENT_GUIDE="# guide")
    ck("run: prep routes", "true", routed(r, "prep_ok"))
    ck("run: prep keeps main_sha", "1", count(".", cat(F.fabro / "flaky/main_sha"), flags="E"))
    ck("run: prep keeps the record claim wrote", "1",
       count("test id", cat(F.fabro / "flaky/flakeradar.md")))
    ck("run: the guide is installed and excluded", "1",
       count("^.codex/instructions.md$", cat(F.repo / ".git/info/exclude")))


# --- gate --------------------------------------------------------------------------


DIAGNOSIS = {
    "cause": "timing",
    "summary": "The downgrade waits for a lock the previous test holds.",
    "evidence": [
        {"where": "tests/test_migration.py:1", "says": "the test body"},
        {"where": "tests/missing.py:2", "says": "a file the model imagined"},
        {"where": "flakeradar", "says": "2 failures in the last 100 executions"},
        {"where": "issue", "says": "the scout saw it fail and pass on one commit"},
    ],
    "fix": [
        {"where": "tests/test_migration.py:1", "change": "wait for the lock with a timeout"},
        {"where": "tests/nowhere.py:9", "change": "a file that does not exist"},
    ],
    "confidence": "medium",
    "reproduced": False,
}


def deliver(F: Flaky, payload: dict, *, receipt: bool = True) -> None:
    write(F.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
    if receipt:
        payload = {**payload, "_io": {"visit": "v1"}}
    write(F.fabro / "flaky.json", json.dumps(payload))


def test_gate_refuses_a_diagnosis_with_no_receipt(ck, tmp_path):
    F = Flaky(ck, tmp_path, "gate-noreceipt")
    deliver(F, DIAGNOSIS, receipt=False)
    r = F.run("gate")
    ck("gate: no receipt fails the node", "1", str(r.rc))
    ck("gate: and renders nothing", "absent", present(F.fabro / "flaky/comment.md"))


def test_gate_refuses_a_cause_the_package_does_not_know(ck, tmp_path):
    F = Flaky(ck, tmp_path, "gate-cause")
    deliver(F, {**DIAGNOSIS, "cause": "vibes"})
    r = F.run("gate")
    ck("gate: an unknown cause fails the node", "1", str(r.rc))
    ck("gate: and says so", "1", count("no cause this package knows", r.err or ""))


def test_gate_drops_what_is_not_in_the_checkout_and_renders_the_rest(ck, tmp_path):
    F = Flaky(ck, tmp_path, "gate-ok")
    deliver(F, DIAGNOSIS)
    r = F.run("gate")
    ck("gate: succeeds", "0", str(r.rc))
    ck("gate: routes", "true", routed(r, "flaky_ok"))
    ck("gate: records the cause", "timing", cat(F.fabro / "flaky/cause"))
    ck("gate: counts what it dropped", "2", cat(F.fabro / "flaky/dropped"))
    md = cat(F.fabro / "flaky/comment.md")
    ck("gate: keeps a real file:line", "1", count("^- `tests/test_migration.py:1`: the test body$", md))
    ck("gate: drops an imagined file", "0", count("missing.py", md))
    ck("gate: drops an imagined fix pointer", "0", count("nowhere.py", md))
    ck("gate: keeps the FlakeRadar fact", "1", count("^- `flakeradar`:", md))
    ck("gate: keeps the issue fact", "1", count("^- `issue`:", md))
    ck("gate: renders the summary", "1", count("downgrade waits for a lock", md))
    ck("gate: renders the fix", "1", count("wait for the lock with a timeout", md))
    ck("gate: says it did not reproduce", "1", count("did not run the test", md))


def test_gate_fails_a_diagnosis_with_no_evidence_left(ck, tmp_path):
    F = Flaky(ck, tmp_path, "gate-noev")
    deliver(F, {**DIAGNOSIS, "evidence": [{"where": "nope.py:1", "says": "nothing real"}]})
    r = F.run("gate")
    ck("gate: no evidence fails the node", "1", str(r.rc))
    ck("gate: and says so", "1", count("no evidence is not posted", r.err or r.out))


def test_gate_holds_the_comment_under_2500_characters(ck, tmp_path):
    F = Flaky(ck, tmp_path, "gate-big")
    deliver(F, {**DIAGNOSIS,
                "summary": "s" * 700,
                "evidence": [{"where": "tests/test_migration.py:1", "says": "e" * 300}
                             for _ in range(6)],
                "fix": [{"where": "tests/test_migration.py:1", "change": "f" * 300}
                        for _ in range(3)]})
    r = F.run("gate")
    ck("gate: a chatty diagnosis still succeeds", "0", str(r.rc))
    size = len(cat(F.fabro / "flaky/comment.md"))
    ck("gate: the rendered comment fits the cap", "true", str(size <= 2500).lower())
    ck("gate: and says it was cut", "1", count("cut to fit", cat(F.fabro / "flaky/comment.md")))


# --- post --------------------------------------------------------------------------


def staged_for_post(F: Flaky, md: str = "**Flaky investigation.** the diagnosis\n") -> None:
    for name, value in (("issue_number", "42"), ("repo", "o/r"), ("cause", "timing"),
                        ("entity_id", "9354"), ("run_id", "01RUN")):
        write(F.fabro / f"flaky/{name}", value + "\n")
    write(F.fabro / "flaky/comment.md", md)


def test_post_writes_the_marker_first_and_posts_a_new_comment(ck, tmp_path):
    F = Flaky(ck, tmp_path, "post-new")
    staged_for_post(F)
    r = F.run("post")
    ck("post: succeeds", "0", str(r.rc))
    ck("post: routes", "true", routed(r, "posted"))
    body = cat(F.posted)
    ck("post: the marker is the first line", "1", count(
        r"^<!-- fabro:flaky-investigation test=9354 cause=timing at=[0-9T:Z-]+ run=01RUN -->$",
        body.split("\n", 1)[0], flags="E"))
    ck("post: it names the entity and the cause", "1", count("test=9354 cause=timing", body))
    ck("post: a new comment, not an edit", "1", count("^issue comment 42 --body-file", F.gh()))
    ck("post: the diagnosis is published", "1", count("the diagnosis", body))
    ck("post: under the cap", "true", str(len(body) <= 2500).lower())


def test_post_says_unknown_when_flakeradar_resolved_nothing(ck, tmp_path):
    F = Flaky(ck, tmp_path, "post-unknown")
    staged_for_post(F)
    write(F.fabro / "flaky/entity_id", "")
    F.run("post")
    ck("post: an unresolved entity is named", "1",
       count("flaky-investigation test=unknown cause=timing", cat(F.posted)))


def test_post_edits_the_existing_marker_in_place(ck, tmp_path):
    F = Flaky(ck, tmp_path, "post-edit")
    staged_for_post(F)
    F.set("COMMENTS_JSON", comments(
        "unrelated", marker("2026-10-01T00:00:00Z", run="R0"), "after it"))
    F.run("post")
    ck("post: edits comment 101", "1", count("^api -X PATCH repos/o/r/issues/comments/101", F.gh()))
    ck("post: posts no second comment", "0", count("^issue comment", F.gh()))
    ck("post: the edit carries the new marker", "1",
       count("fabro:flaky-investigation test=9354", jq(".body", file=F.posted)))


def test_post_scrubs_tokens(ck, tmp_path):
    F = Flaky(ck, tmp_path, "post-scrub")
    secret = "ghs_supersecretvalue123"
    staged_for_post(F, md=f"**Flaky investigation.** token {secret}\n")
    F.run("post", GH_TOKEN=secret)
    body = cat(F.posted)
    ck("post: the token is redacted", "0", count(secret, body))
    ck("post: in place", "1", count(r"token \[redacted\]", body))


# --- the package's shape -----------------------------------------------------------


def test_no_script_in_the_package_can_write_to_github_before_post(ck):
    scripts = "\n".join(extract(GRAPH, node) for node in ("claim", "stop", "prep", "gate"))
    ck("the four read-only nodes extract", "true", "true" if scripts.strip() else "false")
    for needle in ("issue comment", "issue edit", "issue close", "issue pin", "label create",
                   "-X PATCH", "-X POST", "pr create", "pr merge"):
        ck(f"no write before post: {needle}", "0", count(needle, scripts, flags="F"))


def test_the_package_never_runs_the_test_suite(ck):
    """The whole design: a diagnosis from records and the code, never a reproduction."""
    scripts = "\n".join(extract(GRAPH, node)
                        for node in ("claim", "stop", "prep", "gate", "post"))
    for needle in ("setup.sh", "ci.sh", "run_tests", "pytest", "npm test", "cargo test"):
        ck(f"nothing executes the suite: {needle}", "0", count(needle, scripts, flags="F"))


def test_every_failure_ends_the_run_and_only_gate_reaches_post(ck):
    e = edges(GRAPH)
    for src in ("claim", "prep", "gate", "post", "stop"):
        ck(f"edge: {src} has an unconditional edge to exit", "1", count(f"{src}->exit ", e, flags="xF"))
    ck("edge: post is reached only from gate", "1", count("->post", e, flags="F"))
    ck("edge: and only when gate succeeded", "1", count("gate->post outcome=succeeded", e, flags="xF"))
    ck("edge: investigate always goes to gate", "1", count("investigate->gate ", e, flags="xF"))
    ck("edge: claim routes to stop or to prep", "1",
       count("claim->stop outcome=succeeded && context.flaky_go=false", e, flags="xF"))
    ck("edge: a diagnosis only the claim approved is made", "1",
       count("claim->prep outcome=succeeded && context.flaky_go=true", e, flags="xF"))
    ck("edge: stop writes nothing and ends the run", "1", count("stop->exit ", e, flags="xF"))
