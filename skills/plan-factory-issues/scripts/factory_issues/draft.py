"""Parse a factory issue draft: a Markdown file whose first line is `# <title>`.

The parser is deliberately literal. The pipeline reads issue bodies with `awk` line matches
(`^## Post-deploy checks$`, `^- [ ] `), so the lint has to see the body the way those matches
do, not the way a Markdown renderer would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("Context", "Requirements", "Acceptance criteria", "Non-goals", "Test expectations")
OPTIONAL = ("Expected files", "Task map", "Human actions", "Post-deploy checks")
POST_DEPLOY = "## Post-deploy checks"

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*)$")
_TICK = re.compile(r"`([^`\n]+)`")
_PATHLIKE = re.compile(r"^(?:\./)?[\w.@+-]+(?:/[\w.@+*-]+)*/?$")


@dataclass
class Item:
    """One top-level list item, with its continuation lines folded in."""

    line: int
    text: str


@dataclass
class Section:
    name: str
    line: int
    lines: list[tuple[int, str]] = field(default_factory=list)

    def items(self) -> list[Item]:
        out: list[Item] = []
        for no, raw in self.lines:
            m = _ITEM.match(raw)
            if m and not raw[:1].isspace():
                out.append(Item(no, m.group(1).strip()))
            elif out and raw.strip():
                out[-1].text += " " + raw.strip()
        return out

    def text(self) -> str:
        return "\n".join(t for _, t in self.lines)


@dataclass
class Draft:
    path: Path
    title: str | None
    title_line: int
    summary: str
    sections: dict[str, Section]
    duplicates: list[tuple[int, str]]
    headings: list[tuple[int, int, str]]  # (line, level, text), outside code fences
    raw_lines: list[str]

    def section(self, name: str) -> Section | None:
        return self.sections.get(name.lower())

    def criteria(self) -> list[Item]:
        s = self.section("Acceptance criteria")
        return s.items() if s else []

    def task_map_steps(self) -> int:
        s = self.section("Task map")
        return len(s.items()) if s else 0

    def expected_files(self) -> set[str]:
        """Paths named in `## Expected files` and `## Task map`: a hint the size and overlap
        rules read, never an allowlist."""
        out: set[str] = set()
        for name in ("Expected files", "Task map"):
            s = self.section(name)
            if s:
                out |= paths_in(s.text())
        return out

    def body(self) -> str:
        """The issue body: the draft without its `# title` line and the blank lines after it."""
        lines = self.raw_lines[self.title_line:] if self.title else self.raw_lines
        while lines and not lines[0].strip():
            lines = lines[1:]
        return "\n".join(lines).rstrip() + "\n"


def paths_in(text: str) -> set[str]:
    out = set()
    for span in _TICK.findall(text):
        span = re.sub(r":\d+(?:-\d+)?$", "", span.strip())
        if " " in span or not _PATHLIKE.match(span):
            continue
        if "/" in span or re.search(r"\.[A-Za-z0-9]{1,6}$", span):
            out.add(span.removeprefix("./"))
    return out


def parse(path: Path, text: str | None = None) -> Draft:
    text = path.read_text() if text is None else text
    raw = text.splitlines()
    title, title_line = None, 0
    for i, line in enumerate(raw, 1):
        if not line.strip():
            continue
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            title, title_line = m.group(1), i
        break

    sections: dict[str, Section] = {}
    duplicates: list[tuple[int, str]] = []
    headings: list[tuple[int, int, str]] = []
    summary: list[str] = []
    current: Section | None = None
    fenced = False
    for i, line in enumerate(raw, 1):
        if i <= title_line:
            continue
        if _FENCE.match(line):
            fenced = not fenced
        m = None if fenced else _HEADING.match(line)
        if m:
            level, name = len(m.group(1)), m.group(2)
            headings.append((i, level, name))
            if level == 2:
                key = name.lower()
                if key in sections:
                    duplicates.append((i, name))
                    current = Section(name, i)  # keep the first; still collect, never merge
                    continue
                current = sections[key] = Section(name, i)
                continue
        if current is None:
            if line.strip():
                summary.append(line.strip())
        else:
            current.lines.append((i, line))
    return Draft(path, title, title_line, " ".join(summary), sections, duplicates, headings, raw)
