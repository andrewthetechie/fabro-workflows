# CAUTION: averages the 2026-11-13..16 box incident. Read by day (README.md).
import sqlite3, collections as C
c=sqlite3.connect('file:/data/scheduler.db?mode=ro',uri=True); c.row_factory=sqlite3.Row
W="2026-11-09"
ids=[r[0] for r in c.execute("select run_id from run_history where workflow='ralph' and finished_at>=? and target_kind='issue'",(W,))]
q=",".join("?"*len(ids))
vis=C.Counter(); fo=C.Counter(); to=C.Counter(); fl=C.defaultdict(C.Counter)
for r in c.execute(f"select * from run_stage_stats where run_id in ({q})",ids):
    vis[r['node_id']]+=r['visits']; to[r['node_id']]+=r['timeouts']
    import json
    for k,v in json.loads(r['failures']).items(): fl[r['node_id']][k]+=v
for r in c.execute(f"select * from run_failovers where run_id in ({q}) and from_model like 'box%'",ids):
    fo[r['node_id']]+=r['count']
print("node visits box-failover/visit timeouts/visit failures")
for n in ('iterate_t1','plan','plan_review','local_standards','local_spec','learn'):
    print(f"{n:16s} visits={vis[n]:4d} failovers={fo[n]:4d} ({fo[n]/vis[n]:.0%}) timeouts={to[n]:3d} ({to[n]/vis[n]:.1%}) fail={dict(fl[n])}")
