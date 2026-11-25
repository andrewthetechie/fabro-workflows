import json,re,collections as C,statistics as st,sys
from datetime import datetime
R=[json.loads(l) for l in open(sys.argv[1])]
T=lambda x: datetime.fromisoformat(x[:19]).timestamp()
IT=lambda s:s['node'].startswith('iterate_t')
nf=C.Counter(); src=C.Counter(); cnt=C.Counter(); dur=C.defaultdict(list)
seq=C.Counter()
lat=[]
for r in R:
    rv=0; pat=[]
    for s in r['stages']:
        if IT(s) and s.get('end'):
            k=s.get('src','plan'); m=(T(s['end'])-T(s['start']))/60; src[k]+=m; cnt[k]+=1; dur[k].append(m)
        if s['node']=='refute':
            rv+=1; b=[f for f in s.get('fs') or [] if f['kind'] in('not_met','defect')]
            nf[(rv,len(b))]+=1
    seq[rv]+=1
print("blocking findings per refute visit (visit,count)->visits",sorted(nf.items()))
print("iteration minutes by source: total/run, visits, p50")
for k,v in src.most_common(): print(f"  {k:12s} {v/len(R):5.1f} min/run  n={cnt[k]} p50={st.median(dur[k]):.1f} p90={sorted(dur[k])[int(.9*len(dur[k]))]:.1f}")
print(seq)
# multi-visit runs: which gate caused the return?
why=C.Counter()
for r in R:
    S=r['stages']; refs=[i for i,s in enumerate(S) if s['node']=='refute']
    for a,b in zip(refs,refs[1:]):
        rf=[f for f in S[a].get('fs') or [] if f['kind'] in('not_met','defect')]
        sv=[f for s in S[a:b] if s['node'] in('standards','spec') for f in s.get('fs') or [] if f['kind']=='error']
        why[('refute' if rf else '')+('+' if rf and sv else '')+('review' if sv else '') or 'other']+=1
print("what forced a further Refute round:",why)
# cost between consecutive refute visits
g=[]
for r in R:
    S=r['stages']; refs=[i for i,s in enumerate(S) if s['node']=='refute']
    for a,b in zip(refs,refs[1:]): g.append((T(S[b]['start'])-T(S[a]['end']))/60)
g.sort(); print("minutes from one Refute visit to the next: n=%d p50=%.0f p90=%.0f mean=%.0f"%(len(g),g[len(g)//2],g[int(len(g)*.9)],st.mean(g)))
