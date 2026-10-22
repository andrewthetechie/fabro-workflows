#!/usr/bin/env python3.11
"""check-graph-invariants.py — the mechanical invariants of the fabro graphs (ADR 0019, #12).

Usage:
  check-graph-invariants.py [--root DIR]        check every package and phase under DIR/.fabro/workflows/
  check-graph-invariants.py --dump FILE.fabro   print the reader's statements in fabro parse's shape
  check-graph-invariants.py --normalize         normalize a `fabro parse` JSON read from stdin

A package is a directory with a workflow.toml. Every package's root graph gets R1-R9 and R11; every
`_shared/*/*.fabro` phase gets the file rules (the reader, R6 and R8). Each violation prints as
`<package> <rule> <node or hook> <why>`, then `VIOLATIONS: N`. Exit status is min(N, 125).

Standard library only (Python 3.11+, tomllib). It reads the graphs itself, so it runs offline
in `make check`. `make check-host` proves the reader agrees with `fabro parse` on the six files
(--dump and --normalize, the parity step in ops/check-host.sh).

Each rule's docstring names its row in docs/agents/invariants-*.md. Exceptions live here, each
with its reason and source; no exception is written in a graph (ADR 0019, D3).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The breaker floor (AGENTS.md, invariants-graph.md: loop_restart_signature_limit). D4.
BREAKER_FLOOR = 20

# R7 exceptions. Each names the operator decision it comes from.
R7_EXCEPTIONS = {
    ("pr-review", "report_blocked"): "pr-review omits it: operator decision 13 in pr-review/workflow.toml",
    ("pr-review", "mark_needs_human"): "pr-review omits it: operator decision 13 in pr-review/workflow.toml",
}
# The review-merge report nodes a checkpoint_saved hook must cover (invariants-merge.md, Reporting).
REPORT_NODES = ("report_merged", "report_blocked", "mark_needs_human", "report_queued")

# R2 exemptions: an agent emits an event per stream delta, and the watchdog parks during a
# human wait. Only command nodes are compared against stall_timeout.
STALL_EXEMPT = "agent and human nodes"

IDENT = re.compile(r"[A-Za-z0-9_.\-]+")
PUNCT = "{}[]=;,"
DURATION = re.compile(r"(\d+)([smh])")


class ReaderError(Exception):
    """A construct the reader does not model. It fails closed: a violation, never a guess."""


# ---------------------------------------------------------------------------
# Tokenizer and reader (the shape of `fabro parse`, every value a string)
# ---------------------------------------------------------------------------


def read_string(text: str, i: int, line: int) -> tuple[str, int]:
    """The value of the quoted string at text[i], and the index after its closing quote.

    The only escapes are `\\"` and `\\n` (a newline, as fabro's parser makes it). A backslash at the
    end of a line is kept with its newline, as fabro keeps it. Any other backslash is kept too, and
    R6 rejects it.
    """
    out: list[str] = []
    j = i + 1
    while True:
        if j >= len(text):
            raise ReaderError(f"line {line}: unterminated string")
        c = text[j]
        if c == '"':
            return "".join(out), j + 1
        if c == "\\" and j + 1 < len(text):
            nxt = text[j + 1]
            if nxt == '"':
                out.append('"')
            elif nxt == "n":
                out.append("\n")
            elif nxt == "\n":
                out.append("\\\n")  # kept, as fabro's parser keeps it
            else:
                out.append("\\" + nxt)
            j += 2
            continue
        out.append(c)
        j += 1


def tokenize(text: str) -> list[tuple[str, str, int]]:
    """(kind, value, line). kind is 'S' (string), 'I' (identifier) or the punctuation itself."""
    toks: list[tuple[str, str, int]] = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif text.startswith("//", i) or c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ReaderError(f"line {line}: unterminated comment")
            line += text.count("\n", i, j)
            i = j + 2
        elif c == '"':
            value, j = read_string(text, i, line)
            toks.append(("S", value, line))
            line += text.count("\n", i, j)
            i = j
        elif text.startswith("->", i):
            toks.append(("->", "->", line))
            i += 2
        elif c in PUNCT:
            toks.append((c, c, line))
            i += 1
        elif (m := IDENT.match(text, i)):
            toks.append(("I", m.group(), line))
            i = m.end()
        elif c == ":":
            raise ReaderError(f"line {line}: ports (node:port) are not supported")
        elif c == "<":
            raise ReaderError(f"line {line}: HTML strings are not supported")
        else:
            raise ReaderError(f"line {line}: unexpected character {c!r}")
    return toks


class Reader:
    """Parses one `digraph` into fabro parse's statement shape. Values are plain strings."""

    def __init__(self, text: str) -> None:
        self.toks = tokenize(text)
        self.p = 0

    def peek(self, k: int = 0) -> str | None:
        return self.toks[self.p + k][0] if self.p + k < len(self.toks) else None

    def take(self, kinds: tuple[str, ...] = ()) -> tuple[str, str, int]:
        if self.p >= len(self.toks):
            raise ReaderError("unexpected end of file")
        tok = self.toks[self.p]
        if kinds and tok[0] not in kinds:
            raise ReaderError(f"line {tok[2]}: expected {' or '.join(kinds)}, found {tok[1]!r}")
        self.p += 1
        return tok

    def graph(self) -> dict:
        head = self.take(("I",))
        if head[1] == "strict":
            raise ReaderError(f"line {head[2]}: strict graphs are not supported")
        if head[1] != "digraph":
            raise ReaderError(f"line {head[2]}: only digraph is supported, found {head[1]!r}")
        name = self.take(("I", "S"))[1] if self.peek() in ("I", "S") else ""
        self.take(("{",))
        statements: list[dict] = []
        while self.peek() != "}":
            stmt = self.statement()
            if stmt is not None:
                statements.append(stmt)
        self.take(("}",))
        if self.p != len(self.toks):
            raise ReaderError("text after the closing brace of the graph")
        return {"name": name, "statements": statements}

    def statement(self) -> dict | None:
        kind = self.peek()
        if kind in (";", ","):
            self.take()
            return None
        if kind == "{":
            raise ReaderError("subgraph blocks are not supported")
        tok = self.take(("I", "S"))
        word = tok[1]
        if kind == "I" and word == "subgraph":
            raise ReaderError(f"line {tok[2]}: subgraph is not supported")
        if kind == "I" and word in ("graph", "node", "edge") and self.peek() == "[":
            if word != "graph":
                raise ReaderError(f"line {tok[2]}: {word} [...] defaults are not supported")
            return {"GraphAttr": self.attr_lists()}
        if self.peek() == "=":
            self.take(("=",))
            value = self.take(("I", "S"))[1]
            return {"GraphAttrDecl": [word, value]}
        ids = [word]
        while self.peek() == "->":
            self.take(("->",))
            ids.append(self.take(("I", "S"))[1])
        attrs = self.attr_lists()
        if len(ids) == 1:
            return {"Node": {"id": ids[0], "attrs": attrs}}
        return {"Edge": {"nodes": ids, "attrs": attrs or None}}

    def attr_lists(self) -> list[list[str]]:
        """Every `[k=v, ...]` group after a statement, in order."""
        out: list[list[str]] = []
        while self.peek() == "[":
            self.take(("[",))
            while self.peek() != "]":
                key = self.take(("I", "S"))[1]
                self.take(("=",))
                out.append([key, self.take(("I", "S"))[1]])
                if self.peek() in (",", ";"):
                    self.take()
            self.take(("]",))
        return out


def read_graph(text: str) -> dict:
    return Reader(text).graph()


def normalize(ast: dict) -> dict:
    """`fabro parse` JSON with each {"Str"|"Ident"|"Int"...: v} value reduced to its string."""

    def val(v):
        if isinstance(v, dict) and len(v) == 1:
            kind, inner = next(iter(v.items()))
            if kind == "Bool":
                return "true" if inner else "false"
            return str(inner)
        return str(v)

    out = {"name": ast["name"], "statements": []}
    for stmt in ast["statements"]:
        (kind, body), = stmt.items()
        if kind == "GraphAttr":
            body = [[k, val(v)] for k, v in body]
        elif kind == "GraphAttrDecl":
            body = [body[0], val(body[1])]
        elif kind == "Node":
            body = {"id": body["id"], "attrs": [[k, val(v)] for k, v in (body.get("attrs") or [])]}
        elif kind == "Edge":
            attrs = body.get("attrs")
            body = {"nodes": body["nodes"], "attrs": [[k, val(v)] for k, v in attrs] if attrs else None}
        out["statements"].append({kind: body})
    return out


def dumps(obj: object) -> str:
    return json.dumps(obj, indent=1) + "\n"


# ---------------------------------------------------------------------------
# The splice: the running graph of a root package, with each import expanded
# ---------------------------------------------------------------------------


class Graph:
    """A parsed graph with its nodes and edges as dicts, in statement order."""

    def __init__(self, ast: dict) -> None:
        self.name = ast["name"]
        self.graph_attrs: dict[str, str] = {}
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: list[tuple[str, str, dict[str, str]]] = []
        for stmt in ast["statements"]:
            (kind, body), = stmt.items()
            if kind == "GraphAttr":
                self.graph_attrs.update(dict(body))
            elif kind == "GraphAttrDecl":
                self.graph_attrs[body[0]] = body[1]
            elif kind == "Node":
                self.nodes.setdefault(body["id"], {}).update(dict(body["attrs"]))
            elif kind == "Edge":
                attrs = dict(body["attrs"] or [])
                for a, b in zip(body["nodes"], body["nodes"][1:]):
                    self.nodes.setdefault(a, {})
                    self.nodes.setdefault(b, {})
                    self.edges.append((a, b, attrs))


def is_start(attrs: dict) -> bool:
    return attrs.get("shape") == "Mdiamond"


def is_exit(attrs: dict) -> bool:
    return attrs.get("shape") == "Msquare"


def is_command(attrs: dict) -> bool:
    return "script" in attrs


def is_human(attrs: dict) -> bool:
    return attrs.get("shape") == "hexagon"


def imports_of(graph: Graph) -> list[tuple[str, str]]:
    """(import node id, the import path as written) for every node with an import attribute."""
    return [(nid, attrs["import"]) for nid, attrs in graph.nodes.items() if "import" in attrs]


def splice(root: Graph, phases: dict[str, Graph]) -> tuple[dict[str, dict[str, str]], list[tuple[str, str, dict]]]:
    """The running nodes and edges: each import node X is replaced by X.<id> for every phase node
    except its start and exit, and the edges into X and out of X run to the phase's start and exit
    successors. `phases` is keyed by the import path as written.
    """
    nodes = {k: dict(v) for k, v in root.nodes.items()}
    edges = list(root.edges)
    for x, path in imports_of(root):
        phase = phases[path]
        inner = {k: v for k, v in phase.nodes.items() if not (is_start(v) or is_exit(v))}
        starts = [b for a, b, _ in phase.edges if is_start(phase.nodes.get(a, {}))]
        exits = [a for a, b, _ in phase.edges if is_exit(phase.nodes.get(b, {}))]
        inner_edges = [(a, b, at) for a, b, at in phase.edges
                       if not (is_start(phase.nodes.get(a, {})) or is_exit(phase.nodes.get(b, {})))]
        del nodes[x]
        for k, v in inner.items():
            nodes[f"{x}.{k}"] = dict(v)
        rewired: list[tuple[str, str, dict]] = []
        for a, b, at in edges:
            if b == x and a != x:
                rewired += [(a, f"{x}.{s}", at) for s in starts]
            elif a == x and b != x:
                rewired += [(f"{x}.{e}", b, at) for e in exits]
            elif a != x and b != x:
                rewired.append((a, b, at))
        edges = rewired + [(f"{x}.{a}", f"{x}.{b}", at) for a, b, at in inner_edges]
    return nodes, edges


# ---------------------------------------------------------------------------
# Rules. Each returns violations as (rule, node or hook, why).
# ---------------------------------------------------------------------------


def dur(value: str | None) -> int | None:
    """Seconds for Ns, Nm or Nh, else None. Any other unit fails closed (R2)."""
    m = DURATION.fullmatch(value or "")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)] if m else None


def r1_breaker(root: Graph, nodes: dict, edges: list, imports: list) -> list[tuple]:
    """R1: a root graph that imports a phase, or has a cycle through a command node, sets
    loop_restart_signature_limit to at least 20 (invariants-graph.md, the breaker row; D4).
    """
    cyclic = [] if imports else cycles_through_commands(nodes, edges)
    if not imports and not cyclic:
        return []
    limit = root.graph_attrs.get("loop_restart_signature_limit", "")
    if not limit.isdigit() or int(limit) < BREAKER_FLOOR:
        why = (f"imports a phase" if imports else f"cycle through {cyclic[0]}")
        return [("R1", "graph", f"loop_restart_signature_limit {limit or 'unset'} < {BREAKER_FLOOR} ({why})")]
    return []


def cycles_through_commands(nodes: dict, edges: list) -> list[str]:
    """Command nodes that reach themselves over the spliced edges."""
    succ: dict[str, list[str]] = {}
    for a, b, _ in edges:
        succ.setdefault(a, []).append(b)
    found = []
    for start in nodes:
        if not is_command(nodes[start]):
            continue
        seen: set[str] = set()
        stack = list(succ.get(start, []))
        while stack:
            n = stack.pop()
            if n == start:
                found.append(start)
                break
            if n in seen:
                continue
            seen.add(n)
            stack.extend(succ.get(n, []))
    return found


def r2_stall(root: Graph, nodes: dict) -> list[tuple]:
    """R2: the root's stall_timeout is greater than the timeout of every command node, spliced
    nodes included (invariants-graph.md, the timeouts row). Agent and human nodes are exempt
    (D3). Only Ns, Nm and Nh are durations, and any other timeout or stall_timeout is a violation.
    """
    out = []
    stall = root.graph_attrs.get("stall_timeout")
    stall_s = dur(stall)
    if stall_s is None:
        out.append(("R2", "graph", f"stall_timeout {stall or 'unset'} is not Ns, Nm or Nh"))
    for nid, attrs in nodes.items():
        if "timeout" not in attrs:
            continue
        t = dur(attrs["timeout"])
        if t is None:
            out.append(("R2", nid, f"timeout {attrs['timeout']} is not Ns, Nm or Nh"))
        elif stall_s is not None and is_command(attrs) and t >= stall_s:
            out.append(("R2", nid, f"timeout {attrs['timeout']} is not less than stall_timeout {stall}"))
    return out


def stylesheet_classes(stylesheet: str) -> list[str]:
    """The selector names of a model_stylesheet, without the leading dot."""
    return re.findall(r"\.([^\s{}]+)\s*\{", stylesheet)


def r3_phase_classes(root: Graph, phases: list[Graph]) -> list[tuple]:
    """R3: every class a node of an imported phase uses has its own rule in the root's
    model_stylesheet (invariants-graph.md, the stylesheet row). A class without one inherits `*`.
    """
    have = set(stylesheet_classes(root.graph_attrs.get("model_stylesheet", "")))
    out = []
    for phase in phases:
        for nid, attrs in phase.nodes.items():
            for c in (attrs.get("class") or "").split(","):
                c = c.strip()
                if c and c not in have:
                    out.append(("R3", f"{phase.name}.{nid}", f"class {c} has no rule in model_stylesheet"))
    return out


INPUT_DEFAULT = re.compile(r"\{\{\s*inputs\.(\w+)\s*\|\s*default\((.)")


def r4_stylesheet_and_inputs(root: Graph, run_inputs: set[str], phases: list[Graph]) -> list[tuple]:
    """R4: stylesheet class selectors match [a-z0-9-]+; every `{{ inputs.X | default(...) }}`
    quotes its default with ', and X is not a key of the package's [run.inputs] (invariants-
    graph.md, the stylesheet and the per-run model rows). An imported phase renders with the
    importing package's inputs, so its nodes are checked against them too.
    """
    out = []
    for c in stylesheet_classes(root.graph_attrs.get("model_stylesheet", "")):
        if not re.fullmatch(r"[a-z0-9-]+", c):
            out.append(("R4", "model_stylesheet", f"class selector .{c} does not match [a-z0-9-]+"))
    values = list(root.graph_attrs.items())
    for nid, attrs in root.nodes.items():
        values += [(f"{nid}.{k}", v) for k, v in attrs.items()]
    for phase in phases:
        for nid, attrs in phase.nodes.items():
            values += [(f"{phase.name}.{nid}.{k}", v) for k, v in attrs.items()]
    for where, value in values:
        for m in INPUT_DEFAULT.finditer(value):
            name, quote = m.group(1), m.group(2)
            if quote != "'":
                out.append(("R4", where, f"inputs.{name} default is not single-quoted"))
            if name in run_inputs:
                out.append(("R4", where, f"inputs.{name} is bound in [run.inputs]"))
    return out


def r5_hooks(hooks: list[dict], running: set[str]) -> list[tuple]:
    """R5: every hook matcher starts with ^ or (^|[.]) and ends with $, compiles, and matches a
    running node id, or is ^agent$ or ^shell$ (a handler type and a tool name) (invariants-
    graph.md, the hooks rows; invariants-merge.md, Reporting).
    """
    out = []
    names = running | {"agent", "shell"}
    for hook in hooks:
        matcher = hook.get("matcher")
        if matcher is None:
            continue
        label = f"{hook.get('event', '?')} {matcher}"
        if not (matcher.startswith("^") or matcher.startswith("(^|[.])")) or not matcher.endswith("$"):
            out.append(("R5", label, "matcher is not anchored with ^ or (^|[.]) and $"))
            continue
        try:
            rx = re.compile(matcher)
        except re.error as exc:
            out.append(("R5", label, f"matcher does not compile: {exc}"))
            continue
        if not any(rx.search(n) for n in names):
            out.append(("R5", label, "matcher matches no running node id"))
    return out


def r6_script_text(nodes_raw: dict[str, str], raw_lines: list[str]) -> list[tuple]:
    """R6: no line inside a script= attribute starts with `#` after its indentation, and no
    backslash other than \\" appears anywhere in the .fabro file, comments included (invariants-
    graph.md, the syntax rows: `#` is a DOT comment; `\\"` is the only backslash).
    """
    out = []
    for nid, script in nodes_raw.items():
        for line in script.splitlines():
            if line.lstrip().startswith("#"):
                out.append(("R6", nid, "a line of script starts with #"))
                break
    for lineno, line in enumerate(raw_lines, 1):
        if re.search(r'\\(?!")', line):
            out.append(("R6", f"line {lineno}", "a backslash other than \\\" in the .fabro file"))
    return out


def r7_checkpoint_hooks(package: str, root: Graph, phases: list[Graph], import_ids: dict[str, str],
                        hooks: list[dict]) -> list[tuple]:
    """R7: each graph that runs a review-merge report node, imported or its own copy (ralph's),
    has a checkpoint_saved hook for it (invariants-merge.md, Reporting). R7_EXCEPTIONS names the
    omitted ones.
    """
    out = []
    saved = [h for h in hooks if h.get("event") == "checkpoint_saved" and h.get("matcher")]
    targets = [(report, report) for report in REPORT_NODES if report in root.nodes]
    for x, phase in import_ids.items():
        graph = next(g for g in phases if g.name == phase)
        targets += [(report, f"{x}.{report}") for report in REPORT_NODES if report in graph.nodes]
    for report, target in targets:
        if (package, report) in R7_EXCEPTIONS:
            continue
        if not any(re.search(h["matcher"], target) for h in saved):
            out.append(("R7", report, f"no checkpoint_saved hook for {target}"))
    return out


def r11_io_hooks(package: str, toml: dict, import_ids: dict[str, str], manifest: dict | None,
                 hooks: list[dict]) -> list[tuple]:
    """R11: a package that declares the `io` MCP server has the `io-stage` hook (stage_start on
    ^agent$, `fabro-io stage`) and the `git-guard` hook (pre_tool_use on ^shell$, `fabro-io
    git-guard`), and every stage with Sealed paths in the Stage manifest is matched by a
    pre_tool_use hook that runs `fabro-io guard` (invariants-graph.md, Stage I/O). Without
    io-stage no stage has a visit, so every receipt gate reads every agent stage as a miss.
    """
    mcps = ((toml.get("run") or {}).get("agent") or {}).get("mcps") or {}
    if "io" not in mcps:
        return []
    out = []

    def has(event: str, target: str, script: str) -> bool:
        return any(h.get("event") == event and h.get("matcher") and re.search(h["matcher"], target)
                   and script in h.get("script", "") for h in hooks)

    if not has("stage_start", "agent", "fabro-io stage"):
        out.append(("R11", "io-stage", "no stage_start hook runs `fabro-io stage` on ^agent$"))
    if not has("pre_tool_use", "shell", "fabro-io git-guard"):
        out.append(("R11", "git-guard", "no pre_tool_use hook runs `fabro-io git-guard` on ^shell$"))
    if manifest is None:
        return out
    sealed = [sid for sid, st in ((manifest.get("workflows") or {}).get(package) or {}).get("stages", {}).items()
              if st.get("sealed")]
    for x, phase in import_ids.items():
        stages = (manifest.get("phases") or {}).get(phase) or {}
        sealed += [f"{x}.{sid}" for sid, st in stages.items() if isinstance(st, dict) and st.get("sealed")]
    for sid in sealed:
        if not has("pre_tool_use", sid, "fabro-io guard"):
            out.append(("R11", sid, "has Sealed paths but no pre_tool_use hook runs `fabro-io guard` on it"))
    return out


def r8_routing(nodes_raw: dict[str, dict[str, str]]) -> list[tuple]:
    """R8: a command node whose script contains context_updates declares output_schema="routing",
    and a node that declares it prints context_updates (invariants-graph.md, the routing row).
    The rule moved here from ops/check-routing-schemas.py (D6).
    """
    out = []
    for nid, attrs in nodes_raw.items():
        script = attrs.get("script")
        if script is None:
            continue
        declares = attrs.get("output_schema") == "routing"
        prints = "context_updates" in script
        if declares and not prints:
            out.append(("R8", nid, "declares output_schema routing but never prints context_updates"))
        elif prints and not declares:
            out.append(("R8", nid, "prints context_updates but declares no output_schema routing"))
    return out


def r9_needs_human_reason(graph: Graph) -> list[tuple]:
    """R9: every edge into mark_needs_human lands on a node that records a reason for the stage
    that failed into it. A failed agent node writes none, so the sink derives it from the
    engine's failure_signature (stdin_source) and writes both reason files (issue #19,
    invariants-merge.md). A sink that does not leaves the PR labelled with `unspecified`.
    """
    sinks = {b for _, b, _ in graph.edges if b.rsplit(".", 1)[-1] == "mark_needs_human"}
    out = []
    for nid in sorted(sinks):
        attrs = graph.nodes.get(nid, {})
        script = attrs.get("script", "")
        derives = (attrs.get("stdin_source") == "failure_signature"
                   and "$SIG" in script
                   and "> /tmp/fabro/needs_human_reason" in script
                   and "> /tmp/fabro/merge_block_reason" in script)
        if not derives:
            out.append(("R9", nid, "has edges into it but does not derive a reason from "
                                   'stdin_source="failure_signature" and write needs_human_reason '
                                   "and merge_block_reason"))
    return out


# --------------------------------------------------------------------------- #
# R10: ralph's copies of backlog's entry nodes and the shared merge stage
# --------------------------------------------------------------------------- #
# ADR 0029 D4 / docs/ralph C8. `ralph` owns its merge stage, so its copies of the
# shared phase's command nodes and of backlog's entry nodes must not drift from their
# originals: a fix to a shared node now needs a decision about the copy, and this rule
# is what forces one. A row is (ralph node, original .fabro, original node id,
# replacements); the original's `script` with every replacement applied, in order, must
# equal the copy's, and every replacement's `old` must occur in the original exactly
# once. Attributes other than `script` (and `prompt`, for the prompt rows) are not
# compared: a label or a class may differ.
#
# A copy that must differ in a way a replacement cannot express is NOT in the table;
# its `//` comment above the node says why. `merge_prep` is `rm_setup` under another id,
# which is why the row's replacements are empty and the node text is not.

RALPH_GRAPH = ".fabro/workflows/ralph/workflow.fabro"

R10_COPIES: list[tuple[str, str, str, tuple[tuple[str, str], ...]]] = [
    ('claim', '.fabro/workflows/backlog/workflow.fabro', 'claim', (
    )),
    ('prep', '.fabro/workflows/backlog/workflow.fabro', 'prep', (
        ('echo 0 > /tmp/fabro/extra_round\necho 0 > /tmp/fabro/split_rounds\necho 0 > /tmp/fabro/tasks_coded\nrm -f /tmp/fabro/remainder.json /tmp/fabro/remainder_issue /tmp/fabro/stuck_reason.md',
         'echo 0 > /tmp/fabro/iterations\necho 0 > /tmp/fabro/post_iterations\necho 0 > /tmp/fabro/refute_visits\necho 0 > /tmp/fabro/final_fix_attempts\necho 0 > /tmp/fabro/plan_attempts\necho pre > /tmp/fabro/phase\nrm -f /tmp/fabro/remainder.json /tmp/fabro/remainder_issue /tmp/fabro/stuck_reason.md\nrm -f /tmp/fabro/loop_exhausted /tmp/fabro/progress.md /tmp/fabro/task-map.md'),
        ("else echo 'agent guide: could not write .codex/instructions.md' >&2; fi",
         'else echo \'agent guide: could not write .codex/instructions.md\' >&2; fi\njq -r \'.body // ""\' /tmp/fabro/issue.json 2>/dev/null | awk \'/^## Task map$/{s=1; next} s && /^## /{s=0} s && !/^[[:space:]]*$/{print}\' > /tmp/fabro/task-map.md\nif [ ! -s /tmp/fabro/task-map.md ]; then rm -f /tmp/fabro/task-map.md; fi'),
    )),
    ('excerpts', '.fabro/workflows/backlog/workflow.fabro', 'excerpts', (
    )),
    ('autofix', '.fabro/workflows/backlog/workflow.fabro', 'autofix', (
    )),
    ('check', '.fabro/workflows/backlog/workflow.fabro', 'validate', (
        ('rm -f /tmp/fabro/rework.prev /tmp/fabro/task_check.json /tmp/fabro/task_check_error.log',
         'rm -f /tmp/fabro/check_ok /tmp/fabro/rework.prev /tmp/fabro/task_check.json /tmp/fabro/task_check_error.log'),
        ("echo 'task validation: mode='$MODE' reason='$REASON' ok='$OK'",
         'if [ "$OK" = true ]; then echo 1 > /tmp/fabro/check_ok; fi\necho \'task validation: mode=\'$MODE\' reason=\'$REASON\' ok=\'$OK\''),
    )),
    ('resolve_merge_gate', '.fabro/workflows/backlog/workflow.fabro', 'resolve_merge_gate', (
    )),
    ('final_merge', '.fabro/workflows/backlog/workflow.fabro', 'final_merge', (
    )),
    ('hygiene', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'hygiene', (
    )),
    ('open_pr', '.fabro/workflows/backlog/workflow.fabro', 'open_pr', (
        ('N=$(jq -r .number /tmp/fabro/issue.json)\nT=$(cat /tmp/fabro/pr_title.txt)',
         'N=$(jq -r .number /tmp/fabro/issue.json)\nT=$(cat /tmp/fabro/pr_title.txt)\nif [ -s /tmp/fabro/loop_exhausted ]; then\n  { echo; echo \'### Unmet criteria\'; echo; echo \'The loop ran out before these Tasks passed, so this pull request must not merge automatically:\'; echo; jq -r \'.[] | select(.status != "passed") | "- " + (.title // .id) + " (" + (.status // "open") + ")"\' /tmp/fabro/tasks.json; } >> /tmp/fabro/pr_body.md\nfi'),
    )),
    ('file_remainder', '.fabro/workflows/backlog/workflow.fabro', 'file_remainder', (
    )),
    ('pr_handoff', '.fabro/workflows/backlog/workflow.fabro', 'pr_handoff', (
        ('MM=watch\nif [ "$(cat /tmp/fabro/lane 2>/dev/null || echo standard)" = fast ] && [ ! -e /tmp/fabro/remainder_kind ]; then',
         'echo post > /tmp/fabro/phase\nMM=watch\nif false; then'),
        ('echo $MM > /tmp/fabro/merge_mode',
         'echo $MM > /tmp/fabro/merge_mode'),
    )),
    ('merge_prep', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'rm_setup', (
    )),
    ('deliver', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'deliver', (
    )),
    ('merge_gate', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'merge_gate', (
    )),
    ('watch_checks', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'watch_checks', (
        ('    if [ "$NOW" -ge "$DL" ]; then blocked \'The 60-minute merge budget is exhausted; the merge phase was abandoned before CI settled.\'; fi',
         'printf \'%s\' \'The 60-minute merge budget ran out with CI still pending, so the pull request was approved and handed to merge-queue.\' > /tmp/fabro/merge_block_reason\n    echo \'{"context_updates":{"checks_ok":false,"checks_blocked":false,"checks_deadline":true,"gh_fix_attempts":\'$A\'}}\'\n    exit 0'),
        ("    if [ $((NOW - T0)) -ge 2820 ]; then blocked 'CI had not settled after 47 minutes of waiting for checks; the merge phase stopped before its watch timeout. A human can merge this PR once its checks are green.'; fi",
         'printf \'%s\' \'CI had not settled after 47 minutes, so the pull request was approved and handed to merge-queue rather than blocking it.\' > /tmp/fabro/merge_block_reason\n    echo \'{"context_updates":{"checks_ok":false,"checks_blocked":false,"checks_deadline":true,"gh_fix_attempts":\'$A\'}}\'\n    exit 0'),
    )),
    ('merge', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'merge', (
    )),
    ('remerge_base', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'remerge_base', (
        ('    echo 1 > /tmp/fabro/resolve_base_attempts\n    git rev-parse HEAD > /tmp/fabro/resolve_base_pre',
         '    echo 1 > /tmp/fabro/resolve_base_attempts\n    PI=$(cat /tmp/fabro/post_iterations 2>/dev/null || echo 0)\n    case "$PI" in \'\' | *[!0-9]*) PI=0 ;; esac\n    echo $((PI + 1)) > /tmp/fabro/post_iterations\n    git rev-parse HEAD > /tmp/fabro/resolve_base_pre'),
    )),
    ('resolve_base_gate', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'resolve_base_gate', (
        ('resolved the conflict; validate, the diff-hygiene counters and the refuter then ran again on the result.',
         'resolved the conflict; the final validation, the diff-hygiene counters and the review then ran again on the result.'),
    )),
    ('report_merged', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'report_merged', (
        ('echo merged > /tmp/fabro/review_merge_outcome',
         'echo report_merged > /tmp/fabro/ending\necho merged > /tmp/fabro/review_merge_outcome'),
    )),
    ('report_blocked', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'report_blocked', (
        ('echo blocked > /tmp/fabro/review_merge_outcome',
         'echo report_blocked > /tmp/fabro/ending\necho blocked > /tmp/fabro/review_merge_outcome'),
    )),
    ('report_queued', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'report_queued', (
        ('echo queued > /tmp/fabro/review_merge_outcome',
         'echo report_queued > /tmp/fabro/ending\necho queued > /tmp/fabro/review_merge_outcome'),
    )),
    ('mark_needs_human', '.fabro/workflows/_shared/review-merge/review-merge.fabro', 'mark_needs_human', (
        ("    *' standards '*|*' spec '*|*' review_fix '*|*' refute '*|*' ci_fix_t1 '*|*' ci_fix_t2 '*) FA=yes ;;",
         "    *' standards '*|*' spec '*|*' refute '*|*' resolve_base '*|*' resolve_merge '*) FA=yes ;;"),
        ('echo needs_human > /tmp/fabro/review_merge_outcome',
         'echo mark_needs_human > /tmp/fabro/ending\necho needs_human > /tmp/fabro/review_merge_outcome'),
    )),
    ('close_noop', '.fabro/workflows/backlog/workflow.fabro', 'close_noop', (
        ("S=$(jq -r '.summary // empty' /tmp/fabro/decomposition.json 2>/dev/null || true)",
         "S=$(jq -r '.summary // empty' /tmp/fabro/task_list.json 2>/dev/null || true)"),
        ("echo 'Agent run found no actionable decomposition for this issue; leaving it for human triage.'",
         "echo 'Agent run found no actionable plan for this issue; leaving it for human triage.'"),
        ("echo 'The decomposer reported:'",
         "echo 'The planner reported:'"),
        ('echo done',
         'echo close_noop > /tmp/fabro/ending\necho done'),
    )),
    ('return_to_triage', '.fabro/workflows/backlog/workflow.fabro', 'return_to_triage', (
        ('(if .source == "decompose" then "the decomposer" else "the coder" end)',
         '(if .source == "decompose" then "the decomposer" elif .source == "plan" then "the planner" else "the coder" end)'),
        ('jq -nc --arg r "$R" \'{context_updates:{return_reason:$r}}\'',
         'echo return_to_triage > /tmp/fabro/ending\njq -nc --arg r "$R" \'{context_updates:{return_reason:$r}}\''),
    )),
    ('mark_stuck', '.fabro/workflows/backlog/workflow.fabro', 'mark_stuck', (
        ('  if [ -s /tmp/fabro/draft_reason.md ]; then\n    gh issue edit $N --remove-label Review 2>/dev/null || true\n    cp /tmp/fabro/draft_reason.md /tmp/fabro/stuck_comment.md\n  elif [ -s /tmp/fabro/stuck_reason.md ]; then',
         '  if [ -s /tmp/fabro/stuck_reason.md ]; then'),
        ('echo done',
         'echo mark_stuck > /tmp/fabro/ending\necho done'),
    )),
]

R10_PROMPTS: list[tuple[str, str, str, tuple[tuple[str, str], ...]]] = [
]


def _script_of(path: Path, node: str) -> str | None:
    if not path.exists():
        return None
    attrs = Graph(read_graph(path.read_text())).nodes.get(node)
    return None if attrs is None else attrs.get("script")


def _prompt_of(path: Path, node: str) -> Path | None:
    if not path.exists():
        return None
    attrs = Graph(read_graph(path.read_text())).nodes.get(node) or {}
    ref = attrs.get("prompt", "")
    if not ref.startswith("@"):
        return None
    return (path.parent / ref[1:]).resolve()


def _apply(text: str, repls) -> tuple[str | None, str | None]:
    """The original with its replacements applied, or (None, why) when one does not fit."""
    for old, new in repls:
        n = text.count(old)
        if n != 1:
            return None, f"replacement occurs {n} times in the original: {old[:60]!r}"
        text = text.replace(old, new)
    return text, None


def r10_copies(root: Path) -> list[tuple]:
    """R10: every ralph copy still equals its original plus its recorded replacements."""
    graph = root / RALPH_GRAPH
    if not graph.exists():
        return []
    out = []
    for target, src, src_id, repls in R10_COPIES:
        original = _script_of(root / src, src_id)
        if original is None:
            out.append(("ralph", "R10", target, f"original node {src_id} is missing from {src}"))
            continue
        expected, why = _apply(original, repls)
        if expected is None:
            out.append(("ralph", "R10", target, why))
            continue
        copy = _script_of(graph, target)
        if copy is None:
            out.append(("ralph", "R10", target, "the copy is missing its script"))
        elif copy != expected:
            out.append(("ralph", "R10", target,
                        f"drifted from {src} {src_id}; record the difference as a replacement or "
                        "take the node out of the table and say why"))
    for target, src, src_id, repls in R10_PROMPTS:
        original_p, copy_p = _prompt_of(root / src, src_id), _prompt_of(graph, target)
        if original_p is None or copy_p is None:
            out.append(("ralph", "R10", target, "a prompt file is missing"))
            continue
        if not original_p.exists() or not copy_p.exists():
            out.append(("ralph", "R10", target, "a prompt file does not exist"))
            continue
        expected, why = _apply(original_p.read_text(), repls)
        if expected is None:
            out.append(("ralph", "R10", target, why))
        elif copy_p.read_text() != expected:
            out.append(("ralph", "R10", target, f"prompt drifted from {src} {src_id}"))
    return out


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


def read_file(path: Path) -> tuple[Graph | None, list[tuple]]:
    """A parsed graph, or a reader violation. The raw text is checked by the caller (R6)."""
    try:
        return Graph(read_graph(path.read_text())), []
    except ReaderError as exc:
        return None, [("reader", path.name, str(exc))]


def phase_violations(package: str, path: Path) -> list[tuple]:
    """The rules that need no splice: the reader, R6 and R8."""
    graph, errs = read_file(path)
    raw = path.read_text().splitlines()
    out = [(package, *e) for e in errs]
    out += [(package, *v) for v in r6_script_text({k: v["script"] for k, v in
                                                  (graph.nodes.items() if graph else []) if "script" in v}, raw)]
    if graph:
        out += [(package, *v) for v in r8_routing(graph.nodes)]
        out += [(package, *v) for v in r9_needs_human_reason(graph)]
    return out


def package_violations(pkg_dir: Path, workflows: Path) -> tuple[list[tuple], int]:
    """Every rule for one root package. Returns the violations and the spliced node count."""
    package = pkg_dir.name
    t = tomllib.loads((pkg_dir / "workflow.toml").read_text())
    graph_path = pkg_dir / t["workflow"]["graph"]
    run_inputs = set((t.get("run", {}).get("inputs") or {}).keys())
    hooks = (t.get("run", {}) or {}).get("hooks", []) or []
    root, errs = read_file(graph_path)
    out = [(package, *e) for e in errs]
    raw = graph_path.read_text().splitlines()
    if root is None:
        out += [(package, *v) for v in r6_script_text({}, raw)]
        return out, 0
    out += [(package, *v) for v in r6_script_text(
        {k: v["script"] for k, v in root.nodes.items() if "script" in v}, raw)]
    imports = imports_of(root)
    phases: dict[str, Graph] = {}
    phase_graphs: list[Graph] = []
    for _, path in imports:
        phase_path = (graph_path.parent / path).resolve()
        if not str(phase_path).startswith(str(workflows.resolve()) + "/"):
            out.append((package, "splice", path, "import is outside .fabro/workflows"))
            continue
        if not phase_path.exists():
            out.append((package, "splice", path, "import does not exist"))
            continue
        pg, perrs = read_file(phase_path)
        out += [(package, *e) for e in perrs]
        if pg is None:
            continue
        pg.name = phase_path.parent.name
        phases[path] = pg
        phase_graphs.append(pg)
    # A phase that did not load leaves the splice incomplete: report it and check the root as written.
    complete = len(phases) == len(imports)
    nodes, edges = splice(root, phases) if complete else (root.nodes, root.edges)
    out += [(package, *v) for v in r1_breaker(root, nodes, edges, imports)]
    out += [(package, *v) for v in r2_stall(root, nodes)]
    out += [(package, *v) for v in r3_phase_classes(root, phase_graphs)]
    out += [(package, *v) for v in r4_stylesheet_and_inputs(root, run_inputs, phase_graphs)]
    running = set(nodes)
    out += [(package, *v) for v in r5_hooks(hooks, running)]
    import_ids = {x: phases[p].name for x, p in imports if p in phases}
    out += [(package, *v) for v in r7_checkpoint_hooks(package, root, phase_graphs, import_ids, hooks)]
    manifest_path = workflows / "_io" / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    out += [(package, *v) for v in r11_io_hooks(package, t, import_ids, manifest, hooks)]
    out += [(package, *v) for v in r8_routing(root.nodes)]
    out += [(package, *v) for v in r9_needs_human_reason(root)]
    return out, len(nodes)


def run(root_dir: Path) -> list[tuple]:
    workflows = root_dir / ".fabro" / "workflows"
    violations: list[tuple] = []
    for pkg in sorted(workflows.iterdir()) if workflows.is_dir() else []:
        if pkg.is_dir() and pkg.name != "_shared" and (pkg / "workflow.toml").exists():
            v, _ = package_violations(pkg, workflows)
            violations += v
    for phase in sorted((workflows / "_shared").glob("*/*.fabro")) if (workflows / "_shared").is_dir() else []:
        violations += phase_violations(f"_shared/{phase.parent.name}", phase)
    violations += r10_copies(root_dir)
    return violations


def spliced_counts(root_dir: Path) -> dict[str, tuple[int, int]]:
    """(nodes, edges) of each root package after the splice, for the baseline check."""
    workflows = root_dir / ".fabro" / "workflows"
    counts = {}
    for pkg in sorted(workflows.iterdir()):
        if not (pkg.is_dir() and (pkg / "workflow.toml").exists()):
            continue
        t = tomllib.loads((pkg / "workflow.toml").read_text())
        graph_path = pkg / t["workflow"]["graph"]
        root = Graph(read_graph(graph_path.read_text()))
        phases = {}
        for _, path in imports_of(root):
            p = (graph_path.parent / path).resolve()
            pg = Graph(read_graph(p.read_text()))
            pg.name = p.parent.name
            phases[path] = pg
        nodes, edges = splice(root, phases)
        counts[pkg.name] = (len(nodes), len(edges))
    return counts


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="The mechanical graph invariants (ADR 0019).")
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    ap.add_argument("--dump", type=Path, help="print the reader's statements for one .fabro file")
    ap.add_argument("--normalize", action="store_true", help="normalize a fabro parse JSON from stdin")
    ap.add_argument("--counts", action="store_true", help="print each package's spliced (nodes, edges)")
    args = ap.parse_args(argv)
    if args.normalize:
        sys.stdout.write(dumps(normalize(json.load(sys.stdin))))
        return 0
    if args.dump:
        try:
            sys.stdout.write(dumps(normalize(read_graph(args.dump.read_text()))))
        except ReaderError as exc:
            print(f"{args.dump.name} reader {exc}")
            return 1
        return 0
    if args.counts:
        for name, (n, e) in spliced_counts(args.root).items():
            print(f"{name} {n}/{e}")
        return 0
    violations = run(args.root)
    for pkg, rule, node, why in violations:
        print(f"{pkg} {rule} {node} {why}")
    print(f"VIOLATIONS: {len(violations)}")
    return min(len(violations), 125)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
