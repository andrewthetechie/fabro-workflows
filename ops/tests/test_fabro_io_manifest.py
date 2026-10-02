"""Tests for the agent-guide half of ops/fabro-io-manifest.py (docs/coder-tweaks C1).

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


if __name__ == "__main__":
    unittest.main()
