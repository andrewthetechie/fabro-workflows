"""Tests for ops/fabro-box-claims.py, standard library only.

    python3.11 -m unittest discover -s ops/tests
"""
import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fbc", HERE.parent / "fabro-box-claims.py")
fbc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fbc)

MANIFEST = json.dumps({"version": 1, "min_binary": "0.9.0", "stages": {
    "coder": {"box": True, "inputs": []},
    "review": {"box": False, "inputs": []},
    "plan": {"box": "plan-arm", "inputs": []},
}})


def ev(seq, at, name, node=None):
    return {"seq": seq, "ts": f"2026-10-29T10:{at}Z", "event": name, "node_id": node}


def coder(run_id, events, *, box="coders-a", issue="2", manifest=MANIFEST):
    return {"id": run_id, "labels": {"lane": "coder", "box": box, "issue": issue},
            "status": {"kind": "running"}, "events": events, "manifest": manifest}


def background(run_id, events, *, box="coders-a", reason="cancelled"):
    return {"id": run_id, "labels": {"lane": "background", "box": box, "job": "box-noop"},
            "status": {"kind": "failed", "reason": reason}, "events": events}


class Claims(unittest.TestCase):
    def test_the_wait_is_edge_to_stage_started_and_only_box_stages_count(self):
        run = coder("c1", [
            ev(1, "00:00.000000", "edge.selected"), ev(2, "00:00.100000", "stage.started", "review"),
            ev(3, "05:00.000000", "edge.selected"), ev(4, "05:04.150000", "stage.started", "coder"),
        ])
        claims = fbc.coder_claims(run)
        self.assertEqual([(c.node, round(c.wait_s, 2)) for c in claims], [("coder", 4.15)])

    def test_a_stage_retry_is_not_a_second_claim_on_the_same_edge(self):
        run = coder("c1", [
            ev(1, "00:00.000000", "edge.selected"), ev(2, "00:02.400000", "stage.started", "coder"),
            ev(3, "59:59.000000", "stage.retrying"), ev(4, "59:59.900000", "stage.started", "coder"),
        ])
        claims = fbc.coder_claims(run)
        self.assertEqual([round(c.wait_s, 2) for c in claims], [2.4])

    def test_plan_arm_counts_only_on_the_odd_arm(self):
        events = [ev(1, "00:00.000000", "edge.selected"), ev(2, "00:02.000000", "stage.started", "plan")]
        self.assertEqual(fbc.coder_claims(coder("even", events, issue="4")), [])  # 4//2=2, even
        self.assertEqual(len(fbc.coder_claims(coder("odd", events, issue="3051"))), 1)

    def test_a_run_compiled_before_the_claim_has_no_box_stage(self):
        old = json.dumps({"version": 1, "min_binary": "0.5.1", "stages": {"coder": {"inputs": []}}})
        events = [ev(1, "00:00.000000", "edge.selected"), ev(2, "00:30.000000", "stage.started", "coder")]
        self.assertEqual(fbc.coder_claims(coder("old", events, manifest=old)), [])


class Analyse(unittest.TestCase):
    def test_a_claim_that_cancelled_a_background_run_is_attributed_and_timed(self):
        runs = [
            coder("c1", [ev(1, "05:00.000000", "edge.selected"), ev(2, "05:04.000000", "stage.started", "coder")]),
            background("b1", [ev(1, "04:00.000000", "agent.llm.started", "work"),
                              ev(2, "05:00.500000", "run.cancel.requested"),
                              ev(3, "05:01.500000", "run.failed")]),
        ]
        result = fbc.analyse(runs)
        self.assertEqual(result["claims_that_cancelled"], 1)
        self.assertEqual(result["cancel_wait_p95_s"], 4.0)
        self.assertEqual(result["overlaps"], [])
        self.assertEqual(result["idle_false"], [])

    def test_a_box_stage_that_starts_while_a_background_run_is_mid_request_is_an_overlap(self):
        runs = [
            coder("c1", [ev(1, "05:00.000000", "edge.selected"), ev(2, "05:02.000000", "stage.started", "coder")]),
            background("b1", [ev(1, "04:00.000000", "agent.llm.started", "work"),
                              ev(2, "05:30.000000", "run.failed")], reason="cancelled"),
        ]
        result = fbc.analyse(runs)
        self.assertEqual([c["run_id"] for c in result["overlaps"]], ["c1"])

    def test_a_background_run_on_the_other_box_is_neither_cancelled_nor_overlapping(self):
        runs = [
            coder("c1", [ev(1, "05:00.000000", "edge.selected"), ev(2, "05:02.000000", "stage.started", "coder")]),
            background("b1", [ev(1, "04:00.000000", "agent.llm.started", "work"),
                              ev(2, "05:01.000000", "run.cancel.requested"),
                              ev(3, "05:30.000000", "run.failed")], box="coders-b"),
        ]
        result = fbc.analyse(runs)
        self.assertEqual((result["claims_that_cancelled"], result["overlaps"]), (0, []))

    def test_a_sibling_slots_background_run_is_on_the_claims_box(self):
        # Two-slot boxes D2: the background run is labelled with the box's first pool and the
        # claim with its own, so only the map says they share a box.
        runs = [
            coder("c1", [ev(1, "05:00.000000", "edge.selected"), ev(2, "05:04.000000", "stage.started", "coder")],
                  box="coders-b"),
            background("b1", [ev(1, "04:00.000000", "agent.llm.started", "work"),
                              ev(2, "05:00.500000", "run.cancel.requested"),
                              ev(3, "05:30.000000", "run.failed")], box="coders-a"),
        ]
        one_box = fbc.parse_box_health(fbc.DEFAULT_BOX_HEALTH)
        result = fbc.analyse(runs, one_box)
        self.assertEqual(result["claims_that_cancelled"], 1)
        self.assertEqual([c["run_id"] for c in result["overlaps"]], ["c1"])
        self.assertEqual(fbc.analyse(runs)["claims_that_cancelled"], 0, "no map: a pool is a box")

    def test_box_of_names_a_box_by_its_first_pool(self):
        layout = fbc.parse_box_health(fbc.DEFAULT_BOX_HEALTH)
        self.assertEqual([fbc.box_of(layout, p) for p in ("coders-a", "coders-b", "coders-c", "coders-d")],
                         ["coders-a", "coders-a", "coders-c", "coders-c"])
        self.assertEqual(fbc.box_of(layout, "coders-z"), "coders-z")

    def test_a_cancel_before_the_first_request_is_the_guard_or_an_eviction(self):
        runs = [background("b1", [ev(1, "04:00.000000", "run.cancel.requested"),
                                  ev(2, "04:01.000000", "run.failed")])]
        self.assertEqual(fbc.analyse(runs)["cancelled_before_first_request"], ["b1"])

    def test_a_wait_at_the_deadline_is_idle_false(self):
        runs = [coder("c1", [ev(1, "05:00.000000", "edge.selected"), ev(2, "05:44.500000", "stage.started", "coder")])]
        self.assertEqual(len(fbc.analyse(runs)["idle_false"]), 1)


class ClaimLines(unittest.TestCase):
    """`parse_claim_line` reads the hook's own line (C3), old and new formats alike."""

    OLD = ("box-claim: backlog/coder box=coders-a cancelled=1 waited=4s idle=true run=env")
    NEW = ("box-claim: ralph/iterate_t1 box=coders-a cancelled=0 waited=0s idle=skip"
           " run=hook-context siblings=2")

    def test_a_line_from_before_the_change_still_parses(self):
        row = fbc.parse_claim_line(self.OLD)
        self.assertEqual((row["workflow"], row["node"], row["box"]), ("backlog", "coder", "coders-a"))
        self.assertEqual((row["cancelled"], row["waited_s"], row["idle"]), (1, 4, "true"))
        self.assertEqual(row["run_source"], "env")
        self.assertIsNone(row["siblings"], "absent means the build that had no sibling set")

    def test_the_new_fields_parse(self):
        row = fbc.parse_claim_line(self.NEW)
        self.assertEqual(row["idle"], "skip")
        self.assertEqual(row["siblings"], 2)
        self.assertEqual(row["cancelled"], 0)

    def test_only_a_claim_line_parses(self):
        self.assertIsNone(fbc.parse_claim_line("box-guard: box-noop/work box=coders-a clear=true"))
        self.assertIsNone(fbc.parse_claim_line("box-claim: coder box=coders-a cancelled=0 idle=true"))

    def test_skip_is_its_own_count_and_never_a_failure(self):
        report = fbc.summarise_claim_lines([self.OLD, self.NEW, "", "  \n"])
        self.assertEqual((report["lines"], report["unparsed"]), (2, 0))
        self.assertEqual(report["by_idle"], {"true": 1, "skip": 1})
        self.assertEqual((report["skips"], report["idle_false"]), (1, 0))
        self.assertEqual(report["two_slot_claims"], 1)

    def test_an_idle_false_line_is_the_deadline(self):
        report = fbc.summarise_claim_lines([self.OLD.replace("waited=4s idle=true",
                                                            "waited=45s idle=false")])
        self.assertEqual(report["idle_false"], 1)


class Verdicts(unittest.TestCase):
    """The event-derived `idle=` (D4): only the deadline fails, and a skip is neither."""

    def test_the_three_verdicts_by_wait(self):
        self.assertEqual(fbc.idle_verdict(0.2), "skip")
        self.assertEqual(fbc.idle_verdict(2.4), "true")
        self.assertEqual(fbc.idle_verdict(44.0), "false")

    def test_a_skipped_claim_is_not_an_idle_false(self):
        runs = [coder("c1", [ev(1, "05:00.000000", "edge.selected"),
                             ev(2, "05:00.200000", "stage.started", "coder")])]
        result = fbc.analyse(runs)
        self.assertEqual(result["claims_by_idle"], {"skip": 1})
        self.assertEqual(result["idle_false"], [])
        self.assertEqual(result["cancel_wait_p50_s"], None,
                         "a claim that never read the box has no wait to measure")

    def test_a_cancel_that_skipped_the_wait_is_counted_apart(self):
        runs = [
            coder("c1", [ev(1, "05:00.000000", "edge.selected"),
                         ev(2, "05:00.300000", "stage.started", "coder")]),
            background("b1", [ev(1, "04:00.000000", "agent.llm.started", "work"),
                              ev(2, "05:00.100000", "run.cancel.requested"),
                              ev(3, "05:01.000000", "run.failed")]),
        ]
        result = fbc.analyse(runs)
        self.assertEqual(result["claims_that_cancelled"], 1)
        self.assertEqual(result["claims_that_cancelled_without_wait"], 1)


if __name__ == "__main__":
    unittest.main()
