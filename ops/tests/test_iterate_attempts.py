"""Tests for ops/iterate-attempts.py, standard library only.

    python3.11 -m unittest discover -s ops/tests

The fixture under fixtures/iterate-attempts/ is one run: `iterate_t1@1` whose first attempt
times out (stage.started, stage.failed with the `timeout` actor, stage.retrying) and whose
second completes, a second visit that ends on its check, and a third that never ends. The
point of the attempt key is the first case: keyed by stage_id alone, the timeout disappears
under the retry's stage.completed, which is why `ops/fabro-agent-tools.py` reports one
attempt per visit and this script must not (#63).
"""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ia", HERE.parent / "iterate-attempts.py")
ia = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ia)
FIXTURE = HERE / "fixtures/iterate-attempts/run.jsonl"


def by_attempt():
    rows = ia.load(FIXTURE, "")
    return {(r["sid"], r["att"]): r for r in rows}


class Attempts(unittest.TestCase):
    def test_a_timed_out_attempt_is_not_the_retry(self):
        rows = by_attempt()
        self.assertEqual(sorted(rows), [("iterate_t1@1", 1), ("iterate_t1@1", 2), ("iterate_t1@2", 1)])
        first, second = rows[("iterate_t1@1", 1)], rows[("iterate_t1@1", 2)]
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["failure"], "timeout")
        self.assertEqual(round(first["dur"]), 60)
        self.assertEqual(first["edits"], 3)
        self.assertEqual(second["status"], "passed")
        self.assertEqual(second["edits"], 7)
        self.assertEqual(round(second["dur"]), 19)

    def test_an_attempt_that_never_ended_is_dropped(self):
        self.assertNotIn(("iterate_t1@3", 1), by_attempt())

    def test_edits_since_the_last_check(self):
        rows = by_attempt()
        # no check at all: every edit is after "the last check"
        self.assertEqual(rows[("iterate_t1@1", 1)]["edits_after_check"], 3)
        self.assertEqual(rows[("iterate_t1@1", 1)]["checks"], 0)
        # five edits, the check, two more edits; the longest check-free run is the first five
        self.assertEqual(rows[("iterate_t1@1", 2)]["edits_after_check"], 2)
        self.assertEqual(rows[("iterate_t1@1", 2)]["free_streak"], 5)
        # an attempt that ends on its check
        self.assertEqual(rows[("iterate_t1@2", 1)]["edits_after_check"], 0)
        self.assertEqual(rows[("iterate_t1@2", 1)]["checks"], 1)

    def test_the_task_shape_comes_from_the_inputs_tool(self):
        row = by_attempt()[("iterate_t1@1", 2)]
        self.assertEqual(row["task_files"], 2)
        self.assertEqual(row["task_acc"], 3)
        self.assertGreater(row["task_ctx"], 100)
        self.assertGreater(row["inputs_bytes"], 400)

    def test_the_repo_loses_its_dot_git(self):
        self.assertEqual({r["repo"] for r in ia.load(FIXTURE, "")}, {"writers-app"})

    def test_before_keeps_only_earlier_runs(self):
        self.assertEqual(len(ia.load(FIXTURE, "2026-11-02")), 3)
        self.assertEqual(ia.load(FIXTURE, "2026-11-01"), [])

    def test_the_report_names_the_share_and_the_streak_table(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(ia.main(["--before", "2026-11-02", str(FIXTURE)]), 0)
        text = out.getvalue()
        self.assertIn("=== writers-app: 3 attempts, 1 failed (33.3%)", text)
        self.assertIn("attempts that ran >=1 check: failed 0/1, passed 2/2", text)
        self.assertIn("failed share by the longest run of edits", text)

    def test_no_events_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.jsonl"
            empty.write_text("")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(ia.main([str(empty)]), 1)


class Parsing(unittest.TestCase):
    def test_a_task_json_is_read_to_its_matching_brace(self):
        task = {"id": "t1", "files": ["a"], "acceptance": ["x"], "context": "}" * 3}
        blob = "=== current_task.json: x ===\nnote\n" + json.dumps(task, indent=2) + "\n"
        self.assertEqual(ia.task_of(blob), task)

    def test_no_section_is_an_empty_task(self):
        self.assertEqual(ia.task_of("=== issue.json: x ===\n{}"), {})


if __name__ == "__main__":
    unittest.main()
