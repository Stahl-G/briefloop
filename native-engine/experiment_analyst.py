"""One saved Chinese writing task, frozen research, five explicitly chosen arms.

Run --prepare first. --run asks for the provider key through a hidden terminal
prompt, then writes append-only outcomes. It never edits the source workspace.
"""
import argparse
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from briefloop import analyst
from briefloop.store import Store, dump, uid
from briefloop.native_engine import NativeEngine
from briefloop.native_harness import NativeHarness
from briefloop.opencode_harness import OpencodeHarness
from briefloop.interactive_runtime import InteractiveRuntime
from experiment_ab import event_stats

ARMS = [
    ('native-deepseek', 'briefloop-native', 'opencode-go/deepseek-v4.1-flash'),
    ('opencode-deepseek', 'opencode', 'opencode-go/deepseek-v4.1-flash'),
    ('native-muse', 'briefloop-native', 'opencode-go/muse-spark-1.3-contributor'),
    ('native-qwen', 'briefloop-native', 'opencode-go/qwen3.8-flash'),
    ('native-glm', 'briefloop-native', 'opencode-go/glm-5.3-flash'),
]


def prepare(source, job_id, output, arm_names=None):
    original = source/'jobs'/job_id
    plan = json.loads((original/'plan.json').read_text())
    research = json.loads((original/'joined-scouts.json').read_text())
    input_pack = json.loads((original/'input.json').read_text())
    source_ids = sorted({s.get('source_id') or s['id'] for s in input_pack['sources'] + input_pack.get('reference_sources', [])}
                        | {s['source_id'] for s in research['sources']})
    support = {name: json.loads((original/old).read_text()) for name, old in
               [('raw-data.json', 'RAW_JSON'), ('prepared-data.json', 'PREPARED_JSON')] if (original/old).is_file()}
    output.mkdir(parents=True, exist_ok=False)
    c = sqlite3.connect(f'file:{source/"briefloop.db"}?mode=ro', uri=True)
    run_id = json.loads(c.execute('select payload from jobs where id=?', (job_id,)).fetchone()[0])['run_id']
    c.close()
    manifest = {'source_workspace': str(source), 'source_job': job_id, 'run_id': run_id,
                'variant': 'low', 'language': 'zh-CN', 'model_repeats': 1, 'writing_stage_allow_web': False,
                'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(), 'arms': []}
    expected = None
    selected = [arm for arm in ARMS if not arm_names or arm[0] in arm_names]
    if not selected:raise ValueError("No matching experiment arms")
    for name, backend, model in selected:
        ws = output/name/'ws'
        shutil.copytree(source, ws, ignore=shutil.ignore_patterns('jobs', '*.log', 'server.json'))
        store = Store(ws)
        store.set_meta('settings', {**store.settings(), 'timeout_minutes': 0})
        jid = uid('job');folder = ws/'jobs'/jid/'analyst'
        payload = {'run_id': run_id, 'agent_backend': backend, 'runtime': {'model': model, 'model_variant': 'low'}}
        with store.tx() as db:
            db.execute("INSERT INTO jobs(id,kind,payload,status,result,error,created,updated) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
                       (jid, 'generate', dump(payload), 'running', None, None))
        pack = analyst.packet(store, run_id, folder, plan=plan, research=research, source_ids=source_ids, support=support)
        expected = expected or pack['fingerprint']
        if pack['fingerprint'] != expected:
            raise ValueError('Prepared arm packets differ before any model call')
        manifest['arms'].append({'name': name, 'backend': backend, 'model': model, 'job_id': jid,
                                 'folder': str(folder), 'workspace': str(ws), 'packet_fingerprint': pack['fingerprint']})
    manifest['input_fingerprint'] = expected
    manifest['code_files'] = {str(p.relative_to(Path(__file__).resolve().parent.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [Path(__file__).resolve(), *[Path(__file__).resolve().parent.parent/f for f in (
                                  'src/briefloop/analyst.py', 'src/briefloop/draft_checks.py', 'src/briefloop/delivery_checks.py', 'src/briefloop/cli.py',
                                  'src/briefloop/native_roles.py', 'src/briefloop/native_harness.py', 'src/briefloop/agent_prompts.py',
                                  'src/briefloop/prompt_assets/role.analyst.zh.md', 'src/briefloop/static/native-engine.mjs')]]}
    (output/'manifest.json').write_text(dump(manifest))
    (output/'inputs.json').write_text(dump({'plan': plan, 'research': research, 'source_ids': source_ids, 'support': support}))
    print(dump({'prepared': True, 'arms': len(selected), 'sources': len(source_ids), 'fingerprint': expected}), flush=True)


def usage(folder, backend):
    ex = json.loads((folder/'execution.json').read_text()) if (folder/'execution.json').exists() else {}
    if backend == 'briefloop-native':
        raw = [r.get('raw', {}) for r in ex.get('usage', [])]
        rows = [{'input': r.get('input', 0), 'output': r.get('output', 0), 'reasoning': r.get('reasoning', 0),
                 'cacheRead': r.get('cacheRead', 0), 'cacheWrite': r.get('cacheWrite', 0)} for r in raw]
        missing = []
        for path in folder.glob('20*.jsonl'):
            for line in path.read_text().splitlines():
                entry = json.loads(line);message = entry.get('message', {})
                if message.get('role') == 'assistant' and message.get('stopReason') in ('error', 'aborted'):
                    missing.append(entry.get('id') or message.get('timestamp'))
    else:
        db = Path.home()/'.local/share/opencode/opencode.db'
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        messages = [json.loads(r[0]) for r in c.execute("SELECT data FROM message WHERE json_extract(data,'$.role')='assistant' AND json_extract(data,'$.path.cwd')=?", (str(folder.resolve()),))]
        c.close();rows=[];missing=[]
        for m in messages:
            t = m.get('tokens') or {}
            rows.append({'input': t.get('input', 0), 'output': t.get('output', 0)+t.get('reasoning', 0),
                         'reasoning': t.get('reasoning', 0), 'cacheRead': t.get('cache', {}).get('read', 0),
                         'cacheWrite': t.get('cache', {}).get('write', 0)})
            if m.get('finish') not in ('stop', 'tool-calls') and not sum(rows[-1].values()):missing.append(m.get('id'))
    return {'requests': len(rows), 'missing_usage_requests': missing, 'usage_complete': not missing,
            **{k: sum(r[k] for r in rows) for k in ('input', 'output', 'reasoning', 'cacheRead', 'cacheWrite')}}


def write_reader(store, brief, target, arm):
    from markdown_it import MarkdownIt
    from briefloop.exports import reader_markdown
    text = reader_markdown(store, brief)
    (target/'report.md').write_text(text)
    body = MarkdownIt('commonmark', {'html': False}).enable('table').render(text)
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Analyst 对比稿</title><style>body{max-width:980px;margin:48px auto;padding:0 28px;background:#faf9f6;color:#1e2320;font:17px/1.9 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}h1,h2,h3{line-height:1.4}a{color:#006838}table{border-collapse:collapse;display:block;overflow:auto;font-size:15px}td,th{border:1px solid #dedfd8;padding:8px 12px}header{font-size:14px;color:#6a706b;border-bottom:1px solid #dedfd8;padding-bottom:12px}</style><header>'
    page += html.escape(arm['name']+' · '+arm['model'])+'</header><article>'+body+'</article></html>'
    (target/'report.html').write_text(page)


def execute(output, arm_names):
    manifest = json.loads((output/'manifest.json').read_text())
    inputs = json.loads((output/'inputs.json').read_text())
    root = Path(__file__).resolve().parent.parent
    for path, sha in manifest['code_files'].items():
        if hashlib.sha256((root/path).read_bytes()).hexdigest() != sha:
            raise ValueError('Code changed since preparation: '+path)
    key = getpass.getpass('OPENCODE_API_KEY（隐藏输入，不保存）：').strip()
    if not key:raise ValueError('API Key 不能为空')
    os.environ['OPENCODE_API_KEY'] = key;del key
    try:
        for arm in manifest['arms']:
            if arm_names and arm['name'] not in arm_names:continue
            target = output/arm['name'];outcome_path = target/'outcome.json'
            if outcome_path.exists():
                print(dump({'skipped_existing': arm['name']}), flush=True);continue
            store = Store(Path(arm['workspace']));folder=Path(arm['folder'])
            native=NativeHarness(store, NativeEngine());host=OpencodeHarness(store)
            runtime=InteractiveRuntime(store, backends={'briefloop-native': native, 'opencode': host})
            row={**arm,'status':'running'};t0=time.monotonic()
            print(dump({'started':arm['name'],'folder':str(folder)}),flush=True)
            try:
                admitted=analyst.run(store,runtime,store.one('jobs',arm['job_id']),manifest['run_id'],folder,arm['backend'],
                                     expected_fingerprint=manifest['input_fingerprint'],**inputs)
                brief=store.one('briefs',admitted['version_id'])
                write_reader(store,brief,target,arm)
                (target/'draft.json').write_bytes((folder/'draft.json').read_bytes())
                row.update(status='draft_saved',**admitted)
            except Exception as exc:
                row.update(status='failed',error=str(exc)[:800])
            finally:
                row['wall_seconds']=round(time.monotonic()-t0,2)
                row['usage']=usage(folder,arm['backend']);row['events']=event_stats(folder)
                native.close();host.close()
            with outcome_path.open('x') as f:json.dump(row,f,ensure_ascii=False,indent=2)
            with (output/'results.jsonl').open('a') as f:f.write(dump(row)+'\n')
            print(dump(row),flush=True)
            if arm['name']=='native-deepseek' and row['status']!='draft_saved':break
    finally:os.environ.pop('OPENCODE_API_KEY',None)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',type=Path)
    parser.add_argument('--job')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--arms',default='')
    args=parser.parse_args()
    if args.prepare:prepare(args.prepare.resolve(),args.job,args.output.resolve(),args.arms.split(',') if args.arms else None)
    if args.run:execute(args.output.resolve(),args.arms.split(',') if args.arms else [])
