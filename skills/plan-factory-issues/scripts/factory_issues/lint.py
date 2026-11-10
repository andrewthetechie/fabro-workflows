"""The factory issue lint: L1-L10 over one draft. L11 compares drafts, so it lives in `plan.py`.

Each rule cites where its rule comes from. When the factory's rule changes, the source changes
first and this file follows; FORMAT.md explains the rules to the agent and never restates them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import ci
from .draft import POST_DEPLOY, REQUIRED, Draft

SOURCES = {
    "L1": "docs/USER-GUIDE.md, Writing criteria the factory can verify, rule 1",
    "L2": "docs/USER-GUIDE.md, rule 2; issue-precheck prompts/check.md.j2, `unverifiable`",
    "L3": "docs/USER-GUIDE.md, rule 3",
    "L4": "docs/USER-GUIDE.md, the paragraph after rule 4",
    "L5": "docs/USER-GUIDE.md, rule 4; issue-precheck prompts/check.md.j2, `unverifiable`",
    "L6": "FORMAT.md; the `^## Post-deploy checks$` and `^- [ ] ` matches in ralph's and the merge phase's shell",
    "L7": "_shared/review-merge/review-merge.fabro, the merge gate's title check",
    "L8": "ADR 0015 D4; ops/scheduler/src/fabro_scheduler/split.py",
    "L9": "scripts/measure_sizes.py, run 2026-11-10 over 205 ralph runs",
    "L10": "docs/USER-GUIDE.md, The fast path and merge-on-green",
    "L11": "the cc-sdd amendment to #67 (docs/cc-sdd-research/01-adoption-plan.md, section 7)",
}
UNWAIVABLE = {"L6", "L7", "L8"}

# L9. Re-measure with scripts/measure_sizes.py before changing one; the docstring there has the query.
MAX_TASK_MAP_STEPS = 4  # 1-4 planned Tasks: 36% auto-merged (n=61). 5 or more: 15% (n=20).
MAX_EXPECTED_FILES = 6  # 0-6 paths named: 42% auto-merged, 10% no PR (n=105). 7 or more: 23%, 22% (n=100).
MAX_CRITERIA = 9  # no outcome trend from 0 to 9 criteria (n=201); 9 is the edge of the data, not a cliff.
# L10: the fast lane merges the standard way past 200 changed lines or 10 files.
FAST_MAX_FILES = 10

TITLE = re.compile(r"^(feat|fix|docs|chore|refactor|test|ci|build|perf|revert)([(][a-z0-9 ._/-]+[)])?[!]?: .")

PR_TEXT = re.compile(
    r"(?i)\b(?:PR|pull request)(?:'s)?\s+(?:description|body|text|summary|comment|explains?|lists?|shows?|"
    r"states?|notes?|names?|describes?|includes?|records?|mentions?|documents?|reports?|cites?|says)\b"
    r"|\b(?:note|record|report|list|state|explain|describe|document|mention|show|include|attach|add|put|write|post|"
    r"cite|say)\w*\b[^.;]{0,80}?\b(?:in|on|to|into)\s+(?:the|this|its|a)\s+(?:PR|pull request)\b(?!\s+(?:branch|diff|title))"
)
CI_CHECK = re.compile(
    r"(?i)\b(?:github\s+(?:actions?|checks?|ci)|ci\s+(?:jobs?|checks?|runs?|pipelines?|workflows?)|"
    r"(?:all|required)\s+(?:status\s+)?checks|checks?\s+(?:are|is)\s+green|workflow\s+runs?)\b"
)
SEARCH_TOOLS = {"rg", "grep", "egrep", "ag", "ack", "find", "fd", "git grep", "git ls-files", "ls", "cat", "wc", "jq"}
PASS = re.compile(
    r"(?i)\b(?:pass(?:es|ing)?|succeed(?:s|ing)?|exits?\s+(?:with\s+)?(?:0|zero|cleanly|success)|(?:is|are)\s+green|"
    r"returns?\s+(?:0|zero)|reports?\s+(?:no|nothing)|runs?\s+clean(?:ly)?|clean|no\s+(?:errors|warnings|findings))\b"
)
KNOWN_TOOLS = {
    "pytest", "tsc", "eslint", "mypy", "ruff", "vitest", "jest", "prettier", "clippy", "black", "flake8",
    "pyright", "ty", "biome", "rubocop", "rspec", "phpunit", "golangci-lint", "shellcheck", "actionlint",
    "typos", "zizmor", "stylelint", "markdownlint", "hadolint", "bandit", "pylint", "isort",
}
CITE = re.compile(r"ci\.sh:\d+|scripts\.[\w:-]+|Makefile:\d+|pre-commit-config\.ya?ml:\d+")
NO_DROP = re.compile(
    r"(?i)\b(?:must|does|do|should|shall|will|may|can)\s*(?:not|n't)\s+(?:drop|decrease|fall|go\s+down|regress|"
    r"decline|shrink|worsen|get\s+worse|be\s+lower)|\bnever\s+(?:drops?|decreases?|falls?)|"
    r"\bno\s+(?:drop|decrease|regression|reduction|decline)\s+in\b|"
    r"\b(?:stays?|remains?)\s+(?:at\s+or\s+above|above|at\s+least)\s+(?:the\s+)?(?:current|existing|baseline|today)|"
    r"\bnot\s+below\s+(?:the\s+)?(?:current|existing|baseline)"
)
METRIC = re.compile(
    r"(?i)coverage|%|percent|score|\brate\b|latency|\bsize\b|\bcount\b|performance|throughput|duration|speed|"
    r"accuracy|hit@|mrr|recall|precision|bundle|benchmark|threshold|metric|current|baseline"
)
HUMAN_ONLY = re.compile(
    r"(?i)\bscreenshots?\b|\bmanually\b|\bmanual\s+(?:check|test(?:ing)?|verification|inspection|review|qa|run|step)s?\b|"
    r"\bvisual(?:ly)?\s+(?:check|inspect\w*|confirm\w*|verif\w*|review\w*|regression)|\beyeball|"
    r"\b(?:check|verif\w*|test\w*|confirm\w*)\s+(?:it\s+|this\s+)?by\s+hand\b|"
    r"\bon\s+(?:windows|macos|mac\s?os|linux|ios|android)\b|"
    r"\b(?:on|in|against)\s+(?:staging|production|prod|dev|the\s+dev\s+environment|an?\s+deployed|the\s+deployed|the\s+live)\b"
    r"(?!\s+(?:code|build|bundle|mode|dependenc\w*|deps|config\w*|image))|"
    r"\bafter\s+(?:deploy|deployment|release|merge)\b|\breal\s+(?:api\s+keys?|credentials|secrets?|tokens?)\b"
)
WORKFLOW = re.compile(r"\.github/workflows\b")
CHANGE_VERB = re.compile(
    r"(?i)\b(?:add|adds|added|edit\w*|chang\w*|modif\w*|updat\w*|creat\w*|writ\w*|remov\w*|delet\w*|renam\w*|"
    r"bump\w*|pin\w*|replac\w*|mov\w*|has|have|contains?|defines?|gains?|gets?|sets?|uses?|runs?|triggers?|builds?)\b"
)
NEGATED_WORKFLOW = re.compile(
    r"(?i)\b(?:no|nothing|not|never|without|outside|except|untouched|unchanged|none)\b[^.;]*\.github/workflows"
)
FABRO_MARKER = re.compile(r"<!--\s*fabro:")
FREE_BLOCK = re.compile(
    r"(?i)\b(?:blocked\s+by|depends\s+on|depend(?:s|ent)?\s+upon|waits?\s+(?:on|for)|must\s+land\s+after|lands?\s+after)\s+#\d+"
)
POST_DEPLOY_LIKE = re.compile(r"(?i)^#{1,6}\s*post[\s-]*deploy")


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    severity: str  # "error" | "warning"
    message: str
    waived: str | None = None

    def __str__(self) -> str:
        sev = f"waived ({self.waived})" if self.waived else self.severity
        return f"{self.path}:{self.line}: {self.rule} {sev}: {self.message}"


def lint_draft(d: Draft, routes: ci.Routes | None, *, lane: str | None = None, role: str = "issue") -> list[Finding]:
    out: list[Finding] = []
    path = str(d.path)

    def add(line: int, rule: str, severity: str, message: str) -> None:
        out.append(Finding(path, line, rule, severity, message))

    # L7: the title. The merge gate refuses any other title, so the PR could never auto-merge.
    if d.title is None:
        add(1, "L7", "error", "the draft must start with `# <title>`")
    elif not TITLE.match(d.title):
        add(d.title_line, "L7", "error", f"`{d.title}` is not a Conventional Commit title, e.g. `feat(scope): add x`")

    # L6: the shape.
    if not d.summary:
        add(d.title_line or 1, "L6", "error", "a summary or user story must follow the title, before the first `##`")
    for line, name in d.duplicates:
        add(line, "L6", "error", f"`## {name}` appears twice")
    if role == "issue":
        for name in REQUIRED:
            s = d.section(name)
            if s is None:
                add(d.title_line or 1, "L6", "error", f"`## {name}` is missing")
            elif not s.text().strip():
                add(s.line, "L6", "error", f"`## {name}` is empty")
        crit = d.section("Acceptance criteria")
        if crit is not None and crit.text().strip() and not d.criteria():
            add(crit.line, "L6", "error", "`## Acceptance criteria` has no list items; write one `- ` per criterion")
    for line, _level, text in d.headings:
        raw = d.raw_lines[line - 1]
        if POST_DEPLOY_LIKE.match(raw) and raw.rstrip() != POST_DEPLOY:
            add(line, "L6", "error", f"write the heading exactly `{POST_DEPLOY}`: the pipeline cuts only that line from the Refuter's input")
    pd = d.section("Post-deploy checks")
    if pd is not None:
        for no, raw in pd.lines:
            if raw.strip() and not raw.startswith("- [ ] ") and not raw[:1].isspace():
                add(no, "L6", "error", "write each post-deploy check as `- [ ] ...`: only those lines reach the PR body")

    # L8: ordering lives in the plan, never in the body.
    fenced = False
    for no, raw in enumerate(d.raw_lines, 1):
        if raw.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if FABRO_MARKER.search(raw):
            add(no, "L8", "error", "remove the `<!-- fabro:... -->` marker: the publisher writes the only one a draft gets")
        elif not fenced and FREE_BLOCK.search(raw):
            add(no, "L8", "error", "a free-form dependency line does not hold the scheduler; express the order with `after` in the plan")

    # L4: `.github/workflows/` is never the agent's work. A file list names a change outright; a
    # requirement or criterion does when a change verb shares the sentence and the path is not
    # merely an argument to a reading tool (`actionlint .github/workflows/x.yml`).
    for name in ("Expected files", "Task map", "Requirements", "Acceptance criteria"):
        s = d.section(name)
        if s is None:
            continue
        for no, raw in s.lines:
            for sentence in sentences(raw):
                if not WORKFLOW.search(sentence) or NEGATED_WORKFLOW.search(sentence):
                    continue
                if name in ("Requirements", "Acceptance criteria"):
                    kept = re.sub(r"`[^`]*`", lambda m: "" if _reads(m.group(0)) else m.group(0), sentence)
                    if not WORKFLOW.search(kept) or not CHANGE_VERB.search(WORKFLOW.sub("", kept)):
                        continue
                add(no, "L4", "error", "a `.github/workflows/` change cannot be the agent's work (its token cannot push one); "
                    "move it to `## Human actions`")
                break

    for c in d.criteria():
        text = c.text
        # L2, GitHub's checks: the Refuter sees `.fabro/ci.sh`'s run, never GitHub's.
        if any(CI_CHECK.search(x) and PASS.search(x) for x in sentences(text)):
            add(c.line, "L2", "error", "the Refuter sees `.fabro/ci.sh`'s run, not GitHub's checks; name the command `ci.sh` "
                "runs, or a state readable from the diff")
        elif PR_TEXT.search(text):
            add(c.line, "L1", "error", "no stage writes the PR description and the Refuter often runs before the PR exists; "
                "ask for a code comment or a file in the repository instead, or drop it")
        if NO_DROP.search(text) and METRIC.search(text):
            add(c.line, "L3", "error", "write \"must not drop\" as a raised threshold `ci.sh` enforces, e.g. \"`fail_under` in "
                "<config> is raised to the new total, rounded down\"")
        if HUMAN_ONLY.search(text):
            add(c.line, "L5", "error", "only a human can check this; move it to `## Post-deploy checks` as `- [ ] ...`")
        if routes is not None:
            for span in command_spans(text):
                _route(add, c.line, text, span, routes)

    # L9: one Ralph run.
    steps, files, n_crit = d.task_map_steps(), d.expected_files(), len(d.criteria())
    if role == "issue":
        if steps > MAX_TASK_MAP_STEPS:
            add(d.section("Task map").line, "L9", "warning",
                f"{steps} Task map steps (over {MAX_TASK_MAP_STEPS}); runs planned past 4 Tasks auto-merged half as often. Split it")
        if len(files) > MAX_EXPECTED_FILES:
            s = d.section("Expected files") or d.section("Task map")
            add(s.line, "L9", "warning",
                f"{len(files)} expected files (over {MAX_EXPECTED_FILES}); runs naming more than 6 ended without a PR twice as often")
        if n_crit > MAX_CRITERIA:
            add(d.section("Acceptance criteria").line, "L9", "warning",
                f"{n_crit} acceptance criteria (over {MAX_CRITERIA}); check that this is one slice")

    # L10: the fast lane.
    if lane == "fast-path":
        if steps > 1:
            add(d.section("Task map").line, "L10", "warning", "a fast-path issue is one commit; a Task map of more than one step is not")
        if len(files) > FAST_MAX_FILES:
            add(d.title_line or 1, "L10", "warning",
                f"{len(files)} expected files; the fast lane merges the standard way past {FAST_MAX_FILES} files or 200 lines")
    return out


def sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.;:])\s+(?=[A-Z`(])", text) if x.strip()]


def _reads(span: str) -> bool:
    """A backticked command that only reads what it names: a linter or a search over a path."""
    words = ci.split_words(span.strip("`"))
    return len(words) > 1 and not WORKFLOW.match(words[0])


def command_spans(text: str) -> list[str]:
    """Backticked commands in a sentence that says they succeed: a **Command criterion**. A search
    (`rg -n x src/` finds nothing) describes the code, which the Refuter reads for itself."""
    out = []
    for sentence in sentences(text):
        if not PASS.search(sentence):
            continue
        for span in re.findall(r"`([^`\n]+)`", sentence):
            span = span.strip()
            if ".fabro/ci.sh" in span:
                out.append(span)
                continue
            words = ci.split_words(span)
            if not words or any(ch in span for ch in "{}();<>") or re.search(r"[=!]=|\s=\s", span):
                continue
            bare = ci.strip_wrappers(words)
            if not bare or any(re.match(r"^[A-Z][A-Z0-9_]+$", x) for x in bare) \
                    or any("=" in x and not x.startswith("-") for x in bare):
                continue
            first = bare[0]
            key = ci.tool_key(span)
            if first in SEARCH_TOOLS or (key or "") in SEARCH_TOOLS:
                continue
            if len(words) == 1:
                if first in KNOWN_TOOLS:
                    out.append(span)
                continue
            if re.match(r"^(?:\./)?[a-z][\w.+/-]*$", first) and key:
                out.append(span)
    return out


def _route(add, line: int, text: str, span: str, routes: ci.Routes) -> None:
    m = ci.find(routes, span)
    if m.status == "found":
        if not CITE.search(text) and m.via is not None:
            add(line, "L2", "warning", f"cite where `ci.sh` runs `{span}`: {m.via.route()}")
        return
    hint = f"; the closest it runs is {m.closest.route()}" if m.closest else ""
    if m.status == "partial":
        unresolved = "; ".join(f"{u.where} ({u.why})" for u in routes.unresolved[:3])
        add(line, "L2", "warning", f"`.fabro/ci.sh` runs `{span}` nowhere the lint can follow{hint}. It may run in what "
            f"the lint could not follow: {unresolved}. Confirm it, or rewrite the criterion")
        return
    add(line, "L2", "error", f"`.fabro/ci.sh` never runs `{span}`{hint}. Rewrite it as a behaviour the Refuter can read "
        "from the diff, or make adding it to `ci.sh` a human action")
