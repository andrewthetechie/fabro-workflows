"""Tests for ops/check-graph-invariants.py, standard library only.

    python3.11 -m unittest discover -s ops/tests

The fixtures under fixtures/graph-invariants/ are one package and one phase (`ok`), and each
`r<N>` breaks exactly rule N. The real tree must have no violation: `make check` runs the checker on it.
"""
import importlib.util
import json
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
        for n in range(1, 10):
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


    def _ok_copy(self, tmp: str) -> Path:
        root = Path(tmp) / "ok"
        shutil.copytree(FIXTURES / "ok", root)
        return root

    def test_r7_covers_a_report_node_of_the_root_graph(self):
        # ralph copies the merge stage instead of importing it, so its report nodes are its own.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ok_copy(tmp)
            graph = root / ".fabro/workflows/ok/workflow.fabro"
            graph.write_text(graph.read_text().replace(
                "    start -> phase -> check -> exit",
                "    report_blocked [shape=parallelogram, label=\"Blocked\", script=\"true\"]\n"
                "    start -> phase -> check -> exit\n    check -> report_blocked -> exit"))
            found = [(rule, node) for _, rule, node, _ in cgi.run(root)]
            self.assertEqual(found, [("R7", "report_blocked")], found)

    def test_r12_needs_an_unconditional_edge_out_of_every_node_with_edges(self):
        # fabro validate's all_conditional_edges: ralph's stop_reason had only conditional ones.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ok_copy(tmp)
            graph = root / ".fabro/workflows/ok/workflow.fabro"
            graph.write_text(graph.read_text().replace(
                "    start -> phase -> check -> exit",
                "    start -> phase -> check\n    check -> exit [condition=\"outcome=succeeded\"]"))
            found = [(rule, node) for _, rule, node, _ in cgi.run(root)]
            self.assertEqual(found, [("R12", "check")], found)
            graph.write_text(graph.read_text().replace(
                '    check -> exit [condition="outcome=succeeded"]',
                '    check -> exit [condition="outcome=succeeded"]\n    check -> exit'))
            self.assertEqual(cgi.run(root), [])

    def test_r11_needs_the_io_hooks_when_the_io_server_is_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ok_copy(tmp)
            toml = root / ".fabro/workflows/ok/workflow.toml"
            toml.write_text(toml.read_text() + '\n[run.agent.mcps.io]\ntype = "sandbox"\n')
            found = sorted((rule, node) for _, rule, node, _ in cgi.run(root))
            self.assertEqual(found, [("R11", "git-guard"), ("R11", "io-stage")], found)
            toml.write_text(toml.read_text().replace(
                'matcher = "^agent$"', 'matcher = "^agent$"\nscript = "fabro-io stage"') +
                '\n[[run.hooks]]\nevent = "pre_tool_use"\nmatcher = "^shell$"\n'
                'script = "fabro-io git-guard; [ $? -eq 2 ] && exit 2; exit 0"\n')
            self.assertEqual(cgi.run(root), [])

    def test_r11_needs_io_guard_on_every_stage_with_sealed_paths(self):
        # The manifest names the sealed stages: the root package's own, and an imported phase's
        # under its import id.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._ok_copy(tmp)
            toml = root / ".fabro/workflows/ok/workflow.toml"
            toml.write_text(toml.read_text().replace(
                'matcher = "^agent$"', 'matcher = "^agent$"\nscript = "fabro-io stage"') +
                '\n[[run.hooks]]\nevent = "pre_tool_use"\nmatcher = "^shell$"\nscript = "fabro-io git-guard"\n'
                '\n[run.agent.mcps.io]\ntype = "sandbox"\n')
            io = root / ".fabro/workflows/_io"
            io.mkdir()
            (io / "manifest.json").write_text(json.dumps({
                "workflows": {"ok": {"stages": {"check": {"sealed": ["/tmp/fabro/x"], "box": False}}}},
                "phases": {"phase": {"work": {"sealed": ["/tmp/fabro/y"], "box": False}}}}))
            found = sorted((rule, node) for _, rule, node, _ in cgi.run(root))
            self.assertEqual(found, [("R11", "check"), ("R11", "phase.work")], found)
            toml.write_text(toml.read_text().replace(
                "[run.agent.mcps.io]",
                '[[run.hooks]]\nevent = "pre_tool_use"\nmatcher = "^check$"\nscript = "fabro-io guard"\n\n'
                "[run.agent.mcps.io]"))
            found = [(rule, node) for _, rule, node, _ in cgi.run(root)]
            self.assertEqual(found, [("R11", "phase.work")], found)


class BoxStages(unittest.TestCase):
    """R13 and R14: the Stage manifest's `box` values against the stylesheets
    (more-local-inference C1, C5)."""

    # A stylesheet where the phase's only agent stage runs on the box everywhere.
    BOX_EVERYWHERE = "* { model: glm-5.3; }\n.work { model: {{ inputs.coder_pool | default('coders-a') }}; }"
    # The same, but only on the box-plan trial's arm.
    PLAN_ARM = ("* { model: glm-5.3; }\n"
                "{% if (inputs.issue_number | default(0) | int) // 2 % 2 == 1 %}\n"
                ".work { model: {{ inputs.coder_pool | default('coders-a') }}; }\n"
                "{% else %}\n.work { model: glm-5.3; }\n{% endif %}")

    def root(self, tmp: str, *, stylesheet: str | None = None, box: object = True,
             drop_class: bool = False, extra: dict | None = None) -> Path:
        root = Path(tmp) / "ok"
        shutil.copytree(FIXTURES / "ok", root)
        graph = root / ".fabro/workflows/ok/workflow.fabro"
        if stylesheet is not None:
            text = graph.read_text()
            head = 'model_stylesheet="'
            start = text.index(head) + len(head)
            graph.write_text(text[:start] + stylesheet + text[text.index('"', start):])
        # The fixture's phase node is a plain label; make it an agent stage so the manifest
        # has one to describe. `check` is a command node, so `phase.work` is the only agent.
        phase = root / ".fabro/workflows/_shared/phase/phase.fabro"
        node = 'work [label="Work", prompt="Do the work"'
        phase.write_text(phase.read_text().replace(
            'work [label="Work", class="work"]',
            node + ("" if drop_class else ', class="work"') + "]"))
        stages = {} if extra is None else extra
        io = root / ".fabro/workflows/_io"
        io.mkdir()
        work = {"box": box} if box is not None else {}
        (io / "manifest.json").write_text(json.dumps({
            "workflows": {"ok": {"stages": stages}},
            "phases": {"phase": {"work": work}},
        }))
        return root

    def r13(self, root: Path) -> list[tuple[str, str]]:
        return [(rule, node) for _, rule, node, _ in cgi.run(root) if rule.startswith("R1")]

    def r14(self, root: Path) -> list[tuple[str, str]]:
        return [(rule, node) for _, rule, node, _ in cgi.run(root) if rule == "R14"]

    def test_a_box_stage_the_stylesheet_agrees_with_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.BOX_EVERYWHERE)
            self.assertEqual(cgi.run(root), [])

    def test_a_missing_box_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.BOX_EVERYWHERE, box=None)
            found = self.r13(root)
            self.assertEqual(found, [("R13", "phase.work")], found)
            self.assertIn("no `box`", dict((n, w) for _, _, n, w in cgi.run(root))["phase.work"])

    def test_a_box_for_a_node_that_does_not_exist_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.BOX_EVERYWHERE, extra={"ghost": {"box": False}})
            found = self.r13(root)
            self.assertEqual(found, [("R13", "ghost")], found)

    def test_an_arm_stage_under_another_condition_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The stage is on the box on one arm, as `box_mode` requires, but the trial's
            # arithmetic is what `box-claim` hard-codes, so a different condition is a bug.
            wrong = ("* { model: glm-5.3; }\n"
                     "{% if (inputs.issue_number | default(0) | int) // 2 % 2 == 0 %}\n"
                     ".work { model: {{ inputs.coder_pool | default('coders-a') }}; }\n"
                     "{% else %}\n.work { model: glm-5.3; }\n{% endif %}")
            root = self.root(tmp, stylesheet=wrong, box="plan-arm")
            found = self.r13(root)
            self.assertEqual(found, [("R13", "phase.work")], found)
            self.assertIn("not the arm rule", cgi.run(root)[0][3])

    def test_the_arm_stage_under_the_trial_condition_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.PLAN_ARM, box="plan-arm")
            self.assertEqual(cgi.run(root), [])

    def test_a_phase_map_that_disagrees_with_the_importer_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.BOX_EVERYWHERE, box={"ok": False})
            found = self.r13(root)
            self.assertEqual(found, [("R13", "phase.work")], found)
            self.assertIn("resolves to box=true", cgi.run(root)[0][3])

    def test_an_agent_node_with_no_class_under_a_coders_star_rule_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            # `ok`'s `*` is `coders-a`: a node with no class runs on box-a whatever the lease.
            root = self.root(tmp, drop_class=True)
            rules = [rule for _, rule, _, _ in cgi.run(root)]
            self.assertIn("R13", rules)
            self.assertTrue(any("literal box model coders-a" in why for _, _, _, why in cgi.run(root)))

    def test_a_bg_fallback_key_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.BOX_EVERYWHERE)
            toml = root / ".fabro/workflows/ok/workflow.toml"
            toml.write_text(toml.read_text() + '\n[run.model.fallbacks]\n"coders-a-bg" = ["glm-5.3"]\n')
            found = self.r14(root)
            self.assertEqual(found, [("R14", "coders-a-bg")], found)

    def test_a_bg_fallback_target_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp, stylesheet=self.BOX_EVERYWHERE)
            toml = root / ".fabro/workflows/ok/workflow.toml"
            toml.write_text(toml.read_text() + '\n[run.model.fallbacks]\ncoders-a = ["box-a:coders-a-bg"]\n')
            found = self.r14(root)
            self.assertEqual(found, [("R14", "coders-a")], found)

    def test_the_arm_rule_parser_tolerates_the_percent_in_its_arithmetic(self):
        variants = cgi.stylesheet_variants(self.PLAN_ARM)
        self.assertEqual(len(variants), 2)
        self.assertEqual({tuple(sorted(h.items())) for h, _ in variants},
                         {(("(inputs.issue_number | default(0) | int) // 2 % 2 == 1", True),),
                          (("(inputs.issue_number | default(0) | int) // 2 % 2 == 1", False),)})

    def test_the_real_manifest_and_stylesheets_agree(self):
        manifest = json.loads((REAL / ".fabro/workflows/_io/manifest.json").read_text())
        for package in manifest["workflows"]:
            with self.subTest(package=package):
                pkg = REAL / ".fabro/workflows" / package
                found = cgi.package_violations(pkg, REAL / ".fabro/workflows")[0]
                self.assertEqual([v for v in found if v[1] in ("R13", "R14")], [])


class Counts(unittest.TestCase):
    def test_spliced_counts_match_the_agents_baselines(self):
        self.assertEqual(cgi.spliced_counts(REAL), {
            # gate-relief task 05 (#29): refute_fix and refute_fix_gate, +2 nodes and +5 edges in
            # each importer of _shared/review-merge. #37: resolve_base and resolve_base_gate, +2 nodes
            # and +6 edges in each importer. #35: drop_unreviewed and final_fix_prep, +2 nodes and +9
            # edges in backlog only. #17 (docs/unmet-criteria): return_to_triage and task_block_gate,
            # +2 nodes and +6 edges in backlog only. Closing the plan trial (#25, ADR 0030) removed
            # `improve` (-1 node and -2 edges) and the three arm-split edge pairs (-3 edges); the
            # trial had added plan at +1 node and +5 edges. `fabro validate` (check-host) confirms them.
            "backlog": (81, 205),
            "pr-review": (39, 87),
            "issue-triage": (16, 36),
            "arch-review": (24, 54),
            # Task 02 skeleton (ADR 0022). Task 10 records the `fabro validate` baseline.
            "dependabot": (51, 115),
            # Task 05 skeleton (fast-path series): no agent stage. Expected, unverified until check-host.
            "merge-queue": (8, 10),
            # Scout (ADR 0027, task 04): no agent stage. Confirmed by `make check-host`
            # on 2026-10-16 (task 07 of docs/scout/).
            "scout": (9, 11),
            # Ralph (ADR 0029, docs/ralph). The series builds the package over tasks 02-08, so
            # this pair grows with each one; it is the offline parser's count, not a
            # `fabro validate` baseline. Task 12 records the validated numbers in AGENTS.md.
            "ralph": (51, 135),
        })


class Copies(unittest.TestCase):
    """R10: ralph's copies must equal their originals plus the recorded replacements."""

    def _root(self, tmp, copy_script, original_script):
        root = Path(tmp)
        (root / ".fabro/workflows/ralph").mkdir(parents=True)
        (root / ".fabro/workflows/orig").mkdir(parents=True)
        (root / ".fabro/workflows/orig/workflow.fabro").write_text(
            f'digraph O {{ a [script="{original_script}"] }}\n')
        (root / ".fabro/workflows/ralph/workflow.fabro").write_text(
            f'digraph R {{ b [script="{copy_script}"] }}\n')
        return root

    def _violations(self, tmp, copy_script, original_script, repls):
        saved = cgi.R10_COPIES, cgi.R10_PROMPTS
        try:
            cgi.R10_COPIES = [("b", ".fabro/workflows/orig/workflow.fabro", "a", tuple(repls))]
            cgi.R10_PROMPTS = []
            return cgi.r10_copies(self._root(tmp, copy_script, original_script))
        finally:
            cgi.R10_COPIES, cgi.R10_PROMPTS = saved

    def test_a_byte_identical_copy_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._violations(tmp, "echo hi", "echo hi", []), [])

    def test_one_character_of_drift_fails_and_names_the_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = self._violations(tmp, "echo ho", "echo hi", [])
            self.assertEqual([(r, n) for _, r, n, _ in found], [("R10", "b")], found)
            self.assertIn("drifted", found[0][3])

    def test_a_replacement_that_occurs_twice_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = self._violations(tmp, "echo hi one", "echo hi one; echo hi one", [("echo hi", "echo hey")])
            self.assertEqual(len(found), 1, found)
            self.assertIn("occurs 2 times", found[0][3])

    def test_a_replacement_that_does_not_occur_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            found = self._violations(tmp, "echo hi", "echo hi", [("echo bye", "echo hey")])
            self.assertEqual(len(found), 1, found)
            self.assertIn("occurs 0 times", found[0][3])

    def test_a_recorded_replacement_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                self._violations(tmp, "echo hey", "echo hi", [("echo hi", "echo hey")]), [])


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
