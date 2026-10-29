"""The PR doctor's command nodes (more-local-inference 06): claim, prep, fix_gate, validate and
publish.

`fabro validate` proves the graph parses. It never runs a node's script, and every rule that
matters here is a runtime one:

* `claim` must write `/tmp/fabro/box` (R16) and route a merged, closed, already-marked or
  `do-not-merge` PR to `stop` **without touching GitHub** -- a marker posted on an ineligible PR
  would end the doctor's interest in it forever (D13).
* `prep` must check out the PR's **head**, and rebase only when the base moved without a
  conflict; a conflict ends in `git rebase --abort` and the un-rebased head (decision 4).
* `fix_gate` must refuse a fix turn with no Input receipt, and turn the contract's `reviewable`
  into the marker's `review=`.
* `validate` is the repository's own gate. `pushable` is `no` from its first line and `yes`
  only on green, so a red, a second red or a killed `ci.sh` pushes nothing.
* `fix_gate` keeps an accepted contract where `validate` cannot delete it, and `publish`
  reads that copy: run in their real order, the marker carries the fixer's summary and
  blockers.
* `publish` is the only GitHub writer, and its order is the contract: the marker first, then
  the labels, then the squash-and-push, then the marker edited to `pushed=<new head>`. A failed
  push leaves `pushed=pending` (C6, D9).

The last test is decision 5's gate: every commit this package can produce must list
`noreply@fabro.sh` among its authors, checked against a real git history with the review
gate's own filter (`review-merge.fabro:927`).
"""

from __future__ import annotations

import json

from harness import (ORIG_PATH, WF, cat, count, edges, extract, git, jq, lastjson, present,
                     rebase, rm, sh, stub, write)

GRAPH = WF / "pr-doctor" / "workflow.fabro"

# A `gh` that answers every call the doctor makes and records each one, in order, to
# `$DOCTOR_GH_LOG`. `gh pr checkout` lands the checkout on the PR head and sets up tracking,
# as the real one does.
GH_STUB = """#!/bin/sh
log="${DOCTOR_GH_LOG:?}"
printf '%s\\n' "$*" >> "$log"
case "$1 $2" in
  "pr view")     cat "${DOCTOR_PR_JSON:-/dev/null}" ;;
  "pr checks")   printf '%s' "${DOCTOR_CHECKS_JSON:-[]}" ;;
  "pr comment")
    # After the post, GitHub has the marker: the re-read `publish` does to learn the comment
    # id must find it, or a successful push could not edit the marker to `pushed=<sha>`.
    printf '%s' '[{"id": 99, "body": "<!-- fabro:pr-doctor head=x pushed=pending review=fire run=y -->"}]' > "${DOCTOR_COMMENTS_JSON}"
    echo comment
    ;;
  "pr edit")     echo edited ;;
  "pr checkout") git checkout -q -B feature --track origin/feature ;;
esac
if [ "$1" = api ]; then
  case "$*" in
    *"comments?per_page="*) cat "${DOCTOR_COMMENTS_JSON:-/dev/null}" ;;
    *) echo '{"id": 4242}' ;;
  esac
fi
exit 0
"""

PR_JSON = json.dumps(
    {
        "number": 7,
        "title": "fix: a thing",
        "body": "b",
        "url": "https://github.com/o/r/pull/7",
        "state": "OPEN",
        "isCrossRepository": False,
        "headRefName": "feature",
        "baseRefName": "main",
        "headRefOid": "a" * 40,
        "labels": [],
        "closingIssuesReferences": [],
    }
)

REPORT = (
    "## AI PR review\n\n### Findings\n\n- **[error] STD-001** - `a.ts:3` - x\n\n"
    "### Not auto-merged\n\n**Reason:** There are unfixed findings; a human must merge this "
    "PR.\n"
)
COMMENTS = json.dumps([{"id": 11, "created_at": "2026-10-29T10:00:00Z", "body": REPORT}])
MARKED_COMMENTS = json.dumps(
    [
        {
            "id": 12,
            "created_at": "2026-10-29T10:00:00Z",
            "body": "<!-- fabro:pr-doctor head=abc pushed=none review=skip run=R -->\n" + REPORT,
        }
    ]
)

# The values fabro substitutes into a command node's script before the sandbox sees it.
SUBSTITUTIONS = (
    ("{{ inputs.pr_number }}", "7"),
    ("{{ inputs.coder_pool }}", "coders-a-bg"),
)


def substitute(script: str) -> str:
    for token, value in SUBSTITUTIONS:
        script = script.replace(token, value)
    return script


class Doctor:
    """A scratch checkout with a stub `gh` and a per-test `/tmp/fabro`."""

    def __init__(self, ck, tmp_path, name: str):
        self.T = tmp_path / name
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        self.repo = self.T / "repo"
        for d in (self.fabro / "doctor", self.bin, self.repo):
            d.mkdir(parents=True, exist_ok=True)
        stub(self.bin, "gh", GH_STUB)
        self.log = self.T / "gh.log"
        write(self.log, "")
        self.counter = self.T / "ci-counter"
        self.nodes = {
            node: substitute(rebase(extract(GRAPH, node), self.fabro))
            for node in ("claim", "prep", "fix_gate", "validate", "publish")
        }
        # The syntax of every staged script, under dash when it is installed.
        for node, script in self.nodes.items():
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", script))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            "DOCTOR_GH_LOG": str(self.log),
            "DOCTOR_PR_JSON": str(self.T / "pr.json"),
            "DOCTOR_COMMENTS_JSON": str(self.T / "comments.raw"),
        }
        base.update(extra)
        return base

    def setup(
        self,
        *,
        pr_state: str = "OPEN",
        labels: str = "[]",
        comments: str = COMMENTS,
        report: str = REPORT,
    ) -> None:
        payload = json.loads(PR_JSON)
        payload["state"] = pr_state
        # GitHub returns label objects, not names; `claim` reads `.labels[].name`.
        payload["labels"] = [{"name": name} for name in json.loads(labels)]
        # Two copies: the stub `gh pr view` reads `$DOCTOR_PR_JSON` (it cannot read the file
        # claim redirects into -- the redirect truncates it first), and prep/publish read the
        # copy under the run's `/tmp/fabro`.
        write(self.T / "pr.json", json.dumps(payload))
        write(self.fabro / "pr.json", json.dumps(payload))
        write(self.T / "comments.raw", comments)
        # What `claim` would have written, for the nodes that run after it.
        write(self.fabro / "doctor/report.md", report)

    def run(self, node: str, *, env: dict[str, str] | None = None, cwd=None, path=None):
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(
            script,
            cwd=cwd or self.repo,
            path=path or f"{self.bin}:{ORIG_PATH}",
            env=self.env(**(env or {})),
            stderr="keep",
        )

    def gh(self) -> list[str]:
        return [line for line in cat(self.log).split("\n") if line]

    def routed(self, result, key: str) -> str:
        return jq(f".context_updates.{key}", lastjson(result.out))


# --- claim -------------------------------------------------------------------------


def test_claim_writes_the_box_file_and_routes_an_eligible_pr(ck, tmp_path):
    D = Doctor(ck, tmp_path, "claim-ok")
    D.setup()
    r = D.run("claim")
    ck("claim: box written without the -bg suffix", "coders-a", cat(D.fabro / "box"))
    ck("claim: pr_number recorded", "7", cat(D.fabro / "pr_number"))
    ck("claim: routes to prep", "true", D.routed(r, "doctor_go"))
    ck("claim: the report is found", "1",
       count("There are unfixed findings", file=D.fabro / "doctor/report.md"))
    ck("claim: no GitHub write", "0",
       count("pr comment|pr edit|api -X", "\n".join(D.gh())))
    ck("claim: stdout is one routing object", "1", count("^{", r.out))


def test_claim_template_values_are_the_ones_the_graph_names(ck):
    script = extract(GRAPH, "claim")
    ck("claim: interpolates pr_number once", "1", count(r"{{ inputs.pr_number }}", script))
    ck("claim: interpolates coder_pool once", "1", count(r"{{ inputs.coder_pool }}", script))
    ck("claim: strips the -bg suffix", "1", count(r"${POOL%-bg}", script))
    ck("claim: quotes pr_number before the guard", "1", count(r"PR='{{ inputs.pr_number }}'", script))


def test_claim_stops_without_writing_on_every_ineligible_pr(ck, tmp_path):
    for name, kwargs, why in (
        ("merged", {"pr_state": "MERGED"}, "MERGED"),
        ("closed", {"pr_state": "CLOSED"}, "CLOSED"),
        ("marked", {"comments": MARKED_COMMENTS}, "already has a doctor marker"),
        ("do-not-merge", {"labels": '["do-not-merge"]'}, "carries do-not-merge"),
    ):
        D = Doctor(ck, tmp_path, f"claim-{name}")
        D.setup(**kwargs)
        r = D.run("claim")
        ck(f"claim {name}: doctor_go is false", "false", D.routed(r, "doctor_go"))
        ck(f"claim {name}: the reason is said", "1", count(why, r.err or r.out))
        ck(f"claim {name}: no GitHub write", "0",
           count("pr comment|pr edit|api -X", "\n".join(D.gh())))


def test_claim_refuses_a_non_numeric_pr_number(ck, tmp_path):
    D = Doctor(ck, tmp_path, "claim-nan")
    D.setup()
    D.nodes["claim"] = D.nodes["claim"].replace("PR='7'", "PR='7;rm -rf /'")
    r = D.run("claim")
    ck("claim: a non-numeric pr_number fails the node", "1", str(r.rc))
    ck("claim: and writes no routing object", "", lastjson(r.out))


def test_claim_refuses_a_fork(ck, tmp_path):
    D = Doctor(ck, tmp_path, "claim-fork")
    D.setup()
    payload = json.loads((D.T / "pr.json").read_text())
    payload["isCrossRepository"] = True
    write(D.T / "pr.json", json.dumps(payload))
    r = D.run("claim")
    ck("claim: a fork is refused", "false", D.routed(r, "doctor_go"))
    ck("claim: and says why", "1", count("fork", r.err or r.out))


# --- prep --------------------------------------------------------------------------


def prep_repo(D: Doctor, *, behind: bool, conflicting: bool) -> None:
    """A bare origin, a PR branch `feature`, and optionally a `main` that moved under it."""
    origin = D.T / "origin.git"
    git("init", "-q", "--bare", "-b", "main", origin)
    git("clone", "-q", origin, D.repo)
    r = lambda *a: git("-c", "user.email=t@t", "-c", "user.name=t", *a, cwd=D.repo)
    write(D.repo / "a.txt", "one\n")
    write(D.repo / "b.txt", "one\n")
    r("add", ".")
    r("commit", "-q", "-m", "base")
    r("push", "-q", "origin", "main")
    r("checkout", "-q", "-b", "feature")
    # The feature branch always changes `a.txt`. A conflicting base changes `a.txt` too; a
    # non-conflicting one changes `b.txt`, so the rebase succeeds.
    write(D.repo / "a.txt", "feature\n")
    r("commit", "-q", "-am", "feature")
    r("push", "-q", "origin", "feature")
    if behind:
        r("checkout", "-q", "main")
        write((D.repo / "a.txt") if conflicting else (D.repo / "b.txt"), "main\n")
        r("commit", "-q", "-am", "moved")
        r("push", "-q", "origin", "main")
    r("checkout", "-q", "feature")
    D.setup()
    # claim writes this; the nodes that run after it read it.
    write(D.fabro / "pr_number", "7\n")


def test_prep_checks_out_the_head_and_records_the_two_heads(ck, tmp_path):
    D = Doctor(ck, tmp_path, "prep-clean")
    prep_repo(D, behind=False, conflicting=False)
    r = D.run("prep")
    ck("prep: routes to fix", "true", D.routed(r, "prep_ok"))
    ck("prep: base_ref", "main", cat(D.fabro / "base_ref"))
    ck("prep: head_ref", "feature", cat(D.fabro / "head_ref"))
    head = git("-C", D.repo, "rev-parse", "HEAD")
    ck("prep: head_before is the PR head", head, cat(D.fabro / "doctor/head_before"))
    ck("prep: fix_base starts where the head is", head, cat(D.fabro / "doctor/fix_base"))
    ck("prep: the reason is staged", "1", count("Reason:", file=D.fabro / "doctor/reason.txt"))


def test_prep_rebases_when_the_base_moved(ck, tmp_path):
    D = Doctor(ck, tmp_path, "prep-behind")
    prep_repo(D, behind=True, conflicting=False)
    before = git("-C", D.repo, "rev-parse", "HEAD")
    D.run("prep")
    after = git("-C", D.repo, "rev-parse", "HEAD")
    main = git("-C", D.repo, "rev-parse", "origin/main")
    ck("prep: rebased onto the base tip", main, git("-C", D.repo, "rev-parse", "HEAD~1"))
    ck("prep: the head moved", "different", "same" if before == after else "different")
    ck("prep: head_before is the pre-rebase head", before,
       cat(D.fabro / "doctor/head_before"))
    ck("prep: fix_base is the rebased head", after, cat(D.fabro / "doctor/fix_base"))


def test_prep_aborts_a_conflicting_rebase_and_works_on_the_old_head(ck, tmp_path):
    D = Doctor(ck, tmp_path, "prep-conflict")
    prep_repo(D, behind=True, conflicting=True)
    before = git("-C", D.repo, "rev-parse", "HEAD")
    r = D.run("prep")
    ck("prep: still routes", "true", D.routed(r, "prep_ok"))
    ck("prep: the head is untouched", before, git("-C", D.repo, "rev-parse", "HEAD"))
    ck("prep: no rebase left behind", "absent",
       "present" if (D.repo / ".git/rebase-merge").is_dir() else "absent")
    ck("prep: it says so", "1", count("the rebase conflicted", r.err or r.out))


# --- fix_gate ----------------------------------------------------------------------


def fix_contract(visit: str, reviewable: bool = True) -> str:
    return json.dumps(
        {
            "_io": {"visit": visit},
            "summary": "fixed the guard",
            "commands": [{"command": "npm test", "ok": True, "output": "1 passing"}],
            "reviewable": reviewable,
            "blockers": []
            if reviewable
            else [{"kind": "not_code", "text": "the PR body has no commit-body block"}],
        }
    )


def test_fix_gate_accepts_a_turn_with_a_matching_receipt(ck, tmp_path):
    D = Doctor(ck, tmp_path, "gate-ok")
    write(D.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
    write(D.fabro / "doctor/fix.json", fix_contract("v1", reviewable=False))
    r = D.run("fix_gate")
    ck("fix_gate: routes to validate", "true", D.routed(r, "fix_ok"))
    ck("fix_gate: pushable", "yes", cat(D.fabro / "doctor/pushable"))
    ck("fix_gate: an unreviewable contract says skip", "skip",
       cat(D.fabro / "doctor/reviewable"))
    ck("fix_gate: the commands are kept", "1",
       jq("length", file=D.fabro / "doctor/commands.json"))


def test_fix_gate_says_fire_for_a_reviewable_contract(ck, tmp_path):
    D = Doctor(ck, tmp_path, "gate-fire")
    write(D.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
    write(D.fabro / "doctor/fix.json", fix_contract("v1", reviewable=True))
    r = D.run("fix_gate")
    ck("fix_gate: review fires", "fire", cat(D.fabro / "doctor/reviewable"))
    ck("fix_gate: pushable", "yes", cat(D.fabro / "doctor/pushable"))


def test_fix_gate_refuses_a_missing_or_stale_receipt(ck, tmp_path):
    for name, stage_visit, contract_visit in (
        ("missing", "v1", ""),
        ("stale", "v2", "v1"),
        ("no-stage", "", "v1"),
    ):
        D = Doctor(ck, tmp_path, f"gate-{name}")
        if stage_visit:
            write(D.fabro / ".io/stage.json", json.dumps({"visit": stage_visit}))
        if contract_visit:
            write(D.fabro / "doctor/fix.json", fix_contract(contract_visit))
        r = D.run("fix_gate")
        ck(f"fix_gate {name}: not ok", "false", D.routed(r, "fix_ok"))
        ck(f"fix_gate {name}: nothing is pushed", "no", cat(D.fabro / "doctor/pushable"))
        ck(f"fix_gate {name}: no review", "skip", cat(D.fabro / "doctor/reviewable"))


# --- validate ----------------------------------------------------------------------


def ci_repo(D: Doctor, *, fail_times: int) -> None:
    """A repository whose `.fabro/ci.sh` fails the first `fail_times` runs."""
    (D.repo / ".fabro").mkdir(parents=True, exist_ok=True)
    write(D.repo / ".fabro/setup.sh", "#!/bin/sh\nexit 0\n")
    write(
        D.repo / ".fabro/ci.sh",
        "#!/bin/sh\n"
        'n=$(cat "$CI_COUNTER" 2>/dev/null || echo 0)\n'
        "n=$((n+1))\n"
        'echo "$n" > "$CI_COUNTER"\n'
        f'if [ "$n" -le {fail_times} ]; then echo boom >&2; exit 1; fi\n'
        "echo green\n",
    )
    (D.repo / ".fabro/setup.sh").chmod(0o755)
    (D.repo / ".fabro/ci.sh").chmod(0o755)


def test_validate_green_asks_for_no_retry(ck, tmp_path):
    D = Doctor(ck, tmp_path, "validate-green")
    ci_repo(D, fail_times=0)
    r = D.run("validate", env={"CI_COUNTER": str(D.counter)})
    ck("validate: ci_ok", "true", D.routed(r, "ci_ok"))
    ck("validate: no retry", "false", D.routed(r, "retry_ok"))
    ck("validate: writes no failure note", "absent", present(D.fabro / "doctor/ci_failure.md"))


def test_validate_red_asks_for_one_more_fix_turn(ck, tmp_path):
    D = Doctor(ck, tmp_path, "validate-red1")
    ci_repo(D, fail_times=1)
    write(D.fabro / "doctor/fix.json", fix_contract("v1"))
    write(D.fabro / "doctor/pushable", "yes\n")  # what a passing fix_gate left
    r = D.run("validate", env={"CI_COUNTER": str(D.counter)})
    ck("validate: ci_ok is false", "false", D.routed(r, "ci_ok"))
    ck("validate: retry_ok", "true", D.routed(r, "retry_ok"))
    ck("validate: attempt 1", "1", cat(D.fabro / "doctor/attempts"))
    ck("validate: not pushable until a later green", "no", cat(D.fabro / "doctor/pushable"))
    ck("validate: the failure is the second turn's input", "1",
       count("boom", file=D.fabro / "doctor/ci_failure.md"))
    ck("validate: the fix contract is deleted first", "absent",
       present(D.fabro / "doctor/fix.json"))


def test_validate_green_makes_it_pushable(ck, tmp_path):
    D = Doctor(ck, tmp_path, "validate-green-push")
    ci_repo(D, fail_times=0)
    write(D.fabro / "doctor/pushable", "no\n")
    D.run("validate", env={"CI_COUNTER": str(D.counter)})
    ck("validate: green says pushable", "yes", cat(D.fabro / "doctor/pushable"))


def test_a_killed_validate_pushes_nothing(ck, tmp_path):
    """F2 of the Phase 2 review: a timeout or a killed shell runs no line after the one in
    progress, and the node's unconditional edge takes it to `publish`. `fix_gate`'s `yes`
    must not survive that."""
    D = Doctor(ck, tmp_path, "validate-killed")
    ci_repo(D, fail_times=0)
    # ci.sh kills the validate shell itself, as fabro's timeout would.
    write(D.repo / ".fabro/ci.sh", "#!/bin/sh\nkill -KILL $PPID\nsleep 5\n")
    (D.repo / ".fabro/ci.sh").chmod(0o755)
    write(D.fabro / "doctor/pushable", "yes\n")  # what a passing fix_gate left
    r = D.run("validate", env={"CI_COUNTER": str(D.counter)})
    ck("validate: the shell was killed", "no-routing",
       "no-routing" if "context_updates" not in (r.out or "") else "routed")
    ck("validate: nothing is pushable", "no", cat(D.fabro / "doctor/pushable"))


def test_validate_red_twice_stops_pushing(ck, tmp_path):
    D = Doctor(ck, tmp_path, "validate-red2")
    ci_repo(D, fail_times=2)
    write(D.fabro / "doctor/attempts", "1\n")
    r = D.run("validate", env={"CI_COUNTER": str(D.counter)})
    ck("validate: attempt 2", "2", cat(D.fabro / "doctor/attempts"))
    ck("validate: no third try", "false", D.routed(r, "retry_ok"))
    ck("validate: nothing is pushed", "no", cat(D.fabro / "doctor/pushable"))


# --- publish -----------------------------------------------------------------------


def publish_repo(D: Doctor, *, push_fails: bool = False) -> str:
    """A real clone with a bare origin, the PR head at the marker's `head=`, and one doctor
    checkpoint commit on top that `publish` must squash and author itself."""
    origin = D.T / "origin.git"
    git("init", "-q", "--bare", "-b", "main", origin)
    git("clone", "-q", origin, D.repo)
    r = lambda *a: git("-c", "user.email=t@t", "-c", "user.name=t", *a, cwd=D.repo)
    write(D.repo / "a.txt", "one\n")
    r("add", ".")
    r("commit", "-q", "-m", "base")
    r("push", "-q", "origin", "main")
    r("checkout", "-q", "-b", "feature")
    head = git("-C", D.repo, "rev-parse", "HEAD")
    r("push", "-q", "origin", "feature")
    write(D.repo / "a.txt", "fixed\n")
    r(
        "commit",
        "-q",
        "-am",
        "a fabro checkpoint",
        "--author",
        "andrews-ai-agent <andrews-ai-agent@users.noreply.github.com>",
    )
    if push_fails:
        # Someone else moves the remote head: the lease's expectation no longer holds, so
        # the push is refused and the marker must stay `pushed=pending`.
        write(D.repo / "b.txt", "someone else\n")
        r("add", ".")
        r("commit", "-q", "-m", "someone else", "--author", "andrewthetechie <a@t>")
        r("push", "-q", "origin", "HEAD:feature")
        r("reset", "-q", "--hard", "HEAD~1")
    D.setup()
    write(D.fabro / "doctor/head_before", head + "\n")
    write(D.fabro / "doctor/fix_base", head + "\n")
    write(D.fabro / "doctor/pushable", "yes\n")
    write(D.fabro / "doctor/reviewable", "fire\n")
    write(D.fabro / "doctor/run_id", "01MDOCTOR")
    write(D.fabro / "doctor/reason.txt", "**Reason:** There are unfixed findings.\n")
    write(D.fabro / "doctor/fix.accepted.json", fix_contract("v1"))
    write(
        D.fabro / "doctor/commands.json",
        json.dumps([{"command": "npm test", "ok": True, "output": "1 passing"}]),
    )
    write(D.fabro / "head_ref", "feature\n")
    write(D.fabro / "base_ref", "main\n")
    write(D.fabro / "pr_number", "7\n")
    return head


def test_publish_posts_the_marker_before_the_labels_and_the_push(ck, tmp_path):
    D = Doctor(ck, tmp_path, "publish-ok")
    head = publish_repo(D)
    r = D.run("publish", cwd=D.repo)
    calls = D.gh()
    joined = "\n".join(calls)
    # C6's order, exactly: the marker, then the two label removals, then the marker again with
    # the new head. `PATCH` edits the comment in place rather than posting another (C6).
    ck("publish: the marker is written first", "pr comment", calls[0].split(" ")[0] + " " + calls[0].split(" ")[1])
    ck("publish: then ai-review-needs-human is removed", "1",
       count("^pr edit 7 --remove-label ai-review-needs-human$", joined))
    ck("publish: then merge-on-green is removed", "1",
       count("^pr edit 7 --remove-label merge-on-green$", joined))
    ck("publish: the marker is edited in place, in one PATCH", "1",
       count("api -X PATCH", joined))
    ck("publish: the edit names the same comment", "1",
       count("comments/99", joined))
    ck("publish: the marker edit is the last call", "api", calls[-1].split(" ")[0])
    ck("publish: no second comment is posted", "1", count("^pr comment", joined))
    ck("publish: routes as pushed", "pushed", D.routed(r, "published"))

    new = git("-C", D.repo, "rev-parse", "HEAD")
    ck("publish: the pushed head is the doctor commit", new,
       git("-C", D.T / "origin.git", "rev-parse", "feature"))
    ck("publish: one commit on top of head=", "1",
       git("-C", D.repo, "rev-list", "--count", f"{head}..{new}"))

    # The final marker line carries the new head, the review decision and the run id.
    manifest = cat(D.fabro / "doctor/comment.head")
    ck("publish: the marker names the new head", "1", count(f"pushed={new}", manifest))
    ck("publish: the marker keeps head=", "1", count(f"head={head}", manifest))
    ck("publish: the marker says review=fire", "1", count("review=fire", manifest))
    ck("publish: the marker says run=", "1", count("run=01MDOCTOR", manifest))

    # Decision 5: the pushed commit's author is the doctor's, and the review gate's own
    # filter (review-merge.fabro:927) accepts every commit on the PR.
    author = git("-C", D.repo, "log", "-1", "--format=%ae", "HEAD")
    ck("publish: the commit's author", "noreply@fabro.sh", author)
    gh_commits = json.dumps({"commits": [{"authors": [{"email": author}], "oid": new}]})
    rejected = jq(
        '[.commits[] | select([.authors[].email] | index("noreply@fabro.sh") | not) | .oid]',
        gh_commits,
    )
    ck("publish: gate 6 rejects no commit", "[]", rejected.replace(" ", ""))


def test_a_failed_push_leaves_pushed_pending(ck, tmp_path):
    D = Doctor(ck, tmp_path, "publish-fail")
    publish_repo(D, push_fails=True)
    r = D.run("publish", cwd=D.repo)
    ck("publish: routes as pending", "pending", D.routed(r, "published"))
    ck("publish: the marker still says pending", "1",
       count("pushed=pending", file=D.fabro / "doctor/comment.head"))
    ck("publish: the marker is not patched", "0", count("api -X PATCH", "\n".join(D.gh())))


def test_publish_with_nothing_to_push_writes_pushed_none_and_touches_no_label(ck, tmp_path):
    D = Doctor(ck, tmp_path, "publish-none")
    head = publish_repo(D)
    write(D.fabro / "doctor/pushable", "no\n")
    r = D.run("publish", cwd=D.repo)
    ck("publish: routes as none", "none", D.routed(r, "published"))
    ck("publish: the marker says none", "1", count("pushed=none", file=D.fabro / "doctor/comment.head"))
    ck("publish: and skip", "1", count("review=skip", file=D.fabro / "doctor/comment.head"))
    ck("publish: no label is removed", "0", count("--remove-label", "\n".join(D.gh())))
    ck("publish: the PR head is untouched", head,
       git("-C", D.T / "origin.git", "rev-parse", "feature"))


def test_publish_reports_each_command_it_ran(ck, tmp_path):
    """Operator decision 2: the commands the doctor ran are published for the human who takes
    the PR."""
    D = Doctor(ck, tmp_path, "publish-commands")
    publish_repo(D)
    write(
        D.fabro / "doctor/commands.json",
        json.dumps(
            [{"command": "npm run test:coverage", "ok": False, "output": "coverage 41%"}]
        ),
    )
    write(D.fabro / "doctor/fix.accepted.json", fix_contract("v1", reviewable=False))
    write(D.fabro / "doctor/reviewable", "skip\n")
    D.run("publish", cwd=D.repo)
    body = cat(D.fabro / "doctor/comment.tail")
    ck("publish: the command is named", "1", count("npm run test:coverage", body))
    ck("publish: and its failure", "1", count("FAILED", body))
    ck("publish: and its output", "1", count("coverage 41%", body))
    ck("publish: no follow-up review is announced", "1", count("No follow-up review", body))
    ck("publish: no review is promised", "0", count("One automated review follows", body))


def test_fix_gate_validate_publish_in_their_real_order(ck, tmp_path):
    """F1 of the Phase 2 review: `validate` deletes `fix.json`, so a `publish` that read it
    posted "The fix turn submitted no report." and an empty blocker list on every PR. This
    runs the three nodes in the order the graph does, with nothing written between them."""
    D = Doctor(ck, tmp_path, "sequence")
    publish_repo(D)
    rm(D.fabro / "doctor/fix.accepted.json", D.fabro / "doctor/pushable",
       D.fabro / "doctor/reviewable", D.fabro / "doctor/commands.json")
    ci_repo(D, fail_times=0)
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".fabro", cwd=D.repo)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "ci", cwd=D.repo)
    write(D.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
    write(D.fabro / "doctor/fix.json", fix_contract("v1", reviewable=False))
    r = D.run("fix_gate")
    ck("sequence: fix_gate accepts", "true", D.routed(r, "fix_ok"))
    r = D.run("validate", env={"CI_COUNTER": str(D.counter)})
    ck("sequence: validate is green", "true", D.routed(r, "ci_ok"))
    ck("sequence: validate deleted fix.json", "absent", present(D.fabro / "doctor/fix.json"))
    r = D.run("publish", cwd=D.repo)
    ck("sequence: publish pushed", "pushed", D.routed(r, "published"))
    body = cat(D.fabro / "doctor/comment.tail")
    ck("sequence: the summary is in the comment", "1", count("fixed the guard", body))
    ck("sequence: no 'submitted no report'", "0", count("submitted no report", body))
    ck("sequence: the blocker is in the comment", "1",
       count("not_code: the PR body has no commit-body block", body))
    ck("sequence: review=skip", "1", count("review=skip", file=D.fabro / "doctor/comment.head"))
    ck("sequence: no review is promised", "0", count("One automated review follows", body))
    ck("sequence: it says no review follows", "1", count("No automated review follows", body))


def test_a_rejected_second_turn_does_not_reuse_the_first_turns_account(ck, tmp_path):
    D = Doctor(ck, tmp_path, "gate-reject-after-accept")
    write(D.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
    write(D.fabro / "doctor/fix.json", fix_contract("v1"))
    D.run("fix_gate")
    ck("fix_gate: the accepted copy exists", "present",
       present(D.fabro / "doctor/fix.accepted.json"))
    write(D.fabro / ".io/stage.json", json.dumps({"visit": "v2"}))
    rm(D.fabro / "doctor/fix.json")
    D.run("fix_gate")
    ck("fix_gate: a rejected turn removes it", "absent",
       present(D.fabro / "doctor/fix.accepted.json"))


def test_publish_scrubs_the_tokens_from_the_comment(ck, tmp_path):
    """Two of the repositories are public, and `output` is whatever the command printed."""
    D = Doctor(ck, tmp_path, "publish-scrub")
    publish_repo(D)
    secret = "fab_tok.EN+with/odd*chars"
    gh_secret = "ghs_0123456789abcdef"
    write(
        D.fabro / "doctor/commands.json",
        json.dumps([{"command": "env", "ok": True,
                     "output": f"FABRO_API_TOKEN={secret}\nGITHUB_TOKEN={gh_secret}"}]),
    )
    D.run("publish", cwd=D.repo,
          env={"FABRO_API_TOKEN": secret, "GITHUB_TOKEN": gh_secret, "GH_TOKEN": gh_secret})
    body = cat(D.fabro / "doctor/comment.md")
    ck("publish: the fabro token is gone", "0", count(secret, body, flags="F"))
    ck("publish: the GitHub token is gone", "0", count(gh_secret, body, flags="F"))
    ck("publish: each is marked redacted", "2", count(r"\[redacted\]", body))


# --- decision 5's gate -------------------------------------------------------------


def test_the_package_can_only_produce_commits_authored_as_the_doctor(ck):
    """A gate test that fails if `fix` or `publish` can produce a commit whose authors lack
    `noreply@fabro.sh`.

    `fix` is an agent, and the `git-guard` hook blocks mutating git from its shell, so the only
    commit this package makes is `publish`'s squash. This asserts the identity is on that one
    command, and that nothing else in the package commits at all.
    """
    graph = GRAPH.read_text()
    publish = extract(GRAPH, "publish")
    ck("publish: commits with the doctor's email", "1",
       count(r"-c user.email=noreply@fabro.sh", publish))
    ck("publish: and the doctor's name", "1", count(r"-c user.name=fabro-pr-doctor", publish))
    ck("the package has exactly one commit command", "1",
       count(r"git -c user.email=[^ ]* -c user.name=[^ ]* commit", graph))
    ck("no bare git commit", "0", count(r"git commit", graph))
    # Never the base branch, and always the lease.
    ck("publish: pushes the head ref", "1", count(r'origin "HEAD:\$H"', publish))
    ck("publish: never pushes the base ref", "0", count(r"HEAD:\$B", publish))
    ck("publish: force-with-lease, never a bare --force", "0",
       str(len([word for word in publish.split() if word == "--force"])))


def test_a_failed_claim_ends_the_run_failed(ck):
    """F8 of the Phase 2 review: through `stop` a failed `claim` ended the run `succeeded`
    with no marker, and the scheduler recorded a used attempt nothing showed."""
    E = edges(GRAPH)
    for e in ("claim->prep outcome=succeeded && context.doctor_go=true",
              "claim->stop outcome=succeeded && context.doctor_go=false",
              "claim->exit "):
        ck(f"edge: {e}", "1", count(e, E, flags="xF"))
    ck("edge: claim has no other exit", "3", count("^claim->", E))
