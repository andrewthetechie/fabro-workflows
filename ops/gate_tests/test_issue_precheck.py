"""The issue precheck's command nodes (more-local-inference 07): claim, prep, gate and post.

`fabro validate` proves the graph parses. It never runs a node's script, and every rule that
matters here is a runtime one:

* `claim` must write `/tmp/fabro/box` (R16), stage `issue.md` and `open_issues.md` for the
  check, and route a closed issue, or one whose current text already has a precheck, to
  `stop` **without touching GitHub**.
* `claim`'s `body=` hash is C6's: the first 12 hex characters of the SHA-256 of the title, a
  newline and the body. The scheduler will compare it with its own (task 07 step 4).
* `gate` must refuse a check with no Input receipt, and apply task 07's two rules: a verdict
  other than `ready` with no evidence is `ready`, and so is a `blocked` whose blocker is not
  another open issue in the repository.
* `post` is the only GitHub writer. It posts the marker comment, or edits the existing one in
  place, with the marker on the first line, capped at 1,500 characters (cut at a whole line,
  keeping the shadow-mode footer), and with no token in it.
"""

from __future__ import annotations

import hashlib
import json

from harness import (ORIG_PATH, WF, cat, count, edges, extract, git, jq, lastjson, present,
                     rebase, sh, stub, write)

GRAPH = WF / "issue-precheck" / "workflow.fabro"

# A `gh` that answers every call the precheck makes and records each one, in order.
GH_STUB = """#!/bin/sh
log="${PC_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
case "$1 $2" in
  "issue view")    cat "${PC_ISSUE_JSON:-/dev/null}" ;;
  "issue list")    cat "${PC_OPEN_JSON:-/dev/null}" ;;
  "issue comment") cat "$5" > "${PC_POSTED:?}"; echo comment ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *"-X PATCH"*) cat > "${PC_POSTED:?}" ;;
    *"comments?per_page="*) cat "${PC_COMMENTS_JSON:-/dev/null}" ;;
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
OPEN_ISSUES = [
    {"number": 42, "title": "Add a dark mode toggle"},
    {"number": 40, "title": "Add a settings page"},
]


def body_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()[:12]


def comments(*bodies: str) -> str:
    return json.dumps(
        [
            {"id": 100 + i, "user": {"login": "someone"}, "created_at": "2026-10-30T10:00:00Z",
             "body": b}
            for i, b in enumerate(bodies)
        ]
    )


SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b-bg"),
)


class Precheck:
    """A scratch checkout with a stub `gh` and a per-test `/tmp/fabro`."""

    def __init__(self, ck, tmp_path, name: str):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.fabro / "precheck", self.fabro / ".io", self.bin, self.repo):
            d.mkdir(parents=True, exist_ok=True)
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
        self.issue(ISSUE)
        write(self.T / "open.json", json.dumps(OPEN_ISSUES))
        write(self.T / "comments.json", comments())

    def issue(self, payload: dict) -> None:
        write(self.T / "issue.json", json.dumps(payload))

    def run(self, node: str, **env: str):
        base = {
            "PC_GH_LOG": str(self.log),
            "PC_ISSUE_JSON": str(self.T / "issue.json"),
            "PC_OPEN_JSON": str(self.T / "open.json"),
            "PC_COMMENTS_JSON": str(self.T / "comments.json"),
            "PC_POSTED": str(self.posted),
        }
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base,
                  stderr="keep")

    def gh(self) -> str:
        return cat(self.log)

    def verdict_file(self, payload: dict, *, receipt: bool = True) -> None:
        write(self.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
        if receipt:
            payload = {**payload, "_io": {"visit": "v1"}}
        write(self.fabro / "precheck.json", json.dumps(payload))


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


# --- claim -------------------------------------------------------------------------


def test_claim_stages_the_issue_and_routes_an_open_one(ck, tmp_path):
    P = Precheck(ck, tmp_path, "claim-ok")
    P.issue({**ISSUE})
    write(P.T / "comments.json", comments(
        "Looks related to #40.",
        "<!-- fabro:context-pack body=x main=y at=z run=r -->\nA pack.",
    ))
    r = P.run("claim")
    ck("claim: routes to prep", "true", routed(r, "precheck_go"))
    ck("claim: box written without the -bg suffix", "coders-b", cat(P.fabro / "box"))
    ck("claim: the issue number is recorded", "42", cat(P.fabro / "precheck/issue_number"))
    ck("claim: the repository is read from the issue url", "o/r", cat(P.fabro / "precheck/repo"))
    issue_md = cat(P.fabro / "precheck/issue.md")
    ck("claim: issue.md carries the title", "1", count("^# #42: Add a dark mode toggle$", issue_md))
    ck("claim: issue.md carries the labels", "1", count("^Labels: agent$", issue_md))
    ck("claim: issue.md carries a human comment", "1", count("Looks related to #40", issue_md))
    ck("claim: issue.md leaves fabro markers out", "0", count("fabro:context-pack", issue_md))
    ck("claim: open issues leave the issue itself out", "#40 Add a settings page",
       cat(P.fabro / "precheck/open_issues.md"))
    ck("claim: no GitHub write", "0", count("issue comment|-X PATCH", P.gh()))
    ck("claim: stdout is one routing object", "1", count("^{", r.out))


def test_claim_hashes_the_title_and_body_as_c6_says(ck, tmp_path):
    P = Precheck(ck, tmp_path, "claim-hash")
    P.run("claim")
    ck("claim: body= is sha256(title + newline + body)[:12]",
       body_hash(ISSUE["title"], ISSUE["body"]), cat(P.fabro / "precheck/body_hash"))


def test_claim_template_values_are_the_ones_the_graph_names(ck):
    script = extract(GRAPH, "claim")
    ck("claim: interpolates issue_number once", "1", count(r"{{ inputs.issue_number }}", script))
    ck("claim: interpolates coder_pool once", "1", count(r"{{ inputs.coder_pool }}", script))
    ck("claim: quotes issue_number before the guard", "1",
       count(r"N='{{ inputs.issue_number }}'", script))


def test_claim_stops_without_writing_on_a_closed_or_already_checked_issue(ck, tmp_path):
    marker = (f"<!-- fabro:precheck verdict=ready body={body_hash(ISSUE['title'], ISSUE['body'])} "
              "main=abc at=2026-10-30T10:00:00Z run=R -->\nready")
    for name, issue, notes, why in (
        ("closed", {**ISSUE, "state": "CLOSED"}, comments(), "CLOSED"),
        ("checked", ISSUE, comments(marker), "already has a precheck"),
    ):
        P = Precheck(ck, tmp_path, f"claim-{name}")
        P.issue(issue)
        write(P.T / "comments.json", notes)
        r = P.run("claim")
        ck(f"claim {name}: precheck_go is false", "false", routed(r, "precheck_go"))
        ck(f"claim {name}: the reason is said", "1", count(why, r.err or r.out))
        ck(f"claim {name}: no GitHub write", "0", count("issue comment|-X PATCH", P.gh()))


def test_claim_rechecks_an_issue_whose_text_changed(ck, tmp_path):
    P = Precheck(ck, tmp_path, "claim-edited")
    write(P.T / "comments.json", comments(
        "<!-- fabro:precheck verdict=unclear body=000000000000 main=abc at=x run=R -->\nold"))
    r = P.run("claim")
    ck("claim: a precheck of older text does not stop a new one", "true",
       routed(r, "precheck_go"))


def test_claim_refuses_a_non_numeric_issue_number(ck, tmp_path):
    P = Precheck(ck, tmp_path, "claim-nan")
    P.nodes["claim"] = P.nodes["claim"].replace("N='42'", "N='42;rm -rf /'")
    r = P.run("claim")
    ck("claim: a non-numeric issue_number fails the node", "1", str(r.rc))
    ck("claim: and writes no routing object", "", lastjson(r.out))


# --- prep --------------------------------------------------------------------------


def test_prep_records_main_and_excludes_what_it_installs(ck, tmp_path):
    P = Precheck(ck, tmp_path, "prep")
    git("init", "-q", "-b", "main", P.repo)
    write(P.repo / "a.txt", "one\n")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=P.repo)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base", cwd=P.repo)
    head = git("rev-parse", "HEAD", cwd=P.repo)
    r = P.run("prep", FABRO_AGENT_GUIDE="# guide")
    ck("prep: routes to check", "true", routed(r, "prep_ok"))
    ck("prep: main= is HEAD", head, cat(P.fabro / "precheck/main_sha"))
    ck("prep: the guide is installed", "# guide", cat(P.repo / ".codex/instructions.md"))
    ck("prep: and excluded from git", "1",
       count("^.codex/instructions.md$", cat(P.repo / ".git/info/exclude")))


# --- gate --------------------------------------------------------------------------


def staged_for_gate(P: Precheck) -> None:
    write(P.fabro / "precheck/issue_number", "42\n")
    write(P.fabro / "precheck/open_issues.json", json.dumps(OPEN_ISSUES))


def test_gate_refuses_a_check_with_no_receipt(ck, tmp_path):
    P = Precheck(ck, tmp_path, "gate-noreceipt")
    staged_for_gate(P)
    P.verdict_file({"verdict": "ready", "summary": "s", "evidence": []}, receipt=False)
    r = P.run("gate")
    ck("gate: no receipt fails the node", "1", str(r.rc))
    ck("gate: and records no verdict", "absent", present(P.fabro / "precheck/verdict"))


def test_gate_applies_the_evidence_and_blocker_rules(ck, tmp_path):
    ev = [{"where": "src/a.ts:3", "says": "the toggle exists"}]
    for name, payload, want, rule in (
        ("ready", {"verdict": "ready", "summary": "s", "evidence": []}, "ready", ""),
        ("done", {"verdict": "already_done", "summary": "s", "evidence": ev}, "already_done", ""),
        ("done-bare", {"verdict": "already_done", "summary": "s", "evidence": []}, "ready",
         "it cited no evidence"),
        ("blocked", {"verdict": "blocked", "summary": "s", "evidence": ev, "blocker": 40},
         "blocked", ""),
        ("blocked-closed", {"verdict": "blocked", "summary": "s", "evidence": ev, "blocker": 7},
         "ready", "its blocker #7 is not another open issue in this repository"),
        ("blocked-self", {"verdict": "blocked", "summary": "s", "evidence": ev, "blocker": 42},
         "ready", "its blocker #42 is not another open issue in this repository"),
    ):
        P = Precheck(ck, tmp_path, f"gate-{name}")
        staged_for_gate(P)
        P.verdict_file(payload)
        r = P.run("gate")
        ck(f"gate {name}: succeeds", "0", str(r.rc))
        ck(f"gate {name}: verdict", want, cat(P.fabro / "precheck/verdict"))
        ck(f"gate {name}: routes on it", want, routed(r, "verdict"))
        ck(f"gate {name}: the model's own verdict is kept", payload["verdict"],
           cat(P.fabro / "precheck/raw_verdict"))
        ck(f"gate {name}: the rule that applied", rule, cat(P.fabro / "precheck/rule"))


# --- post --------------------------------------------------------------------------


def staged_for_post(P: Precheck, verdict: str, raw: str | None = None, rule: str = "",
                    payload: dict | None = None) -> None:
    for name, value in (("issue_number", "42"), ("repo", "o/r"), ("verdict", verdict),
                        ("raw_verdict", raw or verdict), ("body_hash", "abcdef012345"),
                        ("main_sha", "f" * 40), ("run_id", "01RUN")):
        write(P.fabro / f"precheck/{name}", value + "\n")
    write(P.fabro / "precheck/rule", rule)
    write(P.fabro / "precheck.json", json.dumps(payload or {
        "verdict": raw or verdict, "summary": "The toggle already exists.",
        "evidence": [{"where": "src/settings.ts:12", "says": "ThemeToggle is rendered"}],
    }))


def test_post_writes_the_marker_first_and_posts_a_new_comment(ck, tmp_path):
    P = Precheck(ck, tmp_path, "post-new")
    staged_for_post(P, "already_done")
    r = P.run("post")
    ck("post: succeeds", "0", str(r.rc))
    ck("post: routes", "true", routed(r, "posted"))
    body = cat(P.posted)
    first = body.split("\n", 1)[0]
    ck("post: the marker is the first line", "1", count(
        r"^<!-- fabro:precheck verdict=already_done body=abcdef012345 main=f{40} "
        r"at=[0-9T:Z-]+ run=01RUN -->$", first, flags="E"))
    ck("post: a new comment, not an edit", "1", count("^issue comment 42 --body-file", P.gh()))
    ck("post: the summary is published", "1", count("The toggle already exists.", body))
    ck("post: the evidence is published", "1", count("src/settings.ts:12", body))
    ck("post: says it is a shadow", "1", count("Shadow mode", body))


def test_post_edits_the_existing_marker_in_place(ck, tmp_path):
    P = Precheck(ck, tmp_path, "post-edit")
    staged_for_post(P, "ready")
    write(P.T / "comments.json", comments(
        "unrelated", "<!-- fabro:precheck verdict=unclear body=0 main=0 at=0 run=0 -->\nold"))
    P.run("post")
    ck("post: edits comment 101", "1", count("^api -X PATCH repos/o/r/issues/comments/101", P.gh()))
    ck("post: posts no second comment", "0", count("^issue comment", P.gh()))
    ck("post: the edit carries the new marker", "1",
       count("fabro:precheck verdict=ready", jq(".body", file=P.posted)))


def test_post_says_when_a_rule_overrode_the_model(ck, tmp_path):
    P = Precheck(ck, tmp_path, "post-rule")
    staged_for_post(P, "ready", raw="blocked",
                    rule="its blocker #7 is not another open issue in this repository")
    P.run("post")
    ck("post: the override is explained", "1", count(
        "The model said `blocked`, but its blocker #7 is not another open issue", cat(P.posted)))


def test_post_caps_the_comment_and_scrubs_tokens(ck, tmp_path):
    P = Precheck(ck, tmp_path, "post-cap")
    secret = "ghs_supersecretvalue123"
    staged_for_post(P, "ready", payload={
        "verdict": "ready", "summary": "token " + secret,
        "evidence": [{"where": "x", "says": "y" * 290} for _ in range(6)],
    })
    P.run("post", GH_TOKEN=secret)
    body = P.posted.read_text()
    ck("post: capped at 1500 bytes", "true", str(len(body.encode()) <= 1500).lower())
    ck("post: the token is redacted", "0", count(secret, body))
    ck("post: the marker survives the cap", "1", count("^<!-- fabro:precheck ", body))
    ck("post: the cut is at a whole line, and says so", "1", count("^- (cut to fit)$", body))
    ck("post: the shadow footer survives the cap", "1", count("^_Shadow mode", body))


# --- routing -----------------------------------------------------------------------


def test_every_failure_ends_the_run_and_only_gate_reaches_post(ck):
    e = edges(GRAPH)
    for src in ("claim", "prep", "gate", "post", "stop"):
        ck(f"edge: {src} has an unconditional edge to exit", "1", count(f"{src}->exit ", e, flags="xF"))
    ck("edge: post is reached only from gate", "1", count("->post", e, flags="F"))
    ck("edge: and only when gate succeeded", "1", count("gate->post outcome=succeeded", e, flags="xF"))
    ck("edge: check always goes to gate", "1", count("check->gate ", e, flags="xF"))
