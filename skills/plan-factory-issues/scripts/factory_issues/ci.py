"""What `.fabro/ci.sh` runs, followed through what it calls.

`.fabro/ci.sh` passing on the finished tree is the pipeline's only proof that a command passes,
and the Refuter quotes it to credit a **Command criterion** (USER-GUIDE, *Writing criteria the
factory can verify*, rule 2; the precheck's `unverifiable` definition). A command counts as run
when `ci.sh` reaches it directly or through a shell script, a `package.json` script, a Makefile
target or a pre-commit hook.

The follower reads shell by line, not by grammar. What it cannot follow (a variable command, a
missing file, `just`, `tox`, ...) is recorded as **unresolved**: a criterion that finds no route
while something is unresolved is a partial route, a warning, because the command may run where
the follower cannot see.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

CI = Path(".fabro/ci.sh")

# Tools whose second word names the job (`cargo clippy` is not `cargo test`).
MULTI = {
    "cargo", "go", "deno", "dotnet", "mix", "gradle", "gradlew", "mvn", "uv", "ruff", "git", "docker",
    "flutter", "dart", "swift", "rustup", "poetry", "pdm", "hatch", "bundle", "rake", "rails", "php",
    "composer", "terraform", "helm", "kubectl", "biome", "nx", "turbo", "make", "just", "task", "coverage",
}
# Leading words that run the next command: stripped before the tool is read.
WRAPPERS = {
    ("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("rye", "run"),
    ("coverage", "run"), ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "dlx"), ("yarn", "exec"), ("npm", "exec"), ("bundle", "exec"),
}
SOLO_WRAPPERS = {"npx", "bunx", "time", "env", "sudo", "nice", "xvfb-run", "command", "exec", "nohup"}
VALUE_FLAGS = {"--with", "--python", "-p", "--project", "--extra", "--group", "--package", "--directory",
               "--env-file", "-w", "--workspace", "--filter", "-F", "--prefix", "-C"}
PY = re.compile(r"^python(\d(\.\d+)?)?$")
SCRIPT_RUNNERS = {"npm", "pnpm", "yarn", "bun"}
PNPM_BUILTINS = {"install", "i", "add", "remove", "update", "exec", "dlx", "store", "audit", "outdated",
                 "publish", "pack", "link", "why", "list", "ls", "rebuild", "prune", "import", "fetch"}
YARN_BUILTINS = {"install", "add", "remove", "upgrade", "dlx", "exec", "workspace", "workspaces", "why",
                 "info", "init", "pack", "publish", "set", "config", "cache", "plugin", "constraints"}
BUN_BUILTINS = {"install", "i", "add", "remove", "update", "x", "pm", "build", "test", "link", "create"}
UNFOLLOWED = {"just", "task", "tox", "nox", "invoke", "earthly", "bazel", "nix", "docker"}
SHELL_SKIP = {
    "if", "then", "else", "elif", "fi", "for", "in", "do", "done", "while", "until", "case", "esac",
    "function", "{", "}", "local", "export", "declare", "readonly", "echo", "printf", "set", "unset",
    "test", "[", "[[", "eval", "exit", "return", "true", "false", ":", "shift", "trap", "wait",
    "cd", "pushd", "popd", "mkdir", "rm", "cp", "mv", "touch", "cat", "ln", "chmod", "trap", "break",
    "continue", "read", "mapfile", "readarray", "sleep", "!",
}


@dataclass
class Cmd:
    text: str
    key: str | None
    where: str  # file:line of this command
    chain: list[str]  # where each call on the way to it came from, ci.sh first

    def route(self) -> str:
        return " -> ".join([*self.chain, f"{self.where} `{self.text}`"])

    def top(self) -> str:
        return self.chain[0] if self.chain else self.where


@dataclass
class Unresolved:
    where: str
    text: str
    why: str


@dataclass
class Routes:
    root: Path
    commands: list[Cmd] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    scripts: dict[str, list[tuple[Path, str]]] = field(default_factory=dict)  # name -> (package.json, body)

    def keys(self) -> set[str]:
        return {c.key for c in self.commands if c.key}


@dataclass
class Match:
    status: str  # "found" | "partial" | "none"
    via: Cmd | None = None
    closest: Cmd | None = None


# --- tokens ---------------------------------------------------------------------------------------


def split_words(text: str) -> list[str]:
    try:
        return shlex.split(text, comments=False)
    except ValueError:
        return text.split()


def strip_wrappers(words: list[str]) -> list[str]:
    w = list(words)
    changed = True
    while w and changed:
        changed = False
        while w and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            w, changed = w[1:], True
        if not w:
            break
        if len(w) >= 2 and (w[0], w[1]) in WRAPPERS:
            w, changed = _skip_flags(w[2:]), True
        elif w[0] in SOLO_WRAPPERS:
            w, changed = _skip_flags(w[1:]), True
        elif w[0] == "timeout":
            rest = _skip_flags(w[1:])
            w, changed = rest[1:] if rest else rest, True
        elif PY.match(w[0]) and len(w) >= 3 and w[1] == "-m":
            w, changed = w[2:], True
    return w


def _skip_flags(w: list[str]) -> list[str]:
    i = 0
    while i < len(w) and w[i].startswith("-"):
        i += 2 if w[i] in VALUE_FLAGS and "=" not in w[i] else 1
    return w[i:]


def script_name(words: list[str]) -> tuple[str | None, str | None]:
    """`(script, workspace)` when the words run a package.json script, else `(None, None)`."""
    w = strip_wrappers(words)
    if not w or w[0] not in SCRIPT_RUNNERS:
        return None, None
    runner, rest = w[0], w[1:]
    workspace = None
    args: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--workspace", "-w", "--filter", "-F", "--prefix", "-C") and i + 1 < len(rest):
            workspace = rest[i + 1]
            i += 2
            continue
        m = re.match(r"^--(?:workspace|filter|prefix)=(.+)$", a)
        if m:
            workspace = m.group(1)
        elif a == "--":
            break
        elif not a.startswith("-"):
            args.append(a)
        i += 1
    if not args:
        return None, None
    if args[0] in ("run", "run-script"):
        return (args[1], workspace) if len(args) > 1 else (None, None)
    if runner == "npm":
        alias = {"test": "test", "t": "test", "start": "start", "stop": "stop", "restart": "restart"}
        return (alias[args[0]], workspace) if args[0] in alias else (None, None)
    builtins = {"pnpm": PNPM_BUILTINS, "yarn": YARN_BUILTINS, "bun": BUN_BUILTINS}[runner]
    if args[0] in builtins:
        return None, None
    return args[0], workspace


def tool_key(text: str) -> str | None:
    """The comparable name of what a command runs: `cargo nextest`, `pytest`, `script:lint`."""
    words = split_words(text)
    script, _ = script_name(words)
    if script:
        return f"script:{script}"
    w = strip_wrappers(words)
    if not w:
        return None
    tool = w[0].rsplit("/", 1)[-1]
    if tool.startswith("$") or tool in SHELL_SKIP:
        return None
    if tool == "bun" and len(w) > 1 and w[1] == "test":
        return "bun test"
    if tool in MULTI:
        rest = [a for a in w[1:] if not a.startswith("+")]
        rest = _skip_flags(rest) if tool in ("make", "just", "task") else rest
        if rest and not rest[0].startswith("-"):
            return f"{tool} {rest[0]}"
    return tool


# --- following --------------------------------------------------------------------------------------


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations and drop comments, keeping each command's first line number."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for no, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = no
        line = _drop_comment(line)
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        buf += line
        if buf.strip():
            out.append((start, buf.strip()))
        buf = ""
    if buf.strip():
        out.append((start, buf.strip()))
    return out


def _drop_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def segments(line: str) -> list[str]:
    """Split a shell line on `&&`, `||`, `;`, `|` and subshell parentheses, outside quotes and `$( )`."""
    out, buf, quote, depth = [], "", None, 0
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote, buf = ch, buf + ch
        elif line.startswith("$(", i) or line.startswith("((", i) and not buf.strip():
            depth, buf = depth + 1, buf + line[i:i + 2]
            i += 2
            continue
        elif ch == "(" and buf.endswith("="):  # an array literal, `a=(x y)`
            depth, buf = depth + 1, buf + ch
        elif ch == ")" and depth:
            depth, buf = depth - 1, buf + ch
        elif depth:
            buf += ch
        elif line.startswith("&&", i) or line.startswith("||", i):
            out.append(buf)
            buf = ""
            i += 2
            continue
        elif ch in ";|()":
            out.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    out.append(buf)
    return [s.strip() for s in out if s.strip()]


class Follower:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.routes = Routes(self.root)
        self.seen: set[tuple[str, str]] = set()
        self.routes.scripts = self._package_scripts()

    def rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.root))
        except ValueError:
            return str(p)

    def follow_file(self, path: Path, chain: list[str], cwd: Path | None = None) -> None:
        """Follow one shell file. `cwd` is the caller's directory: a script's relative paths
        resolve against where it was run from, not where it lives."""
        if ("file", str(path.resolve())) in self.seen:
            return
        self.seen.add(("file", str(path.resolve())))
        cwd = cwd or self.root
        for no, line in logical_lines(path.read_text(errors="replace")):
            where = f"{self.rel(path)}:{no}"
            subshell = line.lstrip().startswith("(")
            local = cwd
            for seg in segments(line):
                words = split_words(seg)
                if words and words[0] in ("cd", "pushd") and len(words) > 1 and not words[1].startswith("$"):
                    target = (local / words[1]).resolve()
                    local = target
                    if not subshell:
                        cwd = target
                    continue
                self.command(seg, where, chain, local, path.parent)

    def command(self, seg: str, where: str, chain: list[str], cwd: Path, script_dir: Path) -> None:
        words = split_words(seg)
        while words and words[0] in ("if", "elif", "while", "until", "then", "else", "do", "!", "{", "time"):
            words = words[1:]
        # `x=$(cmd)` and `a[$i]=1` assign; only `X=1 cmd` runs something.
        if not words or re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", seg.lstrip("{ !")) and (
            re.match(r"^[^=]*=\s*(\$\(|`|\()", seg.lstrip("{ !")) or "[" in words[0].split("=")[0] or len(strip_wrappers(words)) == 0
        ):
            return
        text = " ".join(words)
        w = strip_wrappers(words)
        if not w:
            return
        head = w[0]
        if head in SHELL_SKIP or head.endswith("()") or head.startswith(("#", "-", "((", "${#")) or head.endswith("]]"):
            return
        # A shell script: `bash x.sh`, `source x.sh`, `./x.sh`, `scripts/x.sh`.
        target = None
        if head in ("bash", "sh", "source", ".", "zsh") and len(w) > 1:
            target = w[1]
        elif ("/" in head or head.endswith(".sh")) and re.match(r"^[\w.~/${}()\[\]@-]+$", head):
            target = head
        if target is not None:
            path = self._resolve(target, cwd, script_dir)
            if path is None:
                if "$" in target and "dirname" not in target:
                    self.routes.unresolved.append(Unresolved(where, text, "the script path is a variable"))
                else:
                    self.routes.unresolved.append(Unresolved(where, text, f"no such file: {target}"))
                return
            if _is_shell(path):
                self.routes.commands.append(Cmd(text, None, where, chain))
                self.follow_file(path, [*chain, where], cwd)
                return
            self.routes.commands.append(Cmd(text, tool_key(text), where, chain))
            return
        if head.startswith("$"):
            self.routes.unresolved.append(Unresolved(where, text, "the command is a variable"))
            return
        key = tool_key(text)
        self.routes.commands.append(Cmd(text, key, where, chain))
        script, workspace = script_name(words)
        if script:
            self._follow_script(script, workspace, cwd, where, chain, text)
            return
        tool = head.rsplit("/", 1)[-1]
        if tool == "make":
            self._follow_make(w[1:], cwd, where, chain, text)
        elif tool == "docker":
            inner = _docker_inner(w[1:])
            if inner:
                self.command(" ".join(shlex.quote(a) for a in inner), where, chain, cwd, script_dir)
        elif tool in ("pre-commit", "prek"):
            self._follow_precommit(where, chain, text)
        elif tool in ("run-s", "run-p", "npm-run-all", "npm-run-all2"):
            for name in [a for a in w[1:] if not a.startswith("-")]:
                self._follow_script(name, None, cwd, where, chain, text)
        elif tool in UNFOLLOWED:
            self.routes.unresolved.append(Unresolved(where, text, f"`{tool}` is not followed"))

    def _resolve(self, target: str, cwd: Path, script_dir: Path) -> Path | None:
        m = re.match(r'^\$\(dirname\b[^)]*\)/(.+)$', target)
        if m:
            target = str(script_dir / m.group(1))
        if "$" in target:
            return None
        for base in (cwd, self.root, script_dir):
            p = (base / target).resolve() if not Path(target).is_absolute() else Path(target)
            if p.is_file():
                return p
        # A path inside a container (`docker compose exec backend bash scripts/x.sh`) is relative
        # to the service's directory: take the one file in the repository that ends with it.
        tail = target.removeprefix("./")
        hits = sorted((p for p in self.root.rglob(Path(tail).name)
                       if str(p).endswith("/" + tail) and "node_modules" not in p.parts and p.is_file()),
                      key=lambda p: len(p.parts))
        if len(hits) == 1 or len(hits) > 1 and len(hits[0].parts) < len(hits[1].parts):
            return hits[0]
        return None

    def _package_scripts(self) -> dict[str, list[tuple[Path, str]]]:
        out: dict[str, list[tuple[Path, str]]] = {}
        for pj in sorted(self.root.rglob("package.json")):
            if "node_modules" in pj.parts or any(part.startswith(".") for part in pj.relative_to(self.root).parts[:-1]):
                continue
            try:
                scripts = json.loads(pj.read_text()).get("scripts") or {}
            except (json.JSONDecodeError, OSError, AttributeError):
                continue
            for name, body in scripts.items():
                if isinstance(body, str):
                    out.setdefault(name, []).append((pj, body))
        return out

    def _pick_package(self, name: str, workspace: str | None, cwd: Path) -> tuple[Path, str] | None:
        options = self.routes.scripts.get(name) or []
        if not options:
            return None
        prefer = []
        if workspace:
            prefer += [(cwd / workspace / "package.json").resolve(), (self.root / workspace / "package.json").resolve()]
        prefer += [(cwd / "package.json").resolve(), (self.root / "package.json").resolve()]
        for want in prefer:
            for pj, body in options:
                if pj.resolve() == want:
                    return pj, body
        if workspace:
            for pj, body in options:
                try:
                    if json.loads(pj.read_text()).get("name") == workspace:
                        return pj, body
                except (json.JSONDecodeError, OSError):
                    pass
        return options[0]

    def _follow_script(self, name: str, workspace: str | None, cwd: Path, where: str, chain: list[str], text: str) -> None:
        if name.endswith("*") or name.endswith(":*"):
            prefix = name.rstrip("*")
            for n in sorted(k for k in self.routes.scripts if k.startswith(prefix)):
                self._follow_script(n, workspace, cwd, where, chain, text)
            return
        found = self._pick_package(name, workspace, cwd)
        if found is None:
            self.routes.unresolved.append(Unresolved(where, text, f"no package.json script `{name}`"))
            return
        pj, body = found
        tag = ("script", f"{pj}:{name}")
        if tag in self.seen:
            return
        self.seen.add(tag)
        # npm runs `pre<name>` and `post<name>` from the same package.json around the script.
        for part in (f"pre{name}", name, f"post{name}"):
            hook = (pj, body) if part == name else self._pick_package(part, None, pj.parent)
            if hook is None or hook[0] != pj:
                continue
            for seg in segments(hook[1]):
                self.command(seg, f"{self.rel(pj)} scripts.{part}", [*chain, where], pj.parent, pj.parent)

    def _follow_make(self, args: list[str], cwd: Path, where: str, chain: list[str], text: str) -> None:
        directory = cwd
        targets: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-C" and i + 1 < len(args):
                directory = (cwd / args[i + 1]).resolve()
                i += 2
                continue
            if a.startswith("-C") and len(a) > 2:
                directory = (cwd / a[2:]).resolve()
            elif not a.startswith("-") and "=" not in a:
                targets.append(a)
            i += 1
        names = set(os.listdir(directory)) if directory.is_dir() else set()  # exact case, on any filesystem
        mk = next((directory / n for n in ("GNUmakefile", "makefile", "Makefile") if n in names), None)
        if mk is None:
            self.routes.unresolved.append(Unresolved(where, text, f"no Makefile in {self.rel(directory)}"))
            return
        rules = parse_makefile(mk)
        if not targets:
            targets = [next(iter(rules))] if rules else []
        for t in targets:
            self._make_target(mk, rules, t, where, chain, text)

    def _make_target(self, mk: Path, rules: dict, target: str, where: str, chain: list[str], text: str) -> None:
        tag = ("make", f"{mk}:{target}")
        if tag in self.seen:
            return
        self.seen.add(tag)
        if target not in rules:
            if not (mk.parent / target).exists():
                self.routes.unresolved.append(Unresolved(where, text, f"no Makefile target `{target}`"))
            return
        line, prereqs, recipe = rules[target]
        here = f"{self.rel(mk)}:{line} {target}"
        for p in prereqs:
            self._make_target(mk, rules, p, where, chain, text)
        for no, cmd in recipe:
            cmd = cmd.lstrip("@-+ \t")
            if "$(MAKE)" in cmd or "${MAKE}" in cmd:
                cmd = cmd.replace("$(MAKE)", "make").replace("${MAKE}", "make")
            for seg in segments(cmd):
                self.command(seg, f"{self.rel(mk)}:{no}", [*chain, where, here], mk.parent, mk.parent)

    def _follow_precommit(self, where: str, chain: list[str], text: str) -> None:
        cfg = next((self.root / n for n in (".pre-commit-config.yaml", ".pre-commit-config.yml") if (self.root / n).is_file()), None)
        if cfg is None:
            self.routes.unresolved.append(Unresolved(where, text, "pre-commit runs, but there is no .pre-commit-config.yaml"))
            return
        here = self.rel(cfg)
        if ("precommit", here) in self.seen:
            return
        self.seen.add(("precommit", here))
        for no, line in enumerate(cfg.read_text().splitlines(), 1):
            m = re.match(r"^\s*-?\s*(id|entry):\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", line)
            if not m:
                continue
            value = m.group(2).strip()
            key = tool_key(value) if m.group(1) == "entry" else value
            self.routes.commands.append(Cmd(f"{m.group(1)}: {value}", key, f"{here}:{no}", [*chain, where]))


def _docker_inner(args: list[str]) -> list[str] | None:
    """The command `docker compose exec|run <service> ...` or `docker exec|run <name> ...` runs."""
    a = list(args)
    if a and a[0] == "compose":
        a = _skip_flags(a[1:])
    if not a or a[0] not in ("exec", "run"):
        return None
    rest = a[1:]
    i = 0
    takes_value = {"-e", "--env", "-u", "--user", "-w", "--workdir", "--name", "-v", "--volume", "--entrypoint",
                   "-p", "--publish", "--network", "--env-file", "-l", "--label"}
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] in takes_value else 1
    return rest[i + 1:] or None


def parse_makefile(mk: Path) -> dict[str, tuple[int, list[str], list[tuple[int, str]]]]:
    rules: dict[str, tuple[int, list[str], list[tuple[int, str]]]] = {}
    current: list[str] = []
    for no, line in enumerate(mk.read_text(errors="replace").splitlines(), 1):
        if line.startswith("\t"):
            for t in current:
                rules[t][2].append((no, line[1:]))
            continue
        m = re.match(r"^([A-Za-z0-9_./%-][A-Za-z0-9_./% -]*?)\s*::?(?!=)\s*([^=]*)$", line)
        if m and not line.startswith((".PHONY", "#")):
            names = m.group(1).split()
            prereqs = [p for p in m.group(2).split("#")[0].split(";")[0].split() if not p.startswith("|")]
            current = names
            for t in names:
                rules[t] = (no, prereqs, [])
        elif line.startswith(".PHONY") or (line.strip() and not line.startswith("#")):
            current = []
    return rules


def _is_shell(path: Path) -> bool:
    if path.suffix in (".sh", ".bash"):
        return True
    try:
        first = path.read_text(errors="replace").splitlines()[:1]
    except OSError:
        return False
    return bool(first) and first[0].startswith("#!") and re.search(r"\b(ba|z|da)?sh\b", first[0]) is not None


def follow(root: Path) -> Routes:
    """Every command `.fabro/ci.sh` reaches. Raises FileNotFoundError when there is no ci.sh."""
    ci = root / CI
    if not ci.is_file():
        raise FileNotFoundError(str(ci))
    f = Follower(root)
    f.follow_file(ci, [])
    return f.routes


def find(routes: Routes, command: str) -> Match:
    """Whether `ci.sh` runs `command`. A command `ci.sh` reaches with other flags or a narrower path
    still counts: the precheck credits the same tool over the same files."""
    words = split_words(command)
    if ".fabro/ci.sh" in command:
        return Match("found", Cmd(command, None, ".fabro/ci.sh:1", []))
    key = tool_key(command)
    if key is None:
        return Match("none")
    for c in routes.commands:
        if c.key == key:
            return Match("found", c)
    script, workspace = script_name(words)
    if script:
        f = Follower(routes.root)
        picked = f._pick_package(script, workspace, routes.root)
        if picked:
            inner = [tool_key(s) for s in segments(picked[1])]
            inner = [k for k in inner if k]
            hits = [next((c for c in routes.commands if c.key == k), None) for k in inner]
            if inner and all(hits):
                return Match("found", hits[0])
    tool, _, sub = key.removeprefix("script:").partition(" ")
    same = [c for c in routes.commands if c.key and c.key.removeprefix("script:").split()[0] == tool]
    near = [c for c in same if sub and (sub in c.key or c.key.partition(" ")[2] in sub)]
    if key.startswith("script:"):
        name = key.removeprefix("script:")
        same = [c for c in routes.commands if c.key and c.key.startswith("script:")]
        near = [c for c in same if name in c.key.removeprefix("script:") or c.key.removeprefix("script:") in name]
    closest = (near or same or [None])[0]
    return Match("partial" if routes.unresolved else "none", None, closest)
