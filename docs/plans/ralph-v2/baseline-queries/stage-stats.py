import sqlite3, json, statistics as st
c=sqlite3.connect('file:/data/scheduler.db?mode=ro',uri=True)
c.row_factory=sqlite3.Row
W="2026-11-09"
runs=c.execute("select * from run_history where workflow='ralph' and finished_at>=? and target_kind='issue'",(W,)).fetchall()
print("ralph runs",len(runs))
ids=[r['run_id'] for r in runs]
q=",".join("?"*len(ids))
def pct(v,p):
    v=sorted(v); 
    return v[min(len(v)-1,int(len(v)*p))] if v else None
rows=c.execute(f"select * from run_stage_stats where run_id in ({q})",ids).fetchall()
by={}
for r in rows:
    by.setdefault(r['node_id'],[]).append(r)
print("node | runs | visits/run avg | wall per visit p50/p90 min | box_ms share | timeouts | total box-h")
out=[]
for n,rs in by.items():
    vis=sum(r['visits'] for r in rs); wall=sum(r['wall_ms'] for r in rs); box=sum(r['box_ms'] for r in rs)
    pv=[r['wall_ms']/r['visits']/60000 for r in rs if r['visits']]
    out.append((box,n,len(rs),vis/len(rs),pct(pv,.5),pct(pv,.9),box/max(wall,1),sum(r['timeouts'] for r in rs)))
for box,n,k,v,p5,p9,bs,to in sorted(out,reverse=True)[:30]:
    print(f"{n:24s} runs={k:3d} v/run={v:4.1f} p50={p5:5.1f} p90={p9:5.1f} boxshare={bs:.2f} to={to:3d} box-h={box/3.6e6:6.1f}")
# per-run box hours
tot={}
for r in rows: tot[r['run_id']]=tot.get(r['run_id'],0)+r['box_ms']
v=[x/3.6e6 for x in tot.values()]
print("box-h/run p50 %.2f p90 %.2f mean %.2f n=%d"%(pct(v,.5),pct(v,.9),sum(v)/len(v),len(v)))
# lease hours
lh=[(datetime:=None)]
