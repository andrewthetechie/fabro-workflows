#!/usr/bin/env python3.11
"""Tool-use metrics (C7, docs/coder-tweaks) from `fabro events <run> --json` files.

Usage: fabro-agent-tools.py [--local-only] [--since ISO] EVENTS.jsonl...

Standard library only. Runs on the Mac and on the host, never in a sandbox.
One events file per run. Event files carry issue text: never commit them.

Definitions (docs/coder-tweaks/00-overview-and-contracts.md, C7):
  local model     a stage visit whose AssistantMessage model id starts with `coders-`.
  --local-only    restricts M1-M3, M5, M7 and the per-stage table to local visits.
                  M4 (git), M6 (memory) and M8 (review) always cover every stage.
  M1  code-index share of searches: (code_* tools + shell `fabro-code`) over those
      plus native grep plus shell grep/rg, counted per shell command segment.
      Also reported without the grep/rg segments that read a pipe (`cargo test |
      grep ok`): no index can replace a stdin filter. `| xargs grep` still counts.
  M2  read_file output bytes per stage visit.
  M3  share of read_file calls with neither `offset` nor `limit` (whole files).
      Also reported by bytes, and as whole reads of 12 KB or more per visit: reading
      a 4 KB file whole is the right call, so the count alone overstates it.
  M4  mutating git run outside the exempt stages (C2's rule). A call whose result is
      an error that names a hook or guard is reported separately as blocked.
  M5  improve visits that edited a tracked file: an edit_file/write_file outside
      /tmp/, or a mutating git command. A proxy: the events carry no diff, so this
      is the attempt, not the checkpoint. After task 06 the tree is reverted anyway.
  M6  agent sessions whose agent.memory.loaded lists .codex/instructions.md.
  M7  median seconds from a `coder` stage.started to its first edit_file/write_file.
  M8  review_gate first-pass rate (first review_gate edge of a task goes to
      `integrate`) and rework_t* visits per task.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".fabro/workflows/_io/manifest.json"
DEFAULT_EXEMPT = {"resolve_merge", "rebase_agent_t1", "rebase_agent_t2"}

READ_ONLY = {
    "status", "diff", "log", "show", "blame", "grep", "ls-files", "ls-tree", "rev-parse",
    "merge-base", "cat-file", "range-diff", "describe", "shortlog", "rev-list", "diff-tree",
    "name-rev", "for-each-ref", "check-ignore", "version", "help",
}
BRANCH_LISTING = {
    "-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--show-current", "--no-color", "--color", "-i", "--ignore-case",
}
# Global git options that take a separate value.
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
PREFIX_WORDS = {"time", "env", "nice", "nohup", "sudo", "command", "exec"}
# Shell reserved words that put the next word at command position (`then git stash`).
# Kept in step with KEYWORDS in ops/fabro-io/src/gitguard.rs.
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!"}
# fabro hands a hook's block reason to the agent unchanged, and every git-guard reason
# starts with "git-guard: " (REASON_PREFIX in gitguard.rs).
BLOCKED_RE = re.compile(r"git-guard|blocked by (a )?hook|hook (blocked|denied)|denied by", re.I)
VIEW_WORDS = ("head", "tail", "cat", "sed", "python", "python3")


def exempt_stages() -> set[str]:
    """Stage ids carrying `"git": "write"` in the manifest, else C2's three."""
    try:
        data = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return set(DEFAULT_EXEMPT)
    found: set[str] = set()
    saw_field = False
    for section in ("workflows", "phases"):
        for body in (data.get(section) or {}).values():
            for sid, stage in (body.get("stages") or {}).items():
                if "git" in stage:
                    saw_field = True
                    if stage["git"] == "write":
                        found.add(sid)
    return found if saw_field else set(DEFAULT_EXEMPT)


def split_segments(command: str) -> list[list[str]]:
    """Split a shell command into word lists at && || ; | newline ( ) and `$(`.

    Quote aware: a separator inside quotes, or escaped by a backslash (the `\\|` of a
    grep alternation), does not split. Words keep their quotes stripped.
    """
    return [s for s, _ in split_segments_piped(command)]


def split_segments_piped(command: str) -> list[tuple[list[str], bool]]:
    """split_segments, with whether each segment reads a single `|` pipe."""
    segs: list[list[str]] = [[]]
    piped: list[bool] = [False]
    word: list[str] = []
    has_word = False
    quote = ""
    i, n = 0, len(command)

    def end_word() -> None:
        nonlocal word, has_word
        if has_word:
            segs[-1].append("".join(word))
        word, has_word = [], False

    def end_seg(pipe: bool = False) -> None:
        end_word()
        if segs[-1]:
            segs.append([])
            piped.append(pipe)
        else:
            piped[-1] = piped[-1] or pipe  # `a | (grep x)` and `a |\ngrep x`

    while i < n:
        c = command[i]
        if quote:
            if c == quote:
                quote = ""
            elif c == "\\" and quote == '"' and i + 1 < n:
                word.append(command[i + 1])
                i += 1
            else:
                word.append(c)
        elif c in "'\"":
            quote, has_word = c, True
        elif c == "\\" and i + 1 < n:
            word.append(command[i + 1])
            has_word = True
            i += 1
        elif c in ";\n(){}`":
            end_seg()
        elif c == "$" and command[i + 1 : i + 2] == "(":
            end_seg()
            i += 1
        elif c in "&|":
            double = command[i + 1 : i + 2] == c
            end_seg(pipe=c == "|" and not double)
            if double:
                i += 1
        elif c.isspace():
            end_word()
        else:
            word.append(c)
            has_word = True
        i += 1
    end_word()
    return [(s, p) for s, p in zip(segs, piped) if s]


def strip_prefix(words: list[str]) -> list[str]:
    """Drop `timeout N`, `uv run`, `xargs [opts]`, env assignments, reserved words and the like."""
    w = list(words)
    while w:
        head = w[0].rsplit("/", 1)[-1]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]) or head in PREFIX_WORDS or w[0] in KEYWORDS:
            w = w[1:]
        elif head == "timeout":
            w = w[1:]
            while w and w[0].startswith("-"):
                w = w[1:]
            w = w[1:]  # the duration
        elif head == "uv" and len(w) > 1 and w[1] == "run":
            w = w[2:]
            while w and w[0].startswith("-"):
                w = w[1:]
        elif head == "xargs":
            w = w[1:]
            while w and w[0].startswith("-"):
                w = w[1:]
        else:
            break
    return w


def commands(command: str) -> list[list[str]]:
    """The command words of each segment, with prefixes dropped and `cd X` removed."""
    return [w for w, _ in commands_piped(command)]


def commands_piped(command: str) -> list[tuple[list[str], bool]]:
    """commands, with whether each one filters its stdin: it reads a `|` pipe and is
    not run by xargs (`find . | xargs grep -l X` searches files, not the pipe)."""
    out = []
    for seg, piped in split_segments_piped(command):
        w = strip_prefix(seg)
        if not w:
            continue
        if w[0].rsplit("/", 1)[-1] == "cd":
            continue
        w[0] = w[0].rsplit("/", 1)[-1]
        out.append((w, piped and "xargs" not in (x.rsplit("/", 1)[-1] for x in seg[: len(seg) - len(w)])))
    return out


def git_is_mutating(words: list[str]) -> str | None:
    """The mutating subcommand of a `git ...` word list, or None when read-only."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GIT_VALUE_OPTS else 1
    if i >= len(words):
        return None
    sub, args = words[i], words[i + 1 :]
    if sub in READ_ONLY:
        return None
    if sub == "stash":
        return None if args and args[0] in ("list", "show") else "stash"
    if sub == "branch":
        takes_value = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--abbrev"}
        i = 0
        while i < len(args):
            a = args[i]
            if a in takes_value:
                i += 2
                continue
            if not (a in BRANCH_LISTING or a.startswith(("--format", "--sort", "--abbrev"))):
                return "branch"
            i += 1
        return None
    if sub == "worktree":
        return None if args and args[0] == "list" else "worktree"
    if sub == "config":
        return None if args and args[0].startswith("--get") else "config"
    return sub


def ts_seconds(s: str) -> float:
    base, _, frac = s.rstrip("Z").partition(".")
    t = datetime.fromisoformat(base).timestamp()
    return t + (float("0." + frac) if frac else 0.0)


class Visit:
    __slots__ = ("run", "stage_id", "node", "models", "started", "tools")

    def __init__(self, run: str, stage_id: str, node: str) -> None:
        self.run, self.stage_id, self.node = run, stage_id, node
        self.models: Counter[str] = Counter()
        self.started: float | None = None
        self.tools: list[dict] = []

    @property
    def local(self) -> bool:
        return any(m.startswith("coders-") for m in self.models)


def load_run(path: Path, since: str | None):
    visits: dict[str, Visit] = {}
    pending: dict[str, dict] = {}
    edges: list[tuple[str, str]] = []
    memory: list[bool] = []
    created = ""
    origin = ""
    run_id = path.stem
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("event", "")
            if kind == "run.created":
                created = ev.get("ts", "")
                git = (ev.get("properties") or {}).get("git") or {}
                origin = str(git.get("origin_url") or "")
                continue
            props = ev.get("properties") or {}
            sid = ev.get("stage_id") or props.get("stage_id") or ""
            node = ev.get("node_id") or ""

            def visit() -> Visit:
                v = visits.get(sid)
                if v is None:
                    v = visits[sid] = Visit(run_id, sid, node)
                return v

            if kind == "stage.started":
                v = visit()
                v.started = ts_seconds(ev["ts"])
            elif kind == "agent.message":
                msg = (props.get("event") or {}).get("AssistantMessage") or {}
                if msg.get("model"):
                    visit().models[msg["model"]] += 1
            elif kind == "agent.tool.started":
                tc = (props.get("event") or {}).get("ToolCallStarted") or {}
                rec = {
                    "name": tc.get("tool_name", ""),
                    "args": tc.get("arguments") or {},
                    "ts": ts_seconds(ev["ts"]),
                    "output": "",
                    "error": False,
                }
                pending[tc.get("tool_call_id", "")] = rec
                visit().tools.append(rec)
            elif kind == "agent.tool.completed":
                tc = (props.get("event") or {}).get("ToolCallCompleted") or {}
                rec = pending.pop(tc.get("tool_call_id", ""), None)
                if rec is not None:
                    out = tc.get("output")
                    rec["output"] = out if isinstance(out, str) else json.dumps(out or "")
                    rec["error"] = bool(tc.get("is_error"))
            elif kind == "edge.selected":
                edges.append((props.get("from_node", ""), props.get("to_node", "")))
            elif kind == "agent.memory.loaded":
                mem = (props.get("event") or {}).get("MemoryLoaded") or {}
                paths = [f.get("path", "") for f in mem.get("files", [])]
                memory.append(any(p.endswith(".codex/instructions.md") for p in paths))
    if since and created and created < since:
        return None
    return {"id": run_id, "repo": repo_of(origin), "visits": list(visits.values()), "edges": edges,
            "memory": memory}


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


# M-env: a Test environment's service is not running (docs/test-env, C7). Port 5432 is the
# only port the target repositories' Test environments declare today.
ENV_RE = re.compile(
    r"ConftestImportFailure"
    r"|port 5432 failed: Connection refused"
    r"|ECONNREFUSED (?:127\.0\.0\.1|localhost):5432"
    r"|Connect call failed \('(?:127\.0\.0\.1|localhost)', 5432\)"
)


def repo_of(origin: str) -> str:
    """`owner/name` from a run's origin URL (https or ssh), or `unknown`."""
    if not origin:
        return "unknown"
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", origin)
    return m.group(1) if m else origin


def test_kind(words: list[str]) -> str | None:
    """`test` for a direct test runner (M-run's denominator), `fabro` for the
    Test environment's own entry points, None otherwise."""
    head = words[0]
    if head == "fabro-test" or (head == "fabro-io" and words[1:2] == ["run-tests"]):
        return "fabro"
    if head in ("pytest", "vitest") or (head == "npx" and words[1:2] == ["vitest"]):
        return "test"
    if head in ("python", "python3") and words[1:3] == ["-m", "pytest"]:
        return "test"
    if head == "cargo" and words[1:2] == ["test"]:
        return "test"
    if head in ("npm", "bun"):
        if head == "npm" and words[1:2] == ["test"]:
            return "test"
        if "run" in words[1:]:
            after = words[words.index("run") + 1 :]
            script = next((w for w in after if not w.startswith("-")), "")
            if script.startswith("test"):
                return "test"
    return None


def env_hit(v: Visit) -> bool:
    return any(ENV_RE.search(t["output"]) for t in v.tools)


def report(runs: list[dict], local_only: bool) -> str:
    exempt = exempt_stages()
    lines: list[str] = []
    all_visits = [v for r in runs for v in r["visits"]]
    scoped = [v for v in all_visits if v.tools and (v.local or not local_only)]
    local_visits = [v for v in all_visits if v.tools and v.local]

    # M1 - search share
    code_idx = native_grep = shell_grep = shell_filter = 0
    shell_words: Counter[str] = Counter()
    native: Counter[str] = Counter()
    for v in scoped:
        for t in v.tools:
            native[t["name"]] += 1
            if t["name"].startswith("mcp__io__code_"):
                code_idx += 1
            elif t["name"] == "grep":
                native_grep += 1
            elif t["name"] == "shell":
                for w, filters in commands_piped(str(t["args"].get("command", ""))):
                    shell_words[w[0]] += 1
                    if w[0] == "fabro-code":
                        code_idx += 1
                    elif w[0] in ("grep", "rg", "egrep", "fgrep"):
                        shell_grep += 1
                        shell_filter += filters
    searches = code_idx + native_grep + shell_grep
    lines += [
        "M1  code-index share of searches",
        f"    code_* + fabro-code {code_idx}; native grep {native_grep}; shell grep/rg {shell_grep}"
        f" (grep {shell_words['grep']}, rg {shell_words['rg']}; {shell_filter} read a pipe)",
        f"    share {pct(code_idx, searches)}; without pipe filters {pct(code_idx, searches - shell_filter)}",
        "",
    ]

    # M2 / M3 - read_file
    reads = [t for v in scoped for t in v.tools if t["name"] == "read_file"]
    rbytes = sum(len(t["output"].encode()) for t in reads)
    wholes = [len(t["output"].encode()) for t in reads if "limit" not in t["args"] and "offset" not in t["args"]]
    big = [b for b in wholes if b >= 12 * 1024]
    nv = max(len(scoped), 1)
    lines += [
        "M2  read_file bytes per stage visit",
        f"    {len(reads)} calls, {rbytes} bytes over {len(scoped)} stage visits"
        f" = {rbytes // nv} bytes per visit",
        "M3  whole-file share of read_file",
        f"    {len(wholes)} of {len(reads)} = {pct(len(wholes), len(reads))};"
        f" by bytes {pct(sum(wholes), rbytes)}",
        f"    whole reads >= 12 KB: {len(big)} = {len(big) / nv:.2f} per visit,"
        f" {sum(big) // nv} bytes per visit",
        "",
    ]

    # M4 - mutating git, every stage
    executed: list[tuple[str, str, str]] = []
    blocked: list[tuple[str, str, str]] = []
    stages_hit: set[tuple[str, str]] = set()
    by_cmd: Counter[str] = Counter()
    for v in all_visits:
        if v.node.rsplit(".", 1)[-1] in exempt:
            continue
        for t in v.tools:
            if t["name"] != "shell":
                continue
            bad = [g for w in commands(str(t["args"].get("command", ""))) if w[0] == "git" and (g := git_is_mutating(w))]
            if not bad:
                continue
            was_blocked = t["error"] and BLOCKED_RE.search(t["output"])
            (blocked if was_blocked else executed).append((v.run, v.node, ",".join(bad)))
            if not was_blocked:
                stages_hit.add((v.run, v.stage_id))
                by_cmd.update(bad)
    per_node = Counter(n for _, n, _ in executed)
    lines += [
        "M4  mutating git executed outside exempt stages (all stages, all models)",
        f"    exempt: {', '.join(sorted(exempt))}",
        f"    executed {len(executed)} shell call(s) in {len(stages_hit)} stage visit(s);"
        f" by command {dict(by_cmd)}; by stage {dict(per_node)}",
        f"    blocked attempts (reported separately): {len(blocked)}",
        "",
    ]

    # M5 - improve visits that edited tracked files
    improves = [v for v in all_visits if v.node.rsplit(".", 1)[-1] == "improve" and v.tools and (v.local or not local_only)]
    touched = 0
    for v in improves:
        edited = False
        for t in v.tools:
            if t["name"] in ("edit_file", "write_file"):
                p = str(t["args"].get("file_path") or t["args"].get("path") or "")
                if not p.startswith("/tmp/"):
                    edited = True
            elif t["name"] == "shell":
                edited = edited or any(w[0] == "git" and git_is_mutating(w) for w in commands(str(t["args"].get("command", ""))))
        touched += edited
    lines += [
        "M5  improve visits that edited a tracked file (attempt-based proxy)",
        f"    {touched} of {len(improves)} improve visits",
        "",
    ]

    # M6 - agent guide loaded
    mem = [m for r in runs for m in r["memory"]]
    lines += [
        "M6  sessions whose agent.memory.loaded lists .codex/instructions.md",
        f"    {sum(mem)} of {len(mem)} = {pct(sum(mem), len(mem))}",
        "",
    ]

    # M7 - coder start to first edit
    deltas: list[float] = []
    no_edit = 0
    for v in scoped:
        if v.node != "coder" or v.started is None:
            continue
        first = min((t["ts"] for t in v.tools if t["name"] in ("edit_file", "write_file")), default=None)
        if first is None:
            no_edit += 1
        else:
            deltas.append(first - v.started)
    med = f"{statistics.median(deltas) / 60:.1f} min" if deltas else "n/a"
    lines += [
        "M7  coder start to first edit (median)",
        f"    {med} over {len(deltas)} coder visit(s) with an edit; {no_edit} with none",
        "",
    ]

    # M8 - review outcomes and reworks
    tasks = first_pass = 0
    rework: Counter[str] = Counter()
    for r in runs:
        waiting = False
        for frm, to in r["edges"]:
            if frm == "next_task" and to == "improve":
                tasks += 1
                waiting = True
            elif frm == "review_gate" and waiting:
                waiting = False
                first_pass += to == "integrate"
        for v in r["visits"]:
            m = re.match(r"^(?:.*\.)?(rework_t\d+)$", v.node)
            if m:
                rework[m.group(1)] += 1
    lines += [
        "M8  review first-pass rate and rework escalations",
        f"    {first_pass} of {tasks} tasks approved at the first review_gate = {pct(first_pass, tasks)}",
        f"    rework visits {dict(sorted(rework.items()))}; per task {sum(rework.values()) / max(tasks, 1):.2f}",
        "",
    ]

    # M-env - a Test environment's service refused a connection, per repository
    lines.append("M-env  Test-environment refusals (port 5432 or ConftestImportFailure) in tool output")
    per_repo: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # visits hit, visits, runs hit, runs
    env_stages: Counter[str] = Counter()
    for r in runs:
        tooled = [v for v in r["visits"] if v.tools]
        hits = [v for v in tooled if env_hit(v)]
        acc = per_repo[r["repo"]]
        acc[0] += len(hits)
        acc[1] += len(tooled)
        acc[2] += bool(hits)
        acc[3] += 1
        env_stages.update(v.node for v in hits)
    all_hits = sum(a[0] for a in per_repo.values())
    all_tooled = sum(a[1] for a in per_repo.values())
    lines.append(f"    all repositories: {all_hits} of {all_tooled} stage visit(s) = {pct(all_hits, all_tooled)}")
    for repo in sorted(per_repo):
        h, n, hr, nr = per_repo[repo]
        lines.append(f"    {repo}: {h} of {n} stage visit(s) = {pct(h, n)}; {hr} of {nr} run(s)")
    lines += [f"    by stage {dict(sorted(env_stages.items()))}", ""]

    # M-run - test invocations that go through run_tests or fabro-test
    via = total = 0
    mech: Counter[str] = Counter()
    for v in all_visits:
        for t in v.tools:
            if t["name"] == "run_tests" or (t["name"] == "baseline_check" and t["args"].get("target")):
                total += 1
                via += 1
                mech[t["name"]] += 1
            elif t["name"] == "shell":
                for w in commands(str(t["args"].get("command", ""))):
                    kind = test_kind(w)
                    if kind is None:
                        continue
                    total += 1
                    if kind == "fabro":
                        via += 1
                        mech["fabro-test"] += 1
                    else:
                        mech["direct " + w[0]] += 1
    lines += [
        "M-run  test invocations that go through run_tests or fabro-test",
        f"    {via} of {total} = {pct(via, total)}; by entry point {dict(sorted(mech.items()))}",
        "    test invocations: run_tests, baseline_check with a target, fabro-test, and direct pytest,"
        " vitest, cargo test, npm/bun test",
        "",
    ]

    # per-stage tool table
    table: dict[str, Counter[str]] = defaultdict(Counter)
    visit_count: Counter[str] = Counter()
    for v in scoped:
        visit_count[v.node] += 1
        for t in v.tools:
            name = t["name"]
            if name == "shell":
                for w in commands(str(t["args"].get("command", ""))):
                    word = w[0]
                    if word == "fabro-code":
                        table[v.node]["fabro-code"] += 1
                    elif word in ("grep", "rg"):
                        table[v.node]["sh grep/rg"] += 1
                    elif word in VIEW_WORDS:
                        table[v.node]["sh view"] += 1
                    elif word == "git":
                        table[v.node]["sh git"] += 1
                    else:
                        table[v.node]["sh other"] += 1
            elif name.startswith("mcp__io__code_"):
                table[v.node]["code_*"] += 1
            elif name in ("grep", "read_file", "edit_file", "write_file", "glob"):
                table[v.node][name] += 1
            elif name in ("mcp__io__inputs", "mcp__io__submit"):
                table[v.node]["inputs/submit"] += 1
            else:
                table[v.node]["other"] += 1
    cols = ["read_file", "grep", "glob", "edit_file", "write_file", "code_*", "fabro-code", "sh grep/rg",
            "sh view", "sh git", "sh other", "inputs/submit", "other"]
    lines.append("Per-stage tool calls" + (" (local-model visits only)" if local_only else ""))
    lines.append("    " + "stage".ljust(24) + "visits".rjust(7) + "".join(c.rjust(14) for c in cols))
    for node in sorted(table):
        lines.append("    " + node.ljust(24) + str(visit_count[node]).rjust(7)
                     + "".join(str(table[node][c]).rjust(14) for c in cols))
    lines += [
        "",
        f"runs {len(runs)}; stage visits with tools {len([v for v in all_visits if v.tools])};"
        f" local-model visits {len(local_visits)}; tool calls in scope {sum(len(v.tools) for v in scoped)}",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="C7 tool-use metrics from fabro events files.")
    ap.add_argument("--local-only", action="store_true", help="restrict M1-M3, M5, M7 and the table to coders-* visits")
    ap.add_argument("--since", help="ISO timestamp: skip runs created before it")
    ap.add_argument("events", nargs="+", help="one `fabro events <run> --json` file per run")
    args = ap.parse_args(argv)
    runs = []
    for name in args.events:
        try:
            run = load_run(Path(name), args.since)
        except OSError as e:
            print(f"cannot read {name}: {e}", file=sys.stderr)
            return 1
        if run is not None:
            runs.append(run)
    print(report(runs, args.local_only))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
