"""Tests for ops/fabro-io-manifest.py: the agent guide (docs/coder-tweaks C1) and the
Stage manifest's `box` field (more-local-inference C1).

    python3.11 -m unittest discover -s ops/tests
"""
import importlib.util
import copy
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

    def test_the_packages_that_judge_another_run_carry_coder_box_stages(self):
        # The guard judges coder loops from a background package; since two-slot boxes D4 the
        # claim judges a sibling slot's loop from a coder loop, which may be another workflow.
        src = fim.load_source()
        schemas = fim.load_schemas()
        expected = fim.generated_for("box-noop", src, schemas)["coder_box_stages"]
        for loop in fim.CODER_LOOP_WORKFLOWS:
            with self.subTest(loop=loop):
                self.assertEqual(fim.generated_for(loop, src, schemas)["coder_box_stages"], expected)
        for other in ("issue-triage", "arch-review", "dependabot"):
            with self.subTest(other=other):
                self.assertNotIn("coder_box_stages", fim.generated_for(other, src, schemas))

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
                "plan": True,
                # Part B: the whole-branch pre-review runs on the run's own box.
                "pre_review": True,
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
                "local_spec": True,
                "local_standards": True,
                "plan": True,
                "plan_review": True,
                "resolve_base": True,
            },
            # The trial's second arm (ADR 0034): the same box stages, with `resolve_merge`
            # moved onto the box, the fourth rung gone, and the per-Task planner (task 07).
            "ralph-v2": {
                "iterate_t1": True,
                "learn": True,
                "local_spec": True,
                "local_standards": True,
                "plan": True,
                "plan_review": True,
                "resolve_base": True,
                "resolve_merge": True,
                "task_plan": True,
            },
            # A leased run holds its box (ADR 0033): its stages block a background fill.
            "issue-sharpen": {
                "comprehend": True,
                "draft": True,
                "preserve": True,
                "recheck": True,
                "verify": True,
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


class Rungs(unittest.TestCase):
    """rule 6 (ADR 0034 E16): a package's Iteration rungs are one stage under several ids.

    `ralph` has four rungs and `ralph-v2` three, and each package's check names that
    package: the rule used to be ralph's alone.
    """

    def src(self) -> dict:
        return copy.deepcopy(fim.load_source())

    def test_the_real_source_is_clean(self):
        self.assertEqual(fim.rung_problems(fim.load_source()), [])

    def test_a_package_missing_a_rung_is_named(self):
        for wf, expected in (
            ("ralph", "rule6: ralph is missing one of its iterate_t1, iterate_t2, iterate_t3, iterate_t4"),
            ("ralph-v2", "rule6: ralph-v2 is missing one of its iterate_t1, iterate_t2, iterate_t3"),
        ):
            with self.subTest(wf=wf):
                src = self.src()
                del src["workflows"][wf]["stages"]["iterate_t3"]
                self.assertEqual(fim.rung_problems(src), [expected])

    def test_a_rung_that_differs_from_its_siblings_is_named(self):
        src = self.src()
        src["workflows"]["ralph-v2"]["stages"]["iterate_t2"]["prompt"] = "p"
        self.assertEqual(fim.rung_problems(src), [
            "rule6: ralph-v2's iterate_t1..iterate_t3 specs differ; they are one stage under "
            "several node ids"])

    def test_the_box_field_is_the_one_the_rungs_may_differ_in(self):
        src = self.src()
        src["workflows"]["ralph-v2"]["stages"]["iterate_t1"]["box"] = "plan-arm"
        self.assertEqual(fim.rung_problems(src), [])


class ManifestSize(unittest.TestCase):
    """rule 7: a generated manifest is one environment string, and Linux caps it at 131,072.

    Past that every stage start of a run in that package fails, so the size is checked
    offline, with a margin (120,000) for the escaping a growing manifest adds.
    """

    def test_the_real_manifests_fit(self):
        self.assertEqual(fim.body_problems(fim.load_source(), fim.load_schemas()), [])

    def test_a_package_over_the_limit_is_named(self):
        src = copy.deepcopy(fim.load_source())
        stage = src["workflows"]["ralph-v2"]["stages"]["plan"]
        stage["inputs"][0]["about"] = "x" * (fim.MANIFEST_MAX_BYTES + 1)
        problems = fim.body_problems(src, fim.load_schemas())
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("rule7: ralph-v2 generated manifest is", problems[0])
        self.assertIn(f"over the {fim.MANIFEST_MAX_BYTES:,}-byte", problems[0])


if __name__ == "__main__":
    unittest.main()
