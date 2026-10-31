"""The repository orientation's nodes and its reader (more-local-inference 10).

`fabro validate` proves the graphs parse. It never runs a node's script, and every rule that
matters here is a runtime one:

* `claim` must find the orientation issue by label, compare the marker's `main=` with the
  checkout, count how far `main` has moved **through the API** (the sandbox clone is shallow,
  so local history cannot), and route an up-to-date map to `stop` without touching GitHub.
* `prep` (in this package) is the precheck's, and then `gate` must refuse a map with no
  receipt, drop every module and test path not in the checkout, fail one with no module left,
  and hold the body inside 6,000 characters.
* `post` is the only GitHub writer. It creates the issue with `fabro-orientation` as its only
  label and pins it, or edits the body in place, with the marker first and the tokens redacted.
* `backlog`'s and `ralph`'s `prep` fetch that body to `/tmp/fabro/orientation.md` (once, failing
  quietly), and `decompose` and `plan` read it as an optional Stage I/O input.

The label rule (D22) is the one that would be invisible: the orientation issue must never be
selected by any automation, so one test reads every selector in the repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import (ORIG_PATH, RAPH, WF, cat, count, edges, extract, git, jq, lastjson, present,
                     rebase, sh, stub, write)

GRAPH = WF / "repo-orientation" / "workflow.fabro"
BACKLOG = WF / "backlog" / "workflow.fabro"
REPO_ROOT = WF.parents[1]
LABEL = "fabro-orientation"

GH_STUB = """#!/bin/sh
log="${RO_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
case "$1 $2" in
  "repo view")     printf '%s\\n' "${RO_REPO:-o/r}" ;;
  "issue list")    cat "${RO_ISSUES_JSON:-/dev/null}" ;;
  "issue create")  printf '%s\\n' "https://github.com/${RO_REPO:-o/r}/issues/77" ;;
  "issue pin")     : ;;
  "label create")  : ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *compare*) printf '%s' "${RO_COMPARE:-0}" ;;
    *"-X PATCH"*) cat > "${RO_PATCHED:?}" ;;
  esac
fi
exit 0
"""

SUBSTITUTIONS = (("{{ inputs.coder_pool }}", "coders-b-bg"),)


def orientation_body(main: str, at: str, *, text: str = "## Summary\nA map.\n") -> str:
    return f"<!-- fabro:orientation main={main} at={at} run=R0 -->\n{text}"


class Orientation:
    """A scratch checkout at `main` with a stub `gh` and one `/tmp/fabro` for the whole run."""

    def __init__(self, ck, tmp_path, name: str):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.fabro / "orientation", self.fabro / ".io", self.bin, self.repo / "src"):
            d.mkdir(parents=True, exist_ok=True)
        write(self.repo / "src/app.ts", "export const app = 1;\n")
        write(self.repo / "tests/one.test.ts", "test('x', () => {});\n")
        git("init", "-q", "-b", "main", self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base",
            cwd=self.repo)
        self.main = git("rev-parse", "HEAD", cwd=self.repo)
        stub(self.bin, "gh", GH_STUB)
        self.log = self.T / "gh.log"
        write(self.log, "")
        self.patched = self.T / "patched.md"
        self.nodes = {}
        for node in ("claim", "stop", "prep", "gate", "post"):
            script = extract(GRAPH, node)
            for token, value in SUBSTITUTIONS:
                script = script.replace(token, value)
            self.nodes[node] = rebase(script, self.fabro)
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", self.nodes[node]))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)
        self.issues = []

    def issues_json(self, value) -> None:
        path = self.T / "issues.json"
        write(path, json.dumps(value))
        self.issues = value

    def run(self, node: str, **env: str):
        base = {
            "RO_GH_LOG": str(self.log),
            "RO_ISSUES_JSON": str(self.T / "issues.json"),
            "RO_PATCHED": str(self.patched),
            "RO_REPO": "o/r",
        }
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base, stderr="keep")

    def gh(self) -> str:
        return cat(self.log)


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


# --- claim -------------------------------------------------------------------------


def test_claim_goes_when_there_is_no_orientation_issue(ck, tmp_path):
    F = Orientation(ck, tmp_path, "claim-none")
    F.issues_json([])
    r = F.run("claim")
    ck("claim: routes to prep", "true", routed(r, "orientation_go"))
    ck("claim: the repository comes from gh", "o/r", cat(F.fabro / "orientation/repo"))
    ck("claim: main= is the checkout's HEAD", F.main, cat(F.fabro / "orientation/main_sha"))
    ck("claim: no issue number, so post creates one", "absent",
       present(F.fabro / "orientation/issue_number"))
    ck("claim: the issue is looked up by label", "1",
       count(f"issue list --label {LABEL} --state open", F.gh(), flags="F"))
    ck("claim: no GitHub write", "0", count("issue comment|issue create|issue pin|-X PATCH", F.gh()))


def test_claim_stops_on_a_map_of_this_commit(ck, tmp_path):
    F = Orientation(ck, tmp_path, "claim-same")
    F.issues_json([{"number": 9, "body": orientation_body(F.main, "2026-10-01T00:00:00Z")}])
    r = F.run("claim")
    ck("claim: the same commit is done", "false", routed(r, "orientation_go"))
    ck("claim: the issue is adopted, not duplicated", "9",
       cat(F.fabro / "orientation/issue_number"))
    ck("claim: and no compare is asked for", "0", count("compare", F.gh()))
    ck("claim: no GitHub write", "0", count("issue create|issue pin|-X PATCH", F.gh()))


def test_claim_counts_the_move_through_the_api_not_the_shallow_clone(ck, tmp_path):
    """The sandbox clone is shallow and single-branch (fabro clones at depth 100), so `git log`
    cannot say how far `main` has moved; only `repos/O/R/compare` can."""
    F = Orientation(ck, tmp_path, "claim-compare")
    old = "1" * 40
    F.issues_json([{"number": 9, "body": orientation_body(old, "2026-11-01T00:00:00Z")}])
    r = F.run("claim", RO_COMPARE="12")
    ck("claim: 10 or more commits re-maps at once", "true", routed(r, "orientation_go"))
    ck("claim: the compare is the API's", "1", count(f"repos/o/r/compare/{old}\\.\\.\\.{F.main}", F.gh(), flags="E"))
    ck("claim: and the count is kept", "12", cat(F.fabro / "orientation/commits"))
    ck("claim: the earlier map is staged for the agent", "1",
       count("^A map\\.$", cat(F.fabro / "orientation/prior.md"), flags="E"))


def test_claim_maps_when_main_has_moved_and_the_map_is_a_day_old(ck, tmp_path):
    F = Orientation(ck, tmp_path, "claim-day")
    F.issues_json([{"number": 9, "body": orientation_body("2" * 40, "2026-10-01T00:00:00Z")}])
    r = F.run("claim", RO_COMPARE="3")
    ck("claim: a day-old map is refreshed", "true", routed(r, "orientation_go"))
    ck("claim: and the earlier body is kept as the prior", "1",
       count("A map.", cat(F.fabro / "orientation/prior.md")))
    ck("claim: without the marker line", "0",
       count("fabro:orientation", cat(F.fabro / "orientation/prior.md")))


def test_claim_stops_on_a_map_that_is_under_a_day_old_and_under_ten_commits_behind(ck, tmp_path):
    F = Orientation(ck, tmp_path, "claim-fresh")
    F.issues_json([{"number": 9, "body": orientation_body("3" * 40, "2026-11-03T08:00:00Z")}])
    # `now` in the run is real, so build the timestamp from it rather than a literal.
    import datetime
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    F.issues_json([{"number": 9, "body": orientation_body("3" * 40, now)}])
    r = F.run("claim", RO_COMPARE="3")
    ck("claim: 3 commits behind and fresh is not worth a run", "false",
       routed(r, "orientation_go"))


def test_claim_re_maps_when_the_compare_cannot_be_read(ck, tmp_path):
    F = Orientation(ck, tmp_path, "claim-nocompare")
    F.issues_json([{"number": 9, "body": orientation_body("4" * 40, "2026-11-03T08:00:00Z")}])
    r = F.run("claim", RO_COMPARE="not-a-number")
    ck("claim: an unreadable compare re-maps rather than stopping", "true",
       routed(r, "orientation_go"))


def test_claim_adopts_an_issue_that_carries_the_label_and_no_marker(ck, tmp_path):
    F = Orientation(ck, tmp_path, "claim-nomarker")
    F.issues_json([{"number": 41, "body": "a hand-written body"}])
    r = F.run("claim")
    ck("claim: a markerless issue is re-mapped", "true", routed(r, "orientation_go"))
    ck("claim: and edited rather than duplicated", "41",
       cat(F.fabro / "orientation/issue_number"))
    ck("claim: no create", "0", count("issue create", F.gh()))


def test_claim_takes_no_issue_number(ck):
    script = extract(GRAPH, "claim")
    ck("claim: interpolates no issue_number", "0", count("inputs.issue_number", script))
    ck("claim: interpolates coder_pool once", "1", count(r"{{ inputs.coder_pool }}", script))
    ck("claim: and writes the box file for box-guard", "1", count("/tmp/fabro/box", script))


# --- prep, gate, post, in the graph's order ----------------------------------------


def test_the_run_in_order_keeps_every_file_the_next_node_reads(ck, tmp_path):
    F = Orientation(ck, tmp_path, "order")
    F.issues_json([])
    ck("run: claim", "true", routed(F.run("claim"), "orientation_go"))
    r = F.run("prep", FABRO_AGENT_GUIDE="# guide")
    ck("run: prep routes", "true", routed(r, "prep_ok"))
    ck("run: prep writes main_sha to this package's directory", F.main,
       cat(F.fabro / "orientation/main_sha"))


MAP = {
    "summary": "A web app and a backend in one repository.",
    "modules": [
        {"path": "src", "what": "the frontend",
         },
        {"path": "nope", "what": "a directory the model imagined"},
        {"path": "../outside", "what": "outside the checkout"},
    ],
    "conventions": ["Every new file gets a test beside it."],
    "tests": [
        {"path": "tests/one.test.ts", "what": "the unit tests"},
        {"path": "tests/gone.test.ts", "what": "a test file that is not there"},
    ],
    "commands": [{"run": "npm test", "why": "runs the suite"}],
    "gotchas": ["src/generated.ts is written by the build."],
}


def deliver(F: Orientation, payload: dict, *, receipt: bool = True) -> None:
    write(F.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
    if receipt:
        payload = {**payload, "_io": {"visit": "v1"}}
    write(F.fabro / "orientation.json", json.dumps(payload))


def test_the_whole_chain_in_order_writes_the_issue_body(ck, tmp_path):
    """claim -> prep -> the agent's file -> gate -> post, one `/tmp/fabro`, as the graph runs it."""
    F = Orientation(ck, tmp_path, "chain")
    F.issues_json([])
    ck("chain: claim", "true", routed(F.run("claim"), "orientation_go"))
    ck("chain: prep", "true", routed(F.run("prep", FABRO_AGENT_GUIDE="# guide"), "prep_ok"))
    deliver(F, MAP)
    ck("chain: gate", "true", routed(F.run("gate"), "orientation_ok"))
    ck("chain: post", "true", routed(F.run("post"), "posted"))
    body = json.loads(cat(F.T / "patched.md"))["body"] if F.patched.exists() else None
    if body is None:
        # The chain created the issue; the body file is what was passed to `gh issue create`.
        body = cat(F.fabro / "orientation/body.full")
    # `run=` is the `orientation-run-id` hook's file, which the harness does not run; `post`
    # reads an absent one as `unknown` (a real run's hook writes it).
    ck("chain: the marker is the first line", "1",
       count(r"^<!-- fabro:orientation main=[0-9a-f]{40} at=[0-9T:Z-]+ run=\S+ -->$",
             body.split("\n", 1)[0], flags="E"))
    ck("chain: the map is published", "1", count("^- `src`: the frontend$", body, flags="E"))
    ck("chain: without the imagined directory", "0", count("nope", body))
    ck("chain: and with the commands", "1", count("^- `npm test`: runs the suite$", body, flags="E"))
    ck("chain: under the cap", "true", str(len(body) <= 6000).lower())
    ck("chain: pinned once, on the run that created it", "1", count("^issue pin 77$", F.gh()))


def test_gate_refuses_a_map_with_no_receipt(ck, tmp_path):
    F = Orientation(ck, tmp_path, "gate-noreceipt")
    deliver(F, MAP, receipt=False)
    r = F.run("gate")
    ck("gate: no receipt fails the node", "1", str(r.rc))
    ck("gate: and renders nothing", "absent", present(F.fabro / "orientation/comment.md"))


def test_gate_drops_what_is_not_in_the_checkout_and_renders_the_rest(ck, tmp_path):
    F = Orientation(ck, tmp_path, "gate-ok")
    deliver(F, MAP)
    r = F.run("gate")
    ck("gate: succeeds", "0", str(r.rc))
    ck("gate: routes", "true", routed(r, "orientation_ok"))
    ck("gate: counts what it dropped", "3", cat(F.fabro / "orientation/dropped"))
    md = cat(F.fabro / "orientation/comment.md")
    ck("gate: keeps a real module", "1", count("^- `src`: the frontend$", md))
    ck("gate: drops an imagined one", "0", count("nope", md))
    ck("gate: drops one outside the checkout", "0", count("outside", md))
    ck("gate: keeps a real test", "1", count("^- `tests/one.test.ts`: the unit tests$", md))
    ck("gate: drops an imagined test", "0", count("gone.test.ts", md))
    ck("gate: renders the conventions", "1", count("Every new file gets a test beside it", md))
    ck("gate: renders the commands as the repository runs them", "1",
       count("^- `npm test`: runs the suite$", md))
    ck("gate: renders the gotchas", "1", count("generated.ts is written by the build", md))
    ck("gate: says where it came from", "1", count("at `main`", md))


def test_gate_fails_a_map_with_no_module_left(ck, tmp_path):
    F = Orientation(ck, tmp_path, "gate-nomodule")
    deliver(F, {**MAP, "modules": [{"path": "nope", "what": "x"}]})
    r = F.run("gate")
    ck("gate: no module fails the node", "1", str(r.rc))
    ck("gate: and says so", "1", count("nothing in it is not posted", r.err or r.out))


def test_gate_holds_the_map_under_its_cap(ck, tmp_path):
    F = Orientation(ck, tmp_path, "gate-big")
    deliver(F, {**MAP,
                "summary": "s" * 600,
                "modules": [{"path": "src", "what": "w" * 200} for _ in range(10)],
                "conventions": ["c" * 220 for _ in range(8)],
                "tests": [{"path": "tests", "what": "t" * 200} for _ in range(6)],
                "commands": [{"run": "r" * 200, "why": "y" * 160} for _ in range(6)],
                "gotchas": ["g" * 220 for _ in range(5)]})
    r = F.run("gate")
    ck("gate: a long map still succeeds", "0", str(r.rc))
    ck("gate: the rendered body fits the reader's cap", "true",
       str(len(cat(F.fabro / "orientation/comment.md")) <= 6000).lower())
    ck("gate: and says it was cut", "1", count("cut to fit", cat(F.fabro / "orientation/comment.md")))


def staged_for_post(F: Orientation, md: str = "## Summary\nA map.\n") -> None:
    for name, value in (("repo", "o/r"), ("main_sha", "a" * 40), ("run_id", "01RUN")):
        write(F.fabro / f"orientation/{name}", value + "\n")
    write(F.fabro / "orientation/comment.md", md)


def test_post_creates_the_issue_with_one_label_and_pins_it(ck, tmp_path):
    F = Orientation(ck, tmp_path, "post-create")
    staged_for_post(F)
    r = F.run("post")
    ck("post: succeeds", "0", str(r.rc))
    ck("post: routes", "true", routed(r, "posted"))
    ck("post: the label is created first, so the issue can carry it", "1",
       count(f"^label create {LABEL} ", F.gh()))
    ck("post: the issue is created with that label and no other", "1",
       count(f"issue create --title fabro: repository orientation --body-file \\S+ --label {LABEL}$",
             F.gh(), flags="E"))
    ck("post: and pinned", "1", count("^issue pin 77$", F.gh()))
    created = cat(F.T / "issues.json")
    ck("post: the issue number is recorded for the next run", "77",
       cat(F.fabro / "orientation/issue_number"))
    body = cat(F.T / "gh.log")
    ck("post: no edit of an issue that did not exist", "0", count("-X PATCH", body))


def test_post_edits_the_body_in_place_and_writes_the_marker_first(ck, tmp_path):
    F = Orientation(ck, tmp_path, "post-edit")
    staged_for_post(F)
    write(F.fabro / "orientation/issue_number", "9\n")
    r = F.run("post")
    ck("post: succeeds", "0", str(r.rc))
    ck("post: edits issue 9", "1", count("^api -X PATCH repos/o/r/issues/9", F.gh()))
    ck("post: creates nothing", "0", count("issue create", F.gh()))
    ck("post: and pins nothing", "0", count("issue pin", F.gh()))
    body = cat(F.patched and F.T / "patched.md")
    first = json.loads(body)["body"].split("\n", 1)[0]
    ck("post: the marker is the first line", "1",
       count(r"^<!-- fabro:orientation main=a{40} at=[0-9T:Z-]+ run=01RUN -->$", first, flags="E"))
    ck("post: the map follows it", "1", count("A map.", json.loads(body)["body"]))


def test_post_survives_a_pin_that_fails(ck, tmp_path):
    F = Orientation(ck, tmp_path, "post-pinfail")
    staged_for_post(F)
    # A `gh` that fails only `issue pin`.
    stub(F.bin, "gh", GH_STUB.replace('  "issue pin")     : ;;',
                                      '  "issue pin")     exit 1 ;;'))
    r = F.run("post")
    ck("post: a failed pin does not fail the run", "0", str(r.rc))
    # `post` redirects its own stderr to its log (as the precheck's does), so the line is there.
    ck("post: and says so", "1", count("could not pin it", cat(F.fabro / "orientation/post.log")))


def test_post_scrubs_tokens(ck, tmp_path):
    F = Orientation(ck, tmp_path, "post-scrub")
    secret = "ghs_supersecretvalue123"
    staged_for_post(F, md=f"## Summary\nA map with {secret} in it.\n")
    F.run("post", GH_TOKEN=secret)
    body = cat(F.T / "gh.log")
    ck("post: the creation carries no token", "0", count(secret, cat(F.T / "issues.json")))
    ck("post: the body file has none either", "0", count(secret, cat(F.fabro / "orientation/body.full")))


# --- the reader in backlog and ralph ------------------------------------------------


def prep_reader(graph: Path) -> str:
    """The orientation block `prep` runs, as the graph spells it."""
    script = extract(graph, "prep")
    start = script.find("rm -f /tmp/fabro/orientation.md")
    assert start >= 0, "prep does not read the orientation"
    end = script.find("rm -f /tmp/fabro/orientation.raw")
    assert end > start
    return script[start:end + len("rm -f /tmp/fabro/orientation.raw /tmp/fabro/orientation.tmp /tmp/fabro/orientation.tmp2")]


def test_the_reader_is_the_same_block_in_both_graphs(ck):
    a, b = prep_reader(BACKLOG), prep_reader(RAPH)
    ck("reader: identical in backlog and ralph (R10)", "1", str(int(a == b)))
    ck("reader: one gh call, by label", "1",
       count(f"gh issue list --label {LABEL} --state open", a, flags="F"))
    ck("reader: capped at 6,000 characters", "1", count("6000", a, flags="F"))
    ck("reader: the marker line is stripped", "1", count("sed 1d", a, flags="F"))
    ck("reader: it never fails the node", "1", count("the run goes on without it", a, flags="F"))
    ck("reader: its one message goes to stderr", "1",
       count("the run goes on without it' >&2", a, flags="F"))


def test_the_reader_stages_the_body_and_tolerates_every_failure(ck, tmp_path):
    """Run `prep`'s block on its own against a stub `gh`: present, absent, and failing."""
    for name, issues, env in (
        ("present", [{"number": 9, "body": orientation_body("a" * 40, "2026-11-01T00:00:00Z")}], {}),
        ("absent", [], {}),
        ("nobody", [], {"RO_ISSUES_JSON": "/nonexistent.json"}),
        ("unmarked", [{"number": 9, "body": "no marker here"}], {}),
    ):
        F = Orientation(ck, tmp_path, f"reader-{name}")
        F.issues_json(issues)
        block = prep_reader(BACKLOG)
        # The block is a fragment of a bigger script: run it standalone, as the graph does.
        script = rebase(block, F.fabro)
        path = write(F.T / "reader.sh", "set -e\n" + script)
        r = sh(path, cwd=F.repo, path=f"{F.bin}:{ORIG_PATH}",
               env={"RO_GH_LOG": str(F.log), "RO_ISSUES_JSON": str(F.T / "issues.json"), **env},
               stderr="keep")
        ck(f"reader {name}: the block succeeds under set -e", "0", str(r.rc))
        ck(f"reader {name}: and prints nothing to stdout", "", (r.out or "").strip())
        if name == "present":
            staged = cat(F.fabro / "orientation.md")
            ck("reader present: the map is staged", "1", count("^A map\\.$", staged, flags="E"))
            ck("reader present: without the marker", "0", count("fabro:orientation", staged))
        else:
            ck(f"reader {name}: no file, so the input is absent",
               "absent", present(F.fabro / "orientation.md"))


def test_the_reader_caps_a_long_body(ck, tmp_path):
    F = Orientation(ck, tmp_path, "reader-big")
    F.issues_json([{"number": 9, "body": orientation_body("a" * 40, "2026-11-01T00:00:00Z",
                                                         text="x" * 9000)}])
    path = write(F.T / "reader.sh", "set -e\n" + rebase(prep_reader(BACKLOG), F.fabro))
    sh(path, cwd=F.repo, path=f"{F.bin}:{ORIG_PATH}",
       env={"RO_GH_LOG": str(F.log), "RO_ISSUES_JSON": str(F.T / "issues.json")})
    staged = cat(F.fabro / "orientation.md")
    ck("reader: a long body is cut", "1", count("cut to 6000 characters", staged))
    ck("reader: and fits the cap", "true", str(len(staged) <= 6100).lower())


# --- the Stage I/O and manifest wiring ---------------------------------------------


def test_the_planners_read_it_as_an_optional_input(ck):
    manifest = json.loads((WF / "_io" / "manifest.json").read_text())
    for wf, stage in (("backlog", "decompose"), ("backlog", "plan"), ("ralph", "plan")):
        inputs = {i["name"]: i for i in manifest["workflows"][wf]["stages"][stage]["inputs"]}
        assert "orientation.md" in inputs, (wf, stage)
        entry = inputs["orientation.md"]
        ck(f"{wf} {stage}: optional", "false", str(entry["required"]).lower())
        ck(f"{wf} {stage}: at the path prep writes", "/tmp/fabro/orientation.md", entry["path"])
        ck(f"{wf} {stage}: with an absent sentence", "true",
           str(bool(entry.get("absent"))).lower())
        ck(f"{wf} {stage}: and an about that says what to do with it", "true",
           str("check" in entry["about"].lower()).lower())


def test_the_package_manifest_and_its_registrations(ck):
    toml = (WF / "repo-orientation" / "workflow.toml").read_text()
    ck("toml: no run branch, because a background run never pushes", "true",
       str("enabled = false" in toml).lower())
    ck("toml: no fallbacks table", "0", count(r"^\[run\.model\.fallbacks\]", toml, flags="E"))
    ck("toml: no box-claim", "0", count("box-claim", toml))
    ck("toml: box-guard is wired", "1", count("fabro-io box-guard", toml, flags="F"))
    ck("toml: the marker can carry the run id", "1", count("orientation/run_id", toml, flags="F"))
    ck("graph: one agent stage", "1", count('class="bg"', (WF / "repo-orientation" / "workflow.fabro").read_text(), flags="F"))
    invariants = (REPO_ROOT / "ops" / "check-graph-invariants.py").read_text()
    ck("checker: it is a background package", "1",
       count('"repo-orientation"]', invariants, flags="F"))
    host = (REPO_ROOT / "ops" / "check-host.sh").read_text()
    ck("check-host: it is validated", "1", count("[repo-orientation]=", host, flags="F"))
    scheduler = (REPO_ROOT / "ops" / "scheduler" / "src" / "fabro_scheduler" / "workflow_version.py").read_text()
    ck("scheduler: its entrypoint is registered", "1",
       count('"repo-orientation": REPO_ORIENTATION_ENTRYPOINT', scheduler, flags="F"))


def test_every_failure_ends_the_run_and_only_gate_reaches_post(ck):
    e = edges(GRAPH)
    for src in ("claim", "prep", "gate", "post", "stop"):
        ck(f"edge: {src} has an unconditional edge to exit", "1", count(f"{src}->exit ", e, flags="xF"))
    ck("edge: post is reached only from gate", "1", count("->post", e, flags="F"))
    ck("edge: and only when gate succeeded", "1", count("gate->post outcome=succeeded", e, flags="xF"))
    ck("edge: claim routes to stop or to prep", "1",
       count("claim->stop outcome=succeeded && context.orientation_go=false", e, flags="xF"))
    ck("edge: the map only runs when the claim said so", "1",
       count("claim->prep outcome=succeeded && context.orientation_go=true", e, flags="xF"))


# --- the label rule (D22) -----------------------------------------------------------


SELECTORS = (
    # Every label an automation in this repository selects issues by, and where. A new selector
    # has to be added here; the test below proves none of them is the orientation's label.
    ("ops/scheduler/src/fabro_scheduler/github.py", "labels=agent"),
    ("ops/scheduler/src/fabro_scheduler/github.py", "agent-in-progress"),
    ("ops/scheduler/src/fabro_scheduler/github.py", "agent-stuck"),
    (".fabro/workflows/issue-triage/workflow.fabro", "--label needs-triage"),
    (".fabro/workflows/issue-triage/workflow.fabro", "--label needs-info"),
    (".fabro/workflows/arch-review/workflow.fabro", "--label architecture"),
    (".fabro/workflows/arch-review/workflow.fabro", "--label needs-triage"),
    (".fabro/workflows/arch-review/workflow.fabro", "--label needs-info"),
    (".fabro/workflows/dependabot/workflow.fabro", "--label security"),
    (".fabro/workflows/ralph/workflow.fabro", "--label learning"),
    (".fabro/workflows/scout/workflow.fabro", "labels=scout"),
)


# Every file allowed to name the label: the package, the reader in `prep` (both graphs), and
# the output schema's description. Anything else naming it is a selector, and D22 says there
# must be none.
ALLOWED = {
    ".fabro/workflows/repo-orientation/workflow.fabro",
    ".fabro/workflows/repo-orientation/workflow.toml",
    ".fabro/workflows/backlog/workflow.fabro",
    ".fabro/workflows/ralph/workflow.fabro",
    ".fabro/workflows/_io/schemas/repo_orientation.schema.json",
}


def test_no_automation_selects_the_orientation_label(ck):
    """D22: the pinned issue must never be picked up by a run. Every selector, then every file."""
    for path, needle in SELECTORS:
        text = (REPO_ROOT / path).read_text()
        ck(f"selector exists: {needle} in {path}", "true", str(needle in text).lower())
        ck(f"and is not the orientation label: {needle}", "0", count(LABEL, needle, flags="F"))
    found = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in REPO_ROOT.glob(".fabro/workflows/**/*")
        if path.is_file() and LABEL in path.read_text(errors="replace")
    )
    ck("no file outside that list names the label", "", "\n".join(sorted(set(found) - ALLOWED)))


def test_the_package_writes_the_label_and_no_other(ck):
    """The issue must never carry `agent`, `scout` or any other label an automation selects on."""
    for node in ("claim", "stop", "prep", "gate", "post"):
        script = extract(GRAPH, node)
        for other in ("agent", "scout", "bug", "fast-path", "ai-generated", "priority"):
            ck(f"{node}: never adds {other}", "0",
               count(f"--add-label {other}", script, flags="F"))
    post = extract(GRAPH, "post")
    ck("post: the one label it sets is the orientation one", "1",
       count(f"--label {LABEL}", post, flags="F"))
    ck("post: and it is the only label on the create", "0",
       count("--label ", post.replace(f"--label {LABEL}", ""), flags="F"))
