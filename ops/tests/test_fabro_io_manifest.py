"""Tests for ops/fabro-io-manifest.py: the agent guide (docs/coder-tweaks C1) and the
Stage manifest's `box` field (more-local-inference C1).

    python3.11 -m unittest discover -s ops/tests
"""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fim", HERE.parent / "fabro-io-manifest.py")
fim = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fim)

TOML = f"""[run.environment.env]
{fim.BEGIN}
FABRO_IO_MANIFEST = '''{{}}'''
{fim.END}
OTHER = "x"
"""


class Guide(unittest.TestCase):
    def test_inject_is_idempotent_and_sits_after_the_manifest(self):
        once = fim.inject_guide(TOML, "guide one\n")
        self.assertEqual(fim.inject_guide(once, "guide one\n"), once)
        self.assertLess(once.index(fim.END), once.index(fim.GUIDE_BEGIN))
        self.assertLess(once.index(fim.GUIDE_END), once.index('OTHER = "x"'))

    def test_a_hand_edited_block_is_overwritten(self):
        once = fim.inject_guide(TOML, "guide one\n")
        edited = once.replace("guide one", "guide EDITED")
        self.assertEqual(fim.inject_guide(edited, "guide one\n"), once)
        self.assertNotEqual(edited, once)  # which is what `check` reports as drift

    def test_problems(self):
        self.assertEqual(fim.guide_problems("ok\n"), [])
        self.assertTrue(fim.guide_problems("x" * (fim.GUIDE_MAX_BYTES + 1)))
        self.assertTrue(fim.guide_problems("a ''' b"))
        self.assertTrue(fim.guide_problems("  \n"))
        with self.assertRaises(RuntimeError):
            fim.inject_guide(TOML, "a ''' b")

    def test_the_committed_guide_fits(self):
        self.assertEqual(fim.guide_problems(fim.guide_text()), [])


class Box(unittest.TestCase):
    """C1 (more-local-inference task 01): every stage states `box`, and a phase stage's
    per-importer map is flattened into each importing workflow's manifest."""

    PHASE_BOX = {"backlog": True, "pr-review": True, "dependabot": False}

    def src(self, own=None, phase=None, workflows=None):
        if workflows is None:
            workflows = {"backlog": {"stages": own if own is not None else {
                "coder": {"live": True, "prompt": "p", "box": True},
                "review": {"live": True, "prompt": "p", "box": False},
                "plan": {"live": True, "prompt": "p", "box": "plan-arm"},
            }}}
        return {
            "version": 1,
            "min_binary": "0.9.0",
            "workflows": workflows,
            "phases": {"review-merge": phase if phase is not None else {
                "refute_fix": {"live": True, "prompt": "p", "box": {"backlog": True}},
            }},
        }

    def test_a_stage_carries_its_own_box_value(self):
        stages = fim.generated_for("backlog", self.src(), {})["stages"]
        self.assertIs(stages["coder"]["box"], True)
        self.assertIs(stages["review"]["box"], False)
        self.assertEqual(stages["plan"]["box"], "plan-arm")

    def test_a_phase_map_is_flattened_per_importing_workflow(self):
        three = {name: {"stages": {}} for name in ("backlog", "pr-review", "dependabot")}
        src = self.src(workflows=three, phase={
            "refute_fix": {"live": True, "prompt": "p", "box": dict(self.PHASE_BOX)}})
        self.assertEqual(fim.box_problems(src), [])
        for workflow, want in (("backlog", True), ("pr-review", True), ("dependabot", False)):
            with self.subTest(workflow=workflow):
                stages = fim.generated_for(workflow, src, {})["stages"]
                self.assertIs(stages["review_merge.refute_fix"]["box"], want)

    def test_the_real_source_manifest_is_clean(self):
        self.assertEqual(fim.box_problems(fim.load_source()), [])

    def test_only_a_background_package_carries_coder_box_stages(self):
        src = fim.load_source()
        schemas = fim.load_schemas()
        self.assertNotIn("coder_box_stages", fim.generated_for("backlog", src, schemas))
        self.assertNotIn("coder_box_stages", fim.generated_for("ralph", src, schemas))

    def test_coder_box_stages_lists_every_box_stage_of_every_coder_loop(self):
        # C1: box-guard reads this to judge another run's current stage, so the ids must be
        # the ones `GET /runs/{id}/stages` reports -- a phase stage under its import id --
        # and every `true` or `"plan-arm"` stage of the loop must be in it.
        generated = fim.generated_for("box-noop", fim.load_source(), fim.load_schemas())
        stages = generated["coder_box_stages"]
        self.assertEqual(stages, {
            "backlog": {
                "coder": True,
                "extra_decompose": True,
                "plan": "plan-arm",
                "resolve_merge": True,
                "review_merge.refute_fix": True,
                "review_merge.resolve_base": True,
                "rework_t1": True,
            },
            "pr-review": {
                "rebase_agent_t1": True,
                "review_merge.refute_fix": True,
                "review_merge.resolve_base": True,
            },
            "ralph": {
                "iterate_t1": True,
                "learn": True,
                "plan": "plan-arm",
                "resolve_base": True,
            },
        })

    def test_coder_box_stages_omits_a_hosted_stage(self):
        generated = fim.generated_for("box-noop", fim.load_source(), fim.load_schemas())
        for loop, stages in generated["coder_box_stages"].items():
            with self.subTest(loop=loop):
                self.assertNotIn(False, stages.values())
                for sid in ("review", "decompose", "rework_t2", "spec", "standards", "refute"):
                    self.assertNotIn(sid, stages)

    def test_a_missing_box_is_an_error(self):
        problems = fim.box_problems(self.src(own={"coder": {"live": True, "prompt": "p"}}))
        self.assertTrue(any("no `box`" in p for p in problems), problems)

    def test_each_bad_box_value_is_an_error(self):
        for value in ("maybe", "true", 1, None):
            with self.subTest(value=value):
                problems = fim.box_problems(self.src(own={"coder": {"live": True, "prompt": "p", "box": value}}))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("must be false, true or", problems[0])

    def test_a_map_on_a_workflow_stage_is_an_error(self):
        problems = fim.box_problems(self.src(own={"coder": {"live": True, "prompt": "p", "box": {"backlog": True}}}))
        self.assertTrue(any("only a phase stage" in p for p in problems), problems)

    def test_a_phase_map_must_name_exactly_its_importers(self):
        problems = fim.box_problems(self.src(phase={
            "refute_fix": {"live": True, "prompt": "p", "box": {"backlog": True, "ralph": True}}}))
        self.assertTrue(any("imported by" in p for p in problems), problems)
        problems = fim.box_problems(self.src(phase={
            "refute_fix": {"live": True, "prompt": "p",
                           "box": {"backlog": "plan-arm"}}}))
        self.assertTrue(any("only true or false" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
