"""Tests for ops/fabro-change-compare.py (docs/change-compare/).

    python3.11 -m unittest discover -s ops/tests

Every case builds a small scheduler database with only the columns the tool reads.
"""
import importlib.util
import io
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fcc", HERE.parent / "fabro-change-compare.py")
fcc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fcc)

SCHEMA = """
CREATE TABLE run_history (run_id TEXT PRIMARY KEY, coder_pool TEXT, repo TEXT, issue_number INTEGER,
  dispatched_at TEXT, finished_at TEXT, kind TEXT, reason TEXT, merged INTEGER);
CREATE TABLE run_summary (run_id TEXT PRIMARY KEY, terminal_node TEXT, dispositions TEXT,
  first_edit_ms TEXT, refute TEXT);
CREATE TABLE run_stage_stats (run_id TEXT, node_id TEXT, handler TEXT, visits INTEGER, wall_ms INTEGER,
  box_ms INTEGER, timeouts INTEGER, failures TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER);
"""


class Db:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / "scheduler.db")
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        self.n = 0

    def close(self):
        self.conn.close()
        self.dir.cleanup()

    def run(self, at, pool="coders-a", repo="o/wfs", kind="succeeded", reason="completed",
            terminal="review_merge.done", merged=0, rework=0, refute="pass", tps=10.0, minutes=60):
        self.n += 1
        rid = f"R{self.n:03d}"
        start = f"2026-10-{at}:00+00:00"
        end_min = int(start[14:16]) + minutes
        end = f"{start[:11]}{int(start[11:13]) + end_min // 60:02d}:{end_min % 60:02d}{start[16:]}"
        self.conn.execute("insert into run_history values (?,?,?,?,?,?,?,?,?)",
                          (rid, pool, repo, 1, start, end, kind, reason, merged))
        if reason == "launch_failed":
            return rid
        self.conn.execute("insert into run_summary values (?,?,?,?,?)",
                          (rid, terminal, "{}", "[60000]", json.dumps([{"verdict": refute}] if refute else [])))
        self.conn.execute("insert into run_stage_stats values (?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, "coder", "agent", 1, 100_000, 100_000, 0, "{}", 0, int(tps * 100), 0))
        if rework:
            self.conn.execute("insert into run_stage_stats values (?,?,?,?,?,?,?,?,?,?,?)",
                              (rid, "rework_t1", "agent", rework, 0, 0, 0, "{}", 0, 0, 0))
        return rid

    def main(self, *args):
        self.conn.commit()
        out = io.StringIO()
        with redirect_stdout(out):
            fcc.main(["--db", self.path, "--since", "2026-10-01", "--json", *args])
        return json.loads(out.getvalue())


class RankP(unittest.TestCase):
    def test_all_after_above_every_before_is_one_arrangement(self):
        p = fcc.rank_p([1, 2, 3, 4, 5], [10, 11], higher_is_better=True)
        self.assertAlmostEqual(p, 1 / math.comb(7, 2))

    def test_lower_is_better_mirrors(self):
        p = fcc.rank_p([5, 6, 7], [1], higher_is_better=False)
        self.assertAlmostEqual(p, 1 / 4)

    def test_all_ties_is_no_evidence(self):
        self.assertEqual(fcc.rank_p([0, 0, 0], [0, 0], higher_is_better=False), 1.0)

    def test_empty_side_or_too_many_arrangements_refuses(self):
        self.assertIsNone(fcc.rank_p([], [1], True))
        self.assertIsNone(fcc.rank_p(list(range(200)), list(range(10)), True))


class ParseChange(unittest.TestCase):
    def test_pool_and_zulu(self):
        c = fcc.parse_change("m=2026-10-18T04:53:23Z@coders-a")
        self.assertEqual((c["label"], c["pool"], c["at"].utcoffset().total_seconds()), ("m", "coders-a", 0))

    def test_naive_time_refused(self):
        with self.assertRaises(Exception):
            fcc.parse_change("m=2026-10-18T04:53:23")


class Compare(unittest.TestCase):
    def db(self):
        db = Db()
        self.addCleanup(db.close)
        return db

    def test_strata_endings_and_confounding(self):
        db = self.db()
        for h in ("10T01", "10T03", "10T05"):
            db.run(h, tps=9)
        db.run("12T01", tps=30, rework=2, refute="fail")            # after the model swap
        db.run("12T05", tps=20, kind="failed", reason="terminated")  # infra: speed only
        db.run("12T06", reason="launch_failed", kind="failed")      # never ran: dropped
        db.run("10T01", pool="coders-d", repo="o/lawn", tps=40)
        db.run("12T07", pool="coders-d", repo="o/lawn", tps=40, merged=1)
        out = db.main("--change", "model=2026-10-11T00:00Z@coders-a",
                      "--change", "prompts=2026-10-12T04:00Z")

        model = {s["stratum"]: s for s in out["model"]}
        whole = model["coders-a (all repos)"]
        self.assertEqual((whole["before"]["runs"], whole["after"]["runs"]), (3, 2))
        self.assertEqual(whole["after"]["fair"], 1)                  # terminated left out
        self.assertEqual(whole["after"]["median_tps"], 25.0)         # but its speed counts
        self.assertAlmostEqual(whole["p_tps_higher"], 1 / math.comb(5, 2))
        self.assertEqual(whole["confounded_with"], ["prompts"])
        self.assertNotIn("R006", [r["run_id"] for r in out["runs"]])

        prompts = {s["stratum"]: s for s in out["prompts"]}
        # The coders-a stratum has a summarized run after 12T04 (the terminated one),
        # and its before side straddles the model swap.
        self.assertEqual(prompts["wfs / coders-a"]["confounded_with"], ["model"])
        self.assertEqual(prompts["lawn / coders-d"]["confounded_with"], [])
        self.assertEqual(prompts["lawn / coders-d"]["after"]["merged"], 1)

    def test_exclude_and_untouched_stratum(self):
        db = self.db()
        db.run("10T01")
        rid = db.run("12T01")
        db.run("10T02", pool="coders-b", repo="o/other")
        out = db.main("--change", "x=2026-10-11T00:00Z", "--exclude", rid)
        self.assertEqual(out["x"], [])  # the only after-run excluded, coders-b never touched


if __name__ == "__main__":
    unittest.main()
