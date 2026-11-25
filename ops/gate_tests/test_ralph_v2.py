"""ralph-v2 (ADR 0034, docs/plans/ralph-v2/05): the package skeleton's own rules.

Everything ralph-v2 copies from ralph is held by R10 in `ops/check-graph-invariants.py`, and
the graphs' behaviour by the gate tests that already cover ralph's nodes. What no other gate
covers is the file itself: the profile images run Debian, where `/bin/sh` is dash and rejects
a bashism at parse time, a template token left in a node renders as the literal text, and the
dispatch payload has a size the server refuses.

Pin what task 05 decided and nothing more: the fourth rung is gone from the edges, and the
graph stays inside the size the plan gives it (`P14`: under 350,000 bytes).
"""

from harness import (RAPH2, count, edges, extract, script_nodes, syntax_ok, write)
# The two substitutions the server makes in a command node's script before it runs. Every
# other `{{ }}` in an attribute is literal text, so a render token somewhere else is a bug
# only this can see: `fabro validate` accepts it.
SUBSTITUTIONS = (
    ("{{ inputs.issue_number }}", "42"),
    ("{{ inputs.coder_pool }}", "coders-b"),
)

# P14's cap on the package's graph file. The plan's own number, not a measured limit.
GRAPH_MAX_BYTES = 350_000


def test_every_command_node_is_posix_sh(ck, tmp_path):
    """`sh -n` under dash when the harness has it, the shell the images have (conftest)."""
    nodes = script_nodes(RAPH2)
    ck("the graph has command nodes to check", "true", str(bool(nodes)).lower())
    for node in nodes:
        script = extract(RAPH2, node)
        for token, value in SUBSTITUTIONS:
            script = script.replace(token, value)
        path = write(tmp_path / f"{node}.sh", script)
        ok, err = syntax_ok(path)
        ck(f"{node}: valid POSIX sh", "true", "true" if ok else (err or "sh -n failed"))


def test_no_script_carries_a_bashism_or_a_python_leak(ck):
    """`<<<` is a here-string dash does not know, and `read_text` is a Python leftover.

    `<<<<<<<` is a git conflict marker and not a here-string, so it is cut before the count:
    `resolve_merge_gate`, `resolve_base_gate` and `open_pr` grep for it on purpose.
    """
    for node in script_nodes(RAPH2):
        script = extract(RAPH2, node).replace("<<<<<<<", "")
        ck(f"{node}: no here-string", "0", count("<<<", script, flags="F"))
        ck(f"{node}: no Python", "0", count("read_text", script, flags="F"))


def test_only_claim_keeps_a_render_token(ck):
    """`claim` is the one node whose script interpolates inputs: the issue number and the
    box's pool. Anywhere else a `{{` is text the agent or the shell reads."""
    for node in script_nodes(RAPH2):
        script = extract(RAPH2, node)
        if node == "claim":
            ck("claim: the issue number", "1", count("{{ inputs.issue_number }}", script, flags="F"))
            ck("claim: the coder pool", "1", count("{{ inputs.coder_pool }}", script, flags="F"))
            ck("claim: and nothing else", "2", count("{{", script, flags="F"))
            continue
        ck(f"{node}: no unexpanded template", "0", count("{{", script, flags="F"))


def test_the_graph_fits_its_size_cap(ck):
    size = len(RAPH2.read_bytes())
    ck("the graph is under 350,000 bytes (P14)", "true", str(size < GRAPH_MAX_BYTES).lower())


def test_the_fourth_rung_is_gone_from_the_edges(ck):
    """E16's ladder is three rungs, so no edge reads `rung=4` and no node offers one."""
    e = edges(RAPH2)
    ck("the graph has edges to check", "true", str(bool(e)).lower())
    ck("no edge reaches iterate_t4", "0", count("iterate_t4", e, flags="F"))
    ck("no edge reads the fourth rung", "0", count("rung=4", e, flags="F"))
    ck("excerpts still routes the three it has", "3", count("excerpts->iterate_t", e, flags="F"))
