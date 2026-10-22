"""The dependabot package (ADR 0022): prep, inventory and the version comparator, the merge
loop, pick_group / track_issue / sync_base, the patch path, open_pr / after_merge /
pr_handoff, summarize, and the dependabot-summary Discord kind."""

import os
import time
from types import SimpleNamespace

from harness import (DEP, ORIG_PATH, REPO_ROOT, WF, cat, count, extract, git, grep, head1, jq,
                     jq_n, jq_ok, rebase, rm, routing_check, run, run_files, run_node,
                     sh, stub, syntax_ok, tail1, write)

DEP_FIX = REPO_ROOT / "ops/tests/fixtures/dependabot"
ALERTS_JELLY = DEP_FIX / "jelly-swipe-alerts-open.json"
PRS_JELLY = DEP_FIX / "jelly-swipe-prs-open.json"


def route(prefix, filt):
    """`tail -1 prefix.out | jq -r filt`."""
    return jq(filt, tail1(f"{prefix}.out"), pipe=True)


def exists01(p) -> str:
    return "1" if os.path.lexists(p) else "0"


def version_jq() -> str:
    """The comparator module inventory writes, cut out of the graph."""
    return run(["awk", "/version\\.jq <</{f=1; next} /^JQ$/{f=0} f"], stdin=extract(DEP, "inventory"), stderr="drop").out + "\n"


# ---------------------------------------------------------------------------
# dependabot — prep (ADR 0022 task 02): the per-repository switch and the run-local reset.
# gh and curl are stubbed; the automation rows come from a file the test writes.
# ---------------------------------------------------------------------------
def test_dependabot_prep(ck, tmp_path):
    stubs = tmp_path / "dep-stubs"
    stub(stubs / "bin", "gh", """#!/bin/sh
case "$*" in
  "repo view"*) echo '{"nameWithOwner":"andrewthetechie/jelly-swipe","defaultBranchRef":{"name":"main"}}' ;;
  *) echo "gh $*" >&2 ;;
esac
exit 0
""")
    stub(stubs / "bin", "curl", '#!/bin/sh\n[ -n "${CURL_FAIL:-}" ] && exit 22\ncat "$AUTOROWS"\n')

    def case(name, description, auto_merge, curl_fails=False):
        """Runs prep in a fresh git repository standing for the checkout and /tmp/fabro."""
        d = tmp_path / f"dep-{name}"
        rm(d); (d / "dep").mkdir(parents=True)
        git("-C", d, "init", "-q")
        rows = write(f"{d}.rows.json", f'{{"data":[{{"id":"dependabot-jelly-swipe","workflow":"dependabot","target":{{"repo":"andrewthetechie/jelly-swipe"}},"description":{description}}}]}}')
        env = {"AUTOROWS": rows, "FABRO_API_URL": "http://fabro.test", "FABRO_API_TOKEN": "test-token", "FABRO_AUTO_MERGE": auto_merge}
        if curl_fails:
            env["CURL_FAIL"] = "1"
        run_node(ck, "prep", DEP, d, stubs / "bin", env=env)
        return d

    # 1. Empty description, host switch on: merge enabled.
    d = case("on", '""', "1")
    ck("dependabot prep: empty description, host on: exit 0", "0", cat(f"{d}.rc"))
    ck("dependabot prep: empty description, host on: merge_enabled 1", "1", cat(d / "dep/merge_enabled"))

    # 2. The row says auto_merge=false: off, and the reason names the repository switch.
    d = case("repo-off", '"auto_merge=false"', "1")
    ck("dependabot prep: auto_merge=false: merge_enabled 0", "0", cat(d / "dep/merge_enabled"))
    ck("dependabot prep: auto_merge=false: reason names the dependabot-<repo> row", "1",
       count("switched off for dependabot-jelly-swipe. Flip it with ops/fabro-auto-merge-switch.sh dependabot-jelly-swipe on", file=d / "dep/merge_off_reason"))

    # 3. The API cannot be read: off, because a switch that cannot be read is not armed.
    d = case("api-down", '"auto_merge=true"', "1", curl_fails=True)
    ck("dependabot prep: curl fails: merge_enabled 0", "0", cat(d / "dep/merge_enabled"))

    # 4. Row on, host off: off, and the reason names the host switch.
    d = case("host-off", '"auto_merge=true"', "0")
    ck("dependabot prep: host off: merge_enabled 0", "0", cat(d / "dep/merge_enabled"))
    ck("dependabot prep: host off: reason names the host switch", "1", count("host-wide", file=d / "dep/merge_off_reason"))

    # 5. The run-local reset: a leftover contract from an earlier run is removed, results.json
    #    is [] and stopped_at is cleared on success.
    r = tmp_path / "dep-reset-stale"
    (r / "dep").mkdir(parents=True)
    write(r / "dep/results.json", '[{"number":1}]')
    for f in ("alerts.json", "prs.json", "group.json", "patch.json", "cur.json", "cur_result.json"):
        write(r / "dep" / f, "stale\n")
    git("-C", r, "init", "-q")
    rows = write(f"{r}.rows.json", '{"data":[]}')
    script = write(f"{r}.sh", rebase(extract(DEP, "prep"), r))
    rc = sh(script, cwd=r, path=f"{stubs / 'bin'}:{ORIG_PATH}",
            env={"AUTOROWS": rows, "FABRO_API_URL": "http://fabro.test", "FABRO_API_TOKEN": "t", "FABRO_AUTO_MERGE": "1"}).rc
    ck("dependabot prep: reset run exits 0", "0", rc)
    ck("dependabot prep: results.json is []", "[]", jq(".", file=r / "dep/results.json", args=["-c"], raw=False))
    ck("dependabot prep: no stale contract left", "0",
       count("^(alerts|prs|group|patch|cur|cur_result)\\.json$", "\n".join(sorted(os.listdir(r / "dep"))), flags="E", pipe=True))
    ck("dependabot prep: stopped_at cleared on success", "0", exists01(r / "dep/stopped_at"))
    # A repository with no Dependabot PR goes from inventory straight to pick_group, so
    # dep_next never runs: prep must record the run branch for sync_base.
    ck("dependabot prep: records the run branch", git("-C", r, "symbolic-ref", "--short", "HEAD"), cat(r / "dep/run_branch"))


# ---------------------------------------------------------------------------
# dependabot — inventory (ADR 0022 task 03): the alert list (C1), the PR queue (C2), the
# match (C3) and the version comparator. gh is stubbed; the fixtures are live 2026-10-10
# data (jelly-swipe) plus synthetic PRs and alerts built here.
# ---------------------------------------------------------------------------
def test_dependabot_inventory(ck, tmp_path):
    stubs = tmp_path / "dep-inv-stubs"
    stub(stubs / "bin", "gh", """#!/bin/sh
case "$1 $2" in
  "api "*) [ -n "${GH_API_RC:-}" ] && exit "$GH_API_RC"; cat "$GH_ALERTS" ;;
  "pr list") cat "$GH_PRS" ;;
  *) echo "gh $*" >&2; exit 1 ;;
esac
""")

    def case(name, alerts, prs, api_rc=None):
        d = tmp_path / f"dep-inv-{name}"
        (d / "dep").mkdir(parents=True, exist_ok=True)
        write(d / "dep/repo.json", '{"nameWithOwner":"andrewthetechie/jelly-swipe","defaultBranchRef":{"name":"main"}}\n')
        env = {"GH_ALERTS": alerts, "GH_PRS": prs}
        if api_rc:
            env["GH_API_RC"] = api_rc
        run_node(ck, "inventory", DEP, d, stubs / "bin", env=env)
        return d

    def mk_pr(title, head, number, body, out):
        write(out, jq_n('[{number:$n, title:$t, headRefName:$h, headRefOid:"f00d", createdAt:"2026-10-10T06:00:00Z", url:"https://github.com/andrewthetechie/jelly-swipe/pull/\\($n)", body:$b}]',
                        "--arg", "t", title, "--arg", "h", head, "--argjson", "n", str(number), "--arg", "b", body) + "\n")
        return out

    # 1. jelly-swipe on 2026-10-10: 5 alerts, 6 PRs, none fixed, oldest PR first.
    d = case("jelly", ALERTS_JELLY, PRS_JELLY)
    alerts, prs = d / "dep/alerts.json", d / "dep/prs.json"
    ck("dependabot inventory: jelly exit 0", "0", cat(f"{d}.rc"))
    ck("dependabot inventory: jelly has 5 alerts (C1)", "5", jq("length", file=alerts, raw=False))
    ck("dependabot inventory: jelly alert shape", "urllib3", jq("[.[] | select(.number==72)][0].package", file=alerts))
    ck("dependabot inventory: jelly has 6 PRs (C2)", "6", jq("length", file=prs, raw=False))
    ck("dependabot inventory: jelly fixes are all empty", "0", jq("[.[] | select(.fixes != [])] | length", file=prs, raw=False))
    ck("dependabot inventory: jelly PRs oldest first", "437 438 439 440 441 442", jq('[.[].number] | map(tostring) | join(" ")', file=prs))
    got = routing_check(ck, "dependabot inventory: jelly routing", d, ".context_updates.alert_count")
    ck("dependabot inventory: jelly routing alert_count", "5", got)
    ck("dependabot inventory: jelly routing dep_pr_count", "6", jq(".context_updates.dep_pr_count", tail1(f"{d}.out")))
    ck("dependabot inventory: stopped_at cleared on success", "0", exists01(d / "dep/stopped_at"))
    version_dir = d / "dep"

    # 2. A security PR (urllib3 to 2.8.0) fixes alerts 72-74, and it sorts first even though it is newest.
    sec = mk_pr("chore(deps): bump urllib3 from 2.7.1 to 2.8.0", "dependabot/uv/urllib3-2.8.0", 900, "Bumps urllib3.", tmp_path / "dep-sec.json")
    allp = write(tmp_path / "dep-sec-all.json", run(["jq", "-s", "add", PRS_JELLY, sec], stderr="drop").out + "\n")
    d = case("security", ALERTS_JELLY, allp)
    ck("dependabot inventory: security PR fixes 72,73,74", "72 73 74", jq('[.[] | select(.number==900)][0].fixes | map(tostring) | join(" ")', file=d / "dep/prs.json"))
    ck("dependabot inventory: security PR sorts first", "900", jq(".[0].number", file=d / "dep/prs.json"))

    # 3. The same PR to 2.7.9 is below first_patched (2.8.0): no fix.
    low = mk_pr("chore(deps): bump urllib3 from 2.7.1 to 2.7.9", "dependabot/uv/urllib3-2.7.9", 901, "x", tmp_path / "dep-low.json")
    d = case("low", ALERTS_JELLY, low)
    ck("dependabot inventory: 2.7.9 fixes nothing", "[]", jq(".[0].fixes", file=d / "dep/prs.json", args=["-c"], raw=False))

    # 4. A root-directory brace-expansion PR does not fix the frontend/package-lock.json alert.
    fe = write(tmp_path / "dep-fe-alert.json", jq_n('[{number:1, dependency:{package:{ecosystem:"npm",name:"brace-expansion"},manifest_path:"frontend/package-lock.json"}, security_advisory:{severity:"medium"}, security_vulnerability:{first_patched_version:{identifier:"5.0.9"}}, created_at:"2026-10-01T00:00:00Z", html_url:"https://example.test/1"}]') + "\n")
    root = mk_pr("chore(deps): bump brace-expansion from 5.0.8 to 5.0.12", "dependabot/npm_and_yarn/brace-expansion-5.0.12", 902, "Bumps brace-expansion.", tmp_path / "dep-root.json")
    d = case("wrongdir", fe, root)
    ck("dependabot inventory: root PR does not fix a frontend alert", "[]", jq(".[0].fixes", file=d / "dep/prs.json", args=["-c"], raw=False))

    # 5. A grouped PR: the body's Updates row names undici at 8.11.2, which fixes an undici
    #    alert at 8.10.2.
    undici = write(tmp_path / "dep-undici.json", jq_n('[{number:7, dependency:{package:{ecosystem:"npm",name:"undici"},manifest_path:"frontend/package-lock.json"}, security_advisory:{severity:"medium"}, security_vulnerability:{first_patched_version:{identifier:"8.10.2"}}, created_at:"2026-10-01T00:00:00Z", html_url:"https://example.test/7"}]') + "\n")
    d = case("grouped", undici, DEP_FIX / "jelly-swipe-grouped-431.json")
    ck("dependabot inventory: grouped body fixes undici", "[7]", jq(".[0].fixes", file=d / "dep/prs.json", args=["-c"], raw=False))
    ck("dependabot inventory: grouped PR keeps its update list (pick_group reads it)", "true",
       jq('[.[0].updates[] | select(.package == "undici" and .eco == "npm")] | length > 0', file=d / "dep/prs.json", raw=False))

    # 6. Paginated: gh prints one array per page, and both pages are read.
    two = write(tmp_path / "dep-two-pages.json", ALERTS_JELLY.read_text() * 2)
    d = case("paged", two, PRS_JELLY)
    ck("dependabot inventory: two pages give 10 alerts", "10", jq("length", file=d / "dep/alerts.json", raw=False))

    # 7. The alert read fails: exit 1, and stopped_at names inventory.
    d = case("apifail", ALERTS_JELLY, PRS_JELLY, api_rc="1")
    ck("dependabot inventory: API failure exits 1", "1", cat(f"{d}.rc"))
    ck("dependabot inventory: API failure leaves stopped_at at inventory", "inventory", cat(d / "dep/stopped_at"))

    # 8. The comparator (task 03's fixtures), loaded from the module the node wrote.
    cmp_dir = tmp_path / "dep-cmp"
    cmp_dir.mkdir()
    write(cmp_dir / "version.jq", (version_dir / "version.jq").read_text())
    for expr, want in (('vcmp("2.10.0";"2.9.0") > 0 | if . then 1 else 0 end', "1"),
                       ('vcmp("1.0.0-rc1";"1.0.0") < 0 | if . then 1 else 0 end', "1"),
                       ('vcmp("0.23.45";"0.23.45")', "0"),
                       ('vcmp("5.0.12";"5.0.9")', "1"),
                       ('vcmp("v0.16.10";"v0.16.9")', "1"),
                       ('vcmp("2.8.0rc1";"2.8.0")', "-1")):
        ck(f"dependabot comparator: {expr}", want, jq(f'include "version"; {expr}', args=["-n", "-L", str(cmp_dir)]))


# ---------------------------------------------------------------------------
# dependabot — the merge loop (ADR 0022 task 04): dep_next, dep_checks, dep_validate,
# dep_merge. Real git: a bare origin with main and refs/pull/7/head, and a run branch in a
# clone. gh is stubbed and logs every call. The inner timeouts and polls are lowered through
# DEP_* variables.
# ---------------------------------------------------------------------------
LOOP_GH = """#!/bin/sh
echo "gh $*" >> "$GH_LOG"
case "$1 $2" in
  "pr view") printf '{"state":"%s","headRefOid":"%s","mergeable":"%s","comments":%s}\\n' "${GH_STATE:-OPEN}" "${GH_HEAD:-$GH_SHA}" "${GH_MERGEABLE:-MERGEABLE}" "${GH_COMMENTS:-[]}" ;;
  "pr checks") printf '%s\\n' "${GH_CHECKS_JSON:-[]}" ;;
  "pr merge")
    n=$(cat "$GH_LOG.merges" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$GH_LOG.merges"
    if [ -n "${GH_MERGE_FAIL_ONCE:-}" ] && [ "$n" -eq 1 ]; then echo "Base branch was modified. Review and try the merge again." >&2; exit 1; fi
    case " $* " in *" --admin "*) ;; *) if [ -n "${GH_MERGE_BEHIND:-}" ]; then
      echo "X Pull request o/r#7 is not mergeable: the head branch is not up to date with the base branch." >&2
      echo "To use administrator privileges to immediately merge the pull request, add the \\`--admin\\` flag." >&2; exit 1; fi ;; esac
    echo merged ;;
  "api user") echo '{"login":"fabro-bot"}' ;;
  *) : ;;
esac
exit 0
"""


def loop_env(ck, tmp_path):
    """The merge loop's stubs and the per-case repository builders."""
    stubs = tmp_path / "dep-loop-stubs/bin"
    stub(stubs, "gh", LOOP_GH)

    def loop_repo(name, mode):
        """mode: clean | red | slow | conflict. The PR (pull/7/head) changes feature.txt, or
        .fabro/ci.sh for red and slow, or README for conflict."""
        L = tmp_path / f"loop-{name}"
        L.mkdir(parents=True)
        w = L / "work"
        git("init", "-q", "--bare", L / "origin.git")
        git("clone", "-q", L / "origin.git", w)
        git("-C", w, "config", "user.email", "t@example.test"); git("-C", w, "config", "user.name", "t")
        git("-C", w, "checkout", "-q", "-b", "main")
        stub(w / ".fabro", "setup.sh", "exit 0\n"); stub(w / ".fabro", "ci.sh", "exit 0\n")
        write(w / "README", "base\n")
        git("-C", w, "add", "-A"); git("-C", w, "commit", "-qm", "base"); git("-C", w, "push", "-q", "origin", "main")
        git("-C", w, "checkout", "-q", "-b", "pr7")
        if mode == "red":
            stub(w / ".fabro", "ci.sh", "echo the-check-failed\necho x > untracked.txt\nexit 3\n")
        elif mode == "slow":
            stub(w / ".fabro", "ci.sh", "sleep 30\n")
        write(w / "feature.txt", "feat\n")
        if mode == "conflict":
            write(w / "README", "pr-line\n")
        git("-C", w, "add", "-A"); git("-C", w, "commit", "-qm", "pr")
        sha = git("-C", w, "rev-parse", "HEAD")
        write(L / "pr_sha", sha + "\n")
        git("-C", w, "push", "-q", "origin", f"{sha}:refs/pull/7/head")
        git("-C", w, "checkout", "-q", "main")
        if mode == "conflict":
            write(w / "README", "main-line\n"); git("-C", w, "commit", "-qam", "main-moved"); git("-C", w, "push", "-q", "origin", "main")
        git("-C", w, "checkout", "-q", "-b", "run")
        (L / "fab/dep").mkdir(parents=True)
        write(L / "fab/base_ref", "main\n"); write(L / "fab/dep/results.json", "[]\n"); write(L / "fab/dep/merge_enabled", "1\n")
        write(L / "gh.log", "")
        # The queue: one PR, as dep_next reads it.
        write(L / "fab/dep/prs.json", jq_n('[{number: 7, title: "t", head_sha: $s, created_at: "2026-10-01T00:00:00Z", fixes: []}]', "--arg", "s", sha) + "\n")
        return L

    def loop_run(node, name, **env):
        """Runs the node in the clone with /tmp/fabro rebased onto the repo's fab dir. Leaves
        L/node.out, .err and .rc."""
        L = tmp_path / f"loop-{name}"
        script = write(L / f"{node}.sh", rebase(extract(DEP, node), L / "fab"))
        ok, err = syntax_ok(script)
        if not ok:
            ck.fail(f"{node} is not valid POSIX sh", err)
        run_files(["sh", script], L / node, cwd=L / "work", path=f"{stubs}:{ORIG_PATH}",
                  env={"GH_LOG": L / "gh.log", "GH_SHA": cat(L / "pr_sha"), **env})

    lroute = lambda name, node, filt: route(tmp_path / f"loop-{name}" / node, filt)
    results = lambda name: tmp_path / f"loop-{name}/fab/dep/results.json"
    ghlog = lambda name: tmp_path / f"loop-{name}/gh.log"

    def loop_clean(name):
        w = tmp_path / f"loop-{name}/work"
        return "1" if git("-C", w, "symbolic-ref", "--short", "HEAD") == "run" and git("-C", w, "status", "--porcelain") == "" else "0"

    def cur(name):
        L = tmp_path / f"loop-{name}"
        write(L / "fab/dep/cur.json", jq_n("{number: 7, head_sha: $s, fixes: []}", "--arg", "s", cat(L / "pr_sha")) + "\n")

    green = {"GH_CHECKS_JSON": '[{"name":"test","bucket":"pass"}]', "DEP_POLL": "0"}

    return SimpleNamespace(**locals())


def test_dependabot_merge_loop_caps_and_green(ck, tmp_path):
    """Cases 1-4: the caps, a dirty checkout, and a green PR merged once."""
    E = loop_env(ck, tmp_path)
    loop_repo, loop_run, lroute, results, ghlog, loop_clean, green = (E.loop_repo, E.loop_run, E.lroute, E.results, E.ghlog, E.loop_clean, E.green)

    # 1. The cap: seven PRs, six visits. The sixth publishes no and appends the two left as skipped_cap.
    loop_repo("cap", "clean")
    write(tmp_path / "loop-cap/fab/dep/prs.json", jq_n('[range(7) as $i | {number: (100 + $i), title: "t", head_sha: "x", created_at: "2026-10-01T00:00:00Z", fixes: []}]') + "\n")
    for _ in range(6):
        loop_run("dep_next", "cap")
    ck("dep_next cap: sixth visit publishes no", "no", lroute("cap", "dep_next", ".context_updates.dep_pr_next"))
    ck("dep_next cap: two skipped_cap entries", "2", jq('[.[] | select(.outcome == "skipped_cap")] | length', file=results("cap"), raw=False))
    ck("dep_next cap: the skipped PRs are the last two", "105 106", jq('[.[] | select(.outcome == "skipped_cap") | .pr] | map(tostring) | join(" ")', file=results("cap")))

    # 2. The time cap: the loop started 5401 seconds ago, so nothing is picked and both PRs are skipped.
    L = loop_repo("time", "clean")
    write(L / "fab/dep/prs.json", jq_n('[range(2) as $i | {number: (200 + $i), title: "t", head_sha: "x", created_at: "2026-10-01T00:00:00Z", fixes: []}]') + "\n")
    write(L / "fab/dep/loop_started", f"{int(time.time()) - 5401}\n"); write(L / "fab/dep/cursor", "0\n")
    write(L / "fab/dep/run_branch", "run\n")
    loop_run("dep_next", "time")
    ck("dep_next time cap: publishes no", "no", lroute("time", "dep_next", ".context_updates.dep_pr_next"))
    ck("dep_next time cap: both PRs skipped", "2", jq('[.[] | select(.outcome == "skipped_cap")] | length', file=results("time"), raw=False))

    # 3. A dirty checkout: dep_next exits 1 and publishes no.
    L = loop_repo("dirty", "clean")
    loop_run("dep_next", "dirty")
    write(L / "work/stray.txt", "stray\n")
    loop_run("dep_next", "dirty")
    ck("dep_next dirty: exits 1", "1", cat(L / "dep_next.rc"))
    ck("dep_next dirty: publishes no", "no", lroute("dirty", "dep_next", ".context_updates.dep_pr_next"))

    # 4. A clean merge with green checks: the validation passes, the tree is the run branch and
    #    clean, and the PR's file is not in the working tree. Then dep_merge merges it once.
    L = loop_repo("green", "clean")
    loop_run("dep_next", "green")
    ck("dep_next green: publishes yes", "yes", lroute("green", "dep_next", ".context_updates.dep_pr_next"))
    loop_run("dep_checks", "green", **green)
    ck("dep_checks green: publishes green", "green", lroute("green", "dep_checks", ".context_updates.dep_checks"))
    loop_run("dep_validate", "green")
    ck("dep_validate green: publishes pass", "pass", lroute("green", "dep_validate", ".context_updates.dep_valid"))
    ck("dep_validate green: HEAD is the run branch", "1", loop_clean("green"))
    ck("dep_validate green: the PR's file is not in the tree", "0", exists01(L / "work/feature.txt"))
    ck("dep_validate green: no merge state left", "0", exists01(L / "work/.git/MERGE_HEAD"))
    loop_run("dep_merge", "green")
    ck("dep_merge green: records merged", "merged", jq(".[-1].outcome", file=results("green")))
    ck("dep_merge green: one merge call", "1", count("^gh pr merge", file=ghlog("green")))

    # (case 11b's companion: dep_validate records the base it validated)
    ck("dep_validate green: records the validated base", git("-C", tmp_path / "loop-green/work", "rev-parse", "origin/main"),
       cat(tmp_path / "loop-green/fab/dep/validated_base"))



def test_dependabot_merge_loop_validation(ck, tmp_path):
    """Cases 5-8: red CI, a conflict, the inner timeout, and the switch off."""
    E = loop_env(ck, tmp_path)
    loop_repo, loop_run, lroute, results, ghlog, loop_clean, cur, green = (E.loop_repo, E.loop_run, E.lroute, E.results, E.ghlog, E.loop_clean, E.cur, E.green)

    # 5. Red CI: the log's last line is the reason, the untracked file ci.sh left is gone,
    #    and the tree is restored.
    L = loop_repo("redci", "red")
    loop_run("dep_next", "redci")
    loop_run("dep_checks", "redci", **green)
    loop_run("dep_validate", "redci")
    ck("dep_validate red: publishes fail", "fail", lroute("redci", "dep_validate", ".context_updates.dep_valid"))
    ck("dep_validate red: reason is the log's last line", "the-check-failed", jq(".reason", file=L / "fab/dep/cur_result.json"))
    ck("dep_validate red: the untracked file is gone", "0", exists01(L / "work/untracked.txt"))
    ck("dep_validate red: HEAD is the run branch and clean", "1", loop_clean("redci"))

    # 6. A conflict: the merge stops, nothing is validated, and no merge state is left behind.
    L = loop_repo("conf", "conflict")
    loop_run("dep_next", "conf")
    loop_run("dep_checks", "conf", **green)
    loop_run("dep_validate", "conf")
    ck("dep_validate conflict: publishes conflict", "conflict", lroute("conf", "dep_validate", ".context_updates.dep_valid"))
    ck("dep_validate conflict: no MERGE_HEAD", "0", exists01(L / "work/.git/MERGE_HEAD"))
    ck("dep_validate conflict: HEAD is the run branch and clean", "1", loop_clean("conf"))

    # 7. The inner timeout: ci.sh sleeps past a lowered bound. RC 124, the reason says
    #    timeout, and the tree is restored.
    L = loop_repo("slow", "slow")
    loop_run("dep_next", "slow")
    loop_run("dep_validate", "slow", DEP_VALIDATE_TIMEOUT="2")
    ck("dep_validate timeout: publishes fail", "fail", lroute("slow", "dep_validate", ".context_updates.dep_valid"))
    ck("dep_validate timeout: reason says timeout", "1", count("timed out", file=L / "fab/dep/cur_result.json"))
    ck("dep_validate timeout: HEAD is the run branch and clean", "1", loop_clean("slow"))

    # 8. dep_merge with the switch off: a switch_off entry, and no merge call.
    L = loop_repo("off", "clean")
    write(L / "fab/dep/merge_enabled", "0\n"); write(L / "fab/dep/merge_off_reason", "Auto-merge is switched off for this repository.\n")
    cur("off")
    loop_run("dep_merge", "off")
    ck("dep_merge switch off: records switch_off", "switch_off", jq(".[-1].outcome", file=results("off")))
    ck("dep_merge switch off: no merge call", "0", count("^gh pr merge", file=ghlog("off")))



def test_dependabot_merge_loop_merge_and_checks(ck, tmp_path):
    """Cases 9-12: a moved head, the merge retry, rebase comments, branch protection, and dep_checks."""
    E = loop_env(ck, tmp_path)
    loop_repo, loop_run, lroute, results, ghlog, cur = (E.loop_repo, E.loop_run, E.lroute, E.results, E.ghlog, E.cur)

    # 9. dep_merge with the head moved: stale, and no merge call.
    loop_repo("stale", "clean"); cur("stale")
    loop_run("dep_merge", "stale", GH_HEAD="deadbeef")
    ck("dep_merge stale: records stale", "stale", jq(".[-1].outcome", file=results("stale")))
    ck("dep_merge stale: no merge call", "0", count("^gh pr merge", file=ghlog("stale")))

    # 10. dep_merge retry: the first merge is rejected with "Base branch was modified", the
    #     second succeeds.
    loop_repo("retry", "clean"); cur("retry")
    loop_run("dep_merge", "retry", DEP_RETRY_WAIT="0", DEP_MERGE_WAIT="0", GH_MERGE_FAIL_ONCE="1")
    ck("dep_merge retry: records merged", "merged", jq(".[-1].outcome", file=results("retry")))
    ck("dep_merge retry: two merge calls", "2", count("^gh pr merge", file=ghlog("retry")))

    # 11. A conflict comments @dependabot rebase once. A recent comment from this token
    #     suppresses a second.
    L = loop_repo("rebase", "clean")
    write(L / "fab/dep/cur_result.json", '{"outcome":"conflict","reason":""}\n'); cur("rebase")
    loop_run("dep_merge", "rebase")
    ck("dep_merge conflict with no comment: one rebase comment", "1", count("^gh pr comment", file=ghlog("rebase")))
    # Raw output: the shell suite used `jq -n` without -r, so its timestamp kept its JSON quotes,
    # the stub's comment list was not valid JSON, and dep_merge posted nothing whatever the
    # comment's age -- the check passed vacuously. A valid two-hour-old comment is what it means.
    recent = jq('(now - 7200) | strftime("%Y-%m-%dT%H:%M:%SZ")', args=["-n"])
    L = loop_repo("rebase2", "clean")
    write(L / "fab/dep/cur_result.json", '{"outcome":"conflict","reason":""}\n'); cur("rebase2")
    loop_run("dep_merge", "rebase2", GH_COMMENTS=f'[{{"author":{{"login":"fabro-bot"}},"body":"@dependabot rebase","createdAt":"{recent}"}}]')
    ck("dep_merge conflict with a recent comment: no new comment", "0", count("^gh pr comment", file=ghlog("rebase2")))

    # 11b. Branch protection that requires an up-to-date branch (jelly-swipe): the plain merge
    #      is refused as behind. With the base tip still the validated commit, it retries once
    #      with --admin and records merged. With the base moved since the validation, it
    #      records merge_failed and never uses --admin.
    behind = {"DEP_MERGE_WAIT": "0", "GH_MERGE_BEHIND": "1"}
    L = loop_repo("behind", "clean"); cur("behind")
    write(L / "fab/dep/validated_base", git("-C", L / "work", "rev-parse", "origin/main") + "\n")
    loop_run("dep_merge", "behind", **behind)
    merges = [l for l in grep("^gh pr merge", file=ghlog("behind")).split("\n") if l]
    ck("dep_merge behind, base unchanged: records merged", "merged", jq(".[-1].outcome", file=results("behind")))
    ck("dep_merge behind, base unchanged: two merge calls", "2", count("^gh pr merge", file=ghlog("behind")))
    ck("dep_merge behind, base unchanged: the second uses --admin and the head match", "1",
       count("--match-head-commit .* --admin", merges[-1] if merges else "", pipe=True))
    ck("dep_merge behind, base unchanged: the reason says --admin", "1", count("merged with --admin", jq(".[-1].reason", file=results("behind")), pipe=True))
    L = loop_repo("behind2", "clean"); cur("behind2")
    write(L / "fab/dep/validated_base", "0" * 40 + "\n")
    loop_run("dep_merge", "behind2", **behind)
    ck("dep_merge behind, base moved: records merge_failed", "merge_failed", jq(".[-1].outcome", file=results("behind2")))
    ck("dep_merge behind, base moved: never uses --admin", "0", count("--admin", file=ghlog("behind2")))
    ck("dep_merge behind, base moved: the reason says the base moved", "1", count("moved after the validation", jq(".[-1].reason", file=results("behind2")), pipe=True))

    # 12. dep_checks: stale, red, no checks and pending.
    L = loop_repo("chk", "clean"); cur("chk")
    loop_run("dep_checks", "chk", GH_HEAD="deadbeef")
    ck("dep_checks stale: publishes stale", "stale", lroute("chk", "dep_checks", ".context_updates.dep_checks"))
    loop_run("dep_checks", "chk", DEP_POLL="0", GH_CHECKS_JSON='[{"name":"lint","bucket":"fail"}]')
    ck("dep_checks red: publishes red", "red", lroute("chk", "dep_checks", ".context_updates.dep_checks"))
    ck("dep_checks red: the reason names the check", "1", count("GitHub check lint failed", file=L / "fab/dep/cur_result.json"))
    loop_run("dep_checks", "chk", DEP_POLL="0", DEP_NO_CHECKS_GRACE="0", GH_CHECKS_JSON="[]")
    ck("dep_checks no checks: outcome no_checks", "no_checks", jq(".outcome", file=L / "fab/dep/cur_result.json"))
    loop_run("dep_checks", "chk", DEP_POLL="0", DEP_CHECK_BOUND="0", GH_CHECKS_JSON='[{"name":"t","bucket":"pending"}]')
    ck("dep_checks pending: outcome pending", "pending", jq(".outcome", file=L / "fab/dep/cur_result.json"))




# ---------------------------------------------------------------------------
# dependabot — pick_group, track_issue, sync_base (ADR 0022 task 05). gh is stubbed and logs;
# the alerts are the live jelly-swipe C1 projection; the issue lists come from the test.
# ---------------------------------------------------------------------------
def test_dependabot_pick_group_track_issue_sync_base(ck, tmp_path):
    stubs = tmp_path / "pt-stubs/bin"
    stub(stubs, "gh", """#!/bin/sh
echo "gh $*" >> "$GH_LOG"
case "$1 $2" in
  "issue list") printf '%s\\n' "${GH_ISSUES_JSON:-[]}" ;;
  "pr list") printf '%s\\n' "${GH_OPEN_PRS:-[]}" ;;
  "issue create") echo "https://github.com/andrewthetechie/jelly-swipe/issues/412" ;;
  "issue view") case "$*" in *--jq*) echo "${GH_ISSUE_STATE:-OPEN}" ;; *) echo '{}' ;; esac ;;
  *) : ;;
esac
exit 0
""")
    vjq = version_jq()
    st = {}

    def seed(name, results_json, issues_json):
        PT = tmp_path / f"pt-{name}"
        (PT / "dep").mkdir(parents=True, exist_ok=True)
        write(PT / "gh.log", "")
        write(PT / "dep/alerts.json", (DEP_FIX / "jelly-swipe-c1-open.json").read_text())
        write(PT / "dep/alerts_raw.json", ALERTS_JELLY.read_text())
        write(PT / "dep/prs.json", jq("map(. + {fixes: []})", file=PRS_JELLY, raw=False) + "\n")
        write(PT / "dep/repo.json", '{"nameWithOwner":"andrewthetechie/jelly-swipe"}\n')
        write(PT / "dep/results.json", results_json)
        write(PT / "base_ref", "main")
        write(PT / "dep/version.jq", vjq)
        st.update(pt=PT, issues=issues_json)
        return PT

    def pt_run(node, **env):
        PT = st["pt"]
        script = write(PT / f"{node}.sh", rebase(extract(DEP, node), PT))
        ok, err = syntax_ok(script)
        if not ok:
            ck.fail(f"{node} is not valid POSIX sh", err)
        run_files(["sh", script], PT / node, cwd=PT, path=f"{stubs}:{ORIG_PATH}",
                  env={"GH_LOG": PT / "gh.log", "GH_ISSUES_JSON": st["issues"], **env})

    def inplace(PT, f, filt):
        write(PT / f, jq(filt, file=PT / f, raw=False) + "\n")

    patch_group = lambda: route(st["pt"] / "pick_group", ".context_updates.patch_group")
    group = lambda filt: jq(filt, file=st["pt"] / "dep/group.json")
    none = "[]"

    PT = seed("rank", "[]", none); pt_run("pick_group")
    ck("pick_group ranking: exit 0", "0", cat(PT / "pick_group.rc"))
    ck("pick_group ranking: urllib3 first", "pip:uv.lock:urllib3", patch_group())
    ck("pick_group ranking: group alerts 72,73,74", "72 73 74", group('.alerts | map(tostring) | join(" ")'))
    ck("pick_group ranking: first_patched 2.8.0", "2.8.0", group(".first_patched"))
    ck("pick_group ranking: no tracking issue yet (issue 0)", "0", group(".issue"))

    seed("merged", '[{"pr":900,"outcome":"merged","reason":"","fixes":[72,73,74]}]', none); pt_run("pick_group")
    ck("pick_group merged this run: brace-expansion picked", "npm:frontend/package-lock.json:brace-expansion", patch_group())

    PT = seed("nopatch", "[]", none); inplace(PT, "dep/alerts.json", "map(.first_patched = null)"); pt_run("pick_group")
    ck("pick_group no patched version: patch_group none", "none", patch_group())
    ck("pick_group no patched version: no_patch.json holds all five", "5", jq("length", file=PT / "dep/no_patch.json", raw=False))

    u_nh = '[{"number":5,"state":"OPEN","stateReason":"","closedAt":null,"labels":[{"name":"needs-human"}],"body":"<!-- fabro:dep-group key=pip:uv.lock:urllib3 -->"}]'
    PT = seed("needshuman", "[]", u_nh); pt_run("pick_group")
    ck("pick_group needs-human: brace-expansion picked", "npm:frontend/package-lock.json:brace-expansion", patch_group())
    ck("pick_group needs-human: skipped_groups names urllib3", "1", jq('[.[] | select(.key == "pip:uv.lock:urllib3")] | length', file=PT / "dep/skipped_groups.json", raw=False))

    u_rev = '[{"number":6,"state":"OPEN","stateReason":"","closedAt":null,"labels":[{"name":"Review"}],"body":"<!-- fabro:dep-group key=pip:uv.lock:urllib3 -->"}]'
    PT = seed("stalerev", "[]", u_rev); pt_run("pick_group", GH_OPEN_PRS="[]")
    ck("pick_group stale Review: urllib3 picked", "pip:uv.lock:urllib3", patch_group())
    ck("pick_group stale Review: Review removed", "1", count("issue edit 6 --remove-label Review", file=PT / "gh.log"))

    u_recent = jq_n('[{number: 7, state: "CLOSED", stateReason: "COMPLETED", closedAt: ((now - 86400) | strftime("%Y-%m-%dT%H:%M:%SZ")), labels: [], body: "<!-- fabro:dep-group key=pip:uv.lock:urllib3 -->"}]')
    seed("window1", "[]", u_recent); pt_run("pick_group")
    ck("pick_group D22 inside 3 days: urllib3 skipped", "npm:frontend/package-lock.json:brace-expansion", patch_group())
    u_old = jq_n('[{number: 7, state: "CLOSED", stateReason: "COMPLETED", closedAt: ((now - 345600) | strftime("%Y-%m-%dT%H:%M:%SZ")), labels: [], body: "<!-- fabro:dep-group key=pip:uv.lock:urllib3 -->"}]')
    PT = seed("window4", "[]", u_old); pt_run("pick_group")
    ck("pick_group D22 past 3 days: issue reopened", "1", count("issue reopen 7", file=PT / "gh.log"))
    ck("pick_group D22 past 3 days: needs-human added and commented", "2", count("issue (edit 7 --add-label needs-human|comment 7)", file=PT / "gh.log", flags="E"))

    # A merged Dependabot PR that updated brace-expansion in frontend/ but matched no alert
    # (fixes []) still skips that group for the day; urllib3, which it did not touch, is
    # unaffected.
    PT = seed("touched", '[{"pr":900,"outcome":"merged","reason":"","fixes":[]},{"pr":901,"outcome":"red","reason":"x","fixes":[]}]', none)
    inplace(PT, "dep/prs.json", '. + [{number: 900, fixes: [], updates: [{package: "Brace-Expansion", eco: "npm", dir: "frontend"}]}, {number: 901, fixes: [], updates: [{package: "urllib3", eco: "pip", dir: ""}]}]')
    inplace(PT, "dep/alerts.json", '[.[] | select(.package == "brace-expansion")]')
    pt_run("pick_group")
    ck("pick_group touched by a merged PR: patch_group none", "none", patch_group())
    ck("pick_group touched by a merged PR: the skip says why", "1",
       jq('[.[] | select(.key == "npm:frontend/package-lock.json:brace-expansion" and (.reason | test("merged in this run")))] | length', file=PT / "dep/skipped_groups.json", raw=False))
    PT = seed("untouched", '[{"pr":901,"outcome":"red","reason":"x","fixes":[]}]', none)
    inplace(PT, "dep/prs.json", '. + [{number: 901, fixes: [], updates: [{package: "brace-expansion", eco: "npm", dir: "frontend"}]}]')
    inplace(PT, "dep/alerts.json", '[.[] | select(.package == "brace-expansion")]')
    pt_run("pick_group")
    ck("pick_group, a red PR updated it: still picked", "npm:frontend/package-lock.json:brace-expansion", patch_group())
    ck("pick_group: group.json has no touched field", "null", group(".touched"))

    PT = seed("replaces", '[{"pr":440,"outcome":"red","reason":"x","fixes":[72]}]', none)
    inplace(PT, "dep/prs.json", "map(if .number == 440 then .fixes = [72] else . end)")
    pt_run("pick_group")
    ck("pick_group replaces: a red PR that fixes 72 is replaced", "[440]", group(".replaces | tostring"))
    seed("heldoff", '[{"pr":440,"outcome":"switch_off","reason":"x","fixes":[72]}]', none); pt_run("pick_group")
    ck("pick_group switch_off: the urllib3 group is dropped", "npm:frontend/package-lock.json:brace-expansion", patch_group())

    # track_issue: the tracking issue is filed with the marker first, one row per alert, and the labels.
    PT = seed("track", "[]", none); pt_run("pick_group")
    pt_run("track_issue")
    ck("track_issue: exit 0", "0", cat(PT / "track_issue.rc"))
    ck("track_issue: body's first line is the exact marker", "<!-- fabro:dep-group key=pip:uv.lock:urllib3 -->", head1(PT / "dep/issue_body.md"))
    ck("track_issue: one table row per alert", "3", count("^| \\[#", file=PT / "dep/issue_body.md"))
    ck("track_issue: the group records the new issue", "412", group(".issue"))
    ck("track_issue: filed with the three labels", "1", count("issue create.*--label security --label dependencies --label ai-generated", file=PT / "gh.log"))
    ck("track_issue: title is the C6 title", "1", count("issue create --title fix(deps): patch urllib3 security alerts in uv.lock --body-file", file=PT / "gh.log", flags="F"))
    ck("track_issue: group_alerts.json has the three alerts", "3", jq("length", file=PT / "dep/group_alerts.json", raw=False))

    # sync_base: a run branch with two empty checkpoints, and a base that moved by one
    # commit. The merge brings the new file in, and the tree is clean on the run branch.
    SY = tmp_path / "sync"
    SY.mkdir()
    w = SY / "work"
    git("init", "-q", "--bare", SY / "origin.git"); git("clone", "-q", SY / "origin.git", w)
    git("-C", w, "config", "user.email", "t@example.test"); git("-C", w, "config", "user.name", "t")
    git("-C", w, "checkout", "-q", "-b", "main"); write(w / "README", "base\n")
    git("-C", w, "add", "-A"); git("-C", w, "commit", "-qm", "base"); git("-C", w, "push", "-q", "origin", "main")
    git("-C", w, "checkout", "-q", "-b", "run")
    git("-C", w, "commit", "-q", "--allow-empty", "-m", "cp1"); git("-C", w, "commit", "-q", "--allow-empty", "-m", "cp2")
    git("-C", w, "checkout", "-q", "main"); write(w / "new.txt", "new\n")
    git("-C", w, "add", "-A"); git("-C", w, "commit", "-qm", "moved"); git("-C", w, "push", "-q", "origin", "main")
    git("-C", w, "checkout", "-q", "run")
    (SY / "fab/dep").mkdir(parents=True)
    write(SY / "fab/base_ref", "main"); write(SY / "fab/dep/run_branch", "run")
    script = write(SY / "sync.sh", rebase(extract(DEP, "sync_base"), SY / "fab"))
    run_files(["sh", script], SY / "sync", cwd=w)
    ck("sync_base: exit 0", "0", cat(SY / "sync.rc"))
    ck("sync_base: HEAD now contains the new commit", "1", exists01(w / "new.txt"))
    ck("sync_base: the tree is clean", "", git("-C", w, "status", "--porcelain"))
    ck("sync_base: sync_base_sha recorded", git("-C", w, "rev-parse", "HEAD"), cat(SY / "fab/dep/sync_base_sha"))


# ---------------------------------------------------------------------------
# dependabot — the patch path (ADR 0022 task 06): patch_prep, patch_gate, patch_validate,
# patch_blocked. A scratch repository stands for the checkout: sync_base_sha is the base
# commit, and the patch commit (uv.lock bumped) is HEAD. The receipt is $PB/.io/stage.json
# (visit v1).
# ---------------------------------------------------------------------------
def test_dependabot_patch_path(ck, tmp_path):
    pb_stubs = tmp_path / "pb-stubs/bin"
    stub(pb_stubs, "gh", "#!/bin/sh\nexit 1\n")
    vjq = version_jq()
    st = {}

    def pg_repo(name, kind):
        """kind: "lock" (uv.lock bumped), "lock-and-yarn" (also adds yarn.lock), "none"
        (HEAD = sync_base)."""
        PB = tmp_path / f"pg-{name}"
        for d in ("dep", ".io", ".fabro"):
            (PB / d).mkdir(parents=True, exist_ok=True)
        git("-C", PB, "init", "-q"); git("-C", PB, "config", "user.email", "t@example.test"); git("-C", PB, "config", "user.name", "t")
        stub(PB / ".fabro", "setup.sh", "exit 0\n"); stub(PB / ".fabro", "ci.sh", "exit 0\n")
        write(PB / ".gitignore", "dep/\n.io/\n")
        write(PB / "uv.lock", "urllib3 2.7.1\n"); git("-C", PB, "add", "-A"); git("-C", PB, "commit", "-qm", "base")
        write(PB / "dep/sync_base_sha", git("-C", PB, "rev-parse", "HEAD") + "\n")
        if kind != "none":
            write(PB / "uv.lock", "urllib3 2.8.0\n")
        if kind == "lock-and-yarn":
            write(PB / "yarn.lock", "yarn\n")
        git("-C", PB, "add", "-A"); git("-C", PB, "commit", "-qm", "patch", "--allow-empty")
        write(PB / ".io/stage.json", '{"visit":"v1"}')
        write(PB / "dep/group.json", '{"number":412,"issue":412,"package":"urllib3","manifest":"uv.lock","first_patched":"2.8.0","alerts":[72,73,74],"replaces":[]}\n')
        write(PB / "dep/patch_visits", "1\n")
        write(PB / "dep/version.jq", vjq)
        st["pb"] = PB
        return PB

    def contract(status, to, blocker, changes, visit="v1"):
        write(st["pb"] / "dep/patch.json", jq_n('{status: $st, summary: "s", changes: $ch, verification: [], major: false, blocker: $b, _io: {visit: $v}}',
                                                "--arg", "st", status, "--arg", "to", to, "--arg", "b", blocker,
                                                "--argjson", "ch", changes, "--arg", "v", visit) + "\n")

    change = lambda to: jq_n('[{package: "urllib3", from: "2.7.1", to: $to, manifest: "uv.lock", direct: false, parent: ""}]', "--arg", "to", to)

    def pg_run(node, path_first=ORIG_PATH):
        PB = st["pb"]
        script = write(PB / f"{node}.sh", rebase(extract(DEP, node), PB))
        run_files(["sh", script], PB / node, cwd=PB, path=f"{path_first}:{ORIG_PATH}")

    out = lambda node, key: route(st["pb"] / node, f".context_updates.{key}")

    # 1. A valid patch: the contract is patched, the receipt matches, the diff changes the lock.
    PB = pg_repo("valid", "lock"); contract("patched", "2.8.0", "", change("2.8.0")); pg_run("patch_gate")
    ck("patch_gate valid: exit 0", "0", cat(PB / "patch_gate.rc"))
    ck("patch_gate valid: patch_status patched", "patched", out("patch_gate", "patch_status"))

    # 2. No receipt: exit 1 with the one fixed line, and the gate leaves the visit count alone.
    PB = pg_repo("noreceipt", "lock"); contract("patched", "2.8.0", "", change("2.8.0"), "v0"); pg_run("patch_gate")
    ck("patch_gate no receipt: exit 1", "1", cat(PB / "patch_gate.rc"))
    ck("patch_gate no receipt: one fixed line on stderr", "1", count("patch.json is missing or invalid", file=PB / "patch_gate.err"))
    ck("patch_gate no receipt: patch_visits unchanged", "1", cat(PB / "dep/patch_visits"))

    # 3. A version below first_patched: exit 1 on the first visit, and failed at the third.
    PB = pg_repo("low", "lock"); contract("patched", "2.7.9", "", change("2.7.9")); pg_run("patch_gate")
    ck("patch_gate too low, visit 1: exit 1", "1", cat(PB / "patch_gate.rc"))
    write(PB / "dep/patch_visits", "3\n"); pg_run("patch_gate")
    ck("patch_gate too low, visit 3: publishes failed", "failed", out("patch_gate", "patch_status"))

    # 4. No change: a patched contract with HEAD equal to sync_base is invalid.
    PB = pg_repo("nochange", "none"); contract("patched", "2.8.0", "", change("2.8.0")); pg_run("patch_gate")
    ck("patch_gate no change: exit 1", "1", cat(PB / "patch_gate.rc"))

    # 5. A second lockfile added: invalid.
    PB = pg_repo("twolock", "lock-and-yarn"); contract("patched", "2.8.0", "", change("2.8.0")); pg_run("patch_gate")
    ck("patch_gate added yarn.lock: exit 1", "1", cat(PB / "patch_gate.rc"))

    # 6. Blocked with a blocker and no changes: patch_status blocked.
    pg_repo("blocked", "lock"); contract("blocked", "", "a major migration is needed", "[]"); pg_run("patch_gate")
    ck("patch_gate blocked: patch_status blocked", "blocked", out("patch_gate", "patch_status"))

    # patch_validate: a red run on the first visit goes back with the log tail; the third
    # visit does not.
    PB = pg_repo("red", "lock"); stub(PB / ".fabro", "ci.sh", "echo ci-broke\nexit 1\n")
    pg_run("patch_validate")
    ck("patch_validate red, visit 1: patch_ci red", "red", out("patch_validate", "patch_ci"))
    ck("patch_validate red, visit 1: patch_retry yes", "yes", out("patch_validate", "patch_retry"))
    ck("patch_validate red, visit 1: feedback has the log tail", "1", count("ci-broke", file=PB / "dep/patch_feedback.md"))
    write(PB / "dep/patch_visits", "3\n"); pg_run("patch_validate")
    ck("patch_validate red, visit 3: patch_retry no", "no", out("patch_validate", "patch_retry"))
    ck("patch_validate red, visit 3: patch_status failed", "failed", cat(PB / "dep/patch_status"))
    pg_repo("green", "lock"); pg_run("patch_validate")
    ck("patch_validate green: patch_ci green", "green", out("patch_validate", "patch_ci"))

    # patch_prep: the first visit clears the feedback and starts the count; each visit clears patch.json.
    PB = pg_repo("prep", "lock"); rm(PB / "dep/patch_visits")
    write(PB / "dep/patch_feedback.md", "stale\n"); write(PB / "dep/patch.json", "stale\n")
    pg_run("patch_prep")
    ck("patch_prep first visit: count is 1", "1", cat(PB / "dep/patch_visits"))
    ck("patch_prep first visit: feedback cleared", "0", exists01(PB / "dep/patch_feedback.md"))
    ck("patch_prep first visit: contract cleared", "0", exists01(PB / "dep/patch.json"))
    pg_run("patch_prep")
    ck("patch_prep second visit: count is 2", "2", cat(PB / "dep/patch_visits"))

    # patch_blocked: the report must not fail the run even when gh fails.
    PB = pg_repo("blockedgh", "lock"); contract("blocked", "", "blocked for a reason", "[]"); pg_run("patch_blocked", pb_stubs)
    ck("patch_blocked, gh failing: exit 0", "0", cat(PB / "patch_blocked.rc"))


# ---------------------------------------------------------------------------
# dependabot — open_pr and after_merge (ADR 0022 task 07). A bare origin and a clone on a run
# branch; gh is stubbed and logs every call. The PR is created once, and after_merge closes
# or labels. Then pr_handoff, the shared phase's entry contract.
# ---------------------------------------------------------------------------
def test_dependabot_open_pr_after_merge(ck, tmp_path):
    op_stubs = tmp_path / "op-stubs/bin"
    stub(op_stubs, "gh", """#!/bin/sh
echo "gh $*" >> "$GH_LOG"
case "$1 $2" in
  "pr view") [ -f "$GH_STATE/created" ] && echo "https://github.com/andrewthetechie/jelly-swipe/pull/412" ;;
  "pr create") touch "$GH_STATE/created"; echo "https://github.com/andrewthetechie/jelly-swipe/pull/412" ;;
  *) : ;;
esac
[ -n "${GH_FAIL:-}" ] && exit 1
exit 0
""")

    def op_repo(name):
        """A bare origin, a clone on run, and the contract files open_pr reads."""
        OP = tmp_path / f"op-{name}"
        (OP / "fab/dep").mkdir(parents=True); (OP / "state").mkdir()
        w = OP / "work"
        git("init", "-q", "--bare", OP / "origin.git"); git("clone", "-q", OP / "origin.git", w)
        git("-C", w, "config", "user.email", "t@example.test"); git("-C", w, "config", "user.name", "t")
        git("-C", w, "checkout", "-q", "-b", "main"); write(w / "README", "base\n")
        git("-C", w, "add", "-A"); git("-C", w, "commit", "-qm", "base"); git("-C", w, "push", "-q", "origin", "main")
        git("-C", w, "checkout", "-q", "-b", "run"); git("-C", w, "commit", "-q", "--allow-empty", "-m", "the patch")
        write(OP / "fab/dep/group.json", jq_n('{number: 412, package: "urllib3", manifest: "uv.lock", issue: 412, first_patched: "2.8.0", alerts: [72, 73, 74], replaces: [440, 441]}') + "\n")
        write(OP / "fab/dep/patch.json", jq_n('{status: "patched", summary: "Bumped urllib3 2.7.1 to 2.8.0 in uv.lock; no API changes.", changes: [{package: "urllib3", from: "2.7.1", to: "2.8.0", manifest: "uv.lock", direct: false, parent: "requests"}], verification: [{command: "./.fabro/ci.sh", result: "pass"}], major: false, blocker: ""}') + "\n")
        write(OP / "fab/issue.json", jq_n('{number: 412, title: "t", body: "| [#72](u) | high | runtime | < 2.8.0 | 2.8.0 | GHSA-x |\\n", url: "u", state: "OPEN", labels: []}') + "\n")
        return OP

    def op_run(OP, node, **env):
        script = write(OP / f"{node}.sh", rebase(extract(DEP, node), OP / "fab"))
        run_files(["sh", script], OP / node, cwd=OP / "work", path=f"{op_stubs}:{ORIG_PATH}",
                  env={"GH_LOG": OP / "gh.log", "GH_STATE": OP / "state", **env})

    def group_inplace(OP, filt):
        write(OP / "fab/dep/group.json", jq(filt, file=OP / "fab/dep/group.json", raw=False) + "\n")

    # 1. open_pr: the subject is conventional and at most 72 characters with the number, the
    #    body has the marker block and Resolves, and the PR is created with its labels. The
    #    fixture uses a long package name.
    OP = op_repo("open")
    group_inplace(OP, '.package = "a-very-long-package-name-that-keeps-going-and-going-past-the-limit"')
    op_run(OP, "open_pr")
    ck("open_pr: exit 0", "0", cat(OP / "open_pr.rc"))
    subj = cat(OP / "fab/commit_subject.txt")
    ck("open_pr: subject is at most 72 characters", "1", "1" if len(subj) <= 72 else "0")
    ck("open_pr: subject ends with the issue number", "1", count(" (#412)$", subj))
    ck("open_pr: subject is conventional", "1", count("^fix(deps): ", subj))
    ck("open_pr: commit body ends with Resolves", "1", count("^Resolves #412$", file=OP / "fab/commit_body.md"))
    ck("open_pr: PR body has the start marker", "1", count("fabro:commit-body:start", file=OP / "fab/pr_body.md"))
    ck("open_pr: PR body has the end marker", "1", count("fabro:commit-body:end", file=OP / "fab/pr_body.md"))
    ck("open_pr: the pr_create call has all three labels", "1", count("pr create.*--label agent-authored --label security --label dependencies", file=OP / "gh.log"))
    ck("open_pr: the tracking issue gets Review", "1", count("issue edit 412 --add-label Review", file=OP / "gh.log"))
    ck("open_pr: pr_number is 412", "412", cat(OP / "fab/pr_number"))
    ck("open_pr: publishes pr_url", "https://github.com/andrewthetechie/jelly-swipe/pull/412", route(OP / "open_pr", ".context_updates.pr_url"))

    # A manifest path with no spaces is never cut mid-path into an empty "in": the subject
    # falls back to the manifest's basename, then drops " in <manifest>" (jelly-swipe issue
    # #448 was titled "... security alerts in").
    OP = op_repo("opdir")
    group_inplace(OP, '.package = "source-map-js" | .manifest = "frontend/package-lock.json"')
    op_run(OP, "open_pr")
    ck("open_pr, a long manifest path: the subject drops the manifest whole", "fix(deps): patch source-map-js security alerts (#412)", cat(OP / "fab/commit_subject.txt"))

    # after_merge: merged closes each replaced PR once, with the comment; needs_human labels the issue.
    OP = op_repo("am")
    write(OP / "fab/pr_number", "412"); write(OP / "fab/review_merge_outcome", "merged\n")
    op_run(OP, "after_merge")
    ck("after_merge merged: exit 0", "0", cat(OP / "after_merge.rc"))
    ck("after_merge merged: closes 440", "1", count("pr close 440 --comment", file=OP / "gh.log"))
    ck("after_merge merged: closes 441", "1", count("pr close 441 --comment", file=OP / "gh.log"))
    ck("after_merge merged: each close comment names the patch", "2", count("Superseded by #412, which patched urllib3 to 2.8.0", file=OP / "gh.log"))

    OP = op_repo("amh")
    write(OP / "fab/pr_number", "412"); write(OP / "fab/review_merge_outcome", "needs_human\n")
    write(OP / "fab/needs_human_reason", "Could not read the merge gate\n")
    op_run(OP, "after_merge")
    ck("after_merge needs_human: adds needs-human to the issue", "1", count("issue edit 412 --add-label needs-human", file=OP / "gh.log"))
    ck("after_merge needs_human: the comment quotes the reason", "1", count("Could not read the merge gate", file=OP / "gh.log"))
    ck("after_merge needs_human: closes nothing", "0", count("pr close", file=OP / "gh.log"))

    # A missing outcome file (the phase failed before it reported) is treated as needs_human.
    OP = op_repo("amno")
    write(OP / "fab/pr_number", "412")
    op_run(OP, "after_merge")
    ck("after_merge no outcome file: the issue is labelled", "1", count("issue edit 412 --add-label needs-human", file=OP / "gh.log"))
    ck("after_merge no outcome file: the reason is the phase failure", "1", count("the review phase failed before it reported", file=OP / "gh.log"))

    # gh failing: the node still exits 0.
    OP = op_repo("amfail")
    write(OP / "fab/pr_number", "412"); write(OP / "fab/review_merge_outcome", "merged\n")
    op_run(OP, "after_merge", GH_FAIL="1")
    ck("after_merge with gh failing: exit 0", "0", cat(OP / "after_merge.rc"))

    # pr_handoff: writes the shared phase's entry contract. Its script was once a leaked
    # template placeholder, which every check above missed because none ran it. GH_LINKED is
    # the number of reads that return no closingIssuesReferences before the link appears
    # (GitHub links it up to ~20 s late).
    ph_stubs = tmp_path / "ph-stubs/bin"
    stub(ph_stubs, "gh", """#!/bin/sh
echo "gh $*" >> "$GH_LOG"
[ -n "${GH_FAIL:-}" ] && exit 1
N=$(cat "$GH_STATE/reads" 2>/dev/null || echo 0); echo $((N + 1)) > "$GH_STATE/reads"
if [ "$N" -lt "${GH_LINKED:-0}" ]; then L='[]'; else L='[{"number":412}]'; fi
printf '{"number":413,"url":"https://github.com/andrewthetechie/jelly-swipe/pull/413","baseRefName":"main","headRefName":"run","labels":[],"closingIssuesReferences":%s}\\n' "$L"
""")

    def ph_run(name, **env):
        OP = op_repo(name)
        write(OP / "fab/pr_number", "413")
        script = write(OP / "pr_handoff.sh", rebase(extract(DEP, "pr_handoff"), OP / "fab"))
        ok, err = syntax_ok(script)
        if not ok:
            ck.fail("pr_handoff is not valid POSIX sh", err)
        run_files(["sh", script], OP / "pr_handoff", cwd=OP / "work", path=f"{ph_stubs}:{ORIG_PATH}",
                  env={"GH_LOG": OP / "gh.log", "GH_STATE": OP / "state", "DEP_LINK_WAIT": "0", **env})
        return OP

    OP = ph_run("ph")
    ck("pr_handoff: exit 0", "0", cat(OP / "pr_handoff.rc"))
    for f in ("pr.json", "base_ref", "head_ref", "linked_issues.json", "run_base_sha"):
        p = OP / "fab" / f
        ck(f"pr_handoff: writes {f}", "1", "1" if p.is_file() and p.stat().st_size > 0 else "0")
    ck("pr_handoff: base_ref from the PR", "main", cat(OP / "fab/base_ref"))
    ck("pr_handoff: linked_issues holds the issue", "412", jq(".[0].number", file=OP / "fab/linked_issues.json"))
    ck("pr_handoff: run_base_sha is the base tip", git("-C", OP / "work", "rev-parse", "origin/main"), cat(OP / "fab/run_base_sha"))
    ck("pr_handoff: stopped_at cleared", "0", exists01(OP / "fab/dep/stopped_at"))
    ck("pr_handoff: does not re-read the automation rows", "0", count("automations", file=OP / "pr_handoff.sh"))

    OP = ph_run("phlate", GH_LINKED="2")
    ck("pr_handoff, link late: exit 0", "0", cat(OP / "pr_handoff.rc"))
    ck("pr_handoff, link late: reads the PR until it links", "3", count("pr view 413", file=OP / "gh.log"))
    ck("pr_handoff, link late: linked_issues holds the issue", "412", jq(".[0].number", file=OP / "fab/linked_issues.json"))

    OP = ph_run("phnone", GH_LINKED="99")
    ck("pr_handoff, never linked: exit 0", "0", cat(OP / "pr_handoff.rc"))
    ck("pr_handoff, never linked: linked_issues is []", "[]", jq(".", file=OP / "fab/linked_issues.json", args=["-c"], raw=False))

    OP = ph_run("phfail", GH_FAIL="1")
    ck("pr_handoff, gh failing: exit 1", "1", cat(OP / "pr_handoff.rc"))
    ck("pr_handoff, gh failing: stopped_at names it", "pr_handoff", cat(OP / "fab/dep/stopped_at"))


# ---------------------------------------------------------------------------
# dependabot — summarize and the dependabot-summary Discord kind (ADR 0022 task 08).
# ---------------------------------------------------------------------------
def test_dependabot_summarize(ck, tmp_path):
    st = {}

    def run_summarize():
        SM = st["sm"]
        run_files(["sh", SM / "summarize.sh"], SM / "summarize", cwd=SM)
        write(SM / "route.json", tail1(SM / "summarize.out") + "\n" if (SM / "summarize.out").read_text() else "")

    def sm_case(name):
        """Runs summarize in a fresh root; the caller seeds dep/* and runs it again."""
        SM = tmp_path / f"sm-{name}"
        (SM / "dep").mkdir(parents=True)
        write(SM / "dep/repo.json", '{"nameWithOwner":"andrewthetechie/jelly-swipe"}\n')
        write(SM / "summarize.sh", rebase(extract(DEP, "summarize"), SM))
        st["sm"] = SM
        run_summarize()
        return SM

    summary = lambda **kw: jq(".context_updates.dep_summary", file=st["sm"] / "route.json", **kw)
    put = lambda SM, f, filt: write(SM / "dep" / f, jq_n(filt) + "\n")

    # 1. The full day: six PRs (four merged, one red, one conflicting), a patch that merged,
    #    and two alerts with no patched version, one group skipped. The line matches the shape
    #    in ADR 0022 task 08.
    SM = sm_case("day")
    put(SM, "results.json", '[{pr:442,outcome:"merged",reason:"",fixes:[]},{pr:441,outcome:"merged",reason:"",fixes:[]},{pr:440,outcome:"merged",reason:"",fixes:[]},{pr:439,outcome:"red",reason:"test",fixes:[]},{pr:438,outcome:"merged",reason:"",fixes:[]},{pr:437,outcome:"conflict",reason:"",fixes:[]}]')
    put(SM, "prs.json", '[range(6) as $i | {number: (437 + $i), title: "t", head_sha: "x", fixes: []}]')
    put(SM, "alerts.json", '[{number:72,package:"urllib3",ecosystem:"pip",manifest:"uv.lock",first_patched:"2.8.0"},{number:73,package:"urllib3",ecosystem:"pip",manifest:"uv.lock",first_patched:"2.8.0"},{number:74,package:"urllib3",ecosystem:"pip",manifest:"uv.lock",first_patched:"2.8.0"}]')
    put(SM, "group.json", '{package:"urllib3",manifest:"uv.lock",alerts:[72,73,74],issue:412,replaces:[]}')
    put(SM, "no_patch.json", "[{number:12},{number:15}]")
    put(SM, "skipped_groups.json", '[{key:"npm:frontend/package-lock.json:brace-expansion",reason:"needs-human"}]')
    put(SM, "patch.json", '{status:"patched"}')
    # The PR number and the phase outcome are the root files /tmp/fabro/pr_number and
    # review_merge_outcome.
    write(SM / "pr_number", "445"); write(SM / "review_merge_outcome", "merged")
    run_summarize()
    ck("summarize full day: exit 0", "0", cat(SM / "summarize.rc"))
    ck("summarize full day: dep_summary matches the shape",
       "PRs: merged 4 of 6 (#442 #441 #440 #438); #439 red (test); #437 conflict, rebase requested | Patch: urllib3 in uv.lock (3 alerts) -> #445, merged | Left: 2 alerts with no patched version (#12 #15); 1 group skipped (npm:frontend/package-lock.json:brace-expansion: needs-human)",
       summary())

    # 2. A quiet day: no alerts and no PRs.
    SM = sm_case("quiet")
    put(SM, "alerts.json", "[]"); put(SM, "prs.json", "[]")
    run_summarize()
    ck("summarize quiet day: the Nothing to do line", "Nothing to do: 0 alerts open, 0 Dependabot PRs open.", summary())

    # 3. A stopped run names the node and the reason.
    SM = sm_case("stopped")
    put(SM, "alerts.json", "[{number:72}]"); put(SM, "prs.json", "[]")
    write(SM / "dep/stopped_at", "dep_validate"); write(SM / "dep/stop_reason", "the checkout is not clean")
    run_summarize()
    ck("summarize stopped: begins with Stopped at dep_validate:", "1", count("^Stopped at dep_validate: ", summary(), pipe=True))
    ck("summarize stopped: ends with the reason", "1", count("the checkout is not clean$", summary(), pipe=True))

    # 4. Quotes and backslashes in the reason are stripped; the routing object stays valid JSON.
    SM = sm_case("quotes")
    put(SM, "results.json", '[{pr:1,outcome:"red",reason:"a \\"quoted\\" word and a back\\\\slash",fixes:[]}]')
    put(SM, "prs.json", "[{number:1}]"); put(SM, "alerts.json", "[{number:72}]")
    run_summarize()
    ck("summarize quotes: the routing object is valid JSON", "0", jq_ok(cat(SM / "route.json")))
    ck("summarize quotes: no double quote in dep_summary", "0", count('\\"', summary(), pipe=True))
    ck("summarize quotes: no backslash in dep_summary", "0", str(summary().count("\\")))

    # 5. Length: 100 merged PRs stay under 1,500 bytes, and the routing object is valid.
    SM = sm_case("long")
    put(SM, "results.json", '[range(100) as $i | {pr: (1000 + $i), outcome: "merged", reason: "", fixes: []}]')
    put(SM, "prs.json", '[range(100) as $i | {number: (1000 + $i), title: "t", head_sha: "x", fixes: []}]'); put(SM, "alerts.json", "[{number:72}]")
    run_summarize()
    ck("summarize long: dep_summary is at most 1500 bytes", "1", "1" if len(summary(strip=False).encode()) <= 1500 else "0")
    ck("summarize long: the routing object is valid JSON", "0", jq_ok(cat(SM / "route.json")))


# The dependabot-summary Discord kind: a stubbed wget serves the run state and a stubbed curl
# captures the payload. The notify script's /storage paths are rewritten to a scratch root.
def test_dependabot_summary_discord_kind(ck, tmp_path):
    NT = tmp_path / "notify"
    (NT / "storage/secrets").mkdir(parents=True)
    write(NT / "storage/secrets/discord_webhook_url", "http://discord.test/hook")
    write(NT / "storage/server.dev-token", "tok")
    notify = write(NT / "notify.sh", (WF / "backlog/scripts/discord-notify.sh").read_text().replace("/storage", str(NT / "storage")))
    stub(NT / "stub", "wget", """#!/bin/sh
case "$*" in
  *"/state"*) echo '{"dep_summary":"PRs: merged 4 of 6 (#442 #441 #440 #438) | Patch: urllib3 in uv.lock (3 alerts) -> #445, merged | Left: 2 alerts with no patched version (#12 #15)"}' ;;
  *"/api/v1/runs/"*) echo '{"origin_url":"https://github.com/andrewthetechie/jelly-swipe.git"}' ;;
esac
exit 0
""")
    stub(NT / "stub", "curl", '#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && { printf \'%s\' "$2" > "$NT_PAYLOAD"; }; shift; done\nexit 0\n')
    payload = NT / "payload.json"
    sh(notify, "dependabot-summary", path=f"{NT / 'stub'}:{ORIG_PATH}",
       env={"NT_PAYLOAD": payload, "NT": NT}, stdin='{"run_id":"01X"}\n')
    ck("notify dependabot-summary: a payload was sent", "1", "1" if payload.is_file() and payload.stat().st_size else "0")
    ck("notify dependabot-summary: the header names the repo", "1", count("fabro dependabot on andrewthetechie/jelly-swipe", file=payload))
    nl = grep(["-o", "\\\\n"], file=payload)
    ck("notify dependabot-summary: the header and three summary lines, then the repo link", "4", str(len(nl.split("\n")) if nl else 0))
    ck("notify dependabot-summary: the payload is valid JSON", "0", jq_ok(cat(payload)))
