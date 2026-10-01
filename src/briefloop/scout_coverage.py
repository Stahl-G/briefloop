"""Committed Scout work and execution outcomes, separate from evidence gaps.

Allocated capacity is never a commitment. Only an explicit structured manifest
names work to account for. A valid joined result completes that work, not its
semantic questions. All state lives in the existing Store and round lifecycle.
"""
import json
from pathlib import Path

from .store import dump


def _key(run_id):
    return 'scout_coverage:' + run_id


def _read(connection, run_id):
    row = connection.execute('SELECT value FROM meta WHERE key=?', (_key(run_id),)).fetchone()
    return json.loads(row['value']) if row else {'rounds': {}}


def _save(connection, run_id, state):
    connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (_key(run_id), dump(state)))


def required(store, run_id):
    # Frozen by application job admission, never by a model-authored plan.
    from .research_plan import _owner_job
    job = _owner_job(store, run_id)
    return bool(job and json.loads(job['payload']).get('scout_coverage_version') == 1)


def _round(store, run_id, connection, round_id=None):
    from .research_plan import _read_plan, AdmissionError
    plan = _read_plan(connection, run_id)
    if plan is None:
        if required(store, run_id):
            raise AdmissionError('先冻结研究计划，再登记 Scout 分工', code='plan_missing')
        return 'legacy', {'index': 1, 'status': 'active'}
    identity = round_id or plan.get('current_round_id')
    info = plan.get('rounds', {}).get(identity)
    if not info or info.get('status') != 'active':
        raise AdmissionError('Scout 分工只能登记在当前已开启轮次', code='research_round_closed')
    return identity, info


def declare(store, run_id, tasks, *, round_id=None, directory=None):
    """Save the complete committed set; replays cannot silently drop a task."""
    if not isinstance(tasks, list):
        raise ValueError('scout_tasks 必须为数组；确无 Scout 工作时明确写 []')
    normalized = {}
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError('每个 Scout 分工必须为对象')
        slot = task.get('slot_id')
        assignment = task.get('assignment')
        if not isinstance(slot, str) or not slot.strip() or '/' in slot or '\\' in slot or slot in ('.', '..'):
            raise ValueError('Scout 分工需要有效 slot_id')
        if not isinstance(assignment, str) or not assignment.strip():
            raise ValueError('Scout 分工需要非空 assignment')
        value = task.get('result_file') or (str(Path(directory) / slot / 'result.json') if directory else None)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('Scout 分工需要 result_file 绝对路径')
        path = Path(value).resolve()
        if not path.is_relative_to(store.root) or path == store.root:
            raise ValueError('Scout 结果必须位于本工作区')
        if slot in normalized:
            raise ValueError('Scout 分工 slot_id 不可重复')
        normalized[slot] = {'slot_id': slot, 'assignment': assignment.strip(), 'result_file': str(path)}
    if len({task['result_file'] for task in normalized.values()}) != len(normalized):
        raise ValueError('不同 Scout 分工必须使用不同结果文件')
    with store.tx() as connection:
        identity, info = _round(store, run_id, connection, round_id)
        state = _read(connection, run_id)
        record = state['rounds'].setdefault(identity, {'index': info['index'], 'tasks': {}})
        old = record['tasks']
        if not set(old) <= set(normalized):
            raise ValueError('已承诺的 Scout 分工不能省略；请在收轮时明确说明 skipped 原因')
        for slot, task in normalized.items():
            if required(store, run_id):
                from .research_plan import _owner_job
                owner = _owner_job(store, run_id)
                job_root = store.root / 'jobs' / owner['id']
                scoped = [job_root / ('round-' + str(info['index'])) / slot / 'result.json',
                          store.root / 'research' / run_id / 'rounds' / str(info['index']) / slot / 'result.json']
                if info['index'] == 1:
                    scoped.append(job_root / slot / 'result.json')
                # Compare against canonical lexical paths: a symlink must not
                # turn a foreign run's artifact into this task's result.
                if task['result_file'] not in {str(path) for path in scoped}:
                    raise ValueError('Scout 结果路径必须绑定本任务、本轮与 slot_id；本轮可用：' + str(scoped[0]))
                if slot not in old and Path(task['result_file']).exists():
                    raise ValueError('新 Scout 分工不能绑定已有结果；先登记分工，再执行或明确说明跳过')
            if slot in old and any(old[slot][key] != value for key, value in task.items()):
                raise ValueError('已登记 Scout 分工不可改写；需要变更时使用新轮次')
            for previous_id, previous in state['rounds'].items():
                if previous_id != identity and any(t['result_file'] == task['result_file'] for t in previous['tasks'].values()):
                    raise ValueError('不同研究轮次不能复用同一 Scout 结果路径')
            old.setdefault(slot, {**task, 'status': 'planned', 'reason': '', 'history': []})
        record['declared'] = True
        _save(connection, run_id, state)
    return view(store, run_id)


def _transition(task, status, reason=''):
    if task['status'] == status and task.get('reason', '') == reason:
        return
    task['history'].append({'status': status, 'reason': reason})
    task.update(status=status, reason=reason)


def update(store, run_id, updates, *, round_id=None):
    """Host dispatch/failure receipts; completion is only recorded by join."""
    with store.tx() as connection:
        identity, _ = _round(store, run_id, connection, round_id)
        state = _read(connection, run_id)
        record = state['rounds'].get(identity, {})
        _updates(record, updates)
        _save(connection, run_id, state)
    return view(store, run_id)


def _updates(record, updates):
    if not isinstance(updates, list):
        raise ValueError('scout_outcomes 必须为数组')
    for update in updates:
        slot = update.get('slot_id') if isinstance(update, dict) else None
        task = record.get('tasks', {}).get(slot)
        if task is None:
            raise ValueError('Scout 状态引用了未承诺的分工：' + str(slot))
        status, reason = update.get('status'), update.get('reason', '')
        if status not in ('dispatched', 'failed', 'skipped') or not isinstance(reason, str):
            raise ValueError('Scout 状态只能为 dispatched/failed/skipped')
        if status in ('failed', 'skipped') and not reason.strip():
            raise ValueError('未完成 Scout 分工必须说明具体 reason')
        if task['status'] == 'complete':
            continue  # A late transport error cannot undo an admitted artifact.
        _transition(task, status, reason.strip())


def complete(store, run_id, paths, *, round_id=None):
    """Called only after all supplied Scout files pass the real join validator."""
    paths = {str(Path(path).resolve()) for path in paths}
    with store.tx() as connection:
        state = _read(connection, run_id)
        from .research_plan import _read_plan
        plan = _read_plan(connection, run_id) or {}
        admitted = round_id or plan.get('current_round_id') or ('legacy' if not plan else None)
        for identity, record in state['rounds'].items():
            if identity != admitted:
                continue
            if identity != 'legacy' and plan.get('rounds', {}).get(identity, {}).get('status') != 'active':
                continue  # Frozen closeout records are not rewritten by late files.
            for task in record['tasks'].values():
                if task['result_file'] in paths:
                    _transition(task, 'complete')
        _save(connection, run_id, state)


def closeout(store, run_id, connection, round_id, *, outcomes=None, enforce=False):
    from .research_plan import AdmissionError
    state = _read(connection, run_id)
    record = state['rounds'].get(round_id, {})
    if enforce and not record.get('declared'):
        raise AdmissionError('收轮前须用 set_scout_tasks 登记完整研究分工；确无 Scout 工作时明确提交 scout_tasks=[]', code='scout_plan_missing')
    if enforce:
        _check_saved_plan(store, run_id, record)
    if outcomes is not None:
        _updates(record, outcomes)
    pending = [task for task in record.get('tasks', {}).values() if task['status'] in ('planned', 'dispatched')]
    if pending:
        raise AdmissionError('仍有未交接的 Scout 分工：' + '、'.join(task['slot_id'] for task in pending)
            + '。先 join-scouts 接纳结果；无法继续时通过 scout_outcomes 明确 failed/skipped 与 reason，不要求追加检索。', code='scout_coverage_incomplete')
    if outcomes is not None:
        _save(connection, run_id, state)
    return record


def view(store, run_id):
    state = store.meta(_key(run_id)) or {'rounds': {}}
    records, gaps = [], []
    for identity, record in sorted(state['rounds'].items(), key=lambda pair: pair[1]['index']):
        for task in record['tasks'].values():
            value = {'round_id': identity, 'round_index': record['index'], **task}
            records.append(value)
            if task['status'] != 'complete':
                gaps.append({key: value[key] for key in ('round_id', 'round_index', 'slot_id', 'assignment', 'status', 'reason')})
    return {'scout_execution': records, 'execution_gaps': gaps}


def _check_saved_plan(store, run_id, record):
    # Only this explicit field is a binding contract. Never parse narrative lists.
    from .research_plan import _owner_job, AdmissionError
    job = _owner_job(store, run_id)
    path = store.root / 'jobs' / job['id'] / 'plan.json' if job else None
    if path is None or not path.is_file():
        return
    plan = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(plan, dict) or 'scout_tasks' not in plan:
        return
    tasks = plan['scout_tasks']
    committed = record.get('tasks', {})
    if (not isinstance(tasks, list) or len(tasks) != len(committed)
            or any(not isinstance(task, dict) or task.get('slot_id') not in committed
                   or not isinstance(task.get('assignment'), str)
                   or task['assignment'].strip() != committed[task['slot_id']]['assignment']
                   or (task.get('result_file') and str(Path(task['result_file']).resolve()) != committed[task['slot_id']]['result_file'])
                   for task in tasks)
            or len({task['slot_id'] for task in tasks}) != len(tasks)):
        raise AdmissionError('plan.json.scout_tasks 与已登记完整分工不一致；核对后用 set_scout_tasks 补齐，不能只派发计划的一部分', code='scout_plan_mismatch')
