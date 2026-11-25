import json,re,collections as C,statistics as st,sys
R=[json.loads(l) for l in open(sys.argv[1])]
PATH=re.compile(r'([A-Za-z0-9_./\-\[\]$@]+\.[A-Za-z0-9]+)(?::\d+)?')
IT=lambda s:s['node'].startswith('iterate_t')
def med(v): return round(st.median(v),1) if v else None
# --- Task level
itper=C.Counter(); n_task=0; first=C.Counter(); ntask_by_src=C.Counter(); passfirst=C.Counter()
miss=C.Counter(); 
cats=C.defaultdict(C.Counter); bynode=C.defaultdict(C.Counter); roundcat=C.defaultdict(C.Counter); ex=[]
plans=[]; ntasks_run=[]; runs_one_task=0
for r in R:
    tasks={}; 
    edits=[] # (order idx, task, src, files)
    seen=[]; idx=0
    prev_ref=None
    S=r['stages']
    per=C.defaultdict(list)
    for i,s in enumerate(S):
        if IT(s) and s.get('task'):
            # find following record
            ok=None; val=None
            for t in S[i+1:]:
                if t['node']=='check' and val is None: val=t.get('ctx',{}).get('task_validation_ok') if t.get('st')=='succeeded' else 'failed'
                if t['node']=='record': ok=t.get('ctx',{}).get('iteration_passed'); break
                if IT(t): break
            per[s['task']].append((s,ok,val)); 
            edits.append((i,s['task'],s.get('src','plan'),set((s.get('files') or {}).keys())))
    p=[t for t in per if per[t][0][0].get('src','plan')=='plan']
    ntasks_run.append(len(p)); 
    for t in per:
        src=per[t][0][0].get('src','plan'); ntask_by_src[src]+=1
        # dedupe attempts of same stage id
        vis=C.OrderedDict()
        for s,ok,val in per[t]: vis[s['id']]=(s,ok,val)
        itper[(src,len(vis))]+=1
        if list(vis.values())[0][1] is True: passfirst[src]+=1
        for s,ok,val in vis.values():
            if ok is not True:
                sub=(s.get('submit') or {}).get('status')
                miss[(src,'blocked' if (s.get('submit') or {}).get('blocked') else 'validation-red' if val is False else 'progress' if sub=='progress' else 'no-submit/timeout' if sub is None else 'check-failed' if val=='failed' else 'other:%s/%s'%(sub,val))]+=1
    # findings
    rv=0
    for i,s in enumerate(S):
        if s['node'] not in('refute','standards','spec','local_standards','local_spec'): continue
        if s['node']=='refute': rv+=1
        for f in s.get('fs') or []:
            blk = f['kind'] in ('not_met','defect','error')
            if not blk: continue
            fl=set()
            if f.get('file'): fl.add(re.sub(r'^/workspace/[^/]+/','',f['file']))
            for x in f.get('files') or []:
                if x: fl.add(re.sub(r'^/workspace/[^/]+/','',x))
            if not fl:
                for m in PATH.findall(f.get('ev') or ''): 
                    if '/' in m: fl.add(m.lstrip('./'))
            before=[e for e in edits if e[0]<i]
            touched=C.defaultdict(set)
            for _,t,src,fs in before:
                for x in fl:
                    if x in fs: touched[x].add((t,src))
            allT=set().union(*touched.values()) if touched else set()
            planT={t for t,src in allT if src=='plan'}; findT={t for t,src in allT if src!='plan'}
            if not fl: cat='no-file'
            elif not allT: cat='no Task edited cited file'
            elif len(planT)>=2: cat='2+ plan Tasks'
            elif len(planT)==1 and not findT: cat='1 plan Task'
            elif len(planT)==1: cat='1 plan Task + finding Task(s)'
            else: cat='finding Task(s) only'
            if len(p)==1 and cat.startswith('1 plan'): cat+=' (single-Task run)'
            bynode[s['node']][cat]+=1
            if s['node']=='refute': roundcat[min(rv,3)][f['kind']]+=1; cats[min(rv,3)][cat]+=1
            ex.append((r['run'],s['id'],f['kind'],cat,sorted(fl)[:3],(f['text'] or '')[:180]))
print("plan Tasks worked per run:",sorted(C.Counter(ntasks_run).items()))
print("Tasks by source",dict(ntask_by_src))
print("passed on FIRST iteration by source",{k:f"{passfirst[k]}/{ntask_by_src[k]}" for k in ntask_by_src})
print("iterations per Task by source",{src:sorted((n,c) for (s,n),c in itper.items() if s==src) for src in ntask_by_src})
print("miss reasons",miss.most_common())
print("--- blocking findings by stage x Task attribution")
for n in bynode: print(n,sum(bynode[n].values()),bynode[n].most_common())
print("--- refute by round: kind / attribution")
for k in sorted(roundcat): print(k,dict(roundcat[k]),cats[k].most_common())
json.dump(ex,open(sys.argv[2],'w'))
