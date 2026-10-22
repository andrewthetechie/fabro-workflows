"""The shared triage phase (_shared/triage): claim, triage_gate, the terminal outcome,
apply_ready, improve_gate's body rules, plan_gate and apply_split (ADR 0012, ADR 0015)."""

from harness import (ORIG_PATH, SHARED_TRIAGE, cat, count, grep, head1, jq, jq_n, lastjson,
                     mkreadok, rm, rm_glob, sh, stage, stub, write)

NODES = ("claim", "triage_gate", "post_questions", "release", "done", "apply_ready",
         "improve_gate", "plan_gate", "apply_split")

TRIAGE_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "issue view") cat "$GH_STATE/issue_fixture.json"; exit 0 ;;
esac
if [ "$1" = api ]; then
  [ -f "$GH_STATE/events_fail" ] && exit 1
  cat "$GH_STATE/events.json"; exit 0
fi
exit 0
"""


class Triage:
    def __init__(self, ck, T):
        self.T = T
        (T / "bin").mkdir(parents=True, exist_ok=True)
        self.s = {n: stage(ck, n, T, graph=SHARED_TRIAGE) for n in NODES}
        mkreadok(T, "ok1", "issue.json")
        stub(T / "bin", "gh", TRIAGE_GH)
        self.path = f"{T / 'bin'}:{ORIG_PATH}"
        self.env = {"GH_LOG": T / "gh.log", "GH_STATE": T}

    def run(self, node, cwd=None, **kw):
        return sh(self.s[node], cwd=cwd, path=self.path, env=self.env, **kw)

    def setup(self, labels, created=None):
        T = self.T
        rm(T / "gh.log", T / "triage_outcome", T / "events_fail", T / "issue.json")
        write(T / "issue_number", "7\n")
        write(T / "issue_fixture.json", f'{{"number":7,"title":"t","body":"b","labels":{labels},"comments":[],"url":"https://github.com/o/r/issues/7"}}')
        if created:
            write(T / "events.json", f'[{{"event":"labeled","label":{{"name":"triage-in-progress"}},"created_at":"{created}"}}]')
        else:
            write(T / "events.json", "[]\n")


def ctx(out, key):
    return jq(f".context_updates.{key}", lastjson(out))


def test_triage_claim_and_gates(ck, tmp_path):
    tr = Triage(ck, tmp_path / "triage")
    T = tr.T
    log = T / "gh.log"

    # 1. No claim on the issue: claim it.
    tr.setup("[]")
    r = tr.run("claim")
    ck("claim: exit 0", "0", r.rc)
    ck("claim: claimed", "claimed", ctx(r.out, "claim_state"))
    ck("claim: issue_url published", "https://github.com/o/r/issues/7", ctx(r.out, "issue_url"))
    ck("claim: adds the claim label", "1", count("^issue edit 7 --add-label triage-in-progress", file=log))

    # 2. Another run claimed it less than 24h ago (a future date is always fresh).
    tr.setup('[{"name":"triage-in-progress"}]', "2099-01-01T00:00:00Z")
    r = tr.run("claim")
    ck("fresh claim: skipped", "skipped", ctx(r.out, "claim_state"))
    ck("fresh claim: outcome file", "skipped", cat(T / "triage_outcome"))
    ck("fresh claim: no edit", "0", count("^issue edit", file=log))

    # 3. The claim is older than 24h: take it over.
    tr.setup('[{"name":"triage-in-progress"}]', "2020-01-01T00:00:00Z")
    r = tr.run("claim")
    ck("stale claim: claimed", "claimed", ctx(r.out, "claim_state"))
    ck("stale claim: edits", "1", count("^issue edit 7 --add-label triage-in-progress", file=log))

    # 4. The event list cannot be read: fail closed to skipped.
    tr.setup('[{"name":"triage-in-progress"}]', "2020-01-01T00:00:00Z"); write(T / "events_fail", "")
    r = tr.run("claim")
    ck("events unreadable: skipped", "skipped", ctx(r.out, "claim_state"))

    # 5. done publishes the file, and anything else as released.
    done = lambda: ctx(tr.run("done").out, "triage_outcome")
    write(T / "triage_outcome", "skipped\n")
    ck("done: publishes the word", "skipped", done())
    rm(T / "triage_outcome")
    ck("done: no file is released", "released", done())
    write(T / "triage_outcome", "garbage\n")
    ck("done: garbage is released", "released", done())

    # 6. triage_gate: needs_info with one question.
    tr.setup("[]")
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", '{"readiness":"needs_info","title":"feat: x","labels":["bug"],"questions":[{"id":"Q1","question":"Which endpoint?","why":"w","recommended":"/v2"}],"_io":{"visit":"ok1"}}\n')
    write(T / "triage.md", "report\n")
    r = tr.run("triage_gate")
    ck("gate needs_info: exit 0", "0", r.rc)
    ck("gate needs_info: readiness", "needs_info", ctx(r.out, "triage_readiness"))
    ck("gate: no answered key", "false", jq('.context_updates | has("answered")', lastjson(r.out)))

    # 7. triage_gate: ready with a question is invalid; the first attempt fails.
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", '{"readiness":"ready","title":"feat: x","labels":[],"questions":[{"id":"Q1","question":"q"}],"_io":{"visit":"ok1"}}\n')
    ck("gate ready+question: retries", "1", tr.run("triage_gate").rc)

    # 8. release removes the claim and records released.
    write(T / "issue.json", '{"number":7}'); write(log, "")
    tr.run("release")
    ck("release: outcome file", "released", cat(T / "triage_outcome"))
    ck("release: removes the claim", "1", count("^issue edit 7 --remove-label triage-in-progress", file=log))

    # 9. post_questions labels needs-info and records needs_info.
    write(T / "issue.json", '{"number":7,"labels":[{"name":"needs-triage"},{"name":"triage-in-progress"}]}')
    write(T / "triage.json", '{"readiness":"needs_info","title":"feat: x","labels":["bug"],"questions":[{"id":"Q1","question":"q"}],"_io":{"visit":"ok1"}}\n')
    write(log, "")
    tr.run("post_questions")
    ck("post_questions: outcome file", "needs_info", cat(T / "triage_outcome"))
    ck("post_questions: needs-info", "1", count("--add-label needs-info", file=log))

    # 10. An Architecture issue may decide without a basis.
    write(T / "issue.json", '{"number":7,"body":"b","labels":[{"name":"architecture"}]}')
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", '{"readiness":"ready","title":"refactor: x","labels":[],"questions":[],"decisions":[{"question":"q","decision":"d"}],"_io":{"visit":"ok1"}}\n')
    r = tr.run("triage_gate")
    ck("decide arch: exit 0", "0", r.rc)
    ck("decide arch: ready", "ready", ctx(r.out, "triage_readiness"))

    # 11. A human-filed issue needs a basis.
    write(T / "issue.json", '{"number":7,"body":"b","labels":[]}')
    write(T / "triage_attempts", "0\n")
    ck("decide human, no basis: retry", "1", tr.run("triage_gate").rc)

    # 12. decisions must be an array.
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", '{"readiness":"ready","title":"refactor: x","labels":[],"questions":[],"decisions":"x","_io":{"visit":"ok1"}}\n')
    ck("decisions not array: retry", "1", tr.run("triage_gate").rc)

    # 12b. A Contradiction (#17, docs/unmet-criteria C1): a criterion its own non-goals
    # forbid. `ready` with one is refused; `needs_info` with one passes; a non-array is
    # refused. `submit` enforces the key's presence, so the gate reads a missing one as [].
    write(T / "issue.json", '{"number":7,"body":"b","labels":[]}')
    resend = '[{"criterion":"The screen offers a Resend email action","non_goal":"No backend changes","needed_surface":"a backend endpoint that calls the Auth0 resend API with a secret"}]'
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", jq_n('{readiness:"ready",title:"feat: x",labels:[],questions:[],decisions:[],contradictions:$c,_io:{visit:"ok1"}}', "--argjson", "c", resend, compact=False) + "\n")
    ck("contradiction ready: retries", "1", tr.run("triage_gate").rc)
    r = tr.run("triage_gate")
    ck("contradiction ready: 2nd exit 0", "0", r.rc)
    ck("contradiction ready: invalid", "invalid", ctx(r.out, "triage_readiness"))
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", jq_n('{readiness:"needs_info",title:"feat: x",labels:[],questions:[{id:"Q1",question:"Resend needs a backend; drop the criterion or the non-goal?",why:"w"}],decisions:[],contradictions:$c,_io:{visit:"ok1"}}', "--argjson", "c", resend, compact=False) + "\n")
    r = tr.run("triage_gate")
    ck("contradiction needs_info: exit 0", "0", r.rc)
    ck("contradiction needs_info: kept", "needs_info", ctx(r.out, "triage_readiness"))
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", '{"readiness":"needs_info","title":"feat: x","labels":[],"questions":[{"id":"Q1","question":"q","why":"w"}],"decisions":[],"contradictions":"x","_io":{"visit":"ok1"}}\n')
    ck("contradictions not array: retry", "1", tr.run("triage_gate").rc)
    write(T / "triage_attempts", "0\n")
    write(T / "triage.json", '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"decisions":[],"contradictions":[],"_io":{"visit":"ok1"}}\n')
    r = tr.run("triage_gate")
    ck("no contradiction: ready", "ready", ctx(r.out, "triage_readiness"))

    # 13. apply_ready appends one section, and replaces an old one.
    decided = T / "decided_body.md"
    write(T / "issue.json", '{"number":7,"body":"first line","labels":[]}')
    write(T / "triage.json", '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"decisions":[{"question":"Where?","decision":"Here.","basis":"CONTEXT.md"}],"_io":{"visit":"ok1"}}\n')
    write(T / "triage.md", "report\n"); write(log, ""); rm(decided)
    tr.run("apply_ready")
    ck("decided: one heading", "1", count("^## Decisions made during triage$", file=decided))
    ck("decided: keeps the body", "1", count("^first line$", file=decided))
    ck("decided: lists the decision", "1", count("^- \\*\\*Where?\\*\\* Here. (basis: CONTEXT.md)$", file=decided))
    write(T / "issue.json", jq_n("{number:7,body:$b,labels:[]}", "--rawfile", "b", str(decided), compact=False) + "\n")
    tr.run("apply_ready")
    ck("decided again: still one", "1", count("^## Decisions made during triage$", file=decided))

    # 14. No decisions: the body is not edited.
    write(T / "issue.json", '{"number":7,"body":"b","labels":[]}')
    write(T / "triage.json", '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"_io":{"visit":"ok1"}}\n')
    write(log, "")
    tr.run("apply_ready")
    ck("no decisions: no body edit", "0", count("decided_body", file=log))

    # 15. The decisions rewrite drops only the old section; text below it is kept.
    write(T / "issue.json", jq_n('{number:7,labels:[],body:"intro\\n## Decisions made during triage\\n\\n- old\\n## Notes\\nkeep me"}', compact=False) + "\n")
    write(T / "triage.json", '{"readiness":"ready","title":"feat: x","labels":[],"questions":[],"decisions":[{"question":"Q","decision":"D","basis":"b"}],"_io":{"visit":"ok1"}}\n')
    tr.run("apply_ready")
    ck("decided: keeps text below", "1", count("^keep me$", file=decided))
    ck("decided: drops the old entry", "0", count("^- old$", file=decided))

    # 16. improve_gate restores an Architecture issue's slug marker (contract C2).
    mkr = "<!-- fabro:arch-candidate slug=room-deck -->"
    improved = T / "improved_body.md"

    def ig_run(body):
        write(T / "issue.json", jq_n('{number:7,labels:[],body:($m + "\\n\\nold body")}', "--arg", "m", mkr, compact=False) + "\n")
        write(T / "improve.json", jq_n('{status:"improved",body:$b,post_deploy_checks:[],_io:{visit:"ok1"}}', "--arg", "b", body, compact=False) + "\n")
        write(T / "improve_attempts", "0\n"); write(log, ""); rm(improved)
        tr.run("improve_gate", cwd=T)

    write(T / "issue_fixture.json", '{"number":7,"title":"t","body":"b","labels":[],"comments":[],"url":"u"}')
    ig_run("new body, marker dropped")
    ck("marker dropped: restored", mkr, head1(improved))
    ck("marker dropped: body kept", "1", count("^new body, marker dropped$", file=improved))
    ig_run(f"top\n{mkr}\nrest")
    ck("marker moved: back on line 1", mkr, head1(improved))
    ck("marker moved: only once", "1", count(mkr, file=improved, flags="F"))
    write(T / "issue.json", jq_n('{number:7,labels:[],body:"a human report"}', compact=False) + "\n")
    write(T / "improve.json", jq_n('{status:"improved",body:"better report",post_deploy_checks:[],_io:{visit:"ok1"}}', compact=False) + "\n")
    write(T / "improve_attempts", "0\n"); rm(improved)
    tr.run("improve_gate", cwd=T)
    ck("no marker: body untouched", "better report", cat(improved))

    # 17. improve_gate writes the Post-deploy checks section from post_deploy_checks, and
    #     only there (gate-relief G3/G4): one heading, a checkbox per item, the text under
    #     the next heading kept, and a malformed or unchanged-with-checks result refused.
    def pd_run(status, body, checks):
        write(T / "issue.json", jq_n('{number:7,labels:[],body:"old"}', compact=False) + "\n")
        write(T / "improve.json", jq_n('{status:$s,title:"t",reason:"r",body:$b,post_deploy_checks:$c,_io:{visit:"ok1"}}',
                                       "--arg", "s", status, "--arg", "b", body, "--argjson", "c", checks, compact=False) + "\n")
        write(T / "improve_attempts", "0\n"); rm(improved)
        return str(tr.run("improve_gate", cwd=T).rc)

    rc = pd_run("improved", "criteria\n\n## Post-deploy checks\n\n- [ ] old item\n\n## Notes\n\nkept", '["Sign in on staging returns 201"]')
    ck("post-deploy: exit 0", "0", rc)
    ck("post-deploy: one heading", "1", count("^## Post-deploy checks$", file=improved))
    ck("post-deploy: a checkbox per item", "1", count("^- [[] []] Sign in on staging returns 201$", file=improved))
    ck("post-deploy: old item replaced", "0", count("old item", file=improved))
    ck("post-deploy: next section kept", "1", count("^kept$", file=improved))
    rc = pd_run("improved", "plain body", "[]")
    ck("no checks: exit 0", "0", rc)
    ck("no checks: no section", "0", count("Post-deploy", file=improved))
    write(T / "improve.json", jq_n('{status:"improved",title:"t",reason:"r",body:"b",_io:{visit:"ok1"}}', compact=False) + "\n")
    write(T / "improve_attempts", "0\n"); rm(improved)
    ck("missing field: first attempt refused", "1", tr.run("improve_gate", cwd=T).rc)
    ck("missing field: body not written", "no", "yes" if improved.is_file() else "no")
    ck("non-string item: refused", "1", pd_run("improved", "b", "[1]"))
    ck("empty item: refused", "1", pd_run("improved", "b", '[""]'))
    ck("item with a newline: refused", "1", pd_run("improved", "b", '["one\\ntwo"]'))
    ck("unchanged with checks: refused", "1", pd_run("unchanged", "b", '["x"]'))
    ck("unchanged, no checks: exit 0", "0", pd_run("unchanged", "b", "[]"))

    # 18. apply_split: the last child carries the parent's Post-deploy checks, the others
    #     do not, and the section ends at the next heading (gate-relief step 6). Its own
    #     stub and directory, so the tests above keep theirs.
    sp = T / "sp"
    (sp / "bin").mkdir(parents=True)
    from harness import extract, rebase
    write(sp / "apply_split.sh", rebase(extract(SHARED_TRIAGE, "apply_split"), sp))
    stub(sp / "bin", "gh", """#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "issue create" ]; then
  N=$(cat "$GH_STATE/next" 2>/dev/null || echo 201); echo $((N + 1)) > "$GH_STATE/next"
  B=""; P=""
  for a in "$@"; do [ "$P" = "--body-file" ] && B="$a"; P="$a"; done
  cp "$B" "$GH_STATE/body-$N.md"
  echo "https://github.com/o/r/issues/$N"; exit 0
fi
if [ "$1" = api ]; then
  case "$*" in
    *"--jq .id"*) echo 9000; exit 0 ;;
    *) echo '[]'; exit 0 ;;
  esac
fi
exit 0
""")
    write(sp / "issue.json", '{"number":7,"title":"Parent","body":"summary\\n\\n## Acceptance criteria\\n\\n- a\\n\\n## Post-deploy checks\\n\\n- [ ] Sign in on staging\\n- [ ] Reload settings\\n\\n## Notes\\n\\nkept\\n","labels":[{"name":"needs-triage"}]}')
    write(sp / "triage.json", '{"title":"feat: parent","labels":[],"decisions":[]}')
    write(sp / "split_children.json", '[{"title":"feat: first slice","summary":"First.","criteria":["c1"],"task_map":"## Proposed task map","after":null},{"title":"feat: second slice","summary":"Second.","criteria":["c2"],"task_map":"## Proposed task map","after":0}]')
    rm_glob(sp, "body-*.md", "next", "gh.log")
    sh(sp / "apply_split.sh", cwd=sp, path=f"{sp / 'bin'}:{tr.path}", env={"GH_LOG": sp / "gh.log", "GH_STATE": sp})
    ck("split: two children created", "2", str(len(list(sp.glob("body-*.md")))))
    ck("split: first child has no checks", "0", count("Post-deploy", file=sp / "body-201.md"))
    ck("split: last child has the heading", "1", count("^## Post-deploy checks$", file=sp / "body-202.md"))
    ck("split: last child has both checks", "2", count("^- [[] []] ", file=sp / "body-202.md"))
    ck("split: section ends at the next heading", "0", count("^kept$", file=sp / "body-202.md"))


# --- plan_gate (ADR 0015 C1/C4) ---
def _tasks(ids):
    return ",".join(f'{{"id":"{i}","title":"{i.upper()}","intent":"i","files":[],"covers":[]}}' for i in ids)


PG3 = '{"tasks":[{"id":"a","title":"A","intent":"i","files":["f"],"covers":["c"]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]}],"children":[],"summary":"s"}'
PG7 = '{"tasks":[{"id":"a1","title":"A1","intent":"i","files":[],"covers":[]},{"id":"a2","title":"A2","intent":"i","files":[],"covers":[]},{"id":"a3","title":"A3","intent":"i","files":[],"covers":[]},{"id":"a4","title":"A4","intent":"i","files":[],"covers":[]},{"id":"b1","title":"B1","intent":"i","files":[],"covers":[]},{"id":"b2","title":"B2","intent":"i","files":[],"covers":[]},{"id":"b3","title":"B3","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): abc","criteria":["c"],"summary":"one","tasks":["a1","a2","a3","a4"],"after":null},{"title":"feat(b): def","criteria":["c"],"summary":"two","tasks":["b1","b2","b3"],"after":0}],"summary":"s"}'
PG5I = '{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"b","title":"B","intent":"i","files":[],"covers":[]},{"id":"c","title":"C","intent":"i","files":[],"covers":[]},{"id":"d","title":"D","intent":"i","files":[],"covers":[]},{"id":"e","title":"E","intent":"i","files":[],"covers":[]}],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","c","e"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["b","d"],"after":0}],"summary":"s"}'
BAD = {
    "bad_dup": '{"tasks":[' + _tasks("abcde") + '],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["b","c","d","e"],"after":0}],"summary":"s"}',
    "bad_5tasks": '{"tasks":[' + _tasks("abcdef") + '],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b","c","d","e"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["f"],"after":0}],"summary":"s"}',
    "bad_after": '{"tasks":[' + _tasks("abcdef") + '],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["c","d","e","f"],"after":4}],"summary":"s"}',
    "bad_title": '{"tasks":[' + _tasks("abcdef") + '],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b","c"],"after":null},{"title":"feat! b","criteria":["c"],"summary":"2","tasks":["d","e","f"],"after":0}],"summary":"s"}',
    "bad_empty": '{"tasks":[' + _tasks("abcdef") + '],"children":[],"summary":"s"}',
    "bad_did": '{"tasks":[{"id":"a","title":"A","intent":"i","files":[],"covers":[]},{"id":"a","title":"B","intent":"i","files":[],"covers":[]}],"children":[],"summary":"s"}',
    "bad_order": '{"tasks":[' + _tasks("abcde") + '],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["c","a"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["b","d","e"],"after":0}],"summary":"s"}',
    "bad_crit": '{"tasks":[' + _tasks("abcde") + '],"children":[{"title":"feat(a): x","criteria":[],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["c","d","e"],"after":0}],"summary":"s"}',
    "bad_missing": '{"tasks":[' + _tasks("abcde") + '],"children":[{"title":"feat(a): x","criteria":["c"],"summary":"1","tasks":["a","b"],"after":null},{"title":"feat(b): y","criteria":["c"],"summary":"2","tasks":["c","d"],"after":0}],"summary":"s"}',
}


def test_triage_plan_gate(ck, tmp_path):
    tr = Triage(ck, tmp_path / "triage")
    T = tr.T
    write(T / "issue.json", '{"number":7,"labels":[]}')

    def plan(doc, attempts="0"):
        write(T / "plan.json", jq('._io.visit="ok1"', doc, args=["-c"], raw=False, pipe=True) + "\n")
        write(T / "plan_attempts", f"{attempts}\n")
        rm(T / "plan_ok", T / "task_map.md", T / "split_children.json")

    status = lambda out: ctx(out, "plan_status")
    children = T / "split_children.json"

    plan(PG3)
    r = tr.run("plan_gate")
    ck("plan map: exit 0", "0", r.rc)
    ck("plan map: status", "map", status(r.out))
    ck("plan map: count", "3", ctx(r.out, "task_count"))
    ck("plan map: plan_ok", "1", "1" if (T / "plan_ok").is_file() else "0")
    ck("plan map: 3 numbered tasks", "3", count("^[0-9]+\\. \\*\\*", file=T / "task_map.md", flags="E"))
    ck("plan map: header", "1", count("Drafted by triage against `main` at `", file=T / "task_map.md"))

    plan(PG7)
    r = tr.run("plan_gate")
    ck("plan split: status", "split", status(r.out))
    ck("plan split: count", "7", ctx(r.out, "task_count"))
    ck("plan split: 2 children", "2", jq("length", file=children, raw=False))
    ck("plan split: child maps", "2", jq('[.[] | select(has("task_map"))] | length', file=children, raw=False))
    ck("plan split: tasks resolved", "2", jq('[.[] | select((.tasks[0].id // "") != "")] | length', file=children, raw=False))
    ck("plan split: criteria carried", "c", jq(".[0].criteria[0]", file=children))

    # C1 rule 3 is a partition, not a contiguous one: children may interleave the tasks as
    # long as each lists its own in `tasks` order.
    plan(PG5I)
    r = tr.run("plan_gate")
    ck("plan interleaved: split", "split", status(r.out))
    ck("plan interleaved: child tasks", "a c e|b d", jq('map([.tasks[].id]|join(" "))|join("|")', file=children))

    plan(jq_n('{tasks:[range(13) | {id:("t" + tostring),title:"T",intent:"i",files:[],covers:[]}],children:[],summary:"s"}'))
    write(T / "triage.json", '{"readiness":"ready","classification":"bug","confidence":"high","title":"feat: x","labels":[],"questions":[],"decisions":[],"summary":"s"}')
    write(T / "triage.md", "old triage.md\n")
    r = tr.run("plan_gate")
    ck("plan too_large: status", "too_large", status(r.out))
    ck("plan too_large: readiness", "needs_info", jq(".readiness", file=T / "triage.json"))
    ck("plan too_large: one question", "1", jq(".questions|length", file=T / "triage.json", raw=False))
    ck("plan too_large: appends map", "1", count("### Proposed task map (too large to split)", file=T / "triage.md"))
    ck("plan too_large: no plan_ok", "0", "1" if (T / "plan_ok").is_file() else "0")
    ck("plan too_large: question_count", "1", ctx(r.out, "question_count"))
    ck("plan too_large: hook question", "1", count("^- This issue decomposes into more than 12", ctx(r.out, "triage_questions"), pipe=True))

    for name, doc in BAD.items():
        plan(doc)
        ck(f"plan invalid {name}: retries", "1", tr.run("plan_gate").rc)

    plan(BAD["bad_empty"], attempts="1")
    r = tr.run("plan_gate")
    ck("plan second invalid: exit 0", "0", r.rc)
    ck("plan second invalid: none", "none", status(r.out))

    # claim deletes the C1 files, resets plan_attempts, and publishes plan_status
    tr.setup("[]")
    write(T / "plan_ok", ""); write(T / "task_map.md", ""); write(T / "plan.json", "x\n")
    write(children, "y\n"); write(T / "plan_attempts", "5\n")
    tr.run("claim")
    ck("claim deletes plan.json", "0", "1" if (T / "plan.json").is_file() else "0")
    ck("claim deletes plan_ok", "0", "1" if (T / "plan_ok").is_file() else "0")
    ck("claim deletes task_map", "0", "1" if (T / "task_map.md").is_file() else "0")
    ck("claim deletes split_children", "0", "1" if children.is_file() else "0")
    ck("claim resets plan_attempts", "0", cat(T / "plan_attempts"))
    ck("claim routing plan_status", "none", status(tr.run("claim").out))

    # done publishes split
    write(T / "triage_outcome", "split\n")
    ck("done: publishes split", "split", ctx(tr.run("done").out, "triage_outcome"))


# --- apply_split (ADR 0015 C2/C3/C4) ---
# The stub answers the sub-issues list in GitHub's real shape: a list of plain issue
# objects, no wrapper key. Fault switches (files in $GH_STATE): sub_fail makes the list read
# fail; link_fail makes the link POST fail; link_dup makes it fail the way a duplicate link
# does, with the child already in the list; create_blank makes `issue create` print no URL.
ASPLIT_GH = """#!/bin/sh
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "api repos/{owner}/{repo}/issues/7/sub_issues" ]; then
  [ -f "$GH_STATE/sub_fail" ] && { echo 'HTTP 502' >&2; exit 1; }
  [ -f "$GH_STATE/sub_seed.json" ] && cat "$GH_STATE/sub_seed.json" || echo '[]'
  exit 0
fi
if [ "$1" = api ] && [ "$2" = "-X" ]; then
  [ -f "$GH_STATE/link_fail" ] && { echo 'HTTP 500' >&2; exit 1; }
  if [ -f "$GH_STATE/link_dup" ]; then
    C=$(cat "$GH_STATE/counter")
    S=$(cat "$GH_STATE/sub_seed.json" 2>/dev/null || echo '[]')
    printf '%s' "$S" | jq -c --argjson c "$C" '. + [{number:$c,title:"linked"}]' > "$GH_STATE/sub_seed.json"
    echo 'HTTP 422: Issue may not contain duplicate sub-issues' >&2; exit 1
  fi
  echo '{}'; exit 0
fi
if [ "$1" = api ]; then
  echo '123456'; exit 0
fi
if [ "$1 $2" = "issue create" ]; then
  [ -f "$GH_STATE/create_blank" ] && exit 0
  C=$(cat "$GH_STATE/counter" 2>/dev/null || echo 100); C=$((C+1)); echo $C > "$GH_STATE/counter"
  BF=""; p=""
  for a in "$@"; do [ "$p" = "--body-file" ] && BF="$a"; p="$a"; done
  [ -n "$BF" ] && cp "$BF" "$GH_STATE/child_${C}.md"
  echo "https://github.com/o/r/issues/$C"
  exit 0
fi
exit 0
"""


def test_triage_apply_split(ck, tmp_path):
    AS = tmp_path / "asplit"
    (AS / "bin").mkdir(parents=True)
    gate = stage(ck, "apply_split", AS, graph=SHARED_TRIAGE)
    stub(AS / "bin", "gh", ASPLIT_GH)
    path = f"{AS / 'bin'}:{ORIG_PATH}"
    env = {"GH_LOG": AS / "gh.log", "GH_STATE": AS}
    log = AS / "gh.log"
    go = lambda: sh(gate, path=path, env=env)

    write(AS / "issue.json", '{"number":7,"title":"Parent","labels":[{"name":"needs-triage"},{"name":"architecture"},{"name":"agent"},{"name":"agent-noop"}],"body":"parent body"}')
    write(AS / "triage.json", '{"readiness":"ready","title":"feat: parent","labels":["area:web"],"questions":[],"decisions":[{"question":"Where?","decision":"Here.","basis":"CONTEXT.md"}],"summary":"s"}')
    write(AS / "triage.md", "report body\n")
    write(AS / "task_map.md", "## Proposed task map\n\nDrafted by triage against `main`.\n")
    write(AS / "split_children.json", '[{"title":"feat(one): one","summary":"does one","criteria":["Users can do one."],"after":null,"task_map":"## Proposed task map\\n\\n1. **A1** (`a1`)\\n","tasks":[{"id":"a1","title":"A1","intent":"i","files":[],"covers":["c1"]}]},{"title":"feat(two): two","summary":"does two","criteria":["Users can do two."],"after":0,"task_map":"## Proposed task map\\n\\n1. **B1** (`b1`)\\n","tasks":[{"id":"b1","title":"B1","intent":"i","files":[],"covers":[]}]},{"title":"feat(three): three","summary":"does three","criteria":["Users can do three."],"after":1,"task_map":"## Proposed task map\\n\\n1. **C1** (`c1`)\\n","tasks":[{"id":"c1","title":"C1","intent":"i","files":[],"covers":[]}]}]\n')

    def reset():
        rm_glob(AS, "counter", "triage_outcome", "sub_seed.json", "sub_fail", "link_fail",
                "link_dup", "create_blank", "child_*.md")
        write(log, "")

    creates = lambda: [l for l in grep("^issue create", file=log).split("\n") if l]
    sel = lambda ls: "\n".join(ls)
    nonzero = lambda rc: "1" if rc != 0 else "0"

    reset()
    r = go()
    c = creates()
    ck("asplit: exit 0", "0", r.rc)
    ck("asplit: outcome", "split", cat(AS / "triage_outcome"))
    ck("asplit: 3 creates", "3", count("^issue create", file=log))
    ck("asplit: first agent", "1", count("--label agent ", sel(c[0:1]), pipe=True))
    ck("asplit: later children held", "2", count("--label agent-held", sel(c[1:3]), pipe=True))
    ck("asplit: architecture inherited", "3", count("--label=architecture", sel(c), pipe=True))
    ck("asplit: triage labels inherited", "3", count("--label=area:web", sel(c), pipe=True))
    ck("asplit: triage label created", "1", count("^label create area:web", file=log))
    ck("asplit: agent never inherited", "0", count("--label=agent( |$)", sel(c), flags="E", pipe=True))
    ck("asplit: agent-noop never inherited", "0", count("--label=agent-noop", sel(c), pipe=True))
    ck("asplit: needs-triage dropped", "0", count("needs-triage", sel(c), pipe=True))
    ck("asplit: 3 sub-issue links", "3", count("sub_issues -F sub_issue_id", file=log))
    ck("asplit: parent agent-split", "1", count("issue edit 7 --title feat: parent --add-label agent-split", file=log))
    ck("asplit: parent never agent", "0", count("add-label agent ", grep("issue edit 7", file=log), pipe=True))
    ck("asplit: parent agent removed", "1", count("--remove-label=agent", grep("issue edit 7 --title", file=log), pipe=True))

    ck("asplit: marker child1 after=0", "<!-- fabro:split-child parent=7 after=0 -->", head1(AS / "child_101.md"))
    ck("asplit: marker child2", "<!-- fabro:split-child parent=7 after=101 -->", head1(AS / "child_102.md"))
    ck("asplit: marker child3", "<!-- fabro:split-child parent=7 after=102 -->", head1(AS / "child_103.md"))
    ck("asplit: child summary 1st prose", "does two", (cat(AS / "child_102.md").split("\n") + ["", "", ""])[2])
    ck("asplit: child criteria", "1", count("^- Users can do two[.]$", file=AS / "child_102.md"))
    ck("asplit: child criteria heading", "1", count("^## Acceptance criteria$", file=AS / "child_102.md"))
    ck("asplit: split into checklist", "1", count("^## Split into", file=AS / "split_parent_body.md"))
    ck("asplit: split decision line", "1", count("\\*\\*Split this issue?\\*\\* Into #101, #102, #103 (basis: task map of 3 tasks", file=AS / "split_parent_body.md"))

    # Idempotent re-run: the sub-issues list (real shape) already holds two children.
    reset()
    write(AS / "sub_seed.json", '[{"number":101,"title":"feat(one): one","state":"open"},{"number":102,"title":"feat(two): two","state":"open"}]\n')
    write(AS / "counter", "102\n")
    go()
    ck("asplit idempotent: 1 create", "1", count("^issue create", file=log))
    ck("asplit idempotent: 1 link", "1", count("sub_issues -F sub_issue_id", file=log))
    ck("asplit idempotent: real pred", "<!-- fabro:split-child parent=7 after=102 -->", head1(AS / "child_103.md"))

    # An unreadable sub-issues list fails closed: it is never read as "no children".
    reset(); write(AS / "sub_fail", "")
    r = go()
    ck("asplit read fails: nonzero", "1", nonzero(r.rc))
    ck("asplit read fails: no create", "0", count("^issue create", file=log))
    ck("asplit read fails: no outcome", "0", "1" if (AS / "triage_outcome").is_file() else "0")

    # A link that fails and is not in the list stops the node after that child.
    reset(); write(AS / "link_fail", "")
    r = go()
    ck("asplit link fails: nonzero", "1", nonzero(r.rc))
    ck("asplit link fails: 1 create", "1", count("^issue create", file=log))
    ck("asplit link fails: no outcome", "0", "1" if (AS / "triage_outcome").is_file() else "0")

    # A link rejected as a duplicate, with the child already in the list, is success.
    reset(); write(AS / "link_dup", "")
    r = go()
    ck("asplit link dup: exit 0", "0", r.rc)
    ck("asplit link dup: outcome", "split", cat(AS / "triage_outcome"))

    # A create that yields no issue number stops the node; no child is held behind after=0.
    reset(); write(AS / "create_blank", "")
    r = go()
    ck("asplit no number: nonzero", "1", nonzero(r.rc))
    ck("asplit no number: 1 create", "1", count("^issue create", file=log))
    ck("asplit no number: no link", "0", count("sub_issues -F sub_issue_id", file=log))

    # fast-path never reaches a child (ADR 0023 D1); the other labels still do.
    reset()
    write(AS / "issue.json", '{"number":7,"title":"Parent","labels":[{"name":"needs-triage"},{"name":"agent"},{"name":"fast-path"},{"name":"area:keep"}],"body":"parent body"}')
    go()
    c = creates()
    ck("asplit fast-path parent: 3 creates", "3", count("^issue create", file=log))
    ck("asplit fast-path parent: no child carries fast-path", "0", count("fast-path", sel(c), pipe=True))
    ck("asplit fast-path parent: other labels kept", "3", count("--label=area:keep", sel(c), pipe=True))
