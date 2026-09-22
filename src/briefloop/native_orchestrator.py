"""Native main-agent tools. Store/Worker remain the report control plane.

The model chooses tasks, rounds and writing instructions. Children use the same
frozen job and role implementations as standalone executions; no second queue,
source ledger, review controller or budget is introduced here.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

from .store import dump
from .native_roles import ToolError, _atomic, _json_result

READ_ACTIONS = {'inspect', 'capabilities', 'templates', 'workflows', 'profile_read',
                'company_read', 'read_report', 'read_run_report', 'review_status',
                'research_status', 'reconciliation_candidates', 'reconciliation_read', 'evidence_read'}
RUN_ACTIONS = {'set_reader_contract', 'freeze_research_plan', 'begin_research_round',
               'finish_research_round', 'reconciliation_save', 'evidence_span',
               'claim_create', 'claim_bind', 'company_update', 'company_review_complete'}
CHAT_ACTIONS = READ_ACTIONS | {'generate', 'assess', 'comment', 'export_word', 'profile_update', 'company_config', 'company_resolve', 'revise_document', 'learn', 'template_import', 'template_rebuild', 'import_word_revision'}


def _save(path, value):
    _atomic(Path(path), dump(value))


def _folder(config):
    return Path(config['packet_root']).parent


def _alive(store, config):
    if config['_harness'].cancel_requested(config['session_id']):
        raise ToolError('本轮已停止，未启动新操作')
    if config.get('_active_attempt') and config['_harness'].chat.session(config['session_id']).get('turn_id') != config['_active_attempt']:
        raise ToolError('本次执行已结束，未接纳迟到结果')
    if config.get('job_id') and store.one('jobs', config['job_id'])['status'] in ('cancelled', 'interrupted'):
        raise ToolError('任务已停止，已保存结果保留')


def action(store, config, args):
    """Expose only role-approved business operations, never shell or raw files."""
    from .chat_tools import workspace_action
    request = dict(args.get('request') or {})
    name = request.get('action')
    allowed = CHAT_ACTIONS if config.get('native_role') == 'chat' else READ_ACTIONS | RUN_ACTIONS
    if config.get('task_kind') == 'fact_check':
        allowed = {'evidence_span', 'read_report', 'research_status'}
    if name not in allowed:
        raise ToolError('本阶段不提供此操作：' + str(name))
    _alive(store, config)
    if name not in READ_ACTIONS and config.get('permission') == 'read-only' and config.get('native_role') == 'chat':
        raise ToolError('本轮为只读对话，请在权限中选择读写工作区后重试')
    run_id = config.get('run_id')
    if run_id:
        if request.get('run_id', run_id) != run_id:
            raise ToolError('操作必须属于本轮报告')
        request['run_id'] = run_id
        for field in ('version_id','base_version'):
            if request.get(field) and store.one('briefs', request[field])['run_id'] != run_id:
                raise ToolError('稿件不属于本轮报告')
        source = (request.get('evidence') or request.get('fact') or {}).get('source_id')
        if source and source not in store.source_ids(run_id):
            raise ToolError('来源不属于本轮报告')
    if name == 'revise_document':
        if not isinstance(request.get('editor_document'), dict):
            raise ToolError('revise_document 需要 base_version 和完整 editor_document 对象')
        saved=store.revise(request['base_version'], editor_document=request['editor_document'], citations=request.get('citations'), author='agent')
        return _json_result(store.brief_view(saved['id']))
    if name == 'generate':
        if config.get('discuss_only'):
            raise ToolError('/discuss 只整理要求，不启动报告')
        req = dict(request.get('requirements') or {})
        # Preserve the actual user request as context, not model-rewritten prose.
        messages = store.rows('SELECT text FROM chat_messages WHERE id=? AND session_id=? AND role=?',
                              (config['attempt_id'], config['session_id'], 'user'))
        if messages:
            req['raw_input'] = messages[0]['text']
        if req.get('allow_web') and not config.get('allow_web'):
            raise ToolError('本轮未允许联网，不能创建联网报告')
        req['allow_web'] = bool(req.get('allow_web', False) and config.get('allow_web'))
        request['requirements'] = req
        request['runtime'] = {'agent_backend': 'briefloop-native', 'model': config['model'],
                              'model_variant': config.get('variant') or config.get('model_variant') or config.get('effort')}
        request['session_id'] = config['session_id']
        # Replay an accepted tool invocation without enqueuing another paid job.
        key = 'native_submit:' + config['session_id'] + ':' + config['attempt_id']
        fingerprint = hashlib.sha256(dump(request).encode()).hexdigest()
        from .external_requests import _RequestStore
        with store.tx() as connection:
            view = _RequestStore(store, connection)
            saved = view.meta(key)
            if saved:
                if saved['fingerprint'] != fingerprint:
                    raise ToolError('本回合已提交报告，请等待该任务；新报告需新一条用户消息')
                result = saved['result']
            else:
                from .models import Settings, Requirements, runtime_fields
                settings = Settings.model_validate({**view.settings(), **request['runtime']})
                run = view.create_run(Requirements.model_validate(req).model_dump(), request.get('source_ids', []),
                                      research_protocol='quality_v1', agent_backend='briefloop-native')
                job = view.enqueue('generate', {'run_id': run['id'], 'agent_backend': 'briefloop-native',
                                                'runtime': runtime_fields(settings.model_dump(), 'briefloop-native')})
                # Attach the owning chat only after enqueue's notification hook.
                # That hook initializes ChatStore with executescript, which must
                # not commit this still-open admission transaction.
                payload = {**json.loads(job['payload']), 'session_id': config['session_id']}
                connection.execute('UPDATE jobs SET payload=? WHERE id=?', (dump(payload), job['id']))
                result = {'job_id': job['id'], 'run_id': run['id'], 'status': 'queued',
                          'accepted_requirements': {key: value for key, value in json.loads(run['requirements']).items()
                              if key in ('title', 'target_minutes', 'hard_timeout_minutes', 'research_budget', 'key_questions',
                                         'writing_preferences', 'target_words', 'max_words', 'period', 'search_policy')}}
                view.set_meta(key, {'fingerprint': fingerprint, 'result': result})
        store.wake_jobs()
        from .task_notify import notify
        notify(store, store.one('jobs', result['job_id']), 'queued')
        return _json_result(result)
    result = workspace_action(store, request)
    if name == 'reconciliation_save' and config.get('packet_root'):
        _save(_folder(config) / 'reconciliation.json', result)
    return _json_result(result)


def read_source(store, config, args):
    from .scout_tools import read_source as read
    sid = args.get('source_id')
    if config.get('run_id') and sid not in store.source_ids(config['run_id']):
        raise ToolError('来源不属于本轮报告')
    return {'content': [{'type': 'text', 'text': read(store, sid,
        start_line=args.get('start_line') or 1, end_line=args.get('end_line'),
        max_chars=min(int(args.get('max_chars') or 20000), 60000))}]}


def save_plan(store, config, args):
    from .deliverable_spec import save_reader_contract
    _alive(store, config)
    plan = args.get('plan')
    if not isinstance(plan, dict) or not str(plan.get('summary') or '').strip():
        raise ToolError('计划须包含 summary 和 reader_contract')
    contract = save_reader_contract(store, config['run_id'], plan.get('reader_contract'))
    plan = {**plan, 'reader_contract': contract}
    _save(_folder(config) / 'plan.json', plan)
    _save(Path(config['packet_root']) / 'plan.json', plan)
    return _json_result({'saved': True, 'reader_contract': contract})


def _child(store, config, directory, callback):
    """Own child cancellation without killing the shared engine or other jobs."""
    from .interactive_runtime import InteractiveRuntime
    harness = config['_harness']
    runtime = InteractiveRuntime(store, backends={'briefloop-native': harness})
    identity = str(Path(directory).relative_to(store.root))
    role = 'Analyst' if Path(directory).name == 'analyst' else 'Scout'
    def progress(status):
        with harness._lock:
            path = _folder(config) / 'agents.json'
            records = json.loads(path.read_text()) if path.exists() else {'agents': []}
            records['agents'] = [a for a in records['agents'] if a['agent_id'] != identity]
            records['agents'].append({'agent_id': identity, 'role': role, 'status': status})
            _save(path, records)
    with harness.child_runtime(config['session_id'], runtime):
        _alive(store, config)
        progress('running')
        try:
            result = callback(runtime)
            progress('completed')
            return result
        except Exception:
            progress('failed')
            raise


def run_scouts(store, config, args):
    from .scout import run
    from .scout_tools import join_scouts
    from .research_plan import frozen
    from .runtime import _research_handoff
    folder = _folder(config)
    plan_path = folder / 'plan.json'
    if not plan_path.exists():
        raise ToolError('先用 save_plan 保存读者契约和研究分工')
    _alive(store, config)
    job = store.one('jobs', config['job_id'])
    frozen_plan = frozen(store, config['run_id'])
    current = ((frozen_plan or {}).get('rounds') or {}).get((frozen_plan or {}).get('current_round_id'))
    if current and current.get('status') != 'active': current = None
    if frozen_plan and current is None:
        raise ToolError('研究轮次已结束；确有缺口时先 begin_research_round')
    round_id = str((current or {}).get('index', 1))
    data = json.loads((folder / 'input.json').read_text())
    limit = data['max_parallel']
    breadth = int((frozen_plan or {}).get('structure', {}).get('breadth') or limit)
    tasks = args.get('tasks')
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= breadth:
        raise ToolError(f'本轮可安排 1–{breadth} 个 Scout，最多同时执行 {limit} 个')
    ids = [t.get('slot_id') for t in tasks if isinstance(t, dict)]
    if len(ids) != len(tasks) or len(set(ids)) != len(ids) or any(i not in {f'scout-{n}' for n in range(1, breadth+1)} for i in ids):
        raise ToolError('slot_id 须使用本任务 scout-1…scout-N，且不重复')
    results = []
    def execute(task):
        path = folder / ('round-' + round_id) / task['slot_id']
        path.mkdir(parents=True, exist_ok=True)
        assignment = path / 'assignment.json'
        if assignment.exists() and json.loads(assignment.read_text()) != task:
            raise ToolError('已有槽位任务不同；复用原分工，或结束本轮后在下一轮安排新任务')
        _save(assignment, task)
        store.event(job['id'], 'native_child', {'role': 'scout', 'slot_id': task['slot_id'], 'status': 'running'})
        try:
            _child(store, config, path, lambda runtime: run(store, runtime, job, config['run_id'], task,
                path, 'briefloop-native', plan_path=plan_path,
                research_handoff=_research_handoff(store, config['run_id'], frozen_plan)))
            return {'slot_id': task['slot_id'], 'status': 'complete', 'file': str(path / 'result.json')}
        except Exception as exc:
            return {'slot_id': task['slot_id'], 'status': 'failed', 'error': str(exc)}
        finally:
            store.event(job['id'], 'native_child', {'role': 'scout', 'slot_id': task['slot_id'], 'status': 'settled'})
    with ThreadPoolExecutor(max_workers=min(limit, len(tasks))) as pool:
        results = list(pool.map(execute, tasks))
    _alive(store, config)
    paths = sorted(folder.glob('round-*/scout-*/result.json'))
    # All rounds remain available; replayed slots are not duplicated.
    joined = join_scouts(store, paths, run_id=config['run_id']) if paths else {'sources': [], 'gaps': []}
    joined['gaps'] += [r['slot_id'] + ' 未完成：' + r['error'] for r in results if r['status'] != 'complete']
    _save(folder / 'research.json', joined)
    _save(Path(config['packet_root']) / 'research.json', joined)
    return _json_result({'tasks': results, 'research_file': 'research.json', 'sources': len(joined['sources']), 'gaps': joined['gaps']})


def save_handoff(store, config, args):
    from .research_plan import frozen
    from .scout_tools import check_handoff
    from .research_budget import snapshot
    _alive(store, config)
    plan = frozen(store, config['run_id']) or {}
    opened = [r for r in plan.get('rounds', {}).values() if r.get('status') != 'pending']
    if not opened:
        raise ToolError('没有已经开始的研究轮次')
    index = max(r['index'] for r in opened)
    value = check_handoff(store, config['run_id'], args.get('handoff'))
    value['budget'] = snapshot(store, config['run_id'])['remaining']
    path = store.root / 'research' / config['run_id'] / 'rounds' / str(index) / 'handoff.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(path, value)
    return _json_result({'round_index': index, 'saved': True, 'unverified': value['unverified']})


def write_report(store, config, args):
    from .analyst import run
    from .company_context import require_review
    _alive(store, config)
    folder = _folder(config)
    require_review(store, store.one('runs', config['run_id']))
    if not (folder / 'plan.json').exists() or not (folder / 'research.json').exists():
        raise ToolError('先保存计划、读取材料并完成 Scout 交接')
    plan = json.loads((folder / 'plan.json').read_text())
    research = json.loads((folder / 'research.json').read_text())
    if args.get('instructions'):
        plan = {**plan, 'writing_instructions': args['instructions']}
    job = store.one('jobs', config['job_id'])
    # One durable writer stage, re-entered after a failure rather than resampled.
    path = folder / 'analyst'
    support = {}
    if (folder / 'reconciliation.json').exists():
        from .reconciliation import read
        saved = json.loads((folder / 'reconciliation.json').read_text())
        support['reconciliation.json'] = read(store, config['run_id'], saved['id'])
        if support['reconciliation.json'].get('stale'):
            raise ToolError('来源陈述已变化，请先更新写前对照')
        plan = {**plan, 'reconciliation_id': saved['id']}
    result = _child(store, config, path, lambda runtime: run(store, runtime, job, config['run_id'], path,
        'briefloop-native', plan=plan, research=research, support=support, publish=False))
    with config['_harness']._lock:
        _alive(store, config)
        value = json.loads((path / 'draft.json').read_text())
        if support:
            value['reconciliation_id'] = support['reconciliation.json']['id']
        _save(folder / 'draft.json', value)
    return _json_result(result)


def finish(store, config, args):
    _alive(store, config)
    kind = config['task_kind']
    if kind == 'company_review':
        from .company_context import require_review
        result = require_review(store, store.one('runs', config['run_id']))
    else:
        from .models import BriefDraft
        path = _folder(config) / 'draft.json'
        if not path.exists():
            raise ToolError('Analyst 尚未保存草稿，进度文字不能代替报告')
        BriefDraft.model_validate_json(path.read_text())
        result = {'draft_saved': True, 'review_status': 'pending', 'message': '草稿已保存，后续检查由任务控制器独立执行'}
    return {**_json_result(result), 'settle': dump(result)}


def revision_metadata(store, config, args):
    # The runtime performs final admission against the actual published version.
    # Check coverage now so the writer can repair without rewriting its draft.
    original = json.loads((_folder(config) / 'input.json').read_text())
    findings = original.get('review_findings', [])
    responses = args.get('responses')
    expected = {f['id'] for f in findings}
    if not isinstance(responses, list) or len(responses) != len(expected) or {r.get('finding_id') for r in responses} != expected:
        raise ToolError('responses 必须逐项对应本轮 review_findings，每项恰好一次；没有发现时提交 []')
    for r in responses:
        if r.get('action') not in ('corrected', 'removed', 'disagree') or not str(r.get('reason') or '').strip():
            raise ToolError('每项处理说明需要 action 和具体 reason')
    bindings = args.get('bindings', [])
    if not isinstance(bindings, list):
        raise ToolError('bindings 必须是数组；没有已登记主张时提交 []')
    from .evidence import record
    for binding in bindings:
        if not isinstance(binding, dict) or any(not isinstance(binding.get(k), str) or not binding[k].strip()
                                               for k in ('claim_id', 'block_id', 'quote')):
            raise ToolError('bindings 每项需要 claim_id/block_id/quote；数字定位请放 draft.number_bindings，没有已登记主张时提交 []')
        if record(store, 'claims', binding['claim_id'])['run_id'] != config['run_id']:
            raise ToolError('bindings 只能引用本报告已登记的主张')
    _save(_folder(config) / 'responses.json', responses)
    _save(_folder(config) / 'revision_bindings.json', bindings)
    return _json_result({'saved': True})


def fact_submit(store, config, args):
    from .fact_check import submit_result
    if args.get('version_id') != config['version_id']:
        raise ToolError('核查必须绑定本轮指定版本')
    result = submit_result(store, config['run_id'], args, job_id=config['job_id'])
    _save(_folder(config) / 'result.json', args)
    return {**_json_result(result), 'settle': dump(result)}


def template_submit(store, config, args):
    from .templates import prepare
    _alive(store, config)
    row = prepare(store, config['template_id'], args)
    _save(_folder(config) / 'template.json', args)
    result = {'template_id': row['id'], 'status': row['status']}
    return {**_json_result(result), 'settle': dump(result)}


def metadata_submit(store, config, args):
    original = json.loads((Path(config['packet_root']) / 'input.json').read_text())
    if any(args.get(k) != original[k] for k in ('version_id', 'brief_hash')):
        raise ToolError('元数据必须绑定本次既有版本与 hash')
    bindings, responses = args.get('bindings'), args.get('responses')
    if not isinstance(bindings, list) or not isinstance(responses, list):
        raise ToolError('bindings/responses 必须是数组')
    from .evidence import blocks, node_text
    claims = {c['id'] for c in original.get('candidate_claims', [])}
    nodes = blocks(original['document'])
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get('claim_id') not in claims:
            raise ToolError('claim_id 必须来自 input.candidate_claims；source_id 不是 claim_id。candidate_claims 为空时 bindings 必须为 []，数字定位仍保留在既有稿件 number_bindings。')
        node = nodes.get(binding.get('block_id'))
        quote = binding.get('quote')
        if node is None or not isinstance(quote, str) or not quote or node_text(node).count(quote) != 1:
            raise ToolError('绑定必须指向 input.document 的真实 blockId 和唯一原句')
    expected = {f['id'] for f in original.get('findings', [])}
    if any(not isinstance(r, dict) for r in responses) or len(responses) != len(expected) or {r.get('finding_id') for r in responses} != expected:
        raise ToolError('responses 必须逐项对应 input.findings；为空时为 []')
    if any(r.get('action') not in ('corrected', 'removed', 'disagree') or not str(r.get('reason') or '').strip() for r in responses):
        raise ToolError('处理说明需要有效 action 和具体 reason')
    _save(_folder(config) / 'metadata.json', args)
    return {**_json_result({'saved': True}), 'settle': dump({'saved': True})}


def connector(store, config, args):
    tasks = getattr(config['_harness'], 'connector_tasks', None)
    if tasks is None or not tasks.has_binding(config['job_id']):
        raise ToolError('本报告未授权连接器')
    access = tasks.access(config['job_id'])
    try:
        return _json_result(tasks.dispatch(access['access_token'], args.get('request')))
    finally:
        tasks.release_access(access['access_token'])


def spec(name, handler, description, properties=None, required=(), **flags):
    return {'name': name, 'label': description, 'description': description, 'guide': description,
            'parameters': {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False},
            'handler': handler, **flags}


OBJ = {'type': 'object'}
TEXT = {'type': 'string'}
ACTION_TOOL = spec('workspace_action', action, '调用当前角色获准的工作区业务接口，request 包含 action 及该操作参数；不支持 shell 或任意路径写入。', {'request': OBJ}, ('request',), sequential=True)
SOURCE_TOOL = spec('source_read', read_source, '读取已登记来源，按行分页，最多 60000 字符。',
    {'source_id': TEXT, 'start_line': {'type': 'integer', 'minimum': 1}, 'end_line': {'type': 'integer', 'minimum': 1}, 'max_chars': {'type': 'integer', 'minimum': 1}}, ('source_id',))
METADATA_TOOL = spec('save_revision_metadata', revision_metadata, '保存本轮 review_findings 的逐项处理说明 responses 和已登记主张绑定 bindings；bindings 项须有 claim_id/block_id/quote，不是数字定位。没有已登记主张或发现时相应数组为 []。',
    {'responses': {'type': 'array', 'items': OBJ}, 'bindings': {'type': 'array', 'items': {'type':'object', 'required':['claim_id','block_id','quote'], 'properties':{'claim_id':TEXT,'block_id':TEXT,'quote':TEXT}}}}, ('responses',), sequential=True)


def tools(role, config):
    if role == 'chat':
        return [ACTION_TOOL, SOURCE_TOOL]
    if role == 'fact_checker':
        from .native_roles import SCOUT_READ_TOOLS, _scout_web_tools
        return [*SCOUT_READ_TOOLS, *_scout_web_tools(config.get('search_channels') or []), ACTION_TOOL,
                spec('submit_fact_check', fact_submit, '提交四态核查候选，结构遵循 input.json 的结果契约；运行器校验并结束本阶段。',
                     {'version_id': TEXT, 'stage_id': TEXT, 'as_of': TEXT, 'selection': OBJ,
                      'candidates': {'type': 'array', 'items': OBJ}, 'execution': OBJ},
                     ('version_id', 'stage_id', 'selection', 'candidates', 'execution'), settles=True)]
    if config['task_kind'] == 'prepare_template':
        return [spec('submit_template', template_submit, '提交模板主章节、保留块、正文样式索引及可变字段；程序核验并生成模板。',
            {'sections': {'type':'array','items':OBJ}, 'keep_blocks': {'type':'array','items':{'type':'integer'}},
             'paragraph_index': {'type':'integer'}, 'fields': {'type':'array','items':OBJ}}, ('sections','paragraph_index'), settles=True)]
    if config['task_kind'] == 'repair_revision_metadata':
        return [spec('submit_metadata', metadata_submit, '提交固定稿件版本的 bindings/responses 元数据，不修改正文。',
            {'version_id': TEXT, 'brief_hash': TEXT, 'bindings': {'type': 'array', 'items': OBJ}, 'responses': {'type': 'array', 'items': OBJ}},
            ('version_id', 'brief_hash', 'bindings', 'responses'), settles=True)]
    common = [ACTION_TOOL, SOURCE_TOOL]
    if config['task_kind'] == 'company_review':
        from .native_roles import _scout_web_tools
        common += _scout_web_tools(config.get('search_channels') or []) if config.get('allow_web') else []
    else:
        common += [
            spec('save_plan', save_plan, '保存研究计划 summary 和 reader_contract；先读取 reader_contract.schema.json。', {'plan': OBJ}, ('plan',), sequential=True),
            spec('run_scouts', run_scouts, '按当前研究轮次并行执行 Scout；tasks 各含 slot_id 和具体 assignment，冻结模型、搜索策略及共享预算。等待实际结果，可停止；相同槽位恢复原任务。',
                 {'tasks': {'type': 'array', 'items': OBJ}}, ('tasks',), sequential=True, long_running=True),
            spec('save_research_handoff', save_handoff, '保存轮间交接：handoff 含 learnings（summary、可选 source_id/locator）、follow_ups、covered、open_questions 数组；缺少引用保留待证，预算由运行器记录。', {'handoff': OBJ}, ('handoff',), sequential=True),
            spec('write_report', write_report, '将已保存计划、全部研究结果和来源交给独立 Analyst 写稿；沿用冻结主链模型，不自行评分。',
                 {'instructions': TEXT}, sequential=True, long_running=True),
            spec('connector_material', connector, '读取本报告明确授权的 MCP 材料；request 使用既有 read/call/status/receipt 协议。', {'request': OBJ}, ('request',)),
        ]
    return [*common, spec('finish_task', finish, '确认本阶段实际保存的成果；不把写稿完成称为审阅通过。', settles=True)]


def prepare(store, job, folder, prompt):
    """Adapt existing Worker stages into packets without changing their policy."""
    from .search_policy import for_run, allowed
    from .websearch import MANAGED_PROVIDERS
    payload = json.loads(job['payload'])
    if job['kind'] == 'prepare_template':
        from .templates import template, _path
        row = template(store, payload['template_id'])
        root = folder / 'packet'; root.mkdir(exist_ok=True)
        inventory = _path(store, row, 'inventory.json').read_text()
        for image in _path(store, row, 'inventory.json').parent.rglob('*'):
            if image.is_file() and image.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and image.resolve().is_relative_to(_path(store,row,'inventory.json').parent):
                target = root / image.relative_to(_path(store,row,'inventory.json').parent)
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(image.read_bytes())
        inventory = inventory.replace(str(_path(store,row,'inventory.json').parent) + '/', '')
        (root/'inventory.json').write_text(inventory)
        prompt = prompt.replace(str(_path(store,row,'inventory.json')), 'inventory.json')
        return {'role':'orchestrator','task_kind':'prepare_template','job_id':job['id'],'template_id':row['id']}, (prompt + '\n本引擎使用 packet_read 读取 inventory.json 及清单图片；不读取包外文件，原 DOCX 由程序校验。用 submit_template 提交上述对象，不用 shell 或文件写入。')
    data = json.loads((folder / 'input.json').read_text())
    run_id = payload.get('run_id') or store.one('briefs', payload['version_id'])['run_id']
    config = {'role': 'orchestrator', 'task_kind': job['kind'], 'job_id': job['id'], 'run_id': run_id,
              'allow_web': bool(job.get('allow_web')), 'search_channels': [p for p in allowed(for_run(store, run_id)) if p in MANAGED_PROVIDERS]}
    root = folder / 'packet'; root.mkdir(exist_ok=True)
    if job['kind'] in ('generate', 'revise') and data.get('brief'):
        from .analyst import packet, WRITING_GUIDE
        plan_path = folder.parent / 'plan.json'
        research_path = folder.parent / 'research.json'
        packet(store, run_id, folder,
               plan=json.loads(plan_path.read_text()) if plan_path.exists() else {},
               research=json.loads(research_path.read_text()) if research_path.exists() else {'sources': [], 'gaps': []},
               base_version=data['brief']['id'], feedback=data)
        return {**config, 'role': 'analyst', 'revision': True, 'result_file': str(folder / 'draft.json')}, (WRITING_GUIDE +
            '\n先读取 input.feedback 的 assessment/review_findings/revision_reasons。交稿前调用 save_revision_metadata，逐项说明处理，不自行关闭发现；随后 save_draft 保存完整正文及引用/数字/时间元数据，check_draft 只传返回的 revision，修正后重新保存和检查，最后 submit_draft 只传已检查 revision。不要反复提交整篇正文或复制冻结 reader_contract。')
    _save(root / 'input.json', data)
    for name in ('reader_contract.schema.json', 'plan.json', 'research.json', 'analyst-writing.md'):
        if (folder / name).exists():
            (root / name).write_bytes((folder / name).read_bytes())
    if job['kind'] == 'fact_check':
        config.update(role='fact_checker', version_id=payload['version_id'])
        # Preserve the complete frozen fact-checking method; replace only tool transport.
        prompt = prompt + '\n本次没有 shell；web_search/add_url/source_read/workspace_action 直接传 JSON 参数。最后使用 submit_fact_check 提交上述结果对象，不写文件或调用 CLI。'
    elif job['kind'] == 'generate':
        prompt = ('你是本报告主 Agent。读取 input.json 的读者要求、已冻结研究计划、共享预算、角色技能和来源索引；'
                  '来源索引里的 source_id 用 source_read 读取；packet_read 只读取本包实际文件，不存在 packet/sources 目录，不猜原文路径。'
                  '开始时保存 reader_contract 与计划（save_plan），按任务需要决定 Scout 分工，用 run_scouts 执行。'
                  'workspace_action 提供 research_status/finish_research_round/begin_research_round 及写前 reconciliation_candidates/reconciliation_save；'
                  '其请求结构见 action-guide.md。后续轮次必须针对上一轮真实 gap_id，不重复搜索。'
                  '多轮任务结束当前轮时用 save_research_handoff 保留有引用的结论和待证问题，再 finish_research_round；下一轮按真实 gap_id 继续。研究完成后写前对照来源陈述，保留真实关系和未查问题；write_report 调用 Analyst，finish_task 只确认保存。'
                  '程序随后独立执行核查、审阅和最多一次修订，你不递归调用 generate/assess/learn。'
                  '工具已经绑定当前 run_id，不得更改模型、预算、网络权限或读者要求。'
                  '\n' + data.get('retrieval_strategy', '') + '\n' + data.get('orchestrator_instructions', ''))
    else:
        prompt += '\n本次没有 shell；工作区操作使用 workspace_action 直接提交 JSON request。所有路径读取用 packet_read 相对任务包。完成后调用 finish_task（元数据修复用 submit_metadata）。'
    from .chat_tools import chat_instructions
    guide = chat_instructions(store, {'backend': 'briefloop-native', **payload['runtime']}, allow_web=config['allow_web'], backend='briefloop-native')
    (root / 'action-guide.md').write_text(guide, encoding='utf-8')
    prompt = prompt.replace(str(folder.resolve()) + '/', '')
    (root / 'task.md').write_text(prompt, encoding='utf-8')
    return config, '读取 task.md 和 input.json；按需查 action-guide.md。工具列表是实际权限。'
