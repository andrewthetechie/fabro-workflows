"""The entry nodes' code-index lines and agent-guide install (docs/code-context C4, tasks 04
and 05; docs/coder-tweaks 02, C1).

backlog `prep` cannot run whole here (it runs the target repo's setup.sh), so its index
lines are pulled out of the graph's own `prep` script -- never retyped -- and run under
`set -e`, which is how prep runs them. The other three entry nodes run WHOLE, from their own
graphs, in a real git repo with `gh` stubbed: pr-review `claim` (a routing node, so stdout
must stay exactly one routing object), arch-review `prep` (set -e, stdout must stay `ready`)
and issue-triage `acquire` (no set -e, no stdout). Each runs twice with a `fabro-code` that
prints to stdout, and once with no `fabro-code` at all -- an image built before task 02.
"""

import os
from pathlib import Path

from harness import (ARCH, GRAPH, INTRIAGE, ORIG_PATH, PR, cat, count, extract, git, grep,
                     present, rebase, rm, run, sh, sh_c, stub, syntax_ok, which, write)

GH_STUB = """#!/bin/sh
case "$1 $2" in
  "pr view")     echo '{"number":7,"title":"t","body":"b","url":"https://x/pull/7","state":"OPEN","isCrossRepository":false,"headRefName":"feat","baseRefName":"main","headRefOid":"abc","labels":[],"closingIssuesReferences":[]}' ;;
  "issue list")  case "$*" in *needs-triage*) echo '[{"number":5,"title":"x"}]' ;; *) echo '[]' ;; esac ;;
esac
exit 0
"""

FC_STUB = """#!/bin/sh
echo 'FABRO-CODE LEAKED TO STDOUT'
case "$1" in index) printf 'ok\\n' > "$FABRO_TEST_STATE" ;; esac
exit 0
"""

WHOLE = [("claim", PR, '{"context_updates":{"base_ref":"main"}}'),
         ("prep", ARCH, "ready"),
         ("acquire", INTRIAGE, "")]


class Entry:
    """The tools a node needs, minus fabro-code, for the "old image" runs. On the Mac jq is
    under /opt/homebrew, and in the images fabro-code sits next to gh in /usr/local/bin, so
    neither PATH can simply be trimmed."""

    def __init__(self, work: Path):
        self.work = work
        self.nofc_bin = work / "nofc-bin"
        self.nofc_bin.mkdir(parents=True, exist_ok=True)
        for tool in ("jq", "git"):
            link = self.nofc_bin / tool
            rm(link)
            link.symlink_to(which(tool))
        self.nofc_path = f"{self.nofc_bin}:/usr/bin:/bin"

    def repo(self, d: Path) -> Path:
        """A real repo with one commit and a stub bin/."""
        rm(d)
        for sub in ("repo", "bin", "sb"):
            (d / sub).mkdir(parents=True)
        git("init", "-q", "-b", "main", ".", cwd=d / "repo")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=d / "repo")
        stub(d / "bin", "gh", GH_STUB)
        stub(d / "bin", "fabro-code", FC_STUB)
        link = self.nofc_bin / "gh"
        rm(link)
        link.symlink_to(d / "bin" / "gh")
        return d


def test_code_index_entry_fragments(ck, tmp_path):
    E = Entry(tmp_path)

    # backlog prep: the two lines, from the graph.
    ET = E.repo(tmp_path / "entry-backlog-prep")
    frag_text = grep(["-e", "grep -qx '.codegraph/'", "-e", "command -v fabro-code"],
                     rebase(extract(GRAPH, "prep"), ET / "sb"), pipe=True)
    frag = write(ET / "frag.sh", frag_text + "\n" if frag_text else "")
    ck("backlog prep carries both index lines", "2", count(".", file=frag))
    for _ in (1, 2):
        sh_c("set -e; " + cat(frag), cwd=ET / "repo", path=f"{ET / 'bin'}:{ORIG_PATH}",
             env={"FABRO_TEST_STATE": ET / "sb" / "code_index"})
    ck("backlog prep exclude line added once", "1", count(".codegraph/", file=ET / "repo/.git/info/exclude", flags="x"))
    ck("backlog prep index ran", "ok", cat(ET / "sb/code_index", default="missing"))
    rm(ET / "sb/code_index")
    r = sh_c("set -e; " + cat(frag), cwd=ET / "repo", path=E.nofc_path, stderr="keep")
    ck("backlog prep with no fabro-code exits 0", "0", r.rc)
    ck("backlog prep records not-in-image", "failed: fabro-code not in image", cat(ET / "sb/code_index"))

    # The three whole nodes. Expected stdout: claim one routing object, prep `ready`,
    # acquire nothing.
    for node, graph, want in WHOLE:
        ET = E.repo(tmp_path / f"entry-{node}")
        write(ET / "sb/pr_number", "7\n")
        script = write(ET / f"{node}.sh", rebase(extract(graph, node), ET / "sb"))
        ok, err = syntax_ok(script)
        if not ok:
            ck.fail(f"{node} entry script not valid POSIX sh", err)
        for _ in (1, 2):
            r = sh(script, cwd=ET / "repo", path=f"{ET / 'bin'}:{ORIG_PATH}",
                   env={"FABRO_TEST_STATE": ET / "sb/code_index"}, stderr="keep")
        ck(f"{node} exits 0 with fabro-code", "0", r.rc)
        ck(f"{node} stdout unchanged with fabro-code", want, r.out)
        ck(f"{node} index ran", "ok", cat(ET / "sb/code_index", default="missing"))
        ck(f"{node} exclude line added once", "1", count(".codegraph/", file=ET / "repo/.git/info/exclude", flags="x"))
        rm(ET / "sb/code_index")
        r = sh(script, cwd=ET / "repo", path=E.nofc_path, stderr="keep")
        ck(f"{node} exits 0 with no fabro-code", "0", r.rc)
        ck(f"{node} stdout unchanged with no fabro-code", want, r.out)
        ck(f"{node} records not-in-image", "failed: fabro-code not in image", cat(ET / "sb/code_index"))
    ck("acquire still picks its issue", "5", cat(tmp_path / "entry-acquire/sb/issue_number"))


def test_agent_guide(ck, tmp_path):
    """The agent guide install, in the same four entry nodes. The guide is written to
    .codex/instructions.md and excluded once; an empty variable writes nothing; a repository
    that TRACKS the file keeps it byte-identical; stdout is unchanged and the node still
    succeeds. The backlog fragment is cut out of `prep` by its first and last line."""
    E = Entry(tmp_path)
    guide = "# guide\nline two"
    entries = [("prep", GRAPH, "")] + WHOLE
    for node, graph, want in entries:
        label = "backlog-prep" if graph == GRAPH else "arch-prep" if graph == ARCH else node
        ET = E.repo(tmp_path / f"guide-{label}")
        write(ET / "sb/pr_number", "7\n")
        text = rebase(extract(graph, node), ET / "sb")
        if graph == GRAPH:
            cut = run(["awk", '/^if \\[ -z "\\$FABRO_AGENT_GUIDE"/ {p = 1} p {print} /^else echo .agent guide/ {p = 0}'],
                      stdin=text, stderr="drop").out
            script = write(ET / f"{node}.sh", cut + "\n" if cut else "")
            ck(f"{label}: guide lines found", "4", count(".", file=script))
            prefix = "set -e; "
        else:
            script = write(ET / f"{node}.sh", text)
            prefix = ""
        for _ in (1, 2):
            r = sh_c(f"{prefix}. {script}", cwd=ET / "repo", path=f"{ET / 'bin'}:{ORIG_PATH}",
                     env={"FABRO_TEST_STATE": ET / "sb/code_index", "FABRO_AGENT_GUIDE": guide},
                     stderr="keep")
        ck(f"{label}: guide install exits 0", "0", r.rc)
        ck(f"{label}: guide file is the guide", guide, cat(ET / "repo/.codex/instructions.md"))
        ck(f"{label}: guide excluded once", "1", count(".codex/instructions.md", file=ET / "repo/.git/info/exclude", flags="x"))
        ck(f"{label}: guide file not in porcelain", "0", count("codex", git("status", "--porcelain", cwd=ET / "repo"), pipe=True))
        if graph != GRAPH:
            ck(f"{label}: stdout unchanged by the guide", want, grep(["-v", "FABRO-CODE LEAKED"], r.out))

        # an empty variable: nothing is written, the node still succeeds
        ET2 = E.repo(tmp_path / f"guide0-{label}")
        write(ET2 / "sb/pr_number", "7\n")
        s2 = write(ET2 / f"{node}.sh", script.read_text().replace(str(ET / "sb"), str(ET2 / "sb")))
        r = sh_c(f"{prefix}. {s2}", cwd=ET2 / "repo", path=f"{ET2 / 'bin'}:{ORIG_PATH}",
                 env={"FABRO_TEST_STATE": ET2 / "sb/code_index", "FABRO_AGENT_GUIDE": ""})
        ck(f"{label}: empty guide exits 0", "0", r.rc)
        ck(f"{label}: empty guide writes nothing", "absent", present(ET2 / "repo/.codex"))

        # a tracked file is left alone
        ET3 = E.repo(tmp_path / f"guide1-{label}")
        write(ET3 / "sb/pr_number", "7\n")
        s3 = write(ET3 / f"{node}.sh", script.read_text().replace(str(ET / "sb"), str(ET3 / "sb")))
        write(ET3 / "repo/.codex/instructions.md", "mine\n")
        git("add", "-A", cwd=ET3 / "repo")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "codex", cwd=ET3 / "repo")
        r = sh_c(f"{prefix}. {s3}", cwd=ET3 / "repo", path=f"{ET3 / 'bin'}:{ORIG_PATH}",
                 env={"FABRO_TEST_STATE": ET3 / "sb/code_index", "FABRO_AGENT_GUIDE": guide})
        ck(f"{label}: tracked file, exits 0", "0", r.rc)
        ck(f"{label}: tracked file left alone", "mine", cat(ET3 / "repo/.codex/instructions.md"))
        ck(f"{label}: tracked file not excluded", "0", count(".codex/instructions.md", file=ET3 / "repo/.git/info/exclude", flags="x"))
