"""The host scripts the automations lean on: the scout-summary Discord kind,
fabro-run-prune.sh, fabro-sandbox-sweep.sh and fabro-monitor.sh's C3/C4/C8 checks."""

import datetime
import time

from harness import (ORIG_PATH, REPO_ROOT, WF, cat, count, grep, jq, jq_n, jq_ok, run, run_files, sh,
                     stub, write)


# ---------------------------------------------------------------------------
# scout-summary Discord kind (task 05): a quiet run posts nothing, and there is no
# once-an-hour state file (scout runs once a day).
# ---------------------------------------------------------------------------
def test_scout_summary_discord_kind(ck, tmp_path):
    SN = tmp_path / "scoutnotify"
    (SN / "storage/secrets").mkdir(parents=True)
    write(SN / "storage/secrets/discord_webhook_url", "http://discord.test/hook")
    write(SN / "storage/server.dev-token", "tok")
    notify = write(SN / "notify.sh", (WF / "backlog/scripts/discord-notify.sh").read_text().replace("/storage", str(SN / "storage")))
    stub(SN / "stub", "wget", """#!/bin/sh
case "$*" in
  *"/state"*) printf '{"scout_summary":"%s"}' "$SN_SUMMARY" ;;
  *"/api/v1/runs/"*) echo '{"origin_url":"https://github.com/andrewthetechie/jelly-swipe.git"}' ;;
esac
exit 0
""")
    stub(SN / "stub", "curl", '#!/bin/sh\necho call >> "$SN_CALLS"\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && printf \'%s\' "$2" > "$SN_PAYLOAD"; shift; done\nexit 0\n')
    calls_f, payload = SN / "calls", SN / "payload.json"

    def sn_run(summary):
        sh(notify, "scout-summary", path=f"{SN / 'stub'}:{ORIG_PATH}", stdin='{"run_id":"01X"}\n',
           env={"SN_CALLS": calls_f, "SN_PAYLOAD": payload, "SN_SUMMARY": summary})

    calls = lambda: str(calls_f.read_text().count("\n")) if calls_f.exists() else "0"
    content = lambda: jq(".content", file=payload)
    sn_run("")
    ck("notify scout-summary: an empty scout_summary posts nothing", "0", calls())
    sn_run("scout jelly-swipe DRY RUN: would file 1 | would file ratchet ruff B006 (2 findings in 1 file)")
    ck("notify scout-summary: a two-part summary posts once", "1", calls())
    c = content().split("\n") + ["", "", ""]
    ck("notify scout-summary: the header and both parts, one per line",
       "1|scout jelly-swipe DRY RUN: would file 1|would file ratchet ruff B006 (2 findings in 1 file)",
       count("🔭 fabro scout on andrewthetechie/jelly-swipe", content(), pipe=True) + "|" + c[1] + "|" + c[2])
    ck("notify scout-summary: the payload is valid JSON", "0", jq_ok(cat(payload)))
    sn_run('a\\b "q"')
    ck("notify scout-summary: a quote and a backslash are stripped", "0|0",
       count('"', content(), pipe=True) + "|" + count("\\\\", content(), pipe=True))


# ---------------------------------------------------------------------------
# fabro-run-prune.sh (task 08): the command line, and the fallback never deletes a live run.
# ---------------------------------------------------------------------------
def test_fabro_run_prune(ck, tmp_path):
    RP = tmp_path / "runprune"
    (RP / "fabro").mkdir(parents=True)
    stub(RP / "bin", "docker", """#!/bin/sh
echo "docker $*" >> "$RP_LOG"
case "$*" in
  *"system prune --help"*) [ -n "${RP_NOFLAG:-}" ] && echo "Options: --older-than" || echo "Options: --workflow --older-than --yes" ;;
  "exec "*"server.dev-token") echo tok ;;
esac
exit 0
""")
    stub(RP / "bin", "curl", """#!/bin/sh
echo "curl $*" >> "$RP_LOG"
case "$*" in
  *"-X DELETE"*) exit 0 ;;
  *"page[offset]=0"*) cat "$RP_PAGE" ;;
  *) printf '{"data":[],"meta":{"has_more":false}}' ;;
esac
""")
    log, out = RP / "log", RP / "out"

    def rp_run(**env):
        write(log, "")
        r = sh(REPO_ROOT / "ops/fabro-run-prune.sh", path=f"{RP / 'bin'}:{ORIG_PATH}",
               env={"RP_LOG": log, "RP_PAGE": RP / "page.json", "FABRO_DIR": RP / "fabro", **env})
        write(out, r.out + "\n" if r.out else "")

    rp_run(DRY_RUN="0")
    ck("fabro-run-prune: the command line", "1", count("docker compose exec -T fabro fabro system prune --workflow merge-queue --older-than 7d --yes", file=log))
    rp_run(DRY_RUN="1")
    ck("fabro-run-prune: the default is a dry run (no --yes)", "0|1",
       count("--yes", file=log) + "|" + count("system prune --workflow merge-queue --older-than 7d", file=log))
    write(RP / "page.json", jq_n('''{data: [
  {id: "OLDDONE", workflow: {slug: "merge-queue"}, lifecycle: {status: {kind: "succeeded"}}, timestamps: {created_at: "2020-01-01T00:00:00.000Z"}},
  {id: "OLDLIVE", workflow: {slug: "merge-queue"}, lifecycle: {status: {kind: "running"}}, timestamps: {created_at: "2020-01-01T00:00:00.000Z"}},
  {id: "OLDOTHER", workflow: {slug: "backlog"}, lifecycle: {status: {kind: "succeeded"}}, timestamps: {created_at: "2020-01-01T00:00:00.000Z"}},
  {id: "NEWDONE", workflow: {slug: "merge-queue"}, lifecycle: {status: {kind: "succeeded"}}, timestamps: {created_at: "2999-01-01T00:00:00.000Z"}}
], meta: {has_more: false}}''') + "\n")
    rp_run(DRY_RUN="0", RP_NOFLAG="1")
    ck("fabro-run-prune: fallback deletes only the old terminal merge-queue run", "1|1",
       count("X DELETE.*runs/OLDDONE", file=log) + "|" + count("X DELETE", file=log))
    ck("fabro-run-prune: fallback never deletes a non-terminal run", "0", count("DELETE.*OLDLIVE", file=log))
    # The daily archive (2026-10-15): every succeeded merge-queue run leaves the default
    # listing, which the fabro UI pages through in full. A failed or live sweep, or another
    # workflow, stays.
    write(RP / "page.json", jq_n('''{data: [
  {id: "MQDONE", workflow: {slug: "merge-queue"}, lifecycle: {status: {kind: "succeeded"}}, timestamps: {created_at: "2026-10-15T00:00:00.000Z"}},
  {id: "MQFAIL", workflow: {slug: "merge-queue"}, lifecycle: {status: {kind: "failed"}}, timestamps: {created_at: "2026-10-15T00:00:00.000Z"}},
  {id: "MQLIVE", workflow: {slug: "merge-queue"}, lifecycle: {status: {kind: "running"}}, timestamps: {created_at: "2026-10-15T00:00:00.000Z"}},
  {id: "BLDONE", workflow: {slug: "backlog"}, lifecycle: {status: {kind: "succeeded"}}, timestamps: {created_at: "2026-10-15T00:00:00.000Z"}}
], meta: {has_more: false}}''') + "\n")
    rp_run(DRY_RUN="0")
    ck("fabro-run-prune: archives the succeeded merge-queue run", "1", count("fabro archive .*MQDONE", file=log))
    ck("fabro-run-prune: never archives a failed or live sweep, or another workflow", "0",
       count("MQFAIL\\|MQLIVE\\|BLDONE", grep("fabro archive", file=log), pipe=True))
    ck("fabro-run-prune: archiving does not replace the prune", "1", count("system prune --workflow merge-queue --older-than 7d --yes", file=log))
    rp_run(DRY_RUN="1")
    ck("fabro-run-prune: a dry run archives nothing", "0|1", count("fabro archive", file=log) + "|" + count("would archive 1 ", file=out))
    write(RP / "page.json", "garbage")
    rp_run(DRY_RUN="0")
    ck("fabro-run-prune: an unreadable listing still prunes, and fails the pass", "1|1",
       count("system prune --workflow merge-queue --older-than 7d --yes", file=log) + "|" + count("archive: ", file=out))


# ---------------------------------------------------------------------------
# fabro-sandbox-sweep.sh: a docker call inside the loop must not read the names list.
# 2026-10-15: a pass stopped after 504 of ~1,950 exited containers and reported success.
# ---------------------------------------------------------------------------
def test_fabro_sandbox_sweep(ck, tmp_path):
    SW = tmp_path / "sandboxsweep"
    stub(SW / "bin", "docker", """#!/bin/sh
case "$1" in
  ps) printf 'fabro-run-01AAAAAAAAAAAAAAAAAAAAAAAA\\nfabro-run-01BBBBBBBBBBBBBBBBBBBBBBBB\\nfabro-run-01CCCCCCCCCCCCCCCCCCCCCCCC\\nfabro-fabro-1\\n' ;;
  inspect)
    case "$2" in
      --size) echo 1024 ;;
      *) cat >/dev/null; echo "false 2020-01-01T00:00:00Z" ;;
    esac ;;
  rm) cat >/dev/null; echo "rm $2" >> "$SW_LOG" ;;
esac
exit 0
""")
    # The sweep is GNU-only (`date -d`); this stub lets it run here. The FinishedAt is in 2020.
    stub(SW / "bin", "date", '#!/bin/sh\n[ "$1" = "-d" ] && { echo 1577836800; exit 0; }\nexec /bin/date "$@"\n')
    # Under pressure the real host disk would be read; keep this test about stdin only.
    stub(SW / "bin", "df", '#!/bin/sh\necho "Filesystem 1024-blocks Used Available Capacity Mounted"\necho "/dev/x 1000000 500000 500000 50% /"\n')
    log = write(SW / "log", "")
    sh(REPO_ROOT / "ops/fabro-sandbox-sweep.sh", path=f"{SW / 'bin'}:{ORIG_PATH}",
       env={"SW_LOG": log, "DRY_RUN": "0", "GRACE_HOURS": "0"})
    ck("fabro-sandbox-sweep: a docker call that reads stdin does not end the pass", "3", count("^rm fabro-run-", file=log))
    ck("fabro-sandbox-sweep: never removes fabro-fabro-1", "0", count("fabro-fabro-1", file=log))


# ---------------------------------------------------------------------------
# fabro-sandbox-sweep.sh, the disk-pressure rules (issue #50): grace below the
# threshold, oldest-first pressure above it, the minimum-age floor, and the big
# writable-layer cap. docker, df and date are stubs; only the host script runs.
# ---------------------------------------------------------------------------
SWEEP_DOCKER = """#!/bin/sh
case "$1" in
  ps) cut -d'|' -f1 "$SW_CONTAINERS" ;;
  inspect)
    if [ "$2" = "--size" ]; then
      name=$5
    else
      name=$2
    fi
    line=$(grep -F "$name|" "$SW_CONTAINERS")
    if [ "$2" = "--size" ]; then
      printf '%s\\n' "$(printf '%s' "$line" | cut -d'|' -f4)"
    else
      printf '%s %s\\n' "$(printf '%s' "$line" | cut -d'|' -f2)" "$(printf '%s' "$line" | cut -d'|' -f3)"
    fi ;;
  rm)
    name=$2
    printf '%s\\n' "$name" >> "$SW_RM"
    # A pressure test can say what df reads after each removal: one percentage per line.
    if [ -s "$SW_DF_STEPS" ]; then
      pct=$(head -1 "$SW_DF_STEPS")
      sed '1d' "$SW_DF_STEPS" > "$SW_DF_STEPS.next"
      mv "$SW_DF_STEPS.next" "$SW_DF_STEPS"
      printf '%s\\n' "$pct" > "$SW_DF"
    fi ;;
esac
exit 0
"""
SWEEP_DF = """#!/bin/sh
pct=$(cat "$SW_DF")
total=${SW_TOTAL:-1000000}
used=$(( total * pct / 100 ))
avail=$(( total - used ))
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/x %s %s %s %s%% /\\n' "$total" "$used" "$avail" "$pct"
"""
SWEEP_DATE = """#!/bin/sh
if [ "$1" = -d ] && [ "$3" = +%s ]; then
    python3 -c 'import sys,datetime as d; print(int(d.datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")).timestamp()))' "$2"
else exec /bin/date "$@"; fi
"""


def _iso_ago(seconds):
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_name(last):
    return "fabro-run-01ARZ3NDEKTSV4RRFFQ69G5FA" + last


class Sweep:
    """A stubbed docker host: containers as name|running|finished|size_in_bytes."""

    def __init__(self, tmp_path, rows, pct=50, steps=(), total_kb=1000000):
        sw = self.sw = tmp_path / "sweep"
        self.total_kb = total_kb
        write(sw / "containers", "\n".join("|".join(str(v) for v in r) for r in rows) + "\n")
        write(sw / "pct", f"{pct}\n")
        write(sw / "steps", "".join(f"{s}\n" for s in steps))
        self.rm = write(sw / "rm.log", "")
        stub(sw / "bin", "docker", SWEEP_DOCKER)
        stub(sw / "bin", "df", SWEEP_DF)
        stub(sw / "bin", "date", SWEEP_DATE)

    def run(self, **env):
        write(self.rm, "")
        return sh(REPO_ROOT / "ops/fabro-sandbox-sweep.sh", path=f"{self.sw / 'bin'}:{ORIG_PATH}",
                  env={"SW_CONTAINERS": self.sw / "containers", "SW_RM": self.rm,
                       "SW_DF": self.sw / "pct", "SW_DF_STEPS": self.sw / "steps",
                       "SW_TOTAL": self.total_kb, "DRY_RUN": "0", "GRACE_HOURS": "24", "BIG_GB": "5",
                       "BIG_GRACE_HOURS": "2", "PRESSURE_PCT": "70",
                       "TARGET_PCT": "60", "MIN_GRACE_MINUTES": "30", **env})


def test_fabro_sandbox_sweep_pressure(ck, tmp_path):
    gb = 1024 ** 3
    # Below the pressure threshold the grace rules are unchanged, including the big cap.
    old, young = _run_name("V"), _run_name("W")
    bigold, bigyoung = _run_name("X"), _run_name("Y")
    running, never = _run_name("Z"), _run_name("0")
    s = Sweep(tmp_path / "grace", [
        (old, "false", _iso_ago(30 * 3600), gb),
        (young, "false", _iso_ago(12 * 3600), gb),
        (bigold, "false", _iso_ago(3 * 3600), 6 * gb),
        (bigyoung, "false", _iso_ago(3600), 6 * gb),
        (running, "true", _iso_ago(40 * 3600), gb),
        (never, "false", "0001-01-01T00:00:00Z", gb),
        ("fabro-fabro-1", "false", _iso_ago(40 * 3600), gb),
    ], pct=50)
    r = s.run()
    ck("sweep below pressure: a 30h sandbox is removed by grace", "1", count(f"^{old}$", file=s.rm))
    ck("sweep below pressure: a 3h 6GB sandbox is removed by the 2h big grace", "1", count(f"^{bigold}$", file=s.rm))
    ck("sweep below pressure: 12h and 1h sandboxes stay within grace", "0",
       count(f"^{young}$|^{bigyoung}$", file=s.rm, flags="E"))
    ck("sweep below pressure: a running sandbox and fabro-fabro-1 are never removed", "0",
       count(f"^{running}$|fabro-fabro-1", file=s.rm, flags="E"))
    ck("sweep below pressure: the output names the rule that removed each", "1|1|0",
       count("(grace)", r.out) + "|" + count("(big)", r.out) + "|" + count("(pressure)", r.out))

    # Above the threshold: oldest FinishedAt first, ignoring the grace, stopping once below target.
    p1, p2, p3, p4 = _run_name("1"), _run_name("2"), _run_name("3"), _run_name("4")
    hot_running = _run_name("5")
    s = Sweep(tmp_path / "pressure", [
        (p4, "false", _iso_ago(5 * 3600), gb),
        (p2, "false", _iso_ago(20 * 3600), gb),
        (p1, "false", _iso_ago(30 * 3600), gb),
        (p3, "false", _iso_ago(10 * 3600), gb),
        (hot_running, "true", _iso_ago(40 * 3600), gb),
        ("fabro-fabro-1", "false", _iso_ago(40 * 3600), gb),
    ], pct=80, steps=(65, 55))
    r = s.run()
    ck("sweep pressure: removes oldest first, then stops below the target", f"{p1}\n{p2}", cat(s.rm))
    ck("sweep pressure: never removes a running or non-ULID container", "0",
       count(f"^{hot_running}$|fabro-fabro-1", file=s.rm, flags="E"))
    ck("sweep pressure: the output names pressure as the rule", "2", count("(pressure)", r.out))
    ck("sweep pressure: the summary reports the df before and after", "1", count("df 80% -> 55%", r.out))

    # The 30-minute floor holds under pressure, and an unreachable target exits nonzero.
    floor, onehour = _run_name("7"), _run_name("8")
    s = Sweep(tmp_path / "floor", [
        (floor, "false", _iso_ago(10 * 60), gb),
        (onehour, "false", _iso_ago(3600), gb),
    ], pct=90, steps=(55,))
    r = s.run()
    ck("sweep floor: a 10 minute sandbox is kept under pressure", "0", count(f"^{floor}$", file=s.rm))
    ck("sweep floor: the 1h sandbox is still reaped", "1", count(f"^{onehour}$", file=s.rm))
    ck("sweep floor: the output says the floor kept it", "1", count(f"^keep  {floor} .*within 30m floor", r.out))
    ck("sweep floor: reaching the target exits 0", "0", r.rc)
    s = Sweep(tmp_path / "floor-only", [(floor, "false", _iso_ago(10 * 60), gb)], pct=90)
    r = s.run()
    ck("sweep floor: a target the floor blocks fails the pass", "1", r.rc)
    ck("sweep floor: the failure names the target", "1", count("^FAIL  disk pressure: still at .*target 60%", r.out))

    # A dry run removes nothing and says which rule would have taken each sandbox.
    dryold, drynew = _run_name("9"), _run_name("A")
    s = Sweep(tmp_path / "dry", [
        (dryold, "false", _iso_ago(30 * 3600), 30 * gb),
        (drynew, "false", _iso_ago(10 * 60), gb),
    ], pct=75, total_kb=100000000)
    r = s.run(DRY_RUN="1")
    ck("sweep dry run: removes nothing", "0", count("^rm ", r.out))
    ck("sweep dry run: names the pressure rule", "1", count(f"^DRY   {dryold} .*would be removed (pressure)", r.out))
    ck("sweep dry run: names the floor", "1", count(f"^keep  {drynew} .*within 30m floor", r.out))
    ck("sweep dry run: reports freed bytes and the simulated df", "1|1",
       count("would free 32212254720 bytes", r.out) + "|" + count("df 75% -> 43% (simulated)", r.out))
    # A below-threshold dry run simulates its grace removals too, not only pressure ones.
    drygrace = _run_name("B")
    s = Sweep(tmp_path / "dry-grace", [(drygrace, "false", _iso_ago(30 * 3600), gb)],
              pct=50, total_kb=100000000)
    r = s.run(DRY_RUN="1")
    ck("sweep dry run below pressure: reports the simulated df for a grace removal", "1|1",
       count("would free 1073741824 bytes", r.out) + "|" + count("df 50% -> 48% (simulated)", r.out))


# ---------------------------------------------------------------------------
# fabro-monitor.sh: C3/C4 (task 08) -- merge-queue runs neither hide a stuck run nor reset
# the idle clock -- and C3/C8 with drained pools (2026-10-20). docker, curl, df and gh are
# stubs. Only the DRY_RUN output is read.
# ---------------------------------------------------------------------------
MONITOR_CURL = """#!/bin/sh
out=""; url=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; http*) url="$1" ;; esac; shift; done
case "$url" in
  */system/info) body='{"runs":{"total":1}}' ;;
  */automations) body=$(cat "$MM_AUTOS") ;;
  */runs\\?status=*) body=$(cat "$MM_ACTIVE") ;;
  */runs\\?*) body=$(cat "$MM_NEWEST") ;;
  # the scheduler answers only when a test wrote its pools; otherwise C7 fires and C8 skips
  */health) [ -f "$MM_POOLS" ] || { printf '000'; exit 7; }; if [ -f "$MM_DISK" ]; then body=$(cat "$MM_DISK"); else body='{"status":"ok"}'; fi ;;
  */api/pools) [ -f "$MM_POOLS" ] || { printf '000'; exit 7; }; body=$(cat "$MM_POOLS") ;;
  */api/queue) [ -f "$MM_POOLS" ] || { printf '000'; exit 7; }; body=$(cat "$MM_QUEUE") ;;
  *) printf '000'; exit 22 ;;
esac
[ -n "$out" ] && printf '%s' "$body" > "$out" || printf '%s' "$body"
printf '200'
"""
# the monitor targets GNU date (`date -d ISO +%s`); BSD date on a Mac has no -d, so stub just that form.
MONITOR_DATE = """#!/bin/sh
if [ "$1" = -d ] && [ "$3" = +%s ]; then
    python3 -c 'import sys,datetime as d; print(int(d.datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")).timestamp()))' "$2"
else exec /bin/date "$@"; fi
"""


class Monitor:
    def __init__(self, tmp_path):
        self.MM = MM = tmp_path / "monitor"
        stub(MM / "bin", "docker", '#!/bin/sh\ncase "$*" in\n  "inspect "*) echo "running healthy" ;;\n  "exec "*"server.dev-token") echo tok ;;\n  *) exit 0 ;;\nesac\n')
        stub(MM / "bin", "curl", MONITOR_CURL)
        stub(MM / "bin", "date", MONITOR_DATE)
        stub(MM / "bin", "df", '#!/bin/sh\necho "Filesystem 1K Used Avail Use% Mounted\n/dev/x 100 10 90 10% /"\n')
        stub(MM / "bin", "gh", "#!/bin/sh\nexit 1\n")
        write(MM / "autos.json", '{"data":[{"name":"backlog-x","workflow":"backlog","triggers":[{"type":"schedule","id":"s","enabled":true}]}]}')

    @staticmethod
    def ago(seconds):
        t = datetime.datetime.fromtimestamp(int(time.time()) - seconds, datetime.timezone.utc)
        return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    @staticmethod
    def row(run_id, slug, kind, at):
        return jq_n("{id: $id, workflow: {slug: $slug}, lifecycle: {status: {kind: $kind}}, timestamps: {created_at: $at}}",
                    "--arg", "id", run_id, "--arg", "slug", slug, "--arg", "kind", kind, "--arg", "at", at)

    def page(self, name, rows):
        write(self.MM / name, run(["jq", "-cs", "{data: ., meta: {has_more: false}}"], stdin="\n".join(rows) + "\n").out + "\n")

    def run(self, wedge="20"):
        """Run the monitor in DRY_RUN; its stdout goes to a file, as the shell suite read it."""
        MM = self.MM
        run_files(["sh", REPO_ROOT / "ops/fabro-monitor.sh"], MM / "mon", path=f"{MM / 'bin'}:{ORIG_PATH}",
                  env={"MM_AUTOS": MM / "autos.json", "MM_ACTIVE": MM / "active.json", "MM_NEWEST": MM / "newest.json",
                       "MM_POOLS": MM / "pools.json", "MM_QUEUE": MM / "queue.json", "WEDGE_MINUTES": wedge,
                       "MM_DISK": MM / "disk.json",
                       "DRY_RUN": "1", "FABRO_MONITOR_STATE": MM / "state", "FABRO_MONITOR_REPOS": "",
                       "SCHEDULER_URL": "http://127.0.0.1:1"})
        return MM / "mon.out"


def test_fabro_monitor_c3_c4(ck, tmp_path):
    m = Monitor(tmp_path)
    mq30 = lambda kind: [m.row(f"MQ{i}", "merge-queue", kind, m.ago(60)) for i in range(1, 31)]
    # 30 fresh merge-queue rows on top, and a backlog run non-terminal for 5 hours: C4 fires.
    m.page("active.json", mq30("running") + [m.row("BL1", "backlog", "running", m.ago(18000))])
    m.page("newest.json", mq30("succeeded") + [m.row("BL1", "backlog", "running", m.ago(18000))])
    out = m.run()
    ck("monitor C4: 30 newer merge-queue rows do not hide a 5 hour backlog run", "1", count("stuck-run — run BL1", file=out))
    # a merge-queue sweep still running after 40 minutes is stuck (MQ_STUCK_MINUTES=30)
    m.page("active.json", [m.row("MQSTUCK", "merge-queue", "running", m.ago(2400))])
    out = m.run()
    ck("monitor C4: a merge-queue sweep over 30 minutes is stuck", "1", count("stuck-run — run MQSTUCK", file=out))
    m.page("active.json", [m.row("MQFINE", "merge-queue", "running", m.ago(600))])
    out = m.run()
    ck("monitor C4: a 10 minute merge-queue sweep is fine", "0", count("stuck-run — ", file=out))
    # C3: the only recent runs are merge-queue rows, the newest other run is 5 hours old: C3 fires.
    m.page("newest.json", mq30("succeeded") + [m.row("BL1", "backlog", "succeeded", m.ago(18000))])
    write(m.MM / "active.json", '{"data":[],"meta":{"has_more":false}}')
    out = m.run()
    ck("monitor C3: only merge-queue rows are recent, so the scheduler is dead", "1", count("dead-scheduler", file=out))
    m.page("newest.json", mq30("succeeded") + [m.row("BL1", "backlog", "succeeded", m.ago(600))])
    out = m.run()
    ck("monitor C3: a recent backlog run keeps it quiet", "0|1", count("DRY.*dead-scheduler", file=out) + "|" + count("ok    C3", file=out))


# fabro-monitor.sh C3/C8 with drained pools (2026-10-20): the operator drains coder pools to
# use those boxes outside fabro. A full scheduler is not a dead one (C3), and idle undrained
# pools are a wedge even while others are drained (C8). /api/pools and /api/queue are stubs.
def test_fabro_monitor_drained_pools(ck, tmp_path):
    m = Monitor(tmp_path)

    def pool(name, drained, run_id=""):
        if run_id:
            return jq_n("{coder_pool: $p, drained: $d, lease: {run_id: $r}}", "--arg", "p", name, "--argjson", "d", drained, "--arg", "r", run_id)
        return jq_n("{coder_pool: $p, drained: $d, lease: null}", "--arg", "p", name, "--argjson", "d", drained)

    def pools(*ps):
        write(m.MM / "pools.json", run(["jq", "-cs", "."], stdin="\n".join(ps) + "\n").out + "\n")

    # the newest backlog run is 3 hours old; the two undrained pools each hold a live run
    m.page("active.json", [m.row("BLA", "backlog", "running", m.ago(10800)), m.row("BLB", "backlog", "running", m.ago(9000))])
    m.page("newest.json", [m.row("MQ1", "merge-queue", "succeeded", m.ago(60)), m.row("BLB", "backlog", "running", m.ago(9000)),
                           m.row("BLA", "backlog", "running", m.ago(10800))])
    write(m.MM / "queue.json", '[{"number":1}]')
    pools(pool("coders-a", "false", "BLA"), pool("coders-b", "false", "BLB"), pool("coders-c", "true"), pool("coders-d", "true"))
    out = m.run()
    ck("monitor C3: two drained, two leased on live runs, no run for 2h: full, not dead", "0|1",
       count("DRY.*dead-scheduler", file=out) + "|" + count("ok    C3 dead-scheduler: .*scheduler is full (2 undrained pool(s) leased, 2 drained)", file=out))
    ck("monitor C8: both undrained pools busy is not a wedge", "1", count("ok    C8 scheduler-wedged: 1 queued, 0 of 2 undrained pools idle (2 drained)", file=out))
    # a lease on a run fabro no longer lists as active is a wedge, so C3 fires
    pools(pool("coders-a", "false", "BLA"), pool("coders-b", "false", "GONE"), pool("coders-c", "true"), pool("coders-d", "true"))
    out = m.run()
    ck("monitor C3: a lease on a finished run does not count as busy", "1", count("DRY.*dead-scheduler", file=out))
    # an undrained idle pool is not full either
    pools(pool("coders-a", "false", "BLA"), pool("coders-b", "false"), pool("coders-c", "true"), pool("coders-d", "true"))
    out = m.run()
    ck("monitor C3: an idle undrained pool with no new run for 2h fires", "1", count("DRY.*dead-scheduler", file=out))
    # every pool drained: the operator stopped dispatch, so neither C3 nor C8 fires
    pools(pool("coders-a", "true"), pool("coders-b", "true"), pool("coders-c", "true"), pool("coders-d", "true"))
    out = m.run(wedge="0")
    ck("monitor C3/C8: every pool drained fires neither", "0|0", count("DRY.*dead-scheduler", file=out) + "|" + count("DRY.*scheduler-wedged", file=out))
    # two drained, two undrained and idle, work queued: a wedge (any drained pool used to hide it)
    pools(pool("coders-a", "false"), pool("coders-b", "false"), pool("coders-c", "true"), pool("coders-d", "true"))
    out = m.run(wedge="0")
    ck("monitor C8: two undrained pools idle with work queued is a wedge while two are drained", "1",
       count("DRY.*scheduler-wedged — 1 item(s) queued and all 2 undrained coder pool(s) idle .*; 2 drained", file=out))
    # the same with an empty queue is not
    write(m.MM / "queue.json", "[]")
    out = m.run(wedge="0")
    ck("monitor C8: idle undrained pools with nothing queued is fine", "0", count("DRY.*scheduler-wedged", file=out))
    # the scheduler unreachable: C3 judges by run age alone, and C8 skips
    (m.MM / "pools.json").unlink()
    out = m.run()
    ck("monitor C3: an unreadable /api/pools falls back to run age", "1|1", count("DRY.*dead-scheduler", file=out) + "|" + count("skip  C8", file=out))


# A scheduler paused by its disk gate (issue #51) is not wedged. C8 reads
# `/health`'s `disk.gated` and reports the pause instead of firing.
def test_fabro_monitor_disk_gate(ck, tmp_path):
    m = Monitor(tmp_path)
    m.page("active.json", [])
    m.page("newest.json", [m.row("BL1", "backlog", "running", m.ago(600))])
    write(m.MM / "pools.json", '[{"coder_pool":"coders-a","drained":false,"lease":null}]')
    write(m.MM / "queue.json", '[{"repo":"o/a","number":1}]')
    write(m.MM / "disk.json",
          '{"status":"ok","disk":{"gated":true,"free_gb":40.0,"min_free_gb":50,"hysteresis_gb":10}}')
    out = m.run(wedge="0")
    ck("monitor C8: a disk-gated scheduler does not fire wedged", "0", count("DRY.*scheduler-wedged", file=out))
    ck("monitor C8: the gate state is reported", "1",
       count(r"dispatch paused by the disk gate \(40(\.0)? GB free, minimum 50 GB; 1 queued\)",
             file=out, flags="E"))
    # Above the gate, the same state is a wedge again.
    write(m.MM / "disk.json",
          '{"status":"ok","disk":{"gated":false,"free_gb":100.0,"min_free_gb":50,"hysteresis_gb":10}}')
    out = m.run(wedge="0")
    ck("monitor C8: an open disk gate restores the wedge alert", "1", count("DRY.*scheduler-wedged", file=out))
