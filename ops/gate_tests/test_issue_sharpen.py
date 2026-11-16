"""The issue sharpening run's command nodes (issue-sharpening 01): claim, prep, draft_gate, record.

`fabro validate` proves the graph parses. It never runs a node's script, and every rule that
matters here is a runtime one:

* `claim` must write `/tmp/fabro/box` (R17) as the lease's pool comes, and go on only for an
  open `agent` issue whose newest precheck says `unverifiable` for exactly this text and that no
  `fabro:sharpen` marker names as an original or a result (D15). Anything else is a quiet
  `not_needed` stop that writes nothing to GitHub.
* `prep` numbers the original criteria, resolves what `.fabro/ci.sh` calls one level deep, and
  stops when there is nothing the closed list can act on.
* `draft_gate` is the preservation gate (D10, C4): it refuses a draft that changes, byte for
  byte, anything outside its change rows. A refusal is `cannot_sharpen`, never a fault.
* `record` always exits 0 and says `failed` when nobody wrote an outcome.

The #67 corpus (D23) drives the gate: for each fixture the lint rates L1-L5, a hand-written
valid draft must pass and its assembled body must lint clean of that rule.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tomllib

from harness import (ORIG_PATH, REPO_ROOT, WF, cat, count, edges, extract, git, jq, lastjson,
                     present, rebase, sh, stub, write)

GRAPH = WF / "issue-sharpen" / "workflow.fabro"
SKILL = REPO_ROOT / "skills" / "plan-factory-issues"
CORPUS = SKILL / "fixtures" / "issues"
DRAFTS = REPO_ROOT / "ops" / "gate_tests" / "fixtures" / "issue_sharpen"

GH_STUB = """#!/bin/sh
log="${SH_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
d="${SH_STATE:?}"
case "$1 $2" in
  "issue view")
    if [ -f "$d/edited" ] && [ -f "${SH_ISSUE_AFTER_JSON:-/nonexistent}" ]; then cat "$SH_ISSUE_AFTER_JSON"; else cat "${SH_ISSUE_JSON:-/dev/null}"; fi ;;
  "issue list")
    case "$*" in *fabro-orientation*) cat "${SH_ORIENT_JSON:-/dev/null}" ;; *) cat "${SH_OPEN_JSON:-/dev/null}" ;; esac ;;
  "issue comment")
    if [ "$SH_FAIL" = comment ]; then exit 1; fi
    n=$(ls "$d" | grep -c '^posted-'); cat "$5" > "$d/posted-$n.md"; echo comment ;;
  "issue edit")
    if [ "$SH_FAIL" = edit ]; then exit 1; fi
    cat "$5" > "$d/edit-body.md"; : > "$d/edited" ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *"-X PATCH"*) cat > "$d/patched.md" ;;
    *"comments?per_page="*) cat "${SH_COMMENTS_JSON:-/dev/null}" ;;
  esac
fi
exit 0
"""

BODY = """<!-- fabro:split-child parent=40 -->

Summary of the work.

## Context

- `src/reports/render.py` has uncovered branches.

## Acceptance criteria

- [ ] Every branch is covered, or the PR explains why.
- [ ] The PR description lists before/after coverage.
- [x] `npm run format:check` passes
  on the whole tree.

## Non-goals

- Changing behaviour.

## Proposed task map

1. The tests.

## Post-deploy checks

- [ ] The numbers look right in the dashboard.
"""

ISSUE = {
    "number": 42,
    "title": "test(reports): cover the branches",
    "body": BODY,
    "state": "OPEN",
    "url": "https://github.com/o/r/issues/42",
    "labels": [{"name": "agent"}],
}

SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b"),
)


def body_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()[:12]


def comments(*bodies: str) -> str:
    return json.dumps([
        {"id": 100 + i, "user": {"login": "someone"}, "created_at": "2026-11-16T10:00:00Z", "body": b}
        for i, b in enumerate(bodies)
    ])


def precheck(verdict: str, issue: dict = ISSUE) -> str:
    return (f"<!-- fabro:precheck verdict={verdict} body={body_hash(issue['title'], issue['body'])} "
            "main=abc at=2026-11-16T10:00:00Z run=R -->\nA criterion is unverifiable.\n\n- `issue body`: x")


class Sharpen:
    """A scratch checkout with a stub `gh` and a per-test `/tmp/fabro`."""

    NODES = ("claim", "prep", "draft_gate", "audit_gate", "apply", "recheck_prep", "recheck_gate",
             "recheck_post", "post", "record", "fail")

    def __init__(self, ck, tmp_path, name: str):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.S = self.fabro / "sharpen"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.S, self.fabro / ".io", self.bin, self.repo):
            d.mkdir(parents=True, exist_ok=True)
        stub(self.bin, "gh", GH_STUB)
        self.log = self.T / "gh.log"
        write(self.log, "")
        self.state = self.T / "gh-state"
        self.state.mkdir(exist_ok=True)
        write(self.T / "open.json", json.dumps([{"number": 42, "title": "x"}, {"number": 40, "title": "Another issue"}]))
        self.nodes = {}
        for node in self.NODES:
            script = extract(GRAPH, node)
            for token, value in SUBSTITUTIONS:
                script = script.replace(token, value)
            self.nodes[node] = rebase(script, self.fabro)
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", self.nodes[node]))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)
        self.issue(ISSUE)
        write(self.T / "comments.json", comments(precheck("unverifiable")))

    def issue(self, payload: dict) -> None:
        write(self.T / "issue.json", json.dumps(payload))

    def run(self, node: str, **env: str):
        base = {
            "SH_STATE": str(self.state),
            "SH_OPEN_JSON": str(self.T / "open.json"),
            "SH_GH_LOG": str(self.log),
            "SH_ISSUE_JSON": str(self.T / "issue.json"),
            "SH_COMMENTS_JSON": str(self.T / "comments.json"),
        }
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base, stderr="keep")

    def gh(self) -> str:
        return cat(self.log)

    def stage(self, issue: dict = ISSUE, criteria: str = "AC1. x\n", ci: str = "## .fabro/ci.sh\n") -> None:
        """What `claim` and `prep` leave behind, for a test that starts at `draft_gate`."""
        write(self.S / "issue.json", json.dumps(issue))
        write(self.S / "criteria.md", criteria)
        write(self.S / "ci.md", ci)
        write(self.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))

    def draft(self, payload: dict, *, receipt: bool = True) -> None:
        if receipt:
            payload = {**payload, "_io": {"visit": "v1"}}
        write(self.S / "draft.json", json.dumps(payload))


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


# --- the graph ---------------------------------------------------------------------------


def test_the_graph_routes_every_ending_to_record(ck):
    found = edges(GRAPH)
    for want in (
        "start->claim ",
        "claim->prep outcome=succeeded && context.sharpen_go=true",
        "claim->record outcome=succeeded && context.sharpen_go=false",
        "claim->record ",
        "prep->draft outcome=succeeded && context.prep_ok=true",
        "prep->record ",
        "draft->draft_gate outcome=succeeded",
        "draft->record ",
        "draft_gate->comprehend outcome=succeeded && context.draft_ok=true",
        "draft_gate->record ",
        "comprehend->verify outcome=succeeded",
        "verify->preserve outcome=succeeded",
        "preserve->audit_gate outcome=succeeded",
        "audit_gate->apply outcome=succeeded && context.sharpen_result=converged",
        "audit_gate->draft outcome=succeeded && context.sharpen_result=open",
        "audit_gate->post outcome=succeeded && context.sharpen_result=capped",
        "audit_gate->post outcome=succeeded && context.sharpen_result=churn",
        "audit_gate->record ",
        "apply->recheck_prep outcome=succeeded && context.apply_result=applied",
        "apply->post outcome=succeeded && context.apply_result=stale",
        "apply->record ",
        "recheck_prep->recheck outcome=succeeded && context.recheck_ready=true",
        "recheck_prep->post ",
        "recheck->recheck_gate outcome=succeeded",
        "recheck->post ",
        "recheck_gate->recheck_post outcome=succeeded",
        "recheck_gate->post ",
        "recheck_post->post ",
        "post->record ",
        "record->fail context.sharpen_outcome=failed",
        "record->exit ",
        "fail->exit ",
        "comprehend->record ",
        "verify->record ",
        "preserve->record ",
    ):
        ck(f"edge {want!r}", "1", count(f"^{want}$", found))


def test_the_rules_the_draft_reads_are_the_user_guides(ck):
    """`prep` writes `rules.md` from the graph; `prompts/rules.md` is its source (C3)."""
    rules = (WF / "issue-sharpen/prompts/rules.md").read_text()
    guide = (REPO_ROOT / "docs/USER-GUIDE.md").read_text()
    start = guide.index("### Writing criteria the factory can verify")
    end = guide.index("Make that edit\nyourself.", start) + len("Make that edit\nyourself.")
    ck("rules.md opens with the USER-GUIDE section verbatim", "True", str(rules.startswith(guide[start:end])))
    script = extract(GRAPH, "prep")
    body = script.split("<<'RULES'\n")[1].split("\nRULES\n")[0]
    embedded = "\n".join(line[2:] for line in body.split("\n")) + "\n"
    ck("prep embeds prompts/rules.md", "True", str(embedded == rules))


# --- claim -------------------------------------------------------------------------------


def test_claim_goes_on_for_an_unverifiable_issue_and_stages_it(ck, tmp_path):
    P = Sharpen(ck, tmp_path, "claim-ok")
    r = P.run("claim")
    ck("claim: routes to prep", "true", routed(r, "sharpen_go"))
    ck("claim: box is the lease's pool as it comes", "coders-b", cat(P.fabro / "box"))
    ck("claim: the issue number is recorded", "42", cat(P.S / "issue_number"))
    ck("claim: the repository is read from the url", "o/r", cat(P.S / "repo"))
    ck("claim: body_hash is the precheck's", body_hash(ISSUE["title"], ISSUE["body"]), cat(P.S / "body_hash"))
    ck("claim: no outcome yet", "absent", present(P.S / "outcome"))
    ck("claim: no GitHub write", "0", count("comment|edit|-X", P.gh()))
    ck("claim: stdout is one routing object", "1", count("^{", r.out))


def test_claim_template_values_are_the_ones_the_graph_names(ck):
    script = extract(GRAPH, "claim")
    ck("claim: interpolates issue_number once", "1", count(r"{{ inputs.issue_number }}", script))
    ck("claim: interpolates coder_pool once", "1", count(r"{{ inputs.coder_pool }}", script))
    ck("claim: quotes issue_number before the guard", "1", count(r"N='{{ inputs.issue_number }}'", script))


def test_claim_stops_quietly_with_a_reason(ck, tmp_path):
    h = body_hash(ISSUE["title"], ISSUE["body"])
    cases = (
        ("closed", {**ISSUE, "state": "CLOSED"}, comments(precheck("unverifiable")), "is CLOSED"),
        ("no-agent", {**ISSUE, "labels": []}, comments(precheck("unverifiable")), "agent label"),
        ("in-progress", {**ISSUE, "labels": [{"name": "agent"}, {"name": "agent-in-progress"}]},
         comments(precheck("unverifiable")), "agent-in-progress"),
        ("ready", ISSUE, comments(precheck("ready")), "not unverifiable"),
        ("no-precheck", ISSUE, comments(), "not unverifiable"),
        ("older-hash", ISSUE, comments(
            "<!-- fabro:precheck verdict=unverifiable body=000000000000 main=a at=x run=R -->\nold"),
         "not unverifiable"),
        ("newest-wins", ISSUE, comments(precheck("unverifiable"), precheck("ready")), "not unverifiable"),
        ("sharpened-original", ISSUE, comments(
            precheck("unverifiable"),
            f"<!-- fabro:sharpen state=applied round=1 body={h} applied=aaaaaaaaaaaa recheck=ready run=R -->\nx"),
         "already sharpened"),
        ("sharpened-result", ISSUE, comments(
            precheck("unverifiable"),
            f"<!-- fabro:sharpen state=applied round=1 body=bbbbbbbbbbbb applied={h} recheck=- run=R -->\nx"),
         "already sharpened"),
    )
    for name, issue, notes, why in cases:
        P = Sharpen(ck, tmp_path, f"claim-{name}")
        P.issue(issue)
        write(P.T / "comments.json", notes)
        r = P.run("claim")
        ck(f"claim {name}: sharpen_go is false", "false", routed(r, "sharpen_go"))
        ck(f"claim {name}: the reason is said", "1", count(why, cat(P.S / "rule")))
        ck(f"claim {name}: the outcome is not_needed", "not_needed", cat(P.S / "outcome"))
        ck(f"claim {name}: no GitHub write", "0", count("comment|edit|-X", P.gh()))
    P = Sharpen(ck, tmp_path, "claim-other-sharpen-comment")
    write(P.T / "comments.json", comments(
        precheck("unverifiable"),
        "<!-- fabro:sharpen state=capped round=3 body=cccccccccccc applied=- recheck=- run=R -->\nx",
        f"<!-- fabro:sharpen-original body={h} run=R -->\n\noriginal"))
    ck("claim: a marker for another text, or an original-body comment, does not stop it", "true",
       routed(P.run("claim"), "sharpen_go"))


def test_claim_goes_on_for_a_good_fixture_only_with_an_unverifiable_precheck(ck, tmp_path):
    for fixture in sorted(CORPUS.glob("good-*.md")):
        text = fixture.read_text().split("\n")
        title = text[0].removeprefix("# ")
        rest = "\n".join(text[1:]).lstrip("\n")
        issue = {**ISSUE, "title": title, "body": rest}
        for verdict, want in (("ready", "false"), ("unverifiable", "true")):
            P = Sharpen(ck, tmp_path, f"claim-{fixture.stem}-{verdict}")
            P.issue(issue)
            write(P.T / "comments.json", comments(precheck(verdict, issue)))
            ck(f"claim {fixture.stem} with a {verdict} precheck", want, routed(P.run("claim"), "sharpen_go"))


# --- prep --------------------------------------------------------------------------------


def repo_with(P: Sharpen, ci: str | None, files: dict[str, str] | None = None) -> None:
    git("init", "-q", "-b", "main", P.repo)
    write(P.repo / "a.txt", "one\n")
    if ci is not None:
        write(P.repo / ".fabro/ci.sh", ci)
    for name, text in (files or {}).items():
        write(P.repo / name, text)
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=P.repo)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base", cwd=P.repo)


def claimed(ck, tmp_path, name: str, ci: str | None = "set -e\ntrue\n", files=None, issue=ISSUE) -> Sharpen:
    P = Sharpen(ck, tmp_path, name)
    P.issue(issue)
    write(P.T / "comments.json", comments(precheck("unverifiable", issue)))
    repo_with(P, ci, files)
    P.run("claim")
    return P


def test_prep_numbers_the_criteria_and_stages_the_inputs(ck, tmp_path):
    P = claimed(ck, tmp_path, "prep-ok")
    r = P.run("prep", FABRO_AGENT_GUIDE="# guide")
    ck("prep: routes to draft", "true", routed(r, "prep_ok"))
    ck("prep: criteria are numbered, a checkbox and a continuation line kept in order",
       "AC1. Every branch is covered, or the PR explains why.\n"
       "AC2. The PR description lists before/after coverage.\n"
       "AC3. `npm run format:check` passes\n  on the whole tree.",
       cat(P.S / "criteria.md"))
    ck("prep: main is HEAD", git("rev-parse", "HEAD", cwd=P.repo), cat(P.S / "main_sha"))
    ck("prep: issue.md carries the title", "1", count("^# #42: test", cat(P.S / "issue.md")))
    ck("prep: precheck.md is the newest marker comment", "1",
       count("^<!-- fabro:precheck verdict=unverifiable", cat(P.S / "precheck.md")))
    ck("prep: ci.md opens with ci.sh", "1", count("^## .fabro/ci.sh$", cat(P.S / "ci.md")))
    ck("prep: the guide is installed and excluded", "1",
       count("^.codex/instructions.md$", cat(P.repo / ".git/info/exclude")))
    ck("prep: no orientation or pack", "absent", present(P.S / "orientation.md"))
    ck("prep: pack absent", "absent", present(P.S / "pack.md"))


def test_prep_stages_the_optional_inputs_when_they_exist(ck, tmp_path):
    P = claimed(ck, tmp_path, "prep-optional")
    write(P.T / "comments.json", comments(
        precheck("unverifiable"),
        "<!-- fabro:context-pack body=x main=y at=z run=r -->\nThe pack text."))
    P.run("claim")
    write(P.T / "orient.json", json.dumps([{"number": 5, "body": "<!-- fabro:orientation x -->\nThe map."}]))
    P.run("prep", SH_ORIENT_JSON=str(P.T / "orient.json"))
    ck("prep: pack.md is the comment without its marker line", "The pack text.", cat(P.S / "pack.md"))
    ck("prep: orientation.md is the pinned issue without its marker line", "The map.",
       cat(P.S / "orientation.md"))


def test_prep_stops_when_nothing_can_be_sharpened(ck, tmp_path):
    cases = (
        ("no-heading", ISSUE["body"].replace("## Acceptance criteria", "## Done when"),
         "set -e\ntrue\n", "no acceptance criteria heading"),
        ("no-bullets", "## Acceptance criteria\n\nIt works.\n\n## Non-goals\n\n- x\n", "set -e\ntrue\n",
         "no acceptance criteria bullets"),
        ("no-ci", ISSUE["body"], None, "no .fabro/ci.sh"),
    )
    for name, body, ci, why in cases:
        P = claimed(ck, tmp_path, f"prep-{name}", ci=ci, issue={**ISSUE, "body": body})
        r = P.run("prep")
        ck(f"prep {name}: prep_ok is false", "false", routed(r, "prep_ok"))
        ck(f"prep {name}: the rule says why", why, cat(P.S / "rule"))
        ck(f"prep {name}: the outcome is not_needed", "not_needed", cat(P.S / "outcome"))
        ck(f"prep {name}: nothing was staged for the draft", "absent", present(P.S / "ci.md"))


def test_prep_reads_one_level_of_what_ci_calls(ck, tmp_path):
    ci = (SKILL / "fixtures/repo/.fabro/ci.sh").read_text()
    files = {
        "scripts/check-migrations.sh": "echo migrations-script\n",
        "Makefile": "typecheck: mypy-core\n\nmypy-core:\n\tuv run mypy src\n\n.PHONY: typecheck\n",
        "frontend/package.json": json.dumps({"scripts": {"lint": "eslint src", "test": "vitest run"}}),
    }
    P = claimed(ck, tmp_path, "prep-ci", ci=ci, files=files)
    P.run("prep")
    md = cat(P.S / "ci.md")
    ck("ci.md: the script file ci.sh runs", "1", count("^echo migrations-script$", md))
    ck("ci.md: the npm script it runs", "1", count('^lint: "eslint src"$', md))
    ck("ci.md: the Makefile target it runs", "1", count("^typecheck: mypy-core$", md))
    ck("ci.md: ci.sh itself is first", "1", count("uv run pytest -q tests/", md))
    ck("ci.md: a script is listed once however often it is named", "1",
       count("^## scripts/check-migrations.sh$", md))


def test_prep_cuts_ci_md_at_forty_thousand_characters(ck, tmp_path):
    ci = "set -e\nbash scripts/big.sh\n"
    P = claimed(ck, tmp_path, "prep-cut", ci=ci,
                files={"scripts/big.sh": "".join(f"echo line {i} {'x' * 60}\n" for i in range(900))})
    P.run("prep")
    size = len((P.S / "ci.md").read_bytes())
    ck("ci.md: at most 40,000 characters and the cut line", "True", str(size <= 40100))
    ck("ci.md: ends with the cut marker", "- (cut to fit)", cat(P.S / "ci.md").split("\n")[-1])


# --- draft_gate --------------------------------------------------------------------------


def row(ref, original, proposed, transformation, evidence=None):
    return {
        "criterion_ref": ref,
        "original": original,
        "proposed": proposed,
        "transformation": transformation,
        "evidence": evidence or [{"where": "issue body", "says": "the criterion as written"}],
    }


CI_EV = [{"where": ".fabro/ci.sh:7", "says": "runs pytest"}]
PL_EV = [{"where": "src/a.py:3", "says": "the line"}]
AC1 = "Every branch is covered, or the PR explains why."
AC2 = "The PR description lists before/after coverage."
AC3 = "`npm run format:check` passes\n  on the whole tree."

GOOD_BODY = BODY.replace(
    "- [ ] Every branch is covered, or the PR explains why.\n",
    "- [ ] Every branch is covered, or a code comment says why.\n",
).replace("- [ ] The PR description lists before/after coverage.\n", "").replace(
    "\n## Post-deploy checks\n\n- [ ] The numbers look right in the dashboard.\n", "")
GOOD_ROWS = [
    row("AC1", AC1, "Every branch is covered, or a code comment says why.", "pr_text_to_code"),
    row("AC2", AC2, None, "to_post_deploy"),
]


def good(**over) -> dict:
    d = {"status": "proposed", "body": GOOD_BODY, "post_deploy_checks": [], "changes": GOOD_ROWS,
         "human_actions": []}
    d.update(over)
    return d


def gate(ck, tmp_path, name, draft, *, issue=ISSUE, receipt=True):
    P = Sharpen(ck, tmp_path, name)
    P.stage(issue, criteria="AC1. a\n")
    P.draft(draft, receipt=receipt)
    return P, P.run("draft_gate")


def refused(ck, tmp_path, name, draft, why, **kw):
    P, r = gate(ck, tmp_path, name, draft, **kw)
    ck(f"{name}: draft_ok is false", "false", routed(r, "draft_ok"))
    ck(f"{name}: the outcome is cannot_sharpen", "cannot_sharpen", cat(P.S / "outcome"))
    ck(f"{name}: the rule says why", "1", count(why, cat(P.S / "rule")))
    ck(f"{name}: no audit directory", "absent", present(P.S / "audit"))
    ck(f"{name}: no GitHub call", "", P.gh())
    return P


def test_draft_gate_accepts_a_good_draft_and_writes_the_sealed_audit_directory(ck, tmp_path):
    P, r = gate(ck, tmp_path, "gate-ok", good())
    ck("gate: draft_ok", "true", routed(r, "draft_ok"))
    ck("gate: the round is 1 on the first visit", "1", routed(r, "round"))
    ck("gate: the audit directory holds exactly four files",
       "changes.md\nci.md\noriginal_criteria.md\nproposed.md",
       "\n".join(sorted(p.name for p in (P.S / "audit").iterdir())))
    proposed = cat(P.S / "audit/proposed.md")
    ck("gate: proposed.md keeps the marker on line 1", "<!-- fabro:split-child parent=40 -->",
       proposed.split("\n")[0])
    ck("gate: a changed bullet is replaced", "1", count("^- \\[ \\] Every branch is covered, or a code comment says why.$", proposed))
    ck("gate: the moved bullet leaves the criteria", "0", count("before/after coverage.$", cat(P.S / "audit/proposed.md").split("## Post-deploy checks")[0]))
    ck("gate: the original Post-deploy item is kept first, the moved one follows",
       "- [ ] The numbers look right in the dashboard.\n- [ ] The PR description lists before/after coverage.",
       proposed.split("## Post-deploy checks\n\n")[1].strip("\n"))
    ck("gate: changes.md has a row per change", "2", count("^| AC", cat(P.S / "audit/changes.md")))
    ck("gate: original_criteria.md is criteria.md", cat(P.S / "criteria.md"),
       cat(P.S / "audit/original_criteria.md"))
    ck("gate: no GitHub call", "", P.gh())
    P.draft(good())
    r2 = P.run("draft_gate")
    ck("gate: the round counts up on the next visit", "2", routed(r2, "round"))


def test_draft_gate_assembles_post_deploy_items_in_order_and_without_duplicates(ck, tmp_path):
    d = good(post_deploy_checks=["Check the dashboard after deploy.", "The numbers look right in the dashboard."])
    P, r = gate(ck, tmp_path, "gate-assemble", d)
    ck("gate: accepted", "true", routed(r, "draft_ok"))
    tail = cat(P.S / "audit/proposed.md").split("## Post-deploy checks\n\n")[1]
    ck("gate: original item, then the new one, then the moved criterion; the repeat is dropped",
       "- [ ] The numbers look right in the dashboard.\n- [ ] Check the dashboard after deploy.\n"
       "- [ ] The PR description lists before/after coverage.", tail.strip("\n"))
    nopd = {**ISSUE, "body": BODY.split("\n## Post-deploy checks")[0] + "\n"}
    P, r = gate(ck, tmp_path, "gate-newpd", good(), issue=nopd)
    tail = cat(P.S / "audit/proposed.md").split("## Post-deploy checks\n\n")[1]
    ck("gate: a body with no section gets one", "- [ ] The PR description lists before/after coverage.", tail.strip("\n"))
    only_kept = good(changes=[GOOD_ROWS[0]], body=BODY.replace(AC1, "Every branch is covered, or a code comment says why.").split("\n## Post-deploy checks")[0] + "\n")
    P, r = gate(ck, tmp_path, "gate-nopd-at-all", only_kept, issue=nopd)
    ck("gate: no moved item and no section means none is written", "0",
       count("Post-deploy checks", cat(P.S / "audit/proposed.md")))


def test_draft_gate_accepts_each_transformation(ck, tmp_path):
    cases = {
        "pr_text_to_code": row("AC1", AC1, "A code comment says why.", "pr_text_to_code"),
        "command_to_ci": row("AC1", AC1, "`uv run pytest -q tests/` passes.", "command_to_ci", CI_EV),
        "command_to_behaviour": row("AC1", AC1, "`src/a.py` has a test.", "command_to_behaviour", PL_EV),
        "no_drop_to_threshold": row("AC1", AC1, "`fail_under` in `pyproject.toml` is raised.", "no_drop_to_threshold", PL_EV),
        "to_post_deploy": row("AC1", AC1, None, "to_post_deploy"),
    }
    for name, r1 in cases.items():
        body = BODY.split("\n## Post-deploy checks")[0] + "\n"
        if r1["proposed"] is None:
            body = body.replace(f"- [ ] {AC1}\n", "")
        else:
            body = body.replace(AC1, r1["proposed"])
        P, r = gate(ck, tmp_path, f"gate-{name}", good(body=body, changes=[r1]))
        ck(f"gate: {name} is accepted", "true", routed(r, "draft_ok"))


def test_draft_gate_refuses_what_the_schema_or_the_receipt_does_not_allow(ck, tmp_path):
    refused(ck, tmp_path, "gate-no-receipt", good(), "no valid Input receipt", receipt=False)
    refused(ck, tmp_path, "gate-unknown-transformation",
            good(changes=[row("AC1", AC1, "x", "rewrite_freely"), GOOD_ROWS[1]]), "outside the closed list")
    refused(ck, tmp_path, "gate-null-proposed",
            good(changes=[row("AC1", AC1, None, "pr_text_to_code"), GOOD_ROWS[1]]), "null if and only if")
    refused(ck, tmp_path, "gate-proposed-on-post-deploy",
            good(changes=[GOOD_ROWS[0], row("AC2", AC2, "kept", "to_post_deploy")]), "null if and only if")
    refused(ck, tmp_path, "gate-no-evidence",
            good(changes=[{**GOOD_ROWS[0], "evidence": []}, GOOD_ROWS[1]]), "no evidence")
    refused(ck, tmp_path, "gate-ci-needs-ci-evidence",
            good(changes=[row("AC1", AC1, "`pytest` passes.", "command_to_ci"), GOOD_ROWS[1]]), "needs evidence from .fabro/ci.sh")
    refused(ck, tmp_path, "gate-behaviour-needs-a-line",
            good(changes=[row("AC1", AC1, "`pytest` passes.", "command_to_behaviour"), GOOD_ROWS[1]]), "needs path:line")
    refused(ck, tmp_path, "gate-too-many-rows",
            good(changes=[GOOD_ROWS[0]] * 21), "no rows, or more than 20")
    refused(ck, tmp_path, "gate-no-rows", good(changes=[]), "no rows, or more than 20")
    refused(ck, tmp_path, "gate-extra-key", {**good(), "notes": "x"}, "unknown key")
    refused(ck, tmp_path, "gate-multiline-post-deploy",
            good(post_deploy_checks=["two\nlines"]), "one-line strings")
    refused(ck, tmp_path, "gate-bad-ref",
            good(changes=[row("criterion 1", AC1, "x", "pr_text_to_code"), GOOD_ROWS[1]]), "not ACn")
    refused(ck, tmp_path, "gate-no-body", {k: v for k, v in good().items() if k != "body"}, "no body")


def test_draft_gate_carries_the_models_cannot_sharpen_to_the_record(ck, tmp_path):
    P = refused(ck, tmp_path, "gate-model-declines",
                {"status": "cannot_sharpen", "reason": "AC2 contradicts a non-goal"}, "^model:AC2 contradicts")
    ck("gate: the rule begins model:", "model:AC2 contradicts a non-goal", cat(P.S / "rule"))
    refused(ck, tmp_path, "gate-declines-without-a-reason", {"status": "cannot_sharpen"}, "no reason")


def test_draft_gate_refuses_a_change_outside_its_rows(ck, tmp_path):
    ok = GOOD_BODY
    cases = {
        "a-changed-bullet-with-no-row": (
            ok.replace("`npm run format:check` passes", "`npm run format` passes"), "bullet 2"),
        "a-byte-in-an-untouched-bullet": (
            ok.replace("on the whole tree.", "on the whole tree!"), "bullet 2"),
        "a-reordered-bullet": (
            ok.replace(
                "- [ ] Every branch is covered, or a code comment says why.\n- [x] `npm run format:check` passes\n  on the whole tree.\n",
                "- [x] `npm run format:check` passes\n  on the whole tree.\n- [ ] Every branch is covered, or a code comment says why.\n"),
            "bullet 1"),
        "an-extra-bullet": (
            ok.replace("\n## Non-goals", "- [ ] Something new.\n\n## Non-goals"), "no change row accounts"),
        "a-criterion-deleted-with-no-row": (
            ok.replace("- [x] `npm run format:check` passes\n  on the whole tree.\n", ""), "no change row accounts"),
        "a-moved-marker": (
            ok.replace("<!-- fabro:split-child parent=40 -->\n\nSummary of the work.", "Summary of the work.\n\n<!-- fabro:split-child parent=40 -->"),
            "marker on line 1"),
        "an-edited-marker": (
            ok.replace("parent=40", "parent=41"), "marker on line 1"),
        "an-edited-non-goal": (
            ok.replace("- Changing behaviour.", "- Changing behaviour a little."), "## Non-goals changed"),
        "an-edited-task-map": (
            ok.replace("1. The tests.", "1. The tests and more."), "## Proposed task map changed"),
        "an-edited-context": (
            ok.replace("uncovered branches", "uncovered lines"), "## Context changed"),
        "an-edited-summary": (
            ok.replace("Summary of the work.", "Summary of the job."), "before the first heading changed"),
        "a-removed-section": (
            ok.replace("## Non-goals\n\n- Changing behaviour.\n\n", ""), "section heading"),
        "a-renamed-section": (
            ok.replace("## Non-goals", "## Out of scope"), "section heading"),
        "a-post-deploy-heading-in-the-body": (
            ok + "\n## Post-deploy checks\n\n- [ ] sneaky\n", "gate assembles it"),
        "prose-added-to-the-criteria": (
            ok.replace("## Acceptance criteria\n\n", "## Acceptance criteria\n\nAlso, be careful.\n\n"), "text between"),
    }
    for name, (body, why) in cases.items():
        refused(ck, tmp_path, f"gate-{name}", good(body=body), why)
    refused(ck, tmp_path, "gate-a-row-whose-original-is-not-the-bullet",
            good(changes=[row("AC1", "Every branch is covered.", "x", "pr_text_to_code"), GOOD_ROWS[1]]),
            "AC1 is not the bullet")
    refused(ck, tmp_path, "gate-two-rows-for-one-criterion",
            good(changes=[GOOD_ROWS[0], GOOD_ROWS[0]]), "same criterion")
    refused(ck, tmp_path, "gate-a-row-for-a-missing-criterion",
            good(changes=[GOOD_ROWS[0], row("AC9", AC2, None, "to_post_deploy")]), "not in the original")
    refused(ck, tmp_path, "gate-a-row-that-proposes-no-change",
            good(changes=[row("AC1", AC1, AC1, "pr_text_to_code"), GOOD_ROWS[1]]), "proposes the text it already has")
    refused(ck, tmp_path, "gate-a-proposal-not-in-the-body",
            good(changes=[row("AC1", AC1, "Not what the body says.", "pr_text_to_code"), GOOD_ROWS[1]]), "bullet 1")


def test_draft_gate_tolerates_only_trailing_whitespace_and_the_final_newline(ck, tmp_path):
    padded = GOOD_BODY.replace("- Changing behaviour.", "- Changing behaviour.   ").rstrip("\n") + "\n\n\n"
    P, r = gate(ck, tmp_path, "gate-trailing-whitespace", good(body=padded))
    ck("gate: trailing whitespace and extra final newlines are not changes", "true", routed(r, "draft_ok"))
    crlf = {**ISSUE, "body": BODY.replace("\n", "\r\n")}
    P, r = gate(ck, tmp_path, "gate-crlf-original", good(), issue=crlf)
    ck("gate: an original written with CRLF line ends compares equal", "true", routed(r, "draft_ok"))


def test_draft_gate_reads_the_criteria_forms_real_issues_use(ck, tmp_path):
    """`## Acceptance Criteria` (wfs#2342), numbered items (lawncare#3065) and a suffixed heading."""
    for name, old, new in (
        ("capital", "## Acceptance criteria", "## Acceptance Criteria"),
        ("suffix", "## Acceptance criteria", "## Acceptance criteria (in addition to the list above)"),
    ):
        issue = {**ISSUE, "body": BODY.replace(old, new)}
        P, r = gate(ck, tmp_path, f"gate-heading-{name}", good(body=GOOD_BODY.replace(old, new)), issue=issue)
        ck(f"gate: the {name} heading is the criteria section", "true", routed(r, "draft_ok"))
    numbered = BODY.replace("- [ ] Every branch", "1. Every branch").replace(
        "- [ ] The PR description", "2. The PR description").replace("- [x] `npm run", "3. `npm run")
    body = numbered.replace("1. Every branch is covered, or the PR explains why.\n",
                            "1. Every branch is covered, or a code comment says why.\n").replace(
        "2. The PR description lists before/after coverage.\n", "").replace(
        "\n## Post-deploy checks\n\n- [ ] The numbers look right in the dashboard.\n", "")
    P, r = gate(ck, tmp_path, "gate-numbered", good(body=body), issue={**ISSUE, "body": numbered})
    ck("gate: numbered criteria are criteria", "true", routed(r, "draft_ok"))
    ck("gate: the numbered item kept its number", "1", count("^1[.] Every branch is covered, or a code comment", cat(P.S / "audit/proposed.md")))


def test_draft_gate_guards_a_second_criteria_section_byte_for_byte(ck, tmp_path):
    """Only the first `Acceptance criteria` section is the one the change rows act on."""
    extra = "## Acceptance criteria (in addition)\n\n- The report renders.\n\n## Non-goals"
    issue = {**ISSUE, "body": BODY.replace("## Non-goals", extra, 1)}
    kept = GOOD_BODY.replace("## Non-goals", extra, 1)
    P, r = gate(ck, tmp_path, "gate-second-section-kept", good(body=kept), issue=issue)
    ck("gate: an untouched second section passes", "true", routed(r, "draft_ok"))
    refused(ck, tmp_path, "gate-second-section-edited",
            good(body=kept.replace("- The report renders.", "- The report renders quickly.")),
            "(in addition) changed", issue=issue)


# --- the #67 corpus (D23)----------------------------------------------------------------


def lint_rules(title: str, body: str) -> set[str]:
    """The skill's lint over `title` + `body`, against the corpus repository."""
    sys.path.insert(0, str(SKILL / "scripts"))
    from pathlib import Path

    from factory_issues import ci, draft, lint

    found = lint.lint_draft(draft.parse(Path("d.md"), f"# {title}\n\n{body}"), ci.follow(SKILL / "fixtures" / "repo"))
    return {f.rule for f in found if f.severity == "error"}


CORPUS_EXPECTED = tomllib.loads((CORPUS / "EXPECTED.toml").read_text())
SHARPENABLE = sorted(n for n, v in CORPUS_EXPECTED.items()
                     if set(v["errors"]) & {"L1", "L2", "L3", "L4", "L5"})


def fixture_issue(name: str, marker: bool = False) -> tuple[dict, dict]:
    text = (CORPUS / name).read_text().split("\n")
    title = text[0].removeprefix("# ")
    body = "\n".join(text[1:]).lstrip("\n")
    stem = name.removesuffix(".md")
    draft = json.loads((DRAFTS / f"{stem}.draft.json").read_text())
    if marker:
        mk = "<!-- fabro:split-child parent=1 -->\n\n"
        body, draft["body"] = mk + body, mk + draft["body"]
    return {**ISSUE, "title": title, "body": body}, draft


def test_every_sharpenable_fixture_has_a_valid_draft_that_passes_and_lints_clean(ck, tmp_path):
    ck("the corpus has fixtures for L1 to L5", "6", str(len(SHARPENABLE)))
    for name in SHARPENABLE:
        stem = name.removesuffix(".md")
        issue, draft = fixture_issue(name)
        before = lint_rules(issue["title"], issue["body"])
        if name.startswith("L4"):
            # Its one criterion is a `.github/workflows/` change, so the only honest draft moves it
            # to Post-deploy checks and leaves no criterion behind. The gate refuses that: the
            # issue runs as written rather than with nothing for the Refuter to check.
            ck(f"corpus {stem}: the original raises L4", "True", str("L4" in before))
            refused(ck, tmp_path, f"corpus-{stem}", draft, "Refuter would have nothing to check", issue=issue)
            continue
        P, r = gate(ck, tmp_path, f"corpus-{stem}", draft, issue=issue)
        ck(f"corpus {stem}: the draft passes draft_gate", "true", routed(r, "draft_ok"))
        proposed = cat(P.S / "audit/proposed.md") + "\n"
        after = lint_rules(issue["title"], proposed)
        wanted = set(CORPUS_EXPECTED[name]["errors"]) & {"L1", "L2", "L3", "L4", "L5"}
        ck(f"corpus {stem}: the original raises its rule", "True", str(wanted <= before))
        ck(f"corpus {stem}: the proposal lints clean of L1 to L5", "", ",".join(sorted(after & {"L1", "L2", "L3", "L4", "L5"})))


def test_a_bad_draft_for_each_fixture_is_refused(ck, tmp_path):
    for name in SHARPENABLE:
        if name.startswith("L4"):
            continue  # its good draft is itself refused (the test above)
        stem = name.removesuffix(".md")
        issue, draft = fixture_issue(name, marker=True)
        P, r = gate(ck, tmp_path, f"corpus-{stem}-marked", draft, issue=issue)
        ck(f"corpus {stem}: the marked good draft passes", "true", routed(r, "draft_ok"))
        first = draft["changes"][0]
        bullet = f"- [ ] {first['proposed']}\n" if first["proposed"] else ""
        deleted = {**draft, "body": draft["body"].replace(bullet, "", 1), "changes": draft["changes"][1:] or draft["changes"]}
        if bullet:
            refused(ck, tmp_path, f"corpus-{stem}-deleted", deleted, "no change row accounts", issue=issue)
        else:
            refused(ck, tmp_path, f"corpus-{stem}-dropped-row",
                    {**draft, "changes": [dict(draft["changes"][0], proposed=draft["changes"][0]["original"], transformation="pr_text_to_code")] + draft["changes"][1:]},
                    "proposes the text it already has", issue=issue)
        edited = {**draft, "body": draft["body"].replace("## Non-goals\n\n- ", "## Non-goals\n\n- Also, ", 1)}
        refused(ck, tmp_path, f"corpus-{stem}-edited", edited, "## Non-goals changed", issue=issue)
        moved = {**draft, "body": draft["body"].replace("<!-- fabro:split-child parent=1 -->\n\n", "", 1).replace("\n\n## Context", "\n\n<!-- fabro:split-child parent=1 -->\n\n## Context", 1)}
        refused(ck, tmp_path, f"corpus-{stem}-moved", moved, "marker on line 1", issue=issue)


# --- the audit (task 02) -----------------------------------------------------------------

LENSES = ("comprehend", "verify", "preserve")


def finding(severity: str, message: str, ref: str | None = "AC1") -> dict:
    return {"severity": severity, "criterion_ref": ref, "message": message,
            "evidence": [{"where": "proposed.md", "says": "the line"}]}


def lens_result(name: str, findings=(), **over) -> dict:
    """A lens's result for GOOD_BODY's proposal: two bullets, two change rows."""
    base = {
        "comprehend": {"proposed_paraphrase": "Cover the branches.", "existing_system_paraphrase": "The renderer has two."},
        "verify": {"criteria": [
            {"text": "Every branch is covered, or a code comment says why.", "verdict": "checkable_from_diff", "why": "the tests"},
            {"text": "`npm run format:check` passes on the whole tree.", "verdict": "checkable_by_ci", "why": "ci.md line 3"}]},
        "preserve": {"pairs": [
            {"criterion_ref": "AC1", "verdict": "same", "why": "same ask"},
            {"criterion_ref": "AC2", "verdict": "same", "why": "moved, same words"}]},
    }[name]
    return {**base, "findings": list(findings), **over, "_io": {"stage": name, "visit": "v-" + name}}


def play(P: Sharpen, draft: dict | None = None, results: dict | None = None, *, missing=(), receipt=()):
    """One round: the draft's gate, the lenses' files, then the audit gate."""
    P.draft(draft or good())
    r = P.run("draft_gate")
    assert routed(r, "draft_ok") == "true", cat(P.S / "rule")
    for lens in LENSES:
        if lens in missing:
            continue
        payload = (results or {}).get(lens) or lens_result(lens)
        if lens in receipt:
            payload = {k: v for k, v in payload.items() if k != "_io"}
        write(P.S / "lens" / f"{lens}.json", json.dumps(payload))
    return P.run("audit_gate")


def audit_pad(ck, tmp_path, name: str) -> Sharpen:
    P = Sharpen(ck, tmp_path, name)
    P.stage(ISSUE, criteria="AC1. a\n")
    return P


def variant(text: str) -> dict:
    return good(body=GOOD_BODY.replace("Every branch is covered, or a code comment says why.", text),
                changes=[row("AC1", AC1, text, "pr_text_to_code"), GOOD_ROWS[1]])


def test_audit_gate_converges_on_a_clean_round(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-clean")
    r = play(P)
    ck("audit: converged", "converged", routed(r, "sharpen_result"))
    ck("audit: the round", "1", routed(r, "round"))
    ck("audit: no outcome is written (apply decides)", "absent", present(P.S / "outcome"))
    ck("audit: the history is empty", "0", jq("length", file=P.S / "history.json"))
    ck("audit: the paraphrase is kept for post", "1", count("^\\*\\*What the issue now asks for", cat(P.S / "paraphrase.md")))
    ck("audit: round 1's changes are kept for the churn check", "true",
       jq("has(\"AC1\") and has(\"AC2\")", file=P.S / "rounds/1.json"))
    ck("audit: draft_gate made the lens directory", "present", present(P.S / "lens"))
    ck("audit: and the lenses' files are in it", "comprehend.json,preserve.json,verify.json",
       ",".join(sorted(p.name for p in (P.S / "lens").iterdir())))


def test_a_lens_that_did_not_run_leaves_the_round_open(ck, tmp_path):
    for lens in LENSES:
        P = audit_pad(ck, tmp_path, f"audit-missing-{lens}")
        r = play(P, missing=(lens,))
        ck(f"audit {lens} missing: open", "open", routed(r, "sharpen_result"))
        P = audit_pad(ck, tmp_path, f"audit-noreceipt-{lens}")
        r = play(P, receipt=(lens,))
        ck(f"audit {lens} without a receipt: open", "open", routed(r, "sharpen_result"))
        P = audit_pad(ck, tmp_path, f"audit-wrongstage-{lens}")
        bad = lens_result(lens)
        bad["_io"]["stage"] = "someone_else"
        ck(f"audit {lens} with another stage's receipt: open", "open",
           routed(play(P, results={lens: bad}), "sharpen_result"))
        P = audit_pad(ck, tmp_path, f"audit-shape-{lens}")
        ck(f"audit {lens} with a result of the wrong shape: open", "open",
           routed(play(P, results={lens: {**lens_result(lens), "findings": "none"}}), "sharpen_result"))
    P = audit_pad(ck, tmp_path, "audit-missing-note")
    play(P, missing=("verify",))
    ck("audit: the findings file says a lens did not run", "1", count("did not run", cat(P.S / "findings.md")))


def test_only_a_blocker_or_a_major_keeps_the_round_open(ck, tmp_path):
    for sev, want in (("blocker", "open"), ("major", "open"), ("nit", "converged")):
        P = audit_pad(ck, tmp_path, f"audit-sev-{sev}")
        r = play(P, results={"comprehend": lens_result("comprehend", [finding(sev, "a problem")])})
        ck(f"audit: a {sev} alone gives {want}", want, routed(r, "sharpen_result"))
    P = audit_pad(ck, tmp_path, "audit-nit-kept")
    play(P, results={"verify": lens_result("verify", [finding("nit", "wording")])})
    ck("audit: a nit is kept for post, with its lens", "verify", jq(".[0].lens", file=P.S / "open_findings.json"))
    ck("audit: a nit is not in the finding history", "0", jq("length", file=P.S / "history.json"))


def test_a_verdict_alone_can_block_the_round(ck, tmp_path):
    def unverifiable(d):
        d["criteria"][0]["verdict"] = "unverifiable"
        return d
    cases = {
        "an-unverifiable-criterion": {"verify": unverifiable(lens_result("verify"))},
        "a-bullet-with-no-verdict": {"verify": lens_result("verify", criteria=lens_result("verify")["criteria"][:1])},
        "a-substitution": {"preserve": lens_result("preserve", pairs=[
            {"criterion_ref": "AC1", "verdict": "substitution", "why": "narrower"},
            {"criterion_ref": "AC2", "verdict": "same", "why": "w"}])},
        "a-contradiction-resolved": {"preserve": lens_result("preserve", pairs=[
            {"criterion_ref": "AC1", "verdict": "same", "why": "w"},
            {"criterion_ref": "AC2", "verdict": "contradiction_resolved", "why": "picked a side"}])},
        "a-change-row-with-no-pair": {"preserve": lens_result("preserve", pairs=[
            {"criterion_ref": "AC1", "verdict": "same", "why": "w"}])},
    }
    for name, results in cases.items():
        P = audit_pad(ck, tmp_path, f"audit-{name}")
        r = play(P, results=results)
        ck(f"audit {name}: open", "open", routed(r, "sharpen_result"))
        ck(f"audit {name}: it became a blocker in the history", "blocker", jq(".[0].severity", file=P.S / "history.json"))
        ck(f"audit {name}: the next draft is told", "1", count("^- \\[blocker\\]", cat(P.S / "findings.md")))


def test_the_third_round_that_is_not_clean_is_capped(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-capped")
    results = [{"comprehend": lens_result("comprehend", [finding("major", f"problem number {n}")])} for n in (1, 2, 3)]
    got = [routed(play(P, variant(f"A criterion, round {n}."), results[n - 1]), "sharpen_result") for n in (1, 2, 3)]
    ck("audit: open, open, capped", "open,open,capped", ",".join(got))
    ck("audit: capped writes the outcome", "capped", cat(P.S / "outcome"))
    ck("audit: and a one-line rule", "1", count("round 3 was not clean: 1 open finding$", cat(P.S / "rule")))


def test_a_finding_that_comes_back_is_churn(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-recurrence")
    same = {"comprehend": lens_result("comprehend", [finding("blocker", "The criterion names a file that does not exist.")])}
    other = {"comprehend": lens_result("comprehend", [finding("blocker", "A different problem.")])}
    a = routed(play(P, variant("A one."), same), "sharpen_result")
    b = routed(play(P, variant("A two."), other), "sharpen_result")
    ck("audit: the first finding is fixed in round 2", "fixed", jq('map(select(.message | startswith("The criterion"))) | .[0].state', file=P.S / "history.json"))
    c = routed(play(P, variant("A three."), same), "sharpen_result")
    ck("audit: open, open, churn", "open,open,churn", ",".join((a, b, c)))
    ck("audit: the recurrence re-opened the entry", "open", jq('map(select(.message | startswith("The criterion"))) | .[0].state', file=P.S / "history.json"))
    ck("audit: churn writes its outcome", "churn", cat(P.S / "outcome"))
    ck("audit: and says a fixed finding came back", "1", count("a fixed finding came back", cat(P.S / "rule")))


def test_a_criterion_whose_text_alternates_is_churn(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-alternates")
    got = []
    for n, text in ((1, "Text A."), (2, "Text B."), (3, "Text A.")):
        results = {"comprehend": lens_result("comprehend", [finding("major", f"problem number {n}")])}
        got.append(routed(play(P, variant(text), results), "sharpen_result"))
    ck("audit: open, open, churn rather than capped", "open,open,churn", ",".join(got))
    ck("audit: the rule says the text alternated", "1", count("a criterion's text alternated", cat(P.S / "rule")))


def test_the_same_words_under_another_criterion_are_a_new_finding(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-new-ref")
    play(P, variant("A one."), {"comprehend": lens_result("comprehend", [finding("major", "Too vague.", "AC1")])})
    r = play(P, variant("A two."), {"comprehend": lens_result("comprehend", [finding("major", "Too vague.", "AC2")])})
    ck("audit: open, not churn", "open", routed(r, "sharpen_result"))
    ck("audit: two history entries", "2", jq("length", file=P.S / "history.json"))
    ck("audit: the first is fixed, the second open", "fixed,open", jq("map(.state) | join(\",\")", file=P.S / "history.json"))


def test_findings_md_lists_the_open_findings_before_the_history(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-findings-md")
    play(P, variant("A one."), {"comprehend": lens_result("comprehend", [finding("major", "Old problem.")])})
    play(P, variant("A two."), {"verify": lens_result("verify", [finding("blocker", "New problem.", None)])})
    md = cat(P.S / "findings.md").split("\n")
    at = {h: i for i, h in enumerate(md) if h.startswith("## ")}
    ck("findings.md: open findings come first", "True", str(at["## Open findings"] < at["## History"]))
    ck("findings.md: the open one is listed under Open findings", "1",
       count("^- \\[blocker\\] verify: New problem.$", "\n".join(md[at["## Open findings"]:at["## History"]])))
    ck("findings.md: the fixed one is in the history", "1",
       count("^- fixed (opened in round 1, fixed in round 2) comprehend AC1: Old problem.$", "\n".join(md[at["## History"]:])))
    ck("findings.md: its evidence is listed", "1", count("^    - `proposed.md`: the line$", "\n".join(md)))


def test_draft_gate_clears_the_lens_directory_each_round(ck, tmp_path):
    P = audit_pad(ck, tmp_path, "audit-lens-dir")
    play(P, results={"comprehend": lens_result("comprehend", [finding("major", "x")])})
    ck("audit: round 1 left a lens file", "present", present(P.S / "lens/comprehend.json"))
    P.draft(good())
    P.run("draft_gate")
    ck("audit: the next draft_gate removed it", "absent", present(P.S / "lens/comprehend.json"))
    r = P.run("audit_gate")
    ck("audit: a round with no lens files cannot be clean", "open", routed(r, "sharpen_result"))


# --- apply, the recheck, post and record (task 03) -----------------------------------------

AFTER_BODY_NOTE = "after the edit GitHub stores the body as written"


def applied_pad(ck, tmp_path, name: str, *, after: bool = True) -> Sharpen:
    """The state a converged round leaves: a real `draft_gate` run, plus what `claim` kept."""
    P, r = gate(ck, tmp_path, name, good())
    assert routed(r, "draft_ok") == "true"
    write(P.S / "body_hash", body_hash(ISSUE["title"], ISSUE["body"]) + "\n")
    write(P.S / "issue_number", "42\n")
    write(P.S / "repo", "o/r\n")
    write(P.S / "run_id", "RUNID")
    write(P.S / "round", "2\n")
    proposed = (P.S / "audit/proposed.md").read_text()
    if after:
        write(P.T / "issue.after.json", json.dumps({**ISSUE, "body": proposed}))
    P.env_extra = {"SH_ISSUE_AFTER_JSON": str(P.T / "issue.after.json")}
    return P


def run_with(P: Sharpen, node: str, **env):
    return P.run(node, **{**getattr(P, "env_extra", {}), **env})


def posted(P: Sharpen, n: int = 0) -> str:
    return (P.state / f"posted-{n}.md").read_text() if (P.state / f"posted-{n}.md").exists() else ""


def test_apply_posts_the_original_then_edits_with_the_proposal_byte_for_byte(ck, tmp_path):
    P = applied_pad(ck, tmp_path, "apply-ok")
    r = run_with(P, "apply")
    h = body_hash(ISSUE["title"], ISSUE["body"])
    proposed = (P.S / "audit/proposed.md").read_text()
    ck("apply: routes to the recheck", "applied", routed(r, "apply_result"))
    ck("apply: the outcome is applied", "applied", cat(P.S / "outcome"))
    ck("apply: the original comes first", "<!-- fabro:sharpen-original body=" + h + " run=RUNID -->",
       posted(P).split("\n")[0])
    ck("apply: a blank line follows the marker", "", posted(P).split("\n")[1])
    ck("apply: the original body follows verbatim", "True", str(posted(P).split("\n", 2)[2] == ISSUE["body"]))
    ck("apply: the edit is audit/proposed.md byte for byte", "True",
       str((P.state / "edit-body.md").read_bytes() == (P.S / "audit/proposed.md").read_bytes()))
    ck("apply: the original comment was posted before the edit", "comment,edit",
       ",".join(w for w in (line.split()[1] for line in P.gh().split("\n") if line.startswith("issue ")) if w in ("comment", "edit")))
    ck("apply: the applied hash is the precheck's hash of the stored body",
       body_hash(ISSUE["title"], proposed), cat(P.S / "applied_hash"))


def test_apply_stops_stale_when_the_issue_changed_and_edits_nothing(ck, tmp_path):
    P = applied_pad(ck, tmp_path, "apply-stale")
    write(P.T / "issue.json", json.dumps({**ISSUE, "body": ISSUE["body"] + "\nA human added a line.\n"}))
    r = run_with(P, "apply")
    ck("apply stale: routes to post", "stale", routed(r, "apply_result"))
    ck("apply stale: the outcome", "stale", cat(P.S / "outcome"))
    ck("apply stale: the rule says why", "1", count("edited while it was being sharpened", cat(P.S / "rule")))
    ck("apply stale: no comment and no edit", "0", count("issue comment|issue edit", P.gh(), flags="E"))
    ck("apply stale: the node succeeds", "0", str(r.rc))


def test_apply_fails_the_node_when_the_original_comment_cannot_be_posted(ck, tmp_path):
    P = applied_pad(ck, tmp_path, "apply-no-original")
    r = run_with(P, "apply", SH_FAIL="comment")
    ck("apply: a failed original comment fails the node", "1", str(r.rc))
    ck("apply: no edit was attempted", "0", count("issue edit", P.gh()))
    ck("apply: no outcome was written", "absent", present(P.S / "outcome"))


def test_apply_fails_the_node_when_the_edit_fails_after_the_original_was_posted(ck, tmp_path):
    P = applied_pad(ck, tmp_path, "apply-no-edit")
    r = run_with(P, "apply", SH_FAIL="edit")
    ck("apply: a failed edit fails the node", "1", str(r.rc))
    ck("apply: the original comment is on the issue", "1", count("^<!-- fabro:sharpen-original", posted(P)))
    ck("apply: no outcome was written", "absent", present(P.S / "outcome"))
    ck("apply: no applied hash", "absent", present(P.S / "applied_hash"))


def recheck_pad(ck, tmp_path, name: str) -> Sharpen:
    P = applied_pad(ck, tmp_path, name)
    run_with(P, "apply")
    git("init", "-q", "-b", "main", P.repo)
    write(P.repo / "a.txt", "one\n")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=P.repo)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base", cwd=P.repo)
    write(P.T / "issue.json", json.dumps({**ISSUE, "body": (P.S / "audit/proposed.md").read_text()}))
    write(P.T / "comments.json", comments(
        "A human note.", f"<!-- fabro:sharpen-original body=x run=R -->\n\nold body",
        precheck("unverifiable")))
    return P


def test_recheck_prep_stages_the_applied_body_in_the_precheck_format(ck, tmp_path):
    P = recheck_pad(ck, tmp_path, "recheck-prep")
    r = run_with(P, "recheck_prep")
    D = P.S / "recheck"
    ck("recheck_prep: ready", "true", routed(r, "recheck_ready"))
    md = cat(D / "issue.md")
    ck("recheck_prep: issue.md opens with the title", "# #42: " + ISSUE["title"], md.split("\n")[0])
    ck("recheck_prep: and the labels", "Labels: agent", md.split("\n")[2])
    ck("recheck_prep: and the applied body", "1", count("^- \\[ \\] Every branch is covered, or a code comment says why.$", md))
    ck("recheck_prep: a human comment is kept", "1", count("A human note.", md))
    ck("recheck_prep: fabro markers are left out", "0", count("fabro:sharpen-original|fabro:precheck", md, flags="E"))
    ck("recheck_prep: open_issues.md leaves the issue itself out", "#40 Another issue", cat(D / "open_issues.md"))
    ck("recheck_prep: the body hash is the applied hash", cat(P.S / "applied_hash"), cat(D / "body_hash"))
    ck("recheck_prep: main_sha is HEAD", git("rev-parse", "HEAD", cwd=P.repo), cat(D / "main_sha"))
    ck("recheck_prep: the run id is copied for the marker", "RUNID", cat(D / "run_id"))
    ck("recheck_prep: the issue number and repository", "42,o/r", cat(D / "issue_number") + "," + cat(D / "repo"))


def recheck_verdict(P: Sharpen, verdict: str = "ready", *, receipt: bool = True) -> None:
    payload = {"verdict": verdict, "summary": "Nothing against it.", "evidence": []}
    if receipt:
        payload["_io"] = {"visit": "v1"}
    write(P.fabro / "sharpen/recheck.json", json.dumps(payload))


def test_recheck_gate_and_post_are_the_prechecks_own_nodes_on_the_recheck_paths(ck, tmp_path):
    P = recheck_pad(ck, tmp_path, "recheck-gate")
    run_with(P, "recheck_prep")
    recheck_verdict(P, receipt=False)
    r = run_with(P, "recheck_gate")
    ck("recheck_gate: no receipt fails the node", "1", str(r.rc))
    ck("recheck_gate: and records no verdict", "absent", present(P.S / "recheck/verdict"))
    recheck_verdict(P, "unverifiable")
    r = run_with(P, "recheck_gate")
    ck("recheck_gate: a verdict with no evidence counts as ready", "ready", cat(P.S / "recheck/verdict"))
    ck("recheck_gate: and keeps what the model said", "unverifiable", cat(P.S / "recheck/raw_verdict"))
    recheck_verdict(P, "ready")
    run_with(P, "recheck_gate")
    write(P.T / "comments.json", comments("<!-- fabro:precheck verdict=unverifiable body=old main=a at=x run=R -->\nold"))
    r = run_with(P, "recheck_post")
    ck("recheck_post: edits the existing precheck comment in place", "1", count("-X PATCH", P.gh()))
    marker = json.loads(cat(P.state / "patched.md"))["body"].split("\n")[0]
    ck("recheck_post: the marker is the precheck's, for the applied body", "True", str(marker.startswith(
        f"<!-- fabro:precheck verdict=ready body={cat(P.S / 'applied_hash')} main={git('rev-parse', 'HEAD', cwd=P.repo)} at=")))
    ck("recheck_post: and carries this run", "1", count(" run=RUNID -->$", marker))
    ck("recheck_post: nothing else was written (apply's original comment is the only one)", "1",
       count("issue comment", P.gh()))


def post_pad(ck, tmp_path, name: str, state: str, **kw) -> Sharpen:
    P = applied_pad(ck, tmp_path, name)
    write(P.S / "outcome", state + "\n")
    if state == "applied":
        write(P.S / "applied_hash", "bbbbbbbbbbbb\n")
    write(P.S / "paraphrase.md", "**What the issue now asks for.** Cover the branches.\n")
    write(P.S / "open_findings.json", json.dumps([
        {"lens": "preserve", "severity": "blocker", "criterion_ref": "AC2", "message": "AC2 is a substitution.", "evidence": []},
        {"lens": "verify", "severity": "nit", "criterion_ref": None, "message": "Wording.", "evidence": []}]))
    return P


def test_post_writes_one_marker_per_state_with_the_change_table(ck, tmp_path):
    h = body_hash(ISSUE["title"], ISSUE["body"])
    for state, applied, recheck, sentence in (
        ("applied", "bbbbbbbbbbbb", "ready", "rewrote this issue"),
        ("capped", "-", "-", "after 2 rounds"),
        ("churn", "-", "-", "kept changing"),
        ("stale", "-", "-", "edited while the factory was sharpening"),
    ):
        P = post_pad(ck, tmp_path, f"post-{state}", state)
        if state == "applied":
            write(P.S / "recheck/verdict", "ready\n")
        r = run_with(P, "post")
        text = posted(P)
        ck(f"post {state}: the marker is the first line",
           f"<!-- fabro:sharpen state={state} round=2 body={h} applied={applied} recheck={recheck} run=RUNID -->",
           text.split("\n")[0])
        ck(f"post {state}: one sentence says what happened", "1", count(sentence, text))
        ck(f"post {state}: the change table", "1", count("^| AC1 | ", text))
        ck(f"post {state}: the paraphrase", "1", count("^\\*\\*What the issue now asks for", text))
        ck(f"post {state}: the proposed body in a details block", "1", count("^<details><summary>The proposed body", text))
        ck(f"post {state}: open findings only when not applied",
           "0" if state == "applied" else "1", count("^- \\[blocker\\] AC2: AC2 is a substitution.$", text))
        ck(f"post {state}: no precheck marker", "0", count("fabro:precheck", text))
        ck(f"post {state}: the node succeeds and says it posted", "true", routed(r, "posted"))


def test_post_edits_its_comment_in_place_and_ignores_the_original_comment(ck, tmp_path):
    P = post_pad(ck, tmp_path, "post-inplace", "capped")
    write(P.T / "comments.json", json.dumps([
        {"id": 7, "body": "<!-- fabro:sharpen state=capped round=3 body=x applied=- recheck=- run=R -->\nold"},
        {"id": 8, "body": "<!-- fabro:sharpen-original body=x run=R -->\n\nbody"}]))
    run_with(P, "post")
    ck("post: patches the sharpen comment", "1", count("repos/o/r/issues/comments/7 ", P.gh()))
    ck("post: and posts no new one", "0", count("issue comment", P.gh()))
    ck("post: the patched body starts with the marker", "1",
       count("^<!-- fabro:sharpen state=capped", json.loads(cat(P.state / "patched.md"))["body"]))


def test_post_keeps_human_actions_in_the_comment_and_out_of_the_body(ck, tmp_path):
    P = post_pad(ck, tmp_path, "post-human", "applied")
    d = json.loads((P.S / "draft.json").read_text())
    d["human_actions"] = ["Rotate the staging key."]
    write(P.S / "draft.json", json.dumps(d))
    run_with(P, "post")
    text = posted(P)
    ck("post: the heading says it is not part of the issue", "1", count("^### Human actions (not part of the issue)$", text))
    ck("post: the action is listed", "1", count("^- Rotate the staging key.$", text))
    ck("post: the issue body never carries it", "0", count("Rotate the staging key", (P.S / "audit/proposed.md").read_text()))


def test_post_cuts_the_details_then_the_paraphrase_and_never_the_marker_or_table(ck, tmp_path):
    P = post_pad(ck, tmp_path, "post-cut", "applied")
    big = (P.S / "audit/proposed.md").read_text() + "\n" + ("filler line of body text\n" * 3000)
    write(P.S / "audit/proposed.md", big)
    run_with(P, "post")
    text = posted(P)
    ck("post cut: under 60,000 characters", "True", str(len(text.encode()) <= 60000))
    ck("post cut: the details block went first", "0", count("<details>", text))
    ck("post cut: the paraphrase survived", "1", count("What the issue now asks for", text))
    ck("post cut: the marker survived", "1", count("^<!-- fabro:sharpen state=applied", text))
    ck("post cut: the table survived", "1", count("^| AC1 | ", text))
    P = post_pad(ck, tmp_path, "post-cut-more", "applied")
    write(P.S / "audit/proposed.md", big)
    write(P.S / "paraphrase.md", "**What the issue now asks for.** " + "long " * 14000 + "\n")
    run_with(P, "post")
    text = posted(P)
    ck("post cut more: the paraphrase went second", "0", count("What the issue now asks for", text))
    ck("post cut more: the marker remains", "1", count("^<!-- fabro:sharpen state=applied", text))
    ck("post cut more: and the table", "1", count("^| AC1 | ", text))


def test_post_redacts_tokens_and_survives_a_failed_write(ck, tmp_path):
    P = post_pad(ck, tmp_path, "post-redact", "capped")
    write(P.S / "paraphrase.md", "The token is sk-secret-token-1234567890 here.\n")
    run_with(P, "post", FABRO_API_TOKEN="sk-secret-token-1234567890")
    ck("post: the token is redacted", "1", count("The token is \\[redacted\\] here.", posted(P)))
    ck("post: and appears nowhere", "0", count("sk-secret-token", posted(P)))
    P = post_pad(ck, tmp_path, "post-write-fails", "applied")
    r = run_with(P, "post", SH_FAIL="comment")
    ck("post: a failed comment write does not fail the node", "0", str(r.rc))
    ck("post: and says it did not post", "false", routed(r, "posted"))
    ck("post: leaves the outcome alone", "applied", cat(P.S / "outcome"))


def test_record_says_failed_when_the_outcome_is_not_one_it_knows(ck, tmp_path):
    for outcome, want in (("applied", "applied"), ("capped", "capped"), ("churn", "churn"), ("stale", "stale"),
                          ("cannot_sharpen", "cannot_sharpen"), ("not_needed", "not_needed"),
                          ("nonsense", "failed"), ("", "failed")):
        P = Sharpen(ck, tmp_path, f"record-{outcome or 'empty'}")
        write(P.S / "outcome", outcome + "\n")
        if outcome == "applied":
            write(P.S / "applied_hash", "bbbbbbbbbbbb\n")
            write(P.S / "recheck/verdict", "unverifiable\n")
        r = P.run("record")
        ck(f"record {outcome or 'empty'}: exits 0", "0", str(r.rc))
        ck(f"record {outcome or 'empty'}: the outcome", want, routed(r, "sharpen_outcome"))
        if outcome == "applied":
            ck("record applied: carries the applied hash and the recheck verdict", "bbbbbbbbbbbb,unverifiable",
               routed(r, "applied_hash") + "," + routed(r, "recheck"))
    P = Sharpen(ck, tmp_path, "record-capped-no-applied")
    write(P.S / "outcome", "capped\n")
    write(P.S / "applied_hash", "cccccccccccc\n")
    ck("record: an applied hash is reported for an applied run only", "", routed(P.run("record"), "applied_hash"))


def test_the_fail_node_exits_one(ck, tmp_path):
    P = Sharpen(ck, tmp_path, "fail-node")
    r = P.run("fail")
    ck("fail: exits 1 so the run ends failed", "1", str(r.rc))


# --- record ------------------------------------------------------------------------------


def test_record_always_exits_zero_and_says_failed_when_nobody_wrote_an_outcome(ck, tmp_path):
    P = Sharpen(ck, tmp_path, "record-failed")
    shutil.rmtree(P.S)
    r = P.run("record")
    ck("record: exits 0 with no files at all", "0", str(r.rc))
    ck("record: the outcome is failed", "failed", routed(r, "sharpen_outcome"))
    ck("record: the round is 0", "0", routed(r, "round"))
    ck("record: stdout is one routing object", "1", count("^{", r.out))


def test_record_reports_what_the_run_left_behind(ck, tmp_path):
    P = Sharpen(ck, tmp_path, "record-refused")
    write(P.S / "outcome", "cannot_sharpen\n")
    write(P.S / "rule", "AC2 contradicts a non-goal")
    write(P.S / "round", "2\n")
    write(P.S / "body_hash", "abc123abc123\n")
    P.draft(good(changes=[GOOD_ROWS[0], GOOD_ROWS[1], row("AC3", AC3, "`uv run pytest -q tests/` passes.", "command_to_ci", CI_EV)]))
    r = P.run("record")
    ck("record: exits 0", "0", str(r.rc))
    ck("record: the outcome", "cannot_sharpen", routed(r, "sharpen_outcome"))
    ck("record: the rule", "AC2 contradicts a non-goal", routed(r, "sharpen_rule"))
    ck("record: the round is a number", "2", routed(r, "round"))
    ck("record: the body hash", "abc123abc123", routed(r, "body_hash"))
    ck("record: the row count", "3", routed(r, "changes"))
    ck("record: the transformations", "command_to_ci=1,pr_text_to_code=1,to_post_deploy=1",
       routed(r, "transformations"))
    ck("record: no applied hash yet", "", routed(r, "applied_hash"))
