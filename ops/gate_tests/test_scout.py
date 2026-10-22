"""The scout package (ADR 0027, task 04; ADR 0028): its command nodes. gh, fabro-io, date and
curl are stubbed and driven by environment variables, so one set of stubs serves every case.
Each node is extracted from the graph, rebased onto its own scratch git repository, held to
POSIX sh, and run."""

import shutil

from harness import (ORIG_PATH, SCOUT, cat, count, extract, git, jq, jq_n, rebase, rm,
                     routing_check, run_files, stub, syntax_ok, write)

SCOUT_GH = """#!/bin/sh
echo "gh $*" >> "$SC_LOG"
case "$*" in
  "repo view"*) echo "${SC_REPO-andrewthetechie/jelly-swipe}" ;;
  *"/issues?labels=scout"*) [ -n "${SC_FAIL_ISSUES:-}" ] && exit 1; cat "${SC_ISSUES:-/dev/null}" ;;
  *"/actions/runs?"*)
      [ -n "${SC_FAIL_RUNS:-}" ] && exit 1
      if [ -n "${SC_RUNS_PAGES:-}" ]; then
          PG=${2##*page=}
          if [ -e "$SC_RUNS_PAGES/page_$PG.raw" ]; then cat "$SC_RUNS_PAGES/page_$PG.raw"; fi
      else
          cat "${SC_RUNS:-/dev/null}"
      fi ;;
  *"/actions/runs/"*"/jobs?filter=all"*)
      ID=""
      for a in "$@"; do case "$a" in *"/actions/runs/"*"/jobs"*) ID=${a#*/actions/runs/}; ID=${ID%%/jobs*} ;; esac; done
      [ -n "${SC_FAIL_JOBS:-}" ] && exit 1
      cat "$SC_JOBS_DIR/jobs_$ID.json" ;;
  *"/actions/jobs/"*"/logs")
      [ -n "${SC_FAIL_LOG:-}" ] && exit 1
      echo 'a job log line' ;;
  "issue create"*)
      [ -n "${SC_FAIL_CREATE:-}" ] && { echo 'create failed' >&2; exit 1; }
      N=$(cat "$SC_COUNTER"); N=$((N + 1)); printf '%s' "$N" > "$SC_COUNTER"
      echo "https://github.com/${SC_REPO-andrewthetechie/jelly-swipe}/issues/$N" ;;
  "label create"*) exit 0 ;;
  *) echo "gh $*" >&2; exit 1 ;;
esac
"""
SCOUT_FABRO_IO = """#!/bin/sh
echo "fabro-io $*" >> "$SC_LOG"
case "$1 $2" in
  "scout config") [ -n "${SC_FAIL_CONFIG:-}" ] && exit "${SC_FAIL_CONFIG}"; cat "${SC_CONFIG:-/dev/null}" ;;
  "scout ratchet") cat "${SC_RATCHET:-/dev/null}" ;;
  "scout flaky") cat "${SC_FLAKY:-/dev/null}" ;;
  "scout flakeradar") [ -n "${SC_FAIL_RADAR:-}" ] && exit 1; cat "${SC_RADAR:-/dev/null}" ;;
  "scout plan") cat "${SC_PLAN:-/dev/null}" ;;
  "scout summary") cat "${SC_SUMMARY:-/dev/null}" ;;
  *) echo "fabro-io $*" >&2; exit 2 ;;
esac
"""
SCOUT_DATE = """#!/bin/sh
case "$*" in
  "-u -d @"*) echo "${SC_SINCE-2026-10-02T02:00:00Z}" ;;
  "-u +%Y-%m-%dT%H:%M:%SZ") echo "${SC_NOW-2026-10-16T02:00:00Z}" ;;
  "+%s") echo "${SC_NOW_EPOCH-1760000000}" ;;
  *) exec /bin/date "$@" ;;
esac
"""
# FlakeRadar (ADR 0028): answers from $SC_FR, one file per resource, and 404 for a missing
# one. SC_FR_502 names a URL substring that answers 502; SC_FR_DOWN makes every call fail as
# a refused connection does (curl prints 000 and exits 7). A GET still logs one `curl <url>`
# line. A PUT (the write-back, C11) logs `curl PUT <url> key=<X-API-Key> body=<--data>` and
# answers from `link_<entity>_<id>.code` and `.json`, or `..._replace.*` for ?replace=true: a
# missing `.code` is 200, a missing `.json` is `{}`. A `.exit` file makes curl fail after it
# prints the code, with that exit status, as a timeout does.
SCOUT_CURL = """#!/bin/sh
OUT=/dev/null; URL=""; METHOD=GET; KEY=""; DATA=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUT=$2; shift ;;
    -X) METHOD=$2; shift ;;
    -H) case "$2" in X-API-Key:*) KEY=${2#X-API-Key: }; KEY=${KEY# } ;; esac; shift ;;
    --data) DATA=$2; DATA=${DATA#@}; shift ;;
    -w|-m) shift ;;
    -*) ;;
    *) URL=$1 ;;
  esac
  shift
done
if [ "$METHOD" = PUT ]; then
  echo "curl PUT $URL key=$KEY body=$(cat "$DATA")" >> "$SC_LOG"
else
  echo "curl $URL" >> "$SC_LOG"
fi
if [ -n "${SC_FR_DOWN:-}" ]; then echo 'curl: (7) Failed to connect' >&2; printf 000; exit 7; fi
case "$URL" in *"${SC_FR_502:-@none@}"*) printf 'bad gateway' > "$OUT"; printf 502; exit 0 ;; esac
if [ "$METHOD" = PUT ]; then
  R=${URL#*/api/}; ENT=${R%%/*}; R=${R#*/}; ID=${R%%/*}
  F=link_${ENT}_${ID}
  case "$R" in *replace=true*) F=${F}_replace ;; esac
  if [ -e "$SC_FR/$F.code" ]; then CODE=$(cat "$SC_FR/$F.code"); else CODE=200; fi
  if [ -e "$SC_FR/$F.json" ]; then cat "$SC_FR/$F.json" > "$OUT"; else printf '{}' > "$OUT"; fi
  printf '%s' "$CODE"
  if [ -e "$SC_FR/$F.exit" ]; then echo 'curl: (28) Operation timed out after 20001 milliseconds' >&2; exit "$(cat "$SC_FR/$F.exit")"; fi
  exit 0
fi
P=${URL#*/api/}
case "$P" in
  repos) F=repos.json ;;
  tests\\?*) PG=${P##*page=}; F=tests_$PG.json ;;
  tests/*/history*) ID=${P#tests/}; F=history_${ID%%/*}.json ;;
  ci-runs/*) ID=${P#ci-runs/}; F=run_${ID%%/*}_${P##*ci_run_attempt=}.json ;;
  *) F=none ;;
esac
if [ -e "$SC_FR/$F" ]; then cat "$SC_FR/$F" > "$OUT"; printf 200; else printf '{"detail":"Not found"}' > "$OUT"; printf 404; fi
"""


class Scout:
    """The stubs and the small inputs the nodes read, built once per test."""

    def __init__(self, ck, tmp_path):
        self.ck, self.tmp = ck, tmp_path
        self.stubs = tmp_path / "scout-stubs/bin"
        stub(self.stubs, "gh", SCOUT_GH)
        stub(self.stubs, "fabro-io", SCOUT_FABRO_IO)
        stub(self.stubs, "date", SCOUT_DATE)
        stub(self.stubs, "curl", SCOUT_CURL)
        F = self.fix = tmp_path / "scout-fix"
        F.mkdir()
        write(F / "config.json", '{"present":true,"valid":true,"error":null,"filing":false,"ratchet":[],"flaky":{"min_flakes":3,"window_days":14}}')
        write(F / "config_live.json", '{"filing":true,"ratchet":[{"index":0,"tool":"ruff","rule":"B006","command":"cmd","enforce_in":"pyproject.toml","enforce_how":"how","timeout_secs":900}],"flaky":{"min_flakes":3,"window_days":14}}')
        write(F / "env.json", '{"repo":"andrewthetechie/jelly-swipe","sha":"abcdef1234567890"}')
        write(F / "issues.raw", '{"number":1,"state":"open","state_reason":null,"closed_at":null,"body":"<!-- fabro:scout fp=ratchet:ruff:b006 -->"}\n')
        write(F / "no-issues.raw", "")
        self.d = None

    def setup(self, name, node):
        d = self.d = self.tmp / f"scout-{name}"
        rm(d, f"{d}.sh", f"{d}.err")
        for sub in ("scout/jobs", "scout/logs", "jobs-in"):
            (d / sub).mkdir(parents=True)
        git("-C", d, "init", "-q", "-b", "fabro/run/01SCOUT")
        git("-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        write(d / "counter", "100")
        script = write(f"{d}.sh", rebase(extract(SCOUT, node), d))
        ok, err = syntax_ok(script)
        if not ok:
            self.ck.fail(f"scout {node} is not valid POSIX sh", err)
        return d

    def inputs(self, config="config.json"):
        shutil.copyfile(self.fix / config if "/" not in str(config) else config, self.d / "scout/config.json")
        shutil.copyfile(self.fix / "env.json", self.d / "scout/env.json")

    def run(self, **env):
        d = self.d
        run_files(["sh", f"{d}.sh"], d, cwd=d, path=f"{self.stubs}:{ORIG_PATH}",
                  env={"SC_LOG": d / "gh.log", "SC_COUNTER": d / "counter", "SC_JOBS_DIR": d / "jobs-in", **env})

    rc = property(lambda self: cat(f"{self.d}.rc"))
    log = property(lambda self: self.d / "gh.log")

    def logged(self, pattern, flags=""):
        """grep -c over gh.log, 0 when nothing was ever logged."""
        return count(pattern, file=self.log, flags=flags) if self.log.exists() else "0"

    def scout(self, f):
        return self.d / "scout" / f


def exists01(p) -> str:
    return "1" if p.exists() else "0"


def test_scout_nodes(ck, tmp_path):
    S = Scout(ck, tmp_path)
    F = S.fix

    # 1. prep: routes scout=run and writes env.json, config.json and an issues array.
    d = S.setup("prep-ok", "prep")
    S.run(SC_CONFIG=F / "config.json", SC_ISSUES=F / "issues.raw")
    routed = routing_check(ck, "scout prep, ok", d, ".context_updates.scout")
    ck("scout prep: routes scout=run", "run", routed)
    ck("scout prep: env.json has repo, sha and now", "now,repo,sha", jq('keys | join(",")', file=S.scout("env.json")))
    ck("scout prep: config.json is written", "1", "1" if S.scout("config.json").stat().st_size else "0")
    ck("scout prep: issues.json is an array", "array", jq("type", file=S.scout("issues.json")))

    # 2. prep: a failed issue list stops the run.
    S.setup("prep-issues", "prep")
    S.run(SC_CONFIG=F / "config.json", SC_FAIL_ISSUES="1")
    ck("scout prep: issue list fails: exit 1", "1", S.rc)
    ck("scout prep: issue list fails: stop_reason", "could not list scout issues", cat(S.scout("stop_reason")))

    # 3. prep: a fabro-io without the scout subcommand says to deploy the images.
    S.setup("prep-noio", "prep")
    S.run(SC_FAIL_CONFIG="2", SC_ISSUES=F / "issues.raw")
    ck("scout prep: fabro-io fails: exit 1", "1", S.rc)
    ck("scout prep: fabro-io fails: stop_reason names the deploy", "1", count("make deploy-images", file=S.scout("stop_reason")))

    # 4. ratchet: no declared rules never runs setup.sh.
    d = S.setup("ratchet-none", "ratchet")
    shutil.copyfile(F / "config.json", S.scout("config.json"))
    stub(d / ".fabro", "setup.sh", f"#!/bin/sh\ntouch '{d}/.fabro/setup-ran'\n")
    S.run()
    ck("scout ratchet: no entries: exit 0", "0", S.rc)
    ck("scout ratchet: no entries: status ok", "ok", jq(".status", file=S.scout("ratchet.json")))
    ck("scout ratchet: no entries: setup.sh was not run", "0", exists01(d / ".fabro/setup-ran"))

    # 5. ratchet: a failed setup.sh becomes the C3 skip and still exits 0.
    d = S.setup("ratchet-fail", "ratchet")
    shutil.copyfile(F / "config_live.json", S.scout("config.json"))
    stub(d / ".fabro", "setup.sh", "#!/bin/sh\nexit 3\n")
    S.run()
    ck("scout ratchet: setup.sh exit 3: exit 0", "0", S.rc)
    ck("scout ratchet: setup.sh exit 3: status skipped", "skipped", jq(".status", file=S.scout("ratchet.json")))
    ck("scout ratchet: setup.sh exit 3: reason", "setup.sh failed (exit 3)", jq(".reason", file=S.scout("ratchet.json")))

    # 6. flaky: jobs are read only for runs with run_attempt >= 2, and a failed log read leaves none.
    d = S.setup("flaky-jobs", "flaky"); S.inputs()
    write(d / "jobs-in/runs.raw", '{"id":111,"path":".github/workflows/ci.yml","name":"CI","run_attempt":1,"head_sha":"a","head_branch":"main","created_at":"2026-10-15T00:00:00Z","html_url":"https://x/111"}\n'
                                  '{"id":222,"path":".github/workflows/ci.yml","name":"CI","run_attempt":2,"head_sha":"b","head_branch":"main","created_at":"2026-10-15T00:00:00Z","html_url":"https://x/222"}\n')
    write(d / "jobs-in/jobs_222.json", '{"id":2220,"name":"test","run_attempt":1,"conclusion":"failure","html_url":"https://x/222/1","steps":[{"name":"Set up job","number":1,"conclusion":"success"},{"name":"Run pytest","number":2,"conclusion":"failure"}]}\n'
                                       '{"id":2221,"name":"test","run_attempt":2,"conclusion":"success","html_url":"https://x/222/2","steps":[]}\n')
    write(d / "jobs-in/flaky.json", '{"since":"2026-10-02T02:00:00Z","jobs":[{"fp":"flaky:.github/workflows/ci.yml:test","workflow":".github/workflows/ci.yml","job":"test","count":3,"occurrences":[{"run_id":222,"run_url":"https://x/222","attempt":1,"failed_job_id":2220,"failed_job_url":"https://x/222/1","failed_steps":["Run pytest"],"created_at":"2026-10-15T00:00:00Z","head_branch":"main","head_sha":"b"}]}]}')
    S.run(SC_RUNS=d / "jobs-in/runs.raw", SC_FLAKY=d / "jobs-in/flaky.json", SC_FAIL_LOG="1")
    ck("scout flaky: exit 0", "0", S.rc)
    ck("scout flaky: jobs read for the attempt-2 run", "1", count("/actions/runs/222/jobs", file=S.log))
    ck("scout flaky: no jobs read for the attempt-1 run", "0", count("/actions/runs/111/jobs", file=S.log))
    ck("scout flaky: a failed log read leaves no log file", "0", exists01(S.scout("logs/2220.txt")))

    # 6b. flaky: the run list is paged by hand until a page reaches a run older than SINCE (a
    # `created=` query caps at 1,000 runs), and a run seen on two pages is kept once.
    d = S.setup("flaky-pages", "flaky"); S.inputs()
    pages = d / "jobs-in/pages"
    pages.mkdir()
    write(pages / "page_1.raw", jq_n('range(1000; 1100) | {id: ., path: ".github/workflows/ci.yml", name: "CI", run_attempt: 1, head_sha: "a", head_branch: "main", created_at: "2026-10-15T00:00:00Z", html_url: "https://x"}') + "\n")
    write(pages / "page_2.raw", jq_n('(1099, 1100) as $i | {id: $i, path: ".github/workflows/ci.yml", name: "CI", run_attempt: 1, head_sha: "a", head_branch: "main", created_at: (if $i == 1100 then "2026-10-01T00:00:00Z" else "2026-10-15T00:00:00Z" end), html_url: "https://x"}') + "\n")
    write(pages / "page_3.raw", jq_n('{id: 1200, path: ".github/workflows/ci.yml", name: "CI", run_attempt: 1, head_sha: "a", head_branch: "main", created_at: "2026-09-30T00:00:00Z", html_url: "https://x"}') + "\n")
    write(d / "jobs-in/flaky-empty.json", '{"since":"2026-10-02T02:00:00Z","jobs":[]}')
    S.run(SC_RUNS_PAGES=pages, SC_FLAKY=d / "jobs-in/flaky-empty.json")
    ck("scout flaky: paged: exit 0", "0", S.rc)
    ck("scout flaky: paged: no created= filter", "0", count("created=", file=S.log))
    ck("scout flaky: paged: reads pages 1 and 2, not 3", "1|1|0",
       count("runs?per_page=100&page=1 ", file=S.log) + "|" + count("runs?per_page=100&page=2 ", file=S.log) + "|" + count("runs?per_page=100&page=3 ", file=S.log))
    ck("scout flaky: paged: runs de-duplicated by id", "101", jq("length", file=S.scout("runs.json"), raw=False))

    # 7. flaky: a failed run list stops the run.
    S.setup("flaky-runs", "flaky"); S.inputs()
    S.run(SC_FAIL_RUNS="1")
    ck("scout flaky: run list fails: exit 1", "1", S.rc)
    ck("scout flaky: run list fails: stop_reason", "could not read Actions runs", cat(S.scout("stop_reason")))

    # 7b. plan: a ratchet node that hit its own timeout leaves no ratchet.json; plan writes the
    # C3 skip instead of failing, so the flaky source still reports (D15).
    d = S.setup("plan-no-ratchet", "plan"); S.inputs()
    write(d / "jobs-in/plan.json", '{"filing":false,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[{"kind":"known","source":"flaky","fp":"flaky:a:b","issue":3,"title":null,"body":null,"labels":null,"detail":"3 flakes in 14 days"}]}')
    S.run(SC_PLAN=d / "jobs-in/plan.json")
    ck("scout plan: no ratchet.json: exit 0", "0", S.rc)
    ck("scout plan: no ratchet.json: the skip is written", "skipped|the ratchet node did not finish", jq('.status + "|" + .reason', file=S.scout("ratchet.json")))
    ck("scout plan: no ratchet.json: plan still runs", "1", count("^fabro-io scout plan", file=S.log))
    ck("scout plan: stderr names each action", "1", count("^plan: known flaky:a:b 3 flakes in 14 days$", file=f"{d}.err"))

    # 8. file: a dry repository writes nothing.
    S.setup("file-dry", "file"); S.inputs()
    write(S.scout("plan.json"), '{"filing":false,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[]}')
    S.run()
    ck("scout file: dry: exit 0", "0", S.rc)
    ck("scout file: dry: no issue create", "0", S.logged("^gh issue create"))

    # Two file actions, for cases 9 to 11.
    actions = '{"filing":true,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[{"kind":"file","source":"ratchet","fp":"ratchet:ruff:b006","issue":null,"title":"scout: one","body":"body one","labels":["agent","scout","ai-generated","bug","fast-path"],"detail":"1 finding in 1 file"},{"kind":"file","source":"flaky","fp":"flaky:.github/workflows/ci.yml:test","issue":null,"title":"scout: two","body":"body two","labels":["agent","scout","ai-generated","flaky-test"],"detail":"3 flakes in 14 days"}]}'
    filed = lambda filt: jq(filt, file=S.scout("filed.json"))

    # 9. file: two actions become two creates, each carrying every label.
    S.setup("file-live", "file"); S.inputs("config_live.json")
    write(S.scout("plan.json"), actions)
    S.run(SC_ISSUES=F / "no-issues.raw")
    ck("scout file: live: exit 0", "0", S.rc)
    ck("scout file: live: two creates", "2", count("^gh issue create", file=S.log))
    ck("scout file: live: the ratchet labels", "1", count("issue create --title scout: one --body-file .* --label agent --label scout --label ai-generated --label bug --label fast-path", file=S.log))
    ck("scout file: live: the flaky labels", "1", count("issue create --title scout: two --body-file .* --label agent --label scout --label ai-generated --label flaky-test", file=S.log))
    ck("scout file: live: two filed entries", "filed,filed", filed('[.[].outcome] | join(",")'))
    ck("scout file: live: the parsed numbers", "101,102", filed('[.[].number] | map(tostring) | join(",")'))

    # 10. file: a fingerprint that appeared before the write is `raced`.
    d = S.setup("file-raced", "file"); S.inputs("config_live.json")
    write(S.scout("plan.json"), actions)
    write(d / "jobs-in/raced-issues.raw", '{"number":9,"state":"open","state_reason":null,"closed_at":null,"body":"<!-- fabro:scout fp=flaky:.github/workflows/ci.yml:test -->"}\n')
    S.run(SC_ISSUES=d / "jobs-in/raced-issues.raw")
    ck("scout file: raced: one create", "1", count("^gh issue create", file=S.log))
    ck("scout file: raced: outcomes", "filed,raced", filed('[.[].outcome] | join(",")'))

    # 11. file: a failed create is recorded and stops the remaining writes.
    S.setup("file-fail", "file"); S.inputs("config_live.json")
    write(S.scout("plan.json"), actions)
    S.run(SC_ISSUES=F / "no-issues.raw", SC_FAIL_CREATE="1")
    ck("scout file: create fails: exit 0", "0", S.rc)
    ck("scout file: create fails: one entry", "1", jq("length", file=S.scout("filed.json"), raw=False))
    ck("scout file: create fails: the outcome is failed", "failed", filed(".[0].outcome"))
    ck("scout file: create fails: no second create", "1", count("^gh issue create", file=S.log))

    # 12. summarize: a stop is prefixed, and the routing object carries the line.
    d = S.setup("summarize-stop", "summarize")
    write(S.scout("stop_reason"), "prep could not read the repository")
    write(S.scout("stopped_at"), "prep")
    write(S.scout("plan.json"), '{"filing":false,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[]}')
    write(d / "jobs-in/summary.txt", "scout jelly-swipe DRY RUN: would file 1")
    S.run(SC_SUMMARY=d / "jobs-in/summary.txt")
    routed = routing_check(ck, "scout summarize, stop", d, ".context_updates.scout_summary")
    ck("scout summarize: the line starts Stopped at prep:", "1", count("^Stopped at prep: prep could not read the repository", routed, pipe=True))
    ck("scout summarize: no quote survives", "0", count('"', routed, pipe=True))

    # The ratchet-flaky edge must stay unconditional: a ratchet failure must not stop flaky (D15).
    ck("scout: ratchet -> flaky is unconditional", "1", count("^    ratchet -> flaky$", file=SCOUT))


# 13-21. flaky + FlakeRadar (ADR 0028, docs/flakeradar C4b, F7). One covered repository's
# FlakeRadar: one flaky candidate (5) and one suspect (6) on a page of 2, and the CI-run lookup
# of the flake in run 222 attempt 1.
def test_scout_flaky_flakeradar(ck, tmp_path):
    S = Scout(ck, tmp_path)
    F = S.fix
    cov = write(F / "config_covered.json", '{"present":true,"valid":true,"error":null,"filing":false,"ratchet":[],"flaky":{"min_flakes":1,"window_days":14,"ignore_jobs":[]},"flakeradar":{"covered":true,"min_proven":1}}')
    frd = F / "flakeradar"
    frd.mkdir()
    write(frd / "repos.json", '[{"name":"andrewthetechie/jelly-swipe","projects":[]}]')
    write(frd / "tests_1.json", '{"items":[{"id":5,"tier":"flaky","quarantined":false,"confirmed_flake_count":2,"flakiness_score":0.7,"last_seen_at":"2026-10-15T00:00:00.123456Z"},{"id":6,"tier":"suspect","quarantined":false,"confirmed_flake_count":0,"flakiness_score":0.1,"last_seen_at":"2026-10-15T00:00:00.123456Z"}],"total":2,"page":1,"page_size":100}')
    write(frd / "history_5.json", '{"test":{},"executions":[]}')
    write(frd / "run_222_1.json", '{"failed_jobs":[{"ci_job_id":"2220","outcome":"explained"}]}')
    write(F / "flaky_two.json", '{"since":"2026-10-02T02:00:00Z","jobs":[{"fp":"flaky:.github/workflows/ci.yml:test","workflow":".github/workflows/ci.yml","job":"test","count":2,"occurrences":[{"run_id":222,"run_url":"https://x/222","attempt":1,"failed_job_id":2220,"failed_job_url":"https://x/222/1","failed_steps":["Run pytest"],"created_at":"2026-10-15T00:00:00Z","head_branch":"main","head_sha":"b"},{"run_id":333,"run_url":"https://x/333","attempt":1,"failed_job_id":3330,"failed_job_url":"https://x/333/1","failed_steps":["Run pytest"],"created_at":"2026-10-14T00:00:00Z","head_branch":"main","head_sha":"c"}]}]}')
    write(F / "radar_out.json", '{"tests":[{"fp":"flaky:test-default:abc"}],"explained":[2220]}')
    write(F / "plan_empty.json", '{"filing":false,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[]}')
    fr_env = {"SC_FR": frd, "SC_RUNS": F / "no-issues.raw", "SC_FLAKY": F / "flaky_two.json", "SC_RADAR": F / "radar_out.json"}

    def flaky_radar(name, config):
        """A flaky node with the run list empty and flaky_two.json as C4."""
        S.setup(name, "flaky")
        S.inputs(config)

    skip = lambda: cat(S.scout("flakeradar_skip"))

    # 13. An uncovered repository never calls FlakeRadar, even with the URL set.
    unc = write(F / "config_uncovered.json", jq(".flakeradar.covered = false", file=cov, args=["-c"], raw=False) + "\n")
    flaky_radar("flaky-radar-uncovered", unc)
    S.run(**fr_env, FLAKERADAR_URL="https://fr.test")
    ck("scout flaky+radar: uncovered: exit 0", "0", S.rc)
    ck("scout flaky+radar: uncovered: no curl call", "0", S.logged("^curl "))
    ck("scout flaky+radar: uncovered: no skip, no result", "0|0", exists01(S.scout("flakeradar_skip")) + "|" + exists01(S.scout("flakeradar/out.json")))
    ck("scout flaky+radar: uncovered: both log tails read", "2", count("/actions/jobs/.*/logs", file=S.log))

    # 14. Covered and reachable: the reads C4b names, then the digest; the explained flake's
    # log tail is not read.
    flaky_radar("flaky-radar-ok", cov)
    S.run(**fr_env, FLAKERADAR_URL="https://fr.test/")
    d = S.d
    ck("scout flaky+radar: ok: exit 0", "0", S.rc)
    ck("scout flaky+radar: ok: the repository list", "1", count("^curl https://fr.test/api/repos$", file=S.log))
    ck("scout flaky+radar: ok: one tests page (total reached)", "1|0",
       count("api/tests?repo=andrewthetechie/jelly-swipe&sort=score&page_size=100&page=1$", file=S.log) + "|" + count("page=2$", file=S.log))
    ck("scout flaky+radar: ok: history of the candidate only", "1|0",
       count("api/tests/5/history?limit=100$", file=S.log) + "|" + count("api/tests/6/history", file=S.log))
    ck("scout flaky+radar: ok: a CI-run lookup per flake", "1|1",
       count("api/ci-runs/222/failures?repo=andrewthetechie/jelly-swipe&ci_run_attempt=1$", file=S.log) + "|" + count("api/ci-runs/333/failures", file=S.log))
    ck("scout flaky+radar: ok: a 404 lookup leaves no file, a 200 one does", "0|1",
       exists01(S.scout("flakeradar/runs/run_333_1.json")) + "|" + exists01(S.scout("flakeradar/runs/run_222_1.json")))
    ck("scout flaky+radar: ok: the digest's flags", "1",
       count(f"^fabro-io scout flakeradar --tests {d}/scout/flakeradar/tests.json --history-dir {d}/scout/flakeradar/history --runs-dir {d}/scout/flakeradar/runs --flaky {d}/scout/flaky.json --config {d}/scout/config.json --since 2026-10-02T02:00:00Z --base https://fr.test --repo andrewthetechie/jelly-swipe$", file=S.log))
    out = S.scout("flakeradar/out.json")
    ck("scout flaky+radar: ok: no skip, the result kept", "0|1", exists01(S.scout("flakeradar_skip")) + "|" + ("1" if out.exists() and out.stat().st_size else "0"))
    ck("scout flaky+radar: ok: only the unexplained flake's log tail", "0|1",
       count("/actions/jobs/2220/logs", file=S.log) + "|" + count("/actions/jobs/3330/logs", file=S.log))

    # 15. Covered with FLAKERADAR_URL empty: the F7 skip, and no call.
    flaky_radar("flaky-radar-nourl", cov)
    S.run(**fr_env, FLAKERADAR_URL="")
    ck("scout flaky+radar: no URL: exit 0", "0", S.rc)
    ck("scout flaky+radar: no URL: the skip", "FLAKERADAR_URL is empty", skip())
    ck("scout flaky+radar: no URL: no curl call", "0", S.logged("^curl "))

    # 16. A 502 on the tests page: the skip names it, the run goes on, and no result is left.
    flaky_radar("flaky-radar-502", cov)
    S.run(**fr_env, FLAKERADAR_URL="https://fr.test", SC_FR_502="/api/tests?")
    ck("scout flaky+radar: 502: exit 0", "0", S.rc)
    ck("scout flaky+radar: 502: the skip", "tests page 1 returned HTTP 502", skip())
    ck("scout flaky+radar: 502: no result, no digest", "0|0", exists01(S.scout("flakeradar/out.json")) + "|" + count("^fabro-io scout flakeradar", file=S.log))
    ck("scout flaky+radar: 502: log tails read as today", "2", count("/actions/jobs/.*/logs", file=S.log))

    # 17. FlakeRadar down: no response is a skip too.
    flaky_radar("flaky-radar-down", cov)
    S.run(**fr_env, FLAKERADAR_URL="https://fr.test", SC_FR_DOWN="1")
    ck("scout flaky+radar: down: exit 0", "0", S.rc)
    ck("scout flaky+radar: down: the skip names no response", "1", count("^the repository list: no response (curl: (7) Failed to connect)$", file=S.scout("flakeradar_skip")))

    # 18. A 502 on a CI-run lookup is a skip; a 404 (case 14) is not.
    flaky_radar("flaky-radar-run502", cov)
    S.run(**fr_env, FLAKERADAR_URL="https://fr.test", SC_FR_502="/ci-runs/222/")
    ck("scout flaky+radar: lookup 502: the skip", "the CI-run lookup of run 222 returned HTTP 502", skip())

    # 19. A repository FlakeRadar does not list is a skip.
    flaky_radar("flaky-radar-norepo", cov)
    frn = S.d / "fr"
    shutil.copytree(frd, frn)
    write(frn / "repos.json", '[{"name":"andrewthetechie/writers-app","projects":[]}]')
    S.run(**{**fr_env, "SC_FR": frn}, FLAKERADAR_URL="https://fr.test")
    ck("scout flaky+radar: unknown repository: the skip", "andrewthetechie/jelly-swipe is not a FlakeRadar repository", skip())
    ck("scout flaky+radar: unknown repository: no tests read", "0", count("api/tests", file=S.log))

    # 20. Paging stops at `total`: 150 tests read pages 1 and 2, never 3.
    flaky_radar("flaky-radar-pages", cov)
    frp = S.d / "fr"
    shutil.copytree(frd, frp)
    write(frp / "tests_1.json", jq(".total = 150", file=frd / "tests_1.json", raw=False) + "\n")
    write(frp / "tests_2.json", '{"items":[{"id":7,"tier":"stable","quarantined":false,"confirmed_flake_count":0,"flakiness_score":0,"last_seen_at":"2026-10-15T00:00:00Z"}],"total":150,"page":2,"page_size":100}')
    S.run(**{**fr_env, "SC_FR": frp}, FLAKERADAR_URL="https://fr.test")
    ck("scout flaky+radar: paged: pages 1 and 2, not 3", "1|1|0",
       count("page=1$", file=S.log) + "|" + count("page=2$", file=S.log) + "|" + count("page=3$", file=S.log))
    ck("scout flaky+radar: paged: three tests slurped", "3", jq("length", file=S.scout("flakeradar/tests.json"), raw=False))

    # 21. plan passes FlakeRadar on: the skip, the result, or nothing at all.
    ratchet_ok = '{"status":"ok","reason":null,"entries":[]}'
    S.setup("plan-radar-skip", "plan"); S.inputs(cov)
    write(S.scout("ratchet.json"), ratchet_ok)
    write(S.scout("flakeradar_skip"), "tests page 1 returned HTTP 502")
    S.run(SC_PLAN=F / "plan_empty.json")
    ck("scout plan+radar: skip: passed with its reason", "1", count("--sha abcdef1234567890 --flakeradar-skipped tests page 1 returned HTTP 502$", file=S.log))
    S.setup("plan-radar-read", "plan"); S.inputs(cov)
    write(S.scout("ratchet.json"), ratchet_ok)
    S.scout("flakeradar").mkdir(); shutil.copyfile(F / "radar_out.json", S.scout("flakeradar/out.json"))
    S.run(SC_PLAN=F / "plan_empty.json")
    ck("scout plan+radar: result: passed", "1", count(f"--sha abcdef1234567890 --flakeradar {S.d}/scout/flakeradar/out.json$", file=S.log))
    S.setup("plan-radar-none", "plan"); S.inputs()
    write(S.scout("ratchet.json"), ratchet_ok)
    S.run(SC_PLAN=F / "plan_empty.json")
    ck("scout plan+radar: neither: the ADR 0027 call", "1", count("--sha abcdef1234567890$", file=S.log))


# 22-32. file + write-back (ADR 0028 C11, F12-F15). The flaky test file action carries
# `flakeradar`, so the node PUTs its issue number to FlakeRadar after the create. The second
# action is a ratchet file, which proves an action without the key sends nothing and that a
# failed link never stops the loop.
LINK_ACTIONS = '{"filing":true,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[{"kind":"file","source":"flaky","fp":"flaky:test-pilot:aaaa","issue":null,"title":"scout: one","body":"body one","labels":["agent","scout","ai-generated","flaky-test"],"detail":"score 0.70, 2 proven flakes","flakeradar":{"entity":"tests","id":7}},{"kind":"file","source":"ratchet","fp":"ratchet:ruff:b006","issue":null,"title":"scout: two","body":"body two","labels":["agent","scout","ai-generated","bug","fast-path"],"detail":"1 finding in 1 file"}]}'
REG_ACTIONS = '{"filing":true,"repo":"andrewthetechie/jelly-swipe","sources":{"ratchet":"ok","flaky":"ok"},"actions":[{"kind":"regression","source":"flaky","fp":"flaky:test-pilot:aaaa","issue":55,"title":"scout (regression of #55): one","body":"body one","labels":["agent","scout","ai-generated","flaky-test"],"detail":"score 0.70, 2 proven flakes","flakeradar":{"entity":"tests","id":7}},{"kind":"file","source":"ratchet","fp":"ratchet:ruff:b006","issue":null,"title":"scout: two","body":"body two","labels":["agent","scout","ai-generated","bug","fast-path"],"detail":"1 finding in 1 file"}]}'
STALE_55 = '{"detail":"Test already links issue #55; pass replace=true to replace it"}'


def test_scout_file_write_back(ck, tmp_path):
    S = Scout(ck, tmp_path)
    F = S.fix
    link_env = {"SC_ISSUES": F / "no-issues.raw", "FLAKERADAR_URL": "https://fr.test", "FLAKERADAR_API_TOKEN": "tok"}

    def link_setup(name, plan):
        """A live repository that the plan and env are written into."""
        S.setup(name, "file"); S.inputs("config_live.json")
        write(S.scout("plan.json"), plan)

    def link_409(name, plan, detail):
        """A 409 whose detail names an issue."""
        link_setup(name, plan)
        (S.d / "fr").mkdir()
        write(S.d / "fr/link_tests_7.code", "409")
        write(S.d / "fr/link_tests_7.json", detail)

    filed = lambda filt: jq(filt, file=S.scout("filed.json"))
    link_of = lambda: filed('.[0].link + "|" + .[0].link_detail')

    # 22. A 200 is `linked`, the body is the new issue's number, and the ratchet action sends nothing.
    link_setup("file-link", LINK_ACTIONS)
    S.run(**link_env)
    ck("scout file link: linked: exit 0", "0", S.rc)
    ck("scout file link: linked: one PUT, key and body", "1", count('curl PUT https://fr.test/api/tests/7/issue key=tok body={"number":101}', file=S.log, flags="F"))
    ck("scout file link: linked: both entries filed", "filed,filed", filed('[.[].outcome] | join(",")'))
    ck("scout file link: linked: the outcomes", "linked,", filed('[.[] | (.link // "")] | join(",")'))
    ck("scout file link: linked: the ratchet entry has no link key", "false", jq('.[1] | has("link")', file=S.scout("filed.json"), raw=False))

    # 23. A 409 that names someone else's issue is `conflict`, sends no replace, and files on.
    link_409("file-link-conflict", LINK_ACTIONS, STALE_55)
    S.run(**link_env, SC_FR=S.d / "fr")
    ck("scout file link: conflict: outcome and detail", "conflict|55", link_of())
    ck("scout file link: conflict: no replace request", "0", count("replace=true", file=S.log))
    ck("scout file link: conflict: the ratchet issue is still created", "2", count("^gh issue create", file=S.log))

    # 24. A 409 that names the regression's own stale issue is retried with ?replace=true.
    link_409("file-link-replace", REG_ACTIONS, STALE_55)
    S.run(**link_env, SC_FR=S.d / "fr")
    ck("scout file link: replace: plain then replace, each once", "1|1",
       count('curl PUT https://fr.test/api/tests/7/issue key=tok body={"number":101}', file=S.log, flags="F") + "|" +
       count('curl PUT https://fr.test/api/tests/7/issue?replace=true key=tok body={"number":101}', file=S.log, flags="F"))
    ck("scout file link: replace: the outcome", "replaced", filed(".[0].link"))

    # 25. A regression that meets someone else's link leaves it alone and is a `conflict`.
    link_409("file-link-other", REG_ACTIONS, '{"detail":"Test already links issue #77; pass replace=true to replace it"}')
    S.run(**link_env, SC_FR=S.d / "fr")
    ck("scout file link: other link: no replace request", "0", count("replace=true", file=S.log))
    ck("scout file link: other link: the outcome and detail", "conflict|77", link_of())

    # 26. A 502 is `failed` with `HTTP 502`, and the loop still files the next issue.
    link_setup("file-link-502", LINK_ACTIONS)
    S.run(**link_env, SC_FR_502="/api/tests/7/issue")
    ck("scout file link: 502: the outcome", "failed|HTTP 502", link_of())
    ck("scout file link: 502: both issues created", "2", count("^gh issue create", file=S.log))

    # 27. FlakeRadar down: the failure names curl's error, and the loop goes on.
    link_setup("file-link-down", LINK_ACTIONS)
    S.run(**link_env, SC_FR_DOWN="1")
    ck("scout file link: down: the outcome", "failed", filed(".[0].link"))
    ck("scout file link: down: no response in the detail", "1", count("^no response", filed(".[0].link_detail"), pipe=True))
    ck("scout file link: down: both issues created", "2", count("^gh issue create", file=S.log))

    # 28. No token: nothing is sent, and the reason names it.
    link_setup("file-link-notoken", LINK_ACTIONS)
    S.run(SC_ISSUES=F / "no-issues.raw", FLAKERADAR_URL="https://fr.test", FLAKERADAR_API_TOKEN="")
    ck("scout file link: no token: no PUT", "0", count("^curl PUT", file=S.log))
    ck("scout file link: no token: the reason", "failed|FLAKERADAR_API_TOKEN is empty", link_of())

    # 29. A dry repository sends nothing and files nothing.
    S.setup("file-link-dry", "file"); S.inputs()
    write(S.scout("plan.json"), LINK_ACTIONS)
    S.run(**link_env)
    ck("scout file link: dry: no PUT", "0", S.logged("^curl PUT"))
    ck("scout file link: dry: no create", "0", S.logged("^gh issue create"))

    # 30. The token never reaches the node's stderr or the write log.
    link_setup("file-link-token", LINK_ACTIONS)
    S.run(**link_env)
    ck("scout file link: the token is not in stderr", "0", count("tok", file=f"{S.d}.err"))
    ck("scout file link: the token is not in the write log", "0", count("tok", file=S.scout("filed.json")))

    # 31. A 200 whose body then times out is still `linked`: curl printed the code before it failed.
    link_setup("file-link-late", LINK_ACTIONS)
    (S.d / "fr").mkdir()
    write(S.d / "fr/link_tests_7.exit", "28")
    S.run(**link_env, SC_FR=S.d / "fr")
    ck("scout file link: late body: the outcome", "linked|null", filed('.[0].link + "|" + (.[0].link_detail | tostring)'))

    # 32. A replace that gets no response is `failed` with curl's error, not `HTTP 000000`.
    link_409("file-link-replace-down", REG_ACTIONS, STALE_55)
    write(S.d / "fr/link_tests_7_replace.code", "000")
    write(S.d / "fr/link_tests_7_replace.exit", "28")
    S.run(**link_env, SC_FR=S.d / "fr")
    ck("scout file link: replace down: the outcome", "failed|replace got no response (curl: (28) Operation timed out after 20001 milliseconds)", link_of())
    ck("scout file link: replace down: both issues created", "2", count("^gh issue create", file=S.log))
