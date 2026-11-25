import json,re,collections as C,statistics as st,sys
from datetime import datetime
R=[json.loads(l) for l in open(sys.argv[1])]
T=lambda x: datetime.fromisoformat(x[:19]).timestamp()
IT=lambda s:s['node'].startswith('iterate_t')
PATH=re.compile(r'([A-Za-z0-9_./\-\[\]$@]+/[A-Za-z0-9_./\-\[\]$@]+\.[A-Za-z0-9]+)')
rows=[]
for r in R:
    plan={}
    for s in r['stages']:
        if s['node']=='plan' and s.get('plan'): plan={t['id']:t for t in s['plan']}
    ed=C.OrderedDict(); dur=C.Counter(); nits=C.Counter(); edits=C.Counter(); pos={}
    for i,s in enumerate(r['stages']):
        if IT(s) and s.get('task'):
            t=(s['task'],s.get('src','plan')); ed.setdefault(t,set()).update((s.get('files') or {}).keys()); edits[t]+=sum((s.get('files') or {}).values())
            if s.get('end'): dur[t]+=(T(s['end'])-T(s['start']))/60
            nits[t]+=1
    hit=C.Counter()
    for i,s in enumerate(r['stages']):
        if s['node'] not in('refute','standards','spec','local_standards','local_spec'): continue
        for f in s.get('fs') or []:
            if f['kind'] not in('not_met','defect','error'): continue
            fl=set(re.sub(r'^/workspace/[^/]+/','',x) for x in ([f.get('file')] if f.get('file') else (f.get('files') or PATH.findall(f.get('ev') or ''))) if x)
            # blame the LAST task that edited a cited file before this stage
            last=None
            for j in range(i-1,-1,-1):
                q=r['stages'][j]
                if IT(q) and q.get('task') and fl & set((q.get('files') or {}).keys()): last=(q['task'],q.get('src','plan')); break
            if last: hit[(last, 'ref' if s['node']=='refute' else 'rev')]+=1
    order=[t for t in ed if t[1]=='plan']
    for k,t in enumerate(order):
        p=plan.get(t[0],{})
        rows.append(dict(src='plan',nacc=p.get('nacc'),nfiles=len(ed[t]),dur=dur[t],ed=edits[t],ref=hit[(t,'ref')],rev=hit[(t,'rev')],last=k==len(order)-1,only=len(order)==1,repo=(r['repo'] or '').split('/')[-1],ctx=p.get('ctx') or 0))
    for t in ed:
        if t[1] in('refute','review','local_review'): rows.append(dict(src='finding',nacc=1,nfiles=len(ed[t]),dur=dur[t],ed=edits[t],ref=hit[(t,'ref')],rev=hit[(t,'rev')],last=False,only=False,repo='',ctx=0))
def tab(name,key,bands,src='plan'):
    print('--',name,src)
    for lo,hi in bands:
        v=[x for x in rows if x['src']==src and x[key] is not None and lo<=x[key]<hi]
        if v: print(f"   [{lo},{hi}) n={len(v):3d} ({len(v)/len([x for x in rows if x['src']==src]):.0%}) refuter-hit={sum(x['ref']>0 for x in v)/len(v):.0%} any-hit={sum(x['ref']+x['rev']>0 for x in v)/len(v):.0%} share of refuter hits={sum(x['ref']>0 for x in v)/max(1,sum(x['ref']>0 for x in rows if x['src']==src)):.0%}")
tab('acceptance statements','nacc',[(0,2),(2,4),(4,6),(6,9),(9,99)])
tab('files edited','nfiles',[(0,2),(2,3),(3,5),(5,8),(8,99)])
tab('edit calls','ed',[(0,5),(5,10),(10,20),(20,40),(40,999)])
tab('duration min','dur',[(0,10),(10,20),(20,40),(40,999)])
v=[x for x in rows if x['src']=='plan']; f=[x for x in rows if x['src']=='finding']
print('ALL plan Tasks n',len(v),'refuter-hit',sum(x['ref']>0 for x in v),'any-hit',sum(x['ref']+x['rev']>0 for x in v),'| finding Tasks n',len(f),'refuter-hit',sum(x['ref']>0 for x in f),'any-hit',sum(x['ref']+x['rev']>0 for x in f))
print('last plan Task hit rate',sum(x['ref']>0 for x in v if x['last'])/sum(x['last'] for x in v),'earlier',sum(x['ref']>0 for x in v if not x['last'])/sum(not x['last'] for x in v))
print('nacc p50',st.median(x['nacc'] for x in v if x['nacc']),'ctx p50',st.median(x['ctx'] for x in v),'files p50',st.median(x['nfiles'] for x in v))
# refuter usage
u=[]
for r in R:
    for s in r['stages']:
        if s['node']=='refute' and s.get('usage'): 
            t=s['usage']['usage']['tokens']; u.append((s['model'],t['input'],t['cache_read'],t['output']+t['reasoning'],(T(s['end'])-T(s['start']))/60))
print('refute models',C.Counter(x[0] for x in u))
L=[x for x in u if 'luna' in x[0]]
cost=[x[1]*.1e-6+x[2]*.01e-6+x[3]*.5e-6 for x in L]
print('luna visits',len(L),'cost p50 $%.4f p90 $%.4f total $%.2f'%(st.median(cost),sorted(cost)[int(.9*len(cost))],sum(cost)),'min p50 %.1f p90 %.1f'%(st.median(x[4] for x in L),sorted(x[4] for x in L)[int(.9*len(L))]))
m=C.Counter(); 
for r in R:
    for s in r['stages']:
        if s['node'] in('local_standards','local_spec','plan_review','iterate_t1'): m[(s['node'],(s.get('model') or 'none').split('/')[0])]+=1
print(sorted(m.items()))
