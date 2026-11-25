#!/usr/bin/env python3
"""The exact edits that turn ralph's iterate.md.j2 into ralph-v2's (task 06).

    python3 docs/plans/ralph-v2/assets/iterate-replacements.py \
        .fabro/workflows/ralph/prompts/iterate.md.j2 .fabro/workflows/ralph-v2/prompts/iterate.md.j2

Every `old` occurs exactly once in ralph's file; the script stops on any that does not.
"""
import sys

EDITS = [
    (  # 1. what the dossier now holds
        "  following it would violate the Task, and name every departure in your summary. Under\n"
        "  `## Last attempt` is the previous Iteration's failure on this same Task: start there.\n",
        "  following it would violate the Task, and name every departure in your summary. `## Goals`\n"
        "  lists the goals in the order to work them, `## Edge cases to cover` the cases that each need\n"
        "  a test, and `## Class of the bug`, when present, the class a fix must cover, not only the\n"
        "  reported example. Under `## Last attempt` is the previous Iteration's failure on this same\n"
        "  Task: start there. Under `## Review findings` is what two reviewers found after a passing\n"
        "  attempt: this Iteration is the rework.\n",
    ),
    (  # 2. how to work goals and findings
        "Think in a loop of three: the next acceptance statement, the edit, the check.\n",
        "Think in a loop of three: the next goal, the edit, the check. Work every open goal in this\n"
        "Iteration, in order. When the dossier has no `## Goals`, the acceptance statements are the\n"
        "goals.\n\n"
        "Write a test for each edge case under `## Edge cases to cover`. Skip one only when you can show\n"
        "it cannot happen, and say so in your summary.\n\n"
        "**On a rework** (`## Review findings` is present), answer every finding. Fix all of them in this\n"
        "Iteration, warnings included, and fix the whole class when one is named. When you can show that\n"
        "a finding is wrong, dispute it instead: give a `path:N` or a command in backticks. The next\n"
        "review must then withdraw it or bring new evidence. A dispute without evidence is an unanswered\n"
        "finding. Do not change correct code to please a finding you can disprove.\n",
    ),
    (  # 3. the new optional fields
        "Add the optional `blocked` object, with status `progress`, only when this Iteration could\n"
        "not do the Task at all. A Task that is merely hard gets worked.\n",
        "Add these optional fields when they apply:\n\n"
        "- `goals_done`: the ids of the goals that now hold.\n"
        "- `edge_tests`: for each edge case, its id and the test that covers it.\n"
        "- `fixed`: the ids of the review findings you fixed.\n"
        "- `disputes`: for each finding you can disprove, its id, the reason and the evidence.\n\n"
        "On a rework, every finding id belongs in `fixed` or in `disputes`.\n\n"
        "Add the optional `blocked` object, with status `progress`, only when this Iteration could\n"
        "not do the Task at all. A Task that is merely hard gets worked.\n",
    ),
]


def main() -> int:
    text = open(sys.argv[1]).read()
    for old, new in EDITS:
        if text.count(old) != 1:
            print(f"expected exactly one occurrence, found {text.count(old)}: {old[:70]!r}", file=sys.stderr)
            return 1
        text = text.replace(old, new)
    open(sys.argv[2], "w").write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
