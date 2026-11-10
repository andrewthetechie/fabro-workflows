# The shared issue corpus

`issues/` holds issue drafts, one good issue per style and one per bad-criterion class, with the
bad criteria taken from real held PRs and anonymised. `issues/EXPECTED.toml` names the lint rule
ids each must raise, against `repo/`, a small repository whose `.fabro/ci.sh` reaches its tools
directly, through a script, a Makefile prerequisite and `package.json` scripts.

Two tools read it: this skill's lint (`tests/test_lint.py`) and the issue sharpening loop (#68),
whose gates and calibration must reach the same verdict on each file. A new bad class gets a
fixture here first, then a rule in both.
