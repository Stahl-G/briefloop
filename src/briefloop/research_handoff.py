"""Current research gaps over immutable Scout observations and round outcomes.

Only an explicit agent update changes a gap's status. Python verifies ownership,
location and snapshot identity, never whether a passage semantically resolves it.
"""
import hashlib
import json
import re
from copy import deepcopy

from .models import ResearchGap, ResearchGapUpdate, ScoutResult
from .store import dump


PLANNING_GUIDE = '''execution_gaps 是逐轮 Scout 执行未完成或明确跳过的范围，含 assignment/status/reason，不是来源事实缺口。成功恢复会清除同一任务的执行提示，实质证据缺口仍以 gap_records 为准。影响必答问题或核心判断的未检范围须向读者说明其限制；未派发不等于未找到，未找到不等于不存在。
研究交接的 gaps/gap_records 是当前仍待核对的问题；gap_history 保留主 Agent 明确判定已解决的历史及依据，不把历史缺口继续写成当前事实。partial 只就 remaining_question 补查；未更新的缺口仍是 open。证据定位通过仅表示来源和摘录可回查，不证明判断正确，重要结论仍需按原文核对。
写作计划和 writing_instructions 是组织建议，不是事实来源；若与冻结原文在状态、时间或主体上冲突，以原文写事实并在研究记录说明，不照抄被升级的主线，同时保留用户明确的写作要求。主 Agent 的写作计划、章节主线和给 Analyst 的指令同样受研究证据约束：不得把考虑中/公测/上月/最高/预测/适用条件升级或省略。关键二手消息若影响主结论，利用现有搜索与读取能力定向查找一手正文；找到以后明确更新旧缺口，不把历史读取失败当现状。按读者目标检查重要候选的覆盖，纳入或省略的重要候选在研究记录中简述理由，不把厂商数量或来源数量当覆盖率。
研究收尾用已有 finish_research_round.summary 记录：本轮重要候选纳入/省略的理由、哪些承重结论仍只有二手稿、补查结果和剩余影响。handoff 的 covered/follow_ups/open_questions 继续保存具体问题；这些记录会随 research.retrieval_notes 交给写稿，属于主 Agent 的研究判断，不是事实认证。若重要候选可能遗漏或核心结论仍缺一手正文，先核对本轮材料索引；按预算与剩余轮次使用现有 begin_research_round/run_scouts 或已授权来源读取定向补查。补查要回答具体缺口，不重新扫全行业或为凑来源加调用；预算耗尽或仍无法获取时保留未检范围和理由，不把“未取得/未找到”改写成“来源不存在/没有发生”。Reviewer 不负责补查，不增加默认轮次。
读者正文只保留会影响判断的限制，如公测、指定分支、最高值、讨论中或关键数据未披露；抓取失败、查询过程、工具返回的临时 line 行号及自我复核清单留研究记录与引用元数据。公开引用用稳定的标题、日期、章节/页码和链接，不以抓取器行号作为读者入口。不能为了减少过程噪音删掉必要条件。'''

GAP_UPDATE_GUIDE = '''join-scouts/research_status 返回缺口的真实 gap_id。只有主 Agent 核对原文后才通过 finish_research_round 的可选 gap_updates（或本轮 handoff.json 中同字段）明确更新：每项含 gap_id、status=open|partial|resolved、reason、evidence=[{source_id,locator,excerpt}]；locator 为 line 3-5、page 2 或证据定位 JSON，excerpt 为该位置连续逐字原文，partial/resolved 至少一条，open 可只给 reason 重新开放。partial 另给 remaining_question。未更新、仅写 covered 或省略 open_questions 都不会关闭缺口；来源注册、摘录存在不等于语义核查通过。提交更新后再 join-scouts 刷新交给写稿的当前/历史视图，保留所有 Scout 原件。'''


def closeout_snapshot(handoff, summary):
    """Freeze the agent's existing closeout fields, without judging coverage."""
    if not isinstance(summary, str):
        raise ValueError('研究收尾 summary 必须是文本')
    result = {'summary': summary}
    for name in ('covered', 'follow_ups', 'open_questions'):
        items = handoff.get(name, [])
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError('研究收尾 ' + name + ' 必须是文本数组')
        result[name] = items
    return result


def _closeout_notes(store, run_id):
    plan = store.meta('research_plan:' + run_id) or {}
    notes = []
    for identity, info in sorted(plan.get('rounds', {}).items(), key=lambda pair: pair[1].get('index', 0)):
        if info.get('status') != 'closed':
            continue
        outcome = info.get('outcome') or {}
        closeout = outcome.get('closeout') or closeout_snapshot({}, outcome.get('summary') or '')
        if not any(closeout.values()):
            continue
        notes.append({'kind': 'research_round_closeout', 'generated_by': 'briefloop',
                      'round_id': identity, 'round_index': info.get('index'), **closeout,
                      'scope': '主 Agent 的研究取舍与未检范围；不是事实认证。当前缺口以 gap_records 为准，历史记录不自动关闭或重开缺口。'})
    return notes


def _key(run_id):
    return 'research_gaps:' + run_id


def gap_id(run_id, description):
    return 'gap_' + hashlib.sha256(dump([run_id, description.strip()]).encode()).hexdigest()[:24]


def observe(state, run_id, descriptions, *, identities=None):
    """Add observations without undoing any explicit update, including on replay."""
    known = {item['description']: identity for identity, item in state['records'].items()}
    for description in descriptions:
        description = description.strip()
        if not description or description in known:
            continue
        identity = (identities or {}).get(description) or gap_id(run_id, description)
        state['records'][identity] = {'gap_id': identity, 'description': description}
        known[description] = identity
    return known


def read_state(store, run_id, *, connection=None, plan=None):
    if connection is None:
        state = store.meta(_key(run_id)) or {'records': {}, 'updates': []}
        if plan is None:
            plan = store.meta('research_plan:' + run_id) or {}
    else:
        row = connection.execute('SELECT value FROM meta WHERE key=?', (_key(run_id),)).fetchone()
        state = json.loads(row['value']) if row else {'records': {}, 'updates': []}
        if plan is None:
            row = connection.execute('SELECT value FROM meta WHERE key=?', ('research_plan:' + run_id,)).fetchone()
            plan = json.loads(row['value']) if row else {}
    # Older rounds assigned a new ID even for an unchanged description. Keep
    # every historic ID usable without editing the immutable round records.
    aliases = state.setdefault('aliases', {})
    # Adopt legacy program-assigned IDs before adding hash IDs for Scout strings.
    for info in (plan or {}).get('rounds', {}).values():
        for item in info.get('gaps', []):
            description = item['description'].strip()
            known = observe(state, run_id, [description], identities={description: item['id']})
            if item['id'] != known[description]:
                aliases[item['id']] = known[description]
    return state


def save_state(connection, run_id, state):
    connection.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', (_key(run_id), dump(state)))


def _proof(store, run_id, evidence):
    from .evidence import EvidenceInput, _read_location
    allowed = set(store.source_ids(run_id))
    if evidence.source_id not in allowed:
        raise ValueError('缺口依据来源不属于本报告：' + evidence.source_id)
    source = store.one('sources', evidence.source_id)
    if evidence.source_hash and evidence.source_hash != source['hash']:
        raise ValueError('缺口依据来源快照已失效：' + evidence.source_id)
    match = re.fullmatch(r'(?i)(line|page)\s+(\d+)(?:\s*-\s*(\d+))?', evidence.locator.strip())
    if match:
        start, end = int(match[2]), int(match[3] or match[2])
        if match[1].lower() == 'line':
            locator = {'kind': 'text', 'start_line': start, 'end_line': end}
        elif start == end:
            locator = {'kind': 'pdf', 'page': start}
        else:
            raise ValueError('缺口依据每条只定位一个 PDF 页码')
    else:
        try:
            locator = json.loads(evidence.locator)
        except ValueError:
            raise ValueError('缺口依据 locator 必须为行/页或证据定位 JSON') from None
    _, location = _read_location(store, EvidenceInput(source_id=evidence.source_id,
        locator=locator, excerpt=evidence.excerpt))
    if location['location_status'] != 'located' or not evidence.excerpt.strip():
        raise ValueError('缺口依据无法核对逐字摘录，请保留 open 并补充可定位依据')
    return {**evidence.model_dump(), 'source_hash': source['hash']}


def validate_updates(store, run_id, raw, *, state=None):
    if not isinstance(raw, list):
        raise ValueError('gap_updates 必须为数组')
    state = state if state is not None else read_state(store, run_id)
    result, seen = [], set()
    for index, value in enumerate(raw):
        try:
            item = ResearchGapUpdate.model_validate(value)
            canonical = state.get('aliases', {}).get(item.gap_id, item.gap_id)
            item = item.model_copy(update={'gap_id': canonical})
            if item.gap_id not in state['records']:
                raise ValueError('gap_id 不属于当前报告已登记缺口：' + item.gap_id)
            if item.gap_id in seen:
                raise ValueError('同一批次不能重复更新同一 gap_id')
            seen.add(item.gap_id)
            result.append({**item.model_dump(), 'evidence': [_proof(store, run_id, ref) for ref in item.evidence]})
        except (ValueError, OSError) as exc:
            raise ValueError(f'gap_updates[{index}]：{exc}') from exc
    return result


def apply_updates(store, run_id, state, raw, *, round_id, round_index):
    """Pure state mutation inside the caller's transaction; replay is a no-op."""
    updates = validate_updates(store, run_id, raw, state=state)
    known = {entry['id'] for entry in state['updates']}
    for value in updates:
        identity = hashlib.sha256(dump([round_id, value]).encode()).hexdigest()
        if identity not in known:
            state['updates'].append({**value, 'id': identity, 'round_id': round_id, 'round_index': round_index})
            known.add(identity)
    return updates


def gap_view(store, run_id, state=None):
    state = read_state(store, run_id) if state is None else state
    records = deepcopy(state['records'])
    for entry in state['updates']:
        records[entry['gap_id']].update({key: entry[key] for key in
            ('status', 'reason', 'remaining_question', 'evidence', 'round_id', 'round_index')})
    current, history = [], []
    for raw in records.values():
        item = ResearchGap.model_validate(raw).model_dump()
        try:
            for ref in item['evidence']:
                from .models import ResearchGapEvidence
                _proof(store, run_id, ResearchGapEvidence.model_validate(ref))
        except (ValueError, OSError) as exc:
            # Do not silently inherit a resolution whose source can no longer
            # be located. The immutable update remains in the local ledger.
            item.update(status='open', validation_error=str(exc), remaining_question='')
        (history if item['status'] == 'resolved' else current).append(item)
    return {'gaps': [item['remaining_question'] if item['status'] == 'partial' else item['description'] for item in current],
            'gap_records': current, 'gap_history': history}


def current_research(store, run_id, research, *, register=False):
    """Same projection for host join, Native writing, and revision packets."""
    value = ScoutResult.model_validate(research).model_dump(mode='json')
    descriptions = value['gaps'] + [item['description'] for item in value['gap_records'] + value['gap_history']]
    if register:
        with store.tx() as connection:
            state = read_state(store, run_id, connection=connection)
            represented = {item.get('remaining_question', '') for item in state['updates']}
            observe(state, run_id, [text for text in descriptions if text not in represented])
            save_state(connection, run_id, state)
    else:
        state = read_state(store, run_id)
        # A previous projection's remaining_question is not a new observation.
        represented = {item['description'] for item in state['records'].values()}
        represented.update(item.get('remaining_question', '') for item in state['updates'])
        observe(state, run_id, [text for text in descriptions if text not in represented])
    from .scout_coverage import view as scout_view
    execution = scout_view(store, run_id)
    notes = [item for item in value['retrieval_notes']
             if not (item.get('kind') in ('research_round_closeout', 'scout_execution') and item.get('generated_by') == 'briefloop')]
    return {**value, **gap_view(store, run_id, state), 'execution_gaps': execution['execution_gaps'],
            'retrieval_notes': notes + _closeout_notes(store, run_id) + [
                {'kind': 'scout_execution', 'generated_by': 'briefloop', **task,
                 'scope': '执行状态与未检范围；完成仅说明结果已交接，不表示证据缺口已解决。'}
                for task in execution['scout_execution']]}
