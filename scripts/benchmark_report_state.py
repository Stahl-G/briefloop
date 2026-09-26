"""Synthetic state/browsing benchmark; workspace lives in a temporary directory."""
import argparse
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from unittest.mock import patch

from briefloop.store import Store, dump
from briefloop.report_browsing import reports, versions


def old_snapshot(ref):
    source=subprocess.check_output(['git','show',f'{ref}:src/briefloop/store.py'],text=True)
    tree=ast.parse(source)
    method=next(method for cls in tree.body if isinstance(cls,ast.ClassDef) and cls.name=='Store' for method in cls.body if isinstance(method,ast.FunctionDef) and method.name=='snapshot')
    code='\n'.join(line[4:] for line in source.splitlines()[method.lineno-1:method.end_lineno])
    namespace={'__package__':'briefloop'}
    exec('from datetime import datetime\nimport json\n'+code,namespace)
    return namespace['snapshot']


def seed(store,count):
    source=store.add_source('Synthetic fixed source','Synthetic material')
    text=('## Synthetic section\n\nLong evidence-rich paragraph. '+('Synthetic observation and caveat. '*30)+'\n\n| Metric | Value |\n| --- | --- |\n| Synthetic | 12 |\n\n')*24
    document=dump({'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':text}]}]})
    with store.tx() as c:
        for n in range(count):
            rid=f'run_{n:05}';created=f'2026-09-26T00:{n//60%60:02}:{n%60:02}Z'
            c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,dump({'title':f'Synthetic {n}','objective':'Long report benchmark','max_words':20000,'details':'requirements '*500}),dump([source['id']]),None,created,'normal'))
            for v in range(4):
                vid=f'brief_{n:05}_{v}'
                detail=dump({'title':f'Synthetic {n}','citations':[{'source_id':source['id'],'locator':'Synthetic evidence '*300}],'analysis':'Analysis '*1000})
                c.execute('INSERT INTO briefs VALUES(?,?,?,?,?,?,?,?,?)',(vid,rid,f'brief_{n:05}_{v-1}' if v else None,'agent' if not v else 'user',text,vid,detail,document,created))
                c.execute('INSERT INTO assessments VALUES(?,?,?,?)',(f'assessment_{n}_{v}',vid,dump({'status':'complete','overall':4,'findings':[{'description':'Synthetic evaluation '*600}]}),created))
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',('old_active','generate','running',dump({'run_id':'run_00000'}),None,None,'2020','2020'))
        for n in range(35):c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(f'job_{n}','source_refresh','complete','{}','{}',None,created,created))


def measure(store,call,repeats):
    import briefloop.length as length
    rows=store.rows;length_stats=length.length_stats;stats=[]
    for _ in range(repeats):
        counts={'queries':0,'length_stats':0}
        def counted_rows(*args,**kwargs):counts['queries']+=1;return rows(*args,**kwargs)
        def counted_length(*args,**kwargs):counts['length_stats']+=1;return length_stats(*args,**kwargs)
        start=time.perf_counter()
        with patch.object(store,'rows',counted_rows),patch.object(length,'length_stats',counted_length):
            value=call();encoded=json.dumps(value,ensure_ascii=False).encode()
        stats.append({'ms':(time.perf_counter()-start)*1000,'bytes':len(encoded),**counts})
    ordered=sorted(s['ms'] for s in stats)
    return {'bytes':stats[-1]['bytes'],'queries':stats[-1]['queries'],'length_stats':stats[-1]['length_stats'],'p50_ms':round(statistics.median(ordered),2),'p95_ms':round(ordered[min(len(ordered)-1,int(len(ordered)*.95))],2),'samples_ms':[round(x['ms'],2) for x in stats]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',required=True);parser.add_argument('--counts',default='100,1000');parser.add_argument('--repeats',type=int,default=9);parser.add_argument('--output',required=True);args=parser.parse_args()
    baseline=old_snapshot(args.baseline);result={'baseline':args.baseline,'repeats':args.repeats,'fixture':'Synthetic, four 28KB+ Markdown and rich-document versions per report; long detail/evaluations; one fixed source; old active job','results':[]}
    for count in map(int,args.counts.split(',')):
        with tempfile.TemporaryDirectory(prefix='briefloop-state-benchmark-') as directory:
            store=Store(directory);seed(store,count)
            # Warm both paths once. Measurements include projection+JSON serialization.
            baseline(store);store.snapshot()
            entry={'reports':count,'versions':count*4,'before':measure(store,lambda:baseline(store),args.repeats),'after':measure(store,lambda:store.snapshot(),args.repeats),'reports_page':measure(store,lambda:reports(store),args.repeats),'history_page':measure(store,lambda:versions(store,'run_00000'),args.repeats)}
            result['results'].append(entry);print(json.dumps(entry),flush=True)
    Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
