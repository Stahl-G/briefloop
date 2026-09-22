"""A/B for the Scout: one research slot of a saved run, on one backend.

Each leg copies the workspace (without jobs), resets the run to its uploaded
sources with a fresh research budget, and runs scout.run - the product path -
on one slot assignment from the original job's plan.json. The result is read
against the original Scout's result for that slot (which sources it also
found) and checked for excerpt fidelity on every backend.

  python experiment_scout.py <workspace> --job JOB --slots scout-1,scout-2 \
      --model opencode-go/deepseek-v4.1-flash --variant low \
      --backends briefloop-native,opencode --budget 8,40,10 --repeat 1
"""
import argparse
import getpass
import hashlib
import json
import os
import shutil
import sqlite3
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


def frozen_policy(original_input):
    from briefloop.models import normalize_search_provider
    from briefloop.search_policy import resolve
    policy = original_input.get('search_policy')
    if policy is None:
        # Match search_policy.for_run: legacy jobs froze one exclusive channel.
        # resolve(primary='tavily') would add today's default host search.
        policy = {'primary_provider': normalize_search_provider(original_input.get('search_provider') or 'native'),
                  'native_search_enabled': False}
    return resolve(policy)


def validate_comparison(original_input, backends):
    from briefloop.search_policy import allowed
    if (len(set(backends)) > 1 and 'briefloop-native' in backends
            and original_input.get('requirements', {}).get('allow_web', True)
            and 'native' in allowed(frozen_policy(original_input))):
        raise ValueError('无法对齐本次搜索条件：冻结策略包含宿主自带搜索，BriefLoop Native 尚不具备该能力。'
                         '请使用双方都支持的受控渠道任务进行对照；实验不会自动删改原策略。')


def comparison_conditions(store, run_id, assignment, plan_path, backend):
    from briefloop.scout import task
    from briefloop.native_roles import READ_CHARS
    from briefloop.search_policy import allowed
    spec = task(store, run_id, assignment, plan_path=plan_path)
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    common = {'assignment': spec['assignment'], 'period': spec['period'], 'time_context': spec['time_context'],
              'created': spec['created'], 'research_handoff': spec['research_handoff'], 'sources': spec['sources'],
              'search_policy': spec['policy'], 'allow_web': spec['allow_web'], 'budget': spec['budget'],
              'reader_contract': spec['reader_contract'], 'contract': spec['contract'], 'skill': spec['skill'],
              'source_hashes': sorted(store.one('sources', sid)['hash'] for sid in store.source_ids(run_id)),
              'read_max_chars': READ_CHARS}
    channels = allowed(spec['policy']) if spec['allow_web'] else []
    actual = [c for c in channels if c != 'native'] if backend == 'briefloop-native' else channels
    return {'strategy_inputs_sha256': digest(common), 'search_policy': spec['policy'],
            'effective_search_channels': actual, 'unsupported_search_channels': sorted(set(channels) - set(actual)),
            'read_max_chars': READ_CHARS,
            'evidence_transport': 'incremental_runner' if backend == 'briefloop-native' else 'host_file',
            'scope': 'retrieval_strategy; tool interfaces and result admission still differ'}


def _urls(db, ids):
    from briefloop.research_budget import canonical_url
    connection = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    out = {}
    for sid in ids:
        row = connection.execute('SELECT url FROM sources WHERE id=?', (sid,)).fetchone()
        if row and row[0]:
            try:
                out[sid] = canonical_url(row[0])
            except ValueError:
                pass
    connection.close()
    return out


def run_leg(source, job_id, slot, backend, model, variant, budget, index, *, expected_conditions=None):
    from briefloop import scout
    from briefloop.models import ScoutResult
    from briefloop.research_budget import snapshot
    from briefloop.scout_tools import evidence_errors
    original_job = source / 'jobs' / job_id
    plan = json.loads((original_job / 'plan.json').read_text(encoding='utf-8'))
    assignment = next(a for a in plan['scout_assignments'] if a.get('slot_id') == slot)
    assignment = {key: value for key, value in assignment.items()
                  if key not in ('directory', 'result_file', 'schema_path', 'scout_contract_path')}
    work = Path(tempfile.mkdtemp(prefix=f'bl-scout-{backend}-')).resolve()
    shutil.copytree(source, work / 'ws', ignore=shutil.ignore_patterns('jobs', 'server.json', '*.log'))
    store = Store(work / 'ws')
    run_id = json.loads(store.one('jobs', job_id)['payload'])['run_id']
    with store.tx() as c:
        # The run as it started: uploaded sources only, a fresh shared budget.
        c.execute('DELETE FROM run_sources WHERE run_id=?', (run_id,))
        c.execute('DELETE FROM meta WHERE key=?', ('research_budget:' + run_id,))
        requirements = json.loads(c.execute('SELECT requirements FROM runs WHERE id=?', (run_id,)).fetchone()['requirements'])
        if budget:
            requirements['research_budget'] = dict(zip(('search_requests', 'candidate_urls', 'source_pages'), budget))
        c.execute('UPDATE runs SET requirements=? WHERE id=?', (json.dumps(requirements, ensure_ascii=False), run_id))
    initial = set(store.source_ids(run_id))
    harness = NativeHarness(store, NativeEngine())
    opencode = OpencodeHarness(store)
    runtime = InteractiveRuntime(store, backends={'briefloop-native': harness, 'opencode': opencode})
    outcome = {'backend': backend, 'slot': slot, 'repeat': index, 'work': str(work)}
    try:
        jid = uid('job')
        # The newest generate job of a run freezes its search policy (search_policy.for_run),
        # so this job carries the original one: its snapshot, else its frozen provider.
        original_input = json.loads((original_job / 'input.json').read_text(encoding='utf-8'))
        policy = frozen_policy(original_input)
        payload = {'run_id': run_id, 'agent_backend': backend, 'search_policy': policy,
                   'runtime': {'model': model, 'model_variant': variant}}
        with store.tx() as c:
            # Written directly: the main-chain gate still refuses native generate jobs.
            c.execute("INSERT INTO jobs(id,kind,payload,status,result,error,created,updated) VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))",
                      (jid, 'generate', json.dumps(payload), 'running', None, None))
        folder = store.root / 'jobs' / jid / slot
        outcome['conditions'] = comparison_conditions(store, run_id, assignment, original_job / 'plan.json', backend)
        t0 = time.monotonic()
        try:
            if expected_conditions is not None and any(
                    outcome['conditions'][key] != expected_conditions[key]
                    for key in ('strategy_inputs_sha256', 'effective_search_channels')):
                raise ValueError('对照条件与首臂不一致，未调用模型；请核对材料、要求、渠道与预算快照')
            scout.run(store, runtime, store.one('jobs', jid), run_id, assignment, folder, backend,
                      plan_path=original_job / 'plan.json')
            outcome['status'] = 'complete'
        except Exception as exc:
            outcome.update(status='failed', error=str(exc)[:400])
        outcome['wall_seconds'] = round(time.monotonic() - t0, 1)
        outcome['usage'] = usage_totals(folder)
        outcome['events'] = event_stats(folder)
        outcome['budget_used'] = snapshot(store, run_id)['used']
        path = folder / 'result.json'
        if path.is_file():
            try:
                result = ScoutResult.model_validate(json.loads(path.read_text(encoding='utf-8-sig')))
            except ValueError as exc:
                outcome['result_error'] = str(exc)[:300]
            else:
                ids = {item.source_id for item in result.sources}
                original = json.loads((original_job / slot / 'result.json').read_text(encoding='utf-8-sig'))
                original_ids = {item['source_id'] for item in original['sources']}
                mine = _urls(store.db, ids)
                theirs = _urls(source / 'briefloop.db', original_ids)
                outcome.update({
                    'evidence_items': len(result.sources), 'sources': len(ids),
                    'acquired': len(ids - initial), 'gaps': len(result.gaps),
                    'evidence_errors': len(evidence_errors(store, run_id, result)),
                    'original_sources': len(original_ids),
                    'shared_with_original': len((ids & original_ids) | ({sid for sid, url in mine.items() if url in set(theirs.values())})),
                })
                shutil.copy(path, work / f'result-{slot}.json')
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
    parser.add_argument('--job', required=True, help='the original generate job whose plan.json holds scout_assignments')
    parser.add_argument('--slots', required=True)
    parser.add_argument('--model', default='opencode-go/deepseek-v4.1-flash')
    parser.add_argument('--variant', default='low')
    parser.add_argument('--backends', default='briefloop-native,opencode')
    parser.add_argument('--budget', default='', help='search_requests,candidate_urls,source_pages per leg')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--prompt-key', action='store_true', help='在终端隐藏输入 OPENCODE_API_KEY，只放入本进程环境')
    parser.add_argument('--output', type=Path, help='保存到新 JSONL 文件；拒绝覆盖已有结果')
    args = parser.parse_args()
    budget = [int(x) for x in args.budget.split(',')] if args.budget else None
    backends = [backend.strip() for backend in args.backends.split(',')]
    original_input = json.loads((Path(args.workspace) / 'jobs' / args.job / 'input.json').read_text())
    try:
        validate_comparison(original_input, backends)
    except ValueError as exc:
        parser.error(str(exc))
    if args.prompt_key:
        if not sys.stdin.isatty():parser.error('--prompt-key 只能在交互终端使用')
        if not args.model.startswith('opencode-go/'):
            parser.error('--prompt-key 用于 opencode-go 模型；其他提供商使用对应环境变量')
        key = getpass.getpass('OPENCODE_API_KEY（隐藏输入，不保存）：').strip()
        if not key:parser.error('API Key 不能为空')
        os.environ['OPENCODE_API_KEY'] = key
        del key
    output = args.output.open('x', encoding='utf-8') if args.output else None
    expected = {}
    try:
        for backend in backends:
            for slot in args.slots.split(','):
                for i in range(args.repeat):
                    row = run_leg(Path(args.workspace).resolve(), args.job, slot.strip(), backend.strip(),
                                  args.model, args.variant, budget, i, expected_conditions=expected.get(slot.strip()))
                    expected.setdefault(slot.strip(), row['conditions'])
                    line = json.dumps(row, ensure_ascii=False)
                    if output:
                        output.write(line + '\n'); output.flush()
                    print(line, flush=True)
    finally:
        if output:output.close()
        if args.prompt_key:os.environ.pop('OPENCODE_API_KEY', None)


if __name__ == '__main__':
    sys.exit(main())
