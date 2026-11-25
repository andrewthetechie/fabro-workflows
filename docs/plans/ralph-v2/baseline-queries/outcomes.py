import sqlite3, json, collections as C, statistics as st
c=sqlite3.connect('file:/data/scheduler.db?mode=ro',uri=True); c.row_factory=sqlite3.Row
W="2026-11-09"
rows=c.execute("""select h.run_id,h.repo,h.merged,h.pr_number,h.dispatched_at,h.finished_at,s.terminal_node,s.refute,s.task_sources ts,s.post_iterations,s.loop_exhausted,
 p.state,p.report,p.reason,p.additions,p.deletions,p.operator_commits
 from run_history h left join run_summary s using(run_id) left join pr_facts p on p.repo=h.repo and p.pr_number=h.pr_number
 where h.workflow='ralph' and h.finished_at>=? and h.target_kind='issue'""",(W,)).fetchall()
# iterations per run
it={r['run_id']:r['visits'] for r in c.execute("select run_id,visits from run_stage_stats where node_id='iterate_t1'")}
rf={r['run_id']:r['visits'] for r in c.execute("select run_id,visits from run_stage_stats where node_id='refute'")}
def cls(r):
    if r['merged']==1: return 'merged'
    if r['pr_number'] is None: return 'nopr:'+str(r['terminal_node'])
    return 'held:'+str(r['report'])
print(C.Counter(cls(r) for r in rows).most_common())
print("--- held reasons")
for r in rows:
    if r['pr_number'] and r['merged']!=1: pass
print(C.Counter((r['report'],(r['reason'] or '')[:90]) for r in rows if r['pr_number'] and r['merged']!=1).most_common(25))
print("--- task sources per outcome")
agg=C.defaultdict(lambda: C.Counter()); n=C.Counter()
for r in rows:
    k='merged' if r['merged']==1 else ('nopr' if r['pr_number'] is None else 'held')
    n[k]+=1
    if r['ts']:
        for s,v in json.loads(r['ts']).items(): agg[k][s]+=v
for k in agg: print(k,n[k],{s:round(v/n[k],2) for s,v in agg[k].items()})
print("--- iterations/run by outcome (iterate_t1 visits)")
for k in ('merged','held','nopr'):
    v=[it[r['run_id']] for r in rows if r['run_id'] in it and (('merged' if r['merged']==1 else ('nopr' if r['pr_number'] is None else 'held'))==k)]
    v.sort(); print(k,len(v),'p50',v[len(v)//2],'p90',v[int(len(v)*.9)],'mean',round(sum(v)/len(v),2))
print("--- refute visits distribution", C.Counter(rf.values()))
