"""The tools the sandboxes carry: the git-guard hook wrapper in every workflow.toml, the
fabro-code index wrapper, and the fabro-io binary. fabro-code needs a real codegraph and
fabro-io a real fabro-io; both are in every profile image and skipped where absent."""

import re
import shutil
import tomllib
from pathlib import Path

import pytest

from harness import (ORIG_PATH, REPO_ROOT, WF, count, git, nlines, run, sh_c, stub, write)


def git_guard_hook(toml: Path) -> str:
    """The git-guard hook's script, as tomllib reads it; a regex over the file if it does
    not parse."""
    try:
        d = tomllib.loads(toml.read_text())
        return next(h["script"] for h in d["run"]["hooks"] if h["id"] == "git-guard")
    except Exception:
        for line in toml.read_text().split("\n"):
            m = re.match(r'^script = "(fabro-io git-guard[^"]*)"$', line)
            if m:
                return m.group(1)
        return ""


# ---------------------------------------------------------------------------
# git-guard hook wrapper (docs/coder-tweaks 05). Any non-zero hook exit but 2 BLOCKS in
# fabro (parse_decision), so a hook whose program is missing or crashing would fail every
# shell call it matches. The wrapper maps every code but 2 to 0. It is extracted from each
# workflow.toml and run, not retyped.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("w", ["backlog", "pr-review", "issue-triage", "arch-review"])
def test_git_guard_hook_wrapper(ck, tmp_path, w):
    hook = git_guard_hook(WF / w / "workflow.toml")
    ck(f"{w}: git-guard hook script found", "1", count("^fabro-io git-guard", hook))
    T = tmp_path / f"gg-{w}"
    (T / "bin").mkdir(parents=True)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    stub(T / "bin", "fabro-io", '#!/bin/sh\necho "{\\"decision\\":\\"block\\"}"; exit 2\n')
    r = sh_c(hook, path=path, stderr="keep")
    ck(f"{w}: git-guard exit 2 blocks", "2", r.rc)
    ck(f"{w}: git-guard block decision on stdout", "1", count('"decision":"block"', r.out))
    for code in (0, 1, 64, 127):
        stub(T / "bin", "fabro-io", f"#!/bin/sh\nexit {code}\n")
        ck(f"{w}: git-guard exit {code} proceeds", "0", sh_c(hook, path=path).rc)
    (T / "bin/fabro-io").unlink()
    ck(f"{w}: no fabro-io on PATH proceeds", "0", sh_c(hook, path=path).rc)


# ---------------------------------------------------------------------------
# box-claim hook wrapper (more-local-inference C3, C4).
#
# fabro treats ANY non-zero hook exit as a block, and an unrunnable sandbox hook reports
# -1, so a fabro-io missing from an older image (exit 127) would stop the first stage of
# every coder-loop run. The wrapper ends `; exit 0`, which is what this test pins: every
# code the command can return, and no binary at all, must let the stage start.
# ---------------------------------------------------------------------------
def box_claim_hook(toml: Path) -> str:
    """The box-claim hook's script, as tomllib reads it."""
    try:
        d = tomllib.loads(toml.read_text())
        return next(h["script"] for h in d["run"]["hooks"] if h["id"] == "box-claim")
    except Exception:
        return ""


@pytest.mark.parametrize("w", ["backlog", "ralph", "pr-review"])
def test_box_claim_hook_wrapper(ck, tmp_path, w):
    hook = box_claim_hook(WF / w / "workflow.toml")
    ck(f"{w}: box-claim hook script found", "1", count("^fabro-io box-claim; exit 0$", hook))
    T = tmp_path / f"bc-{w}"
    (T / "bin").mkdir(parents=True)
    path = f"{T / 'bin'}:{ORIG_PATH}"
    for code in (0, 1, 2, 3, 64, 127):
        stub(T / "bin", "fabro-io", f"#!/bin/sh\nexit {code}\n")
        ck(f"{w}: box-claim exit {code} starts the stage", "0", sh_c(hook, path=path).rc)
    (T / "bin/fabro-io").unlink()
    ck(f"{w}: no fabro-io on PATH starts the stage", "0", sh_c(hook, path=path).rc)


# ---------------------------------------------------------------------------
# fabro-code — the index wrapper (docs/code-context C1/C2/C3/C4).
#
# This needs a REAL codegraph binary, which is present inside every profile image (and
# nowhere on the Mac), so it is skipped unless `codegraph` is on PATH. It exercises the
# exact-resolve rules, the D1 footer, the freshness sync, the tests probe, the map renderer
# and the .git/info/exclude guard against a real index of a real-git fixture -- the only way
# the wrapper's claims about codegraph's behaviour are actually tested.
#
# The fixture:
#   mod_a.py      def get_thing()  +  class Config: FLAG
#   mod_b.py      calls get_thing() and reads Config.FLAG
#   file1.py / file2.py   two different functions both named helper
#   tests/test_mod_a.py and test_root.py   both import mod_a (`tests` finds both)
#   mod_u.py      a multi-line signature holding `|` (the map rendered it as garbage)
#   mod_many.py   25 callers of get_thing (codegraph's callers stopped at 20)
#   app.ts        a class with a `get` method, and a call to `get_thing` that must not
#                 count as a caller of the Python function (cross-language)
#   hooks.ts      `export const useThing = () => ...` (TS declares most functions this
#                 way) and a local const inside a function
# ---------------------------------------------------------------------------
FC_FILES = {
    "mod_a.py": 'class Config:\n    FLAG = "on"\n\ndef get_thing():\n    return Config.FLAG\n',
    "mod_b.py": "from mod_a import get_thing, Config\n\ndef call_it():\n    return get_thing() and Config.FLAG\n",
    "file1.py": "def helper(a):\n    return a + 1\n",
    "file2.py": "def helper(a, b):\n    return a + b\n",
    "tests/test_mod_a.py": 'from mod_a import get_thing\n\ndef test_get_thing():\n    assert get_thing() == "on"\n',
    "test_root.py": 'import mod_a\n\ndef test_root():\n    assert mod_a.get_thing() == "on"\n',
    "mod_u.py": "from mod_a import get_thing\n\ndef union_thing(\n    a: int | None,\n    b: str | None = None,\n) -> int | None:\n    return get_thing() and a\n",
    "mod_many.py": "from mod_a import get_thing\n" + "".join(f"\ndef many_{i}():\n    return get_thing()\n" for i in range(1, 26)),
    "frontend/app.ts": "export class App {\n  get(key: string): string {\n    return key;\n  }\n}\n\nexport function useIt(): unknown {\n  return get_thing();\n}\n",
    "frontend/hooks.ts": "export const useThing = (key: string): string => {\n  const inner = key.trim();\n  return inner;\n};\n",
}


def test_fabro_code(ck, tmp_path):
    if not shutil.which("codegraph", path=ORIG_PATH):
        pytest.skip("no codegraph on PATH; present in profile images")
    fc = shutil.which("fabro-code", path=ORIG_PATH) or str(REPO_ROOT / "ops/profile-images/fabro-code")
    T = tmp_path / "fabrocode"
    T.mkdir()
    state, fmap = T / "code_index", T / "repomap.md"
    env = {"FABRO_CODE_STATE": state, "FABRO_CODE_MAP": fmap}
    up, wt = T / "up", T / "wt"

    def fc_repo():
        """A fresh fixture repo; fabro-code resolves and syncs against the working tree, so
        the index is built in place."""
        shutil.rmtree(up, ignore_errors=True); shutil.rmtree(wt, ignore_errors=True)
        (up / "frontend").mkdir(parents=True)
        git("-C", up, "init", "-q", "-b", "main", ".")
        git("-C", up, "config", "user.email", "t@t"); git("-C", up, "config", "user.name", "t")
        for name, text in FC_FILES.items():
            write(up / name, text)
        git("-C", up, "add", "-A"); git("-C", up, "commit", "-qm", "init")
        git("clone", "-q", up, wt)
        git("-C", wt, "config", "user.email", "t@t"); git("-C", wt, "config", "user.name", "t")
        # C4: the .git/info/exclude guard prep writes before it builds the index.
        with open(wt / ".git/info/exclude", "a") as f:
            f.write(".codegraph/\n")
        for p in (state, fmap):
            p.unlink(missing_ok=True)

    def fc_run(*args):
        return run([fc, *args], cwd=wt, env=env)

    # 1. Exact resolution of a unique function.
    fc_repo()
    fc_run("index")
    r = fc_run("def", "get_thing")
    ck("fc: def get_thing one line", "1", nlines(r.out))
    ck("fc: def get_thing rc=0", "0", r.rc)

    # 2. A class attribute is not a resolvable definition.
    r = fc_run("def", "FLAG")
    ck("fc: def FLAG rc=1", "1", r.rc)
    ck("fc: def FLAG says fields not indexed", "1", count("fields and attributes are not indexed", r.out))

    # 3. An ambiguous name lists every path and chooses none.
    r = fc_run("def", "helper")
    ck("fc: def helper ambiguous rc=1", "1", r.rc)
    ck("fc: def helper lists file1", "1", count("file1.py", r.out))
    ck("fc: def helper lists file2", "1", count("file2.py", r.out))
    r = fc_run("show", "helper")
    ck("fc: show helper ambiguous rc=1", "1", r.rc)
    ck("fc: show helper lists both", "2", count("file[12]\\.py", r.out))

    # 4. No fuzzy matching: a near-miss name is not found.
    ck("fc: def get_thin rc=1 (no fuzzy)", "1", fc_run("def", "get_thin").rc)

    # 5. callers lists the calling file and carries the D1 footer.
    r = fc_run("callers", "get_thing")
    ck("fc: callers get_thing rc=0", "0", r.rc)
    ck("fc: callers lists mod_b", "1", count("mod_b.py", r.out))
    ck("fc: callers has footer", "1", count("confirm with grep -rnw", r.out))

    # 6. Freshness: an edit with no explicit sync is covered (wrapper syncs).
    write(wt / "mod_c.py", "from mod_a import get_thing\n\ndef also_call():\n    return get_thing()\n")
    ck("fc: callers sees uncommitted edit", "1", count("mod_c.py", fc_run("callers", "get_thing").out))

    # 7. tests probe: a test that imports the module is found.
    r = fc_run("tests", "mod_a.py")
    ck("fc: tests mod_a rc=0", "0", r.rc)
    ck("fc: tests lists test_mod_a", "1", count("test_mod_a.py", r.out))

    # 8. map: <=16,000 bytes, Repo map header, no generated rows.
    fc_run("map")
    ck("fc: map wrote", "1", "1" if fmap.is_file() else "0")
    ck("fc: map <=16000 bytes", "1", "1" if fmap.is_file() and fmap.stat().st_size <= 16000 else "0")
    ck("fc: map header", "1", count("^# Repo map (", file=fmap))

    # 9. C4: the index stays out of every commit (`git add -A` adds nothing).
    porcelain = sh_c("git add -A >/dev/null 2>&1 && git status --porcelain", cwd=wt, stderr="drop").out
    ck("fc: .codegraph not in porcelain", "0", count("\\.codegraph", porcelain, pipe=True))

    # 5b. No 20-row limit, and a same-name call from another language is not a caller.
    out = fc_run("callers", "get_thing").out
    ck("fc: callers lists all 25 in mod_many", "25", count("mod_many.py", out))
    ck("fc: callers nothing cut", "0", count("lines cut", out))
    ck("fc: callers skips the TS call", "0", count("app.ts", out))

    # 5c. The TS probes: a method named `get`, and a top-level const function resolve; a
    #     local const inside a function does not.
    r = fc_run("def", "get")
    ck("fc: def get rc=0", "0", r.rc)
    ck("fc: def get is the TS method", "1", count("frontend/app.ts", r.out))
    r = fc_run("def", "useThing")
    ck("fc: def useThing (TS const) rc=0", "0", r.rc)
    ck("fc: def useThing in hooks.ts", "1", count("frontend/hooks.ts", r.out))
    ck("fc: def inner (local) rc=1", "1", fc_run("def", "inner").rc)

    # 5d. show resolves exactly and prints the source and the call trail.
    r = fc_run("show", "get_thing")
    ck("fc: show get_thing rc=0", "0", r.rc)
    ck("fc: show prints the source", "1", count("^def get_thing", r.out))
    ck("fc: show prints called-by", "1", count("^## called by", r.out))
    r = fc_run("show", "mod_a.py:5")
    ck("fc: show path:line rc=0", "0", r.rc)
    ck("fc: show path:line is get_thing", "1", count("^# mod_a.py:4-5  function  get_thing", r.out))

    # 7b. tests: a root-level test file is found, and nothing outside the imports is.
    out = fc_run("tests", "mod_a.py").out
    ck("fc: tests lists test_root", "1", count("^test_root.py$", out))
    ck("fc: tests lists no TS file", "0", count("frontend/", out))

    # 8b. The map keeps a multi-line signature holding `|` on one line, and leaves test
    #     files out of the ranking.
    fc_run("map")
    ck("fc: map one line for union_thing", "1", count("union_thing", file=fmap))
    ck("fc: map keeps the | in the sig", "1", count("union_thing(a: int | None, b: str | None = None) -> int | None", file=fmap, flags="F"))
    ck("fc: map every line is well formed", "0", count("^(# Repo map [(]|## |- |$)", file=fmap, flags="vE"))
    ck("fc: map leaves test files out", "0", count("^## (tests/|test_)", file=fmap, flags="E"))
    ck("fc: map keeps mod_a", "1", count("^## mod_a.py$", file=fmap))

    # 9b. Usage errors are 64, never 2 ("no index").
    ck("fc: usage rc=64", "64", fc_run("def").rc)

    # 9b2. search (docs/coder-tweaks/03, C3): the definition first, then each match under
    #      the narrowest symbol holding it, every match line `path:line:text`.
    r = fc_run("search", "get_thing")
    ck("fc: search rc=0", "0", r.rc)
    ck("fc: search definition first", "definition: mod_a.py:4  function  get_thing", r.out.split("\n")[0][:48])
    ck("fc: search groups under caller", "1", count("^mod_b.py:[0-9]*-[0-9]* function call_it$", r.out))
    ck("fc: search match line is grep's", "1", count("mod_b.py:4:    return get_thing() and Config.FLAG", r.out, flags="F"))
    out = fc_run("search", "get_thing", "tests").out
    ck("fc: search path limits it", "0", count("^mod_", out))
    ck("fc: search path keeps tests", "1", "1" if int(count("^tests/test_mod_a.py:[0-9]*:", out) or 0) > 0 else "")
    out = fc_run("search", "FLAG = ").out
    ck("fc: search text is not a name", "0", count("^definition: ", out))
    ck("fc: search text match line", "1", count('mod_a.py:2:    FLAG = "on"', out, flags="F"))
    ck("fc: search no match rc=1", "1", fc_run("search", "no_such_text_anywhere").rc)
    write(state, "failed: x\n")
    r = fc_run("search", "get_thing")
    ck("fc: search without an index rc=0", "0", r.rc)
    ck("fc: search without an index is plain", "0", count("^(definition: |[^:]+:[0-9]+-[0-9]+ )", r.out, flags="E"))
    ck("fc: search without an index has matches", "1", count("mod_b.py:4:    return get_thing() and Config.FLAG", r.out, flags="F"))
    fc_run("index")

    # 9c. A database removed under a live `ok` state (a `git clean -x`) fails closed with
    #     exit 2, never prints junk as an answer.
    (wt / ".codegraph").rename(wt / ".cg-away")
    r = fc_run("def", "get_thing")
    ck("fc: deleted db rc=2", "2", r.rc)
    ck("fc: deleted db says no index", "1", count("no code index; use grep", r.out))
    ck("fc: deleted db callers rc=2", "2", fc_run("callers", "get_thing").rc)
    (wt / ".cg-away").rename(wt / ".codegraph")

    # 10. A failed index state fails every query verb closed (exit 2).
    fc_run("index")
    fmap.unlink(missing_ok=True)
    git("-C", wt, "reset", "-q", "--hard", "HEAD", check=False)
    (wt / "mod_c.py").unlink(missing_ok=True)
    write(state, "failed: something broke\n")
    for v in ("def get_thing", "show get_thing", "callers get_thing", "callees get_thing",
              "impact get_thing", "tests mod_a.py", "map"):
        r = fc_run(*v.split())
        ck(f"fc: no-index {v} rc=2", "2", r.rc)
        ck(f"fc: no-index {v} msg", "1", count("no code index; use grep", r.out))


# ---------------------------------------------------------------------------
# fabro-io — the Stage-I/O binary (docs/stage-io C1-C5). Present inside every profile image
# but not assumed on the Mac PATH, so it is skipped when absent. When present, probe
# `version` and the `stage` subcommand under a minimal manifest.
# ---------------------------------------------------------------------------
def test_fabro_io_probe(ck, tmp_path):
    if not shutil.which("fabro-io", path=ORIG_PATH):
        pytest.skip("no fabro-io on PATH; present in profile images")
    ck("fabro-io prints a version", "1", count("^fabro-io ", run(["fabro-io", "version"]).out))
    tmpio = tmp_path / "io"
    tmpio.mkdir()
    env = {"FABRO_IO_ROOT": tmpio, "FABRO_NODE_ID": "probe",
           "FABRO_IO_MANIFEST": '{"version":1,"min_binary":"0.0.0","stages":{}}'}
    ck("fabro-io stage exits 0 under probe manifest", "0", run(["fabro-io", "stage"], env=env).rc)
    stage_json = tmpio / ".io/stage.json"
    ck("fabro-io stage wrote stage.json for probe", "1",
       count('"node": "probe"', file=stage_json) if stage_json.is_file() else "0")
