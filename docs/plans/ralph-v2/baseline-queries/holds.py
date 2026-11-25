import sqlite3, json, collections as C, re
c=sqlite3.connect('file:/data/scheduler.db?mode=ro',uri=True); c.row_factory=sqlite3.Row
W="2026-11-09"
rows=c.execute("""select h.run_id,h.repo,h.merged,h.pr_number,s.task_sources ts,p.state,p.report,p.reason,p.operator_commits,p.additions,p.deletions
 from run_history h left join run_summary s using(run_id) left join pr_facts p on p.repo=h.repo and p.pr_number=h.pr_number
 where h.workflow='ralph' and h.finished_at>=? and h.target_kind='issue' and h.pr_number is not null and h.merged is not 1""",(W,)).fetchall()
def cls(r):
    x=(r['reason'] or '')
    if x.startswith('Diff hygiene'): return 'hygiene:'+re.sub(r'=\d+','',x.split(':')[1].split('.')[0].strip())
    if 'refuter' in x: return 'refuter'
    if 'review did not end' in x: return 'review-needs-human'
    if 'CI' in x: return 'ci'
    return x[:40] or str(r['report'])
cnt=C.Counter(cls(r) for r in rows); print(cnt.most_common())
print("final PR state of held:", C.Counter((cls(r).split(':')[0], r['state'], 'opcommit' if r['operator_commits'] else 'noop') for r in rows).most_common(30))
# merged PRs: how many had hygiene counters nonzero? n/a. size of hygiene PRs vs others
import statistics as st
for k in ('hygiene','refuter'):
    v=[r['additions']+r['deletions'] for r in rows if cls(r).startswith(k)]
    print(k,'size p50',st.median(v) if v else None,len(v))
m=c.execute("""select p.additions+p.deletions s from run_history h join pr_facts p on p.repo=h.repo and p.pr_number=h.pr_number where h.workflow='ralph' and h.merged=1 and h.finished_at>=?""",(W,)).fetchall()
print('merged size p50',st.median([x['s'] for x in m]))
