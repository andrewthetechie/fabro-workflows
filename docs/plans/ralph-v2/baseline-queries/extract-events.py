# runs on the fabro host: reads run ids on stdin, streams events, prints one compact JSON per run
import sys, json, re, subprocess
IT=("iterate_t1","iterate_t2","iterate_t3","iterate_t4")
REV=("refute","standards","spec","local_standards","local_spec","plan_review")
def rel(p):
    return re.sub(r'^/workspace/[^/]+/','',p or '')
for rid in sys.stdin.read().split():
    out=dict(run=rid,stages=[],tasks=[],findings=[],gates=[])
    pr=subprocess.Popen("cd ~/fabro && docker compose exec -T fabro fabro events %s --json"%rid,shell=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL)
    cur={}; n=0
    for l in pr.stdout:
        n+=1
        # cheap prefilter
        if not any(k in l for k in (b'"agent.tool.started"',b'"agent.tool.completed"',b'"stage.started"',b'"stage.completed"',b'"stage.failed"',b'"command.completed"',b'"prompt.completed"',b'"run.created"',b'"checkpoint.completed"')): continue
        try: e=json.loads(l)
        except Exception: continue
        ev=e['event']; p=e.get('properties',{}); sid=e.get('stage_id'); node=(sid or '').split('@')[0] or e.get('node_id')
        if ev=='run.created':
            out['repo']=p.get('git',{}).get('origin_url'); out['title']=p.get('title')
        elif ev=='stage.started':
            cur[sid]=dict(id=sid,node=e.get('node_id'),start=e['ts'],att=p.get('attempt')); out['stages'].append(cur[sid])
        elif ev in('stage.completed','stage.failed'):
            c=cur.get(sid)
            if c is None: continue
            c['end']=e['ts']; c['st']=p.get('status') or ev
            keep0=0
        elif ev=='checkpoint.completed':
            c=cur.get(sid) or cur.get('%s@%s'%(e.get('node_id'),p.get('graph_visit')))
            if c is None: continue
            cv=p.get('context_values') or {}
            keep={k:cv[k] for k in ('task_validation_ok','iteration_passed','refute_verdict','refute_route','refute_not_met','refute_unverifiable_only','review_route','local_review_route','task_validation_mode','plan_review_route','final_route','loop','rung') if k in cv}
            if c['node'] in ('check','record','refute_gate','review_gate','local_review_gate','plan_review_gate','final_check','pick'): c['ctx']=keep
        elif ev=='prompt.completed':
            c=cur.get(sid)
            if c is not None: c['model']=(p.get('provider') or '')+'/'+(p.get('model') or ''); c['usage']=p.get('usage')
        elif ev=='agent.tool.started':
            t=p['event'].get('ToolCallStarted',{}); tn=t.get('tool_name',''); a=t.get('arguments') or {}; a=a if isinstance(a,dict) else {}
            c=cur.get(sid)
            if c is None: continue
            if node in IT:
                if tn in('edit_file','write_file','apply_patch'):
                    f=rel(a.get('file_path') or a.get('path') or '')
                    c.setdefault('files',{}); c['files'][f]=c['files'].get(f,0)+1
                elif tn.endswith('submit'): c['submit']={'status':a.get('status'),'blocked':bool(a.get('blocked'))}
            elif tn.endswith('submit') and node in REV:
                fs=[]
                if node=='refute':
                    for x in a.get('criteria') or []:
                        if isinstance(x,dict) and x.get('status')!='met': fs.append(dict(kind=x.get('status'),text=(x.get('criterion') or '')[:260],ev=(x.get('evidence') or '')[:400]))
                    for x in a.get('defects') or []:
                        if isinstance(x,dict): fs.append(dict(kind='defect',file=x.get('file'),line=x.get('line'),text=(x.get('scenario') or '')[:300]))
                else:
                    for x in a.get('findings') or []:
                        if isinstance(x,dict): fs.append(dict(kind=x.get('severity'),files=x.get('files') or [x.get('file')],line=x.get('line'),text=(x.get('message') or '')[:300],fid=x.get('id')))
                c['submit']=dict(decision=a.get('decision'),n=len(fs)); c['fs']=fs  # last submit wins
            elif tn.endswith('submit') and node=='plan':
                ts=a.get('tasks') or []
                if isinstance(ts,list):
                    c['plan']=[dict(id=t.get('id'),title=(t.get('title') or '')[:120],files=t.get('files'),nacc=len(t.get('acceptance') or []),ctx=len(t.get('context') or '')) for t in ts if isinstance(t,dict)]
        elif ev=='agent.tool.completed' and node in IT:
            c=cur.get(sid); s=p['event'].get('ToolCallCompleted',{}).get('output') or ''
            if not isinstance(s,str): s=json.dumps(s)
            if c is not None and 'task' not in c and '=== current_task.json' in s:
                body=s.split('=== current_task.json',1)[1]
                m=re.search(r'\n  "id": "([^"]+)"',body)
                if m: c['task']=m.group(1)
                m=re.search(r'\n  "source": "([^"]+)"',body)
                if m: c['src']=m.group(1)
    pr.wait()
    out['nev']=n
    out['stages']=[s for s in out['stages'] if s['node'] in IT+REV+('plan','check','record','refute_gate','review_gate','local_review_gate','plan_review_gate','final_check','pick','open_pr','report_merged','report_blocked','learn','resolve_merge')]
    print(json.dumps(out),flush=True)
