"""Tests for ops/check-payload.py (ralph-v2 task 01).

    python3.11 -m unittest discover -s ops/tests

A dispatch uploads one package plus `workflows/_shared/` (C9), and the server refuses a
version over 2 MiB for every workflow rather than for the one that grew. This checker is
the offline half of that limit: one package under the budget and one over it, a `_shared`
file that has to be counted, and the server's per-file limit.

Every fixture is a scratch tree passed to `check`, so nothing here reads this checkout.
"""
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cpp", HERE.parent / "check-payload.py")
cpp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cpp)


def write(root, relative, text):
    """One file in a scratch `.fabro` tree, parents made."""
    path = Path(root) / ".fabro" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class PayloadBudget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write(self.root, "workflows/backlog/workflow.fabro", "digraph B {}")
        write(self.root, "workflows/backlog/prompts/coder.md.j2", "code")

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_package_under_the_budget_passes(self):
        size = cpp.payload_bytes("backlog", cpp.package_files(self.root / ".fabro", "backlog"))
        lines, failures = cpp.check(self.root)
        self.assertEqual(failures, [])
        self.assertEqual(len(lines), 1, "one line per package")
        self.assertEqual(lines[0].split(), ["backlog", f"{size:,}", "bytes", "(2", "file(s))"])

    def test_the_payload_is_the_package_and_shared_and_nothing_else(self):
        write(self.root, "workflows/_shared/review-merge/review-merge.fabro", "digraph S {}")
        write(self.root, "workflows/other/workflow.fabro", "digraph O {}")
        write(self.root, "workflows/_io/manifest.json", "{}")

        files = cpp.package_files(self.root / ".fabro", "backlog")

        self.assertEqual(sorted(files), [
            "workflows/_shared/review-merge/review-merge.fabro",
            "workflows/backlog/prompts/coder.md.j2",
            "workflows/backlog/workflow.fabro",
        ])

    def test_shared_files_are_counted_in_the_byte_count(self):
        before = cpp.payload_bytes("backlog", cpp.package_files(self.root / ".fabro", "backlog"))
        write(self.root, "workflows/_shared/triage/triage.fabro", "digraph T {}")
        after = cpp.payload_bytes("backlog", cpp.package_files(self.root / ".fabro", "backlog"))
        self.assertGreater(after, before)
        _, failures = cpp.check(self.root, budget=before)
        self.assertEqual(len(failures), 1, "the same package with _shared added is now over")
        self.assertIn("backlog", failures[0])

    def test_a_package_over_the_budget_is_a_failure(self):
        # 4 x 400,000 bytes is under the 512 KiB per-file limit and over 1,500,000 in total.
        for n in range(4):
            write(self.root, f"workflows/backlog/prompts/big{n}.md.j2", "a" * 400_000)

        lines, failures = cpp.check(self.root)

        self.assertEqual(len(failures), 1)
        self.assertIn("over the 1,500,000-byte payload budget", failures[0])

    def test_a_file_over_the_per_file_limit_is_a_failure(self):
        write(self.root, "workflows/backlog/prompts/huge.md.j2", "a" * (cpp.MAX_FILE_BYTES + 1))

        _, failures = cpp.check(self.root)

        self.assertEqual(len(failures), 1)
        self.assertIn("workflows/backlog/prompts/huge.md.j2", failures[0])
        self.assertIn("per-file limit", failures[0])

    def test_another_packaged_oversized_tree_does_not_fail_this_package(self):
        # The gate is per package: the tree can be over the server's cap and every package
        # still fit, which is exactly the state that made the payload a blocker (A1).
        write(self.root, "workflows/other/workflow.fabro", "digraph O {}")
        write(self.root, "workflows/other/prompts/big.md.j2", "a" * 400_000)
        write(self.root, "workflows/_io/schemas/big.json", "{}")

        lines, failures = cpp.check(self.root)

        self.assertEqual(failures, [])
        self.assertEqual(len(lines), 2, "backlog and other, one line each")

    def test_the_cli_prints_every_package_and_exits_zero(self):
        write(self.root, "workflows/ralph/workflow.fabro", "digraph R {}")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cpp.main(["--root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertIn("backlog", out.getvalue())
        self.assertIn("ralph", out.getvalue())
        self.assertIn("ok: 2 packages", out.getvalue())

    def test_the_cli_exits_nonzero_on_a_failure(self):
        write(self.root, "workflows/backlog/prompts/huge.md.j2", "a" * (cpp.MAX_FILE_BYTES + 1))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cpp.main(["--root", str(self.root)])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out.getvalue())


if __name__ == "__main__":
    unittest.main()
