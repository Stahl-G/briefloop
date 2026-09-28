"""Explicitly authorized synthetic fast workflow; never a quality benchmark.

Run from a checkout with PYTHONPATH=src. Only the synthetic source below is sent
to the selected, already-configured provider. No web search or private reports.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.request

from briefloop.server import make_server, _close_service
from briefloop.store import dump

SOURCE = '''# Blue Leaf Services — synthetic acceptance material
This company and every number below are fictional, for software testing only.
Published September 15, 2026. Updated September 17, 2026 for layout only.
## Revenue (USD thousands)
| Period | Revenue |
| --- | ---: |
| Q1 2026 | 1000 |
| Q2 2026 | 1200 |
Q2 revenue was US$ 1.2 million, up 20% from Q1. No prior-year data is available.
## Delivery status
The factory remains under construction. It has not opened.
Management tentatively targets delivery of up to 80 units in Q4, subject to a permit.
Timing is uncertain. Only two qualified pilot partners are eligible, not all customers.
No profit figures or causes of revenue growth were disclosed.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--model', required=True, help='Existing Native provider/model ID')
    parser.add_argument('--effort', default='high')
    parser.add_argument('--allow-model-requests', action='store_true')
    parser.add_argument('--hold-preview-seconds', type=int, default=0)
    args = parser.parse_args()
    if not args.allow_model_requests:
        parser.error('Explicit --allow-model-requests is required; this sends synthetic material and uses your provider quota.')
    if not 0 <= args.hold_preview_seconds <= 600:
        parser.error('Preview hold must be between 0 and 600 seconds.')
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    # Bind to actual tracked/untracked code, not a command-line claim of HEAD.
    paths = subprocess.check_output(['git', 'ls-files', '-c', '-o', '--exclude-standard'], cwd=root, text=True).splitlines()
    hashes = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in paths
              if name.startswith(('src/', 'frontend/', 'native-engine/')) and (root/name).is_file()}
    (args.out/'freeze.json').write_text(dump({'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'files': hashes, 'backend': 'briefloop-native', 'model': args.model, 'model_variant': args.effort,
        'scope': 'One synthetic fast -> enrich -> assess workflow, no search, no Jev call, no semantic grading.'}), encoding='utf-8')
    server = make_server(args.out/'workspace', port=0, paused=True)
    store = server.store
    store.set_meta('settings', {**store.settings(), 'agent_backend': 'briefloop-native',
        'model_selection_required': False, 'model': args.model, 'model_variant': args.effort,
        'company_context_enabled': False, 'auto_learn': False, 'auto_revision': False,
        'fact_checker': False, 'role_models': {}, 'max_reports': 1})
    source = store.add_source('Blue Leaf synthetic announcement', SOURCE)
    (args.out/'source.txt').write_text(SOURCE, encoding='utf-8')
    server.worker.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    (args.out/'service.json').write_text(dump({'url': url, 'workspace': str(store.root)}), encoding='utf-8')
    try:
        token = json.load(urllib.request.urlopen(url+'/api/session', timeout=10))['token']
        requirements = {'title': 'Blue Leaf 季度观察（虚构数据）',
            'objective': '用一张小表和简短说明呈现两季收入及交付计划。保留单位、期间、环比、发布日期与更新日区别、对象范围、许可前提和不确定性；不要虚构利润或原因。',
            'completion_mode': 'fast', 'language': 'zh', 'allow_web': False, 'fact_check': False,
            'target_words': 350, 'max_words': 550, 'target_minutes': 10}
        request = urllib.request.Request(url+'/api/generate', data=dump({'requirements': requirements, 'source_ids': [source['id']]}).encode(),
            headers={'Content-Type': 'application/json', 'Origin': url, 'X-BriefLoop-Token': token})
        job = json.load(urllib.request.urlopen(request, timeout=20))
        (args.out/'manifest.json').write_text(dump({'job_id': job['id'], 'requirements': requirements,
            'source_id': source['id'], 'started_epoch': time.time()}), encoding='utf-8')
        print(dump({'url': url, 'job_id': job['id'], 'phase': 'started'}), flush=True)
        start, first, last = time.monotonic(), None, None
        while True:
            jobs = store.rows('SELECT id,kind,status,error,payload FROM jobs ORDER BY rowid')
            briefs = store.rows('SELECT id,parent_id,author,hash,markdown,detail FROM briefs ORDER BY rowid')
            if briefs and first is None:first = round(time.monotonic()-start, 3)
            status = {'elapsed_seconds': round(time.monotonic()-start, 3), 'first_draft_seconds': first,
                      'jobs': [{key: value for key, value in row.items() if key != 'payload'} for row in jobs],
                      'brief_ids': [row['id'] for row in briefs]}
            (args.out/'status.json').write_text(dump(status), encoding='utf-8')
            signature = [(row['id'], row['status']) for row in jobs]
            if signature != last:
                print(dump(status), flush=True);last = signature
            if jobs and not any(row['status'] in ('queued', 'running') for row in jobs):break
            time.sleep(2)
        # Preserve entire public assessments, including coverage and not_checked;
        # findings count and schema acceptance are never presented as accuracy.
        assessments = [{'version_id': row['version_id'], 'data': json.loads(row['data'])}
                       for row in store.rows('SELECT version_id,data FROM assessments ORDER BY rowid')]
        (args.out/'assessments.json').write_text(dump(assessments), encoding='utf-8')
        (args.out/'reports.json').write_text(dump(briefs), encoding='utf-8')
        (args.out/'jobs.json').write_text(dump(jobs), encoding='utf-8')
        (args.out/'outcome.json').write_text(dump({**status, 'semantic_review': 'not_reviewed',
            'assessment_ids': [row['version_id'] for row in assessments]}), encoding='utf-8')
        if args.hold_preview_seconds:
            deadline = time.monotonic()+args.hold_preview_seconds
            while time.monotonic()<deadline and not (args.out/'close-preview').exists():time.sleep(1)
        if any(row['status'] != 'complete' for row in jobs):raise SystemExit(1)
    finally:
        server.shutdown();_close_service(server)


if __name__ == '__main__':
    main()
