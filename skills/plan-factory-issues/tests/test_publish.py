"""The publisher, against a fake GitHub: the order of the calls, the markers, and that a re-run
after a crash at any write finishes the job without a duplicate or an early `agent`."""

from __future__ import annotations

import json
import re

import pytest
from conftest import draft_text, make_plan

import plan_issues
from factory_issues import plan, publish

LABELS = {"agent", "agent-held", "agent-split", "fast-path", "needs-triage", "priority", "architecture"}
PARENT = "# feat(invoices): invoice export\n\nExport invoices in two formats.\n"


class Crash(Exception):
    pass


class FakeGitHub:
    """Issues, sub-issue links and labels in memory, and a log of every write in order.
    `crash_before=k` raises before the k-th write happens; `crash_after=k` raises after it
    happened, as a lost response would."""

    def __init__(self, labels=LABELS):
        self.repo_labels = set(labels)
        self.issues: dict[int, dict] = {}
        self.subs: dict[int, list[int]] = {}
        self.log: list[tuple] = []
        self.next = 100
        self.writes = 0
        self.crash_before: int | None = None
        self.crash_after: int | None = None

    def _write(self, entry: tuple, apply) -> object:
        self.writes += 1
        if self.crash_before == self.writes:
            raise Crash(f"before write {self.writes}")
        result = apply()
        self.log.append(entry if not callable(entry) else entry(result))
        if self.crash_after == self.writes:
            raise Crash(f"after write {self.writes}")
        return result

    def labels(self):
        return set(self.repo_labels)

    def issues_since(self, since):
        return [(n, i["body"]) for n, i in self.issues.items()]

    def create_issue(self, title, body):
        def apply():
            n = self.next
            self.next += 1
            self.issues[n] = {"title": title, "body": body, "labels": [], "id": n * 1000}
            return n
        return self._write(lambda n: ("create", n), apply)

    def edit_body(self, number, body):
        self._write(("edit", number), lambda: self.issues[number].__setitem__("body", body))

    def issue_id(self, number):
        return self.issues[number]["id"]

    def sub_issues(self, parent):
        return list(self.subs.get(parent, []))

    def link(self, parent, child_id):
        child = child_id // 1000
        if child in self.subs.get(parent, []):
            raise publish.GitHubError("422: already a sub-issue")
        self._write(("link", parent, child), lambda: self.subs.setdefault(parent, []).append(child))

    def add_label(self, number, label):
        def apply():
            if label not in self.issues[number]["labels"]:
                self.issues[number]["labels"].append(label)
        self._write(("label", number, label), apply)


def chain_plan(tmp_path, n_children=2, extra=()):
    issues = [{"key": "p", "role": "parent"}]
    drafts = {"p": PARENT}
    prev = None
    for i in range(n_children):
        key = f"c{i + 1}"
        e = {"key": key, "lane": "agent", "parent": "p"}
        if prev:
            e["after"] = prev
        issues.append(e)
        drafts[key] = draft_text(["`GET /a` returns 200."], title=f"feat(invoices): slice {i + 1}")
        prev = key
    for e in extra:
        issues.append(e)
        drafts[e["key"]] = draft_text(["`GET /b` returns 200."], title=f"fix(x): {e['key']}")
    return plan.load(make_plan(tmp_path, issues, drafts))


def by_key(p: plan.Plan) -> dict[str, int]:
    return {e["key"]: e["number"] for e in p.issues}


def assert_published(gh: FakeGitHub, p: plan.Plan):
    """The end state, and the one ordering rule that matters: no `agent` before every hold."""
    assert len(gh.issues) == len(p.issues), "an issue was created twice or not at all"
    nums = by_key(p)
    for e in p.issues:
        issue = gh.issues[nums[e["key"]]]
        assert f"plan={p.data['plan']} key={e['key']}" in issue["body"]
        want = set(publish.labels_for(e, bool(e.get("after"))))
        assert set(issue["labels"]) == want, (e["key"], issue["labels"])
        if e.get("parent"):
            after = nums[e["after"]] if e.get("after") else 0
            assert issue["body"].splitlines()[0] == f"<!-- fabro:split-child parent={nums[e['parent']]} after={after} -->"
            assert nums[e["key"]] in gh.subs[nums[e["parent"]]]
        if e.get("role") == "parent":
            kids = [k for k in p.issues if k.get("parent") == e["key"]]
            assert sorted(gh.subs[nums[e["key"]]]) == sorted(nums[k["key"]] for k in kids)
            for k in kids:
                assert f"- [ ] #{nums[k['key']]} " in issue["body"]
    holds = {(nums[e["key"]], lbl) for e in p.issues for lbl in publish.labels_for(e, bool(e.get("after")))
             if lbl in ("agent-split", "agent-held")}
    links = {(nums[e["parent"]], nums[e["key"]]) for e in p.issues if e.get("parent")}
    applied, linked, created = set(), set(), set()
    for entry in gh.log:
        if entry[0] == "create":
            created.add(entry[1])
        elif entry[0] == "link":
            linked.add((entry[1], entry[2]))
        elif entry[0] == "label":
            applied.add((entry[1], entry[2]))
            if entry[2] in ("agent", "needs-triage"):
                assert created == set(nums.values()), "queued before every issue existed"
                assert holds <= applied, "queued before every hold was in place"
                assert links <= linked, "queued before every child was linked"


def test_single_issue(tmp_path):
    p = plan.load(make_plan(tmp_path, [{"key": "a", "lane": "agent"}], {"a": draft_text(["Returns 200."])}))
    gh = FakeGitHub()
    publish.publish(p, gh, write=True, say=lambda _: None)
    assert gh.log == [("create", 100), ("label", 100, "agent")]
    assert json.loads(p.path.read_text())["published"]


def test_two_child_chain_order(tmp_path):
    p = chain_plan(tmp_path, 2)
    gh = FakeGitHub()
    publish.publish(p, gh, write=True, say=lambda _: None)
    assert gh.log == [
        ("create", 100), ("create", 101), ("create", 102),
        ("link", 100, 101), ("link", 100, 102), ("edit", 100),
        ("label", 100, "agent-split"), ("label", 102, "agent-held"), ("label", 101, "agent"),
    ]
    assert gh.issues[102]["body"].startswith("<!-- fabro:split-child parent=100 after=101 -->\n")
    assert gh.issues[101]["body"].startswith("<!-- fabro:split-child parent=100 after=0 -->\n")
    assert_published(gh, p)


def test_three_child_chain_with_lanes(tmp_path):
    p = chain_plan(tmp_path, 3, extra=[
        {"key": "fast", "lane": "fast-path", "labels": ["architecture"]},
        {"key": "open", "lane": "needs-triage"},
    ])
    gh = FakeGitHub()
    publish.publish(p, gh, write=True, say=lambda _: None)
    assert_published(gh, p)
    nums = by_key(p)
    assert gh.issues[nums["fast"]]["labels"] == ["architecture", "fast-path", "agent"]
    assert gh.issues[nums["c3"]]["labels"] == ["agent-held"]
    assert gh.issues[nums["open"]]["labels"] == ["needs-triage"]


def _total_writes(tmp_path) -> int:
    gh = FakeGitHub()
    publish.publish(chain_plan(tmp_path / "count", 3, extra=[{"key": "solo", "lane": "agent"}]), gh, write=True,
                    say=lambda _: None)
    return gh.writes


@pytest.mark.parametrize("mode", ["crash_before", "crash_after"])
def test_rerun_after_a_crash_at_every_write(tmp_path, mode):
    total = _total_writes(tmp_path)
    for k in range(1, total + 1):
        p = chain_plan(tmp_path / f"{mode}-{k}", 3, extra=[{"key": "solo", "lane": "agent"}])
        gh = FakeGitHub()
        setattr(gh, mode, k)
        with pytest.raises(Crash):
            publish.publish(p, gh, write=True, say=lambda _: None)
        setattr(gh, mode, None)
        p = plan.load(p.path)  # a fresh process reads the plan file the crashed one left
        publish.publish(p, gh, write=True, say=lambda _: None)
        assert_published(gh, p)


def test_rerun_after_success_changes_nothing(tmp_path):
    p = chain_plan(tmp_path, 2)
    gh = FakeGitHub()
    publish.publish(p, gh, write=True, say=lambda _: None)
    before = list(gh.log)
    out: list[str] = []
    publish.publish(plan.load(p.path), gh, write=True, say=out.append)
    assert gh.log == before
    assert out[0].startswith("already published")


def test_dry_run_writes_nothing(tmp_path):
    p = chain_plan(tmp_path, 2)
    saved = p.path.read_text()
    gh = FakeGitHub()
    out: list[str] = []
    publish.publish(p, gh, write=False, say=out.append)
    assert gh.log == [] and gh.writes == 0
    assert p.path.read_text() == saved
    text = "\n".join(out)
    assert "would create `feat(invoices): slice 2` [c2], unlabelled" in text
    assert "first line: <!-- fabro:split-child parent=<p> after=<c1> -->" in text
    assert re.search(r"would label #<c2> `agent-held`[\s\S]*would label #<c1> `agent`", text)


def test_missing_labels_refuse_before_any_write(tmp_path):
    gh = FakeGitHub(labels={"agent"})
    with pytest.raises(publish.GitHubError, match="agent-held"):
        publish.publish(chain_plan(tmp_path, 2), gh, write=True, say=lambda _: None)
    assert gh.log == []


# --- the command line's refusals ---------------------------------------------------------------------


def _cli(monkeypatch, root, gh):
    monkeypatch.setattr(plan_issues, "target_root", lambda given=None: root)
    monkeypatch.setattr(plan_issues, "repo_name", lambda: "o/r")
    monkeypatch.setattr(plan_issues.pub, "Gh", lambda repo: gh)


def test_cli_refuses_a_two_predecessor_graph(tmp_path, monkeypatch, capsys):
    from conftest import REPO
    path = make_plan(tmp_path, [
        {"key": "p", "role": "parent"},
        {"key": "a", "lane": "agent", "parent": "p"},
        {"key": "b", "lane": "agent", "parent": "p"},
        {"key": "c", "lane": "agent", "parent": "p", "after": ["a", "b"]},
    ], {"p": PARENT, "a": draft_text(["Returns 200."]), "b": draft_text(["Returns 200."]), "c": draft_text(["Returns 200."])})
    gh = FakeGitHub()
    _cli(monkeypatch, REPO, gh)
    assert plan_issues.main(["publish", str(path), "--yes"]) == 1
    assert "STOP" in capsys.readouterr().err
    assert gh.log == []


def test_cli_without_yes_is_a_dry_run(tmp_path, monkeypatch, capsys):
    from conftest import REPO
    p = chain_plan(tmp_path, 2)
    gh = FakeGitHub()
    _cli(monkeypatch, REPO, gh)
    assert plan_issues.main(["publish", str(p.path)]) == 1
    assert gh.log == []
    assert "explicit yes" in capsys.readouterr().out


def test_cli_refuses_a_repository_without_ci_sh(tmp_path, capsys):
    path = make_plan(tmp_path, [{"key": "a", "lane": "agent"}], {"a": draft_text(["Returns 200."])})
    (tmp_path / "bare").mkdir()
    assert plan_issues.main(["check", str(path), "--root", str(tmp_path / "bare")]) == 1
    assert "setup-repo-for-factory" in capsys.readouterr().err


def test_cli_refuses_a_plan_for_another_repository(tmp_path, monkeypatch, capsys):
    from conftest import REPO
    p = chain_plan(tmp_path, 2)
    gh = FakeGitHub()
    _cli(monkeypatch, REPO, gh)
    monkeypatch.setattr(plan_issues, "repo_name", lambda: "someone/else")
    assert plan_issues.main(["publish", str(p.path), "--yes"]) == 1
    assert "someone/else" in capsys.readouterr().err
    assert gh.log == []
