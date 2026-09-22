"""A/B for the learning roles: WikiSkill maintainer then proposer, one round.

Each leg copies the workspace, builds the learning experience from the named
feedback exactly as learning.learn does (_experience), starts a WikiSkill study
and runs learning._role for the maintainer and then the proposer on one
backend — the product path: a coordinator plus native subagents on hosts, one
native session per step on briefloop-native. Trials and the pairwise
comparison (which need report generation) are not part of this experiment.

  python experiment_learning.py <workspace> --feedback ID [ID ...] \
      --model opencode-go/deepseek-v4.1-flash --variant high \
      --backends briefloop-native,opencode --repeat 1
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


def run_leg(source, feedback_ids, backend, model, variant, index):
    from briefloop import learning
    from wikiskill import feedback_loop
    work = Path(tempfile.mkdtemp(prefix=f'bl-learn-{backend}-')).resolve()
    shutil.copytree(source, work / 'ws', ignore=shutil.ignore_patterns('jobs', 'server.json', '*.log'))
    store = Store(work / 'ws')
    engine = NativeEngine()
    harness = NativeHarness(store, engine)
    opencode = OpencodeHarness(store)
    runtime = InteractiveRuntime(store, backends={'briefloop-native': harness, 'opencode': opencode})
    outcome = {'backend': backend, 'repeat': index, 'work': str(work)}
    try:
        jid = uid('job')
        payload = {'feedback_ids': feedback_ids, 'agent_backend': backend, 'targets': ['analyst'],
                   'skill_id': None, 'k': 1,
                   'runtime': {'model': model, 'model_variant': variant},
                   'role_models': {role: {'model': model, 'model_variant': variant} for role in ('maintainer', 'proposer')}}
        with store.tx() as c:
            # Written directly: the main-chain gate still refuses native learn jobs.
            c.execute("INSERT INTO jobs(id,kind,payload,status,result,error,created,updated) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
                      (jid, 'learn', json.dumps(payload), 'running', None, None))
        job = store.one('jobs', jid)
        feedback, _cases = learning._experience(store, job)
        root = store.root / 'jobs' / jid
        root.mkdir(parents=True, exist_ok=True)
        study = root / 'study'
        feedback_loop.begin(study, feedback=feedback, rounds=1, runtime=backend)
        store.set_meta('last_study', str(study))
        steps = []
        for phase in ('maintainer', 'proposer'):
            t0 = time.monotonic()
            step = {'phase': phase}
            try:
                learning._role(store, runtime, job, study, 1, phase)
                step['status'] = 'complete'
            except Exception as exc:
                step.update(status='failed', error=str(exc)[:400])
            step['wall_seconds'] = round(time.monotonic() - t0, 1)
            stage = next(iter(sorted(root.glob(f'1-{phase}-*'))), None)
            if stage:
                step['usage'] = usage_totals(stage)
                step['events'] = event_stats(stage)
            steps.append(step)
            if step['status'] != 'complete':
                break
        state = feedback_loop.work(study)
        patterns = state.get('patterns') or {}
        candidate = state.get('candidate') or {}
        outcome.update({
            'status': 'complete' if all(s['status'] == 'complete' for s in steps) and len(steps) == 2 else 'failed',
            'steps': steps, 'phase_after': state.get('phase'),
            'patterns': len(patterns),
            'pattern_names': list(patterns)[:12],
            'proposal': ('no_action' if candidate.get('no_action') else
                         ('skill' if candidate.get('skill') else None)),
            'proposal_note': (candidate.get('note') or '')[:300],
            'skill_chars': len((study / candidate['skill']['file']).read_text(encoding='utf-8'))
                           if candidate.get('skill') else 0,
            'wall_seconds': round(sum(s['wall_seconds'] for s in steps), 1),
        })
        (work / 'patterns.json').write_text(json.dumps(patterns, ensure_ascii=False, indent=1), encoding='utf-8')
        if candidate.get('skill'):
            shutil.copy(study / candidate['skill']['file'], work / 'candidate-SKILL.md')
    except Exception as exc:
        outcome.update(status='failed', error=str(exc)[:500])
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
    parser.add_argument('--feedback', nargs='+', required=True)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--variant', default='high')
    parser.add_argument('--backends', default='briefloop-native,opencode')
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    for backend in args.backends.split(','):
        for i in range(args.repeat):
            print(json.dumps(run_leg(Path(args.workspace).resolve(), args.feedback, backend.strip(),
                                     args.model, args.variant, i), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.exit(main())
