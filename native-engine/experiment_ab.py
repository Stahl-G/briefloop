"""A/B experiment: same frozen version, same model, different runtime.

For each backend leg: copy the workspace (never touch the original), enqueue a
review job pinned to that backend, drive run_review, then collect metrics from
execution.json / events.jsonl / the admitted review row.

Usage:
  OPENCODE_API_KEY=... python experiment_ab.py <workspace> <version_id> \
      [--model opencode-go/deepseek-v4.1-flash] [--variant high] \
      [--backends briefloop-native,opencode] [--repeat 1] [--out DIR]

Each leg prints its job folder; the summary line is one JSON object per leg so
results can be pasted or diffed. Copies live under the system temp dir.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from briefloop.store import Store  # noqa: E402
from briefloop.interactive_runtime import InteractiveRuntime  # noqa: E402
from briefloop.native_engine import NativeEngine  # noqa: E402
from briefloop.native_harness import NativeHarness  # noqa: E402
from briefloop.opencode_harness import OpencodeHarness  # noqa: E402
from briefloop.review import run_review, get_review  # noqa: E402


def usage_totals(folder):
    ex = folder / 'execution.json'
    if not ex.exists():
        return {}
    data = json.loads(ex.read_text())
    rows = data.get('usage') or []
    if rows and all(row.get('backend') == 'opencode' for row in rows):
        # Opencode reports its latest step only; totals live in its own session
        # store, so a sum here would look like a real (and far too small) figure.
        return {'captured': False, 'seconds': data.get('seconds')}
    totals = {'input': 0, 'output': 0, 'reasoning': 0, 'cacheRead': 0, 'cost': 0.0}
    for row in rows:
        raw = row.get('raw') or {}
        totals['input'] += raw.get('input', 0)
        totals['output'] += raw.get('output', 0)
        totals['reasoning'] += raw.get('reasoning', 0)
        totals['cacheRead'] += raw.get('cacheRead', 0)
        totals['cost'] += (raw.get('cost') or {}).get('total', 0)
    totals['turns'] = len(data.get('usage') or [])
    totals['seconds'] = data.get('seconds')
    return totals


def event_stats(folder):
    path = folder / 'events.jsonl'
    if not path.exists():
        return {}
    kinds = {}
    tools = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get('type') or 'unknown'
        kinds[kind] = kinds.get(kind, 0) + 1
        # One tool.record per executed call; started/completed items would
        # count the same call three times.
        if kind == 'tool.record':
            name = (event.get('data') or {}).get('record', {}).get('tool') or 'unknown'
            tools[name] = tools.get(name, 0) + 1
    return {'events': sum(kinds.values()), 'tool_calls': tools}


def run_leg(source, version_id, backend, model, variant, repeat_index):
    work = Path(tempfile.mkdtemp(prefix=f'bl-ab-{backend}-')).resolve()
    shutil.copytree(source, work / 'ws')
    store = Store(work / 'ws')
    engine = NativeEngine()
    harness = NativeHarness(store, engine)
    opencode = OpencodeHarness(store)
    runtime = InteractiveRuntime(store, backends={
        'briefloop-native': harness, 'opencode': opencode})
    try:
        job = store.enqueue('review', {
            'version_id': version_id,
            'agent_backend': backend,
            'role_models': {'evaluator': {'model': model, 'model_variant': variant}},
        })
        folder = work / 'ws' / 'jobs' / job['id']
        t0 = time.monotonic()
        outcome = {'backend': backend, 'repeat': repeat_index, 'job': job['id'],
                   'folder': str(folder)}
        try:
            result = run_review(store, runtime, job, version_id, folder)
            review = get_review(store, result['id'] if isinstance(result, dict) else result)
            data = review['result'] or {}
            outcome.update({
                'wall_seconds': round(time.monotonic() - t0, 1),
                'review_id': review['id'], 'status': review['status'],
                'fingerprint': review['fingerprint'],
                'review_status': data.get('status'),
                'findings': len(data.get('findings', [])),
                'claim_checks': len(data.get('claim_checks', [])),
                'clause_checks': len(data.get('clause_checks', [])),
                'response_checks': len(data.get('response_checks', [])),
                'unchecked_items': len(data.get('unchecked_items', [])),
                'overall': (data.get('assessment') or {}).get('overall'),
                'admission_retry': (folder / 'admission-error.json').exists(),
                # The reply failed ReviewOutput validation once and was re-asked.
                'schema_correction': (folder / 'schema-correction.json').exists(),
            })
        except Exception as exc:
            outcome.update({'wall_seconds': round(time.monotonic() - t0, 1),
                            'status': 'failed', 'error': str(exc)[:500]})
        outcome['usage'] = usage_totals(folder)
        outcome['events'] = event_stats(folder)
        return outcome
    finally:
        harness.close()
        try:
            opencode.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workspace')
    parser.add_argument('version_id')
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--variant', default='high')
    parser.add_argument('--backends', default='briefloop-native,opencode')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--out')
    args = parser.parse_args()

    source = Path(args.workspace).resolve()
    results = []
    for backend in args.backends.split(','):
        for i in range(args.repeat):
            outcome = run_leg(source, args.version_id, backend.strip(),
                              args.model, args.variant, i)
            results.append(outcome)
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        path = out / f'ab-{int(time.time())}.json'
        path.write_text(json.dumps({'model': args.model, 'variant': args.variant,
                                    'version_id': args.version_id,
                                    'results': results}, ensure_ascii=False, indent=2))
        print(f'summary={path}')


if __name__ == '__main__':
    sys.exit(main())
