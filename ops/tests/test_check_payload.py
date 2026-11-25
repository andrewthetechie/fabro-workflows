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
import json
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

    def test_a_sibling_package_whose_name_starts_with_this_one_does_not_travel(self):
        # The filter is `workflows/<package>/`, slash included. `ralph` and `ralph-v2` share a
        # prefix, so a filter without it would count the other arm's bytes as this one's
        # (ralph-v2 01; task 05 adds the second package to the real tree).
        write(self.root, "workflows/ralph/workflow.fabro", "digraph R {}")
        write(self.root, "workflows/ralph-v2/workflow.fabro", "digraph R2 {}")
        write(self.root, "workflows/ralph-v2/prompts/coder.md.j2", "v2")

        files = cpp.package_files(self.root / ".fabro", "ralph")

        self.assertEqual(sorted(files), ["workflows/ralph/workflow.fabro"])

    def test_the_byte_count_is_the_server_s_measure(self):
        # Compact separators and `ensure_ascii` on: the larger of the encodings the server may
        # canonicalise to. A non-ASCII character is three bytes raw and six escaped, and the
        # default separators pad every key. Either drift reports a number no dispatch sends.
        write(self.root, "workflows/backlog/prompts/dash.md.j2", "—")
        files = cpp.package_files(self.root / ".fabro", "backlog")
        body = {
            "entrypoint": "workflows/backlog/workflow.fabro",
            "files": files,
            "workflow_dependencies": {},
        }

        size = cpp.payload_bytes("backlog", files)

        self.assertEqual(
            size,
            len(json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")),
        )
        self.assertNotEqual(
            size,
            len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")),
        )
        self.assertNotEqual(size, len(json.dumps(body, ensure_ascii=True).encode("utf-8")))

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

    def test_no_package_to_check_is_a_failure_not_a_pass(self):
        # The empty path: a checkout with no `workflows/*/workflow.fabro` has nothing to
        # measure, and `ok: 0 packages` over nothing would be a green gate that checks
        # nothing. The same answer for a root with no `.fabro/` at all.
        empty = self.root / "empty"
        (empty / ".fabro" / "workflows").mkdir(parents=True)

        lines, failures = cpp.check(empty)

        self.assertEqual(lines, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("no workflows/*/workflow.fabro to check", failures[0])
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cpp.main(["--root", str(empty)])
        self.assertEqual(rc, 1)
        self.assertNotIn("ok:", out.getvalue())
        self.assertEqual(len(cpp.check(self.root / "nowhere")[1]), 1)


if __name__ == "__main__":
    unittest.main()
