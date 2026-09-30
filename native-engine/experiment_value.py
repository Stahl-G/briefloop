"""Measure Evaluator sensitivity to frozen value perturbations (#757).

V2 runs require --materials, a checked r2 manifest. Labels, paraphrase checks,
chapter expectations and planting truth stay in the external experiment record;
none are supplied to the blinded product Evaluator.
"""
import argparse
import json
import math
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


def write_record(out, record):
    path = Path(out) / 'legs' / f"{record['slice']}-{record['kind']}-{record['repeat']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')


def leg(name, version, kind, repeat, model, variant, out, slices=str(SLICES), material=None, quality_candidate=False):
    sys.path.insert(0, str(HERE.parent / 'src'))
    sys.path.insert(0, str(HERE))
    import seed_value
    import value_materials
    record = {'slice': name, 'version': version, 'kind': kind, 'repeat': repeat, 'truth': None,
              'quality_candidate': quality_candidate, 'requested_model': model, 'requested_variant': variant}
    if material:
        record['chapter'] = material['chapter']
        record['materials'] = {key: material[key] for key in ('markdown_sha256', 'reader_contract_sha256')}
        record['materials']['rules'] = value_materials.RULES
    try:
        if kind in seed_value.V2_KINDS and material is None:
            raise ValueError('V2 requires a verified --materials manifest; legacy caches are not used')
        source = Path(slices) / name
        if material:
            value_materials.validate_workspace(source, version, material)
        work = Path(tempfile.mkdtemp(prefix=f'bl-value-{name}-{kind}-')).resolve()
        record['work'] = str(work)
        shutil.copytree(source, work / 'ws')
        if material:
            value_materials.validate_workspace(work / 'ws', version, material)
        truth = seed_value.apply(work / 'ws', version, kind,
                                 material['paraphrases'] if material else {}, material['labels'] if material else None)
        record['truth'] = truth
        if truth is None:
            record.update(status='not_applicable', reason='No applicable perturbation changed the frozen Markdown')
        else:
            # No runtime construction or model call until the copied material and edit pass.
            import experiment_ab
            from briefloop.store import Store
            from briefloop.interactive_runtime import InteractiveRuntime
            from briefloop.native_engine import NativeEngine
            from briefloop.native_harness import NativeHarness
            from briefloop.opencode_harness import OpencodeHarness
            store = Store(work / 'ws')
            harness = NativeHarness(store, NativeEngine())
            opencode = OpencodeHarness(store)
            runtime = InteractiveRuntime(store, backends={'briefloop-native': harness, 'opencode': opencode})
            record.update(experiment_ab.run_evaluator_leg(work, store, harness, opencode, runtime, version,
                                                          'briefloop-native', model, variant, repeat, None, quality_candidate=quality_candidate))
            if record.get('status') == 'complete' and record.get('route') != 'evaluator':
                record.update(status='failed', error='Actual route was not evaluator; score excluded')
            rows = store.rows('SELECT data FROM assessments WHERE version_id=? ORDER BY rowid DESC LIMIT 1', (version,))
            if rows:
                data = json.loads(rows[0]['data'])
                record['assessment'] = data
                record['quoted'] = seed_value.mentioned(data, truth) if kind != 'control' else None
    except Exception as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:500])
    finally:
        write_record(out, record)
    return record


def _scored(record):
    scores = record.get('scores') or {}
    return (record.get('status') == 'complete' and record.get('assessment_status') == 'complete'
            and record.get('route') == 'evaluator'
            and all(isinstance(scores.get(d), (int, float)) and not isinstance(scores[d], bool)
                    and math.isfinite(scores[d]) for d in DIMENSIONS))


def _summary(records):
    scored = [r for r in records if _scored(r)]
    controls = {}
    for row in scored:
        if row['kind'] == 'control':
            controls.setdefault(row['slice'], []).append(row)
    base = {s: {d: statistics.mean(x['scores'][d] for x in rows) for d in DIMENSIONS} for s, rows in controls.items()}
    kinds = {}
    for row in scored:
        if row['kind'] == 'control' or row['slice'] not in base:
            continue
        kind = kinds.setdefault(row['kind'], {'legs': 0, 'quoted': 0, 'noticed': 0, 'overall': [],
                                                'delta': {d: [] for d in DIMENSIONS}, 'legs_any_dimension_lower': 0})
        kind['legs'] += 1
        kind['quoted'] += bool(row.get('quoted'))
        deltas = {d: row['scores'][d] - base[row['slice']][d] for d in DIMENSIONS}
        for dimension in DIMENSIONS:
            kind['delta'][dimension].append(deltas[dimension])
        kind['overall'].append(row.get('overall'))
        kind['noticed'] += bool(row.get('quoted')) or (row['kind'] in ('conclusions_removed', 'implications_removed', 'implications_half_removed')
                                                     and deltas['analysis'] < 0)
        kind['legs_any_dimension_lower'] += any(delta < 0 for delta in deltas.values())
    for kind in kinds.values():
        kind['mean_delta'] = {d: round(statistics.mean(values), 2) for d, values in kind.pop('delta').items()}
    rates = {}
    for name in sorted({r['kind'] for r in scored}):
        rows = [r for r in scored if r['kind'] == name]
        paired = [r for r in rows if r['slice'] in base]
        rates[name] = {
            'legs': len(rows), 'control_comparison_legs': len(paired),
            'no_implication': sum(any(f.get('kind') == 'no_implication' for f in (r.get('assessment') or {}).get('findings', [])) for r in rows),
            'analysis_at_or_below_2': sum(r['scores']['analysis'] <= 2 for r in rows),
            'analysis_below_control_mean': sum(r['scores']['analysis'] < base[r['slice']]['analysis'] for r in paired) if paired else None,
            'meets_requirement': sum(r.get('overall') == '达到要求' for r in rows),
        }
    def identity(row):
        return {k: row[k] for k in ('slice', 'kind', 'repeat')}
    return {
        'records': len(records), 'scored_legs': len(scored),
        'control_scores': base, 'control_overall': {s: [x.get('overall') for x in rows] for s, rows in controls.items()},
        'kinds': kinds, 'rates': rates,
        'failed_legs': [{**identity(r), 'error': r.get('error')} for r in records if r.get('status') == 'failed'],
        'not_applicable': [{**identity(r), 'reason': r.get('reason')} for r in records if r.get('status') == 'not_applicable'],
        'scored_without_control': [identity(r) for r in scored if r['kind'] != 'control' and r['slice'] not in base],
        'unscored_complete': [{**identity(r), 'assessment_status': r.get('assessment_status'), 'route': r.get('route')}
                              for r in records if r.get('status') == 'complete' and not _scored(r)],
        'rate_interpretation': 'no_implication counts are observations, not automatic false positives; interpret using chapter responsibility and reviewed variant truth',
    }


def summarise(records):
    summary = _summary(records)
    candidate = [r for r in records if r.get('quality_candidate')]
    if candidate:
        coverage = {'candidate_records': len(candidate), 'scored_legs': sum(_scored(r) for r in candidate),
                    'not_applicable': sum(r.get('status') == 'not_applicable' for r in candidate),
                    'executed_records': sum(r.get('status') != 'not_applicable' for r in candidate),
                    'missing': 0, 'empty': 0, 'invalid': 0, 'nonempty': 0, 'checks': 0}
        for row in candidate:
            if row.get('status') == 'not_applicable':
                continue
            data = row.get('assessment') or {}
            checks = data.get('analysis_checks')
            if 'analysis_checks' not in data:
                coverage['missing'] += 1
            elif not isinstance(checks, list):
                coverage['invalid'] += 1
            elif not checks:
                coverage['empty'] += 1
            else:
                coverage['nonempty'] += 1
                coverage['checks'] += len(checks)
        summary['candidate_checklist_coverage'] = coverage
    summary['chapter_strata'] = {
        expectation: _summary([r for r in records if (r.get('chapter') or {}).get('expectation') == expectation])
        for expectation in ('required', 'optional', 'not_required')
        if any((r.get('chapter') or {}).get('expectation') == expectation for r in records)
    }
    return summary


def select_slices(manifest, selected_set, names=None):
    """An explicit subset must contain only unique names from the requested split."""
    entries = [row for row in manifest['slices'] if row['set'] == selected_set]
    if not entries or len({row['name'] for row in entries}) != len(entries):
        raise ValueError('Selected slices must be present and unique')
    if names is None:
        return entries
    requested = [name.strip() for name in names.split(',')]
    available = {row['name']: row for row in entries}
    if not all(requested) or len(set(requested)) != len(requested) or any(name not in available for name in requested):
        raise ValueError('Names must be nonempty, unique and present in the selected set')
    return [available[name] for name in requested]


def main():
    import seed_value
    from value_materials import load_materials
    parser = argparse.ArgumentParser()
    parser.add_argument('--set', default='dev')
    parser.add_argument('--names', help='comma-separated subset; every name must belong to --set')
    parser.add_argument('--quality-candidate', action='store_true', help='opt in to chapter-v1 checklist candidate for this experiment only')
    parser.add_argument('--kinds', default=','.join(['control', *seed_value.KINDS, *seed_value.V2_KINDS]))
    parser.add_argument('--slices', default=str(SLICES), help='frozen slice directory with manifest.json')
    parser.add_argument('--materials', help='verified r2 materials manifest; required for all V2 kinds')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--concurrency', type=int, default=6)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--variant', default='high')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    kinds = args.kinds.split(',')
    if any(kind not in ('control', *seed_value.KINDS, *seed_value.V2_KINDS) for kind in kinds) or len(kinds) != len(set(kinds)):
        parser.error('Kinds must be supported and unique')
    if args.repeat < 1 or args.concurrency < 1:
        parser.error('Repeat and concurrency must be positive')
    if any(kind in seed_value.V2_KINDS for kind in kinds) and not args.materials:
        parser.error('V2 requires --materials; unverified legacy caches are never used')
    source = Path(args.slices).expanduser().resolve()
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    try:
        slices = select_slices(manifest, args.set, args.names)
    except ValueError as exc:
        parser.error(str(exc))
    # Preflight completes for the whole selected batch before any worker is dispatched.
    materials = load_materials(args.materials, source, slices) if args.materials else {}
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; runs are never overwritten')
    out.mkdir(parents=True)
    legs = [(s['name'], s['version'], kind, i, args.model, args.variant, str(out), str(source), materials.get(s['name']), args.quality_candidate)
            for i in range(args.repeat) for s in slices for kind in kinds]
    # Determine inapplicable perturbations offline; keep a record without dispatching a worker.
    records, dispatched = [], []
    originals = {}
    from value_materials import read_frozen_version
    for item in legs:
        name, version, kind, repeat, *_ = item
        material = materials.get(name)
        if name not in originals:
            originals[name] = read_frozen_version(source / name, version)[0]
        truth = {'kind': 'control'} if kind == 'control' else seed_value.degrade(
            originals[name], kind, lambda _: '', material['paraphrases'] if material else {},
            material['labels'] if material else None)[1]
        if truth is not None:
            dispatched.append(item)
            continue
        record = {'slice': name, 'version': version, 'kind': kind, 'repeat': repeat, 'status': 'not_applicable',
                  'truth': None, 'reason': 'Offline preflight found no actual perturbation', 'model_calls': 0,
                  'quality_candidate': args.quality_candidate, 'requested_model': args.model, 'requested_variant': args.variant}
        if material:
            record['chapter'] = material['chapter']
        records.append(record)
        write_record(out, record)
    plan = {'model': args.model, 'variant': args.variant, 'legs': len(legs), 'slices': [s['name'] for s in slices],
            'kinds': kinds, 'repeat': args.repeat, 'quality_candidate': args.quality_candidate,
            'dispatched_legs': len(dispatched), 'not_applicable_legs': len(records), 'slice_dir': str(source), 'materials_manifest': str(Path(args.materials).resolve()) if args.materials else None,
            'chapter_expectations': {s: material['chapter'] for s, material in materials.items()}}
    (out / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf-8')
    with ProcessPoolExecutor(args.concurrency, mp_context=get_context('spawn')) as pool:
        futures = {pool.submit(leg, *item): item for item in dispatched}
        for future in as_completed(futures):
            name, _, kind, i = futures[future][:4]
            try:
                record = future.result()
            except Exception as exc:
                record = {'slice': name, 'kind': kind, 'repeat': i, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:500],
                          'quality_candidate': args.quality_candidate}
                if name in materials:
                    record['chapter'] = materials[name]['chapter']
                write_record(out, record)
            records.append(record)
            print(name, kind, i, record.get('status'), record.get('overall'), record.get('scores'), 'quoted' if record.get('quoted') else '', flush=True)
    summary = summarise(records)
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(summary['kinds'], ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    main()
