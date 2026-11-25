import json,re,collections as C,statistics as st,sys
from datetime import datetime
R=[json.loads(l) for l in open(sys.argv[1])]
IT=lambda s:s['node'].startswith('iterate_t')
def T(x): return datetime.fromisoformat(x[:19]).timestamp()
PATH=re.compile(r'([A-Za-z0-9_./\-\[\]$@]+/[A-Za-z0-9_./\-\[\]$@]+\.[A-Za-z0-9]+)')
orig=C.Counter(); same=C.Counter()
mins=C.Counter(); nruns=len(R)
roundruns=C.Counter(); findpos=[]
shape=C.Counter(); cost=C.defaultdict(list)
for r in R:
    S=r['stages']; lastref=-1; rv=0; seenfiles=set()
    for i,s in enumerate(S):
        if s.get('end'): mins[(('iterate:'+('plan' if s.get('src','plan')=='plan' else 'finding')) if IT(s) else s['node'])]+=(T(s['end'])-T(s['start']))/60
        if s['node']!='refute': continue
        rv+=1
        for f in s.get('fs') or []:
            if f['kind'] not in('not_met','defect'): continue
            fl=set()
            if f.get('file'): fl.add(re.sub(r'^/workspace/[^/]+/','',f['file']))
            else: fl|=set(x.lstrip('./') for x in PATH.findall(f.get('ev') or ''))
            if rv>1:
                ed=set()
                for t in S[lastref:i]:
                    if IT(t): ed|=set((t.get('files') or {}).keys())
                k='file edited since last refute (fix/regression zone)' if fl&ed else 'file NOT edited since last refute (missed earlier)'
                orig[(rv,k)]+=1
                same[(rv,'same file as an earlier-round finding' if fl&seenfiles else 'new file')]+=1
        for f in s.get('fs') or []:
            if f['kind'] in('not_met','defect') and f.get('file'): seenfiles.add(f['file'])
        lastref=i
for k,v in sorted(orig.items()): print(k,v)
for k,v in sorted(same.items()): print(k,v)
print("minutes/run by stage (133 runs)")
for k,v in mins.most_common(16): print(f"  {k:22s} {v/nruns:6.1f}")
# what did each run fail on first? counts of runs by blocking outcome at each stage, first round
