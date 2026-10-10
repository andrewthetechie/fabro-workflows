"""Tests for ops/fabro-export-runs.sh against a stub ssh (fixtures/export-runs/ssh-stub.sh).

    python3.11 -m unittest discover -s ops/tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fabro-export-runs.sh"
FIXTURES = HERE / "fixtures/export-runs"
WINDOW = ["--since", "2026-10-08T05:34Z", "--until", "2026-10-10T00:00Z"]
HEADER = ["id", "created", "workflow", "repo", "status", "issue", "workflow_sha"]


class Export(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fx = self.tmp / "fx"
        shutil.copytree(FIXTURES, self.fx)
        self.calls = self.tmp / "calls.log"
        self.out = self.tmp / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def export(self, *args, outdir=None):
        env = dict(os.environ, SSH=str(self.fx / "ssh-stub.sh"), EXPORT_STUB_LOG=str(self.calls))
        cmd = ["bash", str(SCRIPT), *args, str(outdir or self.out)]
        return subprocess.run(cmd, env=env, capture_output=True, text=True)

    def rows(self, proc):
        lines = [line.split("\t") for line in proc.stdout.splitlines()]
        self.assertEqual(lines[0], HEADER)
        return lines[1:]

    def logged(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_default_window_lists_terminal_backlog_runs(self):
        p = self.export(*WINDOW)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.rows(p), [
            ["RUN5", "2026-10-09T10:00:00.000Z", "backlog", "andrewthetechie/lawncare-saas", "succeeded", "42", "-"],
        ])
        self.assertEqual(sorted(f.name for f in self.out.iterdir()), ["RUN5.jsonl", "index.tsv"])
        self.assertEqual(p.stdout, (self.out / "index.tsv").read_text())

    def test_all_workflows_and_statuses_sort_oldest_first(self):
        p = self.export(*WINDOW, "--workflow", "all", "--status", "all")
        self.assertEqual(p.returncode, 0, p.stderr)
        sha = "a" * 40
        self.assertEqual([r[0] for r in self.rows(p)], ["RUN3", "RUN4", "RUN5"])
        self.assertEqual(self.rows(p)[0],
                         ["RUN3", "2026-10-08T06:00:00.000Z", "backlog", "andrewthetechie/writers-app", "running", "-", sha])
        self.assertEqual(self.rows(p)[1],
                         ["RUN4", "2026-10-09T02:00:00.000Z", "pr-review", "andrewthetechie/womens-fantasy-sports",
                          "succeeded", "7", sha])

    def test_since_is_inclusive_and_until_is_exclusive(self):
        p = self.export("--since", "2026-10-08T06:00Z", "--until", "2026-10-09T02:00:00Z",
                        "--workflow", "all", "--status", "all")
        self.assertEqual([r[0] for r in self.rows(p)], ["RUN3"])

    def test_a_minute_bound_covers_its_whole_minute(self):
        # RUN4 is created at 02:00:00, so --until 02:00Z (the minute) keeps it, and 01:59Z does not.
        p = self.export("--since", "2026-10-08T06:00Z", "--until", "2026-10-09T02:00Z",
                        "--workflow", "all", "--status", "all")
        self.assertEqual([r[0] for r in self.rows(p)], ["RUN3", "RUN4"])
        p = self.export("--since", "2026-10-08T06:00Z", "--until", "2026-10-09T01:59Z",
                        "--workflow", "all", "--status", "all")
        self.assertEqual([r[0] for r in self.rows(p)], ["RUN3"])

    def test_paging_stops_at_since(self):
        self.export(*WINDOW)
        listed = [line for line in self.logged() if "page[offset]=" in line]
        self.assertTrue(any("page[offset]=0" in line for line in listed))
        self.assertTrue(any("page[offset]=100" in line for line in listed))
        # page 100 has has_more true, but its oldest run is before --since: no page 200.
        self.assertFalse(any("page[offset]=200" in line for line in listed))

    def test_listing_reads_the_token_on_the_host(self):
        self.export(*WINDOW)
        listed = [line for line in self.logged() if "page[offset]=" in line]
        self.assertTrue(all("docker exec fabro-fabro-1 cat /storage/server.dev-token" in line for line in listed))

    def test_rerun_resumes_and_prints_the_same_index(self):
        first = self.export(*WINDOW)
        self.calls.unlink()
        second = self.export(*WINDOW)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        self.assertFalse(any("fabro events" in line for line in self.logged()))

    def test_a_failed_run_leaves_err_and_the_others_are_exported(self):
        page = self.fx / "page-0.json"
        text = page.read_text().replace(
            '{\n      "id": "RUN5"',
            '{\n      "id": "BADRUN", "workflow": {"slug": "backlog"}, "repository": {"name": "x/y"}, '
            '"labels": {"issue": "1"}, "lifecycle": {"status": {"kind": "succeeded"}}, '
            '"timestamps": {"created_at": "2026-10-09T11:00:00.000Z", "completed_at": "2026-10-09T11:05:00.000Z"}, '
            '"automation": null},\n    {\n      "id": "RUN5"', 1)
        page.write_text(text)
        p = self.export(*WINDOW)
        self.assertEqual(p.returncode, 1)
        self.assertIn("no run BADRUN", (self.out / "BADRUN.err").read_text())
        self.assertFalse((self.out / "BADRUN.jsonl").exists())
        self.assertTrue((self.out / "RUN5.jsonl").stat().st_size > 0)
        self.assertEqual([r[0] for r in self.rows(p)], ["RUN5", "BADRUN"])

    def test_refuses_an_outdir_inside_a_work_tree(self):
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        out = repo / "nested" / "out"
        p = self.export(*WINDOW, outdir=out)
        self.assertEqual(p.returncode, 2)
        self.assertIn("inside a git work tree", p.stderr)
        self.assertFalse((repo / "nested").exists())
        self.assertEqual(self.logged(), [])

    def test_bad_arguments_exit_two(self):
        self.assertEqual(self.export("--until", "2026-10-10T00:00Z").returncode, 2)  # no --since
        self.assertEqual(self.export(*WINDOW, "--status", "running").returncode, 2)


if __name__ == "__main__":
    unittest.main()
