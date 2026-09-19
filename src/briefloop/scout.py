"""One Scout slot run as its own session.

On a host (codex, opencode) the Orchestrator spawns Scouts as native subagents
inside the generation session, and generation_prompt tells it how. Here one
slot's assignment runs as a separate staged session with the same contract,
reader contract, search policy, budget and slot files. The native engine runs
Scouts this way (its tools are runner tools; see native_roles), and a host can
run the same task for comparison.
"""
import json
from pathlib import Path

from .store import dump


def _reader_contract(store, run_id, plan_path=None):
    saved = store.meta('reader_contract:' + run_id)
    if saved is not None:
        return saved
    if plan_path and Path(plan_path).is_file():
        return json.loads(Path(plan_path).read_text(encoding='utf-8')).get('reader_contract')
    return None


def _source_index(store, run_id):
    rows = []
    for sid in store.source_ids(run_id):
        source = store.one('sources', sid)
        rows.append({'source_id': sid, 'name': source.get('name'), 'url': source.get('url'),
                     'status': source.get('status'), 'error': source.get('error') or None})
    return rows


def task(store, run_id, assignment, *, plan_path=None, research_handoff=None):
    """Everything one Scout slot is given, frozen when it starts."""
    from .deliverable_spec import resolve, instructions
    from .models import Requirements
    from .report_time import instructions as time_instructions
    from .research_budget import snapshot
    from .search_policy import for_run, allowed, instructions as search_instructions
    from .skills import bind_context
    from .websearch import MANAGED_PROVIDERS
    run = store.one('runs', run_id)
    raw = json.loads(run['requirements'])
    req = Requirements.model_validate(raw).model_dump()
    temporal = time_instructions(req.get('time_context'))
    policy = for_run(store, run_id)
    allow_web = bool(req['allow_web'])
    channels = [c for c in allowed(policy) if c in MANAGED_PROVIDERS] if allow_web else []
    skill = store.one('skills', run['skill_id']) if run.get('skill_id') else None
    binding = bind_context(store, skill).get('scout') or {}
    return {
        'run_id': run_id, 'slot_id': assignment.get('slot_id'), 'assignment': assignment,
        'period': req.get('period'), 'time_context': req.get('time_context'), 'created': run['created'],
        'allow_web': allow_web, 'search_channels': channels, 'policy': policy,
        'budget': snapshot(store, run_id), 'research_handoff': research_handoff,
        'contract': instructions(resolve(req), role='scout') + '\n' + temporal,
        'reader_contract': _reader_contract(store, run_id, plan_path),
        'search_note': (search_instructions(policy, None, run_id, native=True) if allow_web
                        else '本轮未允许联网，只读取已登记的材料，不安排公开检索。'),
        'skill': binding.get('instructions') or '',
        'sources': _source_index(store, run_id),
    }


def native_prompt(scout):
    web = ('按 search-policy.md 用受控搜索发现来源，add_url 保存正文后才能引用。' if scout['search_channels']
           else ('本轮允许联网但没有受控搜索渠道：只能用 add_url 保存任务里给出的 URL。' if scout['allow_web']
                 else '本轮未允许联网，只读取已登记的来源。'))
    packet = {
        'task.json': dump({key: scout[key] for key in ('slot_id', 'assignment', 'period', 'time_context', 'created', 'allow_web', 'budget', 'research_handoff')}),
        'scout-contract.md': scout['contract'], 'reader-contract.json': dump(scout['reader_contract']),
        'search-policy.md': scout['search_note'], 'skill.md': scout['skill'],
        'source-index.json': dump(scout['sources']),
    }
    included, deferred, size = [], [], 0
    for name, value in packet.items():
        if not value: continue
        if len(value) <= 6000 and size + len(value) <= 14000:
            included.append('\n--- ' + name + ' ---\n' + value); size += len(value)
        else: deferred.append(name)
    return (f"你是本报告 {scout['slot_id']} 槽位的 Scout。以下是冻结任务包内容，已内联部分无需再次 packet_read。"
            + ('开始时完整读取尚未内联的文件：' + '、'.join(deferred) + '。' if deferred else '')
            + "已登记来源正文用 source_read 直接读取，source_grep 按需用于定位，不是必经步骤；以来源为单位读取完整相关部分，单次最多 60000 字符，需要时继续读取。"
            + web
            + "预算以工具返回的 remaining 为准，所有 Scout 共用；额度耗尽后保留已有证据与具体缺口。"
            + "证据通过 record_evidence 保存，可按来源批量记录：稳定 id、source_id、source_hash、单一行段 locator、短逐字 quote、facts/conflicts/coverage_status/claim_ids。记录时机按研究需要决定，不要求每读一段就中断研究提交。"
            + "运行器取 excerpt；通过项已保存，只重交 rejected 条目。自动重定位后检查返回原文是否完整，必要时补读表头脚注再修正同一 id。"
            + "最终 submit_scout_result 只交 gaps/search_summary/retrieval_notes，不重交 sources；不要把结果 JSON 写进回复正文。"
            + ''.join(included))


def host_prompt(store, scout, folder, backend):
    """The same slot for a host session: the contract files on disk and
    `briefloop tool` commands in place of runner tools."""
    from .agent_commands import tool_command, quote_path
    from .search_policy import instructions as search_instructions
    folder = Path(folder).resolve()
    tool = tool_command(store.root, backend=backend)
    run_id = scout['run_id']
    (folder / 'scout-contract.md').write_text(scout['contract'], encoding='utf-8')
    (folder / 'reader-contract.json').write_text(dump(scout['reader_contract']), encoding='utf-8')
    (folder / 'task.json').write_text(dump({key: scout[key] for key in (
        'slot_id', 'assignment', 'period', 'time_context', 'created', 'allow_web', 'budget', 'research_handoff')}), encoding='utf-8')
    (folder / 'source-index.json').write_text(dump(scout['sources']), encoding='utf-8')
    from .models import ScoutResult
    (folder / 'scout.schema.json').write_text(dump(ScoutResult.model_json_schema()), encoding='utf-8')
    if scout['allow_web'] and scout['search_channels']:
        from importlib.resources import files
        template = files('briefloop').joinpath('skill_assets', 'multi-search', 'SKILL.md').read_text(encoding='utf-8')
        (folder / 'SKILL.md').write_text(template.replace('{tool}', tool).replace('{run_id}', run_id) + '\n'
                                         + search_instructions(scout['policy'], tool, run_id), encoding='utf-8')
        web = f"按 {folder / 'SKILL.md'} 用受控 web-search 发现来源，`{tool} add-url --run {run_id} --url URL` 保存正文取得 source_id 后才能引用。"
    elif scout['allow_web']:
        web = f"本轮只允许宿主自带搜索：用宿主的原生搜索发现来源，`{tool} add-url --run {run_id} --url URL` 保存正文取得 source_id 后才能引用。"
    else:
        web = '本轮未允许联网，只读取已登记的来源。'
    skill = ''
    if scout['skill']:
        (folder / 'skill.md').write_text(scout['skill'], encoding='utf-8')
        skill = f"、{folder / 'skill.md'}"
    result = folder / 'result.json'
    return (f"你是本报告 {scout['slot_id']} 槽位的 Scout，工作目录 {folder}，只在这里写文件。本槽位任务见 {folder / 'task.json'} 的 assignment；"
            f"开始时完整读取一次 {folder / 'scout-contract.md'}、{folder / 'reader-contract.json'}{skill}，简短确认已读。"
            f"已登记来源清单在 {folder / 'source-index.json'}；正文用 `{tool} read-source --id SOURCE_ID --start-line 1 --end-line 400 --max-chars 60000` 读取，"
            f"PDF 页面用 `{tool} render-source --id SOURCE_ID --pages 1 3` 渲染后读图。{web}"
            "预算以工具返回的 remaining 为准，所有 Scout 共用；出现 budget_exhausted 时停止新增检索，保留已有证据交接。"
            f"把结果按 {folder / 'scout.schema.json'} 写到 {result}（绝对路径），然后用 "
            f"`{tool} join-scouts --run {run_id} --files {quote_path(result, backend)}` 自检，报错就按错误修正。"
            "最终回复约 200 字以内：状态、核心发现与缺口、结果文件路径。")


def run(store, runtime, job, run_id, assignment, folder, backend, *, plan_path=None, research_handoff=None):
    """Run one Scout slot to its saved, structurally checked result."""
    from .runtime import stage_job
    from .scout_tools import join_scouts
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    scout = task(store, run_id, assignment, plan_path=plan_path, research_handoff=research_handoff)
    staged = {**stage_job(store, job, 'scout'), 'allow_web': scout['allow_web']}
    if backend == 'briefloop-native':
        from .native_roles import scout_packet
        scout_packet(store, scout, folder)
        staged['native_packet'] = {'role': 'scout', 'run_id': run_id, 'result_file': str(folder / 'result.json'),
                                   'allow_web': scout['allow_web'], 'search_channels': scout['search_channels']}
        prompt = native_prompt(scout)
    else:
        prompt = host_prompt(store, scout, folder, backend)
    runtime.execute(staged, prompt, folder)
    return join_scouts(store, [folder / 'result.json'], run_id=run_id)
