import sqlite3, json, collections as C, statistics as st
from datetime import datetime
c=sqlite3.connect('file:/data/scheduler.db?mode=ro',uri=True); c.row_factory=sqlite3.Row
W="2026-11-09"
runs=c.execute("""select h.*,s.task_sources ts,p.additions a,p.deletions d from run_history h left join run_summary s using(run_id) left join pr_facts p on p.repo=h.repo and p.pr_number=h.pr_number where h.workflow='ralph' and h.finished_at>=? and h.target_kind='issue'""",(W,)).fetchall()
stats=C.defaultdict(dict)
for r in c.execute("select * from run_stage_stats"): stats[r['run_id']][r['node_id']]=r
ts=lambda x: datetime.fromisoformat(x)
rows=[]
for r in runs:
    s=stats.get(r['run_id'],{}); rv=s['refute']['visits'] if 'refute' in s else 0
    t=json.loads(r['ts']) if r['ts'] else {}
    rows.append(dict(repo=r['repo'].split('/')[1],rv=rv,plan=t.get('plan',0),fnd=sum(v for k,v in t.items() if k!='plan'),lease=(ts(r['finished_at'])-ts(r['dispatched_at'])).total_seconds()/3600,size=(r['a'] or 0)+(r['d'] or 0),merged=r['merged']==1))
pr=[x for x in rows if x['rv']>0]
print("by repo: n, mean rv, share rv=3, mean lease, merged%")
for repo in sorted(set(x['repo'] for x in pr)):
    v=[x for x in pr if x['repo']==repo]
    print(f"{repo:22s} n={len(v):3d} rv={st.mean(x['rv'] for x in v):.2f} rv3={sum(x['rv']==3 for x in v)/len(v):.2f} lease={st.mean(x['lease'] for x in v):.2f} merged={sum(x['merged'] for x in v)/len(v):.2f} plan_tasks={st.mean(x['plan'] for x in v):.1f} size_p50={st.median(x['size'] for x in v)}")
print("by plan-task count")
for lo,hi in ((0,2),(3,3),(4,5),(6,8)):
    v=[x for x in pr if lo<=x['plan']<=hi]
    if v: print(lo,hi,len(v),"rv mean %.2f rv3 %.2f lease %.2f merged %.2f"%(st.mean(x['rv'] for x in v),sum(x['rv']==3 for x in v)/len(v),st.mean(x['lease'] for x in v),sum(x['merged'] for x in v)/len(v)))
print("by pr size")
for lo,hi in ((0,300),(300,800),(800,1500),(1500,10**9)):
    v=[x for x in pr if lo<=x['size']<hi]
    if v: print(lo,hi,len(v),"rv mean %.2f rv3 %.2f lease %.2f"%(st.mean(x['rv'] for x in v),sum(x['rv']==3 for x in v)/len(v),st.mean(x['lease'] for x in v)))
