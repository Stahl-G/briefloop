"""A/B for the pairwise Evaluator (learning's gate) on a saved comparison input.

Each leg copies the workspace, then runs learning.compare — the product path —
on the input.json of an earlier learning round, on one backend. The verdicts
can be read against that round's own comparison.json.

  python experiment_compare.py <workspace> <round comparison folder> \
      --model opencode-go/deepseek-v4.1-flash --variant high --backends briefloop-native --repeat 3
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefloop.store import Store, uid  # noqa: E402
from briefloop.interactive_runtime import InteractiveRuntime  # noqa: E402
from briefloop.native_engine import NativeEngine  # noqa: E402
from briefloop.native_harness import NativeHarness  # noqa: E402
from briefloop.opencode_harness import OpencodeHarness  # noqa: E402
from experiment_ab import usage_totals, event_stats  # noqa: E402


def run_leg(source, saved, backend, model, variant, index):
    from briefloop import learning
    work = Path(tempfile.mkdtemp(prefix=f'bl-compare-{backend}-')).resolve()
    shutil.copytree(source, work / 'ws', ignore=shutil.ignore_patterns('jobs', 'server.json', '*.log'))
    store = Store(work / 'ws')
    harness = NativeHarness(store, NativeEngine())
    opencode = OpencodeHarness(store)
    runtime = InteractiveRuntime(store, backends={'briefloop-native': harness, 'opencode': opencode})
    comparisons = json.loads((saved / 'input.json').read_text(encoding='utf-8'))
    outcome = {'backend': backend, 'repeat': index, 'work': str(work)}
    try:
        jid = uid('job')
        payload = {'agent_backend': backend, 'runtime': {'model': model, 'model_variant': variant},
                   'role_models': {'evaluator': {'model': model, 'model_variant': variant}}}
        with store.tx() as c:
            # Written directly: the main-chain gate still refuses native learn jobs.
            c.execute("INSERT INTO jobs(id,kind,payload,status,result,error,created,updated) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
                      (jid, 'learn', json.dumps(payload), 'running', None, None))
        folder = store.root / 'jobs' / jid / 'comparison'
        t0 = time.monotonic()
        try:
            result = learning.compare(store, runtime, store.one('jobs', jid), comparisons, folder, backend)
            outcome.update(status='complete', verdicts={p['case_id']: p['verdict'] for p in result['pairs']},
                           regressions=sum(len(p.get('regressions') or []) for p in result['pairs']),
                           reason=result.get('reason', '')[:300])
        except Exception as exc:
            outcome.update(status='failed', error=str(exc)[:400])
        outcome['wall_seconds'] = round(time.monotonic() - t0, 1)
        outcome['usage'] = usage_totals(folder)
        outcome['events'] = event_stats(folder)
    finally:
        harness.close()
        try:
            opencode.close()
        except Exception:
            pass
    return outcome


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('workspace')
    parser.add_argument('saved', help='folder of an earlier round comparison (input.json, comparison.json)')
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--variant', default='high')
    parser.add_argument('--backends', default='briefloop-native,opencode')
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    saved = Path(args.saved).resolve()
    original = json.loads((saved / 'comparison.json').read_text(encoding='utf-8'))
    print(json.dumps({'original': {p['case_id']: p['verdict'] for p in original['pairs']}}), flush=True)
    for backend in args.backends.split(','):
        for i in range(args.repeat):
            print(json.dumps(run_leg(Path(args.workspace).resolve(), saved, backend.strip(),
                                     args.model, args.variant, i), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.exit(main())
