"""Run a seeded-defect evaluation over report slices and summarise it.

Each leg reviews one slice with one backend and one rng seed, through
experiment_ab.py in its own process with its own HOME (no shared pi or
opencode state; opencode's model catalogue is pre-seeded from the caller's
cache so a cold HOME does not fail on newer models). Legs run at a fixed
concurrency so two rounds are comparable. Credentials come only from the
environment (e.g. DEEPSEEK_API_KEY) and are never written anywhere.

  python eval_slices.py --set dev --backends briefloop-native --seeds 11 12 \
      --model deepseek/deepseek-v4-flash --variant high --concurrency 8 --out DIR

The summary is written to DIR/summary.json and printed per backend: accepted
runs, recall per defect category, median wall time, mean model requests,
generated tokens and cost on one price table.
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SLICES = Path.home() / 'Developer' / 'briefloop-eval' / 'slices'
PATH = ':'.join([str(Path.home() / '.opencode' / 'bin'), '/usr/local/bin', '/opt/homebrew/bin', '/usr/bin', '/bin'])


def legs(manifest, which, backends, seeds, repeats):
    for item in manifest['slices']:
        if which != 'all' and item['set'] != which:
            continue
        for seed in seeds:
            for backend in backends:
                for k in range(repeats):
                    yield {'slice': item['name'], 'version': item['version'], 'backend': backend,
                           'seed': seed, 'label': f"{item['name']}-{seed}-{backend}-{k}"}


def launch(leg, args, slices, out):
    home = out / 'home' / leg['label']
    (home / '.cache' / 'opencode').mkdir(parents=True, exist_ok=True)
    catalogue = Path.home() / '.cache' / 'opencode' / 'models.json'
    if catalogue.exists():
        shutil.copy(catalogue, home / '.cache' / 'opencode' / 'models.json')
    env = {'PATH': PATH, 'HOME': str(home), 'LANG': 'en_US.UTF-8',
           **{k: v for k, v in os.environ.items() if k.endswith('_API_KEY')}}
    log = open(out / 'legs' / (leg['label'] + '.log'), 'w')
    command = ['caffeinate', '-i', sys.executable, str(HERE / 'experiment_ab.py'),
               str(slices / leg['slice']), leg['version'], '--model', args.model, '--variant', args.variant,
               '--backends', leg['backend'], '--repeat', '1', '--seed', str(leg['seed']),
               '--system-layers', args.system_layers]
    if sys.platform != 'darwin':
        command = command[2:]
    return subprocess.Popen(command, cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)


def summarise(results):
    by_backend = defaultdict(list)
    for leg, outcome in results:
        by_backend[leg['backend']].append(outcome)
    summary = {}
    for backend, rows in by_backend.items():
        ok = [r for r in rows if r.get('status') == 'complete']
        hits, planted = Counter(), Counter()
        for r in ok:
            for category, found in (r.get('detected') or {}).items():
                planted[category] += 1
                hits[category] += bool(found)
        usage = lambda key: [r['usage'][key] for r in ok if r.get('usage', {}).get(key) is not None]
        mean = lambda values: round(statistics.mean(values), 4) if values else None
        summary[backend] = {
            'accepted': f'{len(ok)}/{len(rows)}',
            'recall': f'{sum(hits.values())}/{sum(planted.values())}',
            'per_category': {c: f'{hits[c]}/{planted[c]}' for c in sorted(planted)},
            'wall_median': statistics.median([r['wall_seconds'] for r in ok]) if ok else None,
            'requests_mean': mean(usage('requests')),
            'output_tokens_mean': mean(usage('output_total')),
            'reasoning_tokens_mean': mean(usage('reasoning')),
            'cost_mean_usd': mean(usage('cost_same_table')),
            'failed': [r.get('error', '')[:120] for r in rows if r.get('status') != 'complete'],
        }
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slices', default=str(DEFAULT_SLICES))
    parser.add_argument('--set', default='dev', choices=['dev', 'held', 'all'])
    parser.add_argument('--backends', nargs='+', default=['briefloop-native'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[11])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--model', default='deepseek/deepseek-v4-flash')
    parser.add_argument('--variant', default='high')
    parser.add_argument('--concurrency', type=int, default=8)
    parser.add_argument('--out', required=True)
    parser.add_argument('--system-layers', default='core,role,mode', help='passed to experiment_ab.py (native ablation)')
    args = parser.parse_args()
    slices = Path(args.slices).expanduser().resolve()
    manifest = json.loads((slices / 'manifest.json').read_text(encoding='utf-8'))
    out = Path(args.out).expanduser().resolve()
    (out / 'legs').mkdir(parents=True, exist_ok=False)
    pending = list(legs(manifest, args.set, args.backends, args.seeds, args.repeats))
    print(f'{len(pending)} legs, concurrency {args.concurrency}', flush=True)
    running, results = [], []
    while pending or running:
        while pending and len(running) < args.concurrency:
            leg = pending.pop(0)
            running.append((leg, launch(leg, args, slices, out), time.monotonic()))
        time.sleep(5)
        for item in list(running):
            leg, process, _ = item
            if process.poll() is None:
                continue
            running.remove(item)
            lines = (out / 'legs' / (leg['label'] + '.log')).read_text(encoding='utf-8').splitlines()
            outcome = next((json.loads(line) for line in lines if line.startswith('{')),
                           {'status': 'failed', 'error': (lines[-1] if lines else 'no output')})
            results.append((leg, outcome))
            print(f"{leg['label']}: {outcome.get('status')} {outcome.get('wall_seconds')}s "
                  f"hit={sum((outcome.get('detected') or {}).values())}/{outcome.get('planted')}", flush=True)
    summary = summarise(results)
    (out / 'summary.json').write_text(json.dumps({'args': vars(args), 'summary': summary,
                                                  'legs': [{**leg, 'outcome': o} for leg, o in results]},
                                                 ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
