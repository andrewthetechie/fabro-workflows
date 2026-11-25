import sqlite3, json, collections as C, statistics as st
from datetime import datetime
c=sqlite3.connect('file:/data/scheduler.db?mode=ro',uri=True); c.row_factory=sqlite3.Row
W="2026-11-09"
runs=c.execute("select * from run_history where workflow='ralph' and finished_at>=? and target_kind='issue'",(W,)).fetchall()
stats=C.defaultdict(dict)
for r in c.execute("select * from run_stage_stats"): stats[r['run_id']][r['node_id']]=r
def ts(x): return datetime.fromisoformat(x)
by=C.defaultdict(list)
for r in runs:
    s=stats.get(r['run_id'],{})
    rv=s.get('refute',{}) and s['refute']['visits'] or 0
    lease=(ts(r['finished_at'])-ts(r['dispatched_at'])).total_seconds()/3600
    box=sum(x['box_ms'] for x in s.values())/3.6e6
    it=s.get('iterate_t1'); itv=it['visits'] if it else 0
    itw=it['wall_ms']/6e4 if it else 0
    by[min(rv,3)].append((lease,box,itv,itw,r['merged']==1 or r['merged'] is None and 0))
for k,v in sorted(by.items()):
    print("refute visits",k,"n",len(v),"lease_h mean %.2f med %.2f | stage box-h mean %.2f | iterates %.1f | iterate wall min %.0f"%(st.mean(x[0] for x in v),st.median(x[0] for x in v),st.mean(x[1] for x in v),st.mean(x[2] for x in v),st.mean(x[3] for x in v)))
# lease hours total and per-node wall (all, not just box) for ralph
tot=C.Counter(); 
for r in runs:
    for n,x in stats.get(r['run_id'],{}).items(): tot[n]+=x['wall_ms']
T=sum(tot.values())
print("total stage wall h",T/3.6e6, "lease h", sum((ts(r['finished_at'])-ts(r['dispatched_at'])).total_seconds() for r in runs)/3600)
for n,w in tot.most_common(12): print(n, round(w/3.6e6,1))
# iterate wall for finding-sourced iterations: approximate share = finding tasks/all tasks
