#!/usr/bin/env python3
"""Catch the routing-schema mismatch that fabro only reports at runtime.

A command node is wrong in two symmetric ways, and `fabro validate` accepts both:

  declares output_schema="routing" but never prints a routing object
      -> the stage FAILS on schema validation. For a command node that failure is
         deterministic and non-retryable: no repair turn, no retry, and the run
         takes its unconditional edge. Cost one run on 2026-09-17, at pr_handoff,
         with the PR already open.

  prints context_updates but declares no schema
      -> fabro never scans the node's stdout. Routing goes inert, the run walks its
         unconditional edges, and no error appears anywhere. This is the failure the
         main AGENTS.md invariant table already names; it cost a live debugging
         round before that.

Reads the parsed AST rather than the DOT text on purpose. Slicing node blocks out of
the source with a regex does not survive scripts containing `if [ ... ]`, which looks
exactly like the start of a node declaration -- that produced two false positives on
the first attempt at this check.

Run it in the container, where the fabro binary lives:

    rsync -a --delete .fabro/ andrew@<host>:/tmp/check/
    ssh <host> 'docker exec fabro-fabro-1 rm -rf /tmp/check \
      && docker cp /tmp/check fabro-fabro-1:/tmp/check'
    ssh <host> 'cd ~/fabro && python3 /tmp/check-routing-schemas.py \
      /tmp/check/workflows/*/workflow.fabro \
      /tmp/check/workflows/_shared/*/*.fabro'

Exit status is the number of mismatches, so it drops into a pre-push hook.
"""
import json, subprocess, sys, pathlib

def nodes_of(ast):
    found = []
    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "attrs" in o:
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(ast)
    return found

def attr(node, name):
    attrs = node["attrs"] if isinstance(node["attrs"], list) else []
    for entry in attrs:
        if isinstance(entry, list) and len(entry) == 2 and entry[0] == name:
            return entry[1].get("Str") if isinstance(entry[1], dict) else None
    return None

def main(paths):
    bad = 0
    for wf in paths:
        out = subprocess.run(
            ["docker", "compose", "exec", "-T", "fabro", "fabro", "parse", wf],
            capture_output=True, text=True)
        try:
            ast = json.loads(out.stdout)
        except Exception as exc:
            print(f"{wf}: could not read the parsed AST: {exc}")
            bad += 1
            continue
        for node in nodes_of(ast):
            script = attr(node, "script")
            if not script:
                continue
            declares = attr(node, "output_schema") == "routing"
            prints = "context_updates" in script
            if declares == prints:
                continue
            why = ("declares routing but never prints context_updates"
                   if declares else
                   "prints context_updates but declares no routing schema")
            print(f"  {pathlib.Path(wf).parent.name:<16} {node['id']:<20} {why}")
            bad += 1
    print(f"MISMATCHES: {bad}")
    return bad

if __name__ == "__main__":
    sys.exit(min(main(sys.argv[1:]), 125))
