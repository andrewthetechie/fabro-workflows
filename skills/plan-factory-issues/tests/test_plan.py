"""The plan checks: the Child-issue contract (one predecessor, same parent, listed first), lanes,
waivers, and L11's overlap between drafts that can run at once."""

from __future__ import annotations

from conftest import corpus, draft_text, make_plan

from factory_issues import plan

PARENT = "# feat(invoices): invoice export\n\nExport invoices in two formats.\n"


def findings(tmp_path, issues, drafts, routes):
    return plan.check(plan.load(make_plan(tmp_path, issues, drafts)), routes)


def has(found, rule, severity="error", text=""):
    return any(f.rule == rule and f.severity == severity and not f.waived and text in f.message for f in found)


def good(title="feat(x): one slice", files=("src/a.py",)):
    listing = "".join(f"- `{f}`\n" for f in files)
    return draft_text(["`GET /a` returns 200."], title=title, extra=f"\n## Expected files\n\n{listing}")


def test_a_clean_chain(tmp_path, routes):
    found = findings(tmp_path, [
        {"key": "p", "role": "parent"},
        {"key": "a", "lane": "agent", "parent": "p"},
        {"key": "b", "lane": "agent", "parent": "p", "after": "a"},
        {"key": "c", "lane": "fast-path"},
    ], {"p": PARENT, "a": good(files=["src/a.py"]), "b": good(files=["src/a.py"]), "c": good(files=["src/c.py"])}, routes)
    assert plan.errors(found) == []
    assert not has(found, "L11", "warning")  # a and b share a file, but b waits on a


def test_two_predecessors_are_refused(tmp_path, routes):
    found = findings(tmp_path, [
        {"key": "p", "role": "parent"},
        {"key": "a", "lane": "agent", "parent": "p"},
        {"key": "b", "lane": "agent", "parent": "p"},
        {"key": "c", "lane": "agent", "parent": "p", "after": ["a", "b"]},
    ], {k: good() if k != "p" else PARENT for k in "pabc"}, routes)
    assert has(found, "L8", text="one predecessor")


def test_after_needs_a_parent_and_an_earlier_sibling(tmp_path, routes):
    found = findings(tmp_path, [
        {"key": "a", "lane": "agent"},
        {"key": "b", "lane": "agent", "after": "a"},
    ], {"a": good(), "b": good()}, routes)
    assert has(found, "L8", text="needs a `parent`")

    found = findings(tmp_path / "2", [
        {"key": "p", "role": "parent"},
        {"key": "a", "lane": "agent", "parent": "p", "after": "b"},
        {"key": "b", "lane": "agent", "parent": "p"},
    ], {"p": PARENT, "a": good(), "b": good()}, routes)
    assert has(found, "L8", text="listed before")

    found = findings(tmp_path / "3", [
        {"key": "p", "role": "parent"}, {"key": "q", "role": "parent"},
        {"key": "a", "lane": "agent", "parent": "p"},
        {"key": "x", "lane": "agent", "parent": "q"},
        {"key": "b", "lane": "agent", "parent": "q", "after": "a"},
    ], {"p": PARENT, "q": PARENT, "a": good(), "x": good(), "b": good()}, routes)
    assert has(found, "L8", text="same parent")


def test_a_child_is_never_needs_triage(tmp_path, routes):
    found = findings(tmp_path, [
        {"key": "p", "role": "parent"},
        {"key": "a", "lane": "agent", "parent": "p"},
        {"key": "b", "lane": "needs-triage", "parent": "p", "after": "a"},
    ], {"p": PARENT, "a": good(), "b": good()}, routes)
    assert has(found, "L8", text="needs-triage")


def test_parent_shape(tmp_path, routes):
    found = findings(tmp_path, [{"key": "p", "role": "parent", "lane": "agent"}, {"key": "a", "lane": "agent", "parent": "p"}],
                     {"p": PARENT, "a": good()}, routes)
    assert has(found, "L8", text="takes no `lane`")
    assert has(found, "L8", "warning", text="one child")
    found = findings(tmp_path / "2", [{"key": "p", "role": "parent"}], {"p": PARENT}, routes)
    assert has(found, "L8", text="no children")


def test_l11_overlap_outside_a_chain(tmp_path, routes):
    found = findings(tmp_path, [{"key": "a", "lane": "agent"}, {"key": "b", "lane": "agent"}],
                     {"a": good(files=["src/shared.py"]), "b": good(files=["src/shared.py", "src/b.py"])}, routes)
    assert has(found, "L11", "warning", text="src/shared.py")


def test_waivers(tmp_path, routes):
    many = [f"src/f{i}.py" for i in range(8)]
    found = findings(tmp_path, [{"key": "a", "lane": "agent", "waive": {"L9": "five are fixtures"}}],
                     {"a": good(files=many)}, routes)
    assert not has(found, "L9", "warning")
    assert any(f.rule == "L9" and f.waived == "five are fixtures" for f in found)

    found = findings(tmp_path / "2", [{"key": "a", "lane": "agent", "waive": {"L7": "no"}}],
                     {"a": good(title="Add a thing")}, routes)
    assert has(found, "PLAN", text="L7 cannot be waived")
    assert has(found, "L7")


def test_queue_labels_come_from_the_lane(tmp_path, routes):
    found = findings(tmp_path, [{"key": "a", "lane": "agent", "labels": ["agent", "architecture"]}], {"a": good()}, routes)
    assert has(found, "PLAN", text="from the lane")


def test_missing_draft_and_bad_lane(tmp_path, routes):
    path = make_plan(tmp_path, [{"key": "a", "lane": "soon"}], {})
    found = plan.check(plan.load(path), routes)
    assert has(found, "PLAN", text="no draft")
    assert has(found, "PLAN", text="lane must be")


def test_corpus_good_issue_in_a_plan(tmp_path, routes):
    found = findings(tmp_path, [{"key": "a", "lane": "agent"}], {"a": corpus("good-feature.md")}, routes)
    assert plan.errors(found) == []
