"""Tests for ops/check-graph-invariants.py, standard library only.

    python3.11 -m unittest discover -s ops/tests

The fixtures under fixtures/graph-invariants/ are one package and one phase (`ok`), and each
`r<N>` breaks exactly rule N. The real tree must have no violation: `make check` runs the checker on it.
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cgi", HERE.parent / "check-graph-invariants.py")
cgi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cgi)
FIXTURES = HERE / "fixtures/graph-invariants"
REAL = HERE.parent.parent

def rules_of(root: Path) -> list[str]:
    return sorted(rule for _, rule, _, _ in cgi.run(root))


class Fixtures(unittest.TestCase):
    def test_ok_reports_nothing(self):
        self.assertEqual(cgi.run(FIXTURES / "ok"), [])

    def test_each_break_reports_its_rule_and_nothing_else(self):
        for n in range(1, 9):
            with self.subTest(rule=f"R{n}"):
                found = rules_of(FIXTURES / f"r{n}")
                self.assertEqual(found, [f"R{n}"], found)

    def test_r4_reads_the_inputs_of_an_imported_phase(self):
        # A phase renders with the importing package's [run.inputs], so a default there that
        # the package binds is as dead as one in the root graph.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ok"
            shutil.copytree(FIXTURES / "ok", root)
            toml = root / ".fabro/workflows/ok/workflow.toml"
            toml.write_text(toml.read_text() + '\n[run.inputs]\nissue = "1"\n')
            phase = root / ".fabro/workflows/_shared/phase/phase.fabro"
            phase.write_text(phase.read_text().replace(
                'label="Work"', "label=\"Work\", prompt=\"Issue {{ inputs.issue | default('0') }}\""))
            found = [(rule, node) for _, rule, node, _ in cgi.run(root)]
            self.assertEqual(found, [("R4", "phase.work.prompt")], found)


class Counts(unittest.TestCase):
    def test_spliced_counts_match_the_agents_baselines(self):
        self.assertEqual(cgi.spliced_counts(REAL), {
            "backlog": (69, 162),
            "pr-review": (34, 73),
            "issue-triage": (16, 36),
            "arch-review": (24, 54),
        })


class RealTree(unittest.TestCase):
    def test_real_tree_is_clean(self):
        self.assertEqual(cgi.run(REAL), [])


class Reader(unittest.TestCase):
    def test_a_script_with_escaped_quotes_braces_and_a_comment_marker_in_a_string(self):
        text = ('digraph X { c [script="if [ -z \\"$X\\" ]; then echo \'{}\'"]; '
                'd [label="see // here"]; }')
        g = cgi.read_graph(text)
        nodes = {s["Node"]["id"]: dict(s["Node"]["attrs"]) for s in g["statements"] if "Node" in s}
        self.assertEqual(nodes["c"]["script"], "if [ -z \"$X\" ]; then echo '{}'")
        self.assertEqual(nodes["d"]["label"], "see // here")

    def test_subgraph_node_defaults_and_ports_are_refused(self):
        for text in ("digraph X { subgraph cluster_a { a } }",
                     "digraph X { node [shape=box]; a }",
                     "digraph X { edge [color=red]; a -> b }",
                     "digraph X { a:port -> b }",
                     "digraph X { a [label=<html>] }",
                     "graph X { a -- b }"):
            with self.subTest(text=text):
                with self.assertRaises(cgi.ReaderError):
                    cgi.read_graph(text)

    def test_a_chain_is_one_edge_statement_with_its_attributes_once(self):
        g = cgi.read_graph('digraph X { a -> b -> c [condition="outcome=succeeded"] }')
        edges = [s["Edge"] for s in g["statements"] if "Edge" in s]
        self.assertEqual(edges, [{"nodes": ["a", "b", "c"], "attrs": [["condition", "outcome=succeeded"]]}])

    def test_the_duration_units_are_seconds_minutes_and_hours_only(self):
        self.assertEqual(cgi.dur("1h"), 3600)
        self.assertEqual(cgi.dur("15m"), 900)
        self.assertEqual(cgi.dur("30s"), 30)
        for bad in ("10x", "1.5m", "", None, "90"):
            with self.subTest(value=bad):
                self.assertIsNone(cgi.dur(bad))


if __name__ == "__main__":
    unittest.main()
