"""The context pack's command nodes (more-local-inference 08): claim, gate and post.

`fabro validate` proves the graph parses. It never runs a node's script, and every rule that
matters here is a runtime one:

* `claim` must write `/tmp/fabro/box` (R16), stage `issue.md` with the precheck's note, and
  route a closed issue, or one whose current text already has a fresh pack (C6
  *Staleness*), to `stop` **without touching GitHub**.
* `gate` must refuse a map with no Input receipt, drop every file and test whose path is not
  in the checkout, fail a map with no file left, and fail one over 8,000 characters.
* `post` is the only GitHub writer. It posts the marker comment, or edits the existing one in
  place, with the marker on the first line and with no token in it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from harness import (ORIG_PATH, WF, cat, count, edges, extract, git, jq, lastjson, present,
                     rebase, sh, stub, write)

GRAPH = WF / "context-pack" / "workflow.fabro"

# A `gh` that answers every call the pack makes and records each one, in order.
GH_STUB = """#!/bin/sh
log="${CP_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
case "$1 $2" in
  "issue view")    cat "${CP_ISSUE_JSON:-/dev/null}" ;;
  "issue comment") cat "$5" > "${CP_POSTED:?}"; echo comment ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *"-X PATCH"*) cat > "${CP_POSTED:?}" ;;
    *"comments?per_page="*) cat "${CP_COMMENTS_JSON:-/dev/null}" ;;
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
HASH = hashlib.sha256(f"{ISSUE['title']}\n{ISSUE['body']}".encode()).hexdigest()[:12]

SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b-bg"),
)


def comments(*bodies: str) -> str:
    return json.dumps(
        [
            {"id": 100 + i, "user": {"login": "someone"}, "created_at": "2026-10-30T10:00:00Z",
             "body": b}
            for i, b in enumerate(bodies)
        ]
    )


def iso(delta: timedelta) -> str:
    return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


class Pack:
    """A scratch checkout at `main` with a stub `gh` and a per-test `/tmp/fabro`."""

    def __init__(self, ck, tmp_path, name: str):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.fabro / "pack", self.fabro / ".io", self.bin, self.repo / "src",
                  self.repo / "tests"):
            d.mkdir(parents=True, exist_ok=True)
        write(self.repo / "src/theme.ts", "export const theme = 1;\n")
        write(self.repo / "tests/theme.test.ts", "test('x', () => {});\n")
        git("init", "-q", "-b", "main", self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base",
            cwd=self.repo)
        self.main = git("rev-parse", "HEAD", cwd=self.repo)
        stub(self.bin, "gh", GH_STUB)
        self.log = self.T / "gh.log"
        write(self.log, "")
        self.posted = self.T / "posted.md"
        self.nodes = {}
        for node in ("claim", "prep", "gate", "post"):
            script = extract(GRAPH, node)
            for token, value in SUBSTITUTIONS:
                script = script.replace(token, value)
            self.nodes[node] = rebase(script, self.fabro)
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", self.nodes[node]))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)
        write(self.T / "issue.json", json.dumps(ISSUE))
        write(self.T / "comments.json", comments())

    def run(self, node: str, **env: str):
        base = {
            "CP_GH_LOG": str(self.log),
            "CP_ISSUE_JSON": str(self.T / "issue.json"),
            "CP_COMMENTS_JSON": str(self.T / "comments.json"),
            "CP_POSTED": str(self.posted),
        }
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base,
                  stderr="keep")

    def gh(self) -> str:
        return cat(self.log)

    def pack_file(self, payload: dict, *, receipt: bool = True) -> None:
        write(self.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
        if receipt:
            payload = {**payload, "_io": {"visit": "v1"}}
        write(self.fabro / "pack.json", json.dumps(payload))


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


def pack_marker(body: str, main: str, at: str) -> str:
    return f"<!-- fabro:context-pack body={body} main={main} at={at} run=R -->\nold map"


# --- claim -------------------------------------------------------------------------


def test_claim_stages_the_issue_with_the_precheck_note(ck, tmp_path):
    P = Pack(ck, tmp_path, "claim-ok")
    write(P.T / "comments.json", comments(
        "Looks related to #40.",
        "<!-- fabro:precheck verdict=ready body=x main=y at=z run=r -->\n\n"
        "**Precheck: ready.**\n\n- `src/theme.ts:1`: the theme lives here",
    ))
    r = P.run("claim")
    ck("claim: routes to prep", "true", routed(r, "pack_go"))
    ck("claim: box written without the -bg suffix", "coders-b", cat(P.fabro / "box"))
    ck("claim: the repository is read from the issue url", "o/r", cat(P.fabro / "pack/repo"))
    ck("claim: main= is the checkout's HEAD", P.main, cat(P.fabro / "pack/main_sha"))
    ck("claim: body= is C6's hash", HASH, cat(P.fabro / "pack/body_hash"))
    issue_md = cat(P.fabro / "pack/issue.md")
    ck("claim: issue.md carries the title", "1", count("^# #42: Add a dark mode toggle$", issue_md))
    ck("claim: issue.md carries a human comment", "1", count("Looks related to #40", issue_md))
    ck("claim: issue.md carries the precheck note", "1",
       count("`src/theme.ts:1`: the theme lives here", issue_md))
    ck("claim: but not the marker line", "0", count("fabro:precheck", issue_md))
    ck("claim: no GitHub write", "0", count("issue comment|-X PATCH", P.gh()))
    ck("claim: stdout is one routing object", "1", count("^{", r.out))


def test_claim_template_values_are_the_ones_the_graph_names(ck):
    script = extract(GRAPH, "claim")
    ck("claim: interpolates issue_number once", "1", count(r"{{ inputs.issue_number }}", script))
    ck("claim: interpolates coder_pool once", "1", count(r"{{ inputs.coder_pool }}", script))
    ck("claim: quotes issue_number before the guard", "1",
       count(r"N='{{ inputs.issue_number }}'", script))


def test_claim_follows_c6_staleness(ck, tmp_path):
    day = timedelta(hours=25)
    hour = timedelta(hours=1)
    for name, issue, notes, go in (
        ("closed", {**ISSUE, "state": "CLOSED"}, None, "false"),
        ("fresh-same-main", ISSUE, lambda m: pack_marker(HASH, m, iso(day)), "false"),
        ("fresh-recent", ISSUE, lambda m: pack_marker(HASH, "0" * 40, iso(hour)), "false"),
        ("stale-main-moved", ISSUE, lambda m: pack_marker(HASH, "0" * 40, iso(day)), "true"),
        ("stale-text-changed", ISSUE, lambda m: pack_marker("000000000000", m, iso(hour)), "true"),
    ):
        P = Pack(ck, tmp_path, f"claim-{name}")
        write(P.T / "issue.json", json.dumps(issue))
        if notes is not None:
            write(P.T / "comments.json", comments(notes(P.main)))
        r = P.run("claim")
        ck(f"claim {name}: pack_go", go, routed(r, "pack_go"))
        ck(f"claim {name}: no GitHub write", "0", count("issue comment|-X PATCH", P.gh()))


def test_claim_refuses_a_non_numeric_issue_number(ck, tmp_path):
    P = Pack(ck, tmp_path, "claim-nan")
    P.nodes["claim"] = P.nodes["claim"].replace("N='42'", "N='42;rm -rf /'")
    r = P.run("claim")
    ck("claim: a non-numeric issue_number fails the node", "1", str(r.rc))
    ck("claim: and writes no routing object", "", lastjson(r.out))


def test_prep_excludes_what_it_installs(ck, tmp_path):
    P = Pack(ck, tmp_path, "prep")
    r = P.run("prep", FABRO_AGENT_GUIDE="# guide")
    ck("prep: routes to gather", "true", routed(r, "prep_ok"))
    ck("prep: the guide is installed", "# guide", cat(P.repo / ".codex/instructions.md"))
    ck("prep: and excluded from git", "1",
       count("^.codex/instructions.md$", cat(P.repo / ".git/info/exclude")))


# --- gate --------------------------------------------------------------------------


MAP = {
    "files": [
        {"path": "src/theme.ts", "lines": "1-1", "why": "the theme constant"},
        {"path": "src/missing.ts", "why": "a file the model imagined"},
        {"path": "../outside.ts", "why": "outside the checkout"},
    ],
    "symbols": [{"name": "theme", "where": "src/theme.ts:1", "callers": ["src/app.ts:4"]}],
    "tests": [
        {"path": "tests/theme.test.ts", "covers": "the theme"},
        {"path": "tests/nope.test.ts", "covers": "nothing real"},
    ],
    "commands": [{"run": "npm test", "why": "runs the suite"}],
    "task_map": ["Add the toggle", "Wire it to the theme"],
    "notes": "The theme is also read by the server.",
}


def test_gate_refuses_a_map_with_no_receipt(ck, tmp_path):
    P = Pack(ck, tmp_path, "gate-noreceipt")
    P.pack_file(MAP, receipt=False)
    r = P.run("gate")
    ck("gate: no receipt fails the node", "1", str(r.rc))
    ck("gate: and renders nothing", "absent", present(P.fabro / "pack/pack.md"))


def test_gate_drops_what_is_not_in_the_checkout_and_renders_the_rest(ck, tmp_path):
    P = Pack(ck, tmp_path, "gate-ok")
    P.pack_file(MAP)
    r = P.run("gate")
    ck("gate: succeeds", "0", str(r.rc))
    ck("gate: routes", "true", routed(r, "pack_ok"))
    ck("gate: counts what it dropped", "3", cat(P.fabro / "pack/dropped"))
    md = cat(P.fabro / "pack/pack.md")
    ck("gate: keeps a real file with its lines", "1",
       count("^- `src/theme.ts` lines 1-1: the theme constant$", md))
    ck("gate: drops an imagined file", "0", count("missing.ts", md))
    ck("gate: drops a path outside the checkout", "0", count("outside.ts", md))
    ck("gate: keeps a real test", "1", count("^- `tests/theme.test.ts`: the theme$", md))
    ck("gate: drops an imagined test", "0", count("nope.test.ts", md))
    ck("gate: renders the symbol and its caller", "1",
       count("^- `theme` at `src/theme.ts:1`; called from `src/app.ts:4`$", md))
    ck("gate: renders the command", "1", count("^- `npm test`: runs the suite$", md))
    ck("gate: numbers the task map", "1", count("^2. Wire it to the theme$", md))
    ck("gate: renders the notes", "1", count("^The theme is also read by the server.$", md))


def test_gate_fails_a_map_with_no_real_file(ck, tmp_path):
    P = Pack(ck, tmp_path, "gate-nofile")
    P.pack_file({"files": [{"path": "nope.ts", "why": "x"}], "symbols": [], "tests": [],
                 "commands": []})
    r = P.run("gate")
    ck("gate: no real file fails the node", "1", str(r.rc))
    ck("gate: and says so", "1", count("no file in the map exists", r.err or r.out))


def test_gate_fails_a_map_over_8000_characters(ck, tmp_path):
    P = Pack(ck, tmp_path, "gate-big")
    P.pack_file({
        "files": [{"path": "src/theme.ts", "why": "w" * 200} for _ in range(12)],
        "symbols": [{"name": "n" * 120, "where": "s" * 200, "callers": ["c" * 120] * 3}
                    for _ in range(10)],
        "tests": [], "commands": [],
    })
    r = P.run("gate")
    ck("gate: an oversized map fails the node", "1", str(r.rc))
    ck("gate: and says so", "1", count("over the 8,000", r.err or r.out))


# --- post --------------------------------------------------------------------------


def staged_for_post(P: Pack, md: str = "### Files\n- `src/theme.ts`: the theme\n") -> None:
    for name, value in (("issue_number", "42"), ("repo", "o/r"), ("body_hash", HASH),
                        ("main_sha", "f" * 40), ("run_id", "01RUN")):
        write(P.fabro / f"pack/{name}", value + "\n")
    write(P.fabro / "pack/pack.md", md)


def test_post_writes_the_marker_first_and_posts_a_new_comment(ck, tmp_path):
    P = Pack(ck, tmp_path, "post-new")
    staged_for_post(P)
    r = P.run("post")
    ck("post: succeeds", "0", str(r.rc))
    ck("post: routes", "true", routed(r, "posted"))
    body = cat(P.posted)
    ck("post: the marker is the first line", "1", count(
        rf"^<!-- fabro:context-pack body={HASH} main=f{{40}} at=[0-9T:Z-]+ run=01RUN -->$",
        body.split("\n", 1)[0], flags="E"))
    ck("post: a new comment, not an edit", "1", count("^issue comment 42 --body-file", P.gh()))
    ck("post: the map is published", "1", count("`src/theme.ts`: the theme", body))


def test_post_edits_the_existing_marker_in_place(ck, tmp_path):
    P = Pack(ck, tmp_path, "post-edit")
    staged_for_post(P)
    write(P.T / "comments.json", comments(
        "unrelated", "<!-- fabro:precheck verdict=ready body=0 main=0 at=0 run=0 -->\nready",
        pack_marker("000000000000", "0", "0")))
    P.run("post")
    ck("post: edits comment 102", "1", count("^api -X PATCH repos/o/r/issues/comments/102", P.gh()))
    ck("post: posts no second comment", "0", count("^issue comment", P.gh()))
    ck("post: the edit carries the new marker", "1",
       count(f"fabro:context-pack body={HASH}", jq(".body", file=P.posted)))


def test_post_scrubs_tokens(ck, tmp_path):
    P = Pack(ck, tmp_path, "post-scrub")
    secret = "ghs_supersecretvalue123"
    staged_for_post(P, md=f"### Notes\ntoken {secret}\n")
    P.run("post", GH_TOKEN=secret)
    body = cat(P.posted)
    ck("post: the token is redacted", "0", count(secret, body))
    ck("post: in place", "1", count(r"token \[redacted\]", body))


# --- routing -----------------------------------------------------------------------


def test_every_failure_ends_the_run_and_only_gate_reaches_post(ck):
    e = edges(GRAPH)
    for src in ("claim", "prep", "gate", "post", "stop"):
        ck(f"edge: {src} has an unconditional edge to exit", "1", count(f"{src}->exit ", e, flags="xF"))
    ck("edge: post is reached only from gate", "1", count("->post", e, flags="F"))
    ck("edge: and only when gate succeeded", "1", count("gate->post outcome=succeeded", e, flags="xF"))
    ck("edge: gather always goes to gate", "1", count("gather->gate ", e, flags="xF"))
