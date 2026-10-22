"""The harness the gate tests share: extract a node's script, stage it, run it, read results.

Each command node's `script` attribute is extracted from its graph verbatim, `/tmp/fabro` is
rebased onto a scratch directory, and the script runs under `sh` (dash, when installed: the
profile images are Debian, where /bin/sh is dash) against fixtures. Nothing here needs a
host, a container or the network.

Fidelity rule: a check that reads a result with `jq` or `grep` calls the real tool with the
original filter or pattern, never a Python re-implementation. A mistranslated pattern makes
an expected-"0" count pass vacuously; the real tool cannot drift.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WF = REPO_ROOT / ".fabro" / "workflows"
GRAPH = WF / "backlog" / "workflow.fabro"
SHARED = WF / "_shared" / "review-merge" / "review-merge.fabro"
SHARED_TRIAGE = WF / "_shared" / "triage" / "triage.fabro"
ARCH = WF / "arch-review" / "workflow.fabro"
PR = WF / "pr-review" / "workflow.fabro"
DEP = WF / "dependabot" / "workflow.fabro"
INTRIAGE = WF / "issue-triage" / "workflow.fabro"
MQ = WF / "merge-queue" / "workflow.fabro"
SCOUT = WF / "scout" / "workflow.fabro"
RAPH = WF / "ralph" / "workflow.fabro"


# ---------------------------------------------------------------------------
# The environment every subprocess runs under.
# ---------------------------------------------------------------------------

def _git_local_env_vars() -> list[str]:
    out = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True)
    return out.stdout.split()


def base_env() -> dict[str, str]:
    """os.environ minus every repository-local git variable, with signing off.

    Git exports GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and more to its hooks. Inherited, they
    point every fixture's `git init`/`commit`/`push` at the real checkout, whose `origin` is
    GitHub, where a push to main deploys (2026-10-10: a pre-push hook rewrote main locally and
    on origin). And an operator's global `commit.gpgsign=true` signs every fixture commit:
    ~240ms each instead of ~24ms. No sandbox signs. GIT_CONFIG_COUNT is itself one of the
    local variables, so it is set after the scrub.
    """
    env = dict(os.environ)
    for name in _git_local_env_vars():
        env.pop(name, None)
    env.update(
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="commit.gpgsign",
        GIT_CONFIG_VALUE_0="false",
        GIT_CONFIG_KEY_1="tag.gpgsign",
        GIT_CONFIG_VALUE_1="false",
    )
    env.pop("VIRTUAL_ENV", None)
    return env


BASE_ENV = base_env()
SHBIN: Path | None = None  # set by conftest: a directory whose `sh` is dash
# The working directory of a run that names none: an empty scratch directory (set by
# conftest), so a node that reads or writes a relative path cannot reach this checkout.
DEFAULT_CWD: Path | None = None
ORIG_PATH = BASE_ENV["PATH"]  # replaced by conftest once the dash shim exists


def set_shbin(d: Path, cwd: Path) -> None:
    global SHBIN, ORIG_PATH, DEFAULT_CWD
    SHBIN = d
    DEFAULT_CWD = cwd
    ORIG_PATH = f"{d}:{BASE_ENV['PATH']}"


def env_for(path: str | None = None, env: dict[str, str] | None = None,
            unset: tuple[str, ...] = ()) -> dict[str, str]:
    e = dict(BASE_ENV)
    e["PATH"] = path if path is not None else ORIG_PATH
    if env:
        e.update({k: str(v) for k, v in env.items()})
    for k in unset:
        e.pop(k, None)
    return e


def which(tool: str, path: str | None = None) -> str:
    found = shutil.which(tool, path=path if path is not None else ORIG_PATH)
    if not found:
        raise RuntimeError(f"{tool} is required")
    return found


# ---------------------------------------------------------------------------
# Extract and stage.
# ---------------------------------------------------------------------------

def extract(graph: Path, node: str) -> str:
    """A node's `script` attribute exactly as the DOT parser sees it.

    The node's first line starts with four spaces, the node id and ` [`. The attribute
    value runs from `script="` to the next unescaped quote; `\\"` unescapes to `"` and every
    other byte is kept as is. Raises if the node or its script is missing.
    """
    pfx = f"    {node} ["
    out: list[str] = []
    st = 0
    done = False

    def scan(s: str) -> bool:
        i = 0
        while i < len(s):
            c = s[i]
            if c == "\\" and s[i + 1:i + 2] == '"':
                out.append('"')
                i += 2
                continue
            if c == '"':
                return True
            out.append(c)
            i += 1
        return False

    for line in graph.read_text().split("\n"):
        if st == 0 and line.startswith(pfx):
            st = 1
        if st == 1:
            k = line.find('script="')
            if k >= 0:
                st = 2
                if scan(line[k + 8:]):
                    done = True
                    break
                continue
        elif st == 2:
            out.append("\n")
            if scan(line):
                done = True
                break
    if not done:
        raise LookupError(f"no script for node {node!r} in {graph}")
    return "".join(out)


def rebase(script: str, root: Path | str) -> str:
    return script.replace("/tmp/fabro", str(root))


def syntax_ok(path: Path) -> tuple[bool, str]:
    r = subprocess.run(["sh", "-n", str(path)], capture_output=True, text=True, env=env_for())
    return r.returncode == 0, r.stderr


def stage(ck, node: str, T: Path, graph: Path = GRAPH, root: Path | str | None = None,
          name: str | None = None, label: str | None = None) -> Path:
    """Write node's script, /tmp/fabro rebased onto root (default T), to T/<name>.sh.

    The staged file is held to the "inline scripts are POSIX sh" invariant: `sh -n`, under
    dash when it is installed. A failure is recorded as a failed check.
    """
    T.mkdir(parents=True, exist_ok=True)
    path = T / f"{name or node}.sh"
    path.write_text(rebase(extract(graph, node), root if root is not None else T))
    ok, err = syntax_ok(path)
    if not ok:
        ck.fail(f"{label or node} is not valid POSIX sh", err)
    return path


# ---------------------------------------------------------------------------
# Run things.
# ---------------------------------------------------------------------------

@dataclass
class Run:
    out: str   # stdout (plus stderr when merged), trailing newlines stripped like $(...)
    rc: int
    err: str = ""


def strip_nl(s: str) -> str:
    return s.rstrip("\n")


def run(argv: list[str] | str, *, cwd: Path | str | None = None, path: str | None = None,
        env: dict[str, str] | None = None, unset: tuple[str, ...] = (), stderr: str = "merge",
        stdin: str | None = None, timeout: float | None = 600) -> Run:
    """Run argv. stderr: "merge" (2>&1), "drop" (2>/dev/null) or "keep" (separate .err)."""
    if isinstance(argv, str):
        argv = [argv]
    e = env_for(path, env, unset)
    r = subprocess.run(
        [str(a) for a in argv],
        cwd=str(cwd if cwd is not None else DEFAULT_CWD),
        env=e,
        input=stdin if stdin is not None else "",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if stderr == "merge" else subprocess.PIPE,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    return Run(strip_nl(r.stdout), r.returncode, r.stderr if stderr == "keep" and r.stderr else "")


def sh(script: Path | str, *args, **kw) -> Run:
    """`sh script args...`; `sh` resolves on the run's PATH, so it is dash when installed."""
    return run(["sh", str(script), *[str(a) for a in args]], **kw)


def sh_c(code: str, *args, **kw) -> Run:
    return run(["sh", "-c", code, *[str(a) for a in args]], **kw)


def lastjson(text: str) -> str:
    """Fabro selects the LAST JSON object from merged stdout+stderr: grep -o '{.*}' | tail -1."""
    last = ""
    for line in text.split("\n"):
        m = re.search(r"\{.*\}", line)
        if m:
            last = m.group(0)
    return last


def jq(filt: str, data: str | None = None, *, file: Path | str | None = None,
       args: list[str] | tuple[str, ...] = (), raw: bool = True, pipe: bool = False,
       strip: bool = True) -> str:
    """`jq [-r] args filt` over a here-string (data, newline appended), a pipe, or a file.

    Returns stdout with trailing newlines stripped; stderr is discarded and a failure gives
    whatever jq printed before it, exactly as $(...) would.
    """
    argv = ["jq"] + (["-r"] if raw else []) + list(args) + [filt]
    if file is not None:
        argv.append(str(file))
        stdin = ""
    elif data is None:
        stdin = ""
    else:
        stdin = (data + "\n") if not pipe else (data + "\n" if data else "")
    r = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env_for())
    return strip_nl(r.stdout) if strip else r.stdout


def wc_c(s: str) -> int:
    """`... | wc -c` of raw output: bytes, trailing newline included."""
    return len(s.encode())


def jq_ok(data: str) -> str:
    """`jq -e . >/dev/null 2>&1 <<<"$data"; echo $?`."""
    r = subprocess.run(["jq", "-e", "."], input=data + "\n", capture_output=True, text=True,
                       env=env_for())
    return str(r.returncode)


def jq_n(filt: str, *args: str, compact: bool = True) -> str:
    """`jq -n[c] args filt`: build a fixture document."""
    argv = ["jq", "-n"] + (["-c"] if compact else []) + list(args) + [filt]
    r = subprocess.run(argv, capture_output=True, text=True, env=env_for())
    if r.returncode != 0:
        raise RuntimeError(f"jq -n failed: {r.stderr}")
    return strip_nl(r.stdout)


def grep(args: list[str] | str, data: str | None = None, *, file: Path | str | None = None,
         pipe: bool = False) -> str:
    """The real grep. data is a here-string (newline appended) unless pipe=True, where an
    empty string is an empty stream. Returns stdout stripped like $(...)."""
    argv = ["grep"] + ([args] if isinstance(args, str) else list(args))
    if file is not None:
        argv.append(str(file))
        stdin = ""
    else:
        d = data or ""
        stdin = (d + "\n") if not pipe else (d + "\n" if d else "")
    r = subprocess.run(argv, input=stdin, capture_output=True, text=True, errors="replace",
                       env=env_for())
    return strip_nl(r.stdout)


def count(pattern: str, data: str | None = None, *, file: Path | str | None = None,
          flags: str = "", pipe: bool = False) -> str:
    """`grep -c[flags] -- pattern` over a here-string, a pipe or a file.

    A missing file gives "" (grep prints only to stderr), as $(grep -c x missing) does.
    """
    args = [f"-c{flags}", "--", pattern]
    return grep(args, data, file=file, pipe=pipe)


# ---------------------------------------------------------------------------
# Files.
# ---------------------------------------------------------------------------

def cat(path: Path | str, default: str = "") -> str:
    """$(cat path): contents with trailing newlines stripped; default when it is unreadable."""
    try:
        return strip_nl(Path(path).read_text(errors="replace"))
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return default


def present(path: Path | str) -> str:
    return "present" if os.path.lexists(path) else "absent"


def write(path: Path | str, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def stub(d: Path | str, name: str, body: str) -> Path:
    """An executable at d/name with body verbatim (a heredoc's text, shebang included)."""
    p = write(Path(d) / name, body)
    p.chmod(0o755)
    return p


def rm(*paths: Path | str) -> None:
    for p in paths:
        p = Path(p)
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif os.path.lexists(p):
            p.unlink()


def rm_glob(d: Path | str, *patterns: str) -> None:
    for pat in patterns:
        for p in Path(d).glob(pat):
            rm(p)


def nonempty(path: Path | str) -> str:
    """`[ -s path ] && echo 1 || echo 0`."""
    try:
        return "1" if Path(path).stat().st_size > 0 else "0"
    except FileNotFoundError:
        return "0"


def diff(a: Path | str, b: Path | str) -> str:
    """$(diff a b): empty when the files are identical."""
    return run(["diff", str(a), str(b)], stderr="drop").out


def head1(path: Path | str) -> str:
    """$(head -1 path)."""
    try:
        return Path(path).read_text(errors="replace").split("\n", 1)[0]
    except FileNotFoundError:
        return ""


def lines(path: Path | str) -> str:
    """$(wc -l < path | tr -d ' ')."""
    try:
        return str(Path(path).read_bytes().count(b"\n"))
    except FileNotFoundError:
        return ""


def nlines(text: str) -> str:
    """$(printf '%s\\n' "$text" | wc -l)-style count of here-string lines (empty is 1)."""
    return str((text + "\n").count("\n"))


# ---------------------------------------------------------------------------
# git.
# ---------------------------------------------------------------------------

def git(*args, cwd: Path | str | None = None, path: str | None = None, check: bool = True,
        env: dict[str, str] | None = None) -> str:
    r = subprocess.run(["git", *[str(a) for a in args]], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, env=env_for(path, env))
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(map(str, args))} failed in {cwd}: {r.stderr}")
    return strip_nl(r.stdout)


def git_rc(*args, cwd: Path | str | None = None) -> int:
    return subprocess.run(["git", *[str(a) for a in args]], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, env=env_for()).returncode


# ---------------------------------------------------------------------------
# A graph's edges, one `from->to condition` line per hop, from the offline parser.
# ---------------------------------------------------------------------------

EDGES_FILTER = (
    '.statements[] | select(.Edge) | .Edge | (([(.attrs // [])[] | select(.[0] == "condition")'
    ' | .[1]] | .[0]) // "") as $c | .nodes as $n | range(0; ($n | length) - 1)'
    ' | "\\($n[.])->\\($n[. + 1]) \\($c)"'
)
_edges_cache: dict[Path, str] = {}


def edges(graph: Path) -> str:
    if graph not in _edges_cache:
        dump = subprocess.run(["python3.11", str(REPO_ROOT / "ops/check-graph-invariants.py"),
                               "--dump", str(graph)], capture_output=True, text=True,
                              env=env_for())
        if dump.returncode != 0:
            raise RuntimeError(f"--dump {graph} failed: {dump.stderr}")
        _edges_cache[graph] = jq(EDGES_FILTER, dump.stdout, pipe=True)
    return _edges_cache[graph]


# ---------------------------------------------------------------------------
# The fabro-io receipt.
# ---------------------------------------------------------------------------

def mkreadok(T: Path, visit: str, *inputs: str) -> None:
    """A valid fabro-io inputs receipt (ADR 0016 C7): stage.json carries the visit and
    served.json records every named input as fully served at that visit."""
    io = T / ".io"
    io.mkdir(parents=True, exist_ok=True)
    (io / "stage.json").write_text(json.dumps(
        {"workflow": "backlog", "node": "test", "visit": visit,
         "started": "2026-01-01T00:00:00Z"}, separators=(",", ":")))
    served = {
        "visit": visit,
        "inputs": {n: {"path": "/tmp/fabro/" + n, "parts": 1, "served": [1], "status": "ok"}
                   for n in inputs},
    }
    (io / "served.json").write_text(json.dumps(served) + "\n")


def set_visit(path: Path, visit: str, text: str | None = None) -> None:
    """Write text (or rewrite path) with `._io.visit` set, as `jq '._io.visit=$v'` does."""
    if text is not None:
        path.write_text(text)
    path.write_text(jq("._io.visit=$v", file=path, args=["--arg", "v", visit], raw=False) + "\n")
