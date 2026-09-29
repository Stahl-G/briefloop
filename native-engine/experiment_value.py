"""Does the Evaluator notice low-value text? (#757, step 1: measure before redesigning)

For each slice, the unchanged control and each value degradation (seed_value.py)
are scored through Worker.assess_version on the native engine, repeated, each
leg in its own process on a fresh copy. Nothing changes the product prompts.

  python experiment_value.py --set dev --repeat 3 --concurrency 6 \
      --model opencode-go/deepseek-v4.1-flash --variant high --out DIR

A kind counts as noticed in a leg when a finding quotes the planted text, or
(for removed conclusions, which leave nothing to quote) when the analysis score
falls below that slice's control mean. Score drops are reported per dimension.
"""
import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLICES = Path.home() / 'Developer' / 'briefloop-eval' / 'slices'
DIMENSIONS = ('evidence', 'coverage', 'analysis', 'expression')


def leg(name, version, kind, repeat, model, variant, out):
    sys.path.insert(0, str(HERE.parent / 'src'))
    sys.path.insert(0, str(HERE))
    import experiment_ab
    import seed_value
    from briefloop.store import Store
    from briefloop.interactive_runtime import InteractiveRuntime
    from briefloop.native_engine import NativeEngine
    from briefloop.native_harness import NativeHarness
    from briefloop.opencode_harness import OpencodeHarness
    work = Path(tempfile.mkdtemp(prefix=f'bl-value-{name}-{kind}-')).resolve()
    shutil.copytree(SLICES / name, work / 'ws')
    truth = seed_value.apply(work / 'ws', version, kind)
    record = {'slice': name, 'kind': kind, 'repeat': repeat, 'work': str(work), 'truth': truth}
    if truth is None:
        record['status'] = 'not_applicable'
    else:
        store = Store(work / 'ws')
        harness = NativeHarness(store, NativeEngine())
        opencode = OpencodeHarness(store)
        runtime = InteractiveRuntime(store, backends={'briefloop-native': harness, 'opencode': opencode})
        record.update(experiment_ab.run_evaluator_leg(work, store, harness, opencode, runtime, version,
                                                      'briefloop-native', model, variant, repeat, None))
        rows = store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1', (version,))
        if rows:
            data = json.loads(rows[0]['data'])
            record['assessment'] = data
            record['quoted'] = seed_value.mentioned(data, truth) if kind != 'control' else None
    path = Path(out) / 'legs' / f'{name}-{kind}-{repeat}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return record


def summarise(records):
    scored = [r for r in records if r.get('scores') and all(r['scores'].get(d) for d in DIMENSIONS)]
    control = {}
    for r in scored:
        if r['kind'] == 'control':
            control.setdefault(r['slice'], []).append(r)
    base = {s: {d: statistics.mean(x['scores'][d] for x in rows) for d in DIMENSIONS} for s, rows in control.items()}
    base_overall = {s: [x['overall'] for x in rows] for s, rows in control.items()}
    kinds = {}
    for r in scored:
        if r['kind'] == 'control' or r['slice'] not in base:
            continue
        k = kinds.setdefault(r['kind'], {'legs': 0, 'quoted': 0, 'noticed': 0, 'overall': [], 'delta': {d: [] for d in DIMENSIONS}})
        k['legs'] += 1
        k['quoted'] += bool(r.get('quoted'))
        deltas = {d: r['scores'][d] - base[r['slice']][d] for d in DIMENSIONS}
        for d in DIMENSIONS:
            k['delta'][d].append(deltas[d])
        k['overall'].append(r['overall'])
        k['noticed'] += bool(r.get('quoted')) or (r['kind'] == 'conclusions_removed' and deltas['analysis'] < 0)
    for k in kinds.values():
        k['mean_delta'] = {d: round(statistics.mean(v), 2) for d, v in k['delta'].items()}
        k['legs_any_dimension_lower'] = None
    for name, k in kinds.items():
        rows = [r for r in scored if r['kind'] == name and r['slice'] in base]
        k['legs_any_dimension_lower'] = sum(any(r['scores'][d] < base[r['slice']][d] for d in DIMENSIONS) for r in rows)
        del k['delta']
    return {'control_scores': base, 'control_overall': base_overall, 'kinds': kinds,
            'failed_legs': [{'slice': r['slice'], 'kind': r['kind'], 'repeat': r['repeat'], 'error': r.get('error')}
                            for r in records if r.get('status') == 'failed'],
            'not_applicable': [(r['slice'], r['kind']) for r in records if r.get('status') == 'not_applicable']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--set', default='dev')
    parser.add_argument('--kinds', default='control,' + ','.join(__import__('seed_value').KINDS))
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--concurrency', type=int, default=6)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--variant', default='high')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; runs are never overwritten')
    out.mkdir(parents=True)
    manifest = json.loads((SLICES / 'manifest.json').read_text(encoding='utf-8'))
    slices = [s for s in manifest['slices'] if s['set'] == args.set]
    legs = [(s['name'], s['version'], kind, i, args.model, args.variant, str(out))
            for i in range(args.repeat) for s in slices for kind in args.kinds.split(',')]
    (out / 'plan.json').write_text(json.dumps({'model': args.model, 'variant': args.variant, 'legs': len(legs),
                                               'slices': [s['name'] for s in slices], 'kinds': args.kinds.split(','),
                                               'repeat': args.repeat}, ensure_ascii=False, indent=1))
    records = []
    with ProcessPoolExecutor(args.concurrency, mp_context=get_context('spawn')) as pool:
        futures = {pool.submit(leg, *item): item for item in legs}
        for future in as_completed(futures):
            name, _, kind, i = futures[future][:4]
            try:
                record = future.result()
            except Exception as exc:
                record = {'slice': name, 'kind': kind, 'repeat': i, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:500]}
            records.append(record)
            print(name, kind, i, record.get('status'), record.get('overall'), record.get('scores'), 'quoted' if record.get('quoted') else '', flush=True)
    summary = summarise(records)
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary['kinds'], ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    main()
