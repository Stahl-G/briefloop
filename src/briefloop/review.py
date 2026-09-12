"""Version-bound read-only review packets, findings, and response lifecycle."""
from pathlib import Path
import hashlib
import json
import shutil
from typing import Literal
from pydantic import ConfigDict, Field, model_validator
from .models import Model, Assessment
from .store import dump, uid, now
from .evidence import inspect_bindings, record

SCHEMA='''
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,version_id TEXT NOT NULL REFERENCES briefs(id),
 job_id TEXT REFERENCES jobs(id),fingerprint TEXT NOT NULL,status TEXT NOT NULL,data TEXT NOT NULL,
 result TEXT,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_findings(id TEXT PRIMARY KEY,review_id TEXT NOT NULL REFERENCES reviews(id),
 version_id TEXT NOT NULL REFERENCES briefs(id),status TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_responses(id TEXT PRIMARY KEY,finding_id TEXT NOT NULL REFERENCES review_findings(id),
 version_id TEXT NOT NULL REFERENCES briefs(id),data TEXT NOT NULL,created TEXT NOT NULL);
'''


class ClaimCheck(Model):
    claim_id: str
    status: Literal['supported_for_scope','contradicted','insufficient_evidence','unknown']
    reason: str = Field(min_length=1)


class ReviewFinding(Model):
    kind: Literal['contradiction','insufficient_evidence','missing_requirement','missing_binding','execution_gap','expression']
    severity: Literal['major','minor']
    claim_ids: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    report_quote: str = ''
    evidence: str = Field(min_length=1)
    description: str = Field(min_length=1)
    suggested_action: str = ''
    dimension: Literal['evidence','coverage','analysis','expression'] | None = None
    locator: str = ''
    # Tolerated drift: the model sometimes labels a clause id or a source on the
    # review finding. Keep it rather than failing the whole task on a stray key.
    requirement: str = ''
    source_id: str | None = None
    response_to: str | None = Field(default=None,description='history/responses.json 中的处理说明 id（response_开头），不是 finding_id')
    resolution: Literal['resolved','dismissed_with_evidence'] | None = None

    @model_validator(mode='before')
    @classmethod
    def accept_suggestion_alias(cls,value):
        if isinstance(value,dict) and 'suggestion' in value:
            if 'suggested_action' in value and value['suggested_action']!=value['suggestion']:
                raise ValueError('suggestion 与 suggested_action 不能给出不同处理建议')
            value=dict(value);value['suggested_action']=value.pop('suggestion')
        return value

    @model_validator(mode='before')
    @classmethod
    def fill_missing_description(cls,value):
        # Models occasionally omit the description and put the text in evidence
        # or suggested_action; keep the finding instead of failing the whole review.
        if isinstance(value,dict) and not str(value.get('description','') or '').strip():
            for key in ('evidence','suggested_action','report_quote','requirement'):
                fallback=value.get(key)
                if isinstance(fallback,str) and fallback.strip():
                    value=dict(value);value['description']=fallback.strip();break
        return value


class ResponseCheck(Model):
    response_id: str
    decision: Literal['resolved','dismissed_with_evidence','unresolved']
    reason: str = Field(min_length=1)


class ConflictCheck(Model):
    conflict_id: str
    decision: Literal['unresolved','confirmed_correction','different_scope','attributed_forecasts','keep_current','adopt_new']
    reason: str = Field(min_length=1)
    chosen_fact_id: str | None = None
    basis_span_ids: list[str] = Field(default_factory=list)
    scope: str = ''


class RequirementCheck(Model):
    requirement_id: str
    status: Literal['covered','manual','partial','missing']
    reason: str = Field(min_length=1)


class ClauseCheck(Model):
    """One reader-contract clause result. The clause_id is produced by the program."""
    clause_id: str
    status: Literal['covered', 'partial', 'missing', 'not_applicable', 'unverified']
    reason: str = Field(min_length=1)
    basis: list[str] = Field(default_factory=list)


class UncheckedItem(Model):
    description: str = Field(min_length=1)
    importance: Literal['core','supporting']


class ReviewOutput(Model):
    # The reviewer occasionally emits a stray top-level key (for example `overall`,
    # which belongs to the assessment). Ignore extras so schema drift cannot fail a
    # generation task; the required fields below are still enforced.
    model_config = ConfigDict(extra='ignore')
    fingerprint: str
    version_id: str
    status: Literal['complete','incomplete']
    summary: str
    coverage_scan_complete: bool = False
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list)
    unchecked_items: list[UncheckedItem] = Field(default_factory=list)
    requirement_checks: list[RequirementCheck] = Field(default_factory=list)
    clause_checks: list[ClauseCheck] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    response_checks: list[ResponseCheck] = Field(default_factory=list)
    conflict_checks: list[ConflictCheck] = Field(default_factory=list)
    # A review may arrive without a score; findings and checks stay usable and the
    # panel simply shows "尚未评分". Generation must not fail because scoring did.
    assessment: Assessment | None = None


def pack_dump(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)


def sha(data):return hashlib.sha256(data).hexdigest()


def get_review(store,identity):
    rows=store.rows('SELECT * FROM reviews WHERE id=?',(identity,))
    if not rows:raise ValueError('审阅记录不存在')
    row=rows[0];return {**row,'data':json.loads(row['data']),'result':json.loads(row['result']) if row['result'] else None}


def _packet(store,review):
    """Validate the same files the Reviewer was allowed to read, including index."""
    data=review['data'];relative=data.get('packet_path');files=data.get('files')
    if not relative or not isinstance(files,dict) or 'index.json' not in files:
        raise ValueError('审阅缺少固定核查包及文件清单')
    packet=store.root/relative
    if not packet.resolve().is_relative_to(store.root.resolve()) or packet.is_symlink():
        raise ValueError('核查包路径无效')
    for name,digest in files.items():
        path=packet/name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(packet)):
            raise ValueError('Reviewer 核查包文件已变化，不能接纳本次结果')
        if not path.resolve().is_relative_to(packet.resolve()) or not path.is_file() or sha(path.read_bytes())!=digest:
            raise ValueError('Reviewer 核查包文件已变化，不能接纳本次结果')
    # Packet manifests use forward slashes on every platform, including Windows.
    actual={path.relative_to(packet).as_posix() for path in packet.rglob('*') if path.is_file() or path.is_symlink()}
    if actual!=set(files):raise ValueError('Reviewer 核查包文件清单不一致')
    index=json.loads((packet/'index.json').read_text());bound={k:v for k,v in files.items() if k!='index.json'}
    target=json.loads((packet/'target.json').read_text())
    fingerprint=sha(dump({'target':target,'files':bound}).encode())
    if index.get('files')!=bound or index.get('version_id')!=review['version_id'] or target.get('version_id')!=review['version_id'] or index.get('fingerprint')!=fingerprint or fingerprint!=review['fingerprint']:
        raise ValueError('Reviewer 核查包索引与输入指纹不一致')
    return packet,target,bound


def _ancestry(store,version_id):
    versions={};cursor=store.one('briefs',version_id)
    while cursor:
        versions[cursor['id']]=len(versions);cursor=store.one('briefs',cursor['parent_id']) if cursor['parent_id'] else None
    return versions


def _latest_responses(rows,ancestry):
    """Prefer the closest version, then the latest response within that version.

    `rows` are in persisted row order. A new note on an old version must never
    displace the response on a descendant, even if it was inserted later.
    """
    latest={}
    for row in rows:
        depth=ancestry.get(row['version_id'])
        if depth is None:continue
        previous=latest.get(row['finding_id'])
        if previous is None or depth<=ancestry[previous['version_id']]:
            latest[row['finding_id']]=row
    return latest


def _response_scope(store,packet,version_id):
    rows=json.loads((packet/'history/responses.json').read_text())
    # History stays complete, but a later explanation explicitly supersedes the
    # earlier explanation for the same finding. Only its exact id is actionable.
    ancestry=_ancestry(store,version_id)
    latest=_latest_responses(rows,ancestry)
    scoped={row['id']:row for row in latest.values() if row['version_id']==version_id}
    current=store.rows('SELECT r.* FROM review_responses r JOIN review_findings f ON f.id=r.finding_id JOIN briefs b ON b.id=f.version_id WHERE b.run_id=? ORDER BY r.rowid',(store.one('briefs',version_id)['run_id'],))
    current_latest=_latest_responses(current,ancestry)
    current_ids={row['id'] for row in current_latest.values() if row['version_id']==version_id}
    if current_ids!=set(scoped) or any(current_latest.get(row['finding_id'],{}).get('id')!=identity for identity,row in scoped.items()):
        raise ValueError('审阅期间处理说明已更新，请复核最新 response_id')
    for identity,row in scoped.items():
        live=current_latest[row['finding_id']]
        if json.loads(live['data'])!=row['data'] or live['version_id']!=row['version_id']:
            raise ValueError('审阅处理说明与保存记录不一致')
    return scoped


def validate_applicable_review(store,review_id,version_id=None):
    """Validate input applicability; completion and delivery eligibility are separate."""
    review=get_review(store,review_id)
    if version_id is not None and review['version_id']!=version_id:
        raise ValueError('Reviewer 输出未绑定本次正文与核查包')
    packet,target,bound=_packet(store,review)
    current=_snapshot(store,review['version_id'],target.get('snapshot_version',3))
    if target.get('snapshot_version',3)<5:
        source_ids={s['id'] for s in current['sources']}
        annotations=store.rows('SELECT source_id FROM source_snapshot_metadata')
        if any(row['source_id'] in source_ids for row in annotations):
            raise ValueError('旧核查未覆盖已登记的来源时间注释，请对当前证据重新审阅')
    if target.get('snapshot_version',3)<4:
        current['requirements']={key:current['requirements'].get(key) for key in target['requirements']}
        current['requirements']['schema_version']=target['requirements'].get('schema_version',1)
        current['requirements']['requirement_items']=[item for item in current['requirements']['requirement_items'] if item['kind'] in ('objective','question','manual')]
    # Admission itself resolves conflicts. Ignore only those exact state fields
    # written by this accepted review, never later responses or changed evidence.
    if review['result']:
        original={item['id']:item for item in target['conflicts']}
        checks={item['conflict_id']:item for item in review['result'].get('conflict_checks',[])}
        for item in current['conflicts']:
            before=original.get(item['id'])
            if before and item['data'].get('review',{}).get('review_id')==review_id:
                check=checks.get(item['id'])
                expected={'review_id':review_id,'decision':check['decision'],'reason':check['reason'],'chosen_fact_id':check.get('chosen_fact_id')} if check else None
                history=list(before['data'].get('review_history',[]));previous=before['data'].get('review')
                if previous and previous not in history:history.append(previous)
                if expected:history.append(expected)
                expected_status='open' if check and check['decision']=='unresolved' else 'resolved'
                if item['data']['review']!=expected or item['status']!=expected_status or ((target.get('snapshot_version',3)>=4 or 'review_history' in item['data']) and item['data'].get('review_history')!=history):
                    raise ValueError('审阅后的冲突状态与该 Review 的实际决定不一致')
                for key in ('status','updated'):item[key]=before[key]
                for key in ('review','review_history'):
                    if key in before['data']:item['data'][key]=before['data'][key]
                    else:item['data'].pop(key,None)
    if sha(dump({'target':current,'files':bound}).encode())!=review['fingerprint']:
        raise ValueError('审阅期间依据发生变化，结果不能应用于新输入')
    _response_scope(store,packet,review['version_id'])
    return review


def _snapshot(store,version_id,snapshot_version=6):
    from .document_model import brief_document
    from .deliverable_spec import resolve
    from .figure_support import validate_figures
    from .media import source_files
    brief=store.one('briefs',version_id);run=store.one('runs',brief['run_id']);sources=[]
    from .conflicts import for_run
    conflicts=for_run(store,run['id'])
    source_ids=set(store.source_ids(run['id']))|{sid for conflict in conflicts for sid in conflict['data']['source_ids']}
    for sid in sorted(source_ids):
        source=store.one('sources',sid)
        try:
            _,_,original=source_files(store,sid);text=store.source_text(sid)
            sources.append({'id':sid,'name':source['name'],'hash':source['hash'],'status':source['status'],
                            'original_hash':sha(original.read_bytes()) if original else None})
        except (ValueError,OSError) as exc:sources.append({'id':sid,'name':source['name'],'hash':source['hash'],'error':str(exc)})
    from .evidence import claim_closure
    unused=store.rows('SELECT c.id FROM claims c WHERE c.run_id=? AND NOT EXISTS (SELECT 1 FROM claim_bindings b WHERE b.claim_id=c.id) AND NOT EXISTS (SELECT 1 FROM claims n WHERE n.previous_id=c.id)',(run['id'],))
    # Source statements are comparison input, not candidate report claims.
    candidates=[closure for closure in (claim_closure(store,c['id']) for c in unused)
                if snapshot_version < 6 or closure['claim']['data'].get('claim_role','report_statement')=='report_statement']
    source_statements=[]
    for row in store.rows('SELECT * FROM claims WHERE run_id=? ORDER BY rowid',(run['id'],)):
        data=json.loads(row['data'])
        if data.get('claim_role')!='source_statement':continue
        source_statements.append({'claim_id':row['id'],'statement':data['statement'],'kind':data['kind'],
                                  'entity':data.get('entity',''),'metric':data.get('metric',''),'period':data.get('period',''),
                                  'scope':data.get('scope',''),'attribution':data.get('attribution',''),
                                  'supports':[support['span_id'] for support in data.get('supports',[])]})
    detail=json.loads(brief['detail']);requirements=json.loads(run['requirements'])
    reconciliation=None
    if detail.get('reconciliation_id'):
        try:
            from .reconciliation import read as read_reconciliation
            reconciliation=read_reconciliation(store,run['id'],detail['reconciliation_id'])
        except (ValueError,OSError) as exc:
            reconciliation={'id':detail['reconciliation_id'],'error':str(exc)}
    timing=[];changes=[]
    if snapshot_version>=5:
        from .source_updates import for_run as changes_for_run
        changes=[{k:v for k,v in c.items() if k not in ('review','review_status','current_impacts')} for c in changes_for_run(store,run['id'])]
        timing=[{**row,'data':json.loads(row['data'])} for row in store.rows('SELECT * FROM source_snapshot_metadata ORDER BY rowid') if row['source_id'] in source_ids]
    return {'snapshot_version':snapshot_version,'candidate_claims':candidates,'version_id':version_id,'brief_hash':brief['hash'],'document':brief_document(brief),
            'requirements':resolve(requirements,reader_contract=detail.get('reader_contract')),'detail':detail,
            **({'requirements_input':requirements} if snapshot_version>=4 else {}),
            **({'source_updates':changes,'source_timing':timing} if snapshot_version>=5 else {}),
            **({'source_statements':source_statements,'reconciliation':reconciliation} if snapshot_version>=6 else {}),
            'sources':sources,'conflicts':conflicts,'evidence':inspect_bindings(store,version_id),
            'figures':validate_figures(store,run['id'],brief['markdown'])}


def _tool_history(store,executions,save):
    """Attach host records only through the job's persisted message/turn anchor.

    Sessions are reusable. job/attached is a session association, never proof
    that every future command in that session belongs to that job.
    """
    from .execution_records import sanitize
    index=[]
    if not store.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_messages'"):
        return index
    session_events={}
    for execution in executions:
        scopes={};gaps=[]
        attached={row['session_id'] for row in store.rows("SELECT DISTINCT session_id FROM chat_events WHERE kind='job/attached' AND json_extract(data,'$.jobId')=?",(execution['job_id'],))}
        for event in execution['events']:
            if event['kind']!='runtime_started':continue
            anchor=event['data'];sid=anchor.get('session_id');mid=anchor.get('message_id')
            rows=store.rows("SELECT turn_id FROM chat_messages WHERE id=? AND session_id=? AND role='user'",(mid,sid))
            owners=store.rows("SELECT DISTINCT job_id FROM events WHERE kind='runtime_started' AND json_extract(data,'$.session_id')=? AND json_extract(data,'$.message_id')=?",(sid,mid))
            if sid not in attached or not rows or not rows[0]['turn_id'] or {row['job_id'] for row in owners}!={execution['job_id']}:
                gaps.append({'session_id':sid,'message_id':mid,'reason':'缺少唯一任务、消息和实际执行轮次绑定，未归入工具证据'})
                continue
            scopes.setdefault(sid,{})[rows[0]['turn_id']]=mid
        if attached and not scopes and not gaps:
            gaps.append({'reason':'仅保存了会话关联，没有 runtime_started 消息与轮次绑定，未归入工具证据'})
        for sid,turns in scopes.items():
            if sid not in session_events:
                rows=store.rows("SELECT seq,kind,data,created FROM chat_events WHERE session_id=? AND kind IN ('tool/record','item/started','item/completed','child/item/started','child/item/completed') ORDER BY seq",(sid,))
                session_events[sid]=[{**row,'data':json.loads(row['data'])} for row in rows]
            children={}
            for event in session_events[sid]:
                data=event['data'];turn=data.get('turnId');thread=data.get('threadId')
                if event['kind']!='tool/record':
                    item=data.get('item',{})
                    if item.get('type')=='collabAgentToolCall':
                        owner=children.get(thread,{}).get('root_turn_id',turn)
                        for child in item.get('receiverThreadIds',[]):
                            children[child]={'root_turn_id':owner,'parent_thread_id':thread,'delegation_event_seq':event['seq']}
                    continue
                record=data.get('record',{});native=record.get('native_session')
                delegation=children.get(native,{})
                root_turn=turn if turn in turns else delegation.get('root_turn_id')
                if root_turn not in turns:continue
                # Older persisted journals may predate the current sanitizer.
                # Project them safely into a new packet, preserving their hash
                # as a history reference without rewriting the original event.
                public=sanitize(record)
                if public!=record:
                    public['redacted']=True
                    public['record_hash']=sha(dump({k:v for k,v in public.items() if k!='record_hash'}).encode())
                name=f"history/tools/{sid}-{event['seq']}.json"
                identity={'job_id':execution['job_id'],'session_id':sid,'message_id':turns[root_turn],
                          'turn_id':turn,'root_turn_id':root_turn,'native_session':native,'event_seq':event['seq']}
                if turn!=root_turn:identity['delegation']=delegation
                saved={**identity,'created':event['created'],'journal_record_hash':record.get('record_hash'),'record':public}
                save(name,pack_dump(saved).encode())
                index.append({**identity,'file':name,'tool':public['tool'],'status':public['status']})
        if gaps:execution['tool_history_gaps']=gaps
    return index


def _visual_inputs(store,snapshot,packet,source_index,save):
    """Prepare only current report figures and explicitly located visual evidence.

    This is deterministic packet assembly, before the read-only Reviewer runs.
    All model attachment bytes are subsequently read from this fixed packet.
    """
    visuals=[]
    for figure in snapshot['figures']:
        name='figures/'+figure['figure_id']+'/'+Path(figure['image_path']).name
        visuals.append({'id':'figure:'+figure['figure_id'],'kind':'report_figure',
                        'figure_id':figure['figure_id'],'title':figure['title'],
                        'file':name,'sha256':sha((packet/name).read_bytes()),'mime':'image/png'})
    selected={}
    def collect(node):
        for evidence in node.get('evidence',[]):
            locator=evidence['data']['locator'];kind=locator['kind']
            if kind not in ('image','pdf'):continue
            key=(evidence['source_id'],locator.get('page') if kind=='pdf' else None)
            selected.setdefault(key,{'kind':kind,'span_ids':[]})['span_ids'].append(evidence['id'])
        for premise in node.get('premises',[]):collect(premise)
    for node in snapshot['evidence']['bindings']:collect(node)
    # Candidate counter-evidence: source statements and comparison basis spans are
    # visual inputs too, not only the claims the body already adopted.
    extra_span_ids=set()
    for statement in snapshot.get('source_statements',[]):extra_span_ids.update(statement.get('supports',[]))
    for relation in (snapshot.get('reconciliation') or {}).get('relations',[]):extra_span_ids.update(relation.get('basis_span_ids',[]))
    from .evidence import record as _evidence_record
    for span_id in sorted(extra_span_ids):
        try:span=_evidence_record(store,'evidence_spans',span_id)
        except ValueError:continue
        locator=span['data']['locator'];kind=locator['kind']
        if kind not in ('image','pdf'):continue
        key=(span['source_id'],locator.get('page') if kind=='pdf' else None)
        selected.setdefault(key,{'kind':kind,'span_ids':[]})['span_ids'].append(span_id)
    sources={item['id']:item for item in source_index}
    for number,((sid,page),choice) in enumerate(selected.items()):
        source=sources[sid];identity='source:'+sid+(':'+str(page) if page else '')
        item={'id':identity,'kind':'source_evidence','source_id':sid,'page':page,
              'span_ids':sorted(set(choice['span_ids'])),'title':source['name']}
        if number>=8:
            item['unavailable']='本次仅预装前 8 个已定位证据视觉，其他原件保留在核查包中；未逐一读图不得声称完成视觉核查。'
            visuals.append(item);continue
        try:
            original=packet/source['original_file']
            if sha(original.read_bytes())!=source['original_hash']:raise ValueError('证据原件与核查快照不一致')
            if choice['kind']=='image':
                from .figures import _normalized_image
                png,_,_,_=_normalized_image(original.read_bytes());name='sources/'+sid+'.visual.png'
            else:
                from .media import render_source_pages,source_files
                # The established renderer validates its source/page cache hashes.
                # Compare the actual original to the frozen original as well.
                _,_,live_original=source_files(store,sid)
                if not live_original or sha(live_original.read_bytes())!=source['original_hash']:raise ValueError('PDF 原件在核查包准备期间发生变化')
                rendered=render_source_pages(store,sid,[page])['pages'][0]
                png=Path(rendered['path']).read_bytes();name=f'sources/{sid}.page-{page}.png'
            save(name,png);source.setdefault('visual_files',[]).append(name)
            item.update(file=name,sha256=sha(png),mime='image/png')
        except (ValueError,OSError,KeyError,ImportError) as exc:item['unavailable']=str(exc)
        visuals.append(item)
    return {'version_id':snapshot['version_id'],'images':visuals,
            'note':'仅记录本次可供读取的视觉输入。历史未核验项不描述本次所选模型的能力；附件成功提交也不自动证明视觉结论正确。'}


def visual_input_files(store,review_id,packet_root):
    """Return validated packet-local bytes, never paths supplied by an agent."""
    review=get_review(store,review_id);packet,target,bound=_packet(store,review)
    if packet.resolve()!=Path(packet_root).resolve():raise ValueError('视觉输入不属于当前 Reviewer 核查包')
    if 'visual-inputs.json' in bound:
        plan=json.loads((packet/'visual-inputs.json').read_text())
        if plan.get('version_id')!=review['version_id']:raise ValueError('视觉输入属于另一正文版本')
        images=plan['images']
    else:
        # Retained pre-attachment packets can expose their already-frozen figures;
        # never read live figure locations or mutate an earlier packet on resume.
        images=[{'id':'figure:'+figure['figure_id'],'kind':'report_figure','figure_id':figure['figure_id'],
                 'title':figure['title'],'file':'figures/'+figure['figure_id']+'/'+Path(figure['image_path']).name,
                 'mime':'image/png'} for figure in target['figures']]
    output=[]
    for item in images:
        if not item.get('file'):
            output.append({**item,'bytes':None});continue
        name=item['file']
        if name not in bound or (item.get('sha256') and item['sha256']!=bound[name]):raise ValueError('视觉输入未绑定到核查包文件清单')
        blob=(packet/name).read_bytes()
        if sha(blob)!=bound[name]:raise ValueError('Reviewer 图片在发送前发生变化')
        output.append({**item,'sha256':bound[name],'bytes':blob})
    return output


def build_packet(store,version_id,folder):
    from .media import source_files
    snapshot=_snapshot(store,version_id);folder=Path(folder)
    # A saved reader contract must survive into the packet. Losing it silently would
    # let the Reviewer check content without the user's own delivery interpretation.
    saved_contract=snapshot['detail'].get('reader_contract')
    if saved_contract and snapshot['requirements'].get('reader_contract')!=saved_contract:
        raise ValueError('核查包丢失了本轮已保存的读者约定')
    packet=folder/'packet'
    if packet.is_symlink():raise ValueError('核查包目录不能是符号链接')
    packet.mkdir(parents=True,exist_ok=True);entries={};source_index=[]
    def save(name,blob):
        path=packet/name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(packet)):
            raise ValueError('核查包不能包含符号链接')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and path.read_bytes()!=blob:raise ValueError('核查包内容已变化，请创建新审阅')
        if not path.exists():path.write_bytes(blob)
        entries[name]=sha(blob)
    save('target.json',pack_dump(snapshot).encode())
    long_text=[]
    def collect(value,path):
        if isinstance(value,str) and len(value)>1200:long_text.append({'json_path':path,'chunks':[value[i:i+1200] for i in range(0,len(value),1200)]})
        elif isinstance(value,dict):
            for k,v in value.items():collect(v,path+[k])
        elif isinstance(value,list):
            for i,v in enumerate(value):collect(v,path+[i])
    collect(snapshot,[])
    save('target-long-text.json',pack_dump(long_text).encode())
    save('output.schema.json',pack_dump(ReviewOutput.model_json_schema()).encode())
    for source in snapshot['sources']:
        sid=source['id'];item=dict(source)
        try:
            raw,provenance,original=source_files(store,sid)
            text=store.source_text(sid);name='sources/'+sid+'.txt';save(name,text.encode());item['text_file']=name
            view=[{'original_line':i+1,'chunks':[line[n:n+1200] for n in range(0,len(line),1200)] or ['']} for i,line in enumerate(text.splitlines())]
            name='sources/'+sid+'.view.json';save(name,pack_dump(view).encode());item['readable_text_file']=name
            if original:
                name='sources/'+sid+original.suffix;save(name,original.read_bytes());item['original_file']=name
            # Human-readable saved workbook cells require no Reviewer shell or recalculation.
            if original and original.suffix=='.xlsx':
                from .workbook_figures import workbook_text
                name='sources/'+sid+'.cells.txt';save(name,workbook_text(original.read_bytes()).encode());item['cells_file']=name
        except (ValueError,OSError) as exc:item['read_error']=str(exc)
        source_index.append(item)
    for figure in snapshot['figures']:
        for key in ('image_path','data_path','script_path'):
            path=figure.get(key)
            if path:
                source=(store.root/path).resolve()
                if not source.is_relative_to(store.root) or not source.is_file():raise ValueError('图表核查资源路径无效')
                save('figures/'+figure['figure_id']+'/'+Path(path).name,source.read_bytes())
    visual_inputs=_visual_inputs(store,snapshot,packet,source_index,save)
    save('visual-inputs.json',pack_dump(visual_inputs).encode())
    # Only this report's persisted public history; never host-global DB queries.
    brief=store.one('briefs',version_id);versions=store.rows('SELECT id,parent_id,author,markdown,hash,created FROM briefs WHERE run_id=? ORDER BY rowid',(brief['run_id'],))
    save('history/versions.json',pack_dump(versions).encode())
    prior_reviews=store.rows('SELECT r.id,r.version_id,r.status,r.result,r.created FROM reviews r JOIN briefs b ON b.id=r.version_id WHERE b.run_id=? ORDER BY r.rowid',(brief['run_id'],))
    save('history/reviews.json',pack_dump([{**r,'result':json.loads(r['result']) if r['result'] else None} for r in prior_reviews]).encode())
    executions=[]
    for job in store.rows('SELECT id,kind,payload,status,result,error FROM jobs ORDER BY rowid'):
        payload=json.loads(job['payload'])
        if payload.get('run_id')!=brief['run_id'] and payload.get('version_id') not in {v['id'] for v in versions}:continue
        events=store.rows('SELECT kind,data,created FROM events WHERE job_id=? ORDER BY seq',(job['id'],))
        # Controlled event metadata: no hidden reasoning or raw auth/config payloads.
        allowed={'runtime_started','runtime_progress','export_progress','revision_progress','company_review_complete'}
        executions.append({'job_id':job['id'],'kind':job['kind'],'status':job['status'],'runtime':payload.get('runtime'),
                           'backend':payload.get('agent_backend'),'events':[{'kind':e['kind'],'created':e['created'],'data':json.loads(e['data'])} for e in events if e['kind'] in allowed]})
    tool_index=_tool_history(store,executions,save)
    save('history/tools.json',pack_dump(tool_index).encode())
    save('history/executions.json',pack_dump(executions).encode())
    responses=store.rows('SELECT r.*,f.data AS finding_data,f.status AS finding_status,f.version_id AS finding_version FROM review_responses r JOIN review_findings f ON f.id=r.finding_id JOIN briefs b ON b.id=f.version_id WHERE b.run_id=? ORDER BY r.rowid',(brief['run_id'],))
    save('history/responses.json',pack_dump([{**r,'data':json.loads(r['data']),'finding_data':json.loads(r['finding_data'])} for r in responses]).encode())
    fingerprint=sha(dump({'target':snapshot,'files':entries}).encode())
    index={'fingerprint':fingerprint,'version_id':version_id,'sources':source_index,
           'files':entries,'visual_inputs':'visual-inputs.json','history':['history/versions.json','history/executions.json','history/responses.json','history/tools.json','history/reviews.json'],
           'limits':['执行历史只包含已保存的本任务记录；缺少的工具输出需标记未核验，不到宿主全局数据库补查。',
                     '无法读取图像的模型必须把视觉检查标为未完成；只读图表数据不等于已目视核验。']}
    save('index.json',pack_dump(index).encode())
    return fingerprint,entries


def validate_clause_checks(spec, clause_checks, status):
    """New-protocol completeness: the reviewer must answer every frozen clause."""
    from .deliverable_spec import clause_items
    clauses = {c['clause_id']: c for c in clause_items(spec)}
    supplied = [check.clause_id for check in clause_checks]
    if len(supplied) != len(set(supplied)) or not set(supplied).issubset(clauses):
        raise ValueError('条款核查引用范围外或重复的 clause_id')
    for check in clause_checks:
        if check.status == 'not_applicable' and clauses[check.clause_id]['kind'] == 'reader_content':
            raise ValueError('内容条款不能被标为不适用')
    if status == 'complete' and set(supplied) != set(clauses):
        raise ValueError('完整审阅必须逐条给出 clause_checks；缺少='
                         + ','.join(sorted(set(clauses) - set(supplied))))


def accept_review(store,review_id,value):
    result=ReviewOutput.model_validate(value);review=get_review(store,review_id)
    if result.version_id!=review['version_id'] or result.fingerprint!=review['fingerprint']:
        raise ValueError('Reviewer 输出未绑定本次正文与核查包')
    if review['result']:
        if ReviewOutput.model_validate(review['result']).model_dump()!=result.model_dump():raise ValueError('已保存Review不可覆盖，请建立新审阅')
        if result.assessment is not None:store.validate_assessment(result.version_id,result.assessment.model_dump())
        with store.tx() as c:_save_assessment(c,review_id,result)
        from .review_learning import record_verified_corrections
        record_verified_corrections(store,review_id)
        return review
    validate_applicable_review(store,review_id,result.version_id)
    packet,current,_=_packet(store,review)
    expected={b['claim_id'] for b in current['evidence']['bindings']}
    allowed_claims=set(expected)
    def include_premises(node):
        allowed_claims.add(node['claim_id'])
        for premise in node.get('premises',[]):include_premises(premise)
    for binding in current['evidence']['bindings']+current.get('candidate_claims',[]):include_premises(binding)
    supplied=[x.claim_id for x in result.claim_checks]
    if len(supplied)!=len(set(supplied)) or not set(supplied).issubset(allowed_claims):
        raise ValueError('主张核查记录不属于本次范围或重复；范围外ID='+','.join(sorted(set(supplied)-allowed_claims)))
    if result.status=='complete' and not expected.issubset(supplied):raise ValueError('完整审阅遗漏正文已使用主张，必须标为未完成')
    from .evidence import blocks
    allowed_blocks=set(blocks(current['document']))
    requirements={x['requirement_id']:x for x in current['requirements']['requirement_items']}
    allowed_requirements=set(requirements)
    seen_requirements=set()
    for check in result.requirement_checks:
        if check.requirement_id not in allowed_requirements or check.requirement_id in seen_requirements:raise ValueError('要求核查引用范围外或重复的 requirement_id')
        seen_requirements.add(check.requirement_id)
        if check.status=='manual' and requirements[check.requirement_id]['mode']!='manual':raise ValueError('Reviewer 不能把必答要求改为人工待填')
    if review['data'].get('protocol','legacy')=='clauses_v1':
        validate_clause_checks(current['requirements'],result.clause_checks,result.status)
    for finding in result.findings:
        if finding.resolution and not finding.response_to:raise ValueError('关闭发现必须指向准确的 response_id')
        if not finding.response_to:
            if not set(finding.claim_ids).issubset(allowed_claims):raise ValueError('发现引用了本次范围外的主张ID')
            if not set(finding.block_ids).issubset(allowed_blocks):raise ValueError('发现引用了本次正文不存在的块ID')
        if not set(finding.requirement_ids).issubset(allowed_requirements):raise ValueError('发现引用了未登记的要求ID')
    expected_responses=set(_response_scope(store,packet,result.version_id))
    checks={}
    for check in result.response_checks:
        if check.response_id in checks:raise ValueError('处理说明复核重复')
        checks[check.response_id]=(check.decision,check.reason)
    for finding in result.findings:
        if finding.response_to:
            decision=finding.resolution or 'unresolved'
            if finding.response_to in checks and checks[finding.response_to][0]!=decision:raise ValueError('同一处理说明存在相互矛盾的复核结论')
            checks.setdefault(finding.response_to,(decision,finding.evidence))
    if set(checks)-expected_responses:raise ValueError('response_to必须是history/responses.json中针对当前版本的response_id，不能填写finding_id或其他版本记录')
    if result.status=='complete' and expected_responses-set(checks):
        raise ValueError('完整复核仍缺少response_checks；未处理ID='+','.join(sorted(expected_responses-set(checks))))
    allowed_conflicts={x['id'] for x in current['conflicts']}
    conflict_ids=[check.conflict_id for check in result.conflict_checks]
    if len(conflict_ids)!=len(set(conflict_ids)) or not set(conflict_ids).issubset(allowed_conflicts):raise ValueError('冲突复核引用范围外或重复的 conflict_id')
    for check in result.conflict_checks:
        for span_id in check.basis_span_ids:
            try:record(store,'evidence_spans',span_id)
            except ValueError:raise ValueError('冲突复核引用了不存在的证据片段：'+span_id) from None
    if result.status=='complete' and set(conflict_ids)!=allowed_conflicts:raise ValueError('完整审阅遗漏冲突复核')
    if result.assessment is not None:store.validate_assessment(result.version_id,result.assessment.model_dump())
    with store.tx() as c:
        existing=c.execute('SELECT result FROM reviews WHERE id=?',(review_id,)).fetchone()
        serialized=dump(result.model_dump())
        if existing['result']:
            if ReviewOutput.model_validate(json.loads(existing['result'])).model_dump()!=result.model_dump():raise ValueError('已保存Review不可覆盖，请建立新审阅')
            _save_assessment(c,review_id,result)
            return get_review(store,review_id)
        _response_scope(store,packet,result.version_id)
        c.execute('UPDATE reviews SET status=?,result=?,updated=? WHERE id=?',(result.status,serialized,now(),review_id))
        from .conflicts import accept_check
        conflict_inputs={item['id']:item for item in current['conflicts']}
        for check in result.conflict_checks:
            accept_check(store,c,check.conflict_id,check.decision,check.reason,review_id,check.chosen_fact_id,expected=conflict_inputs[check.conflict_id])
        for response_id,(decision,reason) in checks.items():
            response=c.execute('SELECT finding_id FROM review_responses WHERE id=? AND version_id=?',(response_id,result.version_id)).fetchone()
            if not response:raise ValueError('复核的处理说明不存在')
            c.execute('UPDATE review_findings SET status=? WHERE id=?',('open' if decision=='unresolved' else decision,response['finding_id']))
        for finding in result.findings:
            if not finding.resolution and not finding.response_to:
                c.execute('INSERT INTO review_findings VALUES(?,?,?,?,?,?)',(uid('finding'),review_id,result.version_id,'open',dump(finding.model_dump()),now()))
        _save_assessment(c,review_id,result)
    from .review_learning import record_verified_corrections
    record_verified_corrections(store,review_id)
    return get_review(store,review_id)


def _save_assessment(connection,review_id,result):
    if result.assessment is None:return
    identity='assessment_'+review_id;serialized=dump(result.assessment.model_dump())
    previous=connection.execute('SELECT version_id,data FROM assessments WHERE id=?',(identity,)).fetchone()
    if previous and (previous['version_id']!=result.version_id or previous['data']!=serialized):raise ValueError('已保存Review评分不可覆盖')
    connection.execute('INSERT OR IGNORE INTO assessments VALUES(?,?,?,?)',(identity,result.version_id,serialized,now()))


def respond(store,finding_id,version_id,action,reason):
    if action not in ('corrected','removed','disagree') or not isinstance(reason,str) or not reason.strip():raise ValueError('处理需注明修改、移除或有依据异议及理由')
    rows=store.rows('SELECT f.*,b.run_id FROM review_findings f JOIN briefs b ON b.id=f.version_id WHERE f.id=?',(finding_id,))
    brief=store.one('briefs',version_id)
    if not rows or rows[0]['run_id']!=brief['run_id']:raise ValueError('发现与修订版本不属于同一报告')
    cursor=brief
    while cursor and cursor['id']!=rows[0]['version_id']:
        cursor=store.one('briefs',cursor['parent_id']) if cursor['parent_id'] else None
    if cursor is None:raise ValueError('处理说明必须指向发现的原版本或其后续修订')
    def scoped_status():
        return next(item['status'] for item in review_status(store,version_id)['findings'] if item['id']==finding_id)
    if scoped_status() in ('resolved','dismissed_with_evidence'):raise ValueError('该发现已有复核结果')
    identity=uid('response');data={'action':action,'reason':reason}
    with store.tx() as c:
        if scoped_status() in ('resolved','dismissed_with_evidence'):raise ValueError('该发现已有复核结果')
        previous=c.execute('SELECT * FROM review_responses WHERE finding_id=? AND version_id=? ORDER BY rowid DESC LIMIT 1',(finding_id,version_id)).fetchone()
        if previous and json.loads(previous['data'])==data:
            return {'id':previous['id'],'status':'addressed_pending_review'}
        c.execute('INSERT INTO review_responses VALUES(?,?,?,?,?)',(identity,finding_id,version_id,dump(data),now()))
        c.execute("UPDATE review_findings SET status='addressed_pending_review' WHERE id=?",(finding_id,))
    return {'id':identity,'status':'addressed_pending_review'}


def review_status(store,version_id):
    brief=store.one('briefs',version_id)
    reviews=store.rows('SELECT id,status,created,result FROM reviews WHERE version_id=? ORDER BY rowid DESC',(version_id,))
    findings=store.rows("SELECT f.* FROM review_findings f JOIN briefs b ON b.id=f.version_id WHERE b.run_id=? ORDER BY f.rowid",(brief['run_id'],))
    ancestry=_ancestry(store,version_id)
    findings=[finding for finding in findings if finding['version_id'] in ancestry]
    # A correction in one revision must not close an unchanged sibling draft or
    # rewrite the historical state of the version where the finding was raised.
    responses=store.rows('SELECT r.* FROM review_responses r JOIN review_findings f ON f.id=r.finding_id JOIN briefs b ON b.id=f.version_id WHERE b.run_id=? ORDER BY r.rowid',(brief['run_id'],))
    latest=_latest_responses(responses,ancestry)
    decisions={}
    for row in store.rows("SELECT version_id,result FROM reviews WHERE result IS NOT NULL ORDER BY rowid"):
        if row['version_id'] not in ancestry:continue
        result=json.loads(row['result'])
        for check in result.get('response_checks',[]):decisions[check['response_id']]=check['decision']
        for finding in result.get('findings',[]):
            if finding.get('response_to'):decisions[finding['response_to']]=finding.get('resolution') or 'unresolved'
    for finding in findings:
        response=latest.get(finding['id']);decision=decisions.get(response['id']) if response else None
        finding['status']=('open' if decision=='unresolved' or not response else decision or 'addressed_pending_review')
    from .conflicts import for_run
    detail=json.loads(brief['detail'])
    reconciliation=None
    if detail.get('reconciliation_id'):
        try:
            from .reconciliation import read as read_reconciliation
            reconciliation=read_reconciliation(store,brief['run_id'],detail['reconciliation_id'])
        except (ValueError,OSError) as exc:
            reconciliation={'id':detail['reconciliation_id'],'error':str(exc)}
    return {'version_id':version_id,'conflicts':for_run(store,brief['run_id']),'reviews':[{**r,'result':json.loads(r['result']) if r['result'] else None} for r in reviews],
            'reconciliation':reconciliation,
            'findings':[{**f,'data':json.loads(f['data'])} for f in findings]}


def enqueue_review(store,version_id,*,payload=None):
    values=dict(payload or {});values['version_id']=version_id
    identity=sha(dump({'snapshot':_snapshot(store,version_id),'runtime':values.get('runtime') or store.runtime_config()}).encode())
    for row in store.rows("SELECT * FROM jobs WHERE kind='review' AND status IN ('queued','running') ORDER BY rowid DESC"):
        old=json.loads(row['payload'])
        if old.get('review_input')==identity:return row
    values['review_input']=identity
    return store.enqueue('review',values)


def run_review(store,runtime,job,version_id,folder):
    from .runtime import stage_job
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    marker=folder/'review-id.json'
    if marker.exists():
        identity=json.loads(marker.read_text())['review_id'];review=get_review(store,identity)
        if review['version_id']!=version_id:raise ValueError('保存的审阅任务属于另一正文版本')
        target=json.loads((folder/'packet'/'target.json').read_text())
        if target.get('snapshot_version',1)<3:
            return run_review(store,runtime,job,version_id,folder/'scope-v3')
        if review['result']:
            validate_applicable_review(store,identity,version_id)
            return accept_review(store,identity,review['result'])
    else:
        fingerprint,files=build_packet(store,version_id,folder);identity=uid('review')
        from .deliverable_spec import clause_items as _clause_items
        protocol='clauses_v1' if _clause_items(_snapshot(store,version_id)['requirements']) else 'legacy'
        data={'files':files,'packet_path':str((folder/'packet').relative_to(store.root)),'protocol':protocol}
        with store.tx() as c:c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)',(identity,version_id,job['id'],fingerprint,'queued',dump(data),None,now(),now()))
        marker.write_text(dump({'review_id':identity}));review=get_review(store,identity)
    # A transport-complete result can have failed only schema admission. Retry
    # the shared validator first; a compatibility fix must not spend another turn.
    saved_output=folder/'review.json'
    if saved_output.exists():
        validate_applicable_review(store,identity,version_id)
        raw=saved_output.read_bytes()
        try:return accept_review(store,identity,json.loads(raw))
        except (ValueError,TypeError) as exc:
            attempts=folder/'attempts';attempts.mkdir(exist_ok=True)
            archived=attempts/('review-'+sha(raw)+'.json')
            if not archived.exists():archived.write_bytes(raw)
            (folder/'admission-error.json').write_text(dump({'error':str(exc),'original_output':str(archived.relative_to(folder))}))
    schema=folder/'packet'/'output.schema.json'
    validate_applicable_review(store,identity,version_id)
    target=json.loads((folder/'packet'/'target.json').read_text())
    from .deliverable_spec import clause_items
    # The persisted protocol decides the prompt, not whether clauses happen to exist;
    # a legacy review restored after upgrade must keep the legacy instruction.
    protocol=review['data'].get('protocol','legacy')
    clauses=clause_items(target['requirements']) if protocol=='clauses_v1' else []
    requirement_instruction=('本次为条款级审阅：对下表的 reader_contract 条款逐条给 clause_checks（clause_id、status(covered/partial/missing/not_applicable/unverified)、reason、basis）。clause_id 必须逐字复制程序给出的 ID，不要自行计算或改写。reader_content 核对正文是否实际回答；research_method 核对方法是否落实（过程要求需有来源、核查或执行记录，无法确认写 unverified）；writing_preference 核对呈现；manual_assignment 只核对占位。not_applicable 仅限条款自身带适用条件且本稿不满足，并给依据；内容条款不得标为不适用。必须逐条覆盖；仍要对照原始要求，发现漏拆或误分类用 finding 指出。' if clauses else
        '对requirements.requirement_items逐项给requirement_checks：requirement_id、status(covered/manual/partial/missing)、reason。manual只能用于用户原要求中mode=manual的项目，不得自行降低必答要求。')
    prompt=f'''你是独立只读 Reviewer，核对已保存产物与实际依据，不重新研究或运行计算。
只读取 {folder/'packet'/'index.json'} 所索引的文件。JSON已分行；遇到单行截断，target-long-text.json提供长字段分块、sources/*.view.json提供原文行与分块，按顺序无分隔拼接，不把截断当缺失。先看target.json的本轮要求、正文和claim_evidence关联；核对具体原文与图表；本次报告图和已选证据视觉会作为原生图片附件交给当前选定模型，visual-inputs.json记录它们与固定文件的对应关系。先实际检查这些附件的轴、图注、单位和可见内容，附件不可读时用原生read读取同一packet文件；仍失败则说明本次失败。必要时读history中的本报告历史。绝不查询宿主或其他工作区数据库。
只有read工具可用。禁止bash、执行脚本、修改文件、联网、委派。history/reviews.json提供过去实际审阅；只复用已完成且依赖未变的核查，历史的未核验/图像能力失败必须在本次实际输入上重新检查，不能据此判断当前模型能力。重点核对本次修改与处理说明，不重复扩大研究。发现需补搜/重算/改稿的问题交主Agent，不能自己执行。
检查所有重要事实与判断是否有依据，包括作者未登记的主张；逐项核查已有claim并报告支持范围、反证、证据不足或未知。图像不可读、执行记录缺失和审阅失败不是通过。对每个遗漏、错误给正文片段及依据。
企业报告的核查详情留本结果，不要求正文堆免责声明；准确日期/单位/计划性质应保留。缺口披露不抵消研究覆盖与读者要求。不要使用“无发现”代替完整性检查。
核对target.json中的source_updates和source_timing，区分统计/事件有效期、披露/可得时间、抓取时间与本轮截止时间。更正或新期间的分类声明仍需对照旧新原件，不把proposed当已确认。
核对target.json中的conflicts，按明确更正、不同口径、预测归属或未决分歧分类，逐项给conflict_checks；不要因日期新或官方标签一刀切采用。冲突复核可带basis_span_ids与scope说明依据范围。
核对target.json中的source_statements与reconciliation：source_statements是各来源自身提出的陈述；reconciliation是作者写作前的对照记录，relations只表示可比较性与关系，不表示系统已判定真假。独立判断作者选的是否同一问题、关系分类是否正确、是否漏掉已取得的相反材料、正文是否真正执行了限定或修正；需要处理的分歧用finding返回，不写入Conflict，也不因作者标记complete就认为事实通过。
claim_checks可以使用target.evidence.bindings、premises闭包以及candidate_claims中的真实claim_id。candidate_claims是已登记但未用于正文的候选，不能当成当前正文已使用；若其内容实际出现在正文却未绑定，应记录missing_binding，不编造新claim_id。
对history/responses.json每条当前版本的作者回应，必须在response_checks单独给response_id、decision(resolved/dismissed_with_evidence/unresolved)、reason。findings只放新发现，不要因已修复问题从findings消失就省略response_checks。作者说已修复不算解决，须对照修订和证据；图像不可读等遗留问题应明确unresolved，不重复创建同一发现。
{requirement_instruction}
未核验事项用unchecked_items记录description及importance(core/supporting)；普通表达建议使用minor finding，不冒充核心未核验。
最终回复一个符合 {schema} 的 JSON 对象，不加Markdown或说明，不写文件；运行器保存结果。
version_id={version_id}，fingerprint={review['fingerprint']}。assessment.brief_hash={store.one('briefs',version_id)['hash']}。
四维评分使用既有标准，不用高分抵消重大错误。review.status表示是否完成审阅，claim_checks.status表示依据结论。coverage_scan_complete仅在确实检查了正文重要主张遗漏后设true；未核验项写unchecked。
字段边界（不要混用两套 finding）：顶层 overall/四维分数只属于 assessment；assessment 必须给出，不能省略。assessment.findings 用 dimension/severity/description/report_quote/requirement/source_id/locator/evidence/suggestion。顶层 findings 是核查发现，用 kind/severity/description/evidence，可带 claim_ids/block_ids/requirement_ids（条款可用 requirement_ids 关联，不要写 requirement 或 source_id）。
'''
    from .deliverable_spec import instructions
    prompt+='\n'+instructions(target['requirements'],role='reviewer',include_spec=False)
    if clauses:
        prompt+='\n本次条款清单（clause_checks.clause_id 只能取这些值）：'+dump([{k:c[k] for k in ('clause_id','kind','source_quote','instruction')} for c in clauses])
    ids=set()
    def collect_ids(node):
        ids.add(node['claim_id'])
        for child in node.get('premises',[]):collect_ids(child)
    for item in target['evidence']['bindings']+target.get('candidate_claims',[]):collect_ids(item)
    allowed=sorted(ids)
    response_ids=[{'response_id':r['id'],'finding_id':r['finding_id']} for r in _response_scope(store,folder/'packet',version_id).values()]
    prompt+='\n本次允许的claim_checks.claim_id：'+dump(allowed)+'\n本版本处理说明索引（response_to必须取response_id）：'+dump(response_ids)
    if (folder/'admission-error.json').exists():
        error=json.loads((folder/'admission-error.json').read_text()).get('error','')
        prompt+='\n上次结果未通过接纳：'+error+'。仅修正结构化结果中的ID或字段，不重做已经完成的研究或改稿。response_to使用history/responses.json的id字段，finding_id是其关联的原始发现。'
    stage=stage_job(store,{**job,'payload':dump({**json.loads(job['payload']),'version_id':version_id})},'evaluator',mode='single')
    stage.update(readonly_output='review.json',review_id=identity,input_source_ids=[],allow_web=False)
    with store.tx() as c:c.execute("UPDATE reviews SET status='running',updated=? WHERE id=?",(now(),identity))
    try:
        runtime.execute(stage,prompt,folder,resume_on_complete=(folder/'admission-error.json').exists())
        return accept_review(store,identity,json.loads((folder/'review.json').read_text()))
    except Exception as exc:
        with store.tx() as c:c.execute('UPDATE reviews SET status=?,updated=? WHERE id=? AND result IS NULL',('cancelled' if isinstance(exc,InterruptedError) else 'incomplete',now(),identity))
        (folder/'admission-error.json').write_text(dump({'error':str(exc)}))
        raise
