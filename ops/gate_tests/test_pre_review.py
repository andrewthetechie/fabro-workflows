"""The box pre-review (pre-review-and-calibration, Part B): `pre_review_prep` and
`pre_review_check`, in `backlog`. `ralph` had the same two nodes until 2026-11-12, when its
pre-review was replaced by the local standards/spec review (`test_ralph.py`).

`fabro validate` proves the graphs parse. It never runs a node's script, and the whole contract
of this stage is in two scripts:

* `pre_review_prep` decides **once per run** whether this run pre-reviews, from the issue
  number's parity (the A/B arm), and records the base and the branch's diff. A second visit --
  after a Refuter round, or after extra-review follow-ups -- must route past it.
* `pre_review_check` keeps the pre-review's edits only when the scoped `task-check` is green
  over exactly those edits, and otherwise reverts the tree to the base **including the files
  the stage added**. It never fails the run and never calls a rework node: the pre-review may
  only help.

backlog's edges are tested exactly, and so is the absence of the stage from ralph.
"""

from __future__ import annotations

import json

from harness import (ORIG_PATH, RAPH, WF, cat, count, edges, extract, git, jq, lastjson, lines,
                     present, rebase, rm, sh, stub, write)

BACKLOG = WF / "backlog" / "workflow.fabro"

# A `fabro-io` that writes a canned task-check result to --out and exits with a chosen code.
FABRO_IO_STUB = """#!/bin/sh
[ -n "${PR_IO_LOG:-}" ] && printf '%s\\n' "$*" >> "$PR_IO_LOG"
out=
while [ $# -gt 0 ]; do
  case "$1" in --out) out=$2; shift 2 ;; *) shift ;; esac
done
if [ -n "${PR_CANNED:-}" ] && [ -f "$PR_CANNED" ]; then
  [ -n "$out" ] && cp "$PR_CANNED" "$out"
  cat "$PR_CANNED"
fi
exit "${PR_IO_RC:-0}"
"""

SCOPED_OK = '{"version":1,"mode":"scoped","ok":true,"changed":["src.txt"],"targets":[],"results":[]}'
SCOPED_RED = '{"version":1,"mode":"scoped","ok":false,"changed":["src.txt"],"targets":[],"results":[]}'
FULL = '{"version":1,"mode":"full","reason":"no_config","ok":null,"changed":[],"targets":[]}'


class PreReview:
    """A scratch checkout whose branch is one commit ahead of `origin/main`."""

    def __init__(self, ck, tmp_path, name: str, graph=BACKLOG, *, number: str = "7"):
        self.T = tmp_path / name
        self.repo = self.T / "repo"
        self.fabro = self.T / "fabro"
        self.bin = self.T / "bin"
        for d in (self.fabro / "pre_review", self.fabro / ".io", self.bin, self.repo):
            d.mkdir(parents=True, exist_ok=True)
        write(self.repo / "src.txt", "base\n")
        git("init", "-q", "-b", "main", self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base",
            cwd=self.repo)
        self.base_sha = git("rev-parse", "HEAD", cwd=self.repo)
        git("remote", "add", "origin", self.repo, cwd=self.repo)
        git("update-ref", "refs/remotes/origin/main", self.base_sha, cwd=self.repo)
        git("checkout", "-q", "-b", "work", cwd=self.repo)
        write(self.repo / "src.txt", "branch\n")
        write(self.repo / "docs/adr/0001-x.md", "an adr\n")
        git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=self.repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "task",
            cwd=self.repo)
        self.head = git("rev-parse", "HEAD", cwd=self.repo)
        # As `backlog`'s and `ralph`'s `prep` leave it: the index is ignored, so the revert's
        # `git clean -fd` does not take it.
        write(self.repo / ".git/info/exclude", ".codegraph/\n.codex/instructions.md\n")
        write(self.fabro / "issue_number", number)
        write(self.fabro / "issue.json", json.dumps({"number": int(number), "title": "x"}))
        stub(self.bin, "fabro-io", FABRO_IO_STUB)
        self.canned = self.T / "canned.json"
        self.nodes = {}
        for node in ("pre_review_prep", "pre_review_check"):
            script = rebase(extract(graph, node), self.fabro)
            self.nodes[node] = script
            ok, err = __import__("harness").syntax_ok(write(self.T / f"{node}.sh", script))
            if not ok:
                ck.fail(f"{node} is not valid POSIX sh", err)

    def run(self, node: str, **env: str):
        base = {"PR_CANNED": str(self.canned), "PR_IO_RC": "0",
                "PR_IO_LOG": str(self.T / "fabro-io.log")}
        base.update(env)
        script = write(self.T / f"{node}.sh", self.nodes[node])
        return sh(script, cwd=self.repo, path=f"{self.bin}:{ORIG_PATH}", env=base,
                  stderr="keep")

    # --- the state a node leaves --------------------------------------------------

    def edit(self) -> None:
        """What a pre-review does: change a file, add one, and leave an ignored file alone."""
        write(self.repo / "src.txt", "edited\n")
        write(self.repo / "notes/new.md", "a new file\n")
        write(self.repo / ".codegraph/keep", "index\n")

    def receipt(self, *, met: int = 1, not_met: int = 1) -> None:
        write(self.fabro / ".io/stage.json", json.dumps({"visit": "v1"}))
        criteria = [{"criterion": "it works", "status": "met", "where": "src.txt:1"}] * met
        criteria += [{"criterion": "it also does x", "status": "not_met", "where": "?"}] * not_met
        write(self.fabro / "pre_review.json", json.dumps({
            "_io": {"visit": "v1"},
            "summary": "checked",
            "criteria": criteria,
            "changes": [{"file": "src.txt", "why": "the rule"}],
        }))


def routed(result, key: str) -> str:
    return jq(f".context_updates.{key}", lastjson(result.out))


def log_of(P: PreReview) -> str:
    return cat(P.fabro / "pre_review.log")


# --- pre_review_prep: the arm and the once-per-run latch ---------------------------

# Bit 2 of the issue number (more-local-inference Part 0). Not `N % 2`: that is the ralph
# trial's own bit, and under it the pre-review ran on every backlog run and no ralph run.
ON, OFF = (4, 5, 6, 7, 12, 13, 14, 15), (0, 1, 2, 3, 8, 9, 10, 11)


def test_the_arm_is_bit_two_of_the_issue_number(ck, tmp_path):
    """Issues 4-7 pre-review, 0-3 do not, and the same for the next block of eight."""
    for graph, name in ((BACKLOG, "backlog"),):
        for number in ON:
            P = PreReview(ck, tmp_path, f"arm-{name}-{number}", graph=graph,
                          number=str(number))
            ck(f"{name}: #{number} is on", "on",
               routed(P.run("pre_review_prep"), "pre_review_arm"))
        for number in OFF:
            P = PreReview(ck, tmp_path, f"arm-{name}-{number}", graph=graph,
                          number=str(number))
            ck(f"{name}: #{number} is off", "off",
               routed(P.run("pre_review_prep"), "pre_review_arm"))


def test_the_arm_is_not_the_ralph_trials_bit(ck, tmp_path):
    """The first arm was `N % 2`, which is exactly the scheduler's ralph/backlog split
    (`dispatch.choose_workflow`), so the pre-review ran on no ralph run. Bit 2 must put half
    of each workflow's runs on each arm, and every issue the old bit switched on must not
    decide it."""
    for graph in (BACKLOG,):
        on, off = [], []
        for number in range(0, 16):
            P = PreReview(ck, tmp_path, f"bits-{graph}-{number}", graph=graph,
                          number=str(number))
            arm = routed(P.run("pre_review_prep"), "pre_review_arm")
            (on if arm == "on" else off).append(number)
        ck("an even split of the first sixteen issues", "8/8", f"{len(on)}/{len(off)}")
        ck("and the arms are the same set for both graphs", str(list(ON)),
           str([n for n in range(0, 16) if n in on]))
        # Any issue the ralph trial would send to ralph (even) is not always off.
        ck("the ralph arm's bit does not decide it", "true",
           str(bool(on and any(n % 2 == 0 for n in on))).lower())


def test_prep_pre_reviews_on_the_arm_and_records_the_base(ck, tmp_path):
    P = PreReview(ck, tmp_path, "prep-on", number="7")
    r = P.run("pre_review_prep")
    ck("prep: routes to the pre-review", "true", routed(r, "pre_review_go"))
    ck("prep: publishes the arm", "on", routed(r, "pre_review_arm"))
    ck("prep: the base is HEAD", P.head, cat(P.fabro / "pre_review_base"))
    ck("prep: the arm is on disk", "on", cat(P.fabro / "pre_review/arm"))
    ck("prep: the branch diff is staged", "1",
       count("^+branch$", cat(P.fabro / "pre_review/diff.patch")))
    ck("prep: changed files are staged", "1",
       count("^src.txt$", cat(P.fabro / "pre_review/changed_files.txt")))
    ck("prep: the standards docs that exist are listed", "1",
       count("^docs/adr$", cat(P.fabro / "pre_review/standards_docs.txt")))
    ck("prep: the missing ones are not", "0",
       count("^CLAUDE.md$", cat(P.fabro / "pre_review/standards_docs.txt")))
    ck("prep: the latch is written", "present", present(P.fabro / "pre_reviewed"))


def test_prep_does_not_pre_review_off_the_arm(ck, tmp_path):
    P = PreReview(ck, tmp_path, "prep-off", number="8")
    r = P.run("pre_review_prep")
    ck("prep off: routes past the pre-review", "false", routed(r, "pre_review_go"))
    ck("prep off: publishes the arm", "off", routed(r, "pre_review_arm"))
    ck("prep off: the latch is still written, so it is decided once",
       "present", present(P.fabro / "pre_reviewed"))
    ck("prep off: nothing is prepared, because nothing will read it", "absent",
       present(P.fabro / "pre_review/diff.patch"))
    ck("prep off: and no base is recorded", "absent", present(P.fabro / "pre_review_base"))


def test_prep_routes_past_the_review_on_a_second_visit(ck, tmp_path):
    P = PreReview(ck, tmp_path, "prep-twice", number="7")
    ck("prep: first visit", "true", routed(P.run("pre_review_prep"), "pre_review_go"))
    r = P.run("pre_review_prep")
    ck("prep: the second visit does not pre-review again", "false", routed(r, "pre_review_go"))
    ck("prep: and still says which arm", "on", routed(r, "pre_review_arm"))


def test_prep_reads_the_issue_number_from_issue_json_when_the_file_is_gone(ck, tmp_path):
    P = PreReview(ck, tmp_path, "prep-json", number="7")
    rm(P.fabro / "issue_number")
    ck("prep: the arm comes from issue.json", "on",
       routed(P.run("pre_review_prep"), "pre_review_arm"))
    Q = PreReview(ck, tmp_path, "prep-none", number="7")
    rm(Q.fabro / "issue_number", Q.fabro / "issue.json")
    ck("prep: with no issue number it is off, like #0", "off",
       routed(Q.run("pre_review_prep"), "pre_review_arm"))


# --- pre_review_check: keep, or revert --------------------------------------------


def test_check_keeps_nothing_when_the_pre_review_changed_nothing(ck, tmp_path):
    P = PreReview(ck, tmp_path, "check-clean")
    P.run("pre_review_prep")
    r = P.run("pre_review_check")
    ck("check clean: reports kept", "true", routed(r, "pre_review_kept"))
    ck("check clean: the outcome is unchanged", "1", count("outcome=unchanged", log_of(P)))
    ck("check clean: and publishes it", "unchanged", routed(r, "pre_review_verdict"))


def test_check_reverts_a_stage_with_no_receipt(ck, tmp_path):
    P = PreReview(ck, tmp_path, "check-noreceipt")
    P.run("pre_review_prep")
    P.edit()
    r = P.run("pre_review_check")
    ck("check no receipt: reports not kept", "false", routed(r, "pre_review_kept"))
    ck("check no receipt: the tracked file is back at the base", "branch",
       cat(P.repo / "src.txt"))
    ck("check no receipt: the file the stage added is gone", "absent",
       present(P.repo / "notes/new.md"))
    ck("check no receipt: the ignored index is left alone", "index",
       cat(P.repo / ".codegraph/keep"))
    ck("check no receipt: the outcome", "1", count("outcome=no_receipt", log_of(P)))
    ck("check no receipt: and publishes it", "no_receipt", routed(r, "pre_review_verdict"))


def test_check_keeps_edits_a_green_scoped_check_passed(ck, tmp_path):
    P = PreReview(ck, tmp_path, "check-green")
    P.run("pre_review_prep")
    P.edit()
    P.receipt(met=2, not_met=1)
    write(P.canned, SCOPED_OK)
    r = P.run("pre_review_check")
    ck("check green: reports kept", "true", routed(r, "pre_review_kept"))
    ck("check green: the edit is still there", "edited", cat(P.repo / "src.txt"))
    ck("check green: the new file is still there", "present", present(P.repo / "notes/new.md"))
    ck("check green: the measurement line has the arm, outcome, files and unmet count", "1",
       count("^arm=on outcome=kept kept=true files=2 unmet=1$", log_of(P)))
    ck("check green: and publishes its verdict", "kept", routed(r, "pre_review_verdict"))


def test_check_reverts_edits_a_red_scoped_check_failed(ck, tmp_path):
    # `fabro-io task-check` exits 1 on a red scoped check (`taskcheck.rs`: success only for
    # `full` or `ok`), so the verdict must come from task_check.json, not the exit code.
    P = PreReview(ck, tmp_path, "check-red")
    P.run("pre_review_prep")
    P.edit()
    P.receipt()
    write(P.canned, SCOPED_RED)
    r = P.run("pre_review_check", PR_IO_RC="1")
    ck("check red: reports not kept", "false", routed(r, "pre_review_kept"))
    ck("check red: the tracked file is back at the base", "branch", cat(P.repo / "src.txt"))
    ck("check red: the file the stage added is gone", "absent",
       present(P.repo / "notes/new.md"))
    ck("check red: the outcome", "1", count("outcome=red", log_of(P)))
    ck("check red: and publishes it", "red", routed(r, "pre_review_verdict"))


def test_check_reverts_when_task_check_errors(ck, tmp_path):
    P = PreReview(ck, tmp_path, "check-err")
    P.run("pre_review_prep")
    P.edit()
    P.receipt()
    r = P.run("pre_review_check", PR_IO_RC="2")
    ck("check error: reports not kept", "false", routed(r, "pre_review_kept"))
    ck("check error: the tracked file is back at the base", "branch", cat(P.repo / "src.txt"))
    ck("check error: the outcome", "1", count("outcome=check_error", log_of(P)))
    ck("check error: and publishes it", "check_error", routed(r, "pre_review_verdict"))


def test_check_reverts_edits_task_check_could_not_scope(ck, tmp_path):
    # `full` means task-check could not attribute the diff, so nothing validated the edits.
    # Keeping them would leave Final validation (backlog) or final_check (ralph) to find a red
    # tree, at a hosted final_fix or a mark_needs_human the pre-review caused (review F4).
    P = PreReview(ck, tmp_path, "check-full")
    P.run("pre_review_prep")
    P.edit()
    P.receipt()
    write(P.canned, FULL)
    r = P.run("pre_review_check")
    ck("check full: reports not kept", "false", routed(r, "pre_review_kept"))
    ck("check full: the tracked file is back at the base", "branch", cat(P.repo / "src.txt"))
    ck("check full: the file the stage added is gone", "absent",
       present(P.repo / "notes/new.md"))
    ck("check full: the outcome says the check was not scoped", "1",
       count("outcome=unscoped kept=false", log_of(P)))
    ck("check full: and publishes it", "unscoped", routed(r, "pre_review_verdict"))


def test_check_reverts_every_edit_when_one_touches_pipeline_config(ck, tmp_path):
    # Diff hygiene holds a PR for `config_changed` and a workflow file is refused at the push;
    # the prompt forbids both, and this is the guarantee (review F3).
    for name, path, tracked in (
        ("ci", ".fabro/ci.sh", True),
        ("new-config", ".fabro/test.toml", False),
        ("workflow", ".github/workflows/ci.yml", True),
    ):
        P = PreReview(ck, tmp_path, f"check-forbidden-{name}")
        if tracked:
            write(P.repo / path, "original\n")
            git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".", cwd=P.repo)
            git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "cfg",
                cwd=P.repo)
        P.run("pre_review_prep")
        P.edit()
        write(P.repo / path, "changed by the pre-review\n")
        P.receipt()
        write(P.canned, SCOPED_OK)
        r = P.run("pre_review_check")
        ck(f"check {name}: reports not kept", "false", routed(r, "pre_review_kept"))
        ck(f"check {name}: the config is back", "original" if tracked else "absent",
           cat(P.repo / path) if tracked else present(P.repo / path))
        ck(f"check {name}: and so is every other edit", "branch", cat(P.repo / "src.txt"))
        ck(f"check {name}: the outcome", "1", count("outcome=forbidden_path", log_of(P)))
        ck(f"check {name}: and publishes it", "forbidden_path",
           routed(r, "pre_review_verdict"))
        ck(f"check {name}: task-check is not run", "absent", present(P.T / "fabro-io.log"))


def test_check_reverts_when_task_check_times_out(ck, tmp_path):
    # 124 is `timeout`'s own exit code. The node's 25m must never be reached first: a killed
    # node takes its unconditional edge with the edits still in the tree (review F5).
    P = PreReview(ck, tmp_path, "check-slow")
    P.run("pre_review_prep")
    P.edit()
    P.receipt()
    r = P.run("pre_review_check", PR_IO_RC="124")
    ck("check slow: reports not kept", "false", routed(r, "pre_review_kept"))
    ck("check slow: the tracked file is back at the base", "branch", cat(P.repo / "src.txt"))
    ck("check slow: the outcome", "1", count("outcome=check_timeout", log_of(P)))
    ck("check slow: and publishes it", "check_timeout", routed(r, "pre_review_verdict"))
    script = P.nodes["pre_review_check"]
    ck("check: task-check is held under the node's timeout", "1",
       count("TO='timeout 15m'", script, flags="F"))
    ck("check: no set -e, so the revert always runs", "0", count("^set -e", script))


def test_check_survives_a_timeout_that_left_no_files(ck, tmp_path):
    # The stage failed or timed out: prep's base is there, the receipt is not, and the tree may
    # be anything. It must route on, not fail the run.
    P = PreReview(ck, tmp_path, "check-timeout")
    P.run("pre_review_prep")
    P.edit()
    r = P.run("pre_review_check")
    ck("check timeout: the node succeeds", "0", str(r.rc))
    ck("check timeout: and routes on", "false", routed(r, "pre_review_kept"))


# --- the edges, in both graphs ----------------------------------------------------


def test_the_arm_and_the_latch_decide_the_backlog_edges(ck):
    e = edges(BACKLOG)
    ck("backlog: the standard lane enters the prep, not extra_prep", "1",
       count("next_task->pre_review_prep context.tasks_done=true && context.lane!=fast",
             e, flags="xF"))
    ck("backlog: extra_prep has no direct entry from next_task", "0",
       count("next_task->extra_prep", e, flags="F"))
    ck("backlog: the prep enters the review only on its own go", "1",
       count("pre_review_prep->pre_review outcome=succeeded && context.pre_review_go=true",
             e, flags="xF"))
    ck("backlog: the prep has an unconditional fallback", "1",
       count("pre_review_prep->extra_prep ", e, flags="xF"))
    ck("backlog: the review always goes to the check", "1",
       count("pre_review->pre_review_check ", e, flags="xF"))
    ck("backlog: the check always routes on to the extra review", "1",
       count("pre_review_check->extra_prep ", e, flags="xF"))
    ck("backlog: the fast lane is untouched", "1",
       count("next_task->final_merge context.tasks_done=true && context.lane=fast",
             e, flags="xF"))


def test_ralph_has_no_pre_review(ck):
    """ralph's whole-branch pre-review was replaced by the local review (2026-11-12): none of
    its nodes may come back by a merge, and the finished loop goes straight to final_merge."""
    e = edges(RAPH)
    ck("ralph: no edge touches a pre_review node", "0", count("pre_review", e, flags="F"))
    ck("ralph: a finished loop goes straight to final_merge", "1",
       count("pick->final_merge context.loop=done && context.phase=pre", e, flags="xF"))
    ck("ralph: an exhausted loop does too", "1",
       count("pick->final_merge context.loop=exhausted && context.phase=pre", e, flags="xF"))
    ck("ralph: its prompt is gone", "absent", present(WF / "ralph/prompts/pre_review.md.j2"))


def test_check_publishes_no_base_when_prep_left_none(ck, tmp_path):
    """A prep that never ran (a first visit routed past the review, then a re-entry that
    reached the check) leaves no base, so there is nothing to compare the tree with."""
    P = PreReview(ck, tmp_path, "check-nobase")
    P.edit()
    r = P.run("pre_review_check")
    ck("check no base: reports not kept", "false", routed(r, "pre_review_kept"))
    ck("check no base: and publishes it", "no_base", routed(r, "pre_review_verdict"))


def test_every_check_outcome_publishes_a_known_verdict(ck):
    """The scheduler's `run_summary` column counts these, so the published value must be one
    of the nine `pre_review_check` can reach, spelled the way its log line spells it."""
    script = extract(BACKLOG, "pre_review_check")
    published = set()
    for line in script.splitlines():
        if "context_updates" in line and "pre_review_verdict" in line:
            published.add(line)
    ck("the check publishes the verdict on one line", "1", str(len(published)))
    import re
    reachable = set(re.findall(r"VERDICT=([a-z_]+)", script))
    ck("every outcome the document names is reachable",
       "check_error,check_timeout,forbidden_path,kept,no_base,no_receipt,red,unchanged,unscoped",
       ",".join(sorted(reachable)))
