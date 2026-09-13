"""Fact-check task handler and result contract (SAFE-style web check, observation mode).

The fact-checker agent picks public claims, runs neutral plus counter-evidence
queries through the metered search path and registers originals. This module is
the deterministic program side only: it validates identity, coverage and version
binding of one submitted result and persists the D7 record (run/version/blockId/
quote hash/as-of/model+search snapshot). Scope alignment (entity, period, unit,
actual-vs-plan, correction relations) and the factual call stay with the agent;
a candidate becomes actionable only after the independent Reviewer accepts it,
so nothing here gates delivery. The method borrows from SAFE (Apache-2.0) by
translation and re-implementation; no upstream code is copied.
"""
import json
from datetime import date
from typing import get_args
from .store import dump, uid, now
from .evidence import digest, inspect_bindings, record as evidence_record
from .review import ClaimCheck
from .research_plan import FACT_CHECK_STATUSES, AdmissionError, frozen as frozen_plan, pending_requests

SCHEMA = '''
CREATE TABLE IF NOT EXISTS fact_checks(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
 version_id TEXT NOT NULL REFERENCES briefs(id),stage_id TEXT,fingerprint TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
'''

# Candidate judgements stay aligned with the Reviewer's ClaimCheck states
# structurally: if the review contract changes, this tuple follows it.
CANDIDATE_STATUSES = get_args(ClaimCheck.model_fields['status'].annotation)


class FactCheckError(ValueError):
    """All contract violations of one submitted result, structured for self-repair."""
    def __init__(self,errors):
        super().__init__('核查结果不符合结果契约（共 '+str(len(errors))+' 项）：'
                         +'；'.join(error['message'] for error in errors[:3]))
        self.errors=errors


def runtime_snapshot(store,run_id):
    """模型与搜索源快照：优先取本任务实际入队时冻结的配置，退回工作区设置。"""
    payload=None
    for row in store.rows("SELECT payload FROM jobs WHERE kind IN ('fact_check','generate') ORDER BY rowid DESC"):
        candidate=json.loads(row['payload'])
        if candidate.get('run_id')==run_id:payload=candidate;break
    settings=store.settings()
    return {'model':(payload or {}).get('runtime',{}).get('model') or settings.get('model'),
            'search_provider':(payload or {}).get('search_provider') or store.search_provider_for_run(run_id)}


def version_fingerprint(store,version_id):
    """本版本主张绑定身份：改稿或重绑后指纹变化，旧核查不再冒充新稿已核查。"""
    bindings=inspect_bindings(store,version_id)['bindings']
    return digest(dump({'version_id':version_id,
                        'claims':sorted((b['claim_id'],b['block_id'],b['block_hash']) for b in bindings)}))


def _known_claim(store,run_id,claim_id,bindings,where,errors):
    try:claim=evidence_record(store,'claims',claim_id)
    except ValueError:
        errors.append({'path':where,'code':'unknown_claim','message':where+' 引用的主张不存在：'+str(claim_id)});return None
    if claim['run_id']!=run_id:
        errors.append({'path':where,'code':'claim_not_in_run','message':where+' 属于另一报告的主张'});return None
    if claim_id not in bindings:
        errors.append({'path':where,'code':'claim_not_in_version','message':where+' 未绑定到本版本正文；先绑定报告块再核查'});return None
    return claim


def check_fact_result(store,run_id,result,*,snapshot=None):
    """Validate one submitted result against its version; returns the record, unpersisted.

    Python checks only that every claim/span/source/query id is real and belongs
    to this run and version, that candidate statuses stay on the ClaimCheck four
    states, that selected claims are covered when execution completed, and that
    execution status stays separate from any factual judgement. All violations
    are aggregated into one FactCheckError the agent can repair from.
    """
    if not isinstance(result,dict):
        raise FactCheckError([{'path':'','code':'not_object','message':'核查结果必须是 JSON 对象'}])
    run=store.one('runs',run_id)
    errors=[]
    version_id=result.get('version_id');brief=None
    if not isinstance(version_id,str) or not version_id.strip():
        errors.append({'path':'version_id','code':'version_missing','message':'缺少 version_id；核查结果绑定到具体报告版本'})
    else:
        try:brief=store.one('briefs',version_id)
        except ValueError:
            errors.append({'path':'version_id','code':'unknown_version','message':'报告版本不存在：'+version_id})
        else:
            if brief['run_id']!=run_id:
                errors.append({'path':'version_id','code':'version_mismatch','message':'报告版本属于另一任务'})
                brief=None
    bindings={}
    if brief is not None:
        for binding in inspect_bindings(store,brief['id'])['bindings']:
            bindings.setdefault(binding['claim_id'],[]).append(binding)
    plan=frozen_plan(store,run_id) or {};stage=plan.get('fact_check') or {};stage_id=stage.get('stage_id')
    if result.get('stage_id') is not None and result.get('stage_id')!=stage_id:
        errors.append({'path':'stage_id','code':'stage_mismatch','message':'stage_id 与本任务接纳的核查阶段不一致'})
    execution=result.get('execution')
    if not isinstance(execution,dict) or execution.get('status') not in FACT_CHECK_STATUSES:
        hint='；事实判断只写在 candidates[].status' if isinstance(execution,dict) and execution.get('status') in CANDIDATE_STATUSES else ''
        errors.append({'path':'execution.status','code':'execution_status',
                       'message':'execution.status 必须是 '+'/'.join(FACT_CHECK_STATUSES)+'，说明本次执行为何收束，不判断主张真假'+hint})
        execution={'status':'failed','summary':''}
    requirements=json.loads(run['requirements'])
    as_of=result.get('as_of')
    if as_of in (None,''):as_of=requirements.get('report_date') or None
    if as_of is not None:
        try:valid=isinstance(as_of,str) and len(as_of)==10 and date.fromisoformat(as_of).isoformat()==as_of
        except ValueError:valid=False
        if not valid:
            errors.append({'path':'as_of','code':'as_of_invalid','message':'截至日 as_of 应为 YYYY-MM-DD'});as_of=None
    allowed_sources=set(store.source_ids(run_id))-set(requirements.get('reference_source_ids',[]))
    selection=result.get('selection')
    if not isinstance(selection,dict):
        errors.append({'path':'selection','code':'not_object','message':'缺少 selection 对象（选中 claim_ids 与未选原因）'});selection={}
    selected=selection.get('claim_ids')
    if not isinstance(selected,list) or any(not isinstance(item,str) or not item.strip() for item in selected):
        errors.append({'path':'selection.claim_ids','code':'not_claim_list','message':'selection.claim_ids 必须是本版本已绑定主张的 id 数组'});selected=[]
    valid_selected={}
    for position,claim_id in enumerate(selected):
        where='selection.claim_ids['+str(position)+']'
        if claim_id in valid_selected:
            errors.append({'path':where,'code':'selection_duplicate','message':where+' 重复选择同一主张'});continue
        claim=_known_claim(store,run_id,claim_id,bindings,where,errors)
        if claim is not None:valid_selected[claim_id]=claim
    raw_unselected=selection.get('unselected',[])
    if not isinstance(raw_unselected,list):
        errors.append({'path':'selection.unselected','code':'not_list','message':'selection.unselected 若填写必须是 [{claim_id,reason}] 数组'});raw_unselected=[]
    unselected=[]
    for position,item in enumerate(raw_unselected):
        where='selection.unselected['+str(position)+']'
        claim_id=item.get('claim_id') if isinstance(item,dict) else None
        reason=item.get('reason') if isinstance(item,dict) else None
        if not isinstance(claim_id,str) or not claim_id.strip():
            errors.append({'path':where+'.claim_id','code':'claim_missing','message':where+' 缺少 claim_id'});continue
        if claim_id in valid_selected:
            errors.append({'path':where,'code':'selection_conflict','message':where+' 同时出现在已选与未选清单'})
        else:_known_claim(store,run_id,claim_id,bindings,where,errors)
        if not isinstance(reason,str) or not reason.strip():
            errors.append({'path':where+'.reason','code':'unselected_reason_missing',
                           'message':where+' 需记录未选原因（如内部未公开信息不进搜索框、不影响主要判断）'})
        unselected.append({'claim_id':claim_id,'reason':reason if isinstance(reason,str) else ''})
    raw=result.get('candidates')
    if not isinstance(raw,list):
        errors.append({'path':'candidates','code':'not_list','message':'缺少 candidates 数组（一条选中主张一条候选记录）'});raw=[]
    stage_queries=None;covered=set();candidates=[]
    for position,item in enumerate(raw):
        where='candidates['+str(position)+']'
        if not isinstance(item,dict):
            errors.append({'path':where,'code':'not_object','message':where+' 必须是 JSON 对象'});continue
        claim_id=item.get('claim_id')
        if not isinstance(claim_id,str) or not claim_id.strip():
            errors.append({'path':where+'.claim_id','code':'claim_missing','message':where+' 缺少 claim_id'});continue
        if claim_id in covered:
            errors.append({'path':where+'.claim_id','code':'duplicate_candidate','message':where+' 对同一主张重复给出候选：'+claim_id})
        if claim_id not in valid_selected:
            errors.append({'path':where+'.claim_id','code':'candidate_outside_selection',
                           'message':where+' 的主张不在 selection.claim_ids 内；先入选择清单，或作为未选记录原因'})
            _known_claim(store,run_id,claim_id,bindings,where,errors)
        status=item.get('status')
        if status not in CANDIDATE_STATUSES:
            hint='；执行状态只说明停止原因，不判断真假' if status in FACT_CHECK_STATUSES else ''
            errors.append({'path':where+'.status','code':'status_invalid',
                           'message':where+'.status 必须是 '+'/'.join(CANDIDATE_STATUSES)+'（对齐 ClaimCheck 四态）'+hint});status=None
        reason=item.get('reason')
        if not isinstance(reason,str) or not reason.strip():
            errors.append({'path':where+'.reason','code':'reason_missing',
                           'message':where+' 缺少一句依据（查到什么、缺什么或为何无法判断）'});reason=''
        if isinstance(item.get('url'),str) and item['url'].strip():
            errors.append({'path':where+'.url','code':'bare_url',
                           'message':where+' 的证据是裸 URL；先用 add-url 登记来源并登记证据定位，再以 span_ids 引用'})
        raw_spans=item.get('span_ids')
        if raw_spans is None:raw_spans=[]
        if not isinstance(raw_spans,list) or any(not isinstance(x,str) or not x.strip() for x in raw_spans):
            errors.append({'path':where+'.span_ids','code':'not_span_list','message':where+'.span_ids 必须是证据 span id 数组（无证据时给空数组）'});raw_spans=[]
        spans=[]
        for index,span_id in enumerate(raw_spans):
            at=where+'.span_ids['+str(index)+']'
            if span_id.strip().lower().startswith(('http://','https://')):
                errors.append({'path':at,'code':'bare_url','message':at+' 是裸 URL；先登记来源与证据定位取得 span_id，不能用 URL 直接充当证据'});continue
            try:span=evidence_record(store,'evidence_spans',span_id)
            except ValueError:
                errors.append({'path':at,'code':'unknown_span','message':at+' 不是已登记的证据 span：'+span_id});continue
            if span['source_id'] not in allowed_sources:
                errors.append({'path':at,'code':'span_source_not_in_run','message':at+' 的来源未登记为本任务证据（或属于风格参考）：'+span_id});continue
            spans.append(span_id)
        if status in ('supported_for_scope','contradicted') and not spans:
            errors.append({'path':where+'.span_ids','code':'evidence_missing',
                           'message':where+' 判为 '+status+' 必须附至少一个已登记证据 span；材料不足请改用 insufficient_evidence 并说明缺什么'})
        raw_queries=item.get('query_ids',[])
        if not isinstance(raw_queries,list):
            errors.append({'path':where+'.query_ids','code':'not_list','message':where+'.query_ids 若填写必须是本阶段登记的查询 id 数组'});raw_queries=[]
        query_ids=[]
        for index,qid in enumerate(raw_queries):
            at=where+'.query_ids['+str(index)+']'
            if stage_queries is None:
                stage_queries={key for key,entry in pending_requests(store,run_id).items()
                               if entry.get('stage')=='fact_check' and (stage_id is None or entry.get('round_id')==stage_id)}
            if qid not in stage_queries:
                errors.append({'path':at,'code':'unknown_query',
                               'message':at+' 不是本核查阶段登记的查询；query id 来自预算登记的真实 search/extract 请求'});continue
            query_ids.append(qid)
        covered.add(claim_id)
        candidates.append({'claim_id':claim_id,'status':status,'reason':reason,'span_ids':spans,'query_ids':query_ids})
    unchecked=[claim_id for claim_id in valid_selected if claim_id not in covered]
    if execution['status']=='completed':
        for claim_id in unchecked:
            errors.append({'path':'candidates','code':'coverage_missing',
                           'message':'已选择主张缺少候选结果：'+claim_id+'；completed 要求选中主张全覆盖，未核查的移入未选并说明'})
    if errors:raise FactCheckError(errors)
    for candidate in candidates:
        candidate['anchors']=[{'block_id':b['block_id'],'quote':b['quote'],'quote_hash':digest(b['quote']),
                               'block_hash':b['block_hash']} for b in bindings[candidate['claim_id']]]
    return {'id':uid('fchkrec'),'run_id':run_id,'version_id':brief['id'],'stage_id':stage_id,
            'as_of':as_of,'snapshot':snapshot or runtime_snapshot(store,run_id),
            'version_fingerprint':version_fingerprint(store,brief['id']),
            'selection':{'claim_ids':list(valid_selected),'unselected':unselected},
            'candidates':candidates,'unchecked':unchecked,
            'execution':{'status':execution['status'],'summary':execution.get('summary') or ''},
            'created':now()}


def submit_result(store,run_id,result,*,job_id=None):
    """核查任务处理器：接纳入库一份结果并收束阶段（复用 jobs/events/Store）。

    阶段缺失或已收束（取消/失败/预算耗尽之后）时拒绝迟到结果，不静默附到
    旧阶段；预算或取消的交接结果在阶段仍活跃时提交，随后以同一执行状态
    收束。执行状态说明为何停止，永远不改写候选的事实判断。
    """
    stage=(frozen_plan(store,run_id) or {}).get('fact_check')
    if not stage:raise AdmissionError('尚未接纳核查阶段，不能提交结果',code='fact_check_missing')
    if stage.get('status')!='active':
        raise AdmissionError('核查阶段已以 '+str(stage.get('status'))+' 收束，迟到结果不接纳',code='fact_check_closed')
    record=check_fact_result(store,run_id,result)
    with store.tx() as c:
        c.execute('INSERT INTO fact_checks VALUES(?,?,?,?,?,?,?)',
                  (record['id'],run_id,record['version_id'],record['stage_id'],
                   record['version_fingerprint'],dump(record),record['created']))
    if job_id:
        store.event(job_id,'fact_check',{'action':'result','record_id':record['id'],'version_id':record['version_id'],
                                         'execution_status':record['execution']['status'],
                                         'candidates':len(record['candidates']),'unchecked':len(record['unchecked'])})
    from .research_plan import finish_fact_check
    closed=finish_fact_check(store,run_id,status=record['execution']['status'],
                             summary=record['execution']['summary'],job_id=job_id)
    return {'record':record,'stage':closed}


def get_record(store,identity):
    rows=store.rows('SELECT * FROM fact_checks WHERE id=?',(identity,))
    if not rows:raise ValueError('核查记录不存在')
    return {**rows[0],'data':json.loads(rows[0]['data'])}


def records_for(store,run_id,version_id=None):
    """All admitted fact-check records of a run (optionally one version), newest first."""
    query='SELECT * FROM fact_checks WHERE run_id=?'+(' AND version_id=?' if version_id else '')+' ORDER BY rowid DESC'
    return [{**row,'data':json.loads(row['data'])} for row in store.rows(query,(run_id,version_id) if version_id else (run_id,))]


def covers(store,record,version_id):
    """旧核查是否仍覆盖该版本：改稿后的版本不算已核查，重查生成新身份。"""
    data=record['data'] if isinstance(record.get('data'),dict) else record
    return data['version_id']==version_id and data['version_fingerprint']==version_fingerprint(store,version_id)


def view(store,version_id):
    """检查面板只读视图：候选、Reviewer 判断与执行状态分别可查，互不改写。

    候选来自 fact_checks 记录本身；Reviewer 判断取该版本最近一次已接纳审阅的
    claim_checks（按 claim_id 对齐，可与候选不同）；执行状态只说明收束原因。
    原文链接解析成 span 的来源与定位，供面板逐条展示。
    """
    brief=store.one('briefs',version_id)
    reviewer={}
    for row in store.rows('SELECT result FROM reviews WHERE version_id=? AND result IS NOT NULL ORDER BY rowid DESC',(version_id,)):
        for check in json.loads(row['result']).get('claim_checks',[]):
            reviewer.setdefault(check['claim_id'],{'status':check['status'],'reason':check['reason']})
        break
    def statement(claim_id):
        try:return evidence_record(store,'claims',claim_id)['data'].get('statement','')
        except ValueError:return ''
    def display(candidate):
        spans=[]
        for span_id in candidate.get('span_ids',[]):
            try:span=evidence_record(store,'evidence_spans',span_id)
            except ValueError:continue
            spans.append({'span_id':span_id,'source_id':span['source_id'],
                          'source_name':store.one('sources',span['source_id'])['name'],
                          'locator':span['data'].get('locator')})
        return {'claim_id':candidate['claim_id'],'statement':statement(candidate['claim_id']),
                'status':candidate.get('status'),'reason':candidate.get('reason',''),
                'spans':spans,'query_ids':candidate.get('query_ids',[]),
                'anchors':candidate.get('anchors',[]),'reviewer':reviewer.get(candidate['claim_id'])}
    records=[]
    for row in records_for(store,brief['run_id']):
        data=row['data']
        records.append({'id':row['id'],'version_id':data['version_id'],'stage_id':data['stage_id'],
                        'as_of':data['as_of'],'snapshot':data['snapshot'],'execution':data['execution'],
                        'covers_version':covers(store,row,version_id),
                        'candidates':[display(item) for item in data['candidates']],
                        'unchecked':[{'claim_id':claim_id,'statement':statement(claim_id),
                                      'reviewer':reviewer.get(claim_id)} for claim_id in data['unchecked']],
                        'unselected':[{'claim_id':item['claim_id'],'statement':statement(item['claim_id']),
                                       'reason':item['reason']} for item in data['selection'].get('unselected',[])],
                        'created':data['created']})
    return {'version_id':version_id,'records':records}
