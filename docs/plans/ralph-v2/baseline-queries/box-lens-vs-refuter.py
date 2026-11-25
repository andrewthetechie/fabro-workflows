import json,re,collections as C,statistics as st,sys
R=[json.loads(l) for l in open(sys.argv[1])]
IT=lambda s:s['node'].startswith('iterate_t')
def files(f): return set(re.sub(r'^/workspace/[^/]+/','',x) for x in ([f.get('file')] if f.get('file') else (f.get('files') or [])) if x)
# Did the box lenses, given the whole branch, touch the same files the Refuter then blocked on?
hit=miss=0; both=C.Counter(); runs=C.Counter()
for r in R:
    loc=set(); ref1=set(); seen=False; nl=0; nr=0
    for s in r['stages']:
        if s['node'] in('local_standards','local_spec'):
            for f in s.get('fs') or []:
                if f['kind']=='error': loc|=files(f); nl+=1
        if s['node']=='refute' and not seen:
            seen=True
            for f in s.get('fs') or []:
                if f['kind'] in('defect','not_met'):
                    nr+=1; ref1|=files(f)
    runs[(nl>0,nr>0)]+=1
print("runs by (box lens found error, Refuter round 1 found blocker):",dict(runs))
n=sum(runs.values()); 
a=runs[(True,True)]; b=runs[(True,False)]; c=runs[(False,True)]; d=runs[(False,False)]
print("P(refuter fails | box found) = %.2f  P(refuter fails | box clean) = %.2f"%(a/(a+b),c/(c+d)))
# Iteration edits per finding Task etc; rework round-trip: what fraction of finding tasks edit only files from own source Task
pm=[max((int(s['id'].split('@')[1]) for s in r['stages'] if s['node']=='pick'),default=0) for r in R]
print('pick visits per run: max',max(pm),'p50',st.median(pm))
# all tool findings incl warnings from box lenses
